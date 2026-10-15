# geolocation/admin_boundary_service.py
"""
Admin Boundary Geocoding Service for HarmonAIze.

Optimized for fast parallel geocoding by:
1. Building a lightweight name index on first use (cached)
2. Only loading full geometries when needed for polygon rendering
3. Using Django's cache for persistence across requests
"""

import os
import json
import logging
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path
from django.conf import settings
from django.core.cache import cache

# For streaming large JSON files
try:
    import ijson
    IJSON_AVAILABLE = True
except ImportError:
    IJSON_AVAILABLE = False

logger = logging.getLogger(__name__)

# Check for optional dependencies
try:
    from shapely.geometry import shape, Point
    SHAPELY_AVAILABLE = True
except ImportError:
    SHAPELY_AVAILABLE = False
    logger.info("Shapely not available - using simple centroid calculation")

try:
    from fuzzywuzzy import fuzz, process
    FUZZY_AVAILABLE = True
except ImportError:
    FUZZY_AVAILABLE = False
    logger.info("Fuzzywuzzy not available - using exact matching only")


class AdminBoundaryGeocoder:
    """
    Fast geocoder matching location names against admin boundary GeoJSON files.

    Optimized for speed by:
    - Building a lightweight index on first use (names + centroids only)
    - Caching the index across requests
    - Loading full geometries only when needed
    """

    # Cache keys
    INDEX_CACHE_KEY = 'admin_boundary_index_v2'
    INDEX_CACHE_TIMEOUT = 3600 * 24  # 24 hours
    HIERARCHY_CACHE_KEY = 'spatial_hierarchy_v1'
    HIERARCHY_CACHE_TIMEOUT = 3600 * 24

    # Pre-built index file (much faster than parsing 2GB+ GeoJSON)
    INDEX_FILE_NAME = 'admin_boundary_index.json'

    # Admin keywords for type detection
    ADMIN_KEYWORDS = {
        'province': 4, 'state': 4, 'region': 4,
        'district': 6, 'county': 6, 'municipality': 6,
        'city': 6, 'town': 6, 'country': 2, 'nation': 2,
    }

    # Facility keywords suggesting point location
    FACILITY_KEYWORDS = [
        'hospital', 'clinic', 'health centre', 'health center',
        'dispensary', 'pharmacy', 'school', 'university',
        'church', 'mosque', 'hotel', 'airport', 'station',
    ]

    # Buffers (in degrees, ~111km/degree at the equator) absorb GADM boundary
    # simplification and coastline noise so points genuinely near a border
    # aren't flagged. Country buffer is wider since misclassifying a whole
    # country is the case worth catching; province buffer is tighter since
    # province lines are less reliable and false positives there are just noise.
    COUNTRY_BOUNDARY_BUFFER_DEG = 0.05   # ~5.5 km
    PROVINCE_BOUNDARY_BUFFER_DEG = 0.02  # ~2.2 km

    def __init__(self):
        self.data_dir = Path(__file__).parent / 'data_geocoding'
        self._index = None
        self._geometry_cache = {}

    def _get_geojson_files(self) -> Dict[str, Path]:
        """
        Get available GeoJSON files.

        Returns a mapping of logical layer types to file paths.
        Automatically detects:
        - Core files: Africa_Boundaries.geojson, africa_admin_boundaries.geojson
        - Country-specific files: Ethiopia_AdminBoundaries.geojson, Kenya_AdminBoundaries.geojson, etc.

        Country-specific files help fill in gaps in the comprehensive boundaries.
        """
        files = {}

        # Log the data directory for debugging
        logger.info(f"Looking for GeoJSON files in: {self.data_dir}")
        if not self.data_dir.exists():
            logger.warning(f"Data directory does not exist: {self.data_dir}")
            return files

        available_files = list(self.data_dir.glob('*.geojson'))
        logger.info(f"Available GeoJSON files: {[f.name for f in available_files]}")

        # Core files with specific layer types
        core_patterns = {
            'countries': ['Africa_Boundaries.geojson', 'countries.geojson'],
            'admin_boundaries': ['africa_admin_boundaries.geojson'],
        }

        for layer_type, filenames in core_patterns.items():
            for filename in filenames:
                path = self.data_dir / filename
                if path.exists():
                    files[layer_type] = path
                    logger.info(f"Found core layer {layer_type}: {filename}")
                    break

        # Auto-detect country-specific boundary files
        # Pattern: {Country}_AdminBoundaries.geojson or {Country}_Boundaries.geojson
        for geojson_file in available_files:
            filename = geojson_file.name
            filename_lower = filename.lower()

            # Skip already-processed core files
            if filename in ['Africa_Boundaries.geojson', 'africa_admin_boundaries.geojson']:
                continue

            # Detect country-specific admin boundary files
            if '_adminboundaries.geojson' in filename_lower or '_boundaries.geojson' in filename_lower:
                # Extract country name from filename
                country_name = filename.split('_')[0].lower()
                layer_key = f"country_{country_name}"
                files[layer_key] = geojson_file
                logger.info(f"Found country-specific layer {layer_key}: {filename}")

        return files

    def _get_index(self) -> Dict:
        """Get or build the boundary name index (lightweight, fast)."""
        # Try memory cache first
        if self._index is not None:
            return self._index

        # Try Django cache (Redis)
        cached = cache.get(self.INDEX_CACHE_KEY)
        if cached:
            self._index = cached
            return cached

        # Try loading from pre-built index file (instant)
        index_file = self.data_dir / self.INDEX_FILE_NAME
        if index_file.exists():
            logger.info(f"Loading pre-built index from {self.INDEX_FILE_NAME}...")
            try:
                with open(index_file, 'r', encoding='utf-8') as f:
                    self._index = json.load(f)
                # Cache in Redis for faster subsequent access
                cache.set(self.INDEX_CACHE_KEY, self._index, self.INDEX_CACHE_TIMEOUT)
                logger.info(f"Loaded index: {len(self._index.get('names', {}))} names")
                return self._index
            except Exception as e:
                logger.warning(f"Failed to load index file: {e}")

        # Build index from GeoJSON files (slow - only if no pre-built file)
        logger.info("Building admin boundary index from GeoJSON files...")
        self._index = self._build_index()

        # Cache it
        cache.set(self.INDEX_CACHE_KEY, self._index, self.INDEX_CACHE_TIMEOUT)
        logger.info(f"Admin boundary index built: {len(self._index.get('names', {}))} names")

        return self._index

    def save_index_to_file(self) -> bool:
        """Save the current index to a JSON file for fast loading."""
        index = self._get_index()
        index_file = self.data_dir / self.INDEX_FILE_NAME

        try:
            # Custom encoder to handle Decimal and other non-JSON types
            from decimal import Decimal

            def json_encoder(obj):
                if isinstance(obj, Decimal):
                    return float(obj)
                raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

            with open(index_file, 'w', encoding='utf-8') as f:
                json.dump(index, f, default=json_encoder)
            file_size = index_file.stat().st_size / 1024 / 1024
            logger.info(f"Saved index to {index_file} ({file_size:.2f} MB)")
            return True
        except Exception as e:
            logger.error(f"Failed to save index: {e}")
            return False

    def _build_index(self) -> Dict:
        """
        Build a lightweight index of boundary names to centroids.

        Only stores names and pre-calculated centroids, not full geometries.
        This makes the index small and fast to load.
        """
        index = {
            'names': {},  # normalized_name -> list of matches
            'built': True
        }

        files = self._get_geojson_files()
        logger.info(f"Will index {len(files)} layer types: {list(files.keys())}")

        for layer_type, file_path in files.items():
            logger.info(f"Processing layer: {layer_type} from {file_path.name}")
            try:
                self._index_file(index, layer_type, file_path)
                logger.info(f"Completed layer: {layer_type}")
            except Exception as e:
                logger.error(f"Failed to index {layer_type} from {file_path}: {e}", exc_info=True)

        logger.info(f"Index build complete. Total unique names: {len(index.get('names', {}))}")
        return index

    def _index_file(self, index: Dict, layer_type: str, file_path: Path):
        """Index a single GeoJSON file."""
        file_size_mb = file_path.stat().st_size / 1024 / 1024
        logger.info(f"Loading file: {file_path.name} (size: {file_size_mb:.2f} MB)")

        # Use streaming parser for large files (>100MB)
        if file_size_mb > 100 and IJSON_AVAILABLE:
            logger.info(f"Using streaming parser for large file")
            self._index_file_streaming(index, layer_type, file_path)
            return

        # Standard loading for smaller files
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            logger.info(f"File loaded successfully")
        except MemoryError:
            logger.error(f"File too large for memory: {file_path.name}. Need streaming parser.")
            if IJSON_AVAILABLE:
                self._index_file_streaming(index, layer_type, file_path)
            return
        except Exception as e:
            logger.error(f"Failed to load {file_path}: {e}", exc_info=True)
            return

        features = data.get('features', [])
        logger.info(f"Indexing {len(features)} features from {file_path.name}")

        for idx, feature in enumerate(features):
            props = feature.get('properties', {})
            geometry = feature.get('geometry', {})

            # Get all name variants - check multiple field patterns
            # Supports various GeoJSON naming conventions from different sources
            names = set()
            name_fields = [
                # Standard OSM/common fields
                'name', 'name_en', 'NAME_0', 'NAME_1', 'NAME_2', 'NAME_3',
                'local_name', 'name:en', 'official_name', 'alt_name',
                'ADM0_EN', 'ADM1_EN', 'ADM2_EN', 'ADM3_EN',
                'COUNTRY', 'PROVINCE', 'DISTRICT', 'REGION',
                'shapeName', 'shapeGroup',
                # Ethiopia-specific fields (R=Region, Z=Zone, W=Woreda/District)
                'R_NAME', 'Z_NAME', 'W_NAME', 'T_NAME', 'RK_NAME', 'UK_NAME',
                # Additional common patterns
                'NAME', 'ADMIN_NAME', 'ADM_NAME', 'LABEL', 'PLACE_NAME',
            ]
            for field in name_fields:
                name = props.get(field)
                if name and isinstance(name, str) and name.strip():
                    names.add(name.strip())
            # GADM alt_name variants stored as alt_name, alt_name_1, alt_name_2 …
            for key, val in props.items():
                if key.startswith('alt_name') and val and isinstance(val, str):
                    names.add(val.strip())

            if not names:
                continue

            # Calculate centroid once
            centroid = self._calculate_centroid_fast(geometry)
            if not centroid:
                continue

            # Get admin level from properties
            admin_level = props.get('admin_level', '')
            if not admin_level:
                if layer_type == 'countries':
                    admin_level = '2'
                # For admin_boundaries file, try to infer from other properties
                elif props.get('boundary') == 'administrative':
                    admin_level = props.get('admin_level', '4')  # Default to province level

            # Determine actual layer type based on admin level
            actual_layer = layer_type
            if layer_type == 'admin_boundaries' or layer_type.startswith('country_'):
                level_int = int(admin_level) if admin_level and admin_level.isdigit() else 4
                if level_int <= 2:
                    actual_layer = 'countries'
                elif level_int <= 4:
                    actual_layer = 'provinces'
                else:
                    actual_layer = 'districts'

            # Create match entry (small, no geometry)
            entry = {
                'file': file_path.name,
                'layer': actual_layer,
                'idx': idx,
                'names': list(names),
                'name': list(names)[0],
                'centroid': centroid,
                'level': admin_level,
                'country': props.get('country', props.get('NAME_0', '')),
                'province': props.get('NAME_1', ''),  # parent province for district entries
                'osm_id': props.get('osm_id', ''),
            }

            # Index by all normalized names
            for name in names:
                normalized = self._normalize(name)
                if normalized:
                    if normalized not in index['names']:
                        index['names'][normalized] = []
                    index['names'][normalized].append(entry)

    def _index_file_streaming(self, index: Dict, layer_type: str, file_path: Path):
        """Index a large GeoJSON file using streaming parser (ijson)."""
        if not IJSON_AVAILABLE:
            logger.error("ijson not available for streaming large files")
            return

        logger.info(f"Streaming parse of {file_path.name}...")
        indexed_count = 0
        skipped_no_name = 0
        skipped_no_centroid = 0
        sample_props_logged = False

        try:
            with open(file_path, 'rb') as f:
                # Stream through features one at a time
                for idx, feature in enumerate(ijson.items(f, 'features.item')):
                    props = feature.get('properties', {})
                    geometry = feature.get('geometry', {})

                    # Log sample properties to understand data structure
                    if not sample_props_logged and idx < 5:
                        logger.info(f"Sample feature {idx} properties keys: {list(props.keys())[:15]}")
                        sample_props_logged = (idx == 4)

                    # Get all name variants - check more field patterns
                    # Supports various GeoJSON naming conventions from different sources
                    names = set()
                    name_fields = [
                        # Standard OSM/common fields
                        'name', 'name_en', 'NAME_0', 'NAME_1', 'NAME_2', 'NAME_3',
                        'local_name', 'name:en', 'official_name', 'alt_name',
                        'ADM0_EN', 'ADM1_EN', 'ADM2_EN', 'ADM3_EN',
                        'COUNTRY', 'PROVINCE', 'DISTRICT', 'REGION',
                        'shapeName', 'shapeGroup',
                        # Ethiopia-specific fields (R=Region, Z=Zone, W=Woreda/District)
                        'R_NAME', 'Z_NAME', 'W_NAME', 'T_NAME', 'RK_NAME', 'UK_NAME',
                        # Additional common patterns
                        'NAME', 'ADMIN_NAME', 'ADM_NAME', 'LABEL', 'PLACE_NAME',
                    ]
                    for field in name_fields:
                        name = props.get(field)
                        if name and isinstance(name, str) and name.strip():
                            names.add(name.strip())
                    for key, val in props.items():
                        if key.startswith('alt_name') and val and isinstance(val, str):
                            names.add(val.strip())

                    if not names:
                        skipped_no_name += 1
                        if skipped_no_name <= 3:
                            logger.debug(f"Feature {idx} skipped - no name. Props: {list(props.keys())[:10]}")
                        continue

                    # Calculate centroid
                    centroid = self._calculate_centroid_fast(geometry)
                    if not centroid:
                        skipped_no_centroid += 1
                        continue

                    # Get admin level from feature properties
                    admin_level = props.get('admin_level', '')
                    if not admin_level:
                        if layer_type == 'countries':
                            admin_level = '2'
                        elif props.get('boundary') == 'administrative':
                            admin_level = props.get('admin_level', '4')

                    # Determine actual layer type based on admin level
                    actual_layer = layer_type
                    if layer_type == 'admin_boundaries' or layer_type.startswith('country_'):
                        level_int = int(admin_level) if admin_level and admin_level.isdigit() else 4
                        if level_int <= 2:
                            actual_layer = 'countries'
                        elif level_int <= 4:
                            actual_layer = 'provinces'
                        else:
                            actual_layer = 'districts'

                    # Create match entry
                    entry = {
                        'file': file_path.name,
                        'layer': actual_layer,
                        'idx': idx,
                        'names': list(names),
                        'name': list(names)[0],
                        'centroid': centroid,
                        'level': admin_level,
                        'country': props.get('country', props.get('NAME_0', '')),
                        'province': props.get('NAME_1', ''),  # parent province for district entries
                        'osm_id': props.get('osm_id', ''),
                    }

                    # Index by all normalized names
                    for name in names:
                        normalized = self._normalize(name)
                        if normalized:
                            if normalized not in index['names']:
                                index['names'][normalized] = []
                            index['names'][normalized].append(entry)

                    indexed_count += 1

                    # Progress logging every 1000 features
                    if indexed_count % 1000 == 0:
                        logger.info(f"Indexed {indexed_count} features from {file_path.name}...")

            logger.info(f"Streaming complete: indexed {indexed_count} features from {file_path.name}")
            logger.info(f"Skipped: {skipped_no_name} (no name), {skipped_no_centroid} (no centroid)")

        except Exception as e:
            logger.error(f"Streaming parse failed for {file_path}: {e}", exc_info=True)

    def _calculate_centroid_fast(self, geometry: Dict) -> Optional[Tuple[float, float]]:
        """Calculate centroid quickly using simple coordinate averaging."""
        from decimal import Decimal

        coords = geometry.get('coordinates', [])
        if not coords:
            return None

        # Collect all coordinate points
        points = []

        def to_float(val):
            """Convert Decimal or other numeric types to float."""
            if isinstance(val, Decimal):
                return float(val)
            return float(val) if val is not None else 0.0

        def extract(obj):
            if isinstance(obj, list):
                if len(obj) >= 2 and isinstance(obj[0], (int, float, Decimal)):
                    points.append((to_float(obj[0]), to_float(obj[1])))  # lng, lat
                else:
                    for item in obj:
                        extract(item)

        extract(coords)

        if not points:
            return None

        # Simple average (good enough for geocoding)
        avg_lng = sum(p[0] for p in points) / len(points)
        avg_lat = sum(p[1] for p in points) / len(points)

        return (avg_lat, avg_lng)  # Return as (lat, lng)

    def _normalize(self, name: str) -> str:
        """Normalize a name for matching."""
        if not name:
            return ''

        normalized = name.lower().strip()

        # Remove common suffixes
        for suffix in ['province', 'district', 'region', 'county', 'state']:
            if normalized.endswith(f' {suffix}'):
                normalized = normalized[:-len(suffix)-1].strip()

        # Remove ordinal prefixes (1st, 2nd, 3rd, etc.) common in Ethiopian admin names
        import re
        normalized = re.sub(r'^(\d+)(st|nd|rd|th)\s+', '', normalized)

        return normalized

    def geocode(
        self,
        location_name: str,
        country_hint: Optional[str] = None,
        province_hint: Optional[str] = None,
        district_hint: Optional[str] = None,
        admin_level_hint: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Geocode a location name against admin boundaries.

        Fast because it uses the pre-built index (names + centroids only).
        """
        if not location_name:
            return {'success': False, 'error': 'No location name', 'coordinates': None}

        try:
            index = self._get_index()
        except Exception as e:
            logger.error(f"Failed to get boundary index: {e}")
            return {'success': False, 'error': str(e), 'coordinates': None}

        names_index = index.get('names', {})
        if not names_index:
            return {'success': False, 'error': 'Index empty', 'coordinates': None}

        # Clean and normalize
        cleaned = location_name.strip()

        # Common ISO country codes to strip
        country_codes = [
            'ETH', 'ZWE', 'KEN', 'TZA', 'UGA', 'RWA', 'BDI', 'MWI', 'ZMB', 'MOZ',
            'ZAF', 'NAM', 'BWA', 'LSO', 'SWZ', 'AGO', 'COD', 'COG', 'GAB', 'CMR',
            'NGA', 'GHA', 'CIV', 'SEN', 'MLI', 'BFA', 'NER', 'TCD', 'SDN', 'SSD',
            'EGY', 'LBY', 'TUN', 'DZA', 'MAR', 'MRT', 'SOM', 'DJI', 'ERI',
        ]

        # Strip country name if present (e.g., "Harare, Zimbabwe" -> "Harare")
        if country_hint:
            # Remove ", Country" suffix
            country_lower = country_hint.lower()
            if f', {country_lower}' in cleaned.lower():
                idx = cleaned.lower().rfind(f', {country_lower}')
                cleaned = cleaned[:idx].strip()
            elif cleaned.lower().endswith(country_lower):
                cleaned = cleaned[:-len(country_hint)].strip().rstrip(',').strip()

        # Handle comma-separated format like "Arada, ETH, Hossana"
        # Split and clean each part, removing country codes
        # Store all parts for fallback matching
        all_search_parts = []
        if ',' in cleaned:
            parts = [p.strip() for p in cleaned.split(',')]
            # Remove country codes from parts
            parts = [p for p in parts if p.upper() not in country_codes and p.strip()]
            all_search_parts = parts.copy()
            if parts:
                # Try the first part as the primary location name
                cleaned = parts[0]

        # Strip admin level suffixes
        for suffix in ['province', 'district', 'region', 'county', 'state']:
            if cleaned.lower().endswith(f' {suffix}'):
                cleaned = cleaned[:-len(suffix)-1].strip()
                if admin_level_hint is None:
                    admin_level_hint = self.ADMIN_KEYWORDS.get(suffix)

        normalized = self._normalize(cleaned)

        # Try exact match first
        matches = names_index.get(normalized, [])

        # Filter by country if provided
        if country_hint and matches:
            country_lower = country_hint.lower()
            filtered = [m for m in matches if country_lower in m.get('country', '').lower()]
            if filtered:
                matches = filtered

        # Filter by province: keep matches whose name IS the province (level 4)
        # or whose parent province matches (level 6 district entries)
        if province_hint and matches:
            province_lower = province_hint.lower()
            filtered = [
                m for m in matches
                if province_lower in m.get('province', '').lower()
                or (m.get('level') == '4' and province_lower in m.get('name', '').lower())
            ]
            if filtered:
                matches = filtered

        # Filter by district: keep only level-6 entries whose name matches
        if district_hint and matches:
            district_lower = district_hint.lower()
            filtered = [
                m for m in matches
                if m.get('level') == '6' and district_lower in m.get('name', '').lower()
            ]
            if filtered:
                matches = filtered

        # Filter by admin level if provided
        if admin_level_hint and matches:
            level_str = str(admin_level_hint)
            filtered = [m for m in matches if m.get('level') == level_str]
            if filtered:
                matches = filtered

        if matches:
            best = matches[0]
            return {
                'success': True,
                'coordinates': best['centroid'],
                'boundary_reference': {
                    'source_file': best['file'],
                    'layer_type': best['layer'],
                    'feature_index': best['idx'],
                    'osm_id': best['osm_id'],
                    'name': best['name'],
                    'admin_level': best['level'],
                    'country': best['country'],
                    'match_type': 'exact'
                },
                'confidence': 1.0,
                'location_type': 'admin_boundary'
            }

        # Try other parts of comma-separated name (e.g., "Arada, ETH, Hossana" -> try "Hossana")
        if all_search_parts and len(all_search_parts) > 1:
            for part in all_search_parts[1:]:  # Skip first part (already tried)
                part_normalized = self._normalize(part)
                part_matches = names_index.get(part_normalized, [])
                if part_matches:
                    # Apply country filter if provided
                    if country_hint:
                        country_lower = country_hint.lower()
                        filtered = [m for m in part_matches if country_lower in m.get('country', '').lower()]
                        if filtered:
                            part_matches = filtered
                    if part_matches:
                        best = part_matches[0]
                        return {
                            'success': True,
                            'coordinates': best['centroid'],
                            'boundary_reference': {
                                'source_file': best['file'],
                                'layer_type': best['layer'],
                                'feature_index': best['idx'],
                                'osm_id': best['osm_id'],
                                'name': best['name'],
                                'admin_level': best['level'],
                                'country': best['country'],
                                'match_type': 'fallback_part'
                            },
                            'confidence': 0.9,
                            'location_type': 'admin_boundary'
                        }

        # Try fuzzy match if available
        if FUZZY_AVAILABLE and len(names_index) < 50000:  # Only for reasonable index sizes
            try:
                all_names = list(names_index.keys())
                fuzzy_result = process.extractOne(
                    normalized, all_names,
                    scorer=fuzz.token_sort_ratio,
                    score_cutoff=80
                )

                if fuzzy_result:
                    matched_name, score = fuzzy_result[0], fuzzy_result[1]
                    matches = names_index.get(matched_name, [])

                    if matches:
                        best = matches[0]
                        return {
                            'success': True,
                            'coordinates': best['centroid'],
                            'boundary_reference': {
                                'source_file': best['file'],
                                'layer_type': best['layer'],
                                'feature_index': best['idx'],
                                'osm_id': best['osm_id'],
                                'name': best['name'],
                                'admin_level': best['level'],
                                'country': best['country'],
                                'match_type': 'fuzzy',
                                'match_score': score
                            },
                            'confidence': score / 100.0,
                            'location_type': 'admin_boundary'
                        }
            except Exception as e:
                logger.warning(f"Fuzzy matching failed: {e}")

        return {
            'success': False,
            'error': f"No match for '{location_name}'",
            'coordinates': None
        }

    def detect_location_type(self, location_name: str) -> Dict[str, Any]:
        """Detect if location is a point or admin boundary."""
        if not location_name:
            return {'suggested_type': 'unknown', 'confidence': 0.0, 'reasoning': 'Empty'}

        lower = location_name.lower()

        # Check facility keywords
        for kw in self.FACILITY_KEYWORDS:
            if kw in lower:
                return {
                    'suggested_type': 'point',
                    'confidence': 0.9,
                    'reasoning': f"Contains '{kw}'"
                }

        # Check admin keywords
        for kw in self.ADMIN_KEYWORDS:
            if kw in lower:
                return {
                    'suggested_type': 'admin_boundary',
                    'confidence': 0.9,
                    'reasoning': f"Contains '{kw}'"
                }

        # Check if in index
        try:
            index = self._get_index()
            normalized = self._normalize(location_name)
            if normalized in index.get('names', {}):
                return {
                    'suggested_type': 'admin_boundary',
                    'confidence': 0.8,
                    'reasoning': 'Matches known boundary'
                }
        except:
            pass

        return {'suggested_type': 'unknown', 'confidence': 0.3, 'reasoning': 'No indicators'}

    def _find_boundary_entry(
        self, name: str, level: str, country: Optional[str] = None
    ) -> Optional[Dict]:
        """Look up a single index entry by name/level (and optional country) for polygon loading."""
        index = self._get_index()
        normalized = self._normalize(name)
        matches = [m for m in index.get('names', {}).get(normalized, []) if m.get('level') == level]

        if country and matches:
            country_lower = country.lower()
            filtered = [m for m in matches if country_lower in m.get('country', '').lower()]
            if filtered:
                matches = filtered

        return matches[0] if matches else None

    def _get_cached_polygon(self, entry: Dict, buffer_deg: float):
        """Load and cache a buffered shapely polygon for an index entry (keyed by file+index)."""
        cache_key = (entry['file'], entry['idx'], buffer_deg)
        if cache_key in self._geometry_cache:
            return self._geometry_cache[cache_key]

        geometry = self.get_feature_geometry(
            entry.get('layer', ''), entry['idx'], source_file=entry['file']
        )
        polygon = shape(geometry).buffer(buffer_deg) if geometry else None
        self._geometry_cache[cache_key] = polygon
        return polygon

    def validate_point_in_boundary(
        self,
        lat: float,
        lng: float,
        expected_country: Optional[str] = None,
        expected_province: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Validate coordinates against admin boundaries via point-in-polygon checks.

        Deliberately conservative to avoid being a distraction: an unknown
        country/province name, missing boundary data, or any lookup error
        always resolves to a pass rather than a false flag. Only two things
        get flagged:
        - 'critical': point falls outside the expected country's polygon
          (buffered to absorb boundary simplification/coastline noise)
        - 'major': country matches, but point falls outside the expected
          province's polygon (buffered more tightly, and only checked once
          the country itself is confirmed correct)
        """
        result = {
            'is_valid': True,
            'actual_country': None,
            'actual_province': None,
            'warnings': [],
            'severity': 'none',
        }

        if not expected_country or not SHAPELY_AVAILABLE:
            return result

        try:
            country_entry = self._find_boundary_entry(expected_country, level='2')
            if not country_entry:
                # Unknown country name in our reference data - don't punish for that
                return result

            country_poly = self._get_cached_polygon(country_entry, self.COUNTRY_BOUNDARY_BUFFER_DEG)
            if not country_poly:
                return result

            point = Point(lng, lat)

            if not country_poly.contains(point):
                result['is_valid'] = False
                result['severity'] = 'critical'
                result['warnings'].append(
                    f"Coordinates ({lat:.4f}, {lng:.4f}) fall outside {expected_country}"
                )
                return result

            result['actual_country'] = expected_country

            if expected_province:
                province_entry = self._find_boundary_entry(
                    expected_province, level='4', country=expected_country
                )
                if province_entry:
                    province_poly = self._get_cached_polygon(
                        province_entry, self.PROVINCE_BOUNDARY_BUFFER_DEG
                    )
                    if province_poly and not province_poly.contains(point):
                        result['is_valid'] = False
                        result['severity'] = 'major'
                        result['warnings'].append(
                            f"Coordinates are in {expected_country} but outside the "
                            f"expected province '{expected_province}'"
                        )
                    elif province_poly:
                        result['actual_province'] = expected_province

        except Exception as e:
            logger.warning(f"Point-in-boundary check failed: {e}")
            return {
                'is_valid': True,
                'actual_country': None,
                'actual_province': None,
                'warnings': [],
                'severity': 'none',
            }

        return result

    def get_feature_geometry(self, layer_type: str, feature_index: int, source_file: str = None) -> Optional[Dict]:
        """
        Load full geometry for a specific feature.

        Uses streaming for large files to avoid loading 2GB+ into memory.

        Args:
            layer_type: The logical layer type (countries, provinces, districts)
            feature_index: Index of the feature in the source file
            source_file: Optional filename to load from directly (from boundary_reference)
        """
        # If source_file is provided, use it directly
        if source_file:
            file_path = self.data_dir / source_file
            if file_path.exists():
                logger.info(f"Loading geometry from {source_file} at index {feature_index}")
            else:
                logger.warning(f"Source file not found: {source_file}")
                file_path = None
        else:
            file_path = None

        # Fall back to layer type lookup
        if not file_path:
            files = self._get_geojson_files()
            # Try exact layer match first
            file_path = files.get(layer_type)
            # For provinces/districts, use admin_boundaries file
            if not file_path and layer_type in ['provinces', 'districts']:
                file_path = files.get('admin_boundaries')

        if not file_path or not file_path.exists():
            logger.warning(f"File not found for layer: {layer_type}")
            return None

        file_size_mb = file_path.stat().st_size / 1024 / 1024

        # Use streaming for large files (>100MB)
        if file_size_mb > 100 and IJSON_AVAILABLE:
            return self._get_feature_geometry_streaming(file_path, feature_index)

        # Standard loading for smaller files
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            features = data.get('features', [])
            if 0 <= feature_index < len(features):
                return features[feature_index].get('geometry')
        except MemoryError:
            logger.warning(f"File too large, falling back to streaming")
            if IJSON_AVAILABLE:
                return self._get_feature_geometry_streaming(file_path, feature_index)
        except Exception as e:
            logger.error(f"Failed to load geometry: {e}")

        return None

    def _get_feature_geometry_streaming(self, file_path: Path, feature_index: int) -> Optional[Dict]:
        """Stream through GeoJSON to get a specific feature's geometry."""
        if not IJSON_AVAILABLE:
            logger.error("ijson required for streaming large files")
            return None

        logger.info(f"Streaming to feature {feature_index} in {file_path.name}...")

        try:
            with open(file_path, 'rb') as f:
                for idx, feature in enumerate(ijson.items(f, 'features.item')):
                    if idx == feature_index:
                        logger.info(f"Found feature {feature_index}")
                        return feature.get('geometry')
                    # Skip ahead - don't store features we don't need
                    if idx > feature_index:
                        break

            logger.warning(f"Feature {feature_index} not found in {file_path.name}")
        except Exception as e:
            logger.error(f"Streaming failed: {e}")

        return None

    def get_boundary_polygon(self, layer_type: str, feature_index: int, source_file: str = None) -> Dict[str, Any]:
        """Get full boundary feature for map rendering."""
        geometry = self.get_feature_geometry(layer_type, feature_index, source_file)

        if not geometry:
            return {'success': False, 'error': 'Feature not found'}

        centroid = self._calculate_centroid_fast(geometry)

        return {
            'success': True,
            'geometry': geometry,
            'centroid': {'lat': centroid[0], 'lng': centroid[1]} if centroid else None,
            'properties': {}
        }


    def _get_hierarchy(self) -> dict:
        """Return (and cache) the country → province → district tree."""
        cached = cache.get(self.HIERARCHY_CACHE_KEY)
        if cached:
            return cached
        hierarchy = self._build_hierarchy()
        cache.set(self.HIERARCHY_CACHE_KEY, hierarchy, self.HIERARCHY_CACHE_TIMEOUT)
        return hierarchy

    def _build_hierarchy(self) -> dict:
        """
        Scan the boundary index and build a nested lookup tree:
          { country_name: { provinces: [str], districts: { province: [str] } } }

        Note: GADM entries include NAME_0 (country) in the names set, so we
        must pick the first name that is *not* the country (or province) name.
        """
        index = self._get_index()
        tree: Dict[str, Any] = {}

        for entry_list in index.get('names', {}).values():
            for entry in entry_list:
                country = entry.get('country', '').strip()
                if not country:
                    continue
                level = entry.get('level', '')
                all_names = entry.get('names', [])
                if not all_names:
                    all_names = [entry.get('name', '')]
                country_lower = country.lower()

                if country not in tree:
                    tree[country] = {'provinces': set(), 'districts': {}}

                if level == '4':
                    # Province = first name that is not the country name
                    province_name = next(
                        (n.strip() for n in all_names
                         if n and n.strip().lower() != country_lower),
                        None
                    )
                    if province_name:
                        tree[country]['provinces'].add(province_name)

                elif level == '6':
                    province = entry.get('province', '').strip()
                    province_lower = province.lower() if province else ''
                    # District = first name that is not the country or province name
                    district_name = next(
                        (n.strip() for n in all_names
                         if n and n.strip().lower() not in (country_lower, province_lower)),
                        None
                    )
                    if district_name and province:
                        tree[country]['districts'].setdefault(province, set()).add(district_name)

        # Serialisable form (sorted lists)
        return {
            c: {
                'provinces': sorted(d['provinces']),
                'districts': {p: sorted(ds) for p, ds in d['districts'].items()},
            }
            for c, d in tree.items()
        }

    def list_provinces(self, country: str) -> list:
        """Return sorted province names for *country*."""
        hierarchy = self._get_hierarchy()
        # Case-insensitive country lookup
        country_lower = country.lower()
        for c, data in hierarchy.items():
            if c.lower() == country_lower:
                return data['provinces']
        return []

    def list_districts(self, country: str, province: str) -> list:
        """Return sorted district names for *country* / *province*."""
        hierarchy = self._get_hierarchy()
        country_lower = country.lower()
        province_lower = province.lower()
        for c, data in hierarchy.items():
            if c.lower() == country_lower:
                for p, districts in data['districts'].items():
                    if p.lower() == province_lower:
                        return districts
                return []
        return []

    def list_available_countries(self) -> list:
        """
        Return a list of countries available as boundary sources.

        Each entry: {'iso3': str, 'name': str, 'file': str, 'levels': list[str]}
        Used to populate country filter dropdowns in the UI.
        """
        countries = []
        for geojson_file in sorted(self.data_dir.glob('*.geojson')):
            name_lower = geojson_file.name.lower()
            if '_adminboundaries.geojson' not in name_lower and '_boundaries.geojson' not in name_lower:
                continue
            iso3 = geojson_file.name.split('_')[0].upper()
            # Derive country name from the index if already built, else from file
            country_name = iso3
            try:
                index = self._get_index()
                for entry_list in index.get('names', {}).values():
                    for entry in entry_list:
                        if entry.get('file') == geojson_file.name:
                            c = entry.get('country', '')
                            if c:
                                country_name = c
                            break
                    if country_name != iso3:
                        break
            except Exception:
                pass
            countries.append({
                'iso3': iso3,
                'name': country_name,
                'file': geojson_file.name,
            })
        return sorted(countries, key=lambda x: x['name'])


# Singleton instance
_instance = None

def get_admin_boundary_geocoder() -> AdminBoundaryGeocoder:
    """Get singleton geocoder instance."""
    global _instance
    if _instance is None:
        _instance = AdminBoundaryGeocoder()
    return _instance
