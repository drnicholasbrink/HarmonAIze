# geolocation/services.py
"""
Geocoding service that can be used by both management commands and Celery tasks.
This centralizes the geocoding logic to avoid duplication.

Enhanced with optional LLM-powered improvements for better location parsing
and facility matching using Google Gemini.
"""

import os
import time
import requests
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from django.db.models import Q

from .models import ValidatedDataset, GeocodingResult, HDXHealthFacility
from core.models import Location
from .llm_enhancement import get_llm_enhancer

try:
    from fuzzywuzzy import fuzz, process
    FUZZY_AVAILABLE = True
except ImportError:
    FUZZY_AVAILABLE = False

try:
    import pycountry
    PYCOUNTRY_AVAILABLE = True
except ImportError:
    PYCOUNTRY_AVAILABLE = False

logger = logging.getLogger(__name__)
class GeocodingService:
    """Service for geocoding locations using multiple APIs with optional LLM enhancements."""

    def __init__(self):
        self.local_nominatim_url = getattr(settings, 'LOCAL_NOMINATIM_URL', 'http://nominatim:8080')
        self.public_nominatim_url = 'https://nominatim.openstreetmap.org'

        self.llm_enhancer = get_llm_enhancer()
        if self.llm_enhancer.is_enabled():
            logger.info("✓ GeocodingService initialized with LLM enhancements enabled")
        else:
            logger.info("GeocodingService initialized (LLM enhancements disabled)")

        self.country_name_to_iso2 = {
            "Algeria": "DZ", "Angola": "AO", "Benin": "BJ", "Botswana": "BW", "Burkina Faso": "BF",
            "Burundi": "BI", "Cabo Verde": "CV", "Cameroon": "CM", "Central African Republic": "CF",
            "Chad": "TD", "Comoros": "KM", "Congo (Brazzaville)": "CG", "Congo (Kinshasa)": "CD",
            "Cote d'Ivoire": "CI", "Djibouti": "DJ", "Egypt": "EG", "Equatorial Guinea": "GQ",
            "Eritrea": "ER", "Eswatini": "SZ", "Ethiopia": "ET", "Gabon": "GA", "Gambia": "GM",
            "Ghana": "GH", "Guinea": "GN", "Guinea-Bissau": "GW", "Kenya": "KE", "Lesotho": "LS",
            "Liberia": "LR", "Libya": "LY", "Madagascar": "MG", "Malawi": "MW", "Mali": "ML",
            "Mauritania": "MR", "Mauritius": "MU", "Morocco": "MA", "Mozambique": "MZ",
            "Namibia": "NA", "Niger": "NE", "Nigeria": "NG", "Rwanda": "RW", "Sao Tome and Principe": "ST",
            "Senegal": "SN", "Seychelles": "SC", "Sierra Leone": "SL", "Somalia": "SO",
            "South Africa": "ZA", "South Sudan": "SS", "Sudan": "SD", "Tanzania": "TZ",
            "Togo": "TG", "Tunisia": "TN", "Uganda": "UG", "Zambia": "ZM", "Zimbabwe": "ZW"
        }

        # Approximate country bounding boxes for coordinate validation
        # Format: (min_lat, max_lat, min_lng, max_lng)
        self.country_bounds = {
            "Zimbabwe": (-22.5, -15.5, 25.0, 33.1),
            "ZW": (-22.5, -15.5, 25.0, 33.1),
            "South Africa": (-35.0, -22.0, 16.5, 33.0),
            "ZA": (-35.0, -22.0, 16.5, 33.0),
            "Zambia": (-18.1, -8.2, 21.9, 33.8),
            "ZM": (-18.1, -8.2, 21.9, 33.8),
            "Mozambique": (-26.9, -10.5, 30.2, 40.8),
            "MZ": (-26.9, -10.5, 30.2, 40.8),
            "Botswana": (-26.9, -17.8, 19.9, 29.4),
            "BW": (-26.9, -17.8, 19.9, 29.4),
            "Kenya": (-4.7, 5.5, 33.9, 41.9),
            "KE": (-4.7, 5.5, 33.9, 41.9),
            "Uganda": (-1.5, 4.2, 29.6, 35.0),
            "UG": (-1.5, 4.2, 29.6, 35.0),
            "Tanzania": (-11.8, -0.99, 29.3, 40.5),
            "TZ": (-11.8, -0.99, 29.3, 40.5),
            "Malawi": (-17.2, -9.4, 32.7, 35.9),
            "MW": (-17.2, -9.4, 32.7, 35.9),
        }

    def _validate_coordinates_in_country(self, lat, lng, country):
        """
        Validate that coordinates are within the expected country's geographic bounds.
        Returns (is_valid, message).
        """
        if not country or country not in self.country_bounds:
            # If no bounds defined, we can't validate
            return True, "No bounds defined for this country"

        min_lat, max_lat, min_lng, max_lng = self.country_bounds[country]

        if min_lat <= lat <= max_lat and min_lng <= lng <= max_lng:
            return True, f"Coordinates within {country} bounds"
        else:
            # Identify which country the coordinates might actually be in
            possible_countries = []
            for c_name, (c_min_lat, c_max_lat, c_min_lng, c_max_lng) in self.country_bounds.items():
                if c_min_lat <= lat <= c_max_lat and c_min_lng <= lng <= c_max_lng:
                    possible_countries.append(c_name)

            if possible_countries:
                return False, f"Coordinates ({lat}, {lng}) are outside {country}. Likely in: {', '.join(possible_countries)}"
            else:
                return False, f"Coordinates ({lat}, {lng}) are outside {country} bounds (expected lat: {min_lat} to {max_lat}, lng: {min_lng} to {max_lng})"

    def _get_country_name_variants(self, country_name):
        """
        Get country name variants to handle official vs common names.
        E.g., "Tanzania, United Republic of" -> ["Tanzania, United Republic of", "Tanzania"]
        """
        if not country_name:
            return []

        variants = [country_name]

        # Handle comma-separated official names
        # "Tanzania, United Republic of" -> "Tanzania"
        if ', ' in country_name:
            main_part = country_name.split(',')[0].strip()
            if main_part:
                variants.append(main_part)

        # Handle parenthetical disambiguations
        # "Congo (Kinshasa)" -> "Congo"
        if '(' in country_name:
            main_part = country_name.split('(')[0].strip()
            if main_part:
                variants.append(main_part)

        return variants

    def _extract_country_smart(self, location_name):
        """Extract country using pycountry and return clean location."""
        if not location_name or not PYCOUNTRY_AVAILABLE:
            return self._extract_country_from_location_name(location_name)
        
        words = location_name.strip().split()
        if len(words) < 2:
            return None, location_name

        for i in range(len(words)):
            potential_country = ' '.join(words[i:])

            try:
                country = pycountry.countries.lookup(potential_country)
                clean_location = ' '.join(words[:i]).strip()
                return country.name, clean_location
            except LookupError:
                continue

        for country in pycountry.countries:
            if country.name.lower() in location_name.lower():
                clean_location = location_name.replace(country.name, '').strip()
                return country.name, clean_location

        return self._extract_country_from_location_name(location_name)
    
    def _get_country_iso(self, country_name):
        """Get ISO code for API optimization."""
        if not country_name or not PYCOUNTRY_AVAILABLE:
            return self.country_name_to_iso2.get(country_name)
            
        try:
            country = pycountry.countries.lookup(country_name)
            return country.alpha_2
        except LookupError:
            return self.country_name_to_iso2.get(country_name)
    
    def _extract_country_from_location_name(self, location_name):
        """Extract country information from location name."""
        if not location_name:
            return None, location_name

        words = location_name.strip().split()
        if len(words) < 2:
            return None, location_name

        for i in range(len(words)):
            potential_country = ' '.join(words[i:])
            for country_name in self.country_name_to_iso2.keys():
                if potential_country.lower() == country_name.lower():
                    location_part = ' '.join(words[:i]).strip()
                    return country_name, location_part

        last_word = words[-1].lower()

        if last_word in [country.lower() for country in self.country_name_to_iso2.keys()]:
            for country_name in self.country_name_to_iso2.keys():
                if country_name.lower() == last_word:
                    location_part = ' '.join(words[:-1]).strip()
                    return country_name, location_part

        if len(words) >= 2:
            last_two_words = ' '.join(words[-2:]).lower()
            for country_name in self.country_name_to_iso2.keys():
                if country_name.lower() == last_two_words:
                    location_part = ' '.join(words[:-2]).strip()
                    return country_name, location_part

        return None, location_name

    def _parse_location_intelligently(self, location_name: str) -> dict:
        """
        Intelligently extract country and administrative units from ANY location string.

        Works globally - no hardcoded countries. Uses pycountry for 249 countries.

        Examples:
            "parirenyatwa hospital harare zimbabwe" →
                {country: 'Zimbabwe', country_code: 'ZW', city: 'Harare', facility: 'parirenyatwa hospital'}
            "hôpital général paris france" →
                {country: 'France', country_code: 'FR', city: 'Paris', facility: 'hôpital général'}
            "general hospital" →
                {country: None, country_code: None, facility: 'general hospital'}
            "tokyo japan" →
                {country: 'Japan', country_code: 'JP', city: 'Tokyo', facility: None}

        Args:
            location_name: Unstructured location string

        Returns:
            dict: Parsed components with keys:
                - original: Original input
                - country: Country name (or None)
                - country_code: ISO 2-letter code (or None)
                - admin_level_1: State/Province (or None)
                - admin_level_2: City/District (or None)
                - facility: Remaining location name after extraction
                - parsed_components: List of successfully parsed components
        """
        result = {
            'original': location_name,
            'country': None,
            'country_code': None,
            'admin_level_1': None,
            'admin_level_2': None,
            'facility': location_name,
            'parsed_components': []
        }

        if not location_name:
            return result

        words = location_name.strip().split()
        remaining_words = words.copy()
        country_info = self._extract_country_from_anywhere(location_name)
        if country_info:
            result['country'] = country_info['name']
            result['country_code'] = country_info['code']
            result['parsed_components'].append('country')

            country_words = country_info['matched_text'].split()
            remaining_words = [w for w in remaining_words
                              if w.lower() not in [cw.lower() for cw in country_words]]
        if result['country_code']:
            city_info = self._extract_city_for_country(
                ' '.join(remaining_words),
                result['country_code']
            )
            if city_info:
                result['admin_level_2'] = city_info['name']
                result['parsed_components'].append('city')

                remaining_words = [w for w in remaining_words
                                  if w.lower() != city_info['name'].lower()]
        if remaining_words:
            result['facility'] = ' '.join(remaining_words).strip()
        else:

            result['facility'] = location_name

        return result

    def _extract_country_from_anywhere(self, text: str) -> dict:
        """
        Extract country from text using pycountry (works globally).

        Handles:
            - Country names: "Zimbabwe", "France", "United States"
            - Variations: "USA" → "United States"
            - Any position: beginning, middle, or end

        Args:
            text: Text containing potential country name

        Returns:
            dict with keys: name, code, matched_text (or None if not found)
        """
        if not PYCOUNTRY_AVAILABLE:

            country, _ = self._extract_country_from_location_name(text)
            if country:
                iso_code = self._get_country_iso(country)
                return {
                    'name': country,
                    'code': iso_code,
                    'matched_text': country
                }
            return None

        import pycountry
        words = text.split()
        for length in [3, 2, 1]:
            for i in range(len(words) - length + 1):
                phrase = ' '.join(words[i:i+length])
                try:
                    country = pycountry.countries.lookup(phrase)
                    return {
                        'name': country.name,
                        'code': country.alpha_2,
                        'matched_text': phrase
                    }
                except LookupError:
                    continue

        return None

    def _extract_city_for_country(self, text: str, country_code: str) -> dict:
        """
        Extract city/region from text given a country code.

        Uses pycountry.subdivisions (has major cities/regions globally).

        Args:
            text: Text potentially containing city name
            country_code: ISO 2-letter country code

        Returns:
            dict with keys: name, code, type (or None if not found)
        """
        if not PYCOUNTRY_AVAILABLE:
            return None

        import pycountry

        try:

            subdivisions = pycountry.subdivisions.get(country_code=country_code)

            words = text.lower().split()

            for subdivision in subdivisions:

                if subdivision.name.lower() in text.lower():
                    return {
                        'name': subdivision.name,
                        'code': subdivision.code,
                        'type': getattr(subdivision, 'type', 'unknown')
                    }
                for word in words:
                    if word == subdivision.name.lower():
                        return {
                            'name': subdivision.name,
                            'code': subdivision.code,
                            'type': getattr(subdivision, 'type', 'unknown')
                        }

        except (KeyError, LookupError):
            pass

        return None

    def geocode_single_location(self, location, force_reprocess=False, user=None):
        """
        Geocode a single location using all available sources.
        This extracts the core logic from the management command.

        Args:
            location: Location model instance
            force_reprocess: If True, re-geocode even if results exist
            user: User model instance (required for creating GeocodingResult)
        """

        if not force_reprocess:
            existing_result = GeocodingResult.objects.filter(
                location_name__iexact=location.name
            ).first()

            # Only use cached result if we have multiple successful APIs
            # If only HDX succeeded, re-query to try getting ArcGIS/Google/Nominatim
            if existing_result:
                successful_count = sum([
                    existing_result.hdx_success,
                    existing_result.arcgis_success,
                    existing_result.google_success,
                    existing_result.nominatim_success
                ])

                if successful_count >= 2:
                    logger.info(f"Using cached result for '{location.name}' ({successful_count} APIs successful)")
                    return existing_result
                elif successful_count == 1:
                    logger.warning(f"Only 1 API succeeded for '{location.name}' - re-querying all APIs")
                    # Delete and re-geocode to try all APIs again
                    existing_result.delete()

        # Step 1: Check validated dataset first
        validated_result = self.check_validated_dataset(location)
        if validated_result:
            # Create geocoding result from validated data
            geocoding_result, created = GeocodingResult.objects.get_or_create(
                location_name=location.name,
                created_by=user or validated_result.created_by,
                defaults={
                    'location': location,
                    'validation_status': 'validated',
                    'final_lat': validated_result.final_lat,
                    'final_lng': validated_result.final_long,
                    'selected_source': 'validated_dataset',
                    # Mark as having successful results for the validation logic
                    'hdx_success': True,
                    'hdx_lat': validated_result.final_lat,
                    'hdx_lng': validated_result.final_long
                }
            )
            return geocoding_result

        # Step 2: Perform full geocoding using all APIs
        return self.geocode_location_full(location, user=user)
    
    def check_validated_dataset(self, location):
        """
        Check if location exists in validated dataset.
        CRITICAL: Filter by country FIRST to prevent wrong-country matches!
        """

        country, location_part = self._extract_country_smart(location.name)
        search_terms = [location.name, location_part] if location_part else [location.name]

        # Get country name variants to handle "Tanzania, United Republic of" vs "Tanzania"
        country_variants = self._get_country_name_variants(country) if country else []

        logger.info(f"VALIDATED DATASET: Searching for '{location.name}' (extracted country: '{country}')")
        if country_variants and len(country_variants) > 1:
            logger.info(f"VALIDATED DATASET: Country variants: {country_variants}")

        # Step 1: Try exact match (with country filter if country detected)
        for search_term in search_terms:
            if search_term:
                # Try without country filter first (fastest)
                query = ValidatedDataset.objects.filter(location_name__iexact=search_term.strip())

                # CRITICAL: Filter by country if we extracted one
                # Try all country name variants
                if country_variants:
                    for country_variant in country_variants:
                        result = query.filter(country__iexact=country_variant).first()
                        if result:
                            logger.info(f"VALIDATED DATASET: Found exact match for '{search_term}' -> '{result.location_name}' in {result.country}")
                            return result
                else:
                    # No country extracted, try without country filter
                    result = query.first()
                    if result:
                        logger.info(f"VALIDATED DATASET: Found exact match for '{search_term}' -> '{result.location_name}' (no country filter)")
                        return result

        # Step 2: Try fuzzy matching (ONLY within the same country!)
        if FUZZY_AVAILABLE and country_variants:
            # CRITICAL: Only fuzzy match within the SAME country
            # Build query that matches any country variant
            country_query = Q()
            for country_variant in country_variants:
                country_query |= Q(country__iexact=country_variant)

            country_validated = ValidatedDataset.objects.filter(country_query)

            if country_validated.exists():
                location_names = [v.location_name for v in country_validated]
                logger.info(f"VALIDATED DATASET: Trying fuzzy match within {country_variants[0]} ({country_validated.count()} locations)")

                for search_term in search_terms:
                    if search_term:
                        match = process.extractOne(search_term.strip(), location_names, score_cutoff=85)
                        if match:
                            # Find the result using any country variant
                            result = ValidatedDataset.objects.filter(
                                location_name=match[0]
                            ).filter(country_query).first()

                            if result:
                                logger.info(f"VALIDATED DATASET: Found fuzzy match for '{search_term}' -> '{match[0]}' in {result.country} (score: {match[1]}%)")
                                return result
            else:
                logger.info(f"VALIDATED DATASET: No validated locations found for country '{country}' (tried variants: {country_variants})")
        elif FUZZY_AVAILABLE and not country:
            logger.warning(f"VALIDATED DATASET: Skipping fuzzy match - no country extracted from '{location.name}'")

        logger.info(f"VALIDATED DATASET: No match found for '{location.name}'")
        return None
    
    def geocode_location_full(self, location, user=None):
        """
        Geocode using all available API sources with intelligent parsing.

        Enhanced with LLM parsing for better location component extraction.
        Falls back to traditional parsing if LLM unavailable.

        Args:
            location: Location model instance
            user: User model instance (required for creating GeocodingResult)
        """

        logger.info(f"=== GEOCODING: {location.name} ===")

        llm_parsed = self.llm_enhancer.parse_location_structured(location.name)

        if llm_parsed:
            country = llm_parsed.get('country')
            iso_code = llm_parsed.get('country_code')
            city = llm_parsed.get('city')
            facility_name = llm_parsed.get('facility_name')

            logger.info(
                f"✓ LLM parsing: '{location.name}' → "
                f"facility='{facility_name}', city='{city}', country='{country}', iso='{iso_code}'"
            )
            parsed_location = {
                'original': location.name,
                'country': country,
                'country_code': iso_code,
                'admin_level_2': city,
                'facility': facility_name or location.name,
                'llm_enhanced': True,
                'llm_parsed': llm_parsed
            }
        else:
            parsed_location = self._parse_location_intelligently(location.name)
            country = parsed_location['country']
            iso_code = parsed_location['country_code']
            city = parsed_location['admin_level_2']

            logger.info(
                f"Traditional parsing: '{location.name}' → "
                f"country={country}, iso={iso_code}, city={city}"
            )
        # Parsing is ONLY for extracting country codes for API parameters
        query = location.name

        logger.info(f"Querying APIs with: country='{country}', iso='{iso_code}', query='{query}'")

        # PARALLEL GEOCODING: Call all APIs simultaneously using ThreadPoolExecutor
        logger.info(f">>> Calling all geocoding APIs in parallel...")

        results = {}

        # Define API call functions
        def call_hdx():
            return ("hdx", self.geocode_hdx_enhanced(location, country))

        def call_arcgis():
            return ("arcgis", self.geocode_arcgis(query, country, iso_code))

        def call_google():
            return ("google", self.geocode_google(query, country, iso_code))

        def call_nominatim():
            return ("nominatim", self.geocode_nominatim_with_fallback(query, country, iso_code))

        # Execute all API calls in parallel
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(call_hdx),
                executor.submit(call_arcgis),
                executor.submit(call_google),
                executor.submit(call_nominatim)
            ]

            # Collect results as they complete
            for future in as_completed(futures):
                try:
                    source, result = future.result()
                    results[source] = result

                    if result.get("coordinates"):
                        logger.info(f"✓ {source.upper()}: SUCCESS - {result['coordinates']}")
                    else:
                        logger.warning(f"✗ {source.upper()}: {result.get('error', 'No result')}")
                except Exception as e:
                    logger.error(f"API call failed: {e}")
        
        # Create or update geocoding result
        # Get or create with location_name and created_by to ensure uniqueness per user
        if user:
            geocoding_result, created = GeocodingResult.objects.get_or_create(
                location_name=location.name,
                created_by=user,
                defaults={
                    'location': location,
                    'validation_status': 'pending',
                    'parsed_location_data': parsed_location  # Store parsed components
                }
            )
        else:
            # Fallback for management commands without user context
            # In this case, try to find existing result or create with location FK only
            logger.warning(f"No user provided for geocoding '{location.name}' - this may cause issues with user-scoped queries")
            geocoding_result, created = GeocodingResult.objects.filter(
                location_name=location.name
            ).first(), False

            if not geocoding_result:
                # Cannot create without user - this will fail
                logger.error(f"Cannot create GeocodingResult without user for '{location.name}'")
                return None

        # Update parsed data if not created
        if not created:
            geocoding_result.parsed_location_data = parsed_location
        

        has_success = False
        for source, data in results.items():
            if data.get("coordinates"):
                lat, lng = data["coordinates"]
                setattr(geocoding_result, f"{source}_lat", lat)
                setattr(geocoding_result, f"{source}_lng", lng)
                setattr(geocoding_result, f"{source}_success", True)
                

                if source == "hdx" and data.get("facility"):
                    geocoding_result.hdx_facility_match = data["facility"]
                elif source == "nominatim":

                    raw_response = data.get("raw_response", [])
                    geocoding_result.nominatim_raw_response = {
                        'results': raw_response if isinstance(raw_response, list) else [raw_response],
                        'local_nominatim_used': data.get('local_nominatim_used', False)
                    }
                elif source != "hdx":
                    setattr(geocoding_result, f"{source}_raw_response", data.get("raw_response"))
                
                has_success = True
                logger.info(f"{source.upper()}: Success - {lat:.6f}, {lng:.6f}")
            else:
                setattr(geocoding_result, f"{source}_error", data.get("error", "Unknown error"))
                setattr(geocoding_result, f"{source}_success", False)
                logger.info(f"{source.upper()}: Failed - {data.get('error', 'Unknown error')}")
        
        geocoding_result.save()
        return geocoding_result if has_success else None
    
    def geocode_hdx_enhanced(self, location, country=None):
        """
        Enhanced HDX geocoding with comprehensive fuzzy matching.
        This should match "chitungwiza hospital" with "Chitungwiza Central Hospital"
        """
        if not location.name:
            return {"error": "No location name provided"}
        
        try:
            hdx_facilities = HDXHealthFacility.objects.all()

            if not hdx_facilities.exists():
                return {"error": "No HDX facilities loaded in database"}

            logger.info(f"HDX: Total facilities in database: {hdx_facilities.count()}")

            if not country:
                logger.error(f"HDX: No country extracted from '{location.name}' - CANNOT search HDX safely")
                logger.error(f"HDX: Skipping HDX to prevent matching facilities from wrong countries")
                return {"error": "Country not detected - HDX search requires country information"}

            iso_code = self._get_country_iso(country)
            logger.info(f"HDX: Filtering by country='{country}', iso='{iso_code}'")

            # STRICT country filtering - exact match ONLY (no icontains)
            # This prevents matching facilities from wrong countries
            country_filtered = hdx_facilities.filter(
                Q(country__iexact=country) |
                Q(country__iexact=iso_code)
            )

            # Log how many facilities matched for debugging
            logger.info(f"HDX: Exact country filter matched {country_filtered.count()} facilities")

            if country_filtered.exists():
                hdx_facilities = country_filtered
                logger.info(f"HDX: Filtered to {hdx_facilities.count()} facilities in {country}")
            else:
                logger.warning(f"HDX: No facilities found for country '{country}' (ISO: {iso_code})")

                sample_countries = hdx_facilities.values_list('country', flat=True).distinct()[:10]
                logger.warning(f"HDX: Sample countries in DB: {list(sample_countries)}")
                logger.warning(f"HDX: Skipping HDX search to avoid wrong country matches")
                return {"error": f"No HDX facilities found in {country}"}
            

            _, location_part = self._extract_country_smart(location.name)
            search_name = location_part if location_part else location.name
            
            logger.info(f"HDX: Searching {hdx_facilities.count()} total facilities for '{search_name}'")
            
            # Step 2: Try exact matches first
            exact_matches = hdx_facilities.filter(
                Q(facility_name__iexact=search_name)
            )

            if exact_matches.exists():
                facility = exact_matches.first()
                logger.info(f"HDX: EXACT match found - '{facility.facility_name}' in {facility.country}")

                # CRITICAL: Validate coordinates are actually in the expected country
                is_valid, validation_msg = self._validate_coordinates_in_country(
                    facility.hdx_latitude, facility.hdx_longitude, country
                )
                logger.info(f"HDX: Coordinate validation: {validation_msg}")

                if not is_valid:
                    logger.error(f"HDX: REJECTING match - {validation_msg}")
                    logger.error(f"HDX: Facility '{facility.facility_name}' claims country='{facility.country}' but coordinates are elsewhere!")
                    # Continue to next matching strategy instead of returning wrong coordinates
                else:
                    return {
                        "coordinates": (facility.hdx_latitude, facility.hdx_longitude),
                        "facility": facility,
                        "match_type": "exact",
                        "confidence": 1.0
                    }
            
            # Step 3: Try partial matches (contains)
            contains_matches = hdx_facilities.filter(
                Q(facility_name__icontains=search_name) |
                Q(facility_name__icontains=search_name.replace(' ', ''))
            )

            if contains_matches.exists():
                facility = contains_matches.first()
                logger.info(f"HDX: CONTAINS match found - '{facility.facility_name}' in {facility.country}")

                # CRITICAL: Validate coordinates are actually in the expected country
                is_valid, validation_msg = self._validate_coordinates_in_country(
                    facility.hdx_latitude, facility.hdx_longitude, country
                )
                logger.info(f"HDX: Coordinate validation: {validation_msg}")

                if not is_valid:
                    logger.error(f"HDX: REJECTING match - {validation_msg}")
                    logger.error(f"HDX: Facility '{facility.facility_name}' claims country='{facility.country}' but coordinates are elsewhere!")
                    # Continue to next matching strategy instead of returning wrong coordinates
                else:
                    return {
                        "coordinates": (facility.hdx_latitude, facility.hdx_longitude),
                        "facility": facility,
                        "match_type": "contains",
                        "confidence": 0.8
                    }
            
            # Step 4: Try LLM-enhanced semantic matching (if enabled)
            if self.llm_enhancer.is_enabled():
                logger.info(f"HDX: Trying LLM-enhanced semantic matching...")

                all_facilities = list(hdx_facilities)
                facility_names = [f.facility_name for f in all_facilities]
                llm_match = self.llm_enhancer.find_best_facility_match(
                    search_name,
                    facility_names,
                    max_candidates=10
                )

                if llm_match:
                    matched_name, confidence, reasoning = llm_match
                    matched_facility = hdx_facilities.filter(
                        facility_name=matched_name
                    ).first()

                    if matched_facility:
                        logger.info(f"✓ HDX: LLM SEMANTIC match - '{matched_name}' in {matched_facility.country} (confidence: {confidence:.1%})")
                        logger.info(f"  Reasoning: {reasoning}")

                        # CRITICAL: Validate coordinates are actually in the expected country
                        is_valid, validation_msg = self._validate_coordinates_in_country(
                            matched_facility.hdx_latitude, matched_facility.hdx_longitude, country
                        )
                        logger.info(f"HDX: Coordinate validation: {validation_msg}")

                        if not is_valid:
                            logger.error(f"HDX: REJECTING LLM match - {validation_msg}")
                            logger.error(f"HDX: Facility '{matched_facility.facility_name}' claims country='{matched_facility.country}' but coordinates are elsewhere!")
                            # Continue to next matching strategy instead of returning wrong coordinates
                        else:
                            return {
                                "coordinates": (matched_facility.hdx_latitude, matched_facility.hdx_longitude),
                                "facility": matched_facility,
                                "match_type": "llm_semantic",
                                "confidence": confidence,
                                "reasoning": reasoning
                            }

            # Step 5: Fallback to traditional fuzzy matching (if LLM didn't find match)
            if FUZZY_AVAILABLE:
                logger.info(f"HDX: Trying traditional fuzzy matching...")
                all_facilities = list(hdx_facilities)
                facility_names = [f.facility_name for f in all_facilities]
                matching_strategies = {
                    'token_sort_ratio': fuzz.token_sort_ratio,
                    'token_set_ratio': fuzz.token_set_ratio,
                    'partial_ratio': fuzz.partial_ratio,
                    'ratio': fuzz.ratio
                }

                best_match = None
                best_score = 0
                best_strategy = None

                for strategy_name, scorer in matching_strategies.items():
                    matches = process.extract(
                        search_name,
                        facility_names,
                        scorer=scorer,
                        limit=3
                    )

                    for match_name, score in matches:
                        if score > best_score and score >= 80:  # Threshold for fuzzy matching (increased from 65 to 80)
                            best_match = match_name
                            best_score = score
                            best_strategy = strategy_name

                if best_match and best_score >= 80:
                    matched_facility = hdx_facilities.filter(
                        facility_name=best_match
                    ).first()

                    if matched_facility:
                        logger.info(f"HDX: FUZZY match found - '{best_match}' in {matched_facility.country} (score: {best_score}%, strategy: {best_strategy})")

                        # CRITICAL: Validate coordinates are actually in the expected country
                        is_valid, validation_msg = self._validate_coordinates_in_country(
                            matched_facility.hdx_latitude, matched_facility.hdx_longitude, country
                        )
                        logger.info(f"HDX: Coordinate validation: {validation_msg}")

                        if not is_valid:
                            logger.error(f"HDX: REJECTING fuzzy match - {validation_msg}")
                            logger.error(f"HDX: Facility '{matched_facility.facility_name}' claims country='{matched_facility.country}' but coordinates are elsewhere!")
                            # Continue to next matching strategy instead of returning wrong coordinates
                        else:
                            return {
                                "coordinates": (matched_facility.hdx_latitude, matched_facility.hdx_longitude),
                                "facility": matched_facility,
                                "match_type": "fuzzy",
                                "confidence": best_score / 100.0
                            }

            # Step 6: Enhanced containment matching (DISABLED - too aggressive, matches partial words like "hospital")
            # This was matching "Nharira Rural Hospital" for "Parirenyatwa Hospital" just because both have "hospital"
            # Uncomment only if you want very loose matching (NOT RECOMMENDED)
            # search_terms = search_name.lower().split()
            # if len(search_terms) > 0:
            #     logger.info(f"HDX: Trying containment matching with terms: {search_terms}")
            #     for facility in hdx_facilities:
            #         facility_name_lower = facility.facility_name.lower()
            #         matching_terms = sum(1 for term in search_terms if term in facility_name_lower)
            #         match_ratio = matching_terms / len(search_terms)
            #         if match_ratio >= 0.9:  # Increased from 0.7 to 0.9 - require 90% term match
            #             confidence = 0.6 + (match_ratio * 0.3)
            #             logger.info(f"HDX: CONTAINMENT match found - '{facility.facility_name}' (terms: {matching_terms}/{len(search_terms)}, confidence: {confidence:.1%})")
            #             return {
            #                 "coordinates": (facility.hdx_latitude, facility.hdx_longitude),
            #                 "facility": facility,
            #                 "match_type": "containment",
            #                 "confidence": confidence
            #             }

            logger.info(f"HDX: All matching strategies exhausted - no suitable match found")
            return {"error": f"No HDX facility match found for '{location.name}'"}
            
        except Exception as e:
            logger.error(f"HDX search error for '{location.name}': {e}")
            return {"error": f"HDX search error: {e}"}
    
    def geocode_arcgis(self, query, country=None, iso_code=None):
        """Geocode using ArcGIS API with country optimization."""
        try:
            url = 'https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates'
            params = {
                'f': 'json',
                'singleLine': query,
                'outFields': 'Match_addr',
                'maxLocations': 1
            }
            

            if iso_code:
                params['sourceCountry'] = iso_code
            elif country and country in self.country_name_to_iso2:
                params['sourceCountry'] = self.country_name_to_iso2[country]

            response = requests.get(url, params=params, timeout=3)
            response.raise_for_status()
            data = response.json()

            if data.get("candidates") and len(data["candidates"]) > 0:
                candidate = data["candidates"][0]
                location = candidate["location"]
                return {
                    "coordinates": (location["y"], location["x"]),
                    "raw_response": data
                }
            return {"error": "No candidates found", "raw_response": data}
            
        except requests.exceptions.Timeout:
            return {"error": "Request timeout"}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}"}
        except Exception as e:
            return {"error": f"Unexpected error: {str(e)}"}

    def geocode_google(self, query, country=None, iso_code=None):
        """Geocode using Google Maps API with region optimization."""
        try:
            key = getattr(settings, "GOOGLE_GEOCODING_API_KEY", None) or os.getenv("GOOGLE_GEOCODING_API_KEY")
            if not key:
                return {"error": "Missing Google API key"}

            url = "https://maps.googleapis.com/maps/api/geocode/json"
            params = {"address": query, "key": key}
            

            if iso_code:
                params["region"] = iso_code.lower()
            elif country:
                params["region"] = self.country_name_to_iso2.get(country, country.lower())

            response = requests.get(url, params=params, timeout=3)
            response.raise_for_status()
            data = response.json()

            if data["status"] == "OK" and data["results"]:
                result = data["results"][0]
                location = result["geometry"]["location"]
                return {
                    "coordinates": (location["lat"], location["lng"]),
                    "raw_response": data
                }
            return {
                "error": f"Status: {data.get('status', 'No results')}",
                "raw_response": data
            }
            
        except requests.exceptions.Timeout:
            return {"error": "Request timeout"}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}"}
        except Exception as e:
            return {"error": f"Unexpected error: {str(e)}"}

    def geocode_nominatim_with_fallback(self, query, country=None, iso_code=None):
        """Geocode using Nominatim with local fallback to public API and country optimization."""

        result = self._geocode_nominatim_local(query, country, iso_code)
        if result and result.get("coordinates"):
            result['local_nominatim_used'] = True
            logger.info(f"NOMINATIM (LOCAL): Success")
            return result
        

        logger.info(f"NOMINATIM: Local failed, trying public API...")
        result = self._geocode_nominatim_public(query, country, iso_code)
        if result:
            result['local_nominatim_used'] = False
        
        return result

    def _geocode_nominatim_local(self, query, country=None, iso_code=None):
        """Geocode using local Nominatim instance."""
        try:
            url = f'{self.local_nominatim_url}/search'
            params = {
                'q': query, 
                'format': 'json', 
                'limit': 1, 
                'addressdetails': 1,
                'dedupe': 1
            }
            
            if country:
                params['countrycodes'] = self.country_name_to_iso2.get(country, country.lower())
            
            headers = {
                'User-Agent': 'HarmonAIze-Geocoder/1.0 (harmonaize@project.com)'
            }
            

            response = requests.get(url, params=params, headers=headers, timeout=5)
            response.raise_for_status()
            data = response.json()
            
            if data and len(data) > 0:
                result = data[0]
                return {
                    "coordinates": (float(result['lat']), float(result['lon'])),
                    "raw_response": data
                }
            return {"error": "No results found", "raw_response": data}
            
        except requests.exceptions.ConnectionError:
            return {"error": "Local Nominatim connection failed (not running or unreachable)"}
        except requests.exceptions.Timeout:
            return {"error": "Local Nominatim timeout"}
        except Exception as e:
            return {"error": f"Local Nominatim error: {str(e)}"}

    def _geocode_nominatim_public(self, query, country=None, iso_code=None):
        """Geocode using public Nominatim API with country optimization."""
        try:
            url = f'{self.public_nominatim_url}/search'
            params = {
                'q': query, 
                'format': 'json', 
                'limit': 1, 
                'addressdetails': 1,
                'dedupe': 1
            }
            

            if iso_code:
                params['countrycodes'] = iso_code.lower()
            elif country:
                params['countrycodes'] = self.country_name_to_iso2.get(country, country.lower())
            
            headers = {
                'User-Agent': 'HarmonAIze-Geocoder/1.0 (harmonaize@project.com)'
            }
            
            response = requests.get(url, params=params, headers=headers, timeout=3)
            response.raise_for_status()
            data = response.json()

            if data and len(data) > 0:
                result = data[0]
                return {
                    "coordinates": (float(result['lat']), float(result['lon'])),
                    "raw_response": data
                }
            return {"error": "No results found", "raw_response": data}

        except requests.exceptions.Timeout:
            return {"error": "Public Nominatim timeout"}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}"}
        except Exception as e:
            return {"error": f"Public Nominatim error: {str(e)}"}