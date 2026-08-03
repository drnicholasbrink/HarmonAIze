
import json
import logging
import requests
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.http import JsonResponse, Http404
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView
from django.conf import settings
from django.db import transaction
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
            'hdx': '#3b82f6',           # Blue
            'arcgis': '#8b5cf6',        # Purple
            'google': '#dc2626',        # Red
            'nominatim': '#f59e0b',     # Orange
            'admin_boundary': '#10b981' # Emerald/Green
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

        # Admin boundary source (check if field exists for backwards compatibility)
        if hasattr(result, 'admin_boundary_success') and result.admin_boundary_success:
            if result.admin_boundary_lat and result.admin_boundary_lng:
                admin_coord = {
                    'source': 'Admin Boundary',
                    'source_key': 'admin_boundary',
                    'lat': result.admin_boundary_lat,
                    'lng': result.admin_boundary_lng,
                    'color': source_colours['admin_boundary'],
                    'is_boundary': True,
                    'location_type': getattr(result, 'location_type', 'unknown')
                }
                # Include boundary reference for polygon rendering
                if hasattr(result, 'admin_boundary_match') and result.admin_boundary_match:
                    admin_coord['boundary_reference'] = result.admin_boundary_match
                    admin_coord['admin_level'] = getattr(result, 'admin_level', '')
                coordinates.append(admin_coord)

        if coordinates:

            status = validation.validation_status if validation else 'pending'


            metadata = validation.validation_metadata if validation else {}
            individual_scores = metadata.get('individual_scores', {})
            reverse_geocoding = metadata.get('reverse_geocoding_results', {})
            llm_enhanced = metadata.get('llm_enhanced', False)
            llm_conflict_resolution = metadata.get('llm_conflict_resolution')
            llm_sanity_check = metadata.get('llm_sanity_check')
            llm_explanation = metadata.get('llm_explanation')
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
                    'nominatim': 'OpenStreetMap',
                    'admin_boundary': 'Admin Boundary'
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
        except (ValueError, TypeError) as e:
            # Could not determine navigation info (invalid location ID or empty queryset); ignore silently
            logger.warning(f"Could not determine navigation info for location ID '{current_location_id}': {e}")

    return navigation

def get_validation_stats(user=None):
    """Calculate validation statistics for dashboard display."""
    # Core Location statistics
    if user:
        total_locations = Location.objects.filter(created_by=user).count()
        locations_with_coords = Location.objects.filter(
            created_by=user,
            latitude__isnull=False,
            longitude__isnull=False
        ).count()
    else:
        total_locations = Location.objects.count()
        locations_with_coords = Location.objects.filter(
            latitude__isnull=False,
            longitude__isnull=False
        ).count()

    # Count locations that have geocoding results but need validation
    pending_validation = 0

    if user:
        for location in Location.objects.filter(created_by=user, latitude__isnull=True, longitude__isnull=True):
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
        for location in Location.objects.filter(created_by=user, latitude__isnull=True, longitude__isnull=True):
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
            locations = Location.objects.filter(created_by=request.user).order_by('name')

            for location in locations:
                # Determine current status with automatic validation updates
                if location.latitude is not None and location.longitude is not None:
                    status = 'validated'
                    status_display = 'Validated & Complete'
                    status_colour = 'green'
                    confidence = 100
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
                            if hasattr(geocoding_result, 'admin_boundary_success') and geocoding_result.admin_boundary_success:
                                sources.append('Boundary')


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
                    if hasattr(result, 'admin_boundary_success') and result.admin_boundary_success:
                        sources.append('Boundary')

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
                if hasattr(result, 'admin_boundary_success') and result.admin_boundary_success:
                    sources.append('Boundary')

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
                return handle_use_source(validation, data, request.user)
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
        except Http404:
            return JsonResponse({
                'success': False,
                'error': 'Not found: this validation or geocoding result does not exist or does not belong to you.'
            }, status=404)
        except Exception as e:
            logger.error(f"Validation API Error: {str(e)}")
            return JsonResponse({
                'success': False,
                'error': f'An unexpected error occurred while processing your request: {str(e)}'
            }, status=500)

    return JsonResponse({'error': 'Only POST requests are allowed'}, status=405)


