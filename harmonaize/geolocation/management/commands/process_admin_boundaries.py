"""
Management command to process large admin boundaries GeoJSON file
into smaller, more manageable chunks by admin level.

This uses streaming JSON parsing to handle files that are too large
to load entirely into memory. Runs silently on startup.
"""
import os
import json
import logging

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Process admin boundaries GeoJSON into smaller files by admin level'

    def add_arguments(self, parser):
        parser.add_argument(
            '--input',
            type=str,
            default='africa_admin_boundaries.geojson',
            help='Input GeoJSON filename in data_geocoding folder'
        )
        parser.add_argument(
            '--simplify',
            action='store_true',
            help='Simplify geometries to reduce file size'
        )
        parser.add_argument(
            '--tolerance',
            type=float,
            default=0.01,
            help='Simplification tolerance in degrees (default: 0.01)'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force reprocessing even if output files exist'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show progress output'
        )

    def handle(self, *args, **options):
        verbose = options.get('verbose', False)
        force = options.get('force', False)

        # Get paths
        data_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'data_geocoding'
        )
        input_path = os.path.join(data_dir, options['input'])
        output_dir = os.path.join(data_dir, 'admin_boundaries')

        # Check if input file exists
        if not os.path.exists(input_path):
            if verbose:
                self.stdout.write(f'Input file not found: {input_path}')
            return  # Silent exit

        # Check if already processed (skip if output files exist)
        provinces_file = os.path.join(output_dir, 'provinces.geojson')
        districts_file = os.path.join(output_dir, 'districts.geojson')

        if not force and os.path.exists(provinces_file) and os.path.exists(districts_file):
            if verbose:
                self.stdout.write('Admin boundaries already processed. Use --force to reprocess.')
            return  # Already done

        # Create output directory
        os.makedirs(output_dir, exist_ok=True)

        if verbose:
            self.stdout.write(f'Processing: {input_path}')
            self.stdout.write(f'Output directory: {output_dir}')

        # Check file size
        file_size = os.path.getsize(input_path)
        file_size_mb = file_size / (1024 * 1024)

        if verbose:
            self.stdout.write(f'Input file size: {file_size_mb:.1f} MB')

        if file_size_mb > 500:
            if verbose:
                self.stdout.write('Large file detected. Using streaming parser...')
            self._process_large_file(input_path, output_dir, options, verbose)
        else:
            self._process_small_file(input_path, output_dir, options, verbose)

        logger.info('Admin boundaries processing complete')
        if verbose:
            self.stdout.write(self.style.SUCCESS('Processing complete!'))

    def _process_small_file(self, input_path, output_dir, options, verbose):
        """Process smaller files by loading entirely into memory."""
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        features = data.get('features', [])
        if verbose:
            self.stdout.write(f'Total features: {len(features)}')

        # Group by admin level
        by_level = {}
        for feature in features:
            props = feature.get('properties', {})
            level = str(props.get('admin_level', 'unknown'))
            if level not in by_level:
                by_level[level] = []
            by_level[level].append(feature)

        # Simplify if requested
        if options['simplify']:
            by_level = self._simplify_features(by_level, options['tolerance'], verbose)

        # Write output files
        self._write_output_files(by_level, output_dir, verbose)

    def _process_large_file(self, input_path, output_dir, options, verbose):
        """Process large files using streaming JSON parser."""
        try:
            import ijson
        except ImportError:
            logger.warning('ijson not available for large file processing')
            if verbose:
                self.stdout.write(self.style.WARNING(
                    'ijson not installed. Attempting standard JSON (may be slow)...'
                ))
            try:
                self._process_small_file(input_path, output_dir, options, verbose)
            except MemoryError:
                logger.error('Memory error processing admin boundaries')
                if verbose:
                    self.stderr.write(self.style.ERROR(
                        'File too large. Install ijson: pip install ijson'
                    ))
            return

        if verbose:
            self.stdout.write('Streaming file...')

        # Group features by admin level while streaming
        by_level = {}
        count = 0

        with open(input_path, 'rb') as f:
            parser = ijson.items(f, 'features.item')

            for feature in parser:
                count += 1
                if verbose and count % 10000 == 0:
                    self.stdout.write(f'  Processed {count} features...')

                props = feature.get('properties', {})
                level = str(props.get('admin_level', 'unknown'))

                if level not in by_level:
                    by_level[level] = []
                by_level[level].append(feature)

        if verbose:
            self.stdout.write(f'Total features processed: {count}')

        # Simplify if requested
        if options['simplify']:
            by_level = self._simplify_features(by_level, options['tolerance'], verbose)

        # Write output files
        self._write_output_files(by_level, output_dir, verbose)

    def _simplify_features(self, by_level, tolerance, verbose):
        """Simplify geometries to reduce file size."""
        try:
            from shapely.geometry import shape, mapping
            from shapely import simplify
        except ImportError:
            if verbose:
                self.stdout.write(self.style.WARNING(
                    'Shapely not available. Skipping simplification.'
                ))
            return by_level

        if verbose:
            self.stdout.write(f'Simplifying geometries with tolerance {tolerance}...')

        for level, features in by_level.items():
            simplified = []
            for feature in features:
                try:
                    geom = shape(feature['geometry'])
                    simplified_geom = simplify(geom, tolerance, preserve_topology=True)
                    feature['geometry'] = mapping(simplified_geom)
                    simplified.append(feature)
                except Exception:
                    simplified.append(feature)

            by_level[level] = simplified
            if verbose:
                self.stdout.write(f'  Level {level}: {len(simplified)} features simplified')

        return by_level

    def _write_output_files(self, by_level, output_dir, verbose):
        """Write separate GeoJSON files for each admin level."""
        # Map admin levels to layer types
        level_mapping = {
            'provinces': ['2', '3', '4'],
            'districts': ['5', '6', '7', '8'],
            'sub_districts': ['9', '10', '11', '12']
        }

        for layer_type, levels in level_mapping.items():
            features = []
            for level in levels:
                features.extend(by_level.get(level, []))

            if features:
                output_file = os.path.join(output_dir, f'{layer_type}.geojson')
                geojson = {
                    'type': 'FeatureCollection',
                    'features': features
                }

                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(geojson, f)

                file_size = os.path.getsize(output_file) / (1024 * 1024)
                logger.info(f'Created {layer_type}.geojson: {len(features)} features, {file_size:.1f} MB')
                if verbose:
                    self.stdout.write(f'  {layer_type}.geojson: {len(features)} features, {file_size:.1f} MB')

        # Create index file
        index = {
            'levels': {},
            'layer_mapping': level_mapping
        }

        for level, features in by_level.items():
            index['levels'][level] = {
                'count': len(features)
            }

        index_file = os.path.join(output_dir, 'index.json')
        with open(index_file, 'w', encoding='utf-8') as f:
            json.dump(index, f, indent=2)

        if verbose:
            self.stdout.write(f'Index created: {index_file}')
