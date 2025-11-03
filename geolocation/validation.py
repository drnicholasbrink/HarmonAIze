# geolocation/validation.py
"""
Enhanced geocoding validation with LLM-powered improvements:
1. Intelligent source conflict resolution
2. Semantic reverse geocoding validation
3. Contextual sanity checks
4. Natural language explanations
"""
import logging
import requests
import time
import math
import json
from difflib import SequenceMatcher
from typing import Dict, List, Tuple, Optional
from django.db import transaction
from django.utils import timezone
from django.conf import settings
from .models import GeocodingResult, ValidationResult, ValidatedDataset
from core.models import Location
from .llm_enhancement import get_llm_enhancer

logger = logging.getLogger(__name__)


class SmartGeocodingValidator:
    """Enhanced geocoding validation with LLM-powered improvements."""

    def __init__(self):
        self.confidence_thresholds = {
            'needs_review': 0.60,
            'manual_review': 0.40,
        }

        self.confidence_weights = {
            'reverse_geocoding': 0.70,
            'distance_proximity': 0.30
        }

        self.local_nominatim_url = getattr(settings, 'LOCAL_NOMINATIM_URL', 'http://nominatim:8080')
        self.public_nominatim_url = 'https://nominatim.openstreetmap.org'

        self.llm_enhancer = get_llm_enhancer()
        if self.llm_enhancer.is_enabled():
            logger.info("✓ SmartGeocodingValidator initialized with LLM enhancements")
    
    def validate_geocoding_result(self, geocoding_result: GeocodingResult) -> ValidationResult:
        """
        Main validation entry point with simplified two-component analysis.
        
        Validates geocoding results using a 2-component approach:
        - 70% weight: Reverse geocoding name similarity analysis  
        - 30% weight: Distance proximity clustering between sources
        
        Args:
            geocoding_result: GeocodingResult instance with coordinates from multiple APIs
            
        Returns:
            ValidationResult: Analysis with confidence score, recommended coordinates,
                            and validation status (validated/needs_review/rejected)
                            
        Raises:
            Exception: If validation analysis fails due to API errors or invalid data
        """

        coordinates = self._extract_coordinates(geocoding_result)

        if not coordinates:
            return self._create_validation_result(
                geocoding_result, 0.0, 'rejected',
                "No successful geocoding results found"
            )

        reverse_geocoding_results = self._perform_enhanced_reverse_geocoding_multi_source(
            coordinates, geocoding_result.location_name
        )

        parsed_location = geocoding_result.parsed_location_data or {}
        bounds_validation = self._validate_coordinates_dynamically(coordinates, parsed_location)

        individual_scores = self._calculate_individual_source_scores(
            coordinates,
            reverse_geocoding_results,
            geocoding_result.location_name
        )

        best_source, best_score, overall_confidence = self._determine_best_source(individual_scores)

        cluster_analysis = self._calculate_cluster_analysis(coordinates)

        llm_conflict_resolution = None
        if self.llm_enhancer.is_enabled() and cluster_analysis.get('max_distance_km', 0) > 5.0:
            llm_conflict_resolution = self.llm_enhancer.resolve_source_conflict(
                location_name=geocoding_result.location_name,
                coordinates=coordinates,
                reverse_geocoding_results=reverse_geocoding_results,
                parsed_location=parsed_location
            )

            if llm_conflict_resolution and llm_conflict_resolution['confidence'] >= 0.7:
                best_source = llm_conflict_resolution['recommended_source'].lower()
                best_score = max(best_score, llm_conflict_resolution['confidence'] * 0.9)

        llm_sanity_check = None
        if self.llm_enhancer.is_enabled():
            llm_sanity_check = self.llm_enhancer.contextual_sanity_check(
                location_name=geocoding_result.location_name,
                coordinates=coordinates,
                parsed_location=parsed_location,
                reverse_geocoding_results=reverse_geocoding_results
            )

            if llm_sanity_check and not llm_sanity_check['passes_sanity_check']:
                severity = llm_sanity_check.get('severity', 'minor')
                if severity == 'critical':
                    best_score = best_score * 0.3
                elif severity == 'major':
                    best_score = best_score * 0.5
                elif severity == 'minor':
                    best_score = best_score * 0.8

        if bounds_validation.get('any_outside_bounds'):
            best_score = best_score * 0.8
        if bounds_validation.get('outliers'):
            best_score = best_score * 0.9

        if best_score >= self.confidence_thresholds['needs_review']:
            status = 'needs_review'
        elif best_score >= self.confidence_thresholds['manual_review']:
            status = 'pending'
        else:
            status = 'pending'

        # Create enhanced metadata with LLM analysis
        metadata = {
            'sources_count': len(coordinates),
            'individual_scores': individual_scores,
            'reverse_geocoding_results': reverse_geocoding_results,
            'bounds_validation': bounds_validation,
            'parsed_location': parsed_location,
            'best_source': best_source,
            'best_score': best_score,
            'cluster_analysis': cluster_analysis,
            'recommendation': self._generate_recommendation(best_source, best_score),
            'user_friendly_summary': self._generate_user_summary(best_score, len(coordinates)),
            'validation_method': 'two_component_with_llm' if self.llm_enhancer.is_enabled() else 'two_component_simplified',
            'local_nominatim_used': any(
                result.get('local_nominatim_used', False)
                for result in reverse_geocoding_results.values()
            ),
            # LLM enhancements
            'llm_conflict_resolution': llm_conflict_resolution,
            'llm_sanity_check': llm_sanity_check,
            'llm_enhanced': self.llm_enhancer.is_enabled()
        }

        geocoding_result.coordinate_variance = cluster_analysis.get('max_distance_km', 0)
        geocoding_result.save()

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
                reverse_result = self._reverse_geocode_nominatim_with_fallback(lat, lng)

                if reverse_result and reverse_result.get('display_name'):
                    similarity = self._calculate_improved_name_similarity(
                        original_name, reverse_result.get('display_name', '')
                    )

                    llm_similarity = None
                    if self.llm_enhancer.is_enabled():
                        llm_similarity = self.llm_enhancer.semantic_address_similarity(
                            query_name=original_name,
                            reverse_address=reverse_result.get('display_name', '')
                        )

                        if llm_similarity and llm_similarity['similarity_score'] > similarity:
                            similarity = llm_similarity['similarity_score']

                    reverse_results[source] = {
                        'address': reverse_result.get('display_name', 'No address found'),
                        'similarity_score': similarity,
                        'place_type': reverse_result.get('type', 'unknown'),
                        'confidence': self._assess_reverse_geocoding_confidence(reverse_result, original_name),
                        'source_api': 'nominatim',
                        'original_source': source,
                        'fallback_used': reverse_result.get('fallback_used', False),
                        'local_nominatim_used': reverse_result.get('local_nominatim_used', False),
                        'llm_similarity': llm_similarity
                    }

                    nominatim_type = "LOCAL" if reverse_result.get('local_nominatim_used') else "PUBLIC"
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
                
                # Be respectful to APIs
                time.sleep(0.3)
                
            except Exception as e:
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
        result = self._reverse_geocode_nominatim_local(lat, lng)
        if result and result.get('display_name'):
            result['local_nominatim_used'] = True
            result['fallback_used'] = False
            return result

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

    def _reverse_geocode_google(self, lat: float, lng: float) -> Optional[Dict]:
        """
        Reverse geocode using Google Maps Geocoding API.

        Google has excellent POI (Point of Interest) coverage, especially for
        health facilities, hospitals, and landmarks.
        """
        try:
            import os
            key = getattr(settings, "GOOGLE_GEOCODING_API_KEY", None) or os.getenv("GOOGLE_GEOCODING_API_KEY")
            if not key:
                return None

            url = "https://maps.googleapis.com/maps/api/geocode/json"
            params = {
                "latlng": f"{lat},{lng}",
                "key": key
            }

            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            if data["status"] == "OK" and data["results"]:
                result = data["results"][0]
                return {
                    'formatted_address': result.get('formatted_address', ''),
                    'address_components': result.get('address_components', []),
                    'types': result.get('types', []),
                    'place_id': result.get('place_id', '')
                }

        except Exception as e:
            logger.debug(f"Google reverse geocoding failed: {e}")

        return None

    def _reverse_geocode_arcgis(self, lat: float, lng: float) -> Optional[Dict]:
        """
        Reverse geocode using ArcGIS API.

        ArcGIS is good for infrastructure and administrative boundaries.
        """
        try:
            url = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/reverseGeocode"
            params = {
                "f": "json",
                "location": f"{lng},{lat}",  # ArcGIS uses lng,lat order
                "outSR": 4326
            }

            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            if data.get("address"):
                return {
                    'address': data['address'].get('Match_addr', data['address'].get('LongLabel', '')),
                    'type': data['address'].get('Type', 'unknown'),
                    'city': data['address'].get('City', ''),
                    'region': data['address'].get('Region', ''),
                    'country': data['address'].get('CountryCode', '')
                }

        except Exception as e:
            logger.debug(f"ArcGIS reverse geocoding failed: {e}")

        return None

    def _perform_enhanced_reverse_geocoding_multi_source(self,
                                                          coordinates: Dict[str, Tuple[float, float]],
                                                          original_name: str) -> Dict:
        """
        Perform reverse geocoding using MULTIPLE sources with fallback strategy.

        Strategy:
            1. Try Google Reverse Geocoding (best for POIs/facilities)
            2. Try ArcGIS Reverse Geocoding (good for infrastructure)
            3. Fallback to Nominatim (always available)

        For each coordinate source, tries multiple reverse geocoding APIs and
        uses the best match.

        Args:
            coordinates: Dict mapping source names to (lat, lng) tuples
            original_name: Original location name for similarity comparison

        Returns:
            Dict mapping source names to reverse geocoding results with best match selected
        """
        reverse_results = {}

        for source, (lat, lng) in coordinates.items():
            source_reverse_results = []

            google_reverse = self._reverse_geocode_google(lat, lng)
            if google_reverse and google_reverse.get('formatted_address'):
                try:
                    from fuzzywuzzy import fuzz
                    similarity = fuzz.token_set_ratio(
                        original_name,
                        google_reverse['formatted_address']
                    ) / 100.0
                except ImportError:
                    similarity = 0.5 if original_name.lower() in google_reverse['formatted_address'].lower() else 0.3

                source_reverse_results.append({
                    'api': 'google',
                    'address': google_reverse['formatted_address'],
                    'similarity_score': similarity,
                    'place_type': google_reverse.get('types', ['unknown'])[0] if google_reverse.get('types') else 'unknown',
                    'confidence': 0.8,
                    'components': google_reverse.get('address_components', [])
                })

            arcgis_reverse = self._reverse_geocode_arcgis(lat, lng)
            if arcgis_reverse and arcgis_reverse.get('address'):
                try:
                    from fuzzywuzzy import fuzz
                    similarity = fuzz.token_set_ratio(
                        original_name,
                        arcgis_reverse['address']
                    ) / 100.0
                except ImportError:
                    similarity = 0.5 if original_name.lower() in arcgis_reverse['address'].lower() else 0.3

                source_reverse_results.append({
                    'api': 'arcgis',
                    'address': arcgis_reverse['address'],
                    'similarity_score': similarity,
                    'place_type': arcgis_reverse.get('type', 'unknown'),
                    'confidence': 0.7,
                    'components': arcgis_reverse
                })

            nominatim_reverse = self._reverse_geocode_nominatim_with_fallback(lat, lng)
            if nominatim_reverse and nominatim_reverse.get('display_name'):
                try:
                    from fuzzywuzzy import fuzz
                    similarity = fuzz.token_set_ratio(
                        original_name,
                        nominatim_reverse['display_name']
                    ) / 100.0
                except ImportError:
                    similarity = 0.5 if original_name.lower() in nominatim_reverse['display_name'].lower() else 0.3

                source_reverse_results.append({
                    'api': 'nominatim',
                    'address': nominatim_reverse['display_name'],
                    'similarity_score': similarity,
                    'place_type': nominatim_reverse.get('type', 'unknown'),
                    'confidence': 0.6,
                    'local_used': nominatim_reverse.get('local_nominatim_used', False)
                })

            if source_reverse_results:
                # Sort by similarity score (descending)
                source_reverse_results.sort(key=lambda x: x['similarity_score'], reverse=True)
                best_result = source_reverse_results[0]

                reverse_results[source] = {
                    'best_match': best_result,
                    'all_attempts': source_reverse_results,
                    'num_successful': len(source_reverse_results),
                    **best_result
                }
            else:
                reverse_results[source] = {
                    'api': 'none',
                    'address': 'No address found',
                    'similarity_score': 0.0,
                    'confidence': 0.0,
                    'all_attempts': [],
                    'num_successful': 0
                }

            time.sleep(0.3)

        return reverse_results

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
            
        
        return individual_scores
    
    def _calculate_distance_proximity_score(self, target_source: str, coordinates: Dict[str, Tuple[float, float]]) -> float:
        """
        Calculate distance proximity score based on the CLOSEST other source (minimum distance).

        This approach correctly handles outliers:
        - Sources in a tight cluster have close neighbors → HIGH scores
        - Outliers have no close neighbors → LOW scores
        - No averaging or centroid needed - simple and effective!
        """
        if len(coordinates) <= 1:
            return 0.8  # Single source gets good score

        target_coords = coordinates[target_source]

        # Find the MINIMUM distance to any other source
        min_distance = float('inf')
        for source, coords in coordinates.items():
            if source != target_source:
                distance_km = self._calculate_distance_km(target_coords, coords)
                min_distance = min(min_distance, distance_km)

        if min_distance == float('inf'):
            return 0.8

        # Score based on distance to CLOSEST neighbor
        # Close neighbors = in agreement = high score
        # No close neighbors = outlier = low score
        if min_distance < 0.5:
            return 1.0  # Very close agreement (within 500m)
        elif min_distance < 1.0:
            return 0.9  # Close agreement (within 1km)
        elif min_distance < 2.0:
            return 0.8  # Good agreement (within 2km)
        elif min_distance < 5.0:
            return 0.6  # Moderate agreement (within 5km)
        elif min_distance < 10.0:
            return 0.4  # Weak agreement (within 10km)
        else:
            return 0.2  # Poor agreement (far from all sources)
    
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
        """
        Enhanced similarity calculation using fuzzy matching (works globally).

        Uses fuzzywuzzy library for robust string matching that handles:
        - Word order differences
        - Extra words
        - Typos and abbreviations
        - Partial matches

        Args:
            location_name: Original location name (e.g., "parirenyatwa hospital")
            full_address: Reverse geocoded address (e.g., "Parirenyatwa General Hospital, Harare, Zimbabwe")

        Returns:
            float: Similarity score between 0.0 and 1.0
        """
        if not location_name or not full_address:
            return 0.0

        # Import fuzzywuzzy (try-except for graceful fallback)
        try:
            from fuzzywuzzy import fuzz
            FUZZY_AVAILABLE = True
        except ImportError:
            FUZZY_AVAILABLE = False

        # Clean and normalize both strings
        location_clean = self._clean_text(location_name)
        address_clean = self._clean_text(full_address)

        if FUZZY_AVAILABLE:
            # FUZZY MATCHING STRATEGIES (using fuzzywuzzy)

            # Strategy 1: Token Sort Ratio (handles word order)
            # "hospital parirenyatwa" vs "parirenyatwa hospital" = 100% match
            token_sort = fuzz.token_sort_ratio(location_clean, address_clean) / 100.0

            # Strategy 2: Token Set Ratio (handles extra words)
            # "parirenyatwa hospital" vs "parirenyatwa general hospital harare zimbabwe" = high match
            token_set = fuzz.token_set_ratio(location_clean, address_clean) / 100.0

            # Strategy 3: Partial Ratio (handles substrings and abbreviations)
            # "st mary hospital" vs "saint mary's hospital and clinic" = high match
            partial = fuzz.partial_ratio(location_clean, address_clean) / 100.0

            # Strategy 4: Simple Ratio (baseline character-by-character)
            simple = fuzz.ratio(location_clean, address_clean) / 100.0

            # Take weighted average of best scores
            scores = sorted([token_sort, token_set, partial, simple], reverse=True)

            # Weight: Best score 50%, second best 30%, third 20%
            final_score = (scores[0] * 0.5) + (scores[1] * 0.3) + (scores[2] * 0.2)

            # Bonus: Exact substring match (case-insensitive)
            if location_clean.lower() in address_clean.lower():
                final_score = min(final_score + 0.05, 1.0)

            return final_score

        else:
            # FALLBACK: Original token-based matching if fuzzywuzzy not available

            # Strategy 1: Full containment check (highest score)
            if location_clean.lower() in address_clean.lower():
                return 0.95

            # Strategy 2: Partial containment check (high score)
            if self._partial_containment_check(location_clean, address_clean):
                return 0.80

            # Strategy 3: Token-based matching (medium score)
            location_tokens = set(location_clean.lower().split())
            address_tokens = set(address_clean.lower().split())

            if location_tokens and address_tokens:
                common_tokens = location_tokens.intersection(address_tokens)
                token_ratio = len(common_tokens) / len(location_tokens)
                if token_ratio >= 0.7:
                    return 0.65
                elif token_ratio >= 0.5:
                    return 0.45

            # Strategy 4: Facility-specific matching
            facility_score = self._calculate_facility_specific_similarity(location_name, full_address)
            if facility_score > 0:
                return facility_score

            # Strategy 5: Sequence matching (fallback)
            sequence_score = SequenceMatcher(None, location_clean.lower(), address_clean.lower()).ratio()
            if sequence_score >= 0.6:
                return sequence_score * 0.5

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
        
        facility_keywords = ['hospital', 'clinic', 'health', 'centre', 'medical', 'facility']
        is_facility = any(keyword in location_lower for keyword in facility_keywords)
        
        if not is_facility:
            return 0.0
        
        core_name = self._extract_facility_core_name(location_name)
        if core_name and core_name.lower() in address_lower:
            return 0.75
        
        return 0.0
    
    def _extract_facility_core_name(self, facility_name: str) -> str:
        """Extract core name from facility."""
        facility_words = ['hospital', 'clinic', 'health', 'centre', 'medical', 'facility', 'general', 'district']
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

    def _validate_coordinates_dynamically(self,
                                          coordinates: Dict[str, Tuple[float, float]],
                                          parsed_location: Dict) -> Dict:
        """
        Dynamically validate coordinates using multiple strategies.

        NO hardcoded bounding boxes - generates bounds from API responses or calculates
        from coordinate spread.

        Strategies:
            1. Calculate coordinate statistics (centroid, spread)
            2. Identify outliers (coordinates far from cluster)
            3. If country known, fetch bounds dynamically from Nominatim
            4. Generate confidence adjustment based on spread

        Args:
            coordinates: Dict mapping source names to (lat, lng) tuples
            parsed_location: Parsed location data with country info

        Returns:
            Dict with validation results including outliers, bounds check, and confidence
        """
        import statistics

        lats = [coord[0] for coord in coordinates.values()]
        lngs = [coord[1] for coord in coordinates.values()]

        centroid_lat = sum(lats) / len(lats)
        centroid_lng = sum(lngs) / len(lngs)

        lat_std = statistics.stdev(lats) if len(lats) > 1 else 0
        lng_std = statistics.stdev(lngs) if len(lngs) > 1 else 0

        outliers = {}
        for source, (lat, lng) in coordinates.items():
            distance_from_centroid = self._calculate_distance_km(
                (centroid_lat, centroid_lng),
                (lat, lng)
            )

            if distance_from_centroid > 50 or \
               (lat_std > 0 and abs(lat - centroid_lat) > 3 * lat_std) or \
               (lng_std > 0 and abs(lng - centroid_lng) > 3 * lng_std):
                outliers[source] = {
                    'coordinates': (lat, lng),
                    'distance_from_centroid_km': distance_from_centroid,
                    'flag': 'potential_outlier'
                }

        country_validation = None
        if parsed_location and parsed_location.get('country_code'):
            country_validation = self._validate_against_country_bounds_dynamic(
                coordinates,
                parsed_location['country_code']
            )

        max_distance = max([
            self._calculate_distance_km((centroid_lat, centroid_lng), coord)
            for coord in coordinates.values()
        ]) if coordinates else 0

        if max_distance < 1:
            spread_confidence = 1.0
        elif max_distance < 5:
            spread_confidence = 0.9
        elif max_distance < 10:
            spread_confidence = 0.7
        else:
            spread_confidence = 0.5

        return {
            'centroid': (centroid_lat, centroid_lng),
            'spread_km': max_distance,
            'outliers': outliers,
            'country_validation': country_validation,
            'confidence_adjustment': spread_confidence,
            'lat_std': lat_std,
            'lng_std': lng_std
        }

    def _validate_against_country_bounds_dynamic(self,
                                                  coordinates: Dict[str, Tuple[float, float]],
                                                  country_code: str) -> Dict:
        """
        Dynamically fetch country bounds using Nominatim API.

        NO hardcoded bounding boxes - queries OSM for country polygon on-the-fly.

        Args:
            coordinates: Dict of coordinates to validate
            country_code: ISO 2-letter country code

        Returns:
            Dict with bounds validation results
        """
        try:
            # Query Nominatim for country boundary
            url = f'{self.public_nominatim_url}/search'
            params = {
                'country': country_code,
                'format': 'json',
                'polygon_geojson': 0,  # Don't need full polygon, just bbox
                'limit': 1
            }
            headers = {'User-Agent': 'HarmonAIze-Geocoder/1.0'}

            response = requests.get(url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()

            if data and len(data) > 0:
                # Extract bounding box from response
                bbox = data[0].get('boundingbox')  # [min_lat, max_lat, min_lon, max_lon]

                if bbox:
                    min_lat, max_lat, min_lon, max_lon = map(float, bbox)

                    # Check each coordinate
                    results = {}
                    for source, (lat, lng) in coordinates.items():
                        in_bounds = (min_lat <= lat <= max_lat and
                                    min_lon <= lng <= max_lon)

                        results[source] = {
                            'in_country_bounds': in_bounds,
                            'bounds': {
                                'min_lat': min_lat,
                                'max_lat': max_lat,
                                'min_lon': min_lon,
                                'max_lon': max_lon
                            }
                        }

                    return {
                        'country_code': country_code,
                        'bounds_available': True,
                        'results': results,
                        'any_outside_bounds': any(not r['in_country_bounds']
                                                 for r in results.values())
                    }

        except Exception as e:
            logger.debug(f"Could not fetch country bounds for {country_code}: {e}")

        return {
            'country_code': country_code,
            'bounds_available': False,
            'results': {},
            'any_outside_bounds': False
        }

    def _create_validation_result(self, geocoding_result: GeocodingResult, confidence: float,
                                status: str, reason: str, metadata: Optional[Dict] = None) -> ValidationResult:
        """Create a ValidationResult object with optional LLM-generated explanation."""

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
                'reverse_geocoding_score': confidence,
                'api_agreement_score': confidence,
                'distance_confidence': confidence,
                'recommended_source': best_source,
                'recommended_lat': recommended_lat,
                'recommended_lng': recommended_lng,
            }
        )

        if self.llm_enhancer.is_enabled():
            try:
                explanation = self.llm_enhancer.explain_validation_detailed(
                    validation_result,
                    include_technical=False
                )

                if metadata:
                    metadata['llm_explanation'] = explanation
                    validation_result.validation_metadata = metadata
                    validation_result.save()

            except Exception as e:
                logger.warning(f"Failed to generate LLM explanation: {e}")

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
            stats['rejected'] += 1
            continue
    
    return stats