@login_required
def spatial_filter_api(request):
    """
    Cascading dropdown data for spatial filtering.

    GET ?action=countries
        → [{iso3, name, file}, ...]

    GET ?action=provinces&country=Zimbabwe
        → ['Bulawayo', 'Harare', ...]

    GET ?action=districts&country=Zimbabwe&province=Mashonaland+West
        → ['Chinhoyi', 'Hurungwe', ...]
    """
    from .admin_boundary_service import get_admin_boundary_geocoder

    action = request.GET.get('action', '')
    try:
        geocoder = get_admin_boundary_geocoder()

        if action == 'countries':
            raw = geocoder.list_available_countries()
            names = sorted(set(
                c['name'] for c in raw
                if c.get('name') and c['name'] != c.get('iso3') and c['name'] != 'AFRICA'
            ))
            return JsonResponse({'countries': names})

        elif action == 'provinces':
            country = request.GET.get('country', '').strip()
            if not country:
                return JsonResponse({'error': 'country parameter required'}, status=400)
            provinces = geocoder.list_provinces(country)
            return JsonResponse({'provinces': provinces})

        elif action == 'districts':
            country = request.GET.get('country', '').strip()
            province = request.GET.get('province', '').strip()
            if not country or not province:
                return JsonResponse({'error': 'country and province parameters required'}, status=400)
            districts = geocoder.list_districts(country, province)
            return JsonResponse({'districts': districts})

        return JsonResponse({'error': f'Unknown action: {action}'}, status=400)

    except Exception as e:
        logger.error(f'spatial_filter_api error: {e}')
        return JsonResponse({'error': str(e)}, status=500)


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
                spatial_filter = data.get('spatial_filter') or {}

                geocoding_service = GeocodingService()

                locations = Location.objects.filter(created_by=request.user, latitude__isnull=True, longitude__isnull=True)

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
                        result = geocoding_service.geocode_single_location(
                            location, force, user=request.user, spatial_filter=spatial_filter
                        )
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

                stats = get_validation_stats(user=request.user)

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

    # Handle GET requests for specific actions
    elif request.method == 'GET':
        action = request.GET.get('action')

        if action == 'get_location_polygons':
            # Return polygon geometries for locations that have admin boundary matches
            try:
                from .admin_boundary_service import get_admin_boundary_geocoder

                features = []
                geocoder = get_admin_boundary_geocoder()

                # Get all locations with coordinates
                locations = Location.objects.filter(
                    latitude__isnull=False,
                    longitude__isnull=False
                )
                logger.info(f"get_location_polygons: Found {locations.count()} locations with coordinates")

                # Get geocoding results with admin boundary matches
                geocoding_results = GeocodingResult.objects.filter(
                    location__in=locations,
                    admin_boundary_success=True
                ).select_related('location')
                logger.info(f"get_location_polygons: Found {geocoding_results.count()} results with admin_boundary_success=True")

                # Build color map for locations
                color_palette = [
                    '#e74c3c', '#3498db', '#2ecc71', '#f39c12', '#9b59b6',
                    '#1abc9c', '#e67e22', '#34495e', '#e91e63', '#ff5722'
                ]

                for i, result in enumerate(geocoding_results):
                    try:
                        if not hasattr(result, 'admin_boundary_match') or not result.admin_boundary_match:
                            logger.debug(f"Skipping {result.location_name}: no admin_boundary_match")
                            continue

                        boundary_ref = result.admin_boundary_match
                        if not boundary_ref:
                            logger.debug(f"Skipping {result.location_name}: boundary_ref is empty")
                            continue

                        logger.info(f"Processing {result.location_name}: layer_type={boundary_ref.get('layer_type')}, feature_index={boundary_ref.get('feature_index')}")

                        # Get the polygon geometry
                        geometry_data = geocoder.get_feature_geometry(
                            boundary_ref.get('layer_type'),
                            boundary_ref.get('feature_index'),
                            source_file=boundary_ref.get('source_file')
                        )

                        if geometry_data:
                            logger.info(f"Got geometry for {result.location_name}: type={geometry_data.get('type')}")
                            features.append({
                                'type': 'Feature',
                                'geometry': geometry_data,
                                'properties': {
                                    'id': result.location.id if result.location else result.id,
                                    'name': result.location_name,
                                    'color': color_palette[i % len(color_palette)],
                                    'admin_level': getattr(result, 'admin_level', ''),
                                    'location_type': getattr(result, 'location_type', 'admin_boundary')
                                }
                            })
                        else:
                            logger.warning(f"No geometry returned for {result.location_name}")
                    except Exception as e:
                        logger.warning(f"Could not get polygon for {result.location_name}: {e}")
                        continue

                logger.info(f"get_location_polygons: Returning {len(features)} features")
                return JsonResponse({
                    'type': 'FeatureCollection',
                    'features': features
                })

            except Exception as e:
                logger.error(f"Error getting location polygons: {e}")
                return JsonResponse({
                    'error': f'Could not load location polygons: {str(e)}'
                }, status=500)

        return JsonResponse({'error': 'Unknown GET action'}, status=400)

    return JsonResponse({'error': 'Only GET and POST requests are allowed'}, status=405)
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
                            elif best_source == 'admin_boundary' and hasattr(result, 'admin_boundary_success') and result.admin_boundary_success:
                                final_lat, final_lng = result.admin_boundary_lat, result.admin_boundary_lng
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

                            result.compute_source_comparison_metrics(final_lat, final_lng, best_source)

                            # Add to ValidatedDataset (POI arsenal)
                            ValidatedDataset.objects.update_or_create(
                                location_name=result.location_name,
                                created_by=validation.created_by,
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
            elif best_source == 'admin_boundary' and hasattr(result, 'admin_boundary_success') and result.admin_boundary_success:
                final_lat, final_lng = result.admin_boundary_lat, result.admin_boundary_lng
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

            result.selected_source = best_source
            result.save(update_fields=['selected_source'])
            result.compute_source_comparison_metrics(final_lat, final_lng, best_source)

            ValidatedDataset.objects.update_or_create(
                location_name=result.location_name,
                created_by=validation.created_by,
                defaults={
                    'final_lat': final_lat,
                    'final_long': final_lng,
                    'country': '',  # Add country if available
                    'source': f'two_component_{best_source}',
                    'validated_at': timezone.now()
                }
            )


            try:
                location = Location.objects.get(name__iexact=result.location_name, created_by=validation.created_by)
                location.latitude = final_lat
                location.longitude = final_lng
                location.save()
            except Location.DoesNotExist:

                locations = Location.objects.filter(name__icontains=result.location_name, created_by=validation.created_by)
                if locations.exists():
                    location = locations.first()
                    location.latitude = final_lat
                    location.longitude = final_lng
                    location.save()
            except Location.MultipleObjectsReturned:
                location = Location.objects.filter(name__iexact=result.location_name, created_by=validation.created_by).first()
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

def handle_use_source(validation, data, user):
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
            elif source == 'admin_boundary' and hasattr(result, 'admin_boundary_success') and result.admin_boundary_success:
                final_lat, final_lng = result.admin_boundary_lat, result.admin_boundary_lng
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

            result.selected_source = source
            result.save(update_fields=['selected_source'])
            result.compute_source_comparison_metrics(final_lat, final_lng, source)

            ValidatedDataset.objects.update_or_create(
                location_name=result.location_name,
                defaults={
                    'final_lat': final_lat,
                    'final_long': final_lng,
                    'country': '',
                    'source': source,
                    'validated_at': timezone.now(),
                    'created_by': user
                }
            )


            try:
                location = Location.objects.get(name__iexact=result.location_name, created_by=validation.created_by)
                location.latitude = final_lat
                location.longitude = final_lng
                location.save()
            except Location.DoesNotExist:

                locations = Location.objects.filter(name__icontains=result.location_name, created_by=validation.created_by)
                if locations.exists():
                    location = locations.first()
                    location.latitude = final_lat
                    location.longitude = final_lng
                    location.save()
            except Location.MultipleObjectsReturned:
                location = Location.objects.filter(name__iexact=result.location_name, created_by=validation.created_by).first()
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

            result.selected_source = 'manual'
            result.save(update_fields=['selected_source'])
            result.compute_source_comparison_metrics(lat, lng, 'manual')

            #  Add to ValidatedDataset (POI arsenal)
            ValidatedDataset.objects.update_or_create(
                location_name=result.location_name,
                created_by=validation.created_by,
                defaults={
                    'final_lat': lat,
                    'final_long': lng,
                    'country': '',
                    'source': 'manual_entry',
                    'validated_at': timezone.now()
                }
            )


            try:
                location = Location.objects.get(name__iexact=result.location_name, created_by=validation.created_by)
                location.latitude = lat
                location.longitude = lng
                location.save()
            except Location.DoesNotExist:

                locations = Location.objects.filter(name__icontains=result.location_name, created_by=validation.created_by)
                if locations.exists():
                    location = locations.first()
                    location.latitude = lat
                    location.longitude = lng
                    location.save()
            except Location.MultipleObjectsReturned:
                location = Location.objects.filter(name__iexact=result.location_name, created_by=validation.created_by).first()
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
    from .models import LocationCSVUpload

    validated_locations = Location.objects.filter(
        created_by=request.user,
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

    # Get source file information from CSV uploads
    # Query ingested uploads to get source filenames
    source_files = []
    csv_uploads = LocationCSVUpload.objects.filter(
        processing_status='ingested',
        uploaded_by=request.user
    ).order_by('-uploaded_at')

    for upload in csv_uploads:
        # Extract filename without extension for cleaner prefix
        filename = upload.original_filename
        if '.' in filename:
            filename_prefix = filename.rsplit('.', 1)[0]
        else:
            filename_prefix = filename
        source_files.append({
            'id': upload.id,
            'filename': upload.original_filename,
            'prefix': filename_prefix,
            'uploaded_at': upload.uploaded_at.isoformat() if upload.uploaded_at else '',
            'locations_created': upload.locations_created or 0
        })

    # Create a combined prefix from all source files (for default export name)
    if source_files:
        # Use the most recent file's prefix, or combine if multiple
        if len(source_files) == 1:
            default_prefix = source_files[0]['prefix']
        else:
            # Combine first parts of filenames (limit to 3)
            prefixes = [sf['prefix'][:20] for sf in source_files[:3]]
            default_prefix = '_'.join(prefixes)
    else:
        default_prefix = 'validated_locations'

    context = {
        'locations_data': json.dumps(locations_data),
        'mapbox_token': getattr(settings, 'MAPBOX_ACCESS_TOKEN', ''),
        'total_locations': len(locations_data),
        'source_files': source_files,
        'source_files_json': json.dumps(source_files),
        'default_prefix': default_prefix,
    }

    return render(request, 'geolocation/validated_locations_map.html', context)


@login_required
def download_validated_locations_csv(request):
    """
    Download validated locations as CSV file.

    Query parameters:
        prefix: Filename prefix (default: 'validated_locations')
        geometry_export: 'centroid' (default) or 'polygon' - for admin boundaries,
                        includes WKT geometry column when 'polygon' is specified
    """
    import csv
    import re
    from django.http import HttpResponse
    from datetime import datetime

    # Get all validated locations (Location model has: name, lat, lng, created_at, updated_at)
    validated_locations = Location.objects.filter(
        created_by=request.user,
        latitude__isnull=False,
        longitude__isnull=False
    ).order_by('name')

    # Get source file prefix from query parameter
    prefix = request.GET.get('prefix', 'validated_locations')
    # Sanitize prefix for filename safety
    prefix = re.sub(r'[^\w\-_]', '_', prefix)[:50]

    # Get geometry export option (centroid or polygon)
    geometry_export = request.GET.get('geometry_export', 'centroid')
    include_polygon = geometry_export == 'polygon'

    # Create the HttpResponse object with CSV header
    response = HttpResponse(content_type='text/csv')
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    response['Content-Disposition'] = f'attachment; filename="{prefix}_{timestamp}.csv"'

    # Create CSV writer
    writer = csv.writer(response)

    # Write header row
    header = [
        'Location ID',
        'Location Name',
        'Latitude',
        'Longitude',
        'Location Type',
        'Admin Level',
        'Created Date',
        'Updated Date'
    ]
    if include_polygon:
        header.append('WKT_Geometry')
    writer.writerow(header)

    # Preload geocoding results for admin boundary info
    location_geocoding = {}
    geocoder = None
    if include_polygon:
        try:
            from .admin_boundary_service import get_admin_boundary_geocoder
            geocoder = get_admin_boundary_geocoder()

            geocoding_results = GeocodingResult.objects.filter(
                location__in=validated_locations,
                admin_boundary_success=True
            ).select_related('location')

            for result in geocoding_results:
                location_geocoding[result.location_id] = result
        except Exception as e:
            # Admin boundary fields may not exist yet (migration not applied)
            logger.warning(f"Could not load admin boundary data for export: {e}")

    # Write data rows
    for location in validated_locations:
        geocoding_result = location_geocoding.get(location.id)
        location_type = 'point'
        admin_level = ''
        wkt_geometry = ''

        if geocoding_result:
            location_type = getattr(geocoding_result, 'location_type', 'point') or 'point'
            admin_level = getattr(geocoding_result, 'admin_level', '') or ''

            # Get polygon geometry if requested
            if include_polygon and geocoder and hasattr(geocoding_result, 'admin_boundary_match') and geocoding_result.admin_boundary_match:
                boundary_ref = geocoding_result.admin_boundary_match
                try:
                    geometry = geocoder.get_feature_geometry(
                        boundary_ref.get('layer_type'),
                        boundary_ref.get('feature_index'),
                        source_file=boundary_ref.get('source_file')  # Pass source file for large file streaming
                    )
                    if geometry:
                        wkt_geometry = _geometry_to_wkt(geometry)
                except Exception as e:
                    logger.warning(f"Could not get polygon geometry: {e}")
                    wkt_geometry = ''

        row = [
            location.id,
            location.name,
            location.latitude,
            location.longitude,
            location_type,
            admin_level,
            location.created_at.strftime('%Y-%m-%d %H:%M:%S') if location.created_at else 'N/A',
            location.updated_at.strftime('%Y-%m-%d %H:%M:%S') if location.updated_at else 'N/A'
        ]
        if include_polygon:
            row.append(wkt_geometry)
        writer.writerow(row)

    return response


def _geometry_to_wkt(geometry):
    """Convert GeoJSON geometry to WKT format."""
    geom_type = geometry.get('type', '').upper()
    coords = geometry.get('coordinates', [])

    if not coords:
        return ''

    try:
        if geom_type == 'POINT':
            return f"POINT ({coords[0]} {coords[1]})"
        elif geom_type == 'POLYGON':
            rings = []
            for ring in coords:
                points = ', '.join([f"{c[0]} {c[1]}" for c in ring])
                rings.append(f"({points})")
            return f"POLYGON ({', '.join(rings)})"
        elif geom_type == 'MULTIPOLYGON':
            polygons = []
            for polygon in coords:
                rings = []
                for ring in polygon:
                    points = ', '.join([f"{c[0]} {c[1]}" for c in ring])
                    rings.append(f"({points})")
                polygons.append(f"({', '.join(rings)})")
            return f"MULTIPOLYGON ({', '.join(polygons)})"
        else:
            return ''
    except Exception:
        return ''


@login_required
def download_validated_locations_shapefile(request):
    """
    Download validated locations as a shapefile (ZIP archive).

    Query parameters:
        prefix: Filename prefix (default: 'validated_locations')
        geometry_export: 'centroid' (default) or 'polygon' - for admin boundaries,
                        exports full polygon geometry when 'polygon' is specified

    Returns a ZIP file containing:
    - .shp (geometry)
    - .shx (index)
    - .dbf (attributes)
    - .prj (projection - WGS84)
    - .cpg (character encoding)
    """
    import geopandas as gpd
    from shapely.geometry import Point, shape
    import tempfile
    import zipfile
    import os
    import re
    from io import BytesIO
    from django.http import HttpResponse, JsonResponse
    from datetime import datetime

    # Get all validated locations (same query as CSV export)
    validated_locations = Location.objects.filter(
        created_by=request.user,
        latitude__isnull=False,
        longitude__isnull=False
    ).order_by('name')

    # Check if there are locations to export
    if not validated_locations.exists():
        return JsonResponse({
            'error': 'No validated locations found to export.'
        }, status=404)

    # Get source file prefix from query parameter
    prefix = request.GET.get('prefix', 'validated_locations')
    # Sanitize prefix for filename safety
    prefix = re.sub(r'[^\w\-_]', '_', prefix)[:50]

    # Get geometry export option (centroid or polygon)
    geometry_export = request.GET.get('geometry_export', 'centroid')
    include_polygon = geometry_export == 'polygon'

    # Create timestamp for filename
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    shapefile_basename = f'{prefix}_{timestamp}'

    # Preload geocoding results for admin boundary info
    location_geocoding = {}
    geocoder = None
    if include_polygon:
        try:
            from .admin_boundary_service import get_admin_boundary_geocoder
            geocoder = get_admin_boundary_geocoder()

            geocoding_results = GeocodingResult.objects.filter(
                location__in=validated_locations,
                admin_boundary_success=True
            ).select_related('location')

            for result in geocoding_results:
                location_geocoding[result.location_id] = result
        except Exception as e:
            # Admin boundary fields may not exist yet (migration not applied)
            logger.warning(f"Could not load admin boundary data for shapefile export: {e}")

    # Prepare data for GeoDataFrame
    data = []
    geometries = []

    for location in validated_locations:
        geocoding_result = location_geocoding.get(location.id)
        location_type = 'point'
        admin_level = ''
        geom = Point(location.longitude, location.latitude)

        if geocoding_result:
            location_type = getattr(geocoding_result, 'location_type', 'point') or 'point'
            admin_level = getattr(geocoding_result, 'admin_level', '') or ''

            # Get polygon geometry if requested and available
            if include_polygon and geocoder and hasattr(geocoding_result, 'admin_boundary_match') and geocoding_result.admin_boundary_match:
                boundary_ref = geocoding_result.admin_boundary_match
                try:
                    geometry_data = geocoder.get_feature_geometry(
                        boundary_ref.get('layer_type'),
                        boundary_ref.get('feature_index'),
                        source_file=boundary_ref.get('source_file')  # Pass source file for large file streaming
                    )
                    if geometry_data:
                        geom = shape(geometry_data)
                except Exception as e:
                    # Fall back to point geometry
                    logger.warning(f"Could not get polygon geometry for shapefile: {e}")

        geometries.append(geom)

        # Prepare attributes
        data.append({
            'loc_id': location.id,
            'name': location.name[:254] if location.name else '',  # Shapefile limit
            'latitude': location.latitude,
            'longitude': location.longitude,
            'loc_type': location_type[:50],
            'adm_level': admin_level[:10],
            'created_at': location.created_at.date() if location.created_at else None,
            'updated_at': location.updated_at.date() if location.updated_at else None
        })

    # Create GeoDataFrame
    gdf = gpd.GeoDataFrame(data, geometry=geometries, crs='EPSG:4326')

    # Use temporary directory for shapefile creation
    with tempfile.TemporaryDirectory() as temp_dir:
        shapefile_path = os.path.join(temp_dir, shapefile_basename + '.shp')

        # Write shapefile (geopandas automatically creates .shp, .shx, .dbf, .prj, .cpg)
        gdf.to_file(shapefile_path, driver='ESRI Shapefile', encoding='utf-8')

        # Create ZIP file in memory
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # Add all shapefile components to ZIP
            for extension in ['.shp', '.shx', '.dbf', '.prj', '.cpg']:
                file_path = os.path.join(temp_dir, shapefile_basename + extension)
                if os.path.exists(file_path):
                    arcname = shapefile_basename + extension
                    zip_file.write(file_path, arcname=arcname)

        # Prepare response
        zip_buffer.seek(0)
        response = HttpResponse(zip_buffer.getvalue(), content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="{shapefile_basename}.zip"'

        return response


def download_validated_locations_geojson(request):
    """
    Download validated locations as a GeoJSON file.

    Query parameters:
        prefix: Filename prefix (default: 'validated_locations')
        geometry_export: 'centroid' (default) or 'polygon' - for admin boundaries,
                        exports full polygon geometry when 'polygon' is specified

    Returns a GeoJSON file with validated locations.
    """
    from shapely.geometry import Point, shape, mapping
    import re
    from django.http import HttpResponse, JsonResponse
    from datetime import datetime

    # Get all validated locations (same query as CSV export)
    validated_locations = Location.objects.filter(
        created_by=request.user,
        latitude__isnull=False,
        longitude__isnull=False
    ).order_by('name')

    # Check if there are locations to export
    if not validated_locations.exists():
        return JsonResponse({
            'error': 'No validated locations found to export.'
        }, status=404)

    # Get source file prefix from query parameter
    prefix = request.GET.get('prefix', 'validated_locations')
    # Sanitize prefix for filename safety
    prefix = re.sub(r'[^\w\-_]', '_', prefix)[:50]

    # Get geometry export option (centroid or polygon)
    geometry_export = request.GET.get('geometry_export', 'centroid')
    include_polygon = geometry_export == 'polygon'

    # Create timestamp for filename
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    geojson_filename = f'{prefix}_{timestamp}.geojson'

    # Preload geocoding results for admin boundary info
    location_geocoding = {}
    geocoder = None
    if include_polygon:
        try:
            from .admin_boundary_service import get_admin_boundary_geocoder
            geocoder = get_admin_boundary_geocoder()

            geocoding_results = GeocodingResult.objects.filter(
                location__in=validated_locations,
                admin_boundary_success=True
            ).select_related('location')

            for result in geocoding_results:
                location_geocoding[result.location_id] = result
        except Exception as e:
            # Admin boundary fields may not exist yet (migration not applied)
            logger.warning(f"Could not load admin boundary data for GeoJSON export: {e}")

    # Build GeoJSON FeatureCollection
    features = []

    for location in validated_locations:
        geocoding_result = location_geocoding.get(location.id)
        location_type = 'point'
        admin_level = ''
        geom = Point(location.longitude, location.latitude)

        if geocoding_result:
            location_type = getattr(geocoding_result, 'location_type', 'point') or 'point'
            admin_level = getattr(geocoding_result, 'admin_level', '') or ''

            # Get polygon geometry if requested and available
            if include_polygon and geocoder and hasattr(geocoding_result, 'admin_boundary_match') and geocoding_result.admin_boundary_match:
                boundary_ref = geocoding_result.admin_boundary_match
                try:
                    geometry_data = geocoder.get_feature_geometry(
                        boundary_ref.get('layer_type'),
                        boundary_ref.get('feature_index'),
                        source_file=boundary_ref.get('source_file')
                    )
                    if geometry_data:
                        geom = shape(geometry_data)
                except Exception as e:
                    # Fall back to point geometry
                    logger.warning(f"Could not get polygon geometry for GeoJSON: {e}")

        # Create feature
        feature = {
            'type': 'Feature',
            'geometry': mapping(geom),
            'properties': {
                'id': location.id,
                'name': location.name,
                'latitude': location.latitude,
                'longitude': location.longitude,
                'location_type': location_type,
                'admin_level': admin_level,
                'created_at': location.created_at.isoformat() if location.created_at else None,
                'updated_at': location.updated_at.isoformat() if location.updated_at else None
            }
        }
        features.append(feature)

    # Create GeoJSON structure
    geojson = {
        'type': 'FeatureCollection',
        'features': features,
        'metadata': {
            'generated_at': datetime.now().isoformat(),
            'total_features': len(features),
            'geometry_type': geometry_export,
            'source': 'HarmonAIze Geolocation Platform'
        }
    }

    # Return as downloadable JSON file
    response = HttpResponse(
        json.dumps(geojson, indent=2),
        content_type='application/geo+json'
    )
    response['Content-Disposition'] = f'attachment; filename="{geojson_filename}"'

    return response


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

            # Start Celery task (pass user_id for GeocodingResult creation)
            task = batch_geocode_locations.delay(
                location_ids=location_ids,
                force_reprocess=force_reprocess,
                batch_size=batch_size,
                user_id=request.user.id
            )

            return JsonResponse({
                'success': True,
                'task_id': task.id,
                'message': 'Batch geocoding started',
                'monitor_url': reverse('geolocation:batch_progress', kwargs={'task_id': task.id})
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
                'monitor_url': reverse('geolocation:batch_progress', kwargs={'task_id': task.id})
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


@login_required
@csrf_exempt
def manual_coordinate_update(request):
    """
    Manually set coordinates for a location.
    Allows users to click on map or enter coordinates directly.
    """
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            location_id = data.get('location_id')
            latitude = data.get('latitude')
            longitude = data.get('longitude')

            # Validate inputs
            if not location_id:
                return JsonResponse({'success': False, 'error': 'Location ID is required'}, status=400)

            if latitude is None or longitude is None:
                return JsonResponse({'success': False, 'error': 'Latitude and longitude are required'}, status=400)

            # Validate coordinate ranges
            try:
                lat = float(latitude)
                lon = float(longitude)

                if not (-90 <= lat <= 90):
                    return JsonResponse({'success': False, 'error': 'Latitude must be between -90 and 90'}, status=400)

                if not (-180 <= lon <= 180):
                    return JsonResponse({'success': False, 'error': 'Longitude must be between -180 and 180'}, status=400)

            except (ValueError, TypeError):
                return JsonResponse({'success': False, 'error': 'Invalid coordinate format'}, status=400)

            # Get location
            location = Location.objects.filter(id=location_id).first()
            if not location:
                return JsonResponse({'success': False, 'error': 'Location not found'}, status=404)

            # Update coordinates
            location.latitude = lat
            location.longitude = lon
            location.save()

            logger.info(f"Manually set coordinates for '{location.name}' to ({lat}, {lon}) by user {request.user.username}")

            return JsonResponse({
                'success': True,
                'message': f'Coordinates updated for {location.name}',
                'location': {
                    'id': location.id,
                    'name': location.name,
                    'latitude': location.latitude,
                    'longitude': location.longitude
                }
            })

        except Exception as e:
            logger.error(f"Failed to update coordinates: {e}", exc_info=True)
            return JsonResponse({
                'success': False,
                'error': str(e)
            }, status=500)

    return JsonResponse({'error': 'POST required'}, status=405)


# ============================================================================
# LOCATION CSV UPLOAD VIEWS
# ============================================================================

@login_required
def upload_location_csv(request):
    """
    Upload and validate location CSV files.
    Step 1 of the location CSV import workflow.
    """
    from .forms import LocationCSVUploadForm
    from .models import LocationCSVUpload, LocationCSVColumn
    from .utils import analyze_location_csv_columns
    from django.contrib import messages
    import hashlib

    if request.method == 'POST':
        form = LocationCSVUploadForm(request.POST, request.FILES)

        if form.is_valid():
            upload = form.save(commit=False)
            upload.uploaded_by = request.user
            upload.original_filename = request.FILES['file'].name
            upload.file_size = request.FILES['file'].size

            # Detect file format
            file_ext = upload.original_filename.split('.')[-1].lower()
            upload.file_format = file_ext

            # Calculate checksum for duplicate detection
            file_obj = request.FILES['file']
            file_obj.seek(0)
            checksum = hashlib.sha256(file_obj.read()).hexdigest()
            upload.checksum = checksum
            file_obj.seek(0)

            # Check for duplicate uploads (but ignore failed ones)
            duplicate = LocationCSVUpload.objects.filter(
                uploaded_by=request.user,
                checksum=checksum
            ).exclude(processing_status='error').first()

            if duplicate:
                messages.warning(
                    request,
                    f"Identical file already uploaded on {duplicate.uploaded_at.strftime('%Y-%m-%d %H:%M')}. "
                    f"Status: {duplicate.get_processing_status_display()}"
                )
                return redirect('geolocation:upload_location_csv')

            upload.save()

            # Analyze columns
            try:
                analysis = analyze_location_csv_columns(upload.file.path)

                if not analysis.get('success'):
                    upload.processing_status = 'error'
                    upload.processing_message = analysis.get('error', 'Unknown error during analysis')
                    upload.save()
                    messages.error(request, f"Could not analyze file: {upload.processing_message}")
                    return redirect('geolocation:upload_location_csv')

                # Store detected columns and row count
                upload.detected_columns = analysis['columns']
                upload.total_rows = analysis['total_rows']
                upload.processing_status = 'validated'
                upload.processing_message = f"Detected {len(analysis['columns'])} columns, {analysis['total_rows']} rows"
                upload.save()

                # Create LocationCSVColumn records
                for idx, col_name in enumerate(analysis['columns']):
                    col_analysis = analysis['column_analysis'][col_name]

                    LocationCSVColumn.objects.create(
                        upload=upload,
                        column_name=col_name,
                        column_index=idx,
                        inferred_type=col_analysis['inferred_type'],
                        sample_values=col_analysis['sample_values'],
                        non_null_count=col_analysis['non_null_count'],
                        unique_count=col_analysis['unique_count'],
                        is_potential_location_name=col_analysis['is_potential_location_name'],
                        is_potential_latitude=col_analysis['is_potential_latitude'],
                        is_potential_longitude=col_analysis['is_potential_longitude'],
                    )

                messages.success(
                    request,
                    f"File analyzed successfully! Found {len(analysis['columns'])} columns in {analysis['total_rows']} rows. "
                    "Please confirm column mappings."
                )
                return redirect('geolocation:map_location_columns', upload_id=upload.id)

            except Exception as e:
                upload.processing_status = 'error'
                upload.processing_message = str(e)
                upload.save()
                logger.error(f"Failed to analyze location CSV: {e}", exc_info=True)
                messages.error(request, f"Could not analyze file: {e}")
                return redirect('geolocation:upload_location_csv')

    else:
        form = LocationCSVUploadForm()

    # Get recent uploads for display
    recent_uploads = LocationCSVUpload.objects.filter(
        uploaded_by=request.user
    ).order_by('-uploaded_at')[:5]

    return render(request, 'geolocation/upload_location_csv.html', {
        'form': form,
        'recent_uploads': recent_uploads,
    })


@login_required
def map_location_columns(request, upload_id):
    """
    Map CSV columns to location fields with preview.
    Step 2 of the location CSV import workflow.
    """
    from .models import LocationCSVUpload
    from .utils import suggest_location_column_mappings
    from django.contrib import messages

    upload = get_object_or_404(
        LocationCSVUpload,
        id=upload_id,
        uploaded_by=request.user
    )

    columns = upload.columns.all().order_by('column_index')

    if request.method == 'POST':
        # Save column mappings
        location_name_col = request.POST.get('location_name_column')
        latitude_col = request.POST.get('latitude_column')
        longitude_col = request.POST.get('longitude_column')

        # Validation
        if not location_name_col or location_name_col == '':
            messages.error(request, "Location name column is required. Please select one.")
            return redirect('geolocation:map_location_columns', upload_id=upload_id)

        # Save mappings
        upload.location_name_column = location_name_col
        upload.latitude_column = latitude_col if latitude_col and latitude_col != '' else ''
        upload.longitude_column = longitude_col if longitude_col and longitude_col != '' else ''
        upload.processing_status = 'processed'
        upload.save()

        messages.success(request, "Column mappings saved! Ready to import locations.")
        return redirect('geolocation:ingest_location_csv', upload_id=upload_id)

    # Generate suggestions for pre-filling
    column_metadata = []
    for col in columns:
        column_metadata.append({
            'column_name': col.column_name,
            'is_potential_location_name': col.is_potential_location_name,
            'is_potential_latitude': col.is_potential_latitude,
            'is_potential_longitude': col.is_potential_longitude,
        })

    suggestions = suggest_location_column_mappings(column_metadata)

    return render(request, 'geolocation/map_location_columns.html', {
        'upload': upload,
        'columns': columns,
        'suggestions': suggestions,
    })


@login_required
def ingest_location_csv(request, upload_id):
    """
    Import locations from CSV into Location model.
    Step 3 of the location CSV import workflow.
    """
    from .models import LocationCSVUpload
    from core.models import Location
    from django.contrib import messages
    from django.utils import timezone
    import pandas as pd

    upload = get_object_or_404(
        LocationCSVUpload,
        id=upload_id,
        uploaded_by=request.user
    )

    # Verify upload is ready
    if upload.processing_status != 'processed':
        messages.error(request, "Upload is not ready for ingestion. Please map columns first.")
        return redirect('geolocation:map_location_columns', upload_id=upload_id)

    if not upload.location_name_column:
        messages.error(request, "Location name column not specified. Please map columns.")
        return redirect('geolocation:map_location_columns', upload_id=upload_id)

    if request.method == 'POST':
        try:
            replace_existing = request.POST.get('replace_existing', 'true') == 'true'

            if replace_existing:
                # Delete all existing locations for this user to start a fresh session.
                # Cascades automatically delete GeocodingResult → ValidationResult.
                deleted_count, _ = Location.objects.filter(created_by=request.user).delete()
                if deleted_count:
                    logger.info(f"Cleared {deleted_count} previous locations for user {request.user.username}")

            # Read the file
            file_ext = upload.file_format
            if file_ext == 'csv':
                df = pd.read_csv(upload.file.path)
            elif file_ext in ['xlsx', 'xls']:
                df = pd.read_excel(upload.file.path)
            elif file_ext == 'json':
                df = pd.read_json(upload.file.path, lines=True)
            else:
                raise ValueError(f"Unsupported file format: {file_ext}")

            # Verify columns exist
            if upload.location_name_column not in df.columns:
                raise ValueError(f"Column '{upload.location_name_column}' not found in file")

            has_coords = upload.has_coordinates
            if has_coords:
                if upload.latitude_column not in df.columns:
                    raise ValueError(f"Column '{upload.latitude_column}' not found in file")
                if upload.longitude_column not in df.columns:
                    raise ValueError(f"Column '{upload.longitude_column}' not found in file")

            # Identify context columns (district, country, etc.) to enhance location names
            context_keywords = ['district', 'region', 'province', 'city', 'country', 'state', 'county', 'area']
            context_columns = []
            for col in df.columns:
                col_lower = col.lower()
                if col != upload.location_name_column and any(keyword in col_lower for keyword in context_keywords):
                    context_columns.append(col)

            logger.info(f"Context columns to append to location names: {context_columns}")

            # Process rows
            created_count = 0
            skipped_count = 0

            for idx, row in df.iterrows():
                location_name = str(row[upload.location_name_column]).strip()

                # Skip empty names
                if not location_name or location_name == 'nan':
                    skipped_count += 1
                    continue

                # Enhance location name with district/country context
                context_parts = []
                for col in context_columns:
                    val = row[col]
                    if pd.notna(val) and str(val).strip() and str(val).strip().lower() != 'nan':
                        context_parts.append(str(val).strip())

                # Append context to location name for better geocoding
                if context_parts:
                    enhanced_name = f"{location_name}, {', '.join(context_parts)}"
                    logger.info(f"Enhanced location name: '{location_name}' → '{enhanced_name}'")
                    location_name = enhanced_name

                # Get coordinates if available
                latitude = None
                longitude = None

                if has_coords:
                    try:
                        lat_val = row[upload.latitude_column]
                        lon_val = row[upload.longitude_column]

                        # Convert to float and validate - BOTH must be present and valid
                        # Check for NaN, None, empty string, or whitespace
                        lat_valid = pd.notna(lat_val) and str(lat_val).strip() != ''
                        lon_valid = pd.notna(lon_val) and str(lon_val).strip() != ''

                        if lat_valid and lon_valid:
                            latitude = float(lat_val)
                            longitude = float(lon_val)

                            # Validate ranges
                            if not (-90 <= latitude <= 90):
                                logger.warning(f"Invalid latitude {latitude} for {location_name}, skipping coordinates")
                                latitude = None
                                longitude = None
                            elif not (-180 <= longitude <= 180):
                                logger.warning(f"Invalid longitude {longitude} for {location_name}, skipping coordinates")
                                latitude = None
                                longitude = None
                        else:
                            # If either coordinate is missing, skip both
                            if lat_valid and not lon_valid:
                                logger.warning(f"Missing longitude for {location_name}, skipping coordinates")
                            elif lon_valid and not lat_valid:
                                logger.warning(f"Missing latitude for {location_name}, skipping coordinates")
                            latitude = None
                            longitude = None
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Could not parse coordinates for {location_name}: {e}")
                        latitude = None
                        longitude = None

                # Check for duplicates (case-insensitive name match, scoped to current user)
                existing = Location.objects.filter(name__iexact=location_name, created_by=request.user).first()

                if existing:
                    # If CSV had coordinates, store them as user_provided source in GeocodingResult
                    # so the user can compare against OSM/ArcGIS/Google results before validating.
                    # Never overwrite Location.latitude/longitude directly — that is set only by validation.
                    if latitude is not None and longitude is not None:
                        GeocodingResult.objects.update_or_create(
                            location_name=existing.name,
                            created_by=request.user,
                            defaults={
                                'location': existing,
                                'user_provided_lat': latitude,
                                'user_provided_lng': longitude,
                                'user_provided_success': True,
                            }
                        )
                        logger.info(f"Stored user-provided coordinates as source for existing location: {location_name}")
                    skipped_count += 1
                else:
                    # Create new location WITHOUT final coordinates.
                    # CSV coordinates are one source among many (OSM, ArcGIS, Google, HDX).
                    # Location.latitude/longitude is only set after the user validates.
                    location_obj = Location.objects.create(
                        name=location_name,
                        created_by=request.user
                    )

                    # If CSV had coordinates, store them as user_provided geocoding source
                    if latitude is not None and longitude is not None:
                        GeocodingResult.objects.create(
                            location=location_obj,
                            created_by=request.user,
                            location_name=location_name,
                            user_provided_lat=latitude,
                            user_provided_lng=longitude,
                            user_provided_success=True,
                        )
                        logger.info(f"Stored user-provided coordinates as source for new location: {location_name}")

                    created_count += 1

            # Update upload status
            upload.processing_status = 'ingested'
            upload.locations_created = created_count
            upload.locations_skipped = skipped_count
            upload.processed_at = timezone.now()
            upload.processing_message = f"Created {created_count} locations, skipped {skipped_count} duplicates"
            upload.save()

            messages.success(
                request,
                f"Successfully imported {created_count} locations! "
                f"({skipped_count} duplicates skipped)"
            )

            # Redirect to geocoding dashboard
            return redirect('geolocation:validation_dashboard')

        except Exception as e:
            upload.processing_status = 'error'
            upload.processing_message = f"Ingestion failed: {str(e)}"
            upload.save()
            logger.error(f"Failed to ingest location CSV: {e}", exc_info=True)
            messages.error(request, f"Failed to import locations: {e}")
            return redirect('geolocation:ingest_location_csv', upload_id=upload_id)

    # GET request - show preview
    return render(request, 'geolocation/ingest_location_csv.html', {
        'upload': upload,
    })


@login_required
def delete_location_csv_upload(request, upload_id):
    """
    Delete a location CSV upload record.
    """
    from .models import LocationCSVUpload
    from django.contrib import messages

    upload = get_object_or_404(
        LocationCSVUpload,
        id=upload_id,
        uploaded_by=request.user
    )

    if request.method == 'POST':
        filename = upload.original_filename
        upload.delete()
        messages.success(request, f"Deleted upload: {filename}")
        return redirect('geolocation:upload_location_csv')

    # GET request - show confirmation
    return render(request, 'geolocation/delete_csv_upload_confirm.html', {
        'upload': upload,
    })


@login_required
def admin_boundaries_api(request, layer_type):
    """
    Serve admin boundary GeoJSON files for map overlays.

    Supports multiple admin levels with filtering:
    - countries: Country boundaries (Africa_Boundaries.geojson)
    - provinces: Province/state boundaries (admin_level 2-4)
    - districts: District boundaries (admin_level 5-8)

    Query parameters:
    - country: Filter by country name (optional)
    - bbox: Bounding box filter as minLng,minLat,maxLng,maxLat (optional)

    Args:
        request: Django HTTP request
        layer_type: Type of boundary layer ('countries', 'provinces', 'districts')

    Returns:
        JsonResponse: GeoJSON data or error message
    """
    import os

    # Configuration for each layer type
    layer_config = {
        'countries': {
            'file': 'Africa_Boundaries.geojson',
            'preprocessed_file': None,
            'admin_levels': None,
            'cache_timeout': 3600,
            'max_file_size_mb': 100,
        },
        'provinces': {
            'file': 'africa_admin_boundaries.geojson',
            'preprocessed_file': 'admin_boundaries/provinces.geojson',
            'admin_levels': ['2', '3', '4'],
            'cache_timeout': 1800,
            'max_file_size_mb': 50,
        },
        'districts': {
            'file': 'africa_admin_boundaries.geojson',
            'preprocessed_file': 'admin_boundaries/districts.geojson',
            'admin_levels': ['5', '6', '7', '8'],
            'cache_timeout': 1800,
            'max_file_size_mb': 100,
        },
    }

    if layer_type not in layer_config:
        return JsonResponse({
            'error': f'Unknown layer type: {layer_type}',
            'available_layers': list(layer_config.keys())
        }, status=400)

    config = layer_config[layer_type]
    data_dir = os.path.join(os.path.dirname(__file__), 'data_geocoding')

    # Parse query parameters
    country_filter = request.GET.get('country')
    bbox_param = request.GET.get('bbox')
    bbox = None
    if bbox_param:
        try:
            parts = [float(x) for x in bbox_param.split(',')]
            if len(parts) == 4:
                bbox = {
                    'minLng': parts[0],
                    'minLat': parts[1],
                    'maxLng': parts[2],
                    'maxLat': parts[3]
                }
        except ValueError:
            pass

    # Build cache key
    cache_key = f"admin_boundaries_{layer_type}"
    if country_filter:
        cache_key += f"_{country_filter}"
    if bbox:
        cache_key += f"_bbox_{bbox_param}"

    # Try cache first
    cached_data = cache.get(cache_key)
    if cached_data:
        return JsonResponse(cached_data, safe=False)

    # Fast path: use per-country GADM file when country filter is given
    if country_filter and layer_type in ('provinces', 'districts'):
        iso_map = _get_country_iso_map(data_dir)
        iso3 = iso_map.get(country_filter.strip().lower())
        if iso3:
            gadm_path = os.path.join(data_dir, f'{iso3}_AdminBoundaries.geojson')
            if os.path.exists(gadm_path):
                try:
                    with open(gadm_path, 'r', encoding='utf-8') as f:
                        gadm_data = json.load(f)
                    filtered = []
                    for feat in gadm_data.get('features', []):
                        props = feat.get('properties', {})
                        al = str(props.get('admin_level', ''))
                        if al not in config['admin_levels']:
                            continue
                        props = dict(props)
                        if layer_type == 'provinces':
                            props.setdefault('name', props.get('NAME_1', ''))
                        else:
                            props.setdefault('name', props.get('NAME_2', ''))
                        feat = dict(feat, properties=props)
                        filtered.append(feat)
                    result = {'type': 'FeatureCollection', 'features': filtered}
                    cache.set(cache_key, result, config['cache_timeout'])
                    return JsonResponse(result, safe=False)
                except Exception as e:
                    logger.warning(f"Failed to load GADM file for {iso3}: {e}")

    # Try preprocessed file first (faster, smaller)
    geojson_path = None
    if config['preprocessed_file']:
        preprocessed_path = os.path.join(data_dir, config['preprocessed_file'])
        if os.path.exists(preprocessed_path):
            geojson_path = preprocessed_path
            logger.info(f"Using preprocessed file for {layer_type}")

    # Fall back to main file
    if not geojson_path:
        geojson_path = os.path.join(data_dir, config['file'])

    if not os.path.exists(geojson_path):
        return JsonResponse({
            'error': f'{layer_type} boundary file not found',
            'help': 'Run: python manage.py process_admin_boundaries'
        }, status=404)

    # Check file size before loading
    file_size_mb = os.path.getsize(geojson_path) / (1024 * 1024)
    if file_size_mb > config['max_file_size_mb']:
        return JsonResponse({
            'error': f'{layer_type} boundary file too large ({file_size_mb:.0f}MB)',
            'help': 'Run: python manage.py process_admin_boundaries to create optimized files',
            'max_size_mb': config['max_file_size_mb']
        }, status=413)

    try:
        with open(geojson_path, 'r', encoding='utf-8') as f:
            geojson_data = json.load(f)

        # Filter features if using admin_levels and not using preprocessed file
        if config['admin_levels'] and not config['preprocessed_file']:
            filtered_features = []
            for feature in geojson_data.get('features', []):
                props = feature.get('properties', {})
                admin_level = str(props.get('admin_level', ''))

                if admin_level not in config['admin_levels']:
                    continue

                if country_filter:
                    feature_country = props.get('name', '') or props.get('name_en', '')
                    if country_filter.lower() not in feature_country.lower():
                        continue

                if bbox and not _feature_intersects_bbox(feature, bbox):
                    continue

                filtered_features.append(feature)

            geojson_data = {
                'type': 'FeatureCollection',
                'features': filtered_features
            }

            logger.info(f"Filtered {layer_type}: {len(filtered_features)} features")

        # Apply bbox filter if using preprocessed file
        elif bbox:
            filtered_features = [
                f for f in geojson_data.get('features', [])
                if _feature_intersects_bbox(f, bbox)
            ]
            geojson_data = {
                'type': 'FeatureCollection',
                'features': filtered_features
            }

        # Apply country filter if using preprocessed file
        elif country_filter:
            filtered_features = []
            for feature in geojson_data.get('features', []):
                props = feature.get('properties', {})
                feature_name = props.get('name', '') or props.get('name_en', '')
                if country_filter.lower() in feature_name.lower():
                    filtered_features.append(feature)
            geojson_data = {
                'type': 'FeatureCollection',
                'features': filtered_features
            }

        # Cache the result
        cache.set(cache_key, geojson_data, config['cache_timeout'])

        return JsonResponse(geojson_data, safe=False)

    except json.JSONDecodeError as e:
        logger.error(f"Invalid GeoJSON in {geojson_path}: {e}")
        return JsonResponse({
            'error': 'Invalid GeoJSON format',
            'details': str(e)
        }, status=500)
    except MemoryError:
        logger.error(f"Memory error loading {geojson_path}")
        return JsonResponse({
            'error': 'File too large to process',
            'help': 'Run: python manage.py process_admin_boundaries'
        }, status=413)
    except Exception as e:
        logger.error(f"Error reading boundary file {geojson_path}: {e}")
        return JsonResponse({
            'error': 'Failed to read boundary file',
            'details': str(e)
        }, status=500)


def _get_country_iso_map(data_dir):
    """Build and cache a mapping from country name (lowercase) to ISO3 code from GADM files."""
    import glob
    import os
    import re

    cache_key = 'boundary_country_iso_map_v1'
    cached = cache.get(cache_key)
    if cached:
        return cached

    iso_map = {}
    for path in sorted(glob.glob(os.path.join(data_dir, '???_AdminBoundaries.geojson'))):
        iso3 = os.path.basename(path)[:3].upper()
        try:
            with open(path, 'r', encoding='utf-8') as f:
                text = f.read(3000)
            m = re.search(r'"NAME_0"\s*:\s*"([^"]+)"', text)
            if m:
                name0 = m.group(1)
                iso_map[name0.lower()] = iso3
            iso_map[iso3.lower()] = iso3  # also allow ISO3 as filter value
        except Exception:
            iso_map[iso3.lower()] = iso3

    cache.set(cache_key, iso_map, 3600 * 24)
    return iso_map


def _feature_intersects_bbox(feature, bbox):
    """
    Check if a GeoJSON feature intersects with a bounding box.
    Uses a simplified check based on the feature's coordinates.
    """
    geometry = feature.get('geometry', {})
    coords = geometry.get('coordinates', [])

    if not coords:
        return False

    def check_coords(c):
        """Recursively check if any coordinate falls within bbox."""
        if isinstance(c[0], (int, float)):
            # This is a coordinate pair [lng, lat]
            lng, lat = c[0], c[1]
            return (bbox['minLng'] <= lng <= bbox['maxLng'] and
                    bbox['minLat'] <= lat <= bbox['maxLat'])
        else:
            # This is an array of coordinates, check any
            return any(check_coords(inner) for inner in c)

    return check_coords(coords)


@login_required
def boundary_geometry_api(request, layer_type, feature_index):
    """
    Return full polygon geometry for a boundary feature.

    Used for visualizing admin boundary matches on the map.
    Returns the complete GeoJSON geometry along with centroid.

    Args:
        request: Django HTTP request
        layer_type: Type of boundary layer ('countries', 'provinces', 'districts')
        feature_index: Index of the feature in the GeoJSON file

    Query params:
        source_file: Optional filename to load from directly (faster for large files)

    Returns:
        JsonResponse with geometry and centroid data
    """
    from .admin_boundary_service import get_admin_boundary_geocoder

    try:
        # Get optional source_file from query params (from boundary_reference)
        source_file = request.GET.get('source_file')

        geocoder = get_admin_boundary_geocoder()
        result = geocoder.get_boundary_polygon(layer_type, int(feature_index), source_file)

        if result.get('success'):
            return JsonResponse({
                'success': True,
                'geometry': result['geometry'],
                'centroid': result['centroid'],
                'properties': result['properties']
            })
        else:
            return JsonResponse({
                'success': False,
                'error': result.get('error', 'Feature not found')
            }, status=404)

    except ValueError:
        return JsonResponse({
            'success': False,
            'error': f'Invalid feature index: {feature_index}'
        }, status=400)
    except Exception as e:
        logger.error(f"Error getting boundary geometry: {e}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
def research_metrics_api(request):
    """
    Aggregate geocoding accuracy metrics across all validated locations for the current user.

    Returns the statistics needed for the HarmonAIze paper:
      - Per-source success rate, accuracy at 1 km and 5 km, false-positive rate
      - Inter-source agreement (% locations where any source diverges > 5 km)
      - Confidence score distribution
      - Validated-dataset reuse rate (locations served from cache without API calls)
      - Median and mean distances per source (for p-value comparisons)

    Only locations that have source_comparison_metrics populated are included
    (i.e. locations that have been validated through any workflow).
    """
    import statistics

    qs = GeocodingResult.objects.filter(
        created_by=request.user,
        source_comparison_metrics__isnull=False,
    ).select_related('validation')

    total_validated = qs.count()
    if total_validated == 0:
        return JsonResponse({
            'total_validated': 0,
            'message': 'No validated locations with comparison metrics yet. Validate some locations first.',
        })

    all_sources = ['hdx', 'arcgis', 'google', 'nominatim', 'admin_boundary', 'user_provided']

    source_stats = {s: {
        'attempted': 0,
        'succeeded': 0,
        'distances_km': [],
        'accurate_1km': 0,
        'accurate_5km': 0,
        'inaccurate_beyond_5km': 0,
    } for s in all_sources}

    inter_source = {
        'locations_with_multiple_sources': 0,
        'locations_with_discrepancy_5km': 0,
        'max_distances_km': [],
    }

    confidence_scores = []
    locations_from_validated_dataset = 0

    for result in qs:
        metrics = result.source_comparison_metrics
        if not metrics:
            continue

        sm = metrics.get('source_metrics', {})
        for source in all_sources:
            sd = sm.get(source, {})
            source_stats[source]['attempted'] += 1
            if sd.get('succeeded'):
                source_stats[source]['succeeded'] += 1
                dist = sd.get('distance_to_ground_truth_km')
                if dist is not None:
                    source_stats[source]['distances_km'].append(dist)
                    if sd.get('is_accurate_1km'):
                        source_stats[source]['accurate_1km'] += 1
                    if sd.get('is_accurate_5km'):
                        source_stats[source]['accurate_5km'] += 1
                    else:
                        source_stats[source]['inaccurate_beyond_5km'] += 1

        summary = metrics.get('summary', {})
        n_succeeded = summary.get('total_sources_succeeded', 0)
        if n_succeeded >= 2:
            inter_source['locations_with_multiple_sources'] += 1
            max_d = summary.get('max_inter_source_distance_km', 0)
            inter_source['max_distances_km'].append(max_d)
            if summary.get('has_discrepancy_beyond_5km'):
                inter_source['locations_with_discrepancy_5km'] += 1

        # Collect confidence scores
        try:
            conf = result.validation.confidence_score
            if conf is not None:
                confidence_scores.append(conf)
        except Exception:
            pass

        # Validated dataset reuse: selected_source stored as 'validated_dataset'
        gt_source = metrics.get('ground_truth', {}).get('source', '')
        if 'validated_dataset' in gt_source:
            locations_from_validated_dataset += 1

    # Build per-source output
    source_output = {}
    for source in all_sources:
        s = source_stats[source]
        dists = s['distances_km']
        succeeded = s['succeeded']

        source_output[source] = {
            'attempted': s['attempted'],
            'succeeded': succeeded,
            'failed': s['attempted'] - succeeded,
            'failure_rate': round((s['attempted'] - succeeded) / s['attempted'], 4) if s['attempted'] else None,
            'accurate_within_1km': s['accurate_1km'],
            'accurate_within_5km': s['accurate_5km'],
            'inaccurate_beyond_5km': s['inaccurate_beyond_5km'],
            'accuracy_rate_1km': round(s['accurate_1km'] / succeeded, 4) if succeeded else None,
            'accuracy_rate_5km': round(s['accurate_5km'] / succeeded, 4) if succeeded else None,
            'false_positive_rate_5km': round(s['inaccurate_beyond_5km'] / succeeded, 4) if succeeded else None,
            'mean_distance_km': round(statistics.mean(dists), 3) if dists else None,
            'median_distance_km': round(statistics.median(dists), 3) if dists else None,
            'stdev_distance_km': round(statistics.stdev(dists), 3) if len(dists) >= 2 else None,
            'min_distance_km': round(min(dists), 3) if dists else None,
            'max_distance_km': round(max(dists), 3) if dists else None,
        }

    n_multi = inter_source['locations_with_multiple_sources']
    max_dists = inter_source['max_distances_km']

    inter_source_output = {
        'locations_with_multiple_sources': n_multi,
        'locations_with_discrepancy_beyond_5km': inter_source['locations_with_discrepancy_5km'],
        'pct_locations_with_discrepancy_5km': round(
            inter_source['locations_with_discrepancy_5km'] / n_multi, 4
        ) if n_multi else None,
        'mean_max_inter_source_distance_km': round(statistics.mean(max_dists), 3) if max_dists else None,
        'median_max_inter_source_distance_km': round(statistics.median(max_dists), 3) if max_dists else None,
    }

    conf_output = {}
    if confidence_scores:
        conf_output = {
            'mean': round(statistics.mean(confidence_scores), 4),
            'median': round(statistics.median(confidence_scores), 4),
            'stdev': round(statistics.stdev(confidence_scores), 4) if len(confidence_scores) >= 2 else None,
            'high_confidence_count': sum(1 for c in confidence_scores if c >= 0.8),
            'medium_confidence_count': sum(1 for c in confidence_scores if 0.6 <= c < 0.8),
            'low_confidence_count': sum(1 for c in confidence_scores if c < 0.6),
        }

    return JsonResponse({
        'total_validated': total_validated,
        'source_stats': source_output,
        'inter_source_agreement': inter_source_output,
        'confidence_score_distribution': conf_output,
        'validated_dataset_reuse': {
            'locations_from_cache': locations_from_validated_dataset,
            'reuse_rate': round(locations_from_validated_dataset / total_validated, 4),
        },
        'note': (
            'accuracy_rate_1km = fraction of succeeded locations within 1 km of ground truth; '
            'false_positive_rate_5km = fraction of succeeded locations more than 5 km from ground truth'
        ),
    })
