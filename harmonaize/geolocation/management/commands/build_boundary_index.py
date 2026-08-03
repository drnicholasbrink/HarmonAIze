# geolocation/management/commands/build_boundary_index.py
"""
Management command to pre-build the admin boundary index.

Run this on startup to avoid the first-request delay when geocoding.
The index is cached in Redis for 24 hours.

Usage:
    python manage.py build_boundary_index

Add to Docker entrypoint or startup script:
    python manage.py build_boundary_index && gunicorn ...
"""

import time
from django.core.management.base import BaseCommand
from django.core.cache import cache


class Command(BaseCommand):
    help = 'Pre-build the admin boundary index for fast geocoding'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force rebuild even if index exists in cache',
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear the existing index from cache',
        )
        parser.add_argument(
            '--save-to-file',
            action='store_true',
            help='Save index to JSON file for instant loading on future startups',
        )

    def handle(self, *args, **options):
        from geolocation.admin_boundary_service import (
            AdminBoundaryGeocoder,
            get_admin_boundary_geocoder
        )

        force = options.get('force', False)
        clear = options.get('clear', False)

        cache_key = AdminBoundaryGeocoder.INDEX_CACHE_KEY

        if clear:
            cache.delete(cache_key)
            self.stdout.write(self.style.SUCCESS('Cleared boundary index from cache'))
            if not force:
                return

        # Check if index already exists
        if not force:
            existing = cache.get(cache_key)
            if existing:
                name_count = len(existing.get('names', {}))
                self.stdout.write(self.style.SUCCESS(
                    f'Boundary index already cached ({name_count} names). '
                    f'Use --force to rebuild.'
                ))
                return

        self.stdout.write('Building admin boundary index...')
        start_time = time.time()

        try:
            # Get geocoder and force index build
            geocoder = get_admin_boundary_geocoder()

            # Show available files
            self.stdout.write(f'Data directory: {geocoder.data_dir}')
            files = geocoder._get_geojson_files()
            self.stdout.write(f'Found {len(files)} layer types: {list(files.keys())}')

            # Clear memory cache to force rebuild
            geocoder._index = None

            # Clear Redis cache if forcing
            if force:
                cache.delete(cache_key)

                # Also delete the pre-built index file to force rebuild from GeoJSON
                index_file = geocoder.data_dir / geocoder.INDEX_FILE_NAME
                if index_file.exists():
                    index_file.unlink()
                    self.stdout.write(f'Deleted existing index file: {geocoder.INDEX_FILE_NAME}')

            # Build the index (this triggers _get_index -> _build_index)
            index = geocoder._get_index()

            elapsed = time.time() - start_time
            name_count = len(index.get('names', {}))

            self.stdout.write(self.style.SUCCESS(
                f'Built boundary index in {elapsed:.2f}s '
                f'({name_count} unique names indexed)'
            ))

            # Test a sample geocode
            test_result = geocoder.geocode('Harare', country_hint='Zimbabwe')
            if test_result.get('success'):
                coords = test_result['coordinates']
                self.stdout.write(self.style.SUCCESS(
                    f'Test geocode "Harare": {coords[0]:.4f}, {coords[1]:.4f}'
                ))
            else:
                self.stdout.write(self.style.WARNING(
                    f'Test geocode "Harare" failed: {test_result.get("error")}'
                ))

            # Save index to file if requested
            if options.get('save_to_file', False):
                self.stdout.write('Saving index to file for instant loading...')
                if geocoder.save_index_to_file():
                    index_path = geocoder.data_dir / geocoder.INDEX_FILE_NAME
                    file_size = index_path.stat().st_size / 1024 / 1024
                    self.stdout.write(self.style.SUCCESS(
                        f'Saved index to {geocoder.INDEX_FILE_NAME} ({file_size:.2f} MB)'
                    ))
                    self.stdout.write(self.style.SUCCESS(
                        'Future container startups will load this file instantly!'
                    ))
                else:
                    self.stdout.write(self.style.ERROR('Failed to save index file'))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Failed to build index: {e}'))
            raise
