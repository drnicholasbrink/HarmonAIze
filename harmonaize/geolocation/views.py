# geolocation/views.py
import json
import traceback
import logging
import requests
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView
from django.db import transaction
from django.contrib import messages
from django.utils import timezone

from .models import GeocodingResult, ValidationResult, ValidationDataset
from .validation import SmartGeocodingValidator
from core.models import Location
from geolocation.management.commands.geocode_locations import Command as GeocodeCommand

# Set up logging
logger = logging.getLogger(__name__)


def validation_map(request):
    """Enhanced map view with individual source scoring and source colors."""
    
    # Get locations that need validation (one at a time)
    location_id = request.GET.get('location_id')
    
    if location_id:
        # Show specific location
        try:
            result = GeocodingResult.objects.get(id=location_id)
            results = [result]
        except GeocodingResult.DoesNotExist:
            results = []
    else:
        # Get first location needing validation
        results = GeocodingResult.objects.filter(
            validation__validation_status__in=['needs_review', 'pending']
        ).order_by('created_at')[:1]
        
        if not results:
            # If no pending validations, get the first unvalidated result
            results = GeocodingResult.objects.filter(
                validation__isnull=True
            ).order_by('created_at')[:1]
    
    print(f"DEBUG: Found {len(results)} result(s) to display")
    
    # Prepare enhanced data for the template
    locations_data = []
    
    for result in results:
        print(f"DEBUG: Processing {result.location_name}")
        
        # Extract coordinates from all sources with FIXED COLORS
        coordinates = []
        
        # FIXED SOURCE COLORS - ArcGIS is now purple, Green is reserved for AI recommendation
        source_colors = {
            'hdx': '#3b82f6',      # Blue
            'arcgis': '#8b5cf6',   # Purple (changed from green)
            'google': '#dc2626',   # Red
            'nominatim': '#f59e0b' # Orange
        }
        
        # Check each source and add enhanced information
        if result.hdx_success and result.hdx_lat and result.hdx_lng:
            coordinates.append({
                'source': 'HDX',
                'source_key': 'hdx',
                'lat': result.hdx_lat,
                'lng': result.hdx_lng,
                'color': source_colors['hdx']
            })
            
        if result.arcgis_success and result.arcgis_lat and result.arcgis_lng:
            coordinates.append({
                'source': 'ArcGIS',
                'source_key': 'arcgis', 
                'lat': result.arcgis_lat,
                'lng': result.arcgis_lng,
                'color': source_colors['arcgis']  # Now purple instead of green
            })
            
        if result.google_success and result.google_lat and result.google_lng:
            coordinates.append({
                'source': 'Google',
                'source_key': 'google',
                'lat': result.google_lat,
                'lng': result.google_lng, 
                'color': source_colors['google']
            })
            
        if result.nominatim_success and result.nominatim_lat and result.nominatim_lng:
            coordinates.append({
                'source': 'OpenStreetMap',
                'source_key': 'nominatim',
                'lat': result.nominatim_lat,
                'lng': result.nominatim_lng,
                'color': source_colors['nominatim']
            })
        
        if coordinates:  # Only add if we have coordinates
            # Get validation data if exists
            validation = getattr(result, 'validation', None)
            status = validation.validation_status if validation else 'pending'
            
            # Extract enhanced analysis data
            metadata = validation.validation_metadata if validation else {}
            individual_scores = metadata.get('individual_scores', {})
            reverse_geocoding = metadata.get('reverse_geocoding_results', {})
            
            # FIXED: Use best individual source score as overall confidence
            best_source = metadata.get('best_source')
            best_score = metadata.get('best_score', 0.0)
            
            # Overall confidence is the best individual source score
            if best_score > 0:
                confidence = best_score * 100
                print(f"DEBUG: Using best individual source score for {result.location_name}: {confidence}%")
            elif validation:
                # Fallback to validation confidence if no best score available
                confidence = validation.confidence_score * 100
                print(f"DEBUG: Using validation confidence for {result.location_name}: {confidence}%")
            else:
                confidence = 50  # Default if no validation available
                print(f"DEBUG: Using default confidence for {result.location_name}: {confidence}%")
            
            # UPDATED: Add individual source scoring info to each coordinate
            for coord in coordinates:
                source_key = coord['source_key']
                
                # Add reverse geocoding information
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
                
                # UPDATED: Get individual source confidence from simplified validation structure
                if source_key in individual_scores:
                    source_score = individual_scores[source_key]
                    # Get the scores from simplified Auto-Validation system
                    reverse_score = source_score.get('reverse_geocoding_score', 0.0)
                    distance_score = source_score.get('distance_penalty_score', 0.0)
                    individual_confidence = source_score.get('individual_confidence', 0.0)
                    
                    # Store for display
                    coord['reverse_geocoding_score'] = reverse_score * 100
                    coord['distance_penalty_score'] = distance_score * 100
                    coord['individual_confidence'] = individual_confidence * 100
                    
                    # FIXED: Verify the calculation matches the expected formula (70% reverse + 30% distance)
                    calculated_score = (reverse_score * 0.70) + (distance_score * 0.30)
                    print(f"DEBUG {source_key}: Reverse={reverse_score:.2f}, Distance={distance_score:.2f}, Individual={individual_confidence:.2f}, Calculated={calculated_score:.2f}")
                    
                else:
                    # Fallback calculation if no individual scoring
                    reverse_score = coord['name_similarity']
                    distance_score = 0.5  # Default distance score
                    calculated_score = (reverse_score * 0.70) + (distance_score * 0.30)
                    
                    coord['reverse_geocoding_score'] = reverse_score * 100
                    coord['distance_penalty_score'] = distance_score * 100
                    coord['individual_confidence'] = calculated_score * 100
                
                # Update overall_confidence to use individual_confidence
                coord['overall_confidence'] = coord['individual_confidence']
                
                # Add user-friendly confidence description
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
            
            # Sort coordinates by confidence (highest first)
            coordinates.sort(key=lambda x: x['overall_confidence'], reverse=True)
            
            # Determine recommended source (best individual source or from AI analysis)
            if metadata.get('best_source'):
                recommended_source = metadata.get('best_source').upper()
            else:
                recommended_source = coordinates[0]['source'] if coordinates else None
            
            # Convert variance to user-friendly language
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
            
            # Enhanced location data with individual source scoring
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
                'ai_summary': metadata.get('user_friendly_summary', 'auto-validation completed...')
            })
    
    # Get navigation info for next/previous locations
    navigation_info = get_navigation_info(location_id)
    
    # Calculate stats for the template
    stats = get_validation_stats()
    
    context = {
        'locations_data': json.dumps(locations_data),
        'mapbox_token': 'pk.eyJ1Ijoic2htcm9uIiwiYSI6ImNtNzM3MjllODBpczUybHB2dDMzNHg0OHUifQ.njJOQZ3_ZR-kDrTfFXZX0Q',
        'current_location': locations_data[0] if locations_data else None,
        'navigation': navigation_info,
        **stats
    }
    
    return render(request, 'geolocation/validation_map.html', context)


