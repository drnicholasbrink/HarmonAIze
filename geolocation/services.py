# geolocation/services.py
"""
Geocoding service that can be used by both management commands and Celery tasks.
This centralizes the geocoding logic to avoid duplication.
"""

import os
import time
import requests
import logging
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from django.db.models import Q

from .models import ValidatedDataset, GeocodingResult, HDXHealthFacility
from core.models import Location

# Try to import fuzzy matching
try:
    from fuzzywuzzy import fuzz, process
    FUZZY_AVAILABLE = True
except ImportError:
    FUZZY_AVAILABLE = False

# Try to import pycountry for country extraction
try:
    import pycountry
    PYCOUNTRY_AVAILABLE = True
except ImportError:
    PYCOUNTRY_AVAILABLE = False

logger = logging.getLogger(__name__)


class GeocodingService:
    """Service for geocoding locations using multiple APIs."""
    
    def __init__(self):
        # Local Nominatim configuration
        self.local_nominatim_url = getattr(settings, 'LOCAL_NOMINATIM_URL', 'http://nominatim:8080')
        self.public_nominatim_url = 'https://nominatim.openstreetmap.org'
        
        # Country mapping for API calls
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
    
    def _extract_country_smart(self, location_name):
        """Extract country using pycountry and return clean location."""
        if not location_name or not PYCOUNTRY_AVAILABLE:
            return self._extract_country_from_location_name(location_name)
        
        words = location_name.strip().split()
        if len(words) < 2:
            return None, location_name
            
        # Strategy 1: Check last 1-2 words
        for i in range(len(words)):
            potential_country = ' '.join(words[i:])
            
            # Use pycountry to find matches
            try:
                country = pycountry.countries.lookup(potential_country)
                clean_location = ' '.join(words[:i]).strip()
                return country.name, clean_location
            except LookupError:
                continue
        
        # Strategy 2: Check anywhere in string (for "Hospital in Kenya" patterns)
        for country in pycountry.countries:
            if country.name.lower() in location_name.lower():
                clean_location = location_name.replace(country.name, '').strip()
                return country.name, clean_location
        
        # Fallback to original method
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
            
        # Common country patterns at the end of location names
        words = location_name.strip().split()
        if len(words) < 2:
            return None, location_name
            
        # Check if last word(s) match known countries
        for i in range(len(words)):
            potential_country = ' '.join(words[i:])
            for country_name in self.country_name_to_iso2.keys():
                if potential_country.lower() == country_name.lower():
                    location_part = ' '.join(words[:i]).strip()
                    return country_name, location_part
                    
        # Check if the last word could be a country (more flexible approach)
        last_word = words[-1].lower()
        
        # If last word is in our known countries, extract it
        if last_word in [country.lower() for country in self.country_name_to_iso2.keys()]:
            # Find the actual country name with proper capitalization
            for country_name in self.country_name_to_iso2.keys():
                if country_name.lower() == last_word:
                    location_part = ' '.join(words[:-1]).strip()
                    return country_name, location_part
        
        # Check for two-word countries like "South Africa"
        if len(words) >= 2:
            last_two_words = ' '.join(words[-2:]).lower()
            for country_name in self.country_name_to_iso2.keys():
                if country_name.lower() == last_two_words:
                    location_part = ' '.join(words[:-2]).strip()
                    return country_name, location_part
            
        return None, location_name

    # ==========================================
    # INTELLIGENT LOCATION PARSING (GLOBAL)
    # ==========================================

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

        # STEP 1: Extract country (works globally with pycountry)
        country_info = self._extract_country_from_anywhere(location_name)
        if country_info:
            result['country'] = country_info['name']
            result['country_code'] = country_info['code']
            result['parsed_components'].append('country')
            # Remove country words from remaining
            country_words = country_info['matched_text'].split()
            remaining_words = [w for w in remaining_words
                              if w.lower() not in [cw.lower() for cw in country_words]]

        # STEP 2: Extract city/region (if we know the country)
        if result['country_code']:
            city_info = self._extract_city_for_country(
                ' '.join(remaining_words),
                result['country_code']
            )
            if city_info:
                result['admin_level_2'] = city_info['name']
                result['parsed_components'].append('city')
                # Remove city from remaining words
                remaining_words = [w for w in remaining_words
                                  if w.lower() != city_info['name'].lower()]

        # STEP 3: What's left is likely the facility/location name
        if remaining_words:
            result['facility'] = ' '.join(remaining_words).strip()
        else:
            # If everything was extracted, use original as facility
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
            # Fallback to old method
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

        # Try each word/phrase as potential country
        words = text.split()

        # Check 1-3 word combinations (for "United States", "South Africa", etc.)
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
            # Get subdivisions (states/provinces/regions) for this country
            subdivisions = pycountry.subdivisions.get(country_code=country_code)

            words = text.lower().split()

            for subdivision in subdivisions:
                # Check if subdivision name appears in text
                if subdivision.name.lower() in text.lower():
                    return {
                        'name': subdivision.name,
                        'code': subdivision.code,
                        'type': getattr(subdivision, 'type', 'unknown')
                    }

                # Check individual words
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

    def geocode_single_location(self, location, force_reprocess=False):
        """
        Geocode a single location using all available sources.
        This extracts the core logic from the management command.
        """
        # Check if already processed
        if not force_reprocess:
            existing_result = GeocodingResult.objects.filter(
                location_name__iexact=location.name
            ).first()
            
            if existing_result and existing_result.has_any_results:
                return existing_result
        
        # Step 1: Check validated dataset first
        validated_result = self.check_validated_dataset(location)
        if validated_result:
            # Create geocoding result from validated data
            geocoding_result, created = GeocodingResult.objects.get_or_create(
                location_name=location.name,
                defaults={
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
        return self.geocode_location_full(location)
    
    def check_validated_dataset(self, location):
        """Check if location exists in validated dataset."""
        
        # Extract location part without country for better matching using smart extraction
        country, location_part = self._extract_country_smart(location.name)
        search_terms = [location.name, location_part] if location_part else [location.name]
        
        
        # Try exact matches first (case-insensitive)
        for search_term in search_terms:
            if search_term:
                result = ValidatedDataset.objects.filter(
                    location_name__iexact=search_term.strip()
                ).first()
                if result:
                    logger.info(f"VALIDATED DATASET: Found exact match for '{search_term}' -> '{result.location_name}'")
                    return result
        
        # Try fuzzy matching if no exact match
        if FUZZY_AVAILABLE:
            all_validated = ValidatedDataset.objects.all()
            location_names = [v.location_name for v in all_validated]
            
            # Try fuzzy matching with both full name and location part
            for search_term in search_terms:
                if search_term:
                    match = process.extractOne(search_term.strip(), location_names, score_cutoff=80)
                    if match:
                        result = ValidatedDataset.objects.filter(location_name=match[0]).first()
                        logger.info(f"VALIDATED DATASET: Found fuzzy match for '{search_term}' -> '{match[0]}' (score: {match[1]}%)")
                        return result
        
        return None
    
    def geocode_location_full(self, location):
        """
        Geocode using all available API sources with intelligent parsing.

        Uses intelligent location parsing to extract country info for API optimization,
        but KEEPS the full location name for actual geocoding queries.
        """
        # Parse location to extract country/city for API optimization
        parsed_location = self._parse_location_intelligently(location.name)

        # CRITICAL: Use FULL original name for geocoding query
        # Parsing is ONLY for extracting country codes for API parameters
        query = location.name

        # Get country info for API optimization (region biasing, country filters)
        country = parsed_location['country']
        iso_code = parsed_location['country_code']
        city = parsed_location['admin_level_2']

        logger.info(
            f"Geocoding '{location.name}' - "
            f"Query: '{query}' | "
            f"Parsed metadata: country={country}, city={city}, ISO={iso_code}"
        )
        
        # Get results from ALL sources (with respectful delays between API calls)
        results = {}
        
        # HDX (local database - no delay needed)
        results["hdx"] = self.geocode_hdx_enhanced(location, country)
        
        # ArcGIS API with country optimization
        results["arcgis"] = self.geocode_arcgis(query, country, iso_code)
        time.sleep(0.5)  # Be respectful to APIs
        
        # Google Maps API with region optimization
        results["google"] = self.geocode_google(query, country, iso_code)
        time.sleep(0.5)  # Be respectful to APIs
        
        # Nominatim with country codes optimization
        results["nominatim"] = self.geocode_nominatim_with_fallback(query, country, iso_code)
        
        # Create or update geocoding result
        geocoding_result, created = GeocodingResult.objects.get_or_create(
            location_name=location.name,
            defaults={
                'validation_status': 'pending',
                'parsed_location_data': parsed_location  # Store parsed components
            }
        )

        # Update parsed data if not created
        if not created:
            geocoding_result.parsed_location_data = parsed_location
        
        # Store results from each source
        has_success = False
        for source, data in results.items():
            if data.get("coordinates"):
                lat, lng = data["coordinates"]
                setattr(geocoding_result, f"{source}_lat", lat)
                setattr(geocoding_result, f"{source}_lng", lng)
                setattr(geocoding_result, f"{source}_success", True)
                
                # Store additional source-specific data
                if source == "hdx" and data.get("facility"):
                    geocoding_result.hdx_facility_match = data["facility"]
                elif source == "nominatim":
                    # Store info about which Nominatim was used
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
            # Step 1: Get all HDX facilities
            hdx_facilities = HDXHealthFacility.objects.all()
            
            if not hdx_facilities.exists():
                return {"error": "No HDX facilities loaded in database"}
            
            # Step 1.5: Filter by country if available
            if country:
                country_filtered = hdx_facilities.filter(
                    Q(country__iexact=country) |
                    Q(country__icontains=country) |
                    Q(country__icontains=country.split()[0])  # First word
                )
                if country_filtered.exists():
                    hdx_facilities = country_filtered
                    logger.info(f"HDX: Filtered to {hdx_facilities.count()} facilities in {country}")
            
            # Extract the location part (without country) for facility matching using smart extraction
            _, location_part = self._extract_country_smart(location.name)
            search_name = location_part if location_part else location.name
            
            logger.info(f"HDX: Searching {hdx_facilities.count()} total facilities for '{search_name}'")
            
            # Step 2: Try exact matches first
            exact_matches = hdx_facilities.filter(
                Q(facility_name__iexact=search_name)
            )
            
            if exact_matches.exists():
                facility = exact_matches.first()
                logger.info(f"HDX: EXACT match found - '{facility.facility_name}'")
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
                logger.info(f"HDX: CONTAINS match found - '{facility.facility_name}'")
                return {
                    "coordinates": (facility.hdx_latitude, facility.hdx_longitude),
                    "facility": facility,
                    "match_type": "contains",
                    "confidence": 0.8
                }
            
            # Step 4: Fuzzy matching with multiple strategies (if available)
            if FUZZY_AVAILABLE:
                logger.info(f"HDX: Trying fuzzy matching with multiple strategies...")
                
                # Get all facility names for fuzzy matching
                all_facilities = list(hdx_facilities)
                facility_names = [f.facility_name for f in all_facilities]
                
                # Different matching strategies
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
                        if score > best_score and score >= 65:  # Threshold for fuzzy matching
                            best_match = match_name
                            best_score = score
                            best_strategy = strategy_name
                
                if best_match and best_score >= 65:
                    matched_facility = hdx_facilities.filter(
                        facility_name=best_match
                    ).first()
                    
                    if matched_facility:
                        logger.info(f"HDX: FUZZY match found - '{best_match}' (score: {best_score}%, strategy: {best_strategy})")
                        return {
                            "coordinates": (matched_facility.hdx_latitude, matched_facility.hdx_longitude),
                            "facility": matched_facility,
                            "match_type": "fuzzy",
                            "confidence": best_score / 100.0
                        }
            
            # Step 5: Enhanced containment matching
            search_terms = search_name.lower().split()
            if len(search_terms) > 0:
                logger.info(f"HDX: Trying containment matching with terms: {search_terms}")
                
                for facility in hdx_facilities:
                    facility_name_lower = facility.facility_name.lower()
                    
                    # Check how many search terms are contained in the facility name
                    matching_terms = sum(1 for term in search_terms if term in facility_name_lower)
                    match_ratio = matching_terms / len(search_terms)
                    
                    # If 70% or more of the search terms match, consider it a good match
                    if match_ratio >= 0.7:
                        confidence = 0.6 + (match_ratio * 0.3)  # 60-90% confidence
                        logger.info(f"HDX: CONTAINMENT match found - '{facility.facility_name}' (terms: {matching_terms}/{len(search_terms)}, confidence: {confidence:.1%})")
                        return {
                            "coordinates": (facility.hdx_latitude, facility.hdx_longitude),
                            "facility": facility,
                            "match_type": "containment",
                            "confidence": confidence
                        }
            
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
            
            # Use ISO code if available, otherwise fallback to country mapping
            if iso_code:
                params['sourceCountry'] = iso_code
            elif country and country in self.country_name_to_iso2:
                params['sourceCountry'] = self.country_name_to_iso2[country]

            response = requests.get(url, params=params, timeout=15)
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
            
            # Use ISO code if available, otherwise fallback to country mapping
            if iso_code:
                params["region"] = iso_code.lower()
            elif country:
                params["region"] = self.country_name_to_iso2.get(country, country.lower())

            response = requests.get(url, params=params, timeout=15)
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
        # Try local Nominatim first
        result = self._geocode_nominatim_local(query, country, iso_code)
        if result and result.get("coordinates"):
            result['local_nominatim_used'] = True
            logger.info(f"NOMINATIM (LOCAL): Success")
            return result
        
        # Fallback to public Nominatim
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
            
            # Use shorter timeout for local instance
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
            
            # Use ISO code if available, otherwise fallback to country mapping
            if iso_code:
                params['countrycodes'] = iso_code.lower()
            elif country:
                params['countrycodes'] = self.country_name_to_iso2.get(country, country.lower())
            
            headers = {
                'User-Agent': 'HarmonAIze-Geocoder/1.0 (harmonaize@project.com)'
            }
            
            response = requests.get(url, params=params, headers=headers, timeout=15)
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