"""
LLM-powered enhancements for geocoding using Google Gemini.

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

# Check if Gemini is available
GEMINI_AVAILABLE = False
try:
    import google.generativeai as genai

    # Configure Gemini with API key from settings
    if hasattr(settings, 'GEMINI_API_KEY') and settings.GEMINI_API_KEY:
        genai.configure(api_key=settings.GEMINI_API_KEY)
        GEMINI_AVAILABLE = True
        logger.info("✓ Gemini LLM initialized successfully for geocoding enhancements")
    else:
        logger.warning("Gemini API key not configured - LLM enhancements disabled")

except ImportError:
    logger.warning("google-generativeai not installed - LLM enhancements disabled")
except Exception as e:
    logger.error(f"Failed to initialize Gemini: {e}")


class GeocodingLLMEnhancer:
    """
    LLM-powered enhancements for geocoding operations.

    This class provides optional AI improvements that gracefully fall back
    to traditional methods when LLM is unavailable or disabled.
    """

    def __init__(self):
        """Initialize the LLM enhancer with Gemini models."""
        self.enabled = (
            GEMINI_AVAILABLE and
            getattr(settings, 'GEOLOCATION_USE_LLM', True)
        )

        self.model_flash = None
        self.model_pro = None

        if self.enabled:
            try:
                # Flash model for fast, cheap operations (parsing, simple matching)
                self.model_flash = genai.GenerativeModel(
                    'gemini-flash-latest',  # Updated to use stable latest version
                    generation_config=genai.GenerationConfig(
                        temperature=0.1,  # Low temperature for structured output
                        max_output_tokens=500,
                        response_mime_type="application/json"  # Force JSON response
                    )
                )

                # Pro model for complex reasoning (conflict resolution)
                self.model_pro = genai.GenerativeModel(
                    'gemini-pro-latest',  # Updated to use stable latest version
                    generation_config=genai.GenerationConfig(
                        temperature=0.2,
                        max_output_tokens=1000,
                        response_mime_type="application/json"
                    )
                )

                logger.info("✓ Gemini Flash and Pro models initialized")

            except Exception as e:
                logger.error(f"Failed to initialize Gemini models: {e}")
                self.enabled = False

    def is_enabled(self) -> bool:
        """Check if LLM enhancements are enabled and available."""
        return self.enabled

    def parse_location_structured(self, location_name: str) -> Optional[Dict]:
        """
        Extract structured data from unstructured location name using Gemini.

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

            response = self.model_flash.generate_content(prompt)
            result = json.loads(response.text)

            # Validate that we got a dict with expected structure
            if not isinstance(result, dict):
                logger.warning(f"LLM returned non-dict result for '{location_name}'")
                return None

            # Cache successful result for 1 hour
            cache.set(cache_key, result, 3600)

            logger.info(f"✓ LLM parsed '{location_name}': facility={result.get('facility_name')}, city={result.get('city')}, country={result.get('country')}")
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

            response = self.model_flash.generate_content(prompt)
            result = json.loads(response.text)

            # Validate result structure
            if not isinstance(result, dict) or 'is_match' not in result or 'confidence' not in result:
                logger.warning(f"LLM returned invalid match result for '{query}' vs '{candidate}'")
                return None

            # Cache for 1 hour
            cache.set(cache_key, result, 3600)

            if result['is_match']:
                logger.info(f"✓ LLM matched '{query}' → '{candidate}' (confidence: {result['confidence']:.1%})")
            else:
                logger.debug(f"✗ LLM: '{query}' != '{candidate}' (confidence: {result['confidence']:.1%})")

            return result

        except json.JSONDecodeError as e:
            logger.warning(f"LLM returned invalid JSON for similarity check: {e}")
            return None
        except Exception as e:
            logger.warning(f"LLM semantic matching failed: {e}")
            return None

    def find_best_facility_match(self,
                                 query: str,
                                 candidates: list,
                                 max_candidates: int = 10) -> Optional[Tuple[str, float, str]]:
        """
        Find the best matching facility from a list using semantic understanding.

        This combines traditional fuzzy matching (for speed) with LLM reasoning
        for the top candidates.

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
            # Pre-filter with fuzzy matching (cheap and fast)
            from fuzzywuzzy import process
            top_candidates = process.extract(query, candidates, limit=min(max_candidates, len(candidates)))

            # Now use LLM to evaluate top candidates semantically
            best_match = None
            best_confidence = 0.0
            best_reasoning = ""

            for candidate_name, fuzzy_score in top_candidates:
                # Skip very low fuzzy scores to save API calls
                if fuzzy_score < 50:
                    continue

                llm_result = self.semantic_facility_similarity(query, candidate_name)

                if llm_result and llm_result['is_match']:
                    # Combine fuzzy score with LLM confidence
                    combined_confidence = (fuzzy_score / 100.0) * 0.3 + llm_result['confidence'] * 0.7

                    if combined_confidence > best_confidence:
                        best_match = candidate_name
                        best_confidence = combined_confidence
                        best_reasoning = llm_result['reasoning']

            if best_match and best_confidence > 0.6:  # Threshold for accepting match
                logger.info(f"✓ LLM best match for '{query}': '{best_match}' (confidence: {best_confidence:.1%})")
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

            response = self.model_pro.generate_content(prompt)  # Use Pro for complex reasoning
            llm_decision = json.loads(response.text)

            logger.info(f"✓ LLM Conflict Resolution for '{location_name}':")
            logger.info(f"  Recommended: {llm_decision['recommended_source']} (confidence: {llm_decision['confidence']:.1%})")
            logger.info(f"  Reasoning: {llm_decision['reasoning']}")

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

            response = self.model_flash.generate_content(prompt)
            result = json.loads(response.text)

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
            # Build context about what we expected vs what we got
            expected = {
                'query': location_name,
                'parsed_country': parsed_location.get('country', 'Unknown'),
                'parsed_city': parsed_location.get('admin_level_2', 'Unknown'),
                'parsed_facility': parsed_location.get('facility', 'Unknown')
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
1. Do the reverse addresses match the COUNTRY from the query?
2. If a city was mentioned, do addresses include that city?
3. Does the location type make sense? (hospital, clinic, etc.)
4. Are any coordinates clearly wrong? (e.g., ocean, wrong continent)
5. Do all sources agree on general geographic area?

Return JSON:
{{
    "passes_sanity_check": true or false,
    "confidence": <0.0-1.0>,
    "issues_found": ["list specific problems, or empty array if all good"],
    "reasoning": "2-3 sentences explaining your assessment",
    "severity": "none|minor|major|critical"
}}

Examples:
- Query: "Harare Hospital Zimbabwe" | All addresses in Zimbabwe → passes: true
- Query: "Nairobi Clinic Kenya" | Addresses show "South Africa" → passes: false, severity: "critical"
- Query: "General Hospital" | Addresses vague but plausible → passes: true, severity: "minor"
"""

            response = self.model_flash.generate_content(prompt)
            result = json.loads(response.text)

            if not result['passes_sanity_check']:
                logger.warning(f"⚠ LLM Sanity Check FAILED for '{location_name}':")
                logger.warning(f"  Issues: {', '.join(result['issues_found'])}")
                logger.warning(f"  Severity: {result['severity']}")
            else:
                logger.info(f"✓ LLM Sanity Check PASSED for '{location_name}' (confidence: {result['confidence']:.1%})")

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

            # Use Flash model with text response
            model = genai.GenerativeModel('gemini-flash-latest')
            response = model.generate_content(prompt)
            explanation = response.text.strip()

            logger.info(f"✓ Generated validation explanation for '{geocoding_result.location_name}'")

            return explanation

        except Exception as e:
            logger.warning(f"LLM explanation generation failed: {e}")
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
