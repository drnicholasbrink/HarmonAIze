"""
Celery tasks for asynchronous climate data processing.
"""
import logging
from typing import Dict, Any
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.utils import timezone
from .models import ClimateDataRequest
from .services import ClimateDataProcessor

logger = logging.getLogger(__name__)


def calculate_time_limits_for_request(request_id: int) -> Dict[str, int]:
    """Estimate per-task time limits based on request size.

    Uses a simple heuristic: base allowance plus a small increment per
    (location × variable × day). Capped to avoid unbounded growth.
    """
    try:
        climate_request = ClimateDataRequest.objects.get(pk=request_id)
    except ClimateDataRequest.DoesNotExist:
        return {"soft_time_limit": 1800, "time_limit": 1860}

    locations = climate_request.locations.count() or 1
    variables = climate_request.variables.count() or 1
    date_span_days = max((climate_request.end_date - climate_request.start_date).days + 1, 1)

    total_units = locations * variables * date_span_days
    estimated_seconds = 600 + total_units * 0.4  # base + per-unit allowance

    soft_limit = max(1800, min(int(estimated_seconds), 6 * 3600))  # cap at 6h
    hard_limit = soft_limit + 120

    return {"soft_time_limit": soft_limit, "time_limit": hard_limit}


@shared_task(
    bind=True,
    soft_time_limit=1800,
    time_limit=1860,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def process_climate_data_request(self, request_id: int) -> Dict[str, Any]:
    """
    Asynchronous task to process a climate data request.
    
    Args:
        request_id: ID of the ClimateDataRequest to process
        
    Returns:
        Dict containing processing results
    """
    try:
        # Get the climate request
        climate_request = ClimateDataRequest.objects.get(pk=request_id)
        
        # Update task ID for tracking
        climate_request.configuration = climate_request.configuration or {}
        climate_request.configuration['celery_task_id'] = self.request.id
        climate_request.configuration['effective_time_limits'] = {
            'soft': getattr(self.request, 'soft_time_limit', None),
            'hard': getattr(self.request, 'time_limit', None),
        }
        climate_request.save()
        
        logger.info(f"Starting climate data processing for request {request_id}")

        # Abort immediately if user cancelled before the task got to run
        if climate_request.status == 'cancelled':
            logger.info("Request %s was cancelled before start; aborting task", request_id)
            return {'status': 'cancelled', 'error': 'Cancelled before start'}
        
        # Process the request
        processor = ClimateDataProcessor(climate_request)
        result = processor.process_request()

        # If the processor cooperatively marked as cancelled, do not retry
        if result.get('status') == 'cancelled':
            return result
        
        logger.info(f"Completed climate data processing for request {request_id}: {result}")
        
        return result
        
    except ClimateDataRequest.DoesNotExist:
        error_msg = f"Climate data request {request_id} not found"
        logger.error(error_msg)
        return {'status': 'failed', 'error': error_msg}
        
    except SoftTimeLimitExceeded as e:
        error_msg = f"Climate request {request_id} exceeded time limit: {str(e)}"
        logger.error(error_msg, exc_info=True)
        try:
            climate_request = ClimateDataRequest.objects.get(pk=request_id)
            climate_request.error_message = error_msg
            climate_request.save(update_fields=['error_message'])
        except Exception:
            pass
        # Retry with backoff; if retries exhausted, fall through to failure handling
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=120)
        try:
            climate_request = ClimateDataRequest.objects.get(pk=request_id)
            climate_request.status = 'failed'
            climate_request.completed_at = timezone.now()
            climate_request.save(update_fields=['status', 'completed_at'])
        except Exception:
            pass
        return {'status': 'failed', 'error': error_msg}
    except Exception as e:
        error_msg = f"Error processing climate request {request_id}: {str(e)}"
        logger.error(error_msg, exc_info=True)
        
        # Update request status if possible
        try:
            climate_request = ClimateDataRequest.objects.get(pk=request_id)
            climate_request.status = 'failed'
            climate_request.error_message = str(e)
            climate_request.completed_at = timezone.now()
            climate_request.save()
        except:
            pass
            
        return {'status': 'failed', 'error': error_msg}


