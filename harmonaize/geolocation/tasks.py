# geolocation/tasks.py
import logging
import time
from celery import shared_task, group, chord
from celery.signals import task_postrun
from django.core.cache import cache
from django.utils import timezone
from django.db import transaction
from django.conf import settings

from .models import GeocodingResult, ValidationResult, ValidatedDataset
from .validation import SmartGeocodingValidator
from .services import GeocodingService
from core.models import Location

logger = logging.getLogger(__name__)


@shared_task(bind=True, rate_limit='100/m', max_retries=3, default_retry_delay=60)
def geocode_single_location_task(self, location_id, force_reprocess=False, user_id=None):
    """
    Celery task to geocode a single location.
    This enables parallel processing of multiple locations.

    Args:
        location_id: ID of Location to process
        force_reprocess: Re-geocode even if results exist
        user_id: ID of user initiating the geocoding

    Returns:
        dict: {'success': bool, 'location_id': int, 'location_name': str, 'error': str or None}
    """
    try:
        location = Location.objects.get(id=location_id)

        # Get user if provided
        user = None
        if user_id:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            try:
                user = User.objects.get(id=user_id)
            except User.DoesNotExist:
                logger.error(f"User with ID {user_id} not found")
                return {
                    'success': False,
                    'location_id': location_id,
                    'location_name': str(location),
                    'error': f'User {user_id} not found'
                }

        if not user:
            logger.error(f"No user provided for geocoding '{location.name}'")
            return {
                'success': False,
                'location_id': location_id,
                'location_name': str(location),
                'error': 'No user provided'
            }

        # Geocode the location
        result = _geocode_single_location(location, force_reprocess, user_id)

        if result:
            logger.info(f"✓ Successfully geocoded: {location.name}")
            return {
                'success': True,
                'location_id': location_id,
                'location_name': str(location),
                'error': None
            }
        else:
            logger.warning(f"✗ Failed to geocode: {location.name}")
            return {
                'success': False,
                'location_id': location_id,
                'location_name': str(location),
                'error': 'Geocoding returned no results'
            }

    except Location.DoesNotExist:
        logger.error(f"Location {location_id} not found")
        return {
            'success': False,
            'location_id': location_id,
            'location_name': 'Unknown',
            'error': 'Location not found'
        }
    except Exception as e:
        logger.error(f"Failed to geocode location {location_id}: {e}")
        # Retry on transient errors
        try:
            raise self.retry(exc=e)
        except self.MaxRetriesExceededError:
            return {
                'success': False,
                'location_id': location_id,
                'location_name': 'Unknown',
                'error': str(e)
            }


@shared_task
def aggregate_geocoding_results(results, task_id):
    """
    Aggregate results from parallel geocoding tasks.
    Called by chord after all individual geocoding tasks complete.

    Args:
        results: List of result dicts from geocode_single_location_task
        task_id: Original batch task ID for progress tracking
    """
    progress_key = f"geocoding_progress_{task_id}"

    successful = sum(1 for r in results if r['success'])
    failed = len(results) - successful

    logger.info(f"Geocoding batch complete: {successful} successful, {failed} failed")

    # Update final progress
    cache.set(progress_key, {
        'status': 'completed',
        'progress': 100,
        'total': len(results),
        'processed': len(results),
        'successful': successful,
        'failed': failed,
        'completed_at': timezone.now().isoformat(),
        'message': f'Completed: {successful} successful, {failed} failed'
    }, timeout=3600)

    return {
        'status': 'completed',
        'total': len(results),
        'successful': successful,
        'failed': failed,
        'message': f'Geocoded {successful}/{len(results)} locations successfully',
        'details': results
    }