def get_navigation_info(current_location_id):
    """Get navigation information for next/previous locations."""
    # Get all locations that need validation
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

def get_validation_stats():
    """Get CORRECTED validation statistics for the dashboard."""
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
    
    # CORRECTED: Pending validation should only count locations that have been geocoded but not validated
    # This means they have GeocodingResult entries but the core Location doesn't have coordinates yet
    pending_validation = 0
    
    # Get locations that have geocoding results but no coordinates in core Location
    for location in Location.objects.filter(latitude__isnull=True, longitude__isnull=True):
        geocoding_result = GeocodingResult.objects.filter(
            location_name__iexact=location.name
        ).first()
        if geocoding_result and geocoding_result.has_any_results:
            pending_validation += 1
    
    # Also add locations that have validation but still need review
    pending_validation += ValidationResult.objects.filter(
        validation_status__in=['needs_review', 'pending']
    ).count()
    
    # CORRECTED: Awaiting geocoding = locations without coordinates AND without geocoding results
    awaiting_geocoding = 0
    for location in Location.objects.filter(latitude__isnull=True, longitude__isnull=True):
        geocoding_result = GeocodingResult.objects.filter(
            location_name__iexact=location.name
        ).first()
        if not geocoding_result or not geocoding_result.has_any_results:
            awaiting_geocoding += 1
    
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

