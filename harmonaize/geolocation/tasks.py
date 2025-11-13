# geolocation/tasks.py
import logging
import time
from celery import shared_task
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
@shared_task(bind=True)
def batch_geocode_locations(self, location_ids=None, force_reprocess=False, batch_size=50, user_id=None):
    """
    Celery task for batch geocoding locations.
    Replaces geocode_locations.py management command.

    Args:
        location_ids: List of location IDs to process (None = all unprocessed)
        force_reprocess: Re-geocode existing results
        batch_size: Number of locations to process per batch
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
        

        if location_ids:
            locations = Location.objects.filter(id__in=location_ids)
        else:
            if force_reprocess:
                locations = Location.objects.all()
            else:
                # Find locations that don't have complete geocoding results
                # Include locations with:
                #  1. No GeocodingResult at all
                #  2. GeocodingResult with only 1 successful API (need to retry other APIs)

                # Get locations with incomplete results (only 1 API succeeded)
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
                        # Location has complete geocoding (2+ APIs)
                        complete_location_names.append(result.location_name)
                    elif successful_count == 1:
                        # Location has incomplete geocoding (only 1 API) - will be re-processed
                        incomplete_count += 1

                logger.info(f"Batch geocoding: {incomplete_count} locations have only 1 API result and will be re-processed")
                logger.info(f"Batch geocoding: {len(complete_location_names)} locations have 2+ API results and will be skipped")

                # Process locations that either have no results OR have incomplete results
                locations = Location.objects.exclude(name__in=complete_location_names)
            
        total_count = locations.count()
        
        if total_count == 0:
            cache.set(progress_key, {
                'status': 'completed',
                'progress': 100,
                'message': 'No locations to process',
                'completed_at': timezone.now().isoformat()
            }, timeout=3600)
            return {'status': 'completed', 'message': 'No locations to process'}
        

        cache.set(progress_key, {
            'status': 'processing',
            'progress': 0,
            'total': total_count,
            'processed': 0,
            'successful': 0,
            'failed': 0,
            'current_location': None,
        }, timeout=3600)
        
        # Process locations in batches
        successful = 0
        failed = 0
        
        for i, location in enumerate(locations.iterator(chunk_size=batch_size)):
            try:

                progress = int((i / total_count) * 100)
                cache.set(progress_key, {
                    'status': 'processing',
                    'progress': progress,
                    'total': total_count,
                    'processed': i,
                    'successful': successful,
                    'failed': failed,
                    'current_location': str(location),
                }, timeout=3600)
                
                # Geocode the location (extract from management command)
                result = _geocode_single_location(location, force_reprocess, user_id)
                
                if result:
                    successful += 1
                else:
                    failed += 1
                    
            except Exception as e:
                logger.error(f"Failed to geocode {location}: {e}")
                failed += 1
                

            self.update_state(
                state='PROGRESS',
                meta={
                    'progress': progress,
                    'total': total_count,
                    'processed': i + 1,
                    'successful': successful,
                    'failed': failed
                }
            )
        
        # Final progress update
        cache.set(progress_key, {
            'status': 'completed',
            'progress': 100,
            'total': total_count,
            'processed': total_count,
            'successful': successful,
            'failed': failed,
            'completed_at': timezone.now().isoformat(),
            'message': f'Completed: {successful} successful, {failed} failed'
        }, timeout=3600)
        
        return {
            'status': 'completed',
            'total': total_count,
            'successful': successful,
            'failed': failed,
            'message': f'Geocoded {successful}/{total_count} locations successfully'
        }
        
    except Exception as e:
        logger.error(f"Batch geocoding failed: {e}")
        cache.set(progress_key, {
            'status': 'failed',
            'error': str(e),
            'failed_at': timezone.now().isoformat()
        }, timeout=3600)
        raise
@shared_task(bind=True)
def batch_validate_locations(self, geocoding_result_ids=None, batch_size=50):
    """
    Celery task for batch validation of geocoded locations.
    Replaces process_locations.py validation logic.
    
    Args:
        geocoding_result_ids: List of GeocodingResult IDs to validate
        batch_size: Number of results to process per batch
        
    Returns:
        dict: Validation statistics
    """
    task_id = self.request.id
    progress_key = f"validation_progress_{task_id}"
    
    try:
        validator = SmartGeocodingValidator()
        

        if geocoding_result_ids:
            results = GeocodingResult.objects.filter(id__in=geocoding_result_ids)
        else:
            results = GeocodingResult.objects.filter(validation__isnull=True)
            
        total_count = results.count()
        
        if total_count == 0:
            return {'status': 'completed', 'message': 'No results to validate'}
        
        # Initialize progress
        cache.set(progress_key, {
            'status': 'processing',
            'progress': 0,
            'total': total_count,
            'processed': 0,
            'auto_validated': 0,
            'needs_review': 0,
            'rejected': 0,
        }, timeout=3600)
        
        stats = {'auto_validated': 0, 'needs_review': 0, 'rejected': 0}
        
        for i, result in enumerate(results.iterator(chunk_size=batch_size)):
            try:
                # Run validation
                validation = validator.validate_geocoding_result(result)
                

                if validation.validation_status == 'validated':
                    stats['auto_validated'] += 1
                elif validation.validation_status == 'needs_review':
                    stats['needs_review'] += 1
                elif validation.validation_status == 'rejected':
                    stats['rejected'] += 1
                

                progress = int(((i + 1) / total_count) * 100)
                cache.set(progress_key, {
                    'status': 'processing',
                    'progress': progress,
                    'total': total_count,
                    'processed': i + 1,
                    **stats
                }, timeout=3600)
                
                self.update_state(
                    state='PROGRESS',
                    meta={
                        'progress': progress,
                        'processed': i + 1,
                        'total': total_count,
                        **stats
                    }
                )
                
            except Exception as e:
                logger.error(f"Validation failed for {result}: {e}")
        
        # Final update
        cache.set(progress_key, {
            'status': 'completed',
            'progress': 100,
            'total': total_count,
            'processed': total_count,
            'completed_at': timezone.now().isoformat(),
            **stats
        }, timeout=3600)
        
        return {
            'status': 'completed',
            'total': total_count,
            **stats,
            'message': f'Validated {total_count} results'
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