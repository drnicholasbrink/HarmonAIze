"""
LLM-powered enhancements for geocoding using Anthropic Claude.

This module provides optional AI-powered improvements to location parsing
and facility matching. All functions gracefully degrade if LLM is unavailable.

Key Features:
- Intelligent location parsing (extract country, city, facility from unstructured text)
- Semantic facility name matching
- Graceful fallback to traditional methods if LLM unavailable
"""

import logging
import json
import math
from typing import Optional, Dict, Tuple
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Check if Claude is available
CLAUDE_AVAILABLE = False
claude_client = None
try:
    import anthropic

    # Configure Claude with API key from settings
    if hasattr(settings, 'ANTHROPIC_API_KEY') and settings.ANTHROPIC_API_KEY:
        claude_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        CLAUDE_AVAILABLE = True
        logger.debug("Claude LLM initialized successfully for geocoding enhancements")
    else:
        logger.warning("Anthropic API key not configured - LLM enhancements disabled")

except ImportError:
    logger.warning("anthropic not installed - LLM enhancements disabled")
except Exception as e:
    logger.error(f"Failed to initialize Claude: {e}")


class GeocodingLLMEnhancer:
    """
    LLM-powered enhancements for geocoding operations.

    This class provides optional AI improvements that gracefully fall back
    to traditional methods when LLM is unavailable or disabled.
    """

    def __init__(self):
        """Initialize the LLM enhancer with Claude models."""
        self.enabled = (
            CLAUDE_AVAILABLE and
            getattr(settings, 'GEOLOCATION_USE_LLM', True)
        )

        self.client = claude_client

        # Model names for different use cases
        # Haiku: fast, cheap operations (parsing, simple matching)
        # Sonnet: complex reasoning (conflict resolution)
        self.model_fast = "claude-haiku-4-5-20251001"
        self.model_reasoning = "claude-sonnet-5"

        if self.enabled:
            logger.debug("Claude Haiku and Sonnet models configured")

    def is_enabled(self) -> bool:
        """Check if LLM enhancements are enabled and available."""
        return self.enabled

    def _strip_markdown_json(self, text: str) -> str:
        """Strip markdown code blocks from LLM response if present."""
        text = text.strip()
        if text.startswith('```'):
            lines = text.split('\n')
            if lines[0].startswith('```'):
                lines = lines[1:]  # Remove ```json or ```
            if lines and lines[-1].strip() == '```':
                lines = lines[:-1]  # Remove closing ```
            text = '\n'.join(lines)
        return text

    def parse_location_structured(self, location_name: str) -> Optional[Dict]:
        """
        Extract structured data from unstructured location name using Claude.

        This is the primary enhancement - turns messy text into clean components.

        Examples:
            Input: "St Mary's Hospital Harare Zimbabwe"
            Output: {
                'facility_name': 'St Mary\'s Hospital',
                'facility_type': 'hospital',
                'city': 'Harare',
                'country': 'Zimbabwe',
                'country_code': 'ZW'
            }

            Input: "General Hosp Chitungwiza ZW"
            Output: {
                'facility_name': 'General Hospital',
                'facility_type': 'hospital',
                'city': 'Chitungwiza',
                'country': 'Zimbabwe',
                'country_code': 'ZW'
            }

        Args:
            location_name: Unstructured location string

        Returns:
            Dict with parsed components, or None if LLM unavailable
        """
        if not self.enabled:
            return None

        # Check cache first (avoid redundant API calls)
        cache_key = f"llm_parse:{location_name}"
        cached_result = cache.get(cache_key)
        if cached_result:
            logger.debug(f"Using cached LLM parse for '{location_name}'")
            return cached_result

        try:
            prompt = f"""Extract structured location information from: "{location_name}"

Return ONLY valid JSON with these exact fields (use null for any field you cannot extract):

{{
    "facility_name": "the main facility/place name, or null if just a city/country",
    "facility_type": "hospital/clinic/health center/medical center/etc, or null",
    "city": "city or town name, or null",
    "district": "district or county, or null",
    "province": "state or province, or null",
    "country": "full country name, or null",
    "country_code": "ISO 2-letter code (ZW, KE, US, etc.), or null"
}}

RULES:
1. Expand abbreviations: "Gen" → "General", "Hosp" → "Hospital", "St" → "Saint"
2. Normalize facility types: "Medical Center" → "hospital", "Clinic" → "clinic"
3. Extract country even if abbreviated: "ZW" → {{"country": "Zimbabwe", "country_code": "ZW"}}
4. If location is just a city/country (no facility), set facility_name to null
5. Be conservative - if unsure, use null

Examples:
"Chitungwiza Hospital Zimbabwe" → {{"facility_name": "Chitungwiza Hospital", "facility_type": "hospital", "country": "Zimbabwe", "country_code": "ZW"}}
"St Mary's in Harare ZW" → {{"facility_name": "Saint Mary's", "city": "Harare", "country": "Zimbabwe", "country_code": "ZW"}}
"Gen Hosp Harare Province" → {{"facility_name": "General Hospital", "facility_type": "hospital", "city": "Harare", "province": "Harare Province"}}
"Harare Zimbabwe" → {{"city": "Harare", "country": "Zimbabwe", "country_code": "ZW", "facility_name": null}}
"""

            response = self.client.messages.create(
                model=self.model_fast,
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )
            result = json.loads(self._strip_markdown_json(response.content[0].text))

            # Validate that we got a dict with expected structure
            if not isinstance(result, dict):
                logger.warning(f"LLM returned non-dict result for '{location_name}'")
                return None

            # Cache successful result for 1 hour
            cache.set(cache_key, result, 3600)

            logger.debug(f"✓ LLM parsed '{location_name}': facility={result.get('facility_name')}, city={result.get('city')}, country={result.get('country')}")
            return result

        except json.JSONDecodeError as e:
            logger.warning(f"LLM returned invalid JSON for '{location_name}': {e}")
            return None
        except Exception as e:
            logger.warning(f"LLM location parsing failed for '{location_name}': {e}")
            return None

    def semantic_facility_similarity(self,
                                     query: str,
                                     candidate: str,
                                     context: Optional[Dict] = None) -> Optional[Dict]:
        """
        Calculate semantic similarity between query and candidate facility names.

        This is better than fuzzy string matching because it understands:
        - Abbreviations: "St Mary's" vs "Saint Mary Hospital"
        - Word order: "General Hospital Chitungwiza" vs "Chitungwiza General Hospital"
        - Additional words: "Hospital" vs "Hospital and Clinic"
        - Semantic equivalence: "Medical Center" vs "Hospital"

        Args:
            query: Query facility name
            candidate: Candidate facility name from database
            context: Optional dict with additional context (city, country, etc.)

        Returns:
            Dict with similarity analysis, or None if LLM unavailable:
            {
                'is_match': bool,
                'confidence': float (0.0-1.0),
                'reasoning': str
            }
        """
        if not self.enabled:
            return None

        # Check cache
        cache_key = f"llm_match:{query}:{candidate}"
        cached_result = cache.get(cache_key)
        if cached_result:
            return cached_result

        try:
            context_info = ""
            if context:
                context_info = f"\nAdditional context: {json.dumps(context)}"

            prompt = f"""Are these facility names referring to the same place?

Query: "{query}"
Candidate: "{candidate}"{context_info}

Consider:
1. Abbreviations (St = Saint, Gen = General, Hosp = Hospital, Med Ctr = Medical Center)
2. Word order variations ("General Hospital X" vs "X General Hospital")
3. Additional descriptive words ("Hospital" vs "Hospital and Clinic" - still same place)
4. Semantic equivalence ("Medical Center" and "Hospital" are similar)
5. Hierarchical names ("X Hospital" vs "X Group of Hospitals" - likely same)

Return JSON:
{{
    "is_match": true or false,
    "confidence": <0.0 to 1.0>,
    "reasoning": "brief explanation in one sentence"
}}

Be strict: Only return is_match=true if you're reasonably confident they're the same place.
"""

            response = self.client.messages.create(
                model=self.model_fast,
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )
            result = json.loads(self._strip_markdown_json(response.content[0].text))

            # Validate result structure
            if not isinstance(result, dict) or 'is_match' not in result or 'confidence' not in result:
                logger.warning(f"LLM returned invalid match result for '{query}' vs '{candidate}'")
                return None

            # Cache for 1 hour
            cache.set(cache_key, result, 3600)

            if result['is_match']:
                logger.debug(f"✓ LLM matched '{query}' → '{candidate}' (confidence: {result['confidence']:.1%})")
            else:
                logger.debug(f"✗ LLM: '{query}' != '{candidate}' (confidence: {result['confidence']:.1%})")

            return result

        except json.JSONDecodeError as e:
            logger.warning(f"LLM returned invalid JSON for similarity check: {e}")
            return None
        except Exception as e:
            logger.warning(f"LLM semantic matching failed: {e}")
            return None

    def _strip_facility_suffix(self, name: str) -> str:
        """
        Strip common facility type suffixes to get the unique identifying name.

        This prevents false matches like "MBAVI HEALTH CENTRE" matching
        "Londuimbali Health Centre" just because they share "Health Centre".
        """
        if not name:
            return name

        suffixes = [
            'district hospital', 'central hospital', 'general hospital',
            'mission hospital', 'rural hospital', 'private hospital',
            'teaching hospital', 'referral hospital',
            'health centre', 'health center', 'health post',
            'medical centre', 'medical center',
            'maternity hospital', 'maternity clinic',
            'community hospital', 'community clinic', 'community health centre',
            'hospital', 'clinic', 'dispensary', 'infirmary',
            'hc', 'hosp', 'med center', 'med centre',
        ]

        name_lower = name.lower().strip()
        core_name = name.strip()

        for suffix in suffixes:
            if name_lower.endswith(suffix):
                core_name = name[:len(name) - len(suffix)].strip().rstrip(' -.,')
                break

        return core_name if core_name and len(core_name) >= 2 else name.strip()

    def find_best_facility_match(self,
                                 query: str,
                                 candidates: list,
                                 max_candidates: int = 10) -> Optional[Tuple[str, float, str]]:
        """
        Find the best matching facility from a list using semantic understanding.

        This combines traditional fuzzy matching (for speed) with LLM reasoning
        for the top candidates. Strips facility type suffixes before matching
        to prevent false matches on common suffixes like "Health Centre".

        Args:
            query: Query facility name
            candidates: List of candidate facility names
            max_candidates: Maximum candidates to evaluate with LLM

        Returns:
            Tuple of (best_match, confidence, reasoning) or None if no good match
        """
        if not self.enabled or not candidates:
            return None

        try:
            from fuzzywuzzy import process

            # Strip suffix from query to get core name
            query_core = self._strip_facility_suffix(query)
            logger.debug(f"LLM matching: query='{query}' -> core='{query_core}'")

            # Build mapping of stripped names to original names
            stripped_to_original = {}
            for candidate in candidates:
                stripped = self._strip_facility_suffix(candidate)
                if stripped not in stripped_to_original:
                    stripped_to_original[stripped] = candidate

            stripped_candidates = list(stripped_to_original.keys())

            # Pre-filter with fuzzy matching on STRIPPED names (cheap and fast)
            top_candidates = process.extract(query_core, stripped_candidates, limit=min(max_candidates, len(stripped_candidates)))

            # Now use LLM to evaluate top candidates semantically
            best_match = None
            best_confidence = 0.0
            best_reasoning = ""

            for stripped_name, fuzzy_score in top_candidates:
                # Skip low fuzzy scores to save API calls and prevent bad matches
                if fuzzy_score < 65:
                    continue

                original_name = stripped_to_original.get(stripped_name, stripped_name)
                llm_result = self.semantic_facility_similarity(query, original_name)

                if llm_result and llm_result['is_match']:
                    # Combine fuzzy score with LLM confidence
                    combined_confidence = (fuzzy_score / 100.0) * 0.3 + llm_result['confidence'] * 0.7

                    if combined_confidence > best_confidence:
                        best_match = original_name
                        best_confidence = combined_confidence
                        best_reasoning = llm_result['reasoning']

            if best_match and best_confidence > 0.75:  # Threshold for accepting match
                logger.debug(f"✓ LLM best match for '{query}': '{best_match}' (confidence: {best_confidence:.1%})")
                return (best_match, best_confidence, best_reasoning)

            return None

        except Exception as e:
            logger.warning(f"LLM best match finding failed: {e}")
            return None

    def resolve_source_conflict(self,
                                location_name: str,
                                coordinates: Dict[str, Tuple[float, float]],
                                reverse_geocoding_results: Dict,
                                parsed_location: Dict) -> Optional[Dict]:
        """
        ENHANCEMENT #1: Use LLM reasoning to resolve conflicts when geocoding sources disagree.

        When multiple sources return coordinates that are far apart (>5km), use AI to
        reason about which source is most reliable given the context.

        Args:
            location_name: Original location query
            coordinates: Dict of {source: (lat, lng)}
            reverse_geocoding_results: Reverse geocoded addresses for each source
            parsed_location: Parsed location components (country, city, etc.)

        Returns:
            Dict with recommended source and reasoning, or None if LLM unavailable
        """
        if not self.enabled or len(coordinates) < 2:
            return None

        # Calculate distances between sources
        sources = list(coordinates.keys())
        max_distance_km = 0
        for i in range(len(sources)):
            for j in range(i + 1, len(sources)):
                dist = self._haversine_distance(
                    coordinates[sources[i]][0], coordinates[sources[i]][1],
                    coordinates[sources[j]][0], coordinates[sources[j]][1]
                )
                max_distance_km = max(max_distance_km, dist)

        # Only use LLM if there's significant disagreement
        conflict_threshold = getattr(settings, 'GEOLOCATION_LLM_CONFLICT_THRESHOLD_KM', 5.0)
        if max_distance_km < conflict_threshold:
            return None

        try:
            # Build detailed context about each source
            sources_info = []
            for source, (lat, lng) in coordinates.items():
                reverse_info = reverse_geocoding_results.get(source, {})
                reverse_address = reverse_info.get('address', 'No address')
                name_similarity = reverse_info.get('similarity_score', 0.0)

                sources_info.append({
                    'source': source.upper(),
                    'coordinates': f"{lat:.6f}, {lng:.6f}",
                    'reverse_address': reverse_address,
                    'name_similarity_percent': f"{name_similarity*100:.1f}%"
                })

            # Calculate all pairwise distances
            distances = []
            for i in range(len(sources)):
                for j in range(i + 1, len(sources)):
                    dist = self._haversine_distance(
                        coordinates[sources[i]][0], coordinates[sources[i]][1],
                        coordinates[sources[j]][0], coordinates[sources[j]][1]
                    )
                    distances.append({
                        'pair': f"{sources[i].upper()} ↔ {sources[j].upper()}",
                        'distance_km': f"{dist:.2f}"
                    })

            prompt = f"""Location query: "{location_name}"

Multiple geocoding sources returned CONFLICTING coordinates (max distance: {max_distance_km:.1f} km).

SOURCES:
{json.dumps(sources_info, indent=2)}

DISTANCES BETWEEN SOURCES:
{json.dumps(distances, indent=2)}

PARSED LOCATION DATA:
Country: {parsed_location.get('country', 'Unknown')}
City: {parsed_location.get('admin_level_2', 'Unknown')}
Facility Type: {parsed_location.get('facility', 'Unknown')}

CONTEXT ABOUT SOURCES:
- HDX: Authoritative health facility database (high reliability for hospitals/clinics)
- GOOGLE: Excellent for businesses and POI (very reliable for facilities)
- ARCGIS: Good for infrastructure and administrative boundaries
- NOMINATIM/OSM: Community-driven (variable quality)

ANALYSIS NEEDED:
1. Which sources agree closely (within 1-2km)?
2. Are there clear outliers (>10km from others)?
3. Which reverse geocoded addresses best match the original query?
4. Given the location type, which source is typically most reliable?
5. Do any coordinates seem geographically implausible?

Return JSON:
{{
    "recommended_source": "HDX|GOOGLE|ARCGIS|NOMINATIM",
    "confidence": <0.0-1.0>,
    "reasoning": "2-3 sentences explaining why this source is most reliable",
    "red_flags": ["list any concerns about the data"],
    "agreement_level": "high|medium|low",
    "outlier_sources": ["list sources that are clear outliers"]
}}
"""

            response = self.client.messages.create(
                model=self.model_reasoning,  # Use Sonnet for complex reasoning
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )
            llm_decision = json.loads(self._strip_markdown_json(response.content[0].text))

            logger.debug(f"✓ LLM Conflict Resolution for '{location_name}':")
            logger.debug(f"  Recommended: {llm_decision['recommended_source']} (confidence: {llm_decision['confidence']:.1%})")
            logger.debug(f"  Reasoning: {llm_decision['reasoning']}")

            # Cache the decision
            cache_key = f"llm_conflict:{location_name}:{max_distance_km:.1f}"
            cache.set(cache_key, llm_decision, 3600)

            return llm_decision

        except json.JSONDecodeError as e:
            logger.warning(f"LLM returned invalid JSON for conflict resolution: {e}")
            return None
        except Exception as e:
            logger.warning(f"LLM conflict resolution failed: {e}")
            return None

    def semantic_address_similarity(self,
                                    query_name: str,
                                    reverse_address: str,
                                    parsed_query: Optional[Dict] = None) -> Optional[Dict]:
        """
        ENHANCEMENT #2: Use semantic understanding to check if reverse address matches query.

        Better than fuzzy matching because it understands:
        - Geographic hierarchies: "Harare" implicitly includes "Harare Central District"
        - Facility naming: "General Hospital" could be "X General Hospital and Clinic"
        - Context: "Near CBD" vs "Central Business District"

        Args:
            query_name: Original location query
            reverse_address: Reverse geocoded address to compare
            parsed_query: Optional parsed components of the query

        Returns:
            Dict with match quality and reasoning, or None if LLM unavailable
        """
        if not self.enabled:
            return None

        # Check cache
        cache_key = f"llm_addr_sim:{query_name}:{reverse_address[:50]}"
        cached_result = cache.get(cache_key)
        if cached_result:
            return cached_result

        try:
            context_info = ""
            if parsed_query:
                context_info = f"""
Parsed query components:
- Facility: {parsed_query.get('facility', 'Unknown')}
- City: {parsed_query.get('admin_level_2', 'Unknown')}
- Country: {parsed_query.get('country', 'Unknown')}
"""

            prompt = f"""Query location: "{query_name}"
Reverse geocoded address: "{reverse_address}"
{context_info}

Does the reverse geocoded address match the query location?

MATCHING RULES:
1. Exact matches (highest confidence): Same facility name + same location
2. Hierarchical matches: Query is "Harare", address is "123 Main St, Harare, Zimbabwe"
3. Semantic equivalence: "St Mary's" vs "Saint Mary Hospital" (same place)
4. Partial matches: Query is "General Hospital", address adds context like "General Hospital and Clinic"
5. Context clues: "near CBD" means "Central Business District"

Return JSON:
{{
    "match_quality": "excellent|good|fair|poor|none",
    "similarity_score": <0.0-1.0>,
    "confidence": <0.0-1.0>,
    "reasoning": "1-2 sentences explaining the match assessment",
    "matched_components": ["list which parts of query matched address"]
}}

Examples:
- Query: "Parirenyatwa Hospital Harare" | Address: "Parirenyatwa Group of Hospitals, Mazowe Street, Harare"
  → match_quality: "excellent", similarity_score: 0.95

- Query: "General Hospital" | Address: "Chitungwiza General Hospital, Harare Province, Zimbabwe"
  → match_quality: "good", similarity_score: 0.85 (hierarchical match)

- Query: "St Mary's Clinic" | Address: "Government Office, Harare District"
  → match_quality: "none", similarity_score: 0.10
"""

            response = self.client.messages.create(
                model=self.model_fast,
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )
            result = json.loads(self._strip_markdown_json(response.content[0].text))

            # Cache for 1 hour
            cache.set(cache_key, result, 3600)

            logger.debug(f"✓ LLM address similarity: '{query_name}' vs '{reverse_address[:50]}...' = {result['similarity_score']:.1%} ({result['match_quality']})")

            return result

        except json.JSONDecodeError as e:
            logger.warning(f"LLM returned invalid JSON for address similarity: {e}")
            return None
        except Exception as e:
            logger.warning(f"LLM address similarity check failed: {e}")
            return None

    # ISO 3-letter to full country name mapping for sanity checks
    ISO_COUNTRY_NAMES = {
        'ETH': 'Ethiopia', 'ZWE': 'Zimbabwe', 'KEN': 'Kenya', 'TZA': 'Tanzania',
        'UGA': 'Uganda', 'RWA': 'Rwanda', 'BDI': 'Burundi', 'MWI': 'Malawi',
        'ZMB': 'Zambia', 'MOZ': 'Mozambique', 'ZAF': 'South Africa', 'NAM': 'Namibia',
        'BWA': 'Botswana', 'LSO': 'Lesotho', 'SWZ': 'Eswatini', 'AGO': 'Angola',
        'COD': 'Democratic Republic of the Congo', 'COG': 'Republic of the Congo',
        'GAB': 'Gabon', 'CMR': 'Cameroon', 'NGA': 'Nigeria', 'GHA': 'Ghana',
        'CIV': 'Ivory Coast', 'SEN': 'Senegal', 'MLI': 'Mali', 'BFA': 'Burkina Faso',
        'NER': 'Niger', 'TCD': 'Chad', 'SDN': 'Sudan', 'SSD': 'South Sudan',
        'EGY': 'Egypt', 'LBY': 'Libya', 'TUN': 'Tunisia', 'DZA': 'Algeria',
        'MAR': 'Morocco', 'MRT': 'Mauritania', 'SOM': 'Somalia', 'DJI': 'Djibouti',
        'ERI': 'Eritrea',
    }

    def _normalize_country_name(self, country: str) -> str:
        """Normalize ISO country codes to full country names for better LLM matching."""
        if not country:
            return 'Unknown'

        # Check if it's an ISO 3-letter code
        country_upper = country.upper().strip()
        if country_upper in self.ISO_COUNTRY_NAMES:
            return self.ISO_COUNTRY_NAMES[country_upper]

        # Already a full name
        return country

    def contextual_sanity_check(self,
                               location_name: str,
                               coordinates: Dict[str, Tuple[float, float]],
                               parsed_location: Dict,
                               reverse_geocoding_results: Dict) -> Optional[Dict]:
        """
        ENHANCEMENT #3: Check if coordinates make sense given the location context.

        Catches major errors like:
        - Coordinates in wrong country ("Harare Hospital" → coordinates in South Africa)
        - Wrong location type (hospital coordinates point to a lake)
        - Implausible locations (major city hospital in remote wilderness)

        Args:
            location_name: Original location query
            coordinates: Dict of {source: (lat, lng)}
            parsed_location: Parsed location components
            reverse_geocoding_results: Reverse geocoded addresses

        Returns:
            Dict with sanity check results, or None if LLM unavailable
        """
        if not self.enabled or not coordinates:
            return None

        try:
            # Normalize country name (convert ISO codes like "ETH" to "Ethiopia")
            raw_country = parsed_location.get('country', 'Unknown')
            normalized_country = self._normalize_country_name(raw_country)

            # Detect if this is a facility search or location search
            facility_keywords = ['hospital', 'clinic', 'health', 'medical', 'dispensary', 'pharmacy']
            query_lower = location_name.lower()
            is_facility_search = any(kw in query_lower for kw in facility_keywords)
            search_type = 'facility' if is_facility_search else 'location/admin_boundary'

            # Build context about what we expected vs what we got
            expected = {
                'query': location_name,
                'search_type': search_type,
                'parsed_country': normalized_country,
                'parsed_city': parsed_location.get('admin_level_2', 'Unknown'),
                'parsed_facility': parsed_location.get('facility', 'Unknown') if is_facility_search else 'N/A (location search)'
            }

            # Analyze reverse geocoding results
            reverse_summary = []
            for source, result in reverse_geocoding_results.items():
                coords = coordinates.get(source)
                if coords:
                    lat, lng = coords
                    reverse_summary.append({
                        'source': source.upper(),
                        'coordinates': f"{lat:.4f}, {lng:.4f}",
                        'reverse_address': result.get('address', 'No address')[:200]
                    })

            prompt = f"""Perform a sanity check on these geocoding results.

ORIGINAL QUERY: "{location_name}"

EXPECTED LOCATION:
{json.dumps(expected, indent=2)}

GEOCODING RESULTS:
{json.dumps(reverse_summary, indent=2)}

SANITY CHECKS NEEDED:
1. Does AT LEAST ONE source show coordinates in the expected COUNTRY? (If yes, PASS)
2. Account for spelling variations in city names (Hosaina=Hossana, Harare=Salisbury, etc.)
3. ONLY check facility type if query clearly mentions hospital/clinic/etc. Location queries like "Arada, Ethiopia" don't need facility matching.
4. Are coordinates clearly wrong? (ocean, wrong continent) - this is CRITICAL severity
5. If sources disagree on location, that's OK as long as at least one is in the right country

IMPORTANT RULES:
- If the query contains ISO country codes (ETH, ZWE, KEN), these mean Ethiopia, Zimbabwe, Kenya etc.
- Comma-separated queries like "Arada, ETH, Hossana" are location searches, NOT facility searches
- Be LENIENT - only fail for clear geographic errors (wrong country/continent)
- Minor spelling differences should NOT cause failure
- If most sources agree on the general area, PASS the check

Return JSON:
{{
    "passes_sanity_check": true or false,
    "confidence": <0.0-1.0>,
    "issues_found": ["list specific problems, or empty array if all good"],
    "reasoning": "2-3 sentences explaining your assessment",
    "severity": "none|minor|major|critical"
}}

SEVERITY GUIDE:
- "none": All good, coordinates clearly in expected location
- "minor": Small discrepancies but generally correct area
- "major": Only use if coordinates are in WRONG COUNTRY (not just different city)
- "critical": Coordinates on wrong CONTINENT or in ocean

Examples:
- Query: "Arada, ETH, Hossana" | Address shows "Hosaina, Ethiopia" → passes: true (Hosaina=Hossana, ETH=Ethiopia)
- Query: "Harare Hospital Zimbabwe" | All addresses in Zimbabwe → passes: true
- Query: "Nairobi Clinic Kenya" | Addresses show "South Africa" → passes: false, severity: "critical"
- Query: "General Hospital" | Addresses vague but in plausible country → passes: true
"""

            response = self.client.messages.create(
                model=self.model_fast,
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )
            result = json.loads(self._strip_markdown_json(response.content[0].text))

            if not result['passes_sanity_check']:
                logger.warning(f"⚠ LLM Sanity Check FAILED for '{location_name}':")
                logger.warning(f"  Issues: {', '.join(result['issues_found'])}")
                logger.warning(f"  Severity: {result['severity']}")
            else:
                logger.debug(f"✓ LLM Sanity Check PASSED for '{location_name}' (confidence: {result['confidence']:.1%})")

            return result

        except json.JSONDecodeError as e:
            logger.warning(f"LLM returned invalid JSON for sanity check: {e}")
            return None
        except Exception as e:
            logger.warning(f"LLM sanity check failed: {e}")
            return None

    def explain_validation_detailed(self,
                                   validation_result,
                                   include_technical: bool = False) -> str:
        """
        ENHANCEMENT #4: Generate natural language explanation of validation result.

        Creates user-friendly explanations that are much clearer than technical scores.

        Args:
            validation_result: ValidationResult instance
            include_technical: Whether to include technical details

        Returns:
            Human-readable explanation string
        """
        if not self.enabled:
            # Fallback to simple template
            score = validation_result.confidence_score
            if score >= 0.8:
                return f"High confidence result ({score:.0%}). Safe to approve."
            elif score >= 0.6:
                return f"Medium confidence result ({score:.0%}). Quick review recommended."
            else:
                return f"Low confidence result ({score:.0%}). Manual verification needed."

        try:
            # Check if client is initialized
            if not self.client:
                logger.error("LLM client not initialized - cannot generate explanation")
                score = validation_result.confidence_score
                return f"Confidence: {score:.0%}. {'Safe to approve' if score >= 0.8 else 'Review recommended' if score >= 0.6 else 'Manual verification needed'}."

            metadata = validation_result.validation_metadata or {}
            geocoding_result = validation_result.geocoding_result

            # Build comprehensive context
            context = {
                'location': geocoding_result.location_name,
                'confidence_percent': f"{validation_result.confidence_score*100:.0f}%",
                'recommended_source': validation_result.recommended_source.upper() if validation_result.recommended_source else 'None',
                'status': validation_result.validation_status,
                'sources_count': metadata.get('sources_count', 0),
                'max_distance_km': metadata.get('cluster_analysis', {}).get('max_distance_km', 0)
            }

            # Add LLM analysis if available
            llm_conflict = metadata.get('llm_conflict_resolution', {})
            llm_sanity = metadata.get('llm_sanity_check', {})

            additional_context = ""
            if llm_conflict:
                additional_context += f"\n- LLM Conflict Resolution: {llm_conflict.get('reasoning', '')}"
            if llm_sanity:
                additional_context += f"\n- Sanity Check: {llm_sanity.get('reasoning', '')}"
                if not llm_sanity.get('passes_sanity_check'):
                    additional_context += f"\n  ⚠ Issues: {', '.join(llm_sanity.get('issues_found', []))}"

            technical_details = ""
            if include_technical:
                technical_details = f"""

Technical details:
- Sources: {', '.join(metadata.get('individual_scores', {}).keys())}
- Max distance between sources: {context['max_distance_km']:.2f} km
- Validation method: {metadata.get('validation_method', 'unknown')}
"""

            prompt = f"""Generate a clear, friendly explanation for this geocoding validation result.

LOCATION: "{context['location']}"
CONFIDENCE: {context['confidence_percent']}
RECOMMENDED SOURCE: {context['recommended_source']}
STATUS: {context['status']}
SOURCES FOUND: {context['sources_count']}
MAX DISTANCE: {context['max_distance_km']:.2f} km

ADDITIONAL ANALYSIS:{additional_context}
{technical_details}

Write a 2-4 sentence explanation that:
1. States whether the user should trust and approve this result
2. Explains WHY the confidence is at this level
3. Mentions any specific concerns or reasons for caution
4. Provides actionable next steps

Use friendly, non-technical language. Be direct and helpful.
Return ONLY the explanation text (no JSON, no markdown formatting).
"""

            logger.info(f"Generating LLM explanation for '{geocoding_result.location_name}'...")

            # Use Haiku model for fast text response
            response = self.client.messages.create(
                model=self.model_fast,
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )
            explanation = response.content[0].text.strip()

            logger.info(f"✓ Generated validation explanation for '{geocoding_result.location_name}'")

            return explanation

        except Exception as e:
            logger.error(f"LLM explanation generation failed for '{validation_result.geocoding_result.location_name}': {type(e).__name__}: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            # Fallback
            score = validation_result.confidence_score
            return f"Confidence: {score:.0%}. {'Safe to approve' if score >= 0.8 else 'Review recommended' if score >= 0.6 else 'Manual verification needed'}."

    def _haversine_distance(self, lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        """Calculate distance between two points in kilometers using Haversine formula."""
        R = 6371  # Earth's radius in km

        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        dlat = math.radians(lat2 - lat1)
        dlng = math.radians(lng2 - lng1)

        a = math.sin(dlat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlng/2)**2
        c = 2 * math.asin(math.sqrt(a))

        return R * c


# Singleton instance
_llm_enhancer = None

def get_llm_enhancer() -> GeocodingLLMEnhancer:
    """Get or create the singleton LLM enhancer instance."""
    global _llm_enhancer
    if _llm_enhancer is None:
        _llm_enhancer = GeocodingLLMEnhancer()
    return _llm_enhancer
