# geolocation/validation.py
import requests
import time
import math
import json
from difflib import SequenceMatcher
from typing import Dict, List, Tuple, Optional
from django.db import transaction
from django.utils import timezone
from .models import GeocodingResult, ValidationResult, ValidationDataset
from core.models import Location


class SmartGeocodingValidator:
    """Enhanced AI-assisted geocoding validation with multi-factor confidence scoring."""
    
    def __init__(self):
        # New confidence thresholds for enhanced scoring
        self.confidence_thresholds = {
            'suggest_review': 0.70,    # Suggest with human confirmation
            'manual_review': 0.40,     # Flag for detailed investigation
        }
        
        # Enhanced confidence weightings (Option B)
        self.confidence_weights = {
            'reverse_geocoding': 0.40,  # 40% - How well names match
            'distance_agreement': 0.25, # 25% - How well sources agree on location
            'population_density': 0.20, # 20% - Is it in inhabited area
            'road_proximity': 0.15      # 15% - Is it accessible by road
        }
    
    def validate_geocoding_result(self, geocoding_result: GeocodingResult) -> ValidationResult:
        """Main validation entry point with enhanced multi-factor analysis."""
        
        # Extract coordinates from all sources
        coordinates = self._extract_coordinates(geocoding_result)
        
        if not coordinates:
            return self._create_validation_result(
                geocoding_result, 0.0, 'rejected', 
                "No successful geocoding results found"
            )
        
        # Perform source-specific reverse geocoding
        reverse_geocoding_results = self._perform_source_specific_reverse_geocoding(
            coordinates, geocoding_result.location_name
        )
        
        # Calculate enhanced multi-factor confidence score
        analysis = self._analyze_coordinates_with_enhanced_factors(
            coordinates, 
            reverse_geocoding_results, 
            geocoding_result.location_name
        )
        confidence = analysis['confidence']
        
        # Determine status based on confidence - NO AUTO-VALIDATION
        if confidence >= self.confidence_thresholds['suggest_review']:
            status = 'needs_review'  # High confidence but still needs user approval
        elif confidence >= self.confidence_thresholds['manual_review']:
            status = 'needs_review'  # Medium confidence needs review
        else:
            status = 'pending'       # Low confidence needs detailed investigation
        
        # Create enhanced metadata
        metadata = {
            'sources_count': len(coordinates),
            'coordinates_analysis': analysis,
            'reverse_geocoding_results': reverse_geocoding_results,
            'recommendation': self._generate_recommendation(coordinates, analysis),
            'user_friendly_summary': self._generate_user_summary(analysis),
            'confidence_breakdown': analysis.get('confidence_breakdown', {}),
            'validation_flags': analysis.get('flags', [])
        }
        
        # Update coordinate variance on the geocoding result
        geocoding_result.coordinate_variance = analysis.get('variance', 0)
        geocoding_result.save()
        
        # Create validation result
        return self._create_validation_result(
            geocoding_result, confidence, status,
            f"Enhanced analysis: {len(coordinates)} sources - {analysis['accuracy_level']}",
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
    
    def _perform_source_specific_reverse_geocoding(self, coordinates: Dict[str, Tuple[float, float]], original_name: str) -> Dict:
        """Perform source-specific reverse geocoding for fairness."""
        reverse_results = {}
        
        for source, (lat, lng) in coordinates.items():
            try:
                print(f"Reverse geocoding {source} coordinates...")
                
                # Use source-specific reverse geocoding
                if source == 'google':
                    reverse_result = self._reverse_geocode_google(lat, lng)
                elif source == 'arcgis':
                    reverse_result = self._reverse_geocode_arcgis(lat, lng)
                elif source in ['nominatim', 'hdx']:  # HDX uses OSM data
                    reverse_result = self._reverse_geocode_nominatim(lat, lng)
                else:
                    reverse_result = self._reverse_geocode_nominatim(lat, lng)  # Fallback
                
                if reverse_result:
                    # Calculate similarity with original name
                    similarity = self._calculate_improved_name_similarity(
                        original_name, reverse_result.get('display_name', '')
                    )
                    
                    reverse_results[source] = {
                        'address': reverse_result.get('display_name', 'No address found'),
                        'similarity_score': similarity,
                        'place_type': reverse_result.get('type', 'unknown'),
                        'confidence': self._assess_reverse_geocoding_confidence(reverse_result, original_name),
                        'administrative_levels': self._extract_administrative_levels(reverse_result),
                        'source_api': source
                    }
                else:
                    reverse_results[source] = {
                        'address': 'No address found',
                        'similarity_score': 0.0,
                        'confidence': 0.0,
                        'administrative_levels': {},
                        'source_api': source
                    }
                
                # Be respectful to APIs
                time.sleep(0.4)
                
            except Exception as e:
                print(f"Reverse geocoding failed for {source}: {e}")
                reverse_results[source] = {
                    'address': f'Error: {str(e)}',
                    'similarity_score': 0.0,
                    'confidence': 0.0,
                    'administrative_levels': {},
                    'source_api': source
                }
        
        return reverse_results
    
    def _reverse_geocode_google(self, lat: float, lng: float) -> Optional[Dict]:
        """Reverse geocode using Google Maps API."""
        try:
            import os
            from django.conf import settings
            
            key = getattr(settings, "GOOGLE_GEOCODING_API_KEY", None) or os.getenv("GOOGLE_GEOCODING_API_KEY")
            if not key:
                print("Google API key not available for reverse geocoding")
                return None
            
            url = "https://maps.googleapis.com/maps/api/geocode/json"
            params = {
                "latlng": f"{lat},{lng}",
                "key": key,
                "result_type": "establishment|point_of_interest|premise"
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data["status"] == "OK" and data["results"]:
                result = data["results"][0]
                return {
                    'display_name': result.get('formatted_address', ''),
                    'type': result.get('types', [None])[0] if result.get('types') else 'unknown',
                    'address_components': result.get('address_components', [])
                }
            
            return None
            
        except Exception as e:
            print(f"Google reverse geocoding error: {e}")
            return None
    
    def _reverse_geocode_arcgis(self, lat: float, lng: float) -> Optional[Dict]:
        """Reverse geocode using ArcGIS API."""
        try:
            url = 'https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/reverseGeocode'
            params = {
                'f': 'json',
                'location': f"{lng},{lat}",  # ArcGIS uses lng,lat
                'distance': 1000,
                'outSR': 4326
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if 'address' in data and data['address']:
                address_info = data['address']
                return {
                    'display_name': address_info.get('LongLabel', address_info.get('Match_addr', '')),
                    'type': address_info.get('Type', 'unknown'),
                    'address_components': address_info
                }
            
            return None
            
        except Exception as e:
            print(f"ArcGIS reverse geocoding error: {e}")
            return None
    
    def _reverse_geocode_nominatim(self, lat: float, lng: float) -> Optional[Dict]:
        """Reverse geocode using Nominatim."""
        try:
            url = 'https://nominatim.openstreetmap.org/reverse'
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
            print(f"Nominatim reverse geocoding error: {e}")
            return None
    
    def _extract_administrative_levels(self, reverse_result: Dict) -> Dict:
        """Extract full administrative hierarchy from reverse geocoding result."""
        admin_levels = {}
        
        if not reverse_result:
            return admin_levels
        
        # Handle different API response formats
        if 'address_components' in reverse_result:  # Google
            for component in reverse_result['address_components']:
                types = component.get('types', [])
                if 'country' in types:
                    admin_levels['country'] = component['long_name']
                elif 'administrative_area_level_1' in types:
                    admin_levels['state_province'] = component['long_name']
                elif 'administrative_area_level_2' in types:
                    admin_levels['district'] = component['long_name']
                elif 'locality' in types:
                    admin_levels['city'] = component['long_name']
        
        elif 'address' in reverse_result and isinstance(reverse_result['address'], dict):  # Nominatim
            address = reverse_result['address']
            admin_levels.update({
                'country': address.get('country', ''),
                'state_province': address.get('state', ''),
                'district': address.get('county', address.get('state_district', '')),
                'city': address.get('city', address.get('town', address.get('village', '')))
            })
        
        elif isinstance(reverse_result, dict) and 'address_components' in reverse_result:  # ArcGIS address_components
            address_components = reverse_result['address_components']
            admin_levels.update({
                'country': address_components.get('Country', ''),
                'state_province': address_components.get('Region', ''),
                'district': address_components.get('Subregion', ''),
                'city': address_components.get('City', '')
            })
        
        return {k: v for k, v in admin_levels.items() if v}  # Remove empty values
    
    def _get_population_density(self, lat: float, lng: float) -> float:
        """Get population density using WorldPop REST API (binary check)."""
        try:
            # WorldPop REST API endpoint
            url = "https://api.worldpop.org/v1/wopr/pointtable"
            params = {
                'iso3': 'auto',  # Auto-detect country
                'lat': lat,
                'lon': lng
            }
            
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            # Check if there's any population data
            if data and 'data' in data and data['data']:
                population_value = data['data'].get('pop', 0)
                
                # Binary scoring: 1.0 if any population, 0.0 if none
                return 1.0 if population_value > 0 else 0.0
            
            return 0.0  # No data or zero population
            
        except Exception as e:
            print(f"WorldPop API error: {e}")
            # If API fails, assume populated area (fail-safe)
            return 1.0
    
    def _get_road_proximity(self, lat: float, lng: float) -> float:
        """Get road proximity using Overpass API."""
        try:
            # Overpass API query for nearest road within 10km
            overpass_url = "http://overpass-api.de/api/interpreter"
            query = f"""
            [out:json][timeout:25];
            (
              way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential)$"]
                (around:10000,{lat},{lng});
            );
            out geom;
            """
            
            response = requests.post(overpass_url, data=query, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            if not data.get('elements'):
                return 0.0  # No roads found within 10km
            
            # Calculate distance to nearest road
            min_distance_km = float('inf')
            
            for element in data['elements']:
                if 'geometry' in element:
                    for node in element['geometry']:
                        node_lat, node_lng = node['lat'], node['lon']
                        distance_km = self._calculate_distance_km((lat, lng), (node_lat, node_lng))
                        min_distance_km = min(min_distance_km, distance_km)
            
            # Score based on distance: <0.5km=1.0, 0.5-2km=0.7, 2-5km=0.4, >5km=0.0
            if min_distance_km < 0.5:
                return 1.0
            elif min_distance_km < 2.0:
                return 0.7
            elif min_distance_km < 5.0:
                return 0.4
            else:
                return 0.0
                
        except Exception as e:
            print(f"Overpass API error: {e}")
            # If API fails, assume road accessible (fail-safe)
            return 0.7
    
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
    
    def _analyze_coordinates_with_enhanced_factors(self, 
                                                  coordinates: Dict[str, Tuple[float, float]], 
                                                  reverse_results: Dict,
                                                  original_name: str) -> Dict:
        """Enhanced coordinate analysis with multi-factor confidence scoring."""
        if not coordinates:
            return {
                'confidence': 0.0,
                'variance': 0.0,
                'accuracy_level': 'No data',
                'recommended_source': None,
                'recommended_coords': None,
                'confidence_breakdown': {},
                'flags': []
            }
        
        # Calculate individual confidence factors
        factors = self._calculate_all_confidence_factors(coordinates, reverse_results, original_name)
        
        # Calculate weighted overall confidence
        overall_confidence = (
            factors['reverse_geocoding'] * self.confidence_weights['reverse_geocoding'] +
            factors['distance_agreement'] * self.confidence_weights['distance_agreement'] +
            factors['population_density'] * self.confidence_weights['population_density'] +
            factors['road_proximity'] * self.confidence_weights['road_proximity']
        )
        
        # Select best coordinate based on reverse geocoding score
        best_source, best_coords = self._select_best_coordinate_by_reverse_geocoding(
            coordinates, 
            {source: reverse_results[source]['similarity_score'] for source in coordinates.keys() if source in reverse_results}
        )
        
        # Generate validation flags
        flags = self._generate_validation_flags(factors)
        
        return {
            'confidence': overall_confidence,
            'variance': factors.get('avg_distance_km', 0),
            'max_distance_km': factors.get('max_distance_km', 0),
            'accuracy_level': self._determine_accuracy_level(overall_confidence, factors.get('max_distance_km', 0)),
            'recommended_source': best_source,
            'recommended_coords': best_coords,
            'confidence_breakdown': {
                'reverse_geocoding': factors['reverse_geocoding'],
                'distance_agreement': factors['distance_agreement'],
                'population_density': factors['population_density'],
                'road_proximity': factors['road_proximity'],
                'weighted_contributions': {
                    'reverse_geocoding': factors['reverse_geocoding'] * self.confidence_weights['reverse_geocoding'],
                    'distance_agreement': factors['distance_agreement'] * self.confidence_weights['distance_agreement'],
                    'population_density': factors['population_density'] * self.confidence_weights['population_density'],
                    'road_proximity': factors['road_proximity'] * self.confidence_weights['road_proximity']
                }
            },
            'flags': flags,
            'cluster_analysis': {
                'source_count': len(coordinates),
                'avg_distance_km': factors.get('avg_distance_km', 0),
                'max_distance_km': factors.get('max_distance_km', 0)
            }
        }
    
    def _calculate_all_confidence_factors(self, coordinates: Dict, reverse_results: Dict, original_name: str) -> Dict:
        """Calculate all confidence factors."""
        factors = {}
        
        # 1. Reverse Geocoding Factor (40%)
        reverse_scores = []
        for source in coordinates.keys():
            if source in reverse_results:
                reverse_scores.append(reverse_results[source]['similarity_score'])
        factors['reverse_geocoding'] = max(reverse_scores) if reverse_scores else 0.0
        
        # 2. Distance Agreement Factor (25%)
        if len(coordinates) == 1:
            factors['distance_agreement'] = 0.8  # Single source gets decent score
            factors['avg_distance_km'] = 0
            factors['max_distance_km'] = 0
        else:
            coord_list = list(coordinates.values())
            distances_km = []
            for i in range(len(coord_list)):
                for j in range(i + 1, len(coord_list)):
                    dist_km = self._calculate_distance_km(coord_list[i], coord_list[j])
                    distances_km.append(dist_km)
            
            max_distance_km = max(distances_km) if distances_km else 0
            avg_distance_km = sum(distances_km) / len(distances_km) if distances_km else 0
            
            factors['max_distance_km'] = max_distance_km
            factors['avg_distance_km'] = avg_distance_km
            factors['distance_agreement'] = self._calculate_distance_confidence(max_distance_km)
        
        # 3. Population Density Factor (20%) - Binary check
        # Use the recommended coordinates or first available
        if coordinates:
            sample_coords = list(coordinates.values())[0]
            factors['population_density'] = self._get_population_density(sample_coords[0], sample_coords[1])
        else:
            factors['population_density'] = 0.0
        
        # 4. Road Proximity Factor (15%)
        if coordinates:
            sample_coords = list(coordinates.values())[0]
            factors['road_proximity'] = self._get_road_proximity(sample_coords[0], sample_coords[1])
        else:
            factors['road_proximity'] = 0.0
        
        return factors
    
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
    
    def _calculate_distance_confidence(self, max_distance_km: float) -> float:
        """Calculate distance confidence based on maximum distance between sources."""
        if max_distance_km <= 0.5:
            return 0.95  # Excellent agreement
        elif max_distance_km <= 1.0:
            return 0.90  # Very good agreement
        elif max_distance_km <= 2.0:
            return 0.75  # Good agreement
        elif max_distance_km <= 5.0:
            # Linear decrease from 0.75 to 0.0
            return 0.75 * (5.0 - max_distance_km) / 3.0
        else:
            return 0.0  # Poor agreement
    
    def _select_best_coordinate_by_reverse_geocoding(self, 
                                                    coordinates: Dict[str, Tuple[float, float]], 
                                                    reverse_scores: Dict[str, float]) -> Tuple[str, Tuple[float, float]]:
        """Select best coordinate based on reverse geocoding score."""
        if len(coordinates) == 1:
            source = list(coordinates.keys())[0]
            return source, coordinates[source]
        
        if reverse_scores:
            best_source = max(reverse_scores, key=reverse_scores.get)
            return best_source, coordinates[best_source]
        
        # Fallback to first source
        first_source = list(coordinates.keys())[0]
        return first_source, coordinates[first_source]
    
    def _generate_validation_flags(self, factors: Dict) -> List[str]:
        """Generate validation flags based on confidence factors."""
        flags = []
        
        if factors.get('population_density', 0) == 0.0:
            flags.append("⚠️ Location appears to be in unpopulated area (water/desert)")
        
        if factors.get('road_proximity', 0) <= 0.4:
            flags.append("⚠️ Location is >2km from nearest road")
        
        if factors.get('distance_agreement', 0) <= 0.3:
            flags.append("⚠️ Large disagreement between geocoding sources")
        
        if factors.get('reverse_geocoding', 0) <= 0.3:
            flags.append("⚠️ Poor name matching in reverse geocoding")
        
        return flags
    
    def _generate_recommendation(self, coordinates: Dict, analysis: Dict) -> Dict:
        """Generate user-friendly recommendations."""
        confidence = analysis['confidence']
        
        if confidence >= 0.9:
            return {
                'action': 'suggest_approval',
                'message': f"Excellent confidence result. Recommend using {analysis['recommended_source'].upper()} coordinates.",
                'reasoning': "All validation factors show strong agreement and accuracy."
            }
        elif confidence >= 0.7:
            return {
                'action': 'suggest_approval',
                'message': f"Good confidence result. Suggest using {analysis['recommended_source'].upper()} coordinates.",
                'reasoning': "Most validation factors show good agreement with acceptable accuracy."
            }
        elif confidence >= 0.5:
            return {
                'action': 'review_required',
                'message': "Manual review recommended due to moderate confidence.",
                'reasoning': "Some validation factors show concerns that require human judgment."
            }
        else:
            return {
                'action': 'detailed_review',
                'message': "Detailed investigation required - significant issues detected.",
                'reasoning': "Multiple validation factors indicate potential accuracy problems."
            }
    
    def _generate_user_summary(self, analysis: Dict) -> str:
        """Generate user-friendly summary of the analysis."""
        confidence = analysis['confidence']
        breakdown = analysis.get('confidence_breakdown', {})
        
        reverse_score = breakdown.get('reverse_geocoding', 0)
        distance_score = breakdown.get('distance_agreement', 0)
        population_score = breakdown.get('population_density', 0)
        road_score = breakdown.get('road_proximity', 0)
        
        if confidence >= 0.9:
            return f"Excellent validation! Strong scores across all factors (Name: {reverse_score:.0%}, Distance: {distance_score:.0%}, Population: {'✓' if population_score > 0 else '✗'}, Roads: {road_score:.0%})."
        elif confidence >= 0.7:
            return f"Good validation with acceptable scores (Name: {reverse_score:.0%}, Distance: {distance_score:.0%}, Population: {'✓' if population_score > 0 else '✗'}, Roads: {road_score:.0%})."
        elif confidence >= 0.5:
            return f"Moderate confidence - review recommended (Name: {reverse_score:.0%}, Distance: {distance_score:.0%}, Population: {'✓' if population_score > 0 else '✗'}, Roads: {road_score:.0%})."
        else:
            return f"Low confidence - manual verification needed (Name: {reverse_score:.0%}, Distance: {distance_score:.0%}, Population: {'✓' if population_score > 0 else '✗'}, Roads: {road_score:.0%})."
    
    def _determine_accuracy_level(self, confidence: float, max_distance_km: float) -> str:
        """Determine user-friendly accuracy level."""
        if confidence >= 0.9:
            return "Excellent - all validation factors show strong agreement"
        elif confidence >= 0.8:
            return "Very Good - most validation factors show good agreement"
        elif confidence >= 0.7:
            return "Good - acceptable validation with minor concerns"
        elif confidence >= 0.6:
            return "Moderate - some validation concerns, review recommended"
        else:
            return "Low - multiple validation issues detected, manual review required"
    
    def _auto_add_to_validated_dataset(self, geocoding_result: GeocodingResult, coords: Tuple[float, float], source: str):
        """Add to validated dataset (ONLY when user explicitly approves)."""
        try:
            # Add to ValidationDataset
            ValidationDataset.objects.update_or_create(
                location_name=geocoding_result.location_name,
                defaults={
                    'final_lat': coords[0],
                    'final_long': coords[1],
                    'country': '',
                    'source': source
                }
            )
            
            # Update the core Location model
            try:
                location = Location.objects.get(name__iexact=geocoding_result.location_name)
                location.latitude = coords[0]
                location.longitude = coords[1]
                location.save()
                print(f"Updated Location: {location.name} -> {coords[0]}, {coords[1]}")
            except Location.DoesNotExist:
                print(f"Warning: Could not find Location: {geocoding_result.location_name}")
            except Location.MultipleObjectsReturned:
                location = Location.objects.filter(name__iexact=geocoding_result.location_name).first()
                location.latitude = coords[0]
                location.longitude = coords[1]
                location.save()
                print(f"Updated first matching Location: {location.name} -> {coords[0]}, {coords[1]}")
                
        except Exception as e:
            print(f"Error adding to validated dataset: {e}")
    
    def _create_validation_result(self, geocoding_result: GeocodingResult, confidence: float,
                                status: str, reason: str, metadata: Optional[Dict] = None) -> ValidationResult:
        """Create a ValidationResult object."""
        
        analysis = metadata.get('coordinates_analysis', {}) if metadata else {}
        breakdown = analysis.get('confidence_breakdown', {})
        
        validation_result, created = ValidationResult.objects.update_or_create(
            geocoding_result=geocoding_result,
            defaults={
                'confidence_score': confidence,
                'validation_status': status,
                'validation_metadata': metadata or {'reason': reason},
                'reverse_geocoding_score': breakdown.get('reverse_geocoding'),
                'api_agreement_score': breakdown.get('distance_agreement'),
                'distance_confidence': breakdown.get('distance_agreement'),
                'recommended_source': analysis.get('recommended_source', ''),
                'recommended_lat': analysis.get('recommended_coords', [None, None])[0],
                'recommended_lng': analysis.get('recommended_coords', [None, None])[1],
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


def process_location_batch(batch_size: int = 50) -> Dict[str, int]:
    """Process a batch of locations through the complete geocoding and validation pipeline."""
    from geolocation.management.commands.geocode_locations import Command as GeocodeCommand
    
    stats = {
        'locations_processed': 0,
        'geocoding_successful': 0,
        'validations_created': 0,
        'auto_validated': 0,
        'needs_review': 0,
        'manual_review': 0
    }
    
    # Get locations without coordinates
    ungeocoded_locations = Location.objects.filter(
        latitude__isnull=True, 
        longitude__isnull=True
    )[:batch_size]
    
    if not ungeocoded_locations.exists():
        return stats
    
    stats['locations_processed'] = ungeocoded_locations.count()
    
    # Step 1: Geocode locations
    geocode_cmd = GeocodeCommand()
    for location in ungeocoded_locations:
        # Check validated dataset first
        validated_result = geocode_cmd.check_validated_dataset(location)
        if validated_result:
            with transaction.atomic():
                location.latitude = validated_result.final_lat
                location.longitude = validated_result.final_long
                location.save()
            stats['geocoding_successful'] += 1
            stats['auto_validated'] += 1
            continue
        
        # Geocode using all sources
        success = geocode_cmd.geocode_location_enhanced(location)
        if success:
            stats['geocoding_successful'] += 1
    
    # Step 2: Run validation on new geocoding results
    validation_stats = run_smart_validation(limit=batch_size)
    stats.update({
        'validations_created': validation_stats['processed'],
        'auto_validated': stats['auto_validated'] + validation_stats['auto_validated'],
        'needs_review': validation_stats['needs_review'], 
        'manual_review': validation_stats['pending']
    })
    
    return stats