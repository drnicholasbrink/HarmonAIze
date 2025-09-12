# geolocation/validation.py
import requests
import time
import math
import json
from difflib import SequenceMatcher
from typing import Dict, List, Tuple, Optional
from django.db import transaction
from django.utils import timezone
from django.conf import settings
from .models import GeocodingResult, ValidationResult, ValidationDataset
from core.models import Location


class SmartGeocodingValidator:
    """Simplified two-component geocoding validation with reverse geocoding (70%) and distance proximity (30%)."""
    
    def __init__(self):
       
        self.confidence_thresholds = {
            'needs_review': 0.60,    # 60% threshold for review
            'manual_review': 0.40,   # 40% threshold for manual review
        }
        
        # Two-component weighting system
        self.confidence_weights = {
            'reverse_geocoding': 0.70,  # 70% - How well names match
            'distance_proximity': 0.30  # 30% - How close to other sources
        }
        
        # Local Nominatim configuration
        self.local_nominatim_url = getattr(settings, 'LOCAL_NOMINATIM_URL', 'http://nominatim:8080')
        self.public_nominatim_url = 'https://nominatim.openstreetmap.org'
    
    def validate_geocoding_result(self, geocoding_result: GeocodingResult) -> ValidationResult:
        """Main validation entry point with simplified two-component analysis."""
        
        # Extract coordinates from all sources
        coordinates = self._extract_coordinates(geocoding_result)
        
        if not coordinates:
            return self._create_validation_result(
                geocoding_result, 0.0, 'rejected', 
                "No successful geocoding results found"
            )
        
        # Perform enhanced reverse geocoding with fallback
        reverse_geocoding_results = self._perform_enhanced_reverse_geocoding(
            coordinates, geocoding_result.location_name
        )
        
        # Calculate individual source scores using simplified two-component system
        individual_scores = self._calculate_individual_source_scores(
            coordinates, 
            reverse_geocoding_results, 
            geocoding_result.location_name
        )
        
        # Find best source and calculate overall confidence
        best_source, best_score, overall_confidence = self._determine_best_source(individual_scores)
        
        # Determine status based on best individual source confidence
        if best_score >= self.confidence_thresholds['needs_review']:
            status = 'needs_review'  # High confidence but still needs user approval
        elif best_score >= self.confidence_thresholds['manual_review']:
            status = 'pending'       # Medium confidence needs review
        else:
            status = 'pending'       # Low confidence needs detailed investigation
        
        # Calculate cluster analysis for distance information
        cluster_analysis = self._calculate_cluster_analysis(coordinates)
        
        # Create enhanced metadata
        metadata = {
            'sources_count': len(coordinates),
            'individual_scores': individual_scores,
            'reverse_geocoding_results': reverse_geocoding_results,
            'best_source': best_source,
            'best_score': best_score,
            'cluster_analysis': cluster_analysis,
            'recommendation': self._generate_recommendation(best_source, best_score),
            'user_friendly_summary': self._generate_user_summary(best_score, len(coordinates)),
            'validation_method': 'two_component_simplified',
            'local_nominatim_used': any(
                result.get('local_nominatim_used', False) 
                for result in reverse_geocoding_results.values()
            )
        }
        
        # Update coordinate variance on the geocoding result
        geocoding_result.coordinate_variance = cluster_analysis.get('max_distance_km', 0)
        geocoding_result.save()
        
        # Create validation result using best individual source score as overall confidence
        return self._create_validation_result(
            geocoding_result, best_score, status,
            f"Two-component analysis: best source {best_source.upper()} - {best_score:.1%}",
            metadata
        )
    
    def _extract_coordinates(self, result: GeocodingResult) -> Dict[str, Tuple[float, float]]:
        """Extract all successful coordinates from geocoding result."""
        coordinates = {}
        
        sources = ['hdx', 'arcgis', 'google', 'nominatim']
        for source in sources:
            if getattr(result, f"{source}_success", False):
                lat = getattr(result, f"{source}_lat")
                lng = getattr(result, f"{source}_lng") 
                if lat is not None and lng is not None:
                    coordinates[source] = (lat, lng)
        
        return coordinates
    
    def _perform_enhanced_reverse_geocoding(self, coordinates: Dict[str, Tuple[float, float]], original_name: str) -> Dict:
        """Perform reverse geocoding using Nominatim (local first, public fallback) for ALL sources."""
        reverse_results = {}
        
        for source, (lat, lng) in coordinates.items():
            try:
                print(f"Reverse geocoding {source} coordinates using Nominatim (local→public fallback)...")
                
                # Use Nominatim (local first, public fallback) for ALL sources
                reverse_result = self._reverse_geocode_nominatim_with_fallback(lat, lng)
                
                if reverse_result and reverse_result.get('display_name'):
                    # Calculate similarity with original name
                    similarity = self._calculate_improved_name_similarity(
                        original_name, reverse_result.get('display_name', '')
                    )
                    
                    reverse_results[source] = {
                        'address': reverse_result.get('display_name', 'No address found'),
                        'similarity_score': similarity,
                        'place_type': reverse_result.get('type', 'unknown'),
                        'confidence': self._assess_reverse_geocoding_confidence(reverse_result, original_name),
                        'source_api': 'nominatim',  # Always Nominatim now
                        'original_source': source,  # Track which geocoding source provided the coordinates
                        'fallback_used': reverse_result.get('fallback_used', False),
                        'local_nominatim_used': reverse_result.get('local_nominatim_used', False)
                    }
                    
                    # Show which Nominatim was used
                    nominatim_type = "LOCAL" if reverse_result.get('local_nominatim_used') else "PUBLIC"
                    print(f"✓ {source.upper()} → Nominatim ({nominatim_type}) reverse geocoding successful")
                else:
                    reverse_results[source] = {
                        'address': 'No address found',
                        'similarity_score': 0.0,
                        'confidence': 0.0,
                        'source_api': 'nominatim',
                        'original_source': source,
                        'fallback_used': True,
                        'local_nominatim_used': False
                    }
                    print(f"✗ {source.upper()} → Nominatim reverse geocoding failed")
                
                # Be respectful to APIs
                time.sleep(0.3)
                
            except Exception as e:
                print(f"Reverse geocoding failed for {source}: {e}")
                reverse_results[source] = {
                    'address': f'Error: {str(e)}',
                    'similarity_score': 0.0,
                    'confidence': 0.0,
                    'source_api': 'nominatim',
                    'original_source': source,
                    'fallback_used': True,
                    'local_nominatim_used': False
                }
        
        return reverse_results
    
    def _reverse_geocode_nominatim_with_fallback(self, lat: float, lng: float) -> Optional[Dict]:
        """Try local Nominatim first, then fallback to public API if needed."""
        # First try local Nominatim instance
        result = self._reverse_geocode_nominatim_local(lat, lng)
        if result and result.get('display_name'):
            result['local_nominatim_used'] = True
            result['fallback_used'] = False
            return result
        
        # Fallback to public Nominatim
        result = self._reverse_geocode_nominatim_public(lat, lng)
        if result:
            result['local_nominatim_used'] = False
            result['fallback_used'] = True
        
        return result
    
    def _reverse_geocode_nominatim_local(self, lat: float, lng: float) -> Optional[Dict]:
        """Reverse geocode using local Nominatim instance."""
        try:
            url = f'{self.local_nominatim_url}/reverse'
            params = {
                'lat': lat,
                'lon': lng,
                'format': 'json',
                'addressdetails': 1,
                'zoom': 18
            }
            headers = {
                'User-Agent': 'HarmonAIze-Geocoder/1.0 (harmonaize@project.com)'
            }
            
            # Use shorter timeout for local instance
            response = requests.get(url, params=params, headers=headers, timeout=5)
            response.raise_for_status()
            data = response.json()
            
            return data if data else None
            
        except requests.exceptions.ConnectionError:
            return None
        except requests.exceptions.Timeout:
            return None
        except Exception as e:
            return None
    
    def _reverse_geocode_nominatim_public(self, lat: float, lng: float) -> Optional[Dict]:
        """Reverse geocode using public Nominatim API."""
        try:
            url = f'{self.public_nominatim_url}/reverse'
            params = {
                'lat': lat,
                'lon': lng,
                'format': 'json',
                'addressdetails': 1,
                'zoom': 18
            }
            headers = {
                'User-Agent': 'HarmonAIze-Geocoder/1.0 (harmonaize@project.com)'
            }
            
            response = requests.get(url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            return data if data else None
            
        except Exception as e:
            return None
    
    def _calculate_individual_source_scores(self, 
                                           coordinates: Dict[str, Tuple[float, float]], 
                                           reverse_results: Dict,
                                           original_name: str) -> Dict:
        """Calculate individual source scores using simplified two-component system."""
        individual_scores = {}
        
        for source, (lat, lng) in coordinates.items():
            # Component 1: Reverse Geocoding Score (70%)
            reverse_score = 0.0
            if source in reverse_results:
                reverse_score = reverse_results[source].get('similarity_score', 0.0)
            
            # Component 2: Distance Proximity Score (30%)
            distance_score = self._calculate_distance_proximity_score(
                source, coordinates
            )
            
            # Calculate individual confidence using weighted components
            individual_confidence = (
                reverse_score * self.confidence_weights['reverse_geocoding'] +
                distance_score * self.confidence_weights['distance_proximity']
            )
            
            individual_scores[source] = {
                'reverse_geocoding_score': reverse_score,
                'distance_penalty_score': distance_score,
                'individual_confidence': individual_confidence,
                'coordinates': (lat, lng)
            }
            
            # Show if local Nominatim was used for this source
            nominatim_info = ""
            if source in reverse_results:
                local_used = reverse_results[source].get('local_nominatim_used', False)
                nominatim_info = f" (Nominatim: {'LOCAL' if local_used else 'PUBLIC'})"
            
            print(f"Source {source}: Reverse={reverse_score:.2f}, Distance={distance_score:.2f}, Individual={individual_confidence:.2f}{nominatim_info}")
        
        return individual_scores
    
    def _calculate_distance_proximity_score(self, target_source: str, coordinates: Dict[str, Tuple[float, float]]) -> float:
        """Calculate distance proximity score for a source based on how close it is to other sources."""
        if len(coordinates) <= 1:
            return 0.8  # Single source gets good score
        
        target_coords = coordinates[target_source]
        distances = []
        
        for source, coords in coordinates.items():
            if source != target_source:
                distance_km = self._calculate_distance_km(target_coords, coords)
                distances.append(distance_km)
        
        if not distances:
            return 0.8
        
        # Use average distance to other sources
        avg_distance_km = sum(distances) / len(distances)
        
        # Score based on average distance: <0.5km=1.0, 0.5-1km=0.9, 1-2km=0.7, 2-5km=0.5, >5km=0.2
        if avg_distance_km < 0.5:
            return 1.0
        elif avg_distance_km < 1.0:
            return 0.9
        elif avg_distance_km < 2.0:
            return 0.7
        elif avg_distance_km < 5.0:
            return 0.5
        else:
            return 0.2
    
    def _determine_best_source(self, individual_scores: Dict) -> Tuple[str, float, float]:
        """Determine the best source based on individual confidence scores."""
        if not individual_scores:
            return None, 0.0, 0.0
        
        # Find source with highest individual confidence
        best_source = max(individual_scores.keys(), 
                         key=lambda k: individual_scores[k]['individual_confidence'])
        best_score = individual_scores[best_source]['individual_confidence']
        
        # Overall confidence is the best individual source score
        overall_confidence = best_score
        
        return best_source, best_score, overall_confidence
    
    def _calculate_cluster_analysis(self, coordinates: Dict[str, Tuple[float, float]]) -> Dict:
        """Calculate cluster analysis for distance information."""
        if len(coordinates) <= 1:
            return {
                'max_distance_km': 0.0,
                'avg_distance_km': 0.0,
                'source_count': len(coordinates)
            }
        
        coord_list = list(coordinates.values())
        distances_km = []
        
        for i in range(len(coord_list)):
            for j in range(i + 1, len(coord_list)):
                dist_km = self._calculate_distance_km(coord_list[i], coord_list[j])
                distances_km.append(dist_km)
        
        max_distance_km = max(distances_km) if distances_km else 0
        avg_distance_km = sum(distances_km) / len(distances_km) if distances_km else 0
        
        return {
            'max_distance_km': max_distance_km,
            'avg_distance_km': avg_distance_km,
            'source_count': len(coordinates)
        }
    
    def _calculate_distance_km(self, coord1: Tuple[float, float], coord2: Tuple[float, float]) -> float:
        """Calculate distance between two coordinates in kilometers using Haversine formula."""
        lat1, lon1 = coord1
        lat2, lon2 = coord2
        
        lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
        
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        c = 2 * math.asin(math.sqrt(a))
        
        return c * 6371  # Earth's radius in kilometers
    
    def _calculate_improved_name_similarity(self, location_name: str, full_address: str) -> float:
        """Enhanced similarity calculation for location names vs full addresses."""
        if not location_name or not full_address:
            return 0.0
        
        # Clean and normalize both strings
        location_clean = self._clean_text(location_name)
        address_clean = self._clean_text(full_address)
        
        # Strategy 1: Full containment check (highest score)
        if location_clean.lower() in address_clean.lower():
            return 0.95  # Full containment gets 95%
        
        # Strategy 2: Partial containment check (high score)
        if self._partial_containment_check(location_clean, address_clean):
            return 0.80  # Partial containment gets 80%
        
        # Strategy 3: Token-based matching (medium score)
        location_tokens = set(location_clean.lower().split())
        address_tokens = set(address_clean.lower().split())
        
        if location_tokens and address_tokens:
            common_tokens = location_tokens.intersection(address_tokens)
            token_ratio = len(common_tokens) / len(location_tokens)
            if token_ratio >= 0.7:  # 70% of tokens match
                return 0.65  # Token matching gets 65%
            elif token_ratio >= 0.5:  # 50% of tokens match
                return 0.45  # Partial token matching gets 45%
        
        # Strategy 4: Facility-specific matching (medium score)
        facility_score = self._calculate_facility_specific_similarity(location_name, full_address)
        if facility_score > 0:
            return facility_score
        
        # Strategy 5: Traditional sequence matching (low score)
        sequence_score = SequenceMatcher(None, location_clean.lower(), address_clean.lower()).ratio()
        if sequence_score >= 0.6:
            return sequence_score * 0.5  # Cap at 50% for sequence matching
        
        # No meaningful match found
        return 0.0
    
    def _clean_text(self, text: str) -> str:
        """Clean text for better matching."""
        import re
        text = re.sub(r'[^\w\s]', ' ', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
    
    def _partial_containment_check(self, location_name: str, address: str) -> bool:
        """Check if most words in location name appear in address."""
        location_words = location_name.lower().split()
        address_lower = address.lower()
        
        if len(location_words) == 0:
            return False
        
        matching_words = sum(1 for word in location_words if word in address_lower)
        match_ratio = matching_words / len(location_words)
        
        return match_ratio >= 0.7
    
    def _calculate_facility_specific_similarity(self, location_name: str, address: str) -> float:
        """Enhanced facility name matching."""
        location_lower = location_name.lower()
        address_lower = address.lower()
        
        facility_keywords = ['hospital', 'clinic', 'health', 'center', 'centre', 'medical', 'facility']
        is_facility = any(keyword in location_lower for keyword in facility_keywords)
        
        if not is_facility:
            return 0.0
        
        core_name = self._extract_facility_core_name(location_name)
        if core_name and core_name.lower() in address_lower:
            return 0.75
        
        return 0.0
    
    def _extract_facility_core_name(self, facility_name: str) -> str:
        """Extract core name from facility."""
        facility_words = ['hospital', 'clinic', 'health', 'center', 'centre', 'medical', 'facility', 'general', 'district']
        words = facility_name.lower().split()
        core_words = [word for word in words if word not in facility_words]
        return ' '.join(core_words).strip()
    
    def _assess_reverse_geocoding_confidence(self, reverse_result: Dict, original_name: str) -> float:
        """Assess confidence based on reverse geocoding result."""
        if not reverse_result:
            return 0.0
        
        confidence = 0.5
        place_type = reverse_result.get('type', '').lower()
        address = reverse_result.get('display_name', '').lower()
        
        if any(keyword in place_type for keyword in ['hospital', 'clinic', 'medical', 'health']):
            confidence += 0.3
        if any(keyword in address for keyword in ['hospital', 'clinic', 'medical', 'health']):
            confidence += 0.2
        
        return min(confidence, 1.0)
    
    def _generate_recommendation(self, best_source: str, best_score: float) -> Dict:
        """Generate user-friendly recommendations."""
        if best_score >= 0.8:
            return {
                'action': 'suggest_approval',
                'message': f"Excellent confidence result. Recommend using {best_source.upper()} coordinates.",
                'reasoning': "High reverse geocoding match and good distance proximity."
            }
        elif best_score >= 0.6:
            return {
                'action': 'suggest_approval',
                'message': f"Good confidence result. Suggest using {best_source.upper()} coordinates.",
                'reasoning': "Acceptable reverse geocoding match and distance proximity."
            }
        else:
            return {
                'action': 'review_required',
                'message': "Manual review recommended due to low confidence scores.",
                'reasoning': "Poor reverse geocoding match or distance proximity issues."
            }
    
    def _generate_user_summary(self, best_score: float, source_count: int) -> str:
        """Generate user-friendly summary of the analysis."""
        if best_score >= 0.8:
            return f"Excellent validation! Best source shows {best_score:.0%} confidence from {source_count} sources."
        elif best_score >= 0.6:
            return f"Good validation with {best_score:.0%} confidence from {source_count} sources."
        else:
            return f"Low confidence ({best_score:.0%}) from {source_count} sources - manual verification recommended."
    
    def _create_validation_result(self, geocoding_result: GeocodingResult, confidence: float,
                                status: str, reason: str, metadata: Optional[Dict] = None) -> ValidationResult:
        """Create a ValidationResult object."""
        
        # Extract best source information from metadata
        best_source = metadata.get('best_source', '') if metadata else ''
        individual_scores = metadata.get('individual_scores', {}) if metadata else {}
        
        # Get recommended coordinates from best source
        recommended_lat = None
        recommended_lng = None
        if best_source and individual_scores.get(best_source):
            coords = individual_scores[best_source].get('coordinates')
            if coords:
                recommended_lat, recommended_lng = coords
        
        validation_result, created = ValidationResult.objects.update_or_create(
            geocoding_result=geocoding_result,
            defaults={
                'confidence_score': confidence,
                'validation_status': status,
                'validation_metadata': metadata or {'reason': reason},
                'reverse_geocoding_score': confidence,  # Use overall confidence as reverse geocoding score
                'api_agreement_score': confidence,      # Use overall confidence as agreement score
                'distance_confidence': confidence,      # Use overall confidence as distance confidence
                'recommended_source': best_source,
                'recommended_lat': recommended_lat,
                'recommended_lng': recommended_lng,
            }
        )
        
        return validation_result


def run_smart_validation(limit: int = None) -> Dict[str, int]:
    """Run smart validation on pending geocoding results."""
    validator = SmartGeocodingValidator()
    
    pending_results = GeocodingResult.objects.filter(
        validation__isnull=True
    ).exclude(validation_status='rejected')
    
    if limit:
        pending_results = pending_results[:limit]
    
    stats = {
        'processed': 0,
        'auto_validated': 0,
        'needs_review': 0,
        'pending': 0,
        'rejected': 0
    }
    
    for result in pending_results:
        try:
            validation = validator.validate_geocoding_result(result)
            stats['processed'] += 1
            
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
    
    return stats