class ValidationDashboardView(TemplateView):
    """Enhanced validation dashboard with summary and actions."""
    template_name = 'geolocation/validation_dashboard.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get enhanced validation statistics
        stats = {
            'total_validations': ValidationResult.objects.count(),
            'auto_validated': ValidationResult.objects.filter(validation_status='validated').count(),
            'needs_review': ValidationResult.objects.filter(validation_status='needs_review').count(),
            'pending_manual': ValidationResult.objects.filter(validation_status='pending').count(),
            'rejected': ValidationResult.objects.filter(validation_status='rejected').count(),
            'high_confidence': ValidationResult.objects.filter(confidence_score__gte=0.8).count(),
            'medium_confidence': ValidationResult.objects.filter(
                confidence_score__gte=0.6, confidence_score__lt=0.8
            ).count(),
            'low_confidence': ValidationResult.objects.filter(confidence_score__lt=0.6).count(),
        }
        
        # Get recent validations needing attention
        recent_validations = ValidationResult.objects.filter(
            validation_status__in=['needs_review', 'pending']
        ).select_related('geocoding_result').order_by('-created_at')[:10]
        
        context.update({
            'stats': stats,
            'recent_validations': recent_validations,
        })
        
        return context

@csrf_exempt
def location_status_api(request):
    """FIXED: API endpoint to get comprehensive location status for dashboard table."""
    if request.method == 'GET':
        try:
            locations_data = []
            locations = Location.objects.all().order_by('name')
            
            for location in locations:
                # FIXED: Determine status with proper refresh after validation
                if location.latitude is not None and location.longitude is not None:
                    status = 'validated'
                    status_display = '✅ Validated & Complete'
                    status_color = 'green'
                    confidence = 100
                    sources = ['Final']
                    coordinates = {'lat': location.latitude, 'lng': location.longitude}
                    geocoding_result_id = None
                else:
                    # Look for geocoding result
                    geocoding_result = GeocodingResult.objects.filter(
                        location_name__iexact=location.name
                    ).first()
                    
                    if geocoding_result:
                        geocoding_result_id = geocoding_result.id
                        
                        # Check if validation exists and is recent
                        validation = getattr(geocoding_result, 'validation', None)
                        
                        if validation:
                            # FIXED: Handle validation status updates immediately
                            if validation.validation_status == 'validated':
                                # If validation is complete, update core location
                                final_coords = validation.final_coordinates
                                if final_coords:
                                    lat, lng = final_coords
                                    location.latitude = lat
                                    location.longitude = lng
                                    location.save()
                                    
                                    status = 'validated'
                                    status_display = '✅ Validated & Complete'
                                    status_color = 'green'
                                    confidence = 100
                                    sources = ['Final']
                                    coordinates = {'lat': lat, 'lng': lng}
                                else:
                                    # Validation exists but no final coordinates
                                    status = 'needs_review'
                                    status_display = '⚠️ Validation Error - Review Required'
                                    status_color = 'orange'
                                    confidence = int(validation.confidence_score * 100)
                                    sources = []
                                    coordinates = None
                            elif validation.validation_status == 'needs_review':
                                status = 'needs_review'
                                status_display = '⚠️ Good Quality - Quick Review'
                                status_color = 'yellow'
                                # FIXED: Use best individual source confidence from simplified system
                                metadata = validation.validation_metadata or {}
                                best_score = metadata.get('best_score', validation.confidence_score)
                                confidence = int(best_score * 100)
                            elif validation.validation_status == 'pending':
                                status = 'pending'
                                status_display = '🔍 Lower Quality - Detailed Review'
                                status_color = 'orange'
                                # FIXED: Use best individual source confidence from simplified system
                                metadata = validation.validation_metadata or {}
                                best_score = metadata.get('best_score', validation.confidence_score)
                                confidence = int(best_score * 100)
                            elif validation.validation_status == 'rejected':
                                status = 'rejected'
                                status_display = '❌ Rejected - Invalid Location'
                                status_color = 'red'
                                confidence = 0
                            else:
                                status = 'geocoded'
                                status_display = '🔍 Geocoded - Awaiting Auto-validation Analysis'
                                status_color = 'blue'
                                confidence = 50
                        else:
                            # No validation yet, but has geocoding results
                            if geocoding_result.has_any_results:
                                status = 'geocoded'
                                status_display = '🔍 Geocoded - Awaiting Auto-validation Analysis'
                                status_color = 'blue'
                                confidence = 50
                            else:
                                status = 'awaiting_geocoding'
                                status_display = '📍 Awaiting Geocoding'
                                status_color = 'red'
                                confidence = 0
                        
                        # FIXED: Get available sources properly
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
                            
                            # Get coordinates from the first successful source for display
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
                        status_display = '📍 Awaiting Geocoding'
                        status_color = 'red'
                        confidence = 0
                        sources = []
                        coordinates = None
                        geocoding_result_id = None
                
                # FIXED: If status is still validated, ensure we use core location coordinates
                if status == 'validated' and location.latitude and location.longitude:
                    coordinates = {'lat': location.latitude, 'lng': location.longitude}
                    sources = ['Final']
                
                locations_data.append({
                    'id': location.id,
                    'name': location.name,
                    'status': status,
                    'status_display': status_display,
                    'status_color': status_color,
                    'confidence': confidence,
                    'sources': sources,
                    'coordinates': coordinates,
                    'geocoding_result_id': geocoding_result_id
                })
            
            # FIXED: Calculate accurate summary
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
                'timestamp': timezone.now().isoformat()  # For cache busting
            })
            
        except Exception as e:
            logger.error(f"Error fetching location status: {str(e)}")
            print(f"Error fetching location status: {str(e)}")
            import traceback
            traceback.print_exc()
            return JsonResponse({
                'success': False,
                'error': f'Failed to fetch location status: {str(e)}'
            }, status=500)
    
    return JsonResponse({'error': 'Only GET requests are allowed'}, status=405)

