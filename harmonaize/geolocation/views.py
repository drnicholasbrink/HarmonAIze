
import json
import traceback
import logging
import requests
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView
from django.conf import settings
from django.db import transaction
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone
from django.core.cache import cache

from .models import GeocodingResult, ValidationResult, ValidatedDataset
from .validation import SmartGeocodingValidator
from .tasks import batch_geocode_locations, batch_validate_locations
from .services import GeocodingService
from core.models import Location
logger = logging.getLogger(__name__)
def update_locations_from_validation():
    """
    Update core.Location coordinates from validated results.
    Only updates locations that have been validated (needs_review or validated status).
    """
    updated_count = 0


    validation_results = ValidationResult.objects.filter(
        validation_status__in=['needs_review', 'validated'],
        recommended_lat__isnull=False,
        recommended_lng__isnull=False
    ).select_related('geocoding_result')

    for validation_result in validation_results:
        try:
            # Find the corresponding location in core
            location = Location.objects.get(name=validation_result.geocoding_result.location_name)

            # Only update if location doesn't already have coordinates
            if location.latitude is None or location.longitude is None:
                with transaction.atomic():
                    location.latitude = validation_result.recommended_lat
                    location.longitude = validation_result.recommended_lng
                    location.save()
                    updated_count += 1
                    logger.info(f"Updated coordinates for location: {location.name}")

        except Location.DoesNotExist:
            logger.warning(f"Location not found in core: {validation_result.geocoding_result.location_name}")
            continue
        except Exception as e:
            logger.error(f"Failed to update location {validation_result.geocoding_result.location_name}: {e}")
            continue

    return updated_count
@login_required
def validation_map(request):
    """
    Enhanced map view with individual source scoring and source colours.

    Displays geocoding results for validation with interactive map interface.
    Shows results from multiple APIs (HDX, ArcGIS, Google, Nominatim) with
    AI-generated confidence scores and recommendations.

    Args:
        request: Django HTTP request object. May contain 'location_id' parameter
                to display specific location.

    Returns:
        HttpResponse: Rendered validation map template with location data
    """


    location_id = request.GET.get('location_id')

    if location_id:
        # Show specific location
        try:
            result = GeocodingResult.objects.get(id=location_id, created_by=request.user)
            results = [result]
        except GeocodingResult.DoesNotExist:
            results = []
    else:

        results = GeocodingResult.objects.filter(
            created_by=request.user,
            validation__validation_status__in=['needs_review', 'pending']
        ).order_by('created_at')[:1]

        if not results:

            results = GeocodingResult.objects.filter(
                created_by=request.user,
                validation__isnull=True
            ).order_by('created_at')[:1]


    # Prepare enhanced data for the template
    locations_data = []

    for result in results:
        # Ensure result belongs to current user
        if result.created_by != request.user:
            continue


        coordinates = []


        source_colours = {
            'hdx': '#3b82f6',      # Blue
            'arcgis': '#8b5cf6',   # Purple
            'google': '#dc2626',   # Red
            'nominatim': '#f59e0b' # Orange
        }

        # Ensure validation belongs to current user if it exists
        validation = getattr(result, 'validation', None)
        if validation and validation.created_by != request.user:
            validation = None


        if result.hdx_success and result.hdx_lat and result.hdx_lng:
            coordinates.append({
                'source': 'HDX',
                'source_key': 'hdx',
                'lat': result.hdx_lat,
                'lng': result.hdx_lng,
                'color': source_colours['hdx']
            })

        if result.arcgis_success and result.arcgis_lat and result.arcgis_lng:
            coordinates.append({
                'source': 'ArcGIS',
                'source_key': 'arcgis',
                'lat': result.arcgis_lat,
                'lng': result.arcgis_lng,
                'color': source_colours['arcgis']
            })

        if result.google_success and result.google_lat and result.google_lng:
            coordinates.append({
                'source': 'Google',
                'source_key': 'google',
                'lat': result.google_lat,
                'lng': result.google_lng,
                'color': source_colours['google']
            })

        if result.nominatim_success and result.nominatim_lat and result.nominatim_lng:
            coordinates.append({
                'source': 'OpenStreetMap',
                'source_key': 'nominatim',
                'lat': result.nominatim_lat,
                'lng': result.nominatim_lng,
                'color': source_colours['nominatim']
            })

        if coordinates:

            status = validation.validation_status if validation else 'pending'


            metadata = validation.validation_metadata if validation else {}
            individual_scores = metadata.get('individual_scores', {})
            reverse_geocoding = metadata.get('reverse_geocoding_results', {})
            llm_enhanced = metadata.get('llm_enhanced', False)
            llm_conflict_resolution = metadata.get('llm_conflict_resolution')
            llm_sanity_check = metadata.get('llm_sanity_check')
            llm_explanation = metadata.get('llm_explanation')
            best_source = metadata.get('best_source')
            best_score = metadata.get('best_score', 0.0)

            # Calculate confidence from available validation data
            if best_score > 0:
                confidence = best_score * 100
            elif validation:
                confidence = validation.confidence_score * 100
            else:
                confidence = 50

            # Add individual source scoring data to each coordinate
            for coord in coordinates:
                source_key = coord['source_key']

                # Add reverse geocoding information if available
                if source_key in reverse_geocoding:
                    reverse_info = reverse_geocoding[source_key]
                    coord.update({
                        'reverse_address': reverse_info.get('address', 'No address found'),
                        'name_similarity': reverse_info.get('similarity_score', 0.0),
                        'reverse_confidence': reverse_info.get('confidence', 0.0),
                        'place_type': reverse_info.get('place_type', 'unknown')
                    })
                else:
                    coord.update({
                        'reverse_address': 'Not checked',
                        'name_similarity': 0.0,
                        'reverse_confidence': 0.0,
                        'place_type': 'unknown'
                    })


                if source_key in individual_scores:
                    source_score = individual_scores[source_key]
                    reverse_score = source_score.get('reverse_geocoding_score', 0.0)
                    distance_score = source_score.get('distance_penalty_score', 0.0)
                    individual_confidence = source_score.get('individual_confidence', 0.0)

                    coord['reverse_geocoding_score'] = reverse_score * 100
                    coord['distance_penalty_score'] = distance_score * 100
                    coord['individual_confidence'] = individual_confidence * 100


                    calculated_score = (reverse_score * 0.70) + (distance_score * 0.30)

                else:
                    # Calculate fallback scores when validation data is unavailable
                    reverse_score = coord['name_similarity']
                    distance_score = 0.5
                    calculated_score = (reverse_score * 0.70) + (distance_score * 0.30)

                    coord['reverse_geocoding_score'] = reverse_score * 100
                    coord['distance_penalty_score'] = distance_score * 100
                    coord['individual_confidence'] = calculated_score * 100


                coord['overall_confidence'] = coord['individual_confidence']


                if coord['overall_confidence'] >= 90:
                    coord['confidence_description'] = 'Excellent match'
                elif coord['overall_confidence'] >= 80:
                    coord['confidence_description'] = 'Very good match'
                elif coord['overall_confidence'] >= 70:
                    coord['confidence_description'] = 'Good match'
                elif coord['overall_confidence'] >= 60:
                    coord['confidence_description'] = 'Fair match'
                else:
                    coord['confidence_description'] = 'Poor match'


            coordinates.sort(key=lambda x: x['overall_confidence'], reverse=True)


            if metadata.get('best_source'):
                # Map source keys to display names to match coordinates
                source_mapping = {
                    'hdx': 'HDX',
                    'arcgis': 'ArcGIS',
                    'google': 'Google',
                    'nominatim': 'OpenStreetMap'
                }
                best_source_key = metadata.get('best_source')
                recommended_source = source_mapping.get(best_source_key, best_source_key)
            else:
                recommended_source = coordinates[0]['source'] if coordinates else None


            variance = result.coordinate_variance or 0
            if variance < 0.5:
                accuracy_description = "Excellent agreement - all sources very close"
            elif variance < 1.0:
                accuracy_description = "Very good agreement - sources mostly aligned"
            elif variance < 2.0:
                accuracy_description = "Good agreement - minor variations between sources"
            elif variance < 5.0:
                accuracy_description = "Moderate agreement - some differences between sources"
            else:
                accuracy_description = "Variable agreement - significant differences between sources"

            # Enhanced location data with individual source scoring + LLM enhancements
            locations_data.append({
                'id': result.id,
                'name': result.location_name,
                'confidence': confidence,  # Now uses best individual source score
                'status': status,
                'coordinates': coordinates,
                'recommendation': metadata.get('recommendation', {}),
                'individual_scores': individual_scores,
                'reverse_geocoding': reverse_geocoding,
                'recommended_source': recommended_source,
                'variance': variance,
                'accuracy_description': accuracy_description,
                'max_distance_km': metadata.get('cluster_analysis', {}).get('max_distance_km', 0),
                'ai_summary': metadata.get('user_friendly_summary', 'Validation completed'),
                # LLM enhancement data
                'llm_enhanced': llm_enhanced,
                'llm_explanation': llm_explanation,
                'llm_conflict_resolution': llm_conflict_resolution,
                'llm_sanity_check': llm_sanity_check,
                'validation_method': metadata.get('validation_method', 'standard')
            })


    navigation_info = get_navigation_info(location_id, user=request.user)

    # Calculate stats for the template
    stats = get_validation_stats(user=request.user)

    context = {
        'locations_data': json.dumps(locations_data),
        'mapbox_token': getattr(settings, 'MAPBOX_ACCESS_TOKEN', ''),
        'current_location': locations_data[0] if locations_data else None,
        'current_location_json': json.dumps(locations_data[0]) if locations_data else 'null',
        'navigation': navigation_info,
        **stats
    }

    return render(request, 'geolocation/validation_map.html', context)