@shared_task(bind=True)
def batch_geocode_locations(self, location_ids=None, force_reprocess=False, batch_size=50, user_id=None):
    """
    Celery task for batch geocoding locations using PARALLEL processing.

    This task spawns individual geocode tasks that run in parallel across
    multiple Celery workers, dramatically improving throughput.

    Args:
        location_ids: List of location IDs to process (None = all unprocessed)
        force_reprocess: Re-geocode existing results
        batch_size: DEPRECATED - parallel tasks process all at once
        user_id: ID of user initiating the geocoding (required for GeocodingResult)

    Returns:
        dict: Processing statistics and results
    """
    task_id = self.request.id
    progress_key = f"geocoding_progress_{task_id}"

    try:
        # Initialize progress tracking
        cache.set(progress_key, {
            'status': 'starting',
            'progress': 0,
            'total': 0,
            'processed': 0,
            'successful': 0,
            'failed': 0,
            'current_location': None,
            'started_at': timezone.now().isoformat(),
        }, timeout=3600)

        # Get locations to process
        if location_ids:
            locations = Location.objects.filter(id__in=location_ids)
        else:
            if force_reprocess:
                locations = Location.objects.all()
            else:
                # Find locations that don't have complete geocoding results
                incomplete_count = 0
                complete_location_names = []

                for result in GeocodingResult.objects.all():
                    successful_count = sum([
                        result.hdx_success,
                        result.arcgis_success,
                        result.google_success,
                        result.nominatim_success
                    ])

                    if successful_count >= 2:
                        complete_location_names.append(result.location_name)
                    elif successful_count == 1:
                        incomplete_count += 1

                logger.info(f"Batch geocoding: {incomplete_count} locations have only 1 API result and will be re-processed")
                logger.info(f"Batch geocoding: {len(complete_location_names)} locations have 2+ API results and will be skipped")

                locations = Location.objects.exclude(name__in=complete_location_names)

        # Get list of location IDs
        location_ids_to_process = list(locations.values_list('id', flat=True))
        total_count = len(location_ids_to_process)

        if total_count == 0:
            cache.set(progress_key, {
                'status': 'completed',
                'progress': 100,
                'message': 'No locations to process',
                'completed_at': timezone.now().isoformat()
            }, timeout=3600)
            return {'status': 'completed', 'message': 'No locations to process'}

        logger.info(f"🚀 Starting PARALLEL geocoding of {total_count} locations across multiple workers")

        # Update progress
        cache.set(progress_key, {
            'status': 'processing',
            'progress': 0,
            'total': total_count,
            'processed': 0,
            'successful': 0,
            'failed': 0,
            'message': f'Spawning {total_count} parallel geocoding tasks...'
        }, timeout=3600)

        # Create parallel tasks using Celery chord
        # chord = (group of tasks) | callback
        # This runs all tasks in parallel, then calls the callback with all results
        job = chord(
            group([
                geocode_single_location_task.s(loc_id, force_reprocess, user_id)
                for loc_id in location_ids_to_process
            ])
        )(aggregate_geocoding_results.s(task_id))

        logger.info(f"✓ Spawned {total_count} parallel geocoding tasks. Job ID: {job.id}")

        return {
            'status': 'processing',
            'total': total_count,
            'message': f'Processing {total_count} locations in parallel',
            'job_id': job.id,
            'task_id': task_id
        }

    except Exception as e:
        logger.error(f"Batch geocoding failed: {e}")
        cache.set(progress_key, {
            'status': 'failed',
            'error': str(e),
            'failed_at': timezone.now().isoformat()
        }, timeout=3600)
        raise


@shared_task(bind=True, rate_limit='50/m', max_retries=3, default_retry_delay=60)
def validate_single_location_task(self, geocoding_result_id):
    """
    Celery task to validate a single geocoding result.
    This enables parallel processing of multiple validations.

    Args:
        geocoding_result_id: ID of GeocodingResult to validate

    Returns:
        dict: {'success': bool, 'result_id': int, 'status': str, 'error': str or None}
    """
    try:
        result = GeocodingResult.objects.get(id=geocoding_result_id)
        validator = SmartGeocodingValidator()

        # Run validation with LLM enhancements
        validation = validator.validate_geocoding_result(result)

        logger.info(f"✓ Successfully validated: {result.location_name} - Status: {validation.validation_status}")

        return {
            'success': True,
            'result_id': geocoding_result_id,
            'location_name': result.location_name,
            'status': validation.validation_status,
            'confidence': validation.confidence_score,
            'error': None
        }

    except GeocodingResult.DoesNotExist:
        logger.error(f"GeocodingResult {geocoding_result_id} not found")
        return {
            'success': False,
            'result_id': geocoding_result_id,
            'location_name': 'Unknown',
            'status': 'error',
            'error': 'GeocodingResult not found'
        }
    except Exception as e:
        logger.error(f"Validation failed for result {geocoding_result_id}: {e}")
        # Retry on transient errors
        try:
            raise self.retry(exc=e)
        except self.MaxRetriesExceededError:
            return {
                'success': False,
                'result_id': geocoding_result_id,
                'location_name': 'Unknown',
                'status': 'error',
                'error': str(e)
            }