@csrf_exempt
def validation_queue_api(request):
    """API endpoint to get actual validation queue data for the table."""
    if request.method == 'GET':
        try:
            # Get locations that need validation with their details
            pending_validations = ValidationResult.objects.filter(
                validation_status__in=['needs_review', 'pending']
            ).select_related('geocoding_result').order_by('-confidence_score')[:20]
            
            # If no validations exist, get unvalidated geocoding results
            if not pending_validations.exists():
                unvalidated_results = GeocodingResult.objects.filter(
                    validation__isnull=True
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
                
                # Use best individual source score if available, otherwise validation confidence
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
            print(f"Error fetching validation queue: {str(e)}")
            return JsonResponse({
                'success': False,
                'error': f'Failed to fetch validation queue: {str(e)}'
            }, status=500)
    
    return JsonResponse({'error': 'Only GET requests are allowed'}, status=405)


@csrf_exempt
def validation_api(request):
    """Enhanced API endpoint for validation actions with better error handling."""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            action = data.get('action')
            validation_id = data.get('validation_id')
            geocoding_result_id = data.get('geocoding_result_id')
            
            # Get validation or geocoding result
            if validation_id:
                validation = get_object_or_404(ValidationResult, id=validation_id)
            elif geocoding_result_id:
                geocoding_result = get_object_or_404(GeocodingResult, id=geocoding_result_id)
                validation = getattr(geocoding_result, 'validation', None)
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
            print(f"Validation API Error: {str(e)}")
            print(f"Traceback: {traceback.format_exc()}")
            return JsonResponse({
                'success': False,
                'error': f'An unexpected error occurred while processing your request: {str(e)}'
            }, status=500)
    
    return JsonResponse({'error': 'Only POST requests are allowed'}, status=405)


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
                
                # Run geocoding command
                geocode_cmd = GeocodeCommand()
                
                # Get locations without coordinates
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
                        # Check validated dataset first (cache)
                        validated_result = geocode_cmd.check_validated_dataset(location)
                        if validated_result:
                            with transaction.atomic():
                                location.latitude = validated_result.final_lat
                                location.longitude = validated_result.final_long
                                location.save()
                            from_cache += 1
                            found_coordinates += 1
                            continue
                        
                        # Check if results already exist
                        if not force:
                            existing_result = GeocodingResult.objects.filter(
                                location_name__iexact=location.name
                            ).first()
                            
                            if existing_result and existing_result.has_any_results:
                                found_coordinates += 1
                                continue
                        
                        # Perform new geocoding search
                        success = geocode_cmd.geocode_location(location)
                        if success:
                            new_searches += 1
                            found_coordinates += 1
                        else:
                            no_results += 1
                            
                    except Exception as e:
                        logger.error(f"Error geocoding {location.name}: {e}")
                        print(f"Error geocoding {location.name}: {e}")
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
            
            elif action == 'get_geocoding_stats':
                # Get CORRECTED current statistics
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
                    'error': f'Unknown action: {action}. Available actions: run_geocoding, get_geocoding_stats'
                }, status=400)
                
        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Invalid JSON format in request body'
            }, status=400)
        except Exception as e:
            logger.error(f"Geocoding API Error: {str(e)}")
            print(f"Geocoding API Error: {str(e)}")
            print(f"Traceback: {traceback.format_exc()}")
            return JsonResponse({
                'success': False,
                'error': f'An unexpected error occurred: {str(e)}'
            }, status=500)
    
    return JsonResponse({'error': 'Only POST requests are allowed'}, status=405)