def get_navigation_info(current_location_id, user=None):
    """Get navigation information for next/previous locations."""

    if user:
        pending_locations = ValidationResult.objects.filter(
            validation_status__in=['needs_review', 'pending'],
            created_by=user
        ).order_by('created_at')

        if not pending_locations.exists():
            pending_locations = GeocodingResult.objects.filter(
                validation__isnull=True,
                created_by=user
            ).order_by('created_at')
    else:
        pending_locations = ValidationResult.objects.filter(
            validation_status__in=['needs_review', 'pending']
        ).order_by('created_at')

        if not pending_locations.exists():
            pending_locations = GeocodingResult.objects.filter(
                validation__isnull=True
            ).order_by('created_at')

    navigation = {
        'total_pending': pending_locations.count(),
        'current_index': 0,
        'next_location_id': None,
        'prev_location_id': None,
        'has_next': False,
        'has_prev': False
    }

    if current_location_id:
        try:
            current_location_id = int(current_location_id)
            if hasattr(pending_locations.first(), 'geocoding_result'):
                location_ids = list(pending_locations.values_list('geocoding_result__id', flat=True))
            else:
                location_ids = list(pending_locations.values_list('id', flat=True))

            if current_location_id in location_ids:
                current_index = location_ids.index(current_location_id)
                navigation['current_index'] = current_index + 1

                # Next location
                if current_index < len(location_ids) - 1:
                    navigation['next_location_id'] = location_ids[current_index + 1]
                    navigation['has_next'] = True

                # Previous location
                if current_index > 0:
                    navigation['prev_location_id'] = location_ids[current_index - 1]
                    navigation['has_prev'] = True
        except (ValueError, TypeError):
            pass

    return navigation

def get_validation_stats(user=None):
    """Calculate validation statistics for dashboard display."""
    # Core Location statistics
    total_locations = Location.objects.count()
    locations_with_coords = Location.objects.filter(
        latitude__isnull=False,
        longitude__isnull=False
    ).count()
    locations_without_coords = Location.objects.filter(
        latitude__isnull=True,
        longitude__isnull=True
    ).count()

    # Count locations that have geocoding results but need validation
    pending_validation = 0

    if user:
        for location in Location.objects.filter(latitude__isnull=True, longitude__isnull=True):
            geocoding_result = GeocodingResult.objects.filter(
                location_name__iexact=location.name,
                created_by=user
            ).first()
            if geocoding_result and geocoding_result.has_any_results:
                pending_validation += 1

        # Add locations with validation results still requiring review
        pending_validation += ValidationResult.objects.filter(
            validation_status__in=['needs_review', 'pending'],
            created_by=user
        ).count()

        # Count locations without coordinates and without geocoding results
        awaiting_geocoding = 0
        for location in Location.objects.filter(latitude__isnull=True, longitude__isnull=True):
            geocoding_result = GeocodingResult.objects.filter(
                location_name__iexact=location.name,
                created_by=user
            ).first()
            if not geocoding_result or not geocoding_result.has_any_results:
                awaiting_geocoding += 1
    else:
        for location in Location.objects.filter(latitude__isnull=True, longitude__isnull=True):
            geocoding_result = GeocodingResult.objects.filter(
                location_name__iexact=location.name
            ).first()
            if geocoding_result and geocoding_result.has_any_results:
                pending_validation += 1

        # Add locations with validation results still requiring review
        pending_validation += ValidationResult.objects.filter(
            validation_status__in=['needs_review', 'pending']
        ).count()

        # Count locations without coordinates and without geocoding results
        awaiting_geocoding = 0
        for location in Location.objects.filter(latitude__isnull=True, longitude__isnull=True):
            geocoding_result = GeocodingResult.objects.filter(
                location_name__iexact=location.name
            ).first()
            if not geocoding_result or not geocoding_result.has_any_results:
                awaiting_geocoding += 1

    if user:
        return {
            'total_locations': total_locations,
            'awaiting_geocoding': awaiting_geocoding,  # No coordinates, no geocoding results
            'pending_validation': pending_validation,  # Has geocoding results but needs validation
            'validated_complete': locations_with_coords,  # Has final coordinates
            'high_confidence': ValidationResult.objects.filter(confidence_score__gte=0.8, created_by=user).count(),
            'medium_confidence': ValidationResult.objects.filter(
                confidence_score__gte=0.6, confidence_score__lt=0.8, created_by=user
            ).count(),
            'low_confidence': ValidationResult.objects.filter(confidence_score__lt=0.6, created_by=user).count(),
            'needs_review': ValidationResult.objects.filter(
                validation_status='needs_review', created_by=user
            ).count(),
            'manual_review': ValidationResult.objects.filter(
                validation_status='pending', created_by=user
            ).count(),
            'auto_validated': ValidationResult.objects.filter(
                validation_status='validated', created_by=user
            ).count(),
        }
    else:
        return {
            'total_locations': total_locations,
            'awaiting_geocoding': awaiting_geocoding,  # No coordinates, no geocoding results
            'pending_validation': pending_validation,  # Has geocoding results but needs validation
            'validated_complete': locations_with_coords,  # Has final coordinates
            'high_confidence': ValidationResult.objects.filter(confidence_score__gte=0.8).count(),
            'medium_confidence': ValidationResult.objects.filter(
                confidence_score__gte=0.6, confidence_score__lt=0.8
            ).count(),
            'low_confidence': ValidationResult.objects.filter(confidence_score__lt=0.6).count(),
            'needs_review': ValidationResult.objects.filter(
                validation_status='needs_review'
            ).count(),
            'manual_review': ValidationResult.objects.filter(
                validation_status='pending'
            ).count(),
            'auto_validated': ValidationResult.objects.filter(
                validation_status='validated'
            ).count(),
        }

