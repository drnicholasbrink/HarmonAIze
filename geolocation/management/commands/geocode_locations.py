# geolocation/management/commands/geocode_locations.py
import os
import time
import requests
from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from django.db.models import Q
from geolocation.models import ValidationDataset, GeocodingResult, HDXHealthFacility
from core.models import Location

# Try to import fuzzy matching - install with: pip install fuzzywuzzy python-levenshtein
try:
    from fuzzywuzzy import fuzz, process
    FUZZY_AVAILABLE = True
except ImportError:
    FUZZY_AVAILABLE = False
    print("Warning: fuzzywuzzy not available. Install with: pip install fuzzywuzzy python-levenshtein")

# ISO2 country codes for African countries
COUNTRY_NAME_TO_ISO2 = {
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


class Command(BaseCommand):
    help = 'Geocode locations using validated dataset first, then all sources (HDX, ArcGIS, Google, Nominatim) for validation'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Local Nominatim configuration
        self.local_nominatim_url = getattr(settings, 'LOCAL_NOMINATIM_URL', 'http://nominatim:8080')
        self.public_nominatim_url = 'https://nominatim.openstreetmap.org'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Limit the number of locations to process'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force re-geocoding even if results already exist'
        )
        parser.add_argument(
            '--location-name',
            type=str,
            help='Geocode a specific location by name'
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting geocoding process..."))

        # Test local Nominatim connection
        self._test_local_nominatim_connection()

        # Get locations without coordinates
        if options['location_name']:
            locations = Location.objects.filter(name__icontains=options['location_name'])
        else:
            locations = Location.objects.filter(latitude__isnull=True, longitude__isnull=True)
        
        if options['limit']:
            locations = locations[:options['limit']]
        
        if not locations.exists():
            self.stdout.write(self.style.WARNING("No locations need geocoding."))
            return

        successful = 0
        failed = 0
        used_validated = 0

        self.stdout.write(f"Processing {locations.count()} locations...")

        for location in locations:
            self.stdout.write(f"Processing: {location.name}")
            
            # Step 1: Check validated dataset first (ONLY pre-validated coordinates)
            validated_result = self.check_validated_dataset(location)
            if validated_result:
                with transaction.atomic():
                    location.latitude = validated_result.final_lat
                    location.longitude = validated_result.final_long
                    location.save()
                
                used_validated += 1
                successful += 1
                self.stdout.write(
                    self.style.SUCCESS(f"✓ Used validated data for: {location.name}")
                )
                continue
            
            # Step 2: Check if we already have geocoding results
            if not options['force']:
                existing_result = GeocodingResult.objects.filter(
                    location_name__iexact=location.name
                ).first()
                
                if existing_result and existing_result.has_any_results:
                    self.stdout.write(
                        self.style.WARNING(f"⚠ Geocoding result already exists for: {location.name}")
                    )
                    successful += 1
                    continue
            
            # Step 3: Perform geocoding using ALL sources (HDX + APIs)
            success = self.geocode_location(location)
            if success:
                successful += 1
                self.stdout.write(
                    self.style.SUCCESS(f"✓ Successfully geocoded: {location.name}")
                )
            else:
                failed += 1
                self.stdout.write(
                    self.style.ERROR(f"✗ Failed to geocode: {location.name}")
                )
            
            # Be respectful to APIs
            time.sleep(0.5)

        # Final summary
        self.stdout.write(
            self.style.SUCCESS(
                f"\nGeocoding completed:"
                f"\n  ✓ Successful: {successful}"
                f"\n  ✗ Failed: {failed}"
                f"\n  📋 From validated dataset: {used_validated}"
                f"\n  🔍 New geocoding results saved for validation"
            )
        )

    def _test_local_nominatim_connection(self):
        """Test connection to local Nominatim instance."""
        try:
            response = requests.get(f"{self.local_nominatim_url}/status", timeout=5)
            if response.status_code == 200:
                self.stdout.write(self.style.SUCCESS(f"✓ Local Nominatim is available at {self.local_nominatim_url}"))
                return True
        except requests.exceptions.ConnectionError:
            self.stdout.write(self.style.WARNING(f"⚠ Local Nominatim not available at {self.local_nominatim_url}, will use public API"))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"⚠ Local Nominatim connection test failed: {e}"))
        
        return False

    def check_validated_dataset(self, location):
        """Check if location exists in validated dataset (pre-validated coordinates only)."""
        # Try exact match first
        result = ValidationDataset.objects.filter(
            location_name__iexact=location.name,
            country__iexact=location.country or ''
        ).first()
        
        if result:
            return result
        
        # Try without country if no exact match
        if location.country:
            result = ValidationDataset.objects.filter(
                location_name__iexact=location.name
            ).first()
        
        return result

    def geocode_location(self, location):
        """Geocode a single location using ALL sources: HDX + APIs."""
        query = f"{location.name}, {location.country}" if location.country else location.name
        
        self.stdout.write(f"  Geocoding: {query}")
        
        # Get results from ALL sources (HDX is just another source)
        results = {
            "hdx": self.geocode_hdx_enhanced(location),
            "arcgis": self.geocode_arcgis(query, location.country),
            "google": self.geocode_google(query, location.country),
            "nominatim": self.geocode_nominatim_with_fallback(query, location.country),
        }
        
        # Create or update geocoding result
        geocoding_result, created = GeocodingResult.objects.get_or_create(
            location_name=location.name,
            defaults={'validation_status': 'pending'}
        )
        
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
                    # FIXED: Store info about which Nominatim was used
                    raw_response = data.get("raw_response", [])
                    geocoding_result.nominatim_raw_response = {
                        'results': raw_response if isinstance(raw_response, list) else [raw_response],
                        'local_nominatim_used': data.get('local_nominatim_used', False)
                    }
                elif source != "hdx":
                    setattr(geocoding_result, f"{source}_raw_response", data.get("raw_response"))
                
                has_success = True
                local_indicator = " (LOCAL)" if source == "nominatim" and data.get('local_nominatim_used') else ""
                self.stdout.write(f"    ✓ {source.upper()}{local_indicator}: {lat:.6f}, {lng:.6f}")
            else:
                setattr(geocoding_result, f"{source}_error", data.get("error", "Unknown error"))
                setattr(geocoding_result, f"{source}_success", False)
                self.stdout.write(f"    ✗ {source.upper()}: {data.get('error', 'Failed')}")
        
        geocoding_result.save()
        return has_success

    def geocode_hdx_enhanced(self, location):
        """
        Enhanced HDX geocoding with comprehensive fuzzy matching.
        This should match "chitungwiza hospital" with "Chitungwiza Central Hospital"
        """
        if not location.name:
            return {"error": "No location name provided"}
        
        # Step 1: Get all HDX facilities
        hdx_facilities = HDXHealthFacility.objects.all()
        
        if not hdx_facilities.exists():
            return {"error": "No HDX facilities loaded in database"}
        
        self.stdout.write(f"    📍 HDX: Searching {hdx_facilities.count()} total facilities")
        
        # Step 2: Filter by country if available
        country_facilities = hdx_facilities
        if location.country:
            # Try different country name variations
            country_filters = [
                Q(country__iexact=location.country),
                Q(country__icontains=location.country),
                Q(country__icontains=location.country.split()[0])  # First word
            ]
            
            for country_filter in country_filters:
                filtered_facilities = hdx_facilities.filter(country_filter)
                if filtered_facilities.exists():
                    country_facilities = filtered_facilities
                    self.stdout.write(f"    📍 HDX: Found {country_facilities.count()} facilities in {location.country}")
                    break
        
        # Step 3: Try EXACT matching first (case insensitive)
        exact_matches = country_facilities.filter(facility_name__iexact=location.name)
        if exact_matches.exists():
            facility = exact_matches.first()
            self.stdout.write(f"    📍 HDX: EXACT match found - {facility.facility_name}")
            return {
                "coordinates": (facility.hdx_latitude, facility.hdx_longitude),
                "facility": facility,
                "match_type": "exact",
                "confidence": 1.0
            }
        
        # Step 4: Enhanced fuzzy matching
        if FUZZY_AVAILABLE and country_facilities.exists():
            facility_names = list(country_facilities.values_list('facility_name', flat=True))
            facility_names = [name for name in facility_names if name and name.strip()]
            
            if facility_names:
                self.stdout.write(f"    📍 HDX: Running fuzzy matching on {len(facility_names)} facility names")
                
                # Use multiple fuzzy matching strategies
                strategies = [
                    (fuzz.token_sort_ratio, "token_sort"),
                    (fuzz.token_set_ratio, "token_set"), 
                    (fuzz.partial_ratio, "partial"),
                    (fuzz.ratio, "ratio")
                ]
                
                best_match = None
                best_score = 0
                best_strategy = None
                
                for scorer, strategy_name in strategies:
                    matches = process.extract(
                        location.name, 
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
                    matched_facility = country_facilities.filter(
                        facility_name=best_match
                    ).first()
                    
                    if matched_facility:
                        self.stdout.write(f"    📍 HDX: FUZZY match found - '{best_match}' (score: {best_score}%, strategy: {best_strategy})")
                        return {
                            "coordinates": (matched_facility.hdx_latitude, matched_facility.hdx_longitude),
                            "facility": matched_facility,
                            "match_type": "fuzzy",
                            "confidence": best_score / 100.0
                        }
        
        # Step 5: Enhanced containment matching
        search_terms = location.name.lower().split()
        if len(search_terms) > 0:
            self.stdout.write(f"    📍 HDX: Trying containment matching with terms: {search_terms}")
            
            for facility in country_facilities:
                facility_name_lower = facility.facility_name.lower()
                
                # Check how many search terms are contained in the facility name
                matching_terms = sum(1 for term in search_terms if term in facility_name_lower)
                match_ratio = matching_terms / len(search_terms)
                
                # If 70% or more of the search terms match, consider it a good match
                if match_ratio >= 0.7:
                    confidence = 0.6 + (match_ratio * 0.3)  # 60-90% confidence
                    self.stdout.write(f"    📍 HDX: CONTAINMENT match found - '{facility.facility_name}' (terms: {matching_terms}/{len(search_terms)}, confidence: {confidence:.1%})")
                    return {
                        "coordinates": (facility.hdx_latitude, facility.hdx_longitude),
                        "facility": facility,
                        "match_type": "containment",
                        "confidence": confidence
                    }
        
        return {"error": f"No HDX facility match found for '{location.name}' in {location.country or 'any country'}"}

    def geocode_arcgis(self, query, country):
        """Geocode using ArcGIS API."""
        try:
            url = 'https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates'
            params = {
                'f': 'json',
                'singleLine': query,
                'outFields': 'Match_addr',
                'maxLocations': 1
            }
            if country and country in COUNTRY_NAME_TO_ISO2:
                params['sourceCountry'] = COUNTRY_NAME_TO_ISO2[country]

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

    def geocode_google(self, query, country):
        """Geocode using Google Maps API."""
        try:
            key = getattr(settings, "GOOGLE_GEOCODING_API_KEY", None) or os.getenv("GOOGLE_GEOCODING_API_KEY")
            if not key:
                return {"error": "Missing Google API key"}

            url = "https://maps.googleapis.com/maps/api/geocode/json"
            params = {"address": query, "key": key}
            
            if country:
                params["region"] = COUNTRY_NAME_TO_ISO2.get(country, country.lower())

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

    def geocode_nominatim_with_fallback(self, query, country):
        """Geocode using Nominatim with local fallback to public API."""
        # Try local Nominatim first
        result = self._geocode_nominatim_local(query, country)
        if result and result.get("coordinates"):
            result['local_nominatim_used'] = True
            self.stdout.write(f"    📍 NOMINATIM (LOCAL): Success")
            return result
        
        # Fallback to public Nominatim
        self.stdout.write(f"    📍 NOMINATIM: Local failed, trying public API...")
        result = self._geocode_nominatim_public(query, country)
        if result:
            result['local_nominatim_used'] = False
        
        return result

    def _geocode_nominatim_local(self, query, country):
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
                params['countrycodes'] = COUNTRY_NAME_TO_ISO2.get(country, country.lower())
            
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

    def _geocode_nominatim_public(self, query, country):
        """Geocode using public Nominatim API."""
        try:
            url = f'{self.public_nominatim_url}/search'
            params = {
                'q': query, 
                'format': 'json', 
                'limit': 1, 
                'addressdetails': 1,
                'dedupe': 1
            }
            if country:
                params['countrycodes'] = COUNTRY_NAME_TO_ISO2.get(country, country.lower())
            
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
            return {"error": "Request timeout"}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}"}
        except Exception as e:
            return {"error": f"Unexpected error: {str(e)}"}