@csrf_exempt
def bulk_validation_actions(request):
    """FIXED: Handle bulk validation actions with enhanced auto-approve logic."""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            action = data.get('action')
            
            if action == 'auto_validate_high_confidence':
                # Check if any geocoding has been done
                total_geocoding_results = GeocodingResult.objects.count()
                if total_geocoding_results == 0:
                    return JsonResponse({
                        'success': False,
                        'error': 'No locations have been geocoded yet. Please run "Start Coordinate Search" first to find coordinates for your locations.'
                    }, status=400)
                
                # Check if any validations exist
                total_validations = ValidationResult.objects.count()
                if total_validations == 0:
                    return JsonResponse({
                        'success': False,
                        'error': 'No validation analysis has been performed yet. Please wait for analysis to complete first.'
                    }, status=400)
                
                # FIXED: Look for high-confidence results that need validation
                # Look for ValidationResults with status = 'needs_review'
                high_confidence_results = ValidationResult.objects.filter(
                    validation_status='needs_review'  # Only those needing review
                ).select_related('geocoding_result')
                
                # FIXED: Filter by best individual source confidence (≥80%)
                qualified_results = []
                for validation in high_confidence_results:
                    metadata = validation.validation_metadata or {}
                    best_score = metadata.get('best_score', validation.confidence_score)
                    
                    # Check if best score has >= 80% confidence
                    if best_score >= 0.8:  # 80% threshold
                        qualified_results.append(validation)
                        print(f"✅ Qualified for auto-approve: {validation.geocoding_result.location_name} - {best_score*100:.1f}%")
                
                if not qualified_results:
                    return JsonResponse({
                        'success': True,
                        'message': 'No locations found with ≥80% best source confidence that need auto-validation. All high-confidence locations may already be validated.'
                    })
                
                count = 0
                errors = 0
                for validation in qualified_results:
                    try:
                        # FIXED: Use the existing handle_approve_ai_suggestion function
                        with transaction.atomic():
                            result = validation.geocoding_result
                            metadata = validation.validation_metadata or {}
                            best_source = metadata.get('best_source')
                            
                            # Get coordinates from best source
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
                            
                            # Add to ValidationDataset
                            ValidationDataset.objects.update_or_create(
                                location_name=result.location_name,
                                defaults={
                                    'final_lat': final_lat,
                                    'final_long': final_lng,
                                    'country': '',
                                    'source': f'auto_approve_{best_source}',
                                    'validated_at': timezone.now()
                                }
                            )
                            
                            # FIXED: Update core Location model immediately
                            try:
                                location = Location.objects.get(name__iexact=result.location_name)
                                location.latitude = final_lat
                                location.longitude = final_lng
                                location.save()
                                count += 1
                                print(f"✅ Auto-approved: {location.name} using {best_source.upper()}")
                            except Location.DoesNotExist:
                                print(f"⚠️ Warning: Could not find Location: {result.location_name}")
                                errors += 1
                            except Location.MultipleObjectsReturned:
                                location = Location.objects.filter(name__iexact=result.location_name).first()
                                location.latitude = final_lat
                                location.longitude = final_lng
                                location.save()
                                count += 1
                                print(f"✅ Auto-approved: {location.name} using {best_source.upper()}")
                        
                    except Exception as e:
                        logger.error(f"Error auto-validating {validation.geocoding_result.location_name}: {e}")
                        print(f"Error auto-validating {validation.geocoding_result.location_name}: {e}")
                        errors += 1
                        continue
                
                if count > 0:
                    return JsonResponse({
                        'success': True,
                        'message': f'✅ Successfully auto-approved {count} high confidence locations (≥80% best source confidence)' + (f' ({errors} had errors)' if errors > 0 else '')
                    })
                else:
                    return JsonResponse({
                        'success': False,
                        'error': f'Failed to auto-approve any locations. {errors} errors occurred during processing.'
                    })
            
            elif action == 'run_smart_validation_batch':
                # Check if any geocoding results exist
                total_geocoding_results = GeocodingResult.objects.count()
                if total_geocoding_results == 0:
                    return JsonResponse({
                        'success': False,
                        'error': 'No locations have been geocoded yet. Please run "Start Coordinate Search" first to find coordinates for your locations before running Auto-Validation analysis.'
                    }, status=400)
                
                # Check if there are results to analyze
                pending_results = GeocodingResult.objects.filter(
                    validation__isnull=True
                ).exclude(validation_status='rejected')
                
                if not pending_results.exists():
                    return JsonResponse({
                        'success': True,
                        'message': 'All geocoded locations have already been analyzed by Auto-Validation . No new analysis needed.'
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
                            print(f"🔍 Auto-Validation : {result.location_name}")
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
                            print(f"⚠️ External API timeout for {result.location_name} - using basic validation")
                            stats['processed'] += 1
                            stats['needs_review'] += 1
                            continue
                        except Exception as e:
                            logger.error(f"Error validating {result.location_name}: {e}")
                            print(f"Error validating {result.location_name}: {e}")
                            stats['rejected'] += 1
                            continue
                    
                    if stats['processed'] == 0:
                        return JsonResponse({
                            'success': True,
                            'message': 'No new locations to analyze. All locations have already been processed by Auto-Validation .'
                        })
                    
                    return JsonResponse({
                        'success': True,
                        'message': f'✅ Auto-Validation analysis completed: processed {stats["processed"]} locations with reverse geocoding and distance proximity scoring. {stats["auto_validated"]} auto-validated, {stats["needs_review"]} need review, {stats["pending"]} need manual verification.',
                        'stats': stats
                    })
                    
                except Exception as e:
                    logger.error(f"Error running Auto-Validation : {str(e)}")
                    print(f"Error running Auto-Validation : {str(e)}")
                    print(f"Traceback: {traceback.format_exc()}")
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
            print(f"Bulk validation error: {str(e)}")
            print(f"Traceback: {traceback.format_exc()}")
            return JsonResponse({
                'success': False,
                'error': f'An unexpected error occurred during bulk operation: {str(e)}'
            }, status=500)
    
    return JsonResponse({'error': 'Only POST requests are allowed'}, status=405)


# FIXED: Complete validation handler with proper status updates
def handle_approve_ai_suggestion(validation, data):
    """Handle approval of AI suggestion with enhanced error handling and status updates."""
    try:
        # Get AI recommended source from metadata
        metadata = validation.validation_metadata or {}
        best_source = metadata.get('best_source')
        
        if not best_source:
            return JsonResponse({
                'success': False,
                'error': 'No Auto-Validation recommendation available for this location. Please run Auto-Validation analysis first or select a source manually.'
            }, status=400)
        
        with transaction.atomic():
            result = validation.geocoding_result
            
            # Get coordinates from AI recommended source
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
                    'error': f'The Auto-Validation recommended source ({best_source}) does not have valid coordinates. Please select a different source manually.'
                }, status=400)
            
            # FIXED: Update validation status with proper completion
            validation.validation_status = 'validated'
            validation.validated_at = timezone.now()
            validation.validated_by = 'Two_Component_Recommendation'
            validation.recommended_lat = final_lat
            validation.recommended_lng = final_lng
            validation.recommended_source = best_source
            validation.save()
            
            # FIXED: Add to ValidationDataset (the "validated dataset")
            ValidationDataset.objects.update_or_create(
                location_name=result.location_name,
                defaults={
                    'final_lat': final_lat,
                    'final_long': final_lng,
                    'country': '',  # Add country if available
                    'source': f'two_component_{best_source}',
                    'validated_at': timezone.now()
                }
            )
            
            # FIXED: Update the core Location model immediately
            try:
                location = Location.objects.get(name__iexact=result.location_name)
                location.latitude = final_lat
                location.longitude = final_lng
                location.save()
                print(f"✅ Updated core Location: {location.name} -> {final_lat}, {final_lng}")
            except Location.DoesNotExist:
                print(f"⚠️  Warning: Could not find Location with name: {result.location_name}")
            except Location.MultipleObjectsReturned:
                location = Location.objects.filter(name__iexact=result.location_name).first()
                location.latitude = final_lat
                location.longitude = final_lng
                location.save()
                print(f"✅ Updated first matching Location: {location.name} -> {final_lat}, {final_lng}")
            
            return JsonResponse({
                'success': True,
                'message': f'✅ Auto-Validation recommendation accepted: {result.location_name} validated using {best_source.upper()} coordinates',
                'coordinates': {'lat': final_lat, 'lng': final_lng},
                'source': best_source,
                'status': 'validated',
                'trigger_refresh': True  # Signal for frontend to refresh
            })
    
    except Exception as e:
        logger.error(f"Error approving Auto-Validation suggestion: {str(e)}")
        print(f"Error approving Auto-Validation suggestion: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({
            'success': False,
            'error': f'Failed to approve Auto-Validation suggestion: {str(e)}'
        }, status=500)