class ValidationDashboardView(LoginRequiredMixin, TemplateView):
    """Enhanced validation dashboard with summary and actions."""
    template_name = 'geolocation/validation_dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)


        stats = {
            'total_validations': ValidationResult.objects.filter(created_by=self.request.user).count(),
            'auto_validated': ValidationResult.objects.filter(validation_status='validated', created_by=self.request.user).count(),
            'needs_review': ValidationResult.objects.filter(validation_status='needs_review', created_by=self.request.user).count(),
            'pending_manual': ValidationResult.objects.filter(validation_status='pending', created_by=self.request.user).count(),
            'rejected': ValidationResult.objects.filter(validation_status='rejected', created_by=self.request.user).count(),
            'high_confidence': ValidationResult.objects.filter(confidence_score__gte=0.8, created_by=self.request.user).count(),
            'medium_confidence': ValidationResult.objects.filter(
                confidence_score__gte=0.6, confidence_score__lt=0.8, created_by=self.request.user
            ).count(),
            'low_confidence': ValidationResult.objects.filter(confidence_score__lt=0.6, created_by=self.request.user).count(),
        }


        recent_validations = ValidationResult.objects.filter(
            validation_status__in=['needs_review', 'pending'],
            created_by=self.request.user
        ).select_related('geocoding_result').order_by('-created_at')[:10]

        context.update({
            'stats': stats,
            'recent_validations': recent_validations,
        })

        return context

@login_required
@csrf_exempt
def location_status_api(request):
    """ API endpoint to get comprehensive location status for dashboard table."""
    if request.method == 'GET':
        try:
            locations_data = []
            locations = Location.objects.all().order_by('name')

            for location in locations:
                # Determine current status with automatic validation updates
                if location.latitude is not None and location.longitude is not None:
                    status = 'validated'
                    status_display = 'Validated & Complete'
                    status_colour = 'green'
                    confidence = 100
                    sources = ['Final']
                    coordinates = {'lat': location.latitude, 'lng': location.longitude}
                    geocoding_result_id = None
                else:
                    # Look for geocoding result (user's own results only)
                    geocoding_result = GeocodingResult.objects.filter(
                        location_name__iexact=location.name,
                        created_by=request.user
                    ).first()

                    if geocoding_result:
                        geocoding_result_id = geocoding_result.id


                        validation = getattr(geocoding_result, 'validation', None)

                        if validation:

                            if validation.validation_status == 'validated':

                                final_coords = validation.final_coordinates
                                if final_coords:
                                    lat, lng = final_coords
                                    location.latitude = lat
                                    location.longitude = lng
                                    location.save()

                                    status = 'validated'
                                    status_display = 'Validated & Complete'
                                    status_colour = 'green'
                                    confidence = 100
                                    sources = ['Final']
                                    coordinates = {'lat': lat, 'lng': lng}
                                else:
                                    # Validation exists but no final coordinates
                                    status = 'needs_review'
                                    status_display = 'Validation Error - Review Required'
                                    status_colour = 'orange'
                                    confidence = int(validation.confidence_score * 100)
                                    sources = []
                                    coordinates = None
                            elif validation.validation_status == 'needs_review':
                                status = 'needs_review'
                                status_display = 'Good Quality - Quick Review'
                                status_colour = 'yellow'

                                metadata = validation.validation_metadata or {}
                                best_score = metadata.get('best_score', validation.confidence_score)
                                confidence = int(best_score * 100)
                            elif validation.validation_status == 'pending':
                                status = 'pending'
                                status_display = 'Lower Quality - Detailed Review'
                                status_colour = 'orange'

                                metadata = validation.validation_metadata or {}
                                best_score = metadata.get('best_score', validation.confidence_score)
                                confidence = int(best_score * 100)
                            elif validation.validation_status == 'rejected':
                                status = 'rejected'
                                status_display = 'Rejected - Invalid Location'
                                status_colour = 'red'
                                confidence = 0
                            else:
                                status = 'geocoded'
                                status_display = 'Ready for Validation'
                                status_colour = 'blue'
                                confidence = 50
                        else:
                            # No validation yet, but has geocoding results
                            if geocoding_result.has_any_results:
                                status = 'geocoded'
                                status_display = 'Ready for Validation'
                                status_colour = 'blue'
                                confidence = 50
                            else:
                                status = 'awaiting_geocoding'
                                status_display = 'Awaiting Geocoding'
                                status_colour = 'red'
                                confidence = 0


                        sources = []
                        coordinates = None

                        if geocoding_result.has_any_results:
                            if geocoding_result.hdx_success:
                                sources.append('HDX')
                            if geocoding_result.arcgis_success:
                                sources.append('ArcGIS')
                            if geocoding_result.google_success:
                                sources.append('Google')
                            if geocoding_result.nominatim_success:
                                sources.append('OSM')


                            if geocoding_result.hdx_success and geocoding_result.hdx_lat:
                                coordinates = {'lat': geocoding_result.hdx_lat, 'lng': geocoding_result.hdx_lng}
                            elif geocoding_result.arcgis_success and geocoding_result.arcgis_lat:
                                coordinates = {'lat': geocoding_result.arcgis_lat, 'lng': geocoding_result.arcgis_lng}
                            elif geocoding_result.google_success and geocoding_result.google_lat:
                                coordinates = {'lat': geocoding_result.google_lat, 'lng': geocoding_result.google_lng}
                            elif geocoding_result.nominatim_success and geocoding_result.nominatim_lat:
                                coordinates = {'lat': geocoding_result.nominatim_lat, 'lng': geocoding_result.nominatim_lng}
                    else:
                        # No geocoding result at all
                        status = 'awaiting_geocoding'
                        status_display = 'Awaiting Geocoding'
                        status_colour = 'red'
                        confidence = 0
                        sources = []
                        coordinates = None
                        geocoding_result_id = None


                if status == 'validated' and location.latitude and location.longitude:
                    coordinates = {'lat': location.latitude, 'lng': location.longitude}
                    sources = ['Final']

                locations_data.append({
                    'id': location.id,
                    'name': location.name,
                    'status': status,
                    'status_display': status_display,
                    'status_colour': status_colour,
                    'confidence': confidence,
                    'sources': sources,
                    'coordinates': coordinates,
                    'geocoding_result_id': geocoding_result_id
                })


            summary = {
                'total': len(locations_data),
                'awaiting_geocoding': len([l for l in locations_data if l['status'] == 'awaiting_geocoding']),
                'geocoded': len([l for l in locations_data if l['status'] == 'geocoded']),
                'needs_review': len([l for l in locations_data if l['status'] == 'needs_review']),
                'pending': len([l for l in locations_data if l['status'] == 'pending']),
                'validated': len([l for l in locations_data if l['status'] == 'validated']),
                'rejected': len([l for l in locations_data if l['status'] == 'rejected'])
            }

            return JsonResponse({
                'success': True,
                'locations': locations_data,
                'summary': summary,
                'timestamp': timezone.now().isoformat()
            })

        except Exception as e:
            logger.error(f"Error fetching location status: {str(e)}")
            return JsonResponse({
                'success': False,
                'error': f'Failed to fetch location status: {str(e)}'
            }, status=500)

    return JsonResponse({'error': 'Only GET requests are allowed'}, status=405)