@shared_task
def aggregate_validation_results(results, task_id):
    """
    Aggregate results from parallel validation tasks.
    Called by chord after all individual validation tasks complete.

    Args:
        results: List of result dicts from validate_single_location_task
        task_id: Original batch task ID for progress tracking
    """
    progress_key = f"validation_progress_{task_id}"

    successful = sum(1 for r in results if r['success'])
    failed = len(results) - successful

    # Count by status
    stats = {
        'auto_validated': sum(1 for r in results if r.get('status') == 'validated'),
        'needs_review': sum(1 for r in results if r.get('status') == 'needs_review'),
        'rejected': sum(1 for r in results if r.get('status') == 'rejected'),
        'pending': sum(1 for r in results if r.get('status') == 'pending')
    }

    logger.info(f"Validation batch complete: {successful} successful, {failed} failed")
    logger.info(f"Status breakdown: {stats}")

    # Update final progress
    cache.set(progress_key, {
        'status': 'completed',
        'progress': 100,
        'total': len(results),
        'processed': len(results),
        'completed_at': timezone.now().isoformat(),
        **stats
    }, timeout=3600)

    return {
        'status': 'completed',
        'total': len(results),
        **stats,
        'message': f'Validated {len(results)} results',
        'details': results
    }


@shared_task(bind=True)
def batch_validate_locations(self, geocoding_result_ids=None, batch_size=50):
    """
    Celery task for batch validation using PARALLEL processing.

    This task spawns individual validation tasks that run in parallel across
    multiple Celery workers, dramatically improving throughput.

    Args:
        geocoding_result_ids: List of GeocodingResult IDs to validate
        batch_size: DEPRECATED - parallel tasks process all at once

    Returns:
        dict: Validation statistics
    """
    task_id = self.request.id
    progress_key = f"validation_progress_{task_id}"

    try:
        # Get results to validate
        if geocoding_result_ids:
            results = GeocodingResult.objects.filter(id__in=geocoding_result_ids)
        else:
            results = GeocodingResult.objects.filter(validation__isnull=True)

        # Get list of result IDs
        result_ids_to_process = list(results.values_list('id', flat=True))
        total_count = len(result_ids_to_process)

        if total_count == 0:
            cache.set(progress_key, {
                'status': 'completed',
                'progress': 100,
                'message': 'No results to validate',
                'completed_at': timezone.now().isoformat()
            }, timeout=3600)
            return {'status': 'completed', 'message': 'No results to validate'}

        logger.info(f"🚀 Starting PARALLEL validation of {total_count} results across multiple workers")

        # Initialize progress
        cache.set(progress_key, {
            'status': 'processing',
            'progress': 0,
            'total': total_count,
            'processed': 0,
            'auto_validated': 0,
            'needs_review': 0,
            'rejected': 0,
            'message': f'Spawning {total_count} parallel validation tasks...'
        }, timeout=3600)

        # Create parallel tasks using Celery chord
        job = chord(
            group([
                validate_single_location_task.s(result_id)
                for result_id in result_ids_to_process
            ])
        )(aggregate_validation_results.s(task_id))

        logger.info(f"✓ Spawned {total_count} parallel validation tasks. Job ID: {job.id}")

        return {
            'status': 'processing',
            'total': total_count,
            'message': f'Validating {total_count} results in parallel',
            'job_id': job.id,
            'task_id': task_id
        }

    except Exception as e:
        logger.error(f"Batch validation failed: {e}")
        cache.set(progress_key, {
            'status': 'failed',
            'error': str(e),
            'failed_at': timezone.now().isoformat()
        }, timeout=3600)
        raise


def _geocode_single_location(location, force_reprocess=False, user_id=None):
    """
    Geocode a single location using the centralized GeocodingService.
    This replaces the management command logic.

    Args:
        location: Location model instance
        force_reprocess: If True, re-geocode even if results exist
        user_id: ID of user initiating the geocoding (required for GeocodingResult)
    """
    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()

        # Retrieve user from ID
        user = None
        if user_id:
            try:
                user = User.objects.get(id=user_id)
            except User.DoesNotExist:
                logger.error(f"User with ID {user_id} not found")
                return None

        if not user:
            logger.error(f"No user provided for geocoding '{location.name}' - cannot create GeocodingResult without user")
            return None

        geocoding_service = GeocodingService()
        result = geocoding_service.geocode_single_location(location, force_reprocess, user=user)
        return result
    except Exception as e:
        logger.error(f"Failed to geocode {location}: {e}")
        return None


@shared_task
def cleanup_old_progress_data():
    """
    Periodic task to clean up old progress tracking data.
    Can be scheduled with celerybeat.
    """
    # Clean up progress data older than 24 hours
    # Implementation depends on your cache backend
    pass