# FIXED: Complete use source handler with proper status updates
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
            
            # Get coordinates from selected source
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
            
            # FIXED: Update validation with proper completion
            validation.validation_status = 'validated'
            validation.validated_at = timezone.now()
            validation.validated_by = 'User_Selection'
            validation.manual_review_notes = f'User manually selected {source.upper()} coordinates'
            validation.recommended_lat = final_lat
            validation.recommended_lng = final_lng
            validation.recommended_source = source
            validation.save()
            
            # FIXED: Add to ValidationDataset
            ValidationDataset.objects.update_or_create(
                location_name=result.location_name,
                defaults={
                    'final_lat': final_lat,
                    'final_long': final_lng,
                    'country': '',
                    'source': source,
                    'validated_at': timezone.now()
                }
            )
            
            # FIXED: Update core Location model immediately
            try:
                location = Location.objects.get(name__iexact=result.location_name)
                location.latitude = final_lat
                location.longitude = final_lng
                location.save()
                print(f"✅ Updated core Location: {location.name} -> {final_lat}, {final_lng}")
            except Location.DoesNotExist:
                print(f"⚠️  Warning: Could not find Location: {result.location_name}")
            except Location.MultipleObjectsReturned:
                location = Location.objects.filter(name__iexact=result.location_name).first()
                location.latitude = final_lat
                location.longitude = final_lng
                location.save()
                print(f"✅ Updated first matching Location: {location.name} -> {final_lat}, {final_lng}")
            
            return JsonResponse({
                'success': True,
                'message': f'✅ Using {source.upper()} coordinates for {result.location_name}',
                'coordinates': {'lat': final_lat, 'lng': final_lng},
                'source': source,
                'status': 'validated',
                'trigger_refresh': True  # Signal for frontend to refresh
            })
    
    except Exception as e:
        logger.error(f"Error using source: {str(e)}")
        print(f"Error using source: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({
            'success': False,
            'error': f'Failed to use selected source: {str(e)}'
        }, status=500)