@login_required
@csrf_exempt
def validation_queue_api(request):
    """API endpoint to get actual validation queue data for the table."""
    if request.method == 'GET':
        try:

            pending_validations = ValidationResult.objects.filter(
                validation_status__in=['needs_review', 'pending'],
                created_by=request.user
            ).select_related('geocoding_result').order_by('-confidence_score')[:20]


            if not pending_validations.exists():
                unvalidated_results = GeocodingResult.objects.filter(
                    validation__isnull=True,
                    created_by=request.user
                ).order_by('-created_at')[:20]

                locations_data = []
                for result in unvalidated_results:
                    # Determine available sources
                    sources = []
                    if result.hdx_success:
                        sources.append('HDX')
                    if result.arcgis_success:
                        sources.append('ArcGIS')
                    if result.google_success:
                        sources.append('Google')
                    if result.nominatim_success:
                        sources.append('OSM')

                    locations_data.append({
                        'id': result.id,
                        'name': result.location_name,
                        'confidence': 50,  # Default confidence for unanalyzed
                        'status': 'pending',
                        'sources': sources
                    })

                return JsonResponse({
                    'success': True,
                    'locations': locations_data,
                    'message': 'Showing unanalyzed geocoding results'
                })

            # Process validated results
            locations_data = []
            for validation in pending_validations:
                result = validation.geocoding_result

                # Determine available sources
                sources = []
                if result.hdx_success:
                    sources.append('HDX')
                if result.arcgis_success:
                    sources.append('ArcGIS')
                if result.google_success:
                    sources.append('Google')
                if result.nominatim_success:
                    sources.append('OSM')


                metadata = validation.validation_metadata or {}
                best_score = metadata.get('best_score', validation.confidence_score)

                locations_data.append({
                    'id': result.id,
                    'name': result.location_name,
                    'confidence': best_score * 100,
                    'status': validation.validation_status,
                    'sources': sources
                })

            return JsonResponse({
                'success': True,
                'locations': locations_data
            })

        except Exception as e:
            logger.error(f"Error fetching validation queue: {str(e)}")
            return JsonResponse({
                'success': False,
                'error': f'Failed to fetch validation queue: {str(e)}'
            }, status=500)

    return JsonResponse({'error': 'Only GET requests are allowed'}, status=405)
@login_required
@csrf_exempt
def validation_api(request):
    """Enhanced API endpoint for validation actions with better error handling."""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            action = data.get('action')
            validation_id = data.get('validation_id')
            geocoding_result_id = data.get('geocoding_result_id')


            if validation_id:
                validation = get_object_or_404(ValidationResult, id=validation_id, created_by=request.user)
            elif geocoding_result_id:
                geocoding_result = get_object_or_404(GeocodingResult, id=geocoding_result_id, created_by=request.user)
                validation = getattr(geocoding_result, 'validation', None)
                if validation and validation.created_by != request.user:
                    return JsonResponse({
                        'success': False,
                        'error': 'Access denied: This validation does not belong to you.'
                    }, status=403)
                if not validation:
                    # Create validation if it doesn't exist using simplified Auto-Validation scoring
                    validator = SmartGeocodingValidator()
                    validation = validator.validate_geocoding_result(geocoding_result)
            else:
                return JsonResponse({
                    'success': False,
                    'error': 'Missing location identifier. Please specify either validation_id or geocoding_result_id.'
                }, status=400)

            if action == 'approve_suggestion':
                return handle_approve_ai_suggestion(validation, data)
            elif action == 'manual_coordinates':
                return handle_manual_coordinates(validation, data)
            elif action == 'reject':
                return handle_reject(validation, data)
            elif action == 'get_details':
                return get_enhanced_validation_details(validation)
            elif action == 'use_source':
                return handle_use_source(validation, data)
            elif action == 'run_ai_analysis':
                return run_ai_analysis(validation)
            else:
                return JsonResponse({
                    'success': False,
                    'error': f'Unknown action: {action}. Available actions: approve_suggestion, manual_coordinates, reject, get_details, use_source, run_ai_analysis'
                }, status=400)

        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Invalid JSON format in request body'
            }, status=400)
        except Exception as e:
            logger.error(f"Validation API Error: {str(e)}")
            return JsonResponse({
                'success': False,
                'error': f'An unexpected error occurred while processing your request: {str(e)}'
            }, status=500)

    return JsonResponse({'error': 'Only POST requests are allowed'}, status=405)