@shared_task
def cleanup_expired_cache() -> Dict[str, Any]:
    """
    Periodic task to clean up expired cache entries.
    """
    from .models import ClimateDataCache
    
    try:
        # Delete expired cache entries
        deleted_count, _ = ClimateDataCache.objects.filter(
            expires_at__lt=timezone.now()
        ).delete()
        
        logger.info(f"Cleaned up {deleted_count} expired cache entries")
        
        return {
            'status': 'success',
            'deleted_count': deleted_count,
        }
        
    except Exception as e:
        error_msg = f"Error cleaning up cache: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {'status': 'failed', 'error': error_msg}


@shared_task
def update_data_source_availability() -> Dict[str, Any]:
    """
    Periodic task to check availability of climate data sources.
    """
    from .models import ClimateDataSource
    
    updated_count = 0
    error_count = 0
    
    try:
        for source in ClimateDataSource.objects.filter(is_active=True):
            try:
                # TODO: ping the API endpoint as a test, currently just returning current time.
                source.last_checked = timezone.now()
                source.save(update_fields=['last_checked'])
                updated_count += 1
                
            except Exception as e:
                logger.error(f"Error checking {source.name}: {str(e)}")
                error_count += 1
        
        logger.info(f"Updated {updated_count} data sources, {error_count} errors")
        
        return {
            'status': 'success',
            'updated_count': updated_count,
            'error_count': error_count,
        }
        
    except Exception as e:
        error_msg = f"Error updating data source availability: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {'status': 'failed', 'error': error_msg}


@shared_task
def generate_climate_data_report(study_id: int) -> Dict[str, Any]:
    """
    Generate a comprehensive report of climate data for a study.
    
    Args:
        study_id: ID of the study to generate report for
        
    Returns:
        Dict containing report data
    """
    try:
        from core.models import Study, Attribute, Observation
        from django.db.models import Count, Avg, Min, Max
        
        study = Study.objects.get(pk=study_id)
        
        # Get climate attributes for this study
        climate_attributes = Attribute.objects.filter(
            category='climate',
            observations__location__observations__attribute__study=study
        ).distinct()
        
        report_data = {
            'study_name': study.name,
            'generated_at': timezone.now().isoformat(),
            'variables': [],
            'summary': {
                'total_variables': climate_attributes.count(),
                'total_observations': 0,
                'date_range': {'start': None, 'end': None},
            }
        }
        
        total_observations = 0
        
        for attr in climate_attributes:
            # Get statistics for this variable
            stats = Observation.objects.filter(
                attribute=attr,
                location__observations__attribute__study=study
            ).aggregate(
                count=Count('id'),
                min_value=Min('float_value'),
                max_value=Max('float_value'),
                avg_value=Avg('float_value')
            )
            
            if stats['count'] > 0:
                variable_data = {
                    'name': attr.display_name,
                    'unit': attr.unit,
                    'count': stats['count'],
                    'min_value': stats['min_value'],
                    'max_value': stats['max_value'],
                    'avg_value': stats['avg_value'],
                }
                
                report_data['variables'].append(variable_data)
                total_observations += stats['count']
        
        report_data['summary']['total_observations'] = total_observations
        
        logger.info(f"Generated climate report for study {study_id}")
        
        return {
            'status': 'success',
            'report': report_data,
        }
        
    except Study.DoesNotExist:
        error_msg = f"Study {study_id} not found"
        logger.error(error_msg)
        return {'status': 'failed', 'error': error_msg}
        
    except Exception as e:
        error_msg = f"Error generating report for study {study_id}: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {'status': 'failed', 'error': error_msg}