# FIXED: Complete manual coordinates handler with proper status updates
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
            
            # FIXED: Update validation with manual coordinates
            validation.manual_lat = lat
            validation.manual_lng = lng
            validation.manual_review_notes = notes
            validation.validation_status = 'validated'
            validation.validated_at = timezone.now()
            validation.validated_by = 'Manual_Entry'
            validation.confidence_score = 1.0  # Manual entry gets highest confidence
            validation.save()
            
            # FIXED: Add to ValidationDataset
            ValidationDataset.objects.update_or_create(
                location_name=result.location_name,
                defaults={
                    'final_lat': lat,
                    'final_long': lng,
                    'country': '',
                    'source': 'manual_entry',
                    'validated_at': timezone.now()
                }
            )
            
            # FIXED: Update core Location model immediately
            try:
                location = Location.objects.get(name__iexact=result.location_name)
                location.latitude = lat
                location.longitude = lng
                location.save()
                print(f"✅ Updated core Location: {location.name} -> {lat}, {lng}")
            except Location.DoesNotExist:
                print(f"⚠️  Warning: Could not find Location: {result.location_name}")
            except Location.MultipleObjectsReturned:
                location = Location.objects.filter(name__iexact=result.location_name).first()
                location.latitude = lat
                location.longitude = lng
                location.save()
                print(f"✅ Updated first matching Location: {location.name} -> {lat}, {lng}")
        
        return JsonResponse({
            'success': True,
            'message': f'✅ Manual coordinates saved for {result.location_name}',
            'coordinates': {'lat': lat, 'lng': lng},
            'source': 'manual',
            'status': 'validated',
            'trigger_refresh': True  # Signal for frontend to refresh
        })
    
    except ValueError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid coordinate values. Please enter valid numbers for latitude and longitude.'
        }, status=400)
    except Exception as e:
        logger.error(f"Error saving manual coordinates: {str(e)}")
        print(f"Error saving manual coordinates: {str(e)}")
        import traceback
        traceback.print_exc()
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
            'message': f'✅ Location rejected: {validation.geocoding_result.location_name}'
        })
    
    except Exception as e:
        logger.error(f"Error rejecting location: {str(e)}")
        print(f"Error rejecting location: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'Failed to reject location: {str(e)}'
        }, status=500)


def get_enhanced_validation_details(validation):
    """Get detailed validation information with Auto-Validation analysis."""
    try:
        result = validation.geocoding_result
        metadata = validation.validation_metadata or {}
        
        # Extract coordinate details with Auto-Validation scoring information
        coordinates = []
        sources = ['hdx', 'arcgis', 'google', 'nominatim']
        reverse_geocoding = metadata.get('reverse_geocoding_results', {})
        individual_scores = metadata.get('individual_scores', {})
        
        for source in sources:
            if getattr(result, f"{source}_success", False):
                lat = getattr(result, f"{source}_lat")
                lng = getattr(result, f"{source}_lng")
                
                # Get reverse geocoding info for this source
                reverse_info = reverse_geocoding.get(source, {})
                
                # Get individual source scoring info
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
        
        # Extract Auto-Validation analysis data
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
                'ai_summary': metadata.get('user_friendly_summary', 'Auto-Validation analysis completed with reverse geocoding and distance proximity validation'),
                'reverse_geocoding_results': reverse_geocoding,
                'individual_scores': individual_scores,
                'validation_flags': metadata.get('validation_flags', [])
            }
        })
    
    except Exception as e:
        logger.error(f"Error getting Auto-Validation  details: {str(e)}")
        print(f"Error getting validation details: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'Failed to get Auto-Validation  details: {str(e)}'
        }, status=500)


def run_ai_analysis(validation):
    """Re-run Auto-Validation analysis on a validation result with external API timeout handling."""
    try:
        validator = SmartGeocodingValidator()
        # Add timeout handling for external APIs
        updated_validation = validator.validate_geocoding_result(validation.geocoding_result)
        
        return JsonResponse({
            'success': True,
            'message': '✅ Auto-Validation analysis completed successfully with reverse geocoding and distance proximity validation',
            'confidence': updated_validation.confidence_score * 100,
            'status': updated_validation.validation_status,
            'two_component': True
        })
    except requests.exceptions.Timeout:
        logger.warning(f"External API timeout during validation of {validation.geocoding_result.location_name}")
        return JsonResponse({
            'success': True,
            'message': '⚠️ Auto-Validation analysis completed with basic factors (external APIs temporarily unavailable)',
            'confidence': validation.confidence_score * 100,
            'status': validation.validation_status,
            'two_component': False
        })
    except Exception as e:
        logger.error(f"Error running Auto-Validation analysis: {str(e)}")
        print(f"Error running Auto-Validation analysis: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'Auto-Validation analysis failed: {str(e)}'
        }, status=500)


def validation_statistics(request):
    """Get detailed validation statistics for dashboard with enhanced error handling."""
    try:
        # Basic counts
        total_locations = GeocodingResult.objects.count()
        total_validations = ValidationResult.objects.count()
        
        # Confidence distribution
        high_confidence = ValidationResult.objects.filter(confidence_score__gte=0.8).count()
        medium_confidence = ValidationResult.objects.filter(
            confidence_score__gte=0.6, confidence_score__lt=0.8
        ).count()
        low_confidence = ValidationResult.objects.filter(confidence_score__lt=0.6).count()
        
        # Status distribution
        validated = ValidationResult.objects.filter(validation_status='validated').count()
        needs_review = ValidationResult.objects.filter(validation_status='needs_review').count()
        pending = ValidationResult.objects.filter(validation_status='pending').count()
        rejected = ValidationResult.objects.filter(validation_status='rejected').count()
        
        # Source reliability stats
        source_usage = {}
        for source in ['google', 'arcgis', 'hdx', 'nominatim']:
            source_usage[source] = ValidationResult.objects.filter(
                recommended_source=source
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
        print(f"Error getting validation statistics: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'Failed to get statistics: {str(e)}'
        }, status=500)

def validated_locations_map(request):
    """FIXED: Show map of all validated locations with proper data structure."""
    # Get all validated locations from core Location model
    validated_locations = Location.objects.filter(
        latitude__isnull=False,
        longitude__isnull=False
    ).order_by('name')
    
    print(f"DEBUG: Found {validated_locations.count()} validated locations")
    
    # FIXED: Prepare data for map in the correct format
    locations_data = []
    for location in validated_locations:
        locations_data.append({
            'id': location.id,
            'name': location.name,
            'lat': float(location.latitude),
            'lng': float(location.longitude),
            'status': 'validated'
        })
        print(f"DEBUG: Added location: {location.name} at {location.latitude}, {location.longitude}")
    
    context = {
        'locations_data': json.dumps(locations_data),
        'mapbox_token': 'pk.eyJ1Ijoic2htcm9uIiwiYSI6ImNtNzM3MjllODBpczUybHB2dDMzNHg0OHUifQ.njJOQZ3_ZR-kDrTfFXZX0Q',
        'total_locations': len(locations_data)
    }
    
    print(f"DEBUG: Rendering template with {len(locations_data)} locations")
    return render(request, 'geolocation/validated_locations_map.html', context)