@login_required
@csrf_exempt
def geocoding_api(request):
    """API endpoint for running geocoding from the interface with CLEARER statistics."""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            action = data.get('action')

            if action == 'run_geocoding':
                limit = data.get('limit', None)
                force = data.get('force', False)


                geocoding_service = GeocodingService()


                locations = Location.objects.filter(latitude__isnull=True, longitude__isnull=True)

                if limit:
                    locations = locations[:limit]

                if not locations.exists():
                    return JsonResponse({
                        'success': True,
                        'message': 'All locations already have coordinates',
                        'stats': {
                            'processed': 0,
                            'found_coordinates': 0,
                            'no_results': 0,
                            'from_cache': 0,
                            'new_searches': 0
                        }
                    })

                # Process locations
                found_coordinates = 0
                no_results = 0
                from_cache = 0
                new_searches = 0

                for location in locations:
                    try:
                        logger.info(f"Processing location: '{location.name}' (ID: {location.id})")

                        validated_result = geocoding_service.check_validated_dataset(location)
                        logger.info(f"Validated result for '{location.name}': {validated_result}")
                        if validated_result:

                            with transaction.atomic():
                                location.latitude = validated_result.final_lat
                                location.longitude = validated_result.final_long
                                location.save()
                            from_cache += 1
                            found_coordinates += 1
                            continue


                        if not force:
                            existing_result = GeocodingResult.objects.filter(
                                location_name__iexact=location.name,
                                created_by=request.user
                            ).first()

                            if existing_result and existing_result.has_any_results:

                                validation_result = getattr(existing_result, 'validation', None)
                                if validation_result and validation_result.recommended_lat and validation_result.recommended_lng:
                                    with transaction.atomic():
                                        location.latitude = validation_result.recommended_lat
                                        location.longitude = validation_result.recommended_lng
                                        location.save()
                                    found_coordinates += 1
                                    continue
                                else:
                                    # Results exist but not validated yet
                                    found_coordinates += 1
                                    continue

                        # Perform new geocoding search
                        result = geocoding_service.geocode_single_location(location, force)
                        success = result is not None
                        if success:
                            new_searches += 1
                            found_coordinates += 1
                        else:
                            no_results += 1

                    except Exception as e:
                        logger.error(f"Error geocoding {location.name}: {e}")
                        no_results += 1

                processed = found_coordinates + no_results

                return JsonResponse({
                    'success': True,
                    'message': f'Coordinate search completed: {found_coordinates} locations now have coordinates, {no_results} locations could not be geocoded',
                    'stats': {
                        'processed': processed,
                        'found_coordinates': found_coordinates,  # Successfully found coordinates
                        'no_results': no_results,  # Failed to find coordinates
                        'from_cache': from_cache,  # Retrieved from validated dataset
                        'new_searches': new_searches  # New API calls made
                    }
                })

            elif action == 'run_validation':
                # Import validation functionality
                from .validation import run_smart_validation

                limit = data.get('limit', None)
                validation_stats = run_smart_validation(limit)

                # After validation, update Location coordinates for validated results
                updated_locations = update_locations_from_validation()
                validation_stats['updated_locations'] = updated_locations

                return JsonResponse({
                    'success': True,
                    'message': f'Validation completed: {validation_stats["processed"]} results processed, {updated_locations} locations updated',
                    'stats': validation_stats,
                    'updated_locations': updated_locations
                })

            elif action == 'update_coordinates':
                # Update Location coordinates from validation results
                updated_locations = update_locations_from_validation()

                return JsonResponse({
                    'success': True,
                    'message': f'Updated coordinates for {updated_locations} locations',
                    'updated_locations': updated_locations
                })

            elif action == 'get_geocoding_stats':

                stats = get_validation_stats()

                return JsonResponse({
                    'success': True,
                    'stats': {
                        'total_locations': stats['total_locations'],
                        'awaiting_geocoding': stats['awaiting_geocoding'],  # Locations without coordinates
                        'pending_validation': stats['pending_validation'],  # Geocoded but awaiting validation
                        'validated_complete': stats['validated_complete'],  # Locations with coordinates
                        'completion_rate': (stats['validated_complete'] / stats['total_locations'] * 100) if stats['total_locations'] > 0 else 0
                    }
                })
            else:
                return JsonResponse({
                    'success': False,
                    'error': f'Unknown action: {action}. Available actions: run_geocoding, run_validation, update_coordinates, get_geocoding_stats'
                }, status=400)

        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Invalid JSON format in request body'
            }, status=400)
        except Exception as e:
            logger.error(f"Geocoding API Error: {str(e)}")
            return JsonResponse({
                'success': False,
                'error': f'An unexpected error occurred: {str(e)}'
            }, status=500)

    return JsonResponse({'error': 'Only POST requests are allowed'}, status=405)
@login_required
@csrf_exempt
def bulk_validation_actions(request):
    """FIXED: Handle bulk validation actions with enhanced auto-approve logic."""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            action = data.get('action')

            if action == 'auto_validate_high_confidence':

                total_geocoding_results = GeocodingResult.objects.filter(created_by=request.user).count()
                if total_geocoding_results == 0:
                    return JsonResponse({
                        'success': False,
                        'error': 'Please run coordinate search first.'
                    }, status=400)


                total_validations = ValidationResult.objects.filter(created_by=request.user).count()
                if total_validations == 0:
                    return JsonResponse({
                        'success': False,
                        'error': 'Please wait for validation to complete.'
                    }, status=400)


                # Look for ValidationResults with status = 'needs_review'
                high_confidence_results = ValidationResult.objects.filter(
                    validation_status='needs_review',  # Only those needing review
                    created_by=request.user
                ).select_related('geocoding_result')

                #Filter by best individual source confidence (≥80%)
                qualified_results = []
                for validation in high_confidence_results:
                    metadata = validation.validation_metadata or {}
                    best_score = metadata.get('best_score', validation.confidence_score)


                    if best_score >= 0.8:  # 80% threshold
                        qualified_results.append(validation)

                if not qualified_results:
                    return JsonResponse({
                        'success': True,
                        'message': 'No high-confidence locations found to approve.'
                    })

                count = 0
                errors = 0
                for validation in qualified_results:
                    try:

                        with transaction.atomic():
                            result = validation.geocoding_result
                            metadata = validation.validation_metadata or {}
                            best_source = metadata.get('best_source')


                            if best_source == 'hdx' and result.hdx_success:
                                final_lat, final_lng = result.hdx_lat, result.hdx_lng
                            elif best_source == 'arcgis' and result.arcgis_success:
                                final_lat, final_lng = result.arcgis_lat, result.arcgis_lng
                            elif best_source == 'google' and result.google_success:
                                final_lat, final_lng = result.google_lat, result.google_lng
                            elif best_source == 'nominatim' and result.nominatim_success:
                                final_lat, final_lng = result.nominatim_lat, result.nominatim_lng
                            else:
                                errors += 1
                                continue

                            # Update validation status
                            validation.validation_status = 'validated'
                            validation.validated_at = timezone.now()
                            validation.validated_by = 'Auto_Approve_High_Confidence'
                            validation.recommended_lat = final_lat
                            validation.recommended_lng = final_lng
                            validation.recommended_source = best_source
                            validation.save()

                            # Add to ValidatedDataset (POI arsenal)
                            ValidatedDataset.objects.update_or_create(
                                location_name=result.location_name,
                                defaults={
                                    'final_lat': final_lat,
                                    'final_long': final_lng,
                                    'country': '',
                                    'source': f'auto_approve_{best_source}',
                                    'validated_at': timezone.now()
                                }
                            )


                            try:
                                location = Location.objects.get(name__iexact=result.location_name)
                                location.latitude = final_lat
                                location.longitude = final_lng
                                location.save()
                                count += 1
                            except Location.DoesNotExist:
                                errors += 1
                            except Location.MultipleObjectsReturned:
                                location = Location.objects.filter(name__iexact=result.location_name).first()
                                location.latitude = final_lat
                                location.longitude = final_lng
                                location.save()
                                count += 1

                    except Exception as e:
                        logger.error(f"Error auto-validating {validation.geocoding_result.location_name}: {e}")
                        errors += 1
                        continue

                if count > 0:
                    return JsonResponse({
                        'success': True,
                        'message': f'Successfully approved {count} locations' + (f' ({errors} had errors)' if errors > 0 else '')
                    })
                else:
                    return JsonResponse({
                        'success': False,
                        'error': f'Failed to auto-approve any locations. {errors} errors occurred during processing.'
                    })

            elif action == 'run_smart_validation_batch':

                total_geocoding_results = GeocodingResult.objects.filter(created_by=request.user).count()
                if total_geocoding_results == 0:
                    return JsonResponse({
                        'success': False,
                        'error': 'Please run coordinate search first.'
                    }, status=400)


                pending_results = GeocodingResult.objects.filter(
                    validation__isnull=True,
                    created_by=request.user
                ).exclude(validation_status='rejected')

                if not pending_results.exists():
                    return JsonResponse({
                        'success': True,
                        'message': 'All locations already validated.'
                    })

                # Run validation manually with proper error handling
                try:
                    validator = SmartGeocodingValidator()

                    stats = {
                        'processed': 0,
                        'auto_validated': 0,
                        'needs_review': 0,
                        'pending': 0,
                        'rejected': 0
                    }

                    # Process up to 50 results
                    for result in pending_results[:50]:
                        try:
                            validation = validator.validate_geocoding_result(result)
                            stats['processed'] += 1

                            # Map validation status to stats
                            if validation.validation_status == 'validated':
                                stats['auto_validated'] += 1
                            elif validation.validation_status == 'needs_review':
                                stats['needs_review'] += 1
                            elif validation.validation_status == 'pending':
                                stats['pending'] += 1
                            else:
                                stats['rejected'] += 1

                        except requests.exceptions.Timeout:
                            logger.warning(f"External API timeout during validation of {result.location_name}")
                            stats['processed'] += 1
                            stats['needs_review'] += 1
                            continue
                        except Exception as e:
                            logger.error(f"Error validating {result.location_name}: {e}")
                            stats['rejected'] += 1
                            continue

                    if stats['processed'] == 0:
                        return JsonResponse({
                            'success': True,
                            'message': 'All locations already validated.'
                        })

                    return JsonResponse({
                        'success': True,
                        'message': f'Validation completed: {stats["processed"]} locations processed. {stats["needs_review"]} ready for review, {stats["pending"]} need verification.',
                        'stats': stats
                    })

                except Exception as e:
                    logger.error(f"Error running Auto-Validation : {str(e)}")
                    return JsonResponse({
                        'success': False,
                        'error': f'Auto-Validation analysis failed: {str(e)}'
                    }, status=500)
            else:
                return JsonResponse({
                    'success': False,
                    'error': f'Unknown bulk action: {action}. Available actions: auto_validate_high_confidence, run_smart_validation_batch'
                }, status=400)

        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Invalid JSON format in request body'
            }, status=400)
        except Exception as e:
            logger.error(f"Bulk validation error: {str(e)}")
            return JsonResponse({
                'success': False,
                'error': f'An unexpected error occurred during bulk operation: {str(e)}'
            }, status=500)

    return JsonResponse({'error': 'Only POST requests are allowed'}, status=405)

