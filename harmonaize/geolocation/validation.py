# geolocation/validation.py
import requests
import time
import math
from difflib import SequenceMatcher
from typing import Dict, List, Tuple, Optional
from django.db import transaction
from django.utils import timezone
from .models import GeocodingResult, ValidationResult, ValidationDataset
from core.models import Location


class SmartGeocodingValidator:
    """AI-assisted geocoding validation logic with enhanced similarity matching."""
    
    def __init__(self):
        self.confidence_thresholds = {
            'auto_validate': 0.85,     # Auto-validate high confidence
            'suggest_review': 0.65,    # Suggest with human confirmation
            'manual_review': 0.40,     # Flag for detailed investigation
        }
        
        # NO SOURCE WEIGHTS - treat all sources equally
        # Validation based on coordinate agreement and reverse geocoding quality
    
    def validate_geocoding_result(self, geocoding_result: GeocodingResult) -> ValidationResult:
        """Main validation entry point with enhanced analysis including reverse geocoding."""
        
        # Extract coordinates from all sources
        coordinates = self._extract_coordinates(geocoding_result)
        
        if not coordinates:
            return self._create_validation_result(
                geocoding_result, 0.0, 'rejected', 
                "No successful geocoding results found"
            )
        
        # Perform reverse geocoding analysis for each coordinate
        reverse_geocoding_results = self._perform_reverse_geocoding(coordinates, geocoding_result.location_name)
        
        # Calculate enhanced confidence score and analysis
        analysis = self._analyze_coordinates_with_reverse_geocoding(
            coordinates, 
            reverse_geocoding_results, 
            geocoding_result.location_name
        )
        confidence = analysis['confidence']
        
        # Determine status based on confidence
        if confidence >= self.confidence_thresholds['auto_validate']:
            status = 'validated'
            # Auto-add to validated dataset if confidence is very high
            if confidence >= 0.9:
                self._auto_add_to_validated_dataset(
                    geocoding_result, 
                    analysis['recommended_coords'], 
                    analysis['recommended_source']
                )
        elif confidence >= self.confidence_thresholds['suggest_review']:
            status = 'needs_review'
        else:
            status = 'pending'
        
        # Create enhanced metadata
        metadata = {
            'sources_count': len(coordinates),
            'coordinates_analysis': analysis,
            'reverse_geocoding_results': reverse_geocoding_results,
            'recommendation': self._generate_recommendation(coordinates, analysis),
            'user_friendly_summary': self._generate_user_summary(analysis)
        }
        
        # Update coordinate variance on the geocoding result
        geocoding_result.coordinate_variance = analysis['variance']
        geocoding_result.save()
        
        # Create validation result
        return self._create_validation_result(
            geocoding_result, confidence, status,
            f"Analyzed {len(coordinates)} sources - {analysis['accuracy_level']}",
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
    
    def _perform_reverse_geocoding(self, coordinates: Dict[str, Tuple[float, float]], original_name: str) -> Dict:
        """Perform reverse geocoding for each coordinate to validate location names."""
        reverse_results = {}
        
        for source, (lat, lng) in coordinates.items():
            try:
                # Use Nominatim for reverse geocoding (free and reliable)
                reverse_result = self._reverse_geocode_nominatim(lat, lng)
                if reverse_result:
                    # Calculate similarity with original name using IMPROVED logic
                    similarity = self._calculate_improved_name_similarity(original_name, reverse_result['display_name'])
                    reverse_results[source] = {
                        'address': reverse_result['display_name'],
                        'similarity_score': similarity,
                        'place_type': reverse_result.get('type', 'unknown'),
                        'confidence': self._assess_reverse_geocoding_confidence(reverse_result, original_name)
                    }
                else:
                    reverse_results[source] = {
                        'address': 'No address found',
                        'similarity_score': 0.0,
                        'confidence': 0.0
                    }
                
                # Be respectful to the API
                time.sleep(0.3)
                
            except Exception as e:
                print(f"Reverse geocoding failed for {source}: {e}")
                reverse_results[source] = {
                    'address': f'Error: {str(e)}',
                    'similarity_score': 0.0,
                    'confidence': 0.0
                }
        
        return reverse_results
    
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
    
    def _calculate_improved_name_similarity(self, location_name: str, full_address: str) -> float:
        """
        IMPROVED similarity calculation for location names vs full addresses.
        For "Parirenyatwa Hospital" vs "Parirenyatwa Hospital, East Road, Avondale, Harare"
        this should return ~90-95% instead of 32%.
        """
        if not location_name or not full_address:
            return 0.0
        
        # Clean and normalize both strings
        location_clean = self._clean_text(location_name)
        address_clean = self._clean_text(full_address)
        
        # Strategy 1: Direct containment check (most important for location names)
        if location_clean.lower() in address_clean.lower():
            containment_score = 0.95
        elif self._partial_containment_check(location_clean, address_clean):
            containment_score = 0.85
        else:
            containment_score = 0.0
        
        # Strategy 2: Token-based matching (for partial matches)
        location_tokens = set(location_clean.lower().split())
        address_tokens = set(address_clean.lower().split())
        
        if location_tokens and address_tokens:
            common_tokens = location_tokens.intersection(address_tokens)
            token_score = len(common_tokens) / len(location_tokens)
        else:
            token_score = 0.0
        
        # Strategy 3: Traditional sequence matching (as fallback)
        sequence_score = SequenceMatcher(None, location_clean.lower(), address_clean.lower()).ratio()
        
        # Strategy 4: Hospital/facility specific matching
        facility_score = self._calculate_facility_specific_similarity(location_name, full_address)
        
        # Combine scores with weights - prioritize containment
        final_score = max(
            containment_score,      # Highest priority
            facility_score,         # Good for medical facilities
            token_score * 0.8,      # Token matching
            sequence_score * 0.6    # Traditional similarity (lowest priority)
        )
        
        return min(final_score, 1.0)
    
    def _clean_text(self, text: str) -> str:
        """Clean text for better matching."""
        import re
        # Remove extra punctuation and normalize spaces
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
        """Enhanced facility name matching (hospitals, clinics, etc)."""
        location_lower = location_name.lower()
        address_lower = address.lower()
        
        # Common facility keywords
        facility_keywords = ['hospital', 'clinic', 'health', 'center', 'centre', 'medical', 'facility']
        
        # Check if this is a medical facility
        is_facility = any(keyword in location_lower for keyword in facility_keywords)
        
        if not is_facility:
            return 0.0
        
        # Extract the core facility name (remove common words)
        core_name = self._extract_facility_core_name(location_name)
        
        if core_name and core_name.lower() in address_lower:
            return 0.9
        
        return 0.0
    
    def _extract_facility_core_name(self, facility_name: str) -> str:
        """Extract core name from facility (e.g., 'Parirenyatwa' from 'Parirenyatwa Hospital')."""
        facility_words = ['hospital', 'clinic', 'health', 'center', 'centre', 'medical', 'facility', 'general', 'district']
        
        words = facility_name.lower().split()
        core_words = [word for word in words if word not in facility_words]
        
        return ' '.join(core_words).strip()
    
    def _assess_reverse_geocoding_confidence(self, reverse_result: Dict, original_name: str) -> float:
        """Assess confidence based on reverse geocoding result."""
        if not reverse_result:
            return 0.0
        
        confidence = 0.5  # Base confidence
        
        # Boost confidence if it's a medical facility
        place_type = reverse_result.get('type', '').lower()
        if any(keyword in place_type for keyword in ['hospital', 'clinic', 'medical', 'health']):
            confidence += 0.3
        
        # Boost confidence if address contains hospital-related terms
        address = reverse_result.get('display_name', '').lower()
        if any(keyword in address for keyword in ['hospital', 'clinic', 'medical', 'health']):
            confidence += 0.2
        
        return min(confidence, 1.0)
    
    def _analyze_coordinates_with_reverse_geocoding(self, 
                                                   coordinates: Dict[str, Tuple[float, float]], 
                                                   reverse_results: Dict,
                                                   original_name: str) -> Dict:
        """Enhanced coordinate analysis with NO SOURCE WEIGHTS - equal treatment."""
        if not coordinates:
            return {
                'confidence': 0.0,
                'variance': 0.0,
                'accuracy_level': 'No data',
                'recommended_source': None,
                'recommended_coords': None,
                'reverse_geocoding_score': 0.0,
                'cluster_analysis': {}
            }
        
        if len(coordinates) == 1:
            source = list(coordinates.keys())[0]
            coords = list(coordinates.values())[0]
            reverse_score = 0.0
            if source in reverse_results:
                reverse_score = (
                    reverse_results[source]['similarity_score'] * 0.7 + 
                    reverse_results[source]['confidence'] * 0.3
                )
            
            # NO SOURCE WEIGHTS - base confidence only on reverse geocoding and single-source penalty
            confidence = 0.6 + reverse_score * 0.4  # Single source gets 60% base + reverse score boost
            
            return {
                'confidence': confidence,
                'variance': 0.0,
                'accuracy_level': self._determine_accuracy_level(confidence, 0.0),
                'recommended_source': source,
                'recommended_coords': coords,
                'reverse_geocoding_score': reverse_score,
                'cluster_analysis': {'single_source': True}
            }
        
        # Calculate distance-based analysis for multiple sources
        coord_list = list(coordinates.values())
        source_list = list(coordinates.keys())
        distances = []
        
        for i in range(len(coord_list)):
            for j in range(i + 1, len(coord_list)):
                dist = self._calculate_distance(coord_list[i], coord_list[j])
                distances.append(dist)
        
        # Calculate variance and agreement metrics
        variance = sum(distances) / len(distances) if distances else 0
        max_distance = max(distances) if distances else 0
        
        # Calculate reverse geocoding scores for each source
        reverse_scores = {}
        for source in coordinates.keys():
            if source in reverse_results:
                reverse_scores[source] = (
                    reverse_results[source]['similarity_score'] * 0.7 + 
                    reverse_results[source]['confidence'] * 0.3
                )
            else:
                reverse_scores[source] = 0.0
        
        # Select best coordinate based on EQUAL TREATMENT + reverse geocoding
        best_source, best_coords = self._select_best_coordinate_equal_treatment(
            coordinates, 
            variance, 
            reverse_scores
        )
        
        # Calculate overall confidence (NO SOURCE WEIGHTS)
        reverse_geocoding_score = max(reverse_scores.values()) if reverse_scores else 0.0
        
        # Determine base confidence from distance analysis
        if variance < 0.001:  # ~100 meters
            distance_confidence = 0.95
        elif variance < 0.01:  # ~1 km
            distance_confidence = 0.8
        elif variance < 0.1:  # ~10 km
            distance_confidence = 0.65
        else:
            distance_confidence = 0.4
        
        # EQUAL TREATMENT: Weighted combination prioritizing reverse geocoding + coordinate agreement
        overall_confidence = (
            distance_confidence * 0.3 +          # How well sources agree (30%)
            reverse_geocoding_score * 0.7        # How well reverse geocoding matches (70%)
        )
        
        return {
            'confidence': overall_confidence,
            'variance': variance,
            'max_distance_km': max_distance * 111,  # Convert to approximate km
            'accuracy_level': self._determine_accuracy_level(overall_confidence, variance),
            'recommended_source': best_source,
            'recommended_coords': best_coords,
            'reverse_geocoding_score': reverse_geocoding_score,
            'distance_confidence': distance_confidence,
            'cluster_analysis': {
                'source_count': len(coordinates),
                'avg_distance': variance,
                'max_distance': max_distance,
                'agreement_score': 1 / (1 + variance * 100)
            }
        }
    
    def _calculate_distance(self, coord1: Tuple[float, float], coord2: Tuple[float, float]) -> float:
        """Calculate distance between two coordinates using Haversine formula."""
        lat1, lon1 = coord1
        lat2, lon2 = coord2
        
        # Convert to radians
        lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
        
        # Haversine formula
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        c = 2 * math.asin(math.sqrt(a))
        
        # Earth's radius in degrees (approximate)
        r = 6371 / 111000  # Convert km to degrees
        
        return c * r
    
    def _select_best_coordinate_equal_treatment(self, 
                                              coordinates: Dict[str, Tuple[float, float]], 
                                              variance: float, 
                                              reverse_scores: Dict[str, float]) -> Tuple[str, Tuple[float, float]]:
        """Select best coordinate using EQUAL TREATMENT - no source weights."""
        if len(coordinates) == 1:
            source = list(coordinates.keys())[0]
            return source, coordinates[source]
        
        best_score = -1
        best_source = None
        best_coords = None
        
        for source, coords in coordinates.items():
            # EQUAL TREATMENT: Only use reverse geocoding score (no source preference)
            reverse_score = reverse_scores.get(source, 0.0)
            
            # Pure reverse geocoding-based selection
            composite_score = reverse_score
            
            if composite_score > best_score:
                best_score = composite_score
                best_source = source
                best_coords = coords
        
        # If no clear winner from reverse geocoding, pick first source
        return best_source or list(coordinates.keys())[0], best_coords or list(coordinates.values())[0]
    
    def _generate_recommendation(self, coordinates: Dict, analysis: Dict) -> Dict:
        """Generate user-friendly recommendations."""
        if analysis['confidence'] >= 0.9:
            return {
                'action': 'auto_approve',
                'message': f"High confidence result. Recommend using {analysis['recommended_source'].upper()} coordinates.",
                'reasoning': "Sources agree well and reverse geocoding confirms location accuracy."
            }
        elif analysis['confidence'] >= 0.7:
            return {
                'action': 'suggest_approval',
                'message': f"Good result. Suggest using {analysis['recommended_source'].upper()} coordinates.",
                'reasoning': "Sources show good agreement with acceptable reverse geocoding match."
            }
        elif analysis['confidence'] >= 0.5:
            return {
                'action': 'review_required',
                'message': "Manual review recommended due to coordinate variations or poor reverse geocoding.",
                'reasoning': "Sources show disagreement or location name doesn't match well with addresses."
            }
        else:
            return {
                'action': 'detailed_review',
                'message': "Detailed investigation required - significant issues detected.",
                'reasoning': "Major disagreements between sources and poor reverse geocoding matches."
            }
    
    def _generate_user_summary(self, analysis: Dict) -> str:
        """Generate user-friendly summary of the analysis."""
        source_count = analysis['cluster_analysis'].get('source_count', 0)
        confidence = analysis['confidence']
        reverse_score = analysis['reverse_geocoding_score']
        
        if confidence >= 0.9:
            return f"Excellent match! {source_count} sources found with high agreement and strong reverse geocoding match ({reverse_score:.0%})."
        elif confidence >= 0.7:
            return f"Good match from {source_count} sources with acceptable reverse geocoding ({reverse_score:.0%})."
        elif confidence >= 0.5:
            return f"Moderate confidence from {source_count} sources - reverse geocoding shows {reverse_score:.0%} match. Review recommended."
        else:
            return f"Low confidence from {source_count} sources - reverse geocoding only {reverse_score:.0%} match. Manual verification needed."
    
    def _determine_accuracy_level(self, confidence: float, variance: float) -> str:
        """Determine user-friendly accuracy level."""
        if confidence >= 0.9 and variance < 0.001:
            return "Excellent - high confidence with precise coordinates and strong reverse geocoding"
        elif confidence >= 0.8 and variance < 0.01:
            return "Very Good - sources agree well with good reverse geocoding match"
        elif confidence >= 0.7:
            return "Good - reliable coordinates with acceptable reverse geocoding"
        elif confidence >= 0.6:
            return "Moderate - some uncertainty in reverse geocoding, review recommended"
        else:
            return "Low - poor reverse geocoding match or coordinate disagreement, manual review required"
    
    def _auto_add_to_validated_dataset(self, geocoding_result: GeocodingResult, coords: Tuple[float, float], source: str):
        """Automatically add high-confidence results to validated dataset."""
        try:
            # Add to ValidationDataset (the "validated dataset")
            ValidationDataset.objects.update_or_create(
                location_name=geocoding_result.location_name,
                defaults={
                    'final_lat': coords[0],
                    'final_long': coords[1],
                    'country': '',  # Could be enhanced with country detection
                    'source': source
                }
            )
            
            # Update the core Location model
            try:
                location = Location.objects.get(name__iexact=geocoding_result.location_name)
                location.latitude = coords[0]
                location.longitude = coords[1]
                location.save()
                print(f"Auto-updated Location: {location.name} -> {coords[0]}, {coords[1]}")
            except Location.DoesNotExist:
                print(f"Warning: Could not find Location: {geocoding_result.location_name}")
            except Location.MultipleObjectsReturned:
                location = Location.objects.filter(name__iexact=geocoding_result.location_name).first()
                location.latitude = coords[0]
                location.longitude = coords[1]
                location.save()
                print(f"Auto-updated first matching Location: {location.name} -> {coords[0]}, {coords[1]}")
                
        except Exception as e:
            print(f"Error auto-adding to validated dataset: {e}")
    
    def _create_validation_result(self, geocoding_result: GeocodingResult, confidence: float,
                                status: str, reason: str, metadata: Optional[Dict] = None) -> ValidationResult:
        """Create a ValidationResult object."""
        
        # Extract additional scores from metadata if available
        analysis = metadata.get('coordinates_analysis', {}) if metadata else {}
        reverse_geocoding_score = analysis.get('reverse_geocoding_score', None)
        distance_confidence = analysis.get('distance_confidence', None)
        recommended_source = analysis.get('recommended_source', '')
        recommended_coords = analysis.get('recommended_coords')
        
        validation_result, created = ValidationResult.objects.update_or_create(
            geocoding_result=geocoding_result,
            defaults={
                'confidence_score': confidence,
                'validation_status': status,
                'validation_metadata': metadata or {'reason': reason},
                'reverse_geocoding_score': reverse_geocoding_score,
                'api_agreement_score': distance_confidence,
                'distance_confidence': distance_confidence,
                'recommended_source': recommended_source,
                'recommended_lat': recommended_coords[0] if recommended_coords else None,
                'recommended_lng': recommended_coords[1] if recommended_coords else None,
            }
        )
        
        return validation_result


def run_smart_validation(limit: int = None) -> Dict[str, int]:
    """Run smart validation on pending geocoding results."""
    validator = SmartGeocodingValidator()
    
    # Get pending results without validation
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
        success = geocode_cmd.geocode_location(location)
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