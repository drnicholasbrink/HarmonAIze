# geolocation/views.py
import json
import traceback
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


def validation_map(request):
    """Enhanced map view showing one location at a time for validation with AI analysis."""
    
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
        
        # Extract coordinates from all sources
        coordinates = []
        
        # Check each source and add enhanced information
        if result.hdx_success and result.hdx_lat and result.hdx_lng:
            coordinates.append({
                'source': 'HDX',
                'source_key': 'hdx',
                'lat': result.hdx_lat,
                'lng': result.hdx_lng,
                'color': '#2563eb'  # Blue
            })
            
        if result.arcgis_success and result.arcgis_lat and result.arcgis_lng:
            coordinates.append({
                'source': 'ArcGIS',
                'source_key': 'arcgis', 
                'lat': result.arcgis_lat,
                'lng': result.arcgis_lng,
                'color': '#059669'  # Green
            })
            
        if result.google_success and result.google_lat and result.google_lng:
            coordinates.append({
                'source': 'Google',
                'source_key': 'google',
                'lat': result.google_lat,
                'lng': result.google_lng, 
                'color': '#dc2626'  # Red
            })
            
        if result.nominatim_success and result.nominatim_lat and result.nominatim_lng:
            coordinates.append({
                'source': 'OpenStreetMap',
                'source_key': 'nominatim',
                'lat': result.nominatim_lat,
                'lng': result.nominatim_lng,
                'color': '#d97706'  # Orange
            })
        
        if coordinates:  # Only add if we have coordinates
            # Get validation data if exists
            validation = getattr(result, 'validation', None)
            confidence = validation.confidence_score if validation else 0.5
            status = validation.validation_status if validation else 'pending'
            
            # Extract enhanced analysis data
            metadata = validation.validation_metadata if validation else {}
            analysis = metadata.get('coordinates_analysis', {})
            reverse_geocoding = metadata.get('reverse_geocoding_results', {})
            recommendation = metadata.get('recommendation', {})
            
            # Add reverse geocoding info and confidence to each coordinate
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
                
                # Calculate individual source confidence (no source weights - equal treatment)
                reverse_score = coord['name_similarity'] * 0.7 + coord['reverse_confidence'] * 0.3
                coord['overall_confidence'] = reverse_score * 100  # Pure reverse geocoding confidence
                
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
            
            # Determine recommended source (highest confidence)
            recommended_source = coordinates[0]['source'] if coordinates else None
            
            # Convert variance to user-friendly language
            variance = result.coordinate_variance or 0
            if variance < 0.001:
                accuracy_description = "Excellent precision - all sources agree closely"
            elif variance < 0.01:
                accuracy_description = "Good precision - sources mostly agree"
            elif variance < 0.1:
                accuracy_description = "Moderate precision - some variation between sources"
            else:
                accuracy_description = "Variable precision - significant differences between sources"
            
            # Enhanced location data
            locations_data.append({
                'id': result.id,
                'name': result.location_name,
                'confidence': confidence * 100,  # Convert to percentage for display
                'status': status,
                'coordinates': coordinates,
                'recommendation': recommendation,
                'analysis': analysis,
                'reverse_geocoding': reverse_geocoding,
                'recommended_source': recommended_source,
                'variance': variance,
                'accuracy_description': accuracy_description,
                'max_distance_km': analysis.get('max_distance_km', 0),
                'reverse_geocoding_score': analysis.get('reverse_geocoding_score', 0) * 100,
                'distance_confidence': analysis.get('distance_confidence', 0) * 100,
                'ai_summary': metadata.get('user_friendly_summary', 'Analysis in progress...')
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
    """Get enhanced validation statistics for the dashboard."""
    total_geocoding_results = GeocodingResult.objects.count()
    total_validations = ValidationResult.objects.count()
    
    # Calculate pending validation
    pending_validation = GeocodingResult.objects.filter(
        validation__isnull=True
    ).count() + ValidationResult.objects.filter(
        validation_status__in=['needs_review', 'pending']
    ).count()
    
    return {
        'total_locations': total_geocoding_results,
        'high_confidence': ValidationResult.objects.filter(confidence_score__gte=0.8).count(),
        'medium_confidence': ValidationResult.objects.filter(
            confidence_score__gte=0.6, confidence_score__lt=0.8
        ).count(),
        'low_confidence': ValidationResult.objects.filter(confidence_score__lt=0.6).count(),
        'pending_validation': pending_validation,
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
                
                locations_data.append({
                    'id': result.id,
                    'name': result.location_name,
                    'confidence': validation.confidence_score * 100,
                    'status': validation.validation_status,
                    'sources': sources
                })
            
            return JsonResponse({
                'success': True,
                'locations': locations_data
            })
            
        except Exception as e:
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
                    # Create validation if it doesn't exist using AI
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
            print(f"Validation API Error: {str(e)}")
            print(f"Traceback: {traceback.format_exc()}")
            return JsonResponse({
                'success': False,
                'error': f'An unexpected error occurred while processing your request: {str(e)}'
            }, status=500)
    
    return JsonResponse({'error': 'Only POST requests are allowed'}, status=405)


@csrf_exempt
def geocoding_api(request):
    """API endpoint for running geocoding from the interface with enhanced error handling."""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            action = data.get('action')
            
            if action == 'run_geocoding':
                limit = data.get('limit', 10)
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
                        'message': 'All location names already have coordinates',
                        'stats': {'processed': 0, 'successful': 0, 'failed': 0, 'used_validated': 0}
                    })
                
                # Process locations
                successful = 0
                failed = 0
                used_validated = 0
                
                for location in locations:
                    try:
                        # Check validated dataset first
                        validated_result = geocode_cmd.check_validated_dataset(location)
                        if validated_result:
                            with transaction.atomic():
                                location.latitude = validated_result.final_lat
                                location.longitude = validated_result.final_long
                                location.save()
                            used_validated += 1
                            successful += 1
                            continue
                        
                        # Check if results already exist
                        if not force:
                            existing_result = GeocodingResult.objects.filter(
                                location_name__iexact=location.name
                            ).first()
                            
                            if existing_result and existing_result.has_any_results:
                                successful += 1
                                continue
                        
                        # Perform geocoding
                        success = geocode_cmd.geocode_location(location)
                        if success:
                            successful += 1
                        else:
                            failed += 1
                            
                    except Exception as e:
                        print(f"Error geocoding {location.name}: {e}")
                        failed += 1
                
                return JsonResponse({
                    'success': True,
                    'message': f'Coordinate search completed: {successful} locations processed successfully, {failed} could not find coordinates',
                    'stats': {
                        'processed': successful + failed,
                        'successful': successful,
                        'failed': failed,
                        'used_validated': used_validated
                    }
                })
            
            elif action == 'get_geocoding_stats':
                # Get current statistics
                total_locations = Location.objects.count()
                geocoded_locations = Location.objects.filter(
                    latitude__isnull=False, 
                    longitude__isnull=False
                ).count()
                need_geocoding = total_locations - geocoded_locations
                
                geocoding_results = GeocodingResult.objects.count()
                
                # Pending validation calculation
                pending_validation = GeocodingResult.objects.filter(
                    validation__isnull=True
                ).count() + ValidationResult.objects.filter(
                    validation_status__in=['needs_review', 'pending']
                ).count()
                
                return JsonResponse({
                    'success': True,
                    'stats': {
                        'total_locations': total_locations,
                        'geocoded_locations': geocoded_locations,
                        'need_geocoding': need_geocoding,
                        'geocoding_results': geocoding_results,
                        'pending_validation': pending_validation,
                        'completion_rate': (geocoded_locations / total_locations * 100) if total_locations > 0 else 0
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
            print(f"Geocoding API Error: {str(e)}")
            print(f"Traceback: {traceback.format_exc()}")
            return JsonResponse({
                'success': False,
                'error': f'An unexpected error occurred: {str(e)}'
            }, status=500)
    
    return JsonResponse({'error': 'Only POST requests are allowed'}, status=405)


@csrf_exempt
def bulk_validation_actions(request):
    """Handle bulk validation actions with enhanced error handling."""
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
                        'error': 'No AI analysis has been performed yet. Please wait for AI analysis to complete first.'
                    }, status=400)
                
                # Check for high-confidence results
                high_confidence_results = ValidationResult.objects.filter(
                    confidence_score__gte=0.8,
                    validation_status='needs_review'
                )
                
                if not high_confidence_results.exists():
                    return JsonResponse({
                        'success': True,
                        'message': 'No high-confidence locations found that need auto-validation. All high-confidence locations may already be validated, or none meet the 80% confidence threshold.'
                    })
                
                count = 0
                errors = 0
                for validation in high_confidence_results:
                    try:
                        # Use the existing handle_approve_ai_suggestion function
                        result_response = handle_approve_ai_suggestion(validation, {})
                        result_data = json.loads(result_response.content.decode('utf-8'))
                        if result_data.get('success'):
                            count += 1
                        else:
                            errors += 1
                    except Exception as e:
                        print(f"Error auto-validating {validation.geocoding_result.location_name}: {e}")
                        errors += 1
                        continue
                
                if count > 0:
                    return JsonResponse({
                        'success': True,
                        'message': f'✅ Auto-validated {count} high confidence locations' + (f' ({errors} had errors)' if errors > 0 else '')
                    })
                else:
                    return JsonResponse({
                        'success': False,
                        'error': f'Failed to auto-validate any locations. {errors} errors occurred.'
                    })
            
            elif action == 'run_smart_validation_batch':
                # Check if any geocoding results exist
                total_geocoding_results = GeocodingResult.objects.count()
                if total_geocoding_results == 0:
                    return JsonResponse({
                        'success': False,
                        'error': 'No locations have been geocoded yet. Please run "Start Coordinate Search" first to find coordinates for your locations before running AI analysis.'
                    }, status=400)
                
                # Check if there are results to analyze
                pending_results = GeocodingResult.objects.filter(
                    validation__isnull=True
                ).exclude(validation_status='rejected')
                
                if not pending_results.exists():
                    return JsonResponse({
                        'success': True,
                        'message': 'All geocoded locations have already been analyzed by AI. No new analysis needed.'
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
                            print(f"🔍 Validating: {result.location_name}")
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
                        
                        except Exception as e:
                            print(f"Error validating {result.location_name}: {e}")
                            stats['rejected'] += 1
                            continue
                    
                    if stats['processed'] == 0:
                        return JsonResponse({
                            'success': True,
                            'message': 'No new locations to analyze. All locations have already been processed.'
                        })
                    
                    return JsonResponse({
                        'success': True,
                        'message': f'✅ AI analysis completed: processed {stats["processed"]} locations. {stats["auto_validated"]} auto-validated, {stats["needs_review"]} need review, {stats["pending"]} need manual verification.',
                        'stats': stats
                    })
                    
                except Exception as e:
                    print(f"Error running smart validation: {str(e)}")
                    print(f"Traceback: {traceback.format_exc()}")
                    return JsonResponse({
                        'success': False,
                        'error': f'AI analysis failed: {str(e)}'
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
            print(f"Bulk validation error: {str(e)}")
            print(f"Traceback: {traceback.format_exc()}")
            return JsonResponse({
                'success': False,
                'error': f'An unexpected error occurred during bulk operation: {str(e)}'
            }, status=500)
    
    return JsonResponse({'error': 'Only POST requests are allowed'}, status=405)


# Helper functions for validation actions
def handle_approve_ai_suggestion(validation, data):
    """Handle approval of AI suggestion with enhanced error handling."""
    try:
        # Get AI recommended source from metadata
        metadata = validation.validation_metadata or {}
        analysis = metadata.get('coordinates_analysis', {})
        recommended_source = analysis.get('recommended_source')
        
        if not recommended_source:
            return JsonResponse({
                'success': False,
                'error': 'No AI recommendation available for this location. Please run AI analysis first or select a source manually.'
            }, status=400)
        
        with transaction.atomic():
            result = validation.geocoding_result
            
            # Get coordinates from AI recommended source
            if recommended_source == 'hdx' and result.hdx_success:
                final_lat, final_lng = result.hdx_lat, result.hdx_lng
            elif recommended_source == 'arcgis' and result.arcgis_success:
                final_lat, final_lng = result.arcgis_lat, result.arcgis_lng
            elif recommended_source == 'google' and result.google_success:
                final_lat, final_lng = result.google_lat, result.google_lng
            elif recommended_source == 'nominatim' and result.nominatim_success:
                final_lat, final_lng = result.nominatim_lat, result.nominatim_lng
            else:
                return JsonResponse({
                    'success': False,
                    'error': f'The AI recommended source ({recommended_source}) does not have valid coordinates. Please select a different source manually.'
                }, status=400)
            
            # Update validation status
            validation.validation_status = 'validated'
            validation.validated_at = timezone.now()
            validation.validated_by = 'AI_Recommendation'
            validation.recommended_lat = final_lat
            validation.recommended_lng = final_lng
            validation.recommended_source = recommended_source
            validation.save()
            
            # Add to ValidationDataset (the "validated dataset")
            ValidationDataset.objects.update_or_create(
                location_name=result.location_name,
                defaults={
                    'final_lat': final_lat,
                    'final_long': final_lng,
                    'country': '',  # Add country if available
                    'source': f'ai_recommended_{recommended_source}'
                }
            )
            
            # Update the core Location model
            try:
                location = Location.objects.get(name__iexact=result.location_name)
                location.latitude = final_lat
                location.longitude = final_lng
                location.save()
                print(f"Updated core Location: {location.name} -> {final_lat}, {final_lng}")
            except Location.DoesNotExist:
                print(f"Warning: Could not find Location with name: {result.location_name}")
            except Location.MultipleObjectsReturned:
                location = Location.objects.filter(name__iexact=result.location_name).first()
                location.latitude = final_lat
                location.longitude = final_lng
                location.save()
                print(f"Updated first matching Location: {location.name} -> {final_lat}, {final_lng}")
            
            return JsonResponse({
                'success': True,
                'message': f'✅ AI recommendation accepted: {result.location_name} validated using {recommended_source.upper()} coordinates',
                'coordinates': {'lat': final_lat, 'lng': final_lng},
                'source': recommended_source
            })
    
    except Exception as e:
        print(f"Error approving AI suggestion: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'Failed to approve AI suggestion: {str(e)}'
        }, status=500)


def handle_use_source(validation, data):
    """Handle user selecting a specific source with enhanced error handling."""
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
            
            # Update validation
            validation.validation_status = 'validated'
            validation.validated_at = timezone.now()
            validation.validated_by = 'User_Selection'
            validation.manual_review_notes = f'User manually selected {source.upper()} coordinates'
            validation.recommended_lat = final_lat
            validation.recommended_lng = final_lng
            validation.recommended_source = source
            validation.save()
            
            # Add to ValidationDataset
            ValidationDataset.objects.update_or_create(
                location_name=result.location_name,
                defaults={
                    'final_lat': final_lat,
                    'final_long': final_lng,
                    'country': '',
                    'source': f'user_selected_{source}'
                }
            )
            
            # Update core Location model
            try:
                location = Location.objects.get(name__iexact=result.location_name)
                location.latitude = final_lat
                location.longitude = final_lng
                location.save()
            except Location.DoesNotExist:
                print(f"Warning: Could not find Location: {result.location_name}")
            except Location.MultipleObjectsReturned:
                location = Location.objects.filter(name__iexact=result.location_name).first()
                location.latitude = final_lat
                location.longitude = final_lng
                location.save()
            
            return JsonResponse({
                'success': True,
                'message': f'✅ Using {source.upper()} coordinates for {result.location_name}',
                'coordinates': {'lat': final_lat, 'lng': final_lng},
                'source': source
            })
    
    except Exception as e:
        print(f"Error using source: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'Failed to use selected source: {str(e)}'
        }, status=500)


def handle_manual_coordinates(validation, data):
    """Handle manual coordinate entry with enhanced validation."""
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
            
            # Update validation with manual coordinates
            validation.manual_lat = lat
            validation.manual_lng = lng
            validation.manual_review_notes = notes
            validation.validation_status = 'validated'
            validation.validated_at = timezone.now()
            validation.validated_by = 'Manual_Entry'
            validation.confidence_score = 1.0  # Manual entry gets highest confidence
            validation.save()
            
            # Add to ValidationDataset
            ValidationDataset.objects.update_or_create(
                location_name=result.location_name,
                defaults={
                    'final_lat': lat,
                    'final_long': lng,
                    'country': '',
                    'source': 'manual_entry'
                }
            )
            
            # Update core Location model
            try:
                location = Location.objects.get(name__iexact=result.location_name)
                location.latitude = lat
                location.longitude = lng
                location.save()
            except Location.DoesNotExist:
                print(f"Warning: Could not find Location: {result.location_name}")
            except Location.MultipleObjectsReturned:
                location = Location.objects.filter(name__iexact=result.location_name).first()
                location.latitude = lat
                location.longitude = lng
                location.save()
        
        return JsonResponse({
            'success': True,
            'message': f'✅ Manual coordinates saved for {result.location_name}',
            'coordinates': {'lat': lat, 'lng': lng},
            'source': 'manual'
        })
    
    except ValueError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid coordinate values. Please enter valid numbers for latitude and longitude.'
        }, status=400)
    except Exception as e:
        print(f"Error saving manual coordinates: {str(e)}")
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
        print(f"Error rejecting location: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'Failed to reject location: {str(e)}'
        }, status=500)


def get_enhanced_validation_details(validation):
    """Get detailed validation information with AI analysis."""
    try:
        result = validation.geocoding_result
        metadata = validation.validation_metadata or {}
        
        # Extract coordinate details with enhanced information
        coordinates = []
        sources = ['hdx', 'arcgis', 'google', 'nominatim']
        reverse_geocoding = metadata.get('reverse_geocoding_results', {})
        
        for source in sources:
            if getattr(result, f"{source}_success", False):
                lat = getattr(result, f"{source}_lat")
                lng = getattr(result, f"{source}_lng")
                
                # Get reverse geocoding info for this source
                reverse_info = reverse_geocoding.get(source, {})
                
                coordinates.append({
                    'source': source.upper(),
                    'lat': lat,
                    'lng': lng,
                    'maps_url': f"https://www.google.com/maps/@{lat},{lng},15z",
                    'reverse_address': reverse_info.get('address', 'Not available'),
                    'name_similarity': reverse_info.get('similarity_score', 0.0) * 100,
                    'reverse_confidence': reverse_info.get('confidence', 0.0) * 100,
                    'place_type': reverse_info.get('place_type', 'unknown')
                })
        
        # Extract analysis data
        analysis = metadata.get('coordinates_analysis', {})
        recommendation = metadata.get('recommendation', {})
        
        # Convert variance to user-friendly description
        variance = result.coordinate_variance or 0
        if variance < 0.001:
            accuracy_description = "Excellent precision - all sources agree closely"
            distance_quality = "excellent"
        elif variance < 0.01:
            accuracy_description = "Good precision - sources mostly agree"
            distance_quality = "good"
        elif variance < 0.1:
            accuracy_description = "Moderate precision - some variation between sources"
            distance_quality = "moderate"
        else:
            accuracy_description = "Variable precision - significant differences between sources"
            distance_quality = "poor"
        
        return JsonResponse({
            'success': True,
            'data': {
                'name': result.location_name,
                'confidence': validation.confidence_score * 100,
                'status': validation.validation_status,
                'coordinates': coordinates,
                'analysis': {
                    'recommended_source': analysis.get('recommended_source', 'Unknown'),
                    'reverse_geocoding_score': analysis.get('reverse_geocoding_score', 0) * 100,
                    'distance_confidence': analysis.get('distance_confidence', 0) * 100,
                    'max_distance_km': analysis.get('max_distance_km', 0),
                    'accuracy_level': analysis.get('accuracy_level', 'Unknown')
                },
                'recommendation': recommendation,
                'variance': variance,
                'accuracy_description': accuracy_description,
                'distance_quality': distance_quality,
                'ai_summary': metadata.get('user_friendly_summary', 'No AI analysis available'),
                'reverse_geocoding_results': reverse_geocoding
            }
        })
    
    except Exception as e:
        print(f"Error getting validation details: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'Failed to get validation details: {str(e)}'
        }, status=500)


def run_ai_analysis(validation):
    """Re-run AI analysis on a validation result with enhanced error handling."""
    try:
        validator = SmartGeocodingValidator()
        updated_validation = validator.validate_geocoding_result(validation.geocoding_result)
        
        return JsonResponse({
            'success': True,
            'message': '✅ AI analysis completed successfully',
            'confidence': updated_validation.confidence_score * 100,
            'status': updated_validation.validation_status
        })
    except Exception as e:
        print(f"Error running AI analysis: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'AI analysis failed: {str(e)}'
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
        print(f"Error getting validation statistics: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'Failed to get statistics: {str(e)}'
        }, status=500)