def handle_approve_ai_suggestion(validation, data):
    """Handle approval of AI suggestion with enhanced error handling and status updates."""
    try:

        metadata = validation.validation_metadata or {}
        best_source = metadata.get('best_source')

        if not best_source:
            return JsonResponse({
                'success': False,
                'error': 'No validation available. Please run validation first.'
            }, status=400)

        with transaction.atomic():
            result = validation.geocoding_result


            if best_source == 'hdx' and result.hdx_success:
                final_lat, final_lng = result.hdx_lat, result.hdx_lng
            elif best_source == 'arcgis' and result.arcgis_success:
                final_lat, final_lng = result.arcgis_lat, result.arcgis_lng
            elif best_source == 'google' and result.google_success:
                final_lat, final_lng = result.google_lat, result.google_lng
            elif best_source == 'nominatim' and result.nominatim_success:
                final_lat, final_lng = result.nominatim_lat, result.nominatim_lng
            else:
                return JsonResponse({
                    'success': False,
                    'error': f'Recommended source ({best_source.upper()}) has invalid coordinates. Please select another source.'
                }, status=400)


            validation.validation_status = 'validated'
            validation.validated_at = timezone.now()
            validation.validated_by = 'Two_Component_Recommendation'
            validation.recommended_lat = final_lat
            validation.recommended_lng = final_lng
            validation.recommended_source = best_source
            validation.save()


            ValidatedDataset.objects.update_or_create(
                location_name=result.location_name,
                defaults={
                    'final_lat': final_lat,
                    'final_long': final_lng,
                    'country': '',  # Add country if available
                    'source': f'two_component_{best_source}',
                    'validated_at': timezone.now()
                }
            )


            try:
                location = Location.objects.get(name__iexact=result.location_name)
                location.latitude = final_lat
                location.longitude = final_lng
                location.save()
            except Location.DoesNotExist:

                locations = Location.objects.filter(name__icontains=result.location_name)
                if locations.exists():
                    location = locations.first()
                    location.latitude = final_lat
                    location.longitude = final_lng
                    location.save()
            except Location.MultipleObjectsReturned:
                location = Location.objects.filter(name__iexact=result.location_name).first()
                location.latitude = final_lat
                location.longitude = final_lng
                location.save()

            return JsonResponse({
                'success': True,
                'message': f'{result.location_name} validated using {best_source.upper()} coordinates',
                'coordinates': {'lat': final_lat, 'lng': final_lng},
                'source': best_source,
                'status': 'validated',
                'trigger_refresh': True
            })

    except Exception as e:
        logger.error(f"Error approving Auto-Validation suggestion: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'Failed to approve Auto-Validation suggestion: {str(e)}'
        }, status=500)

def handle_use_source(validation, data):
    """Handle user selecting a specific source with enhanced error handling and status updates."""
    try:
        source = data.get('source')

        if not source:
            return JsonResponse({
                'success': False,
                'error': 'No source specified. Please select a data source (hdx, arcgis, google, or nominatim).'
            }, status=400)

        with transaction.atomic():
            result = validation.geocoding_result


            if source == 'hdx' and result.hdx_success:
                final_lat, final_lng = result.hdx_lat, result.hdx_lng
            elif source == 'arcgis' and result.arcgis_success:
                final_lat, final_lng = result.arcgis_lat, result.arcgis_lng
            elif source == 'google' and result.google_success:
                final_lat, final_lng = result.google_lat, result.google_lng
            elif source == 'nominatim' and result.nominatim_success:
                final_lat, final_lng = result.nominatim_lat, result.nominatim_lng
            else:
                return JsonResponse({
                    'success': False,
                    'error': f'The selected source ({source.upper()}) does not have valid coordinates for this location. Please try a different source.'
                }, status=400)


            validation.validation_status = 'validated'
            validation.validated_at = timezone.now()
            validation.validated_by = 'User_Selection'
            validation.manual_review_notes = f'User manually selected {source.upper()} coordinates'
            validation.recommended_lat = final_lat
            validation.recommended_lng = final_lng
            validation.recommended_source = source
            validation.save()


            ValidatedDataset.objects.update_or_create(
                location_name=result.location_name,
                defaults={
                    'final_lat': final_lat,
                    'final_long': final_lng,
                    'country': '',
                    'source': source,
                    'validated_at': timezone.now()
                }
            )


            try:
                location = Location.objects.get(name__iexact=result.location_name)
                location.latitude = final_lat
                location.longitude = final_lng
                location.save()
            except Location.DoesNotExist:

                locations = Location.objects.filter(name__icontains=result.location_name)
                if locations.exists():
                    location = locations.first()
                    location.latitude = final_lat
                    location.longitude = final_lng
                    location.save()
            except Location.MultipleObjectsReturned:
                location = Location.objects.filter(name__iexact=result.location_name).first()
                location.latitude = final_lat
                location.longitude = final_lng
                location.save()

            return JsonResponse({
                'success': True,
                'message': f'Using {source.upper()} coordinates for {result.location_name}',
                'coordinates': {'lat': final_lat, 'lng': final_lng},
                'source': source,
                'status': 'validated',
                'trigger_refresh': True
            })

    except Exception as e:
        logger.error(f"Error using source: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'Failed to use selected source: {str(e)}'
        }, status=500)

def handle_manual_coordinates(validation, data):
    """Handle manual coordinate entry with enhanced validation and status updates."""
    try:
        lat = float(data.get('lat', 0))
        lng = float(data.get('lng', 0))
        notes = data.get('notes', '')

        # Validate coordinates
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return JsonResponse({
                'success': False,
                'error': 'Invalid coordinates: Latitude must be between -90 and 90, longitude between -180 and 180'
            }, status=400)

        with transaction.atomic():
            result = validation.geocoding_result


            validation.manual_lat = lat
            validation.manual_lng = lng
            validation.manual_review_notes = notes
            validation.validation_status = 'validated'
            validation.validated_at = timezone.now()
            validation.validated_by = 'Manual_Entry'
            validation.confidence_score = 1.0  # Manual entry gets highest confidence
            validation.save()

            #  Add to ValidatedDataset (POI arsenal)
            ValidatedDataset.objects.update_or_create(
                location_name=result.location_name,
                defaults={
                    'final_lat': lat,
                    'final_long': lng,
                    'country': '',
                    'source': 'manual_entry',
                    'validated_at': timezone.now()
                }
            )


            try:
                location = Location.objects.get(name__iexact=result.location_name)
                location.latitude = lat
                location.longitude = lng
                location.save()
            except Location.DoesNotExist:

                locations = Location.objects.filter(name__icontains=result.location_name)
                if locations.exists():
                    location = locations.first()
                    location.latitude = lat
                    location.longitude = lng
                    location.save()
            except Location.MultipleObjectsReturned:
                location = Location.objects.filter(name__iexact=result.location_name).first()
                location.latitude = lat
                location.longitude = lng
                location.save()

        return JsonResponse({
            'success': True,
            'message': f'Manual coordinates saved for {result.location_name}',
            'coordinates': {'lat': lat, 'lng': lng},
            'source': 'manual',
            'status': 'validated',
            'trigger_refresh': True
        })

    except ValueError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid coordinate values. Please enter valid numbers for latitude and longitude.'
        }, status=400)
    except Exception as e:
        logger.error(f"Error saving manual coordinates: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'Failed to save manual coordinates: {str(e)}'
        }, status=500)
def handle_reject(validation, data):
    """Handle rejection of location with enhanced validation."""
    try:
        notes = data.get('notes', '')

        if not notes or not notes.strip():
            return JsonResponse({
                'success': False,
                'error': 'Please provide a reason for rejecting this location.'
            }, status=400)

        with transaction.atomic():
            validation.validation_status = 'rejected'
            validation.validated_at = timezone.now()
            validation.validated_by = 'User_Rejection'
            validation.manual_review_notes = notes
            validation.save()

        return JsonResponse({
            'success': True,
            'message': f'Location rejected: {validation.geocoding_result.location_name}'
        })

    except Exception as e:
        logger.error(f"Error rejecting location: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'Failed to reject location: {str(e)}'
        }, status=500)
def get_enhanced_validation_details(validation):
    """Get detailed validation information with Auto-Validation analysis."""
    try:
        result = validation.geocoding_result
        metadata = validation.validation_metadata or {}


        coordinates = []
        sources = ['hdx', 'arcgis', 'google', 'nominatim']
        reverse_geocoding = metadata.get('reverse_geocoding_results', {})
        individual_scores = metadata.get('individual_scores', {})

        for source in sources:
            if getattr(result, f"{source}_success", False):
                lat = getattr(result, f"{source}_lat")
                lng = getattr(result, f"{source}_lng")


                reverse_info = reverse_geocoding.get(source, {})


                score_info = individual_scores.get(source, {})

                coordinates.append({
                    'source': source.upper(),
                    'lat': lat,
                    'lng': lng,
                    'maps_url': f"https://www.google.com/maps/@{lat},{lng},15z",
                    'reverse_address': reverse_info.get('address', 'Not available'),
                    'name_similarity': reverse_info.get('similarity_score', 0.0) * 100,
                    'reverse_confidence': reverse_info.get('confidence', 0.0) * 100,
                    'place_type': reverse_info.get('place_type', 'unknown'),
                    'individual_confidence': score_info.get('individual_confidence', 0.0) * 100,
                    'reverse_geocoding_score': score_info.get('reverse_geocoding_score', 0.0) * 100,
                    'distance_penalty_score': score_info.get('distance_penalty_score', 0.0) * 100
                })


        best_source = metadata.get('best_source', 'Unknown')
        best_score = metadata.get('best_score', 0.0)

        # Convert variance to user-friendly description
        variance = result.coordinate_variance or 0
        if variance < 0.5:
            accuracy_description = "Excellent agreement - all sources very close"
            distance_quality = "excellent"
        elif variance < 1.0:
            accuracy_description = "Very good agreement - sources mostly aligned"
            distance_quality = "good"
        elif variance < 2.0:
            accuracy_description = "Good agreement - minor variations between sources"
            distance_quality = "moderate"
        else:
            accuracy_description = "Variable agreement - significant differences between sources"
            distance_quality = "poor"

        return JsonResponse({
            'success': True,
            'data': {
                'name': result.location_name,
                'confidence': validation.confidence_score * 100,
                'status': validation.validation_status,
                'coordinates': coordinates,
                'analysis': {
                    'best_source': best_source,
                    'best_score': best_score * 100,
                    'max_distance_km': metadata.get('cluster_analysis', {}).get('max_distance_km', 0),
                    'avg_distance_km': metadata.get('cluster_analysis', {}).get('avg_distance_km', 0),
                    'source_count': metadata.get('sources_count', 0)
                },
                'recommendation': metadata.get('recommendation', {}),
                'variance': variance,
                'accuracy_description': accuracy_description,
                'distance_quality': distance_quality,
                'ai_summary': metadata.get('user_friendly_summary', 'Validation analysis completed'),
                'reverse_geocoding_results': reverse_geocoding,
                'individual_scores': individual_scores,
                'validation_flags': metadata.get('validation_flags', [])
            }
        })

    except Exception as e:
        logger.error(f"Error getting Auto-Validation  details: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'Failed to get Auto-Validation  details: {str(e)}'
        }, status=500)
def run_ai_analysis(validation):
    """Re-run Auto-Validation analysis on a validation result with external API timeout handling."""
    try:
        validator = SmartGeocodingValidator()

        updated_validation = validator.validate_geocoding_result(validation.geocoding_result)

        return JsonResponse({
            'success': True,
            'message': 'Validation completed successfully',
            'confidence': updated_validation.confidence_score * 100,
            'status': updated_validation.validation_status,
            'two_component': True
        })
    except requests.exceptions.Timeout:
        logger.warning(f"External API timeout during validation of {validation.geocoding_result.location_name}")
        return JsonResponse({
            'success': True,
            'message': 'Auto-Validation analysis completed with basic factors (external APIs temporarily unavailable)',
            'confidence': validation.confidence_score * 100,
            'status': validation.validation_status,
            'two_component': False
        })
    except Exception as e:
        logger.error(f"Error running Auto-Validation analysis: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'Auto-Validation analysis failed: {str(e)}'
        }, status=500)
@login_required
def validation_statistics(request):
    """Get detailed validation statistics for dashboard with enhanced error handling."""
    try:
        # Basic counts
        total_locations = GeocodingResult.objects.filter(created_by=request.user).count()
        total_validations = ValidationResult.objects.filter(created_by=request.user).count()

        # Confidence distribution
        high_confidence = ValidationResult.objects.filter(confidence_score__gte=0.8, created_by=request.user).count()
        medium_confidence = ValidationResult.objects.filter(
            confidence_score__gte=0.6, confidence_score__lt=0.8, created_by=request.user
        ).count()
        low_confidence = ValidationResult.objects.filter(confidence_score__lt=0.6, created_by=request.user).count()

        # Status distribution
        validated = ValidationResult.objects.filter(validation_status='validated', created_by=request.user).count()
        needs_review = ValidationResult.objects.filter(validation_status='needs_review', created_by=request.user).count()
        pending = ValidationResult.objects.filter(validation_status='pending', created_by=request.user).count()
        rejected = ValidationResult.objects.filter(validation_status='rejected', created_by=request.user).count()

        # Source reliability stats
        source_usage = {}
        for source in ['google', 'arcgis', 'hdx', 'nominatim']:
            source_usage[source] = ValidationResult.objects.filter(
                recommended_source=source,
                created_by=request.user
            ).count()

        return JsonResponse({
            'total_locations': total_locations,
            'total_validations': total_validations,
            'confidence_distribution': {
                'high': high_confidence,
                'medium': medium_confidence,
                'low': low_confidence
            },
            'status_distribution': {
                'validated': validated,
                'needs_review': needs_review,
                'pending': pending,
                'rejected': rejected
            },
            'source_usage': source_usage,
            'completion_rate': (validated / total_locations * 100) if total_locations > 0 else 0
        })

    except Exception as e:
        logger.error(f"Error getting validation statistics: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'Failed to get statistics: {str(e)}'
        }, status=500)

@login_required
def validated_locations_map(request):
    """Show map of all validated locations with proper data structure."""

    validated_locations = Location.objects.filter(
        latitude__isnull=False,
        longitude__isnull=False
    ).order_by('name')


    # Prepare location data for map display
    locations_data = []
    for location in validated_locations:
        locations_data.append({
            'id': location.id,
            'name': location.name,
            'lat': float(location.latitude),
            'lng': float(location.longitude),
            'status': 'validated'
        })

    context = {
        'locations_data': json.dumps(locations_data),
        'mapbox_token': getattr(settings, 'MAPBOX_ACCESS_TOKEN', ''),
        'total_locations': len(locations_data)
    }

    return render(request, 'geolocation/validated_locations_map.html', context)

# MODERN CELERY-BASED BATCH PROCESSING VIEWS
@login_required
@csrf_exempt
def start_batch_geocoding(request):
    """
    Modern view-based batch geocoding using Celery.
    Replaces the need for geocode_locations.py management command.
    """
    if request.method == 'POST':
        try:
            data = json.loads(request.body)


            location_ids = data.get('location_ids')  # Specific locations or None for all
            force_reprocess = data.get('force_reprocess', False)
            batch_size = data.get('batch_size', 50)

            # Start Celery task
            task = batch_geocode_locations.delay(
                location_ids=location_ids,
                force_reprocess=force_reprocess,
                batch_size=batch_size
            )

            return JsonResponse({
                'success': True,
                'task_id': task.id,
                'message': 'Batch geocoding started',
                'monitor_url': f'/geolocation/batch-progress/{task.id}/'
            })

        except Exception as e:
            logger.error(f"Failed to start batch geocoding: {e}")
            return JsonResponse({
                'success': False,
                'error': str(e)
            }, status=500)

    return JsonResponse({'error': 'POST required'}, status=405)
@login_required
@csrf_exempt
def start_batch_validation(request):
    """
    Modern view-based batch validation using Celery.
    Replaces the validation logic from process_locations.py management command.
    """
    if request.method == 'POST':
        try:
            data = json.loads(request.body)


            result_ids = data.get('result_ids')  # Specific results or None for all unvalidated
            batch_size = data.get('batch_size', 50)

            # Start Celery task
            task = batch_validate_locations.delay(
                geocoding_result_ids=result_ids,
                batch_size=batch_size
            )

            return JsonResponse({
                'success': True,
                'task_id': task.id,
                'message': 'Batch validation started',
                'monitor_url': f'/geolocation/batch-progress/{task.id}/'
            })

        except Exception as e:
            logger.error(f"Failed to start batch validation: {e}")
            return JsonResponse({
                'success': False,
                'error': str(e)
            }, status=500)

    return JsonResponse({'error': 'POST required'}, status=405)
@login_required
def batch_progress(request, task_id):
    """
    Get real-time progress of batch processing tasks.
    Provides progress updates for both geocoding and validation.
    """
    try:

        progress_key = f"geocoding_progress_{task_id}"
        progress_data = cache.get(progress_key)

        if not progress_data:

            progress_key = f"validation_progress_{task_id}"
            progress_data = cache.get(progress_key)

        if not progress_data:

            from celery.result import AsyncResult
            task = AsyncResult(task_id)

            if task.state == 'PENDING':
                progress_data = {
                    'status': 'pending',
                    'progress': 0,
                    'message': 'Task is waiting to start...'
                }
            elif task.state == 'PROGRESS':
                progress_data = {
                    'status': 'processing',
                    **task.info
                }
            elif task.state == 'SUCCESS':
                progress_data = {
                    'status': 'completed',
                    'progress': 100,
                    'result': task.result
                }
            elif task.state == 'FAILURE':
                progress_data = {
                    'status': 'failed',
                    'error': str(task.info)
                }
            else:
                progress_data = {
                    'status': task.state.lower(),
                    'message': f'Task is {task.state.lower()}'
                }

        return JsonResponse(progress_data)

    except Exception as e:
        logger.error(f"Failed to get batch progress: {e}")
        return JsonResponse({
            'status': 'error',
            'error': str(e)
        }, status=500)
