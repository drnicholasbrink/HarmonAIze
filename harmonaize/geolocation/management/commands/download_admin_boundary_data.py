"""
Download and extract the pre-built admin boundary reference dataset
(GADM-derived per-country GeoJSON files, continent-wide boundary/admin
layers, and the HDX facilities CSV) from Google Drive, if not already
present locally.

The dataset is too large to version in git (several GB), so it is built
once, zipped, and hosted externally. This command fetches it on first
run and is a no-op on every run after that.

Usage:
    python manage.py download_admin_boundary_data
    python manage.py download_admin_boundary_data --force
"""
import logging
import os
import zipfile
from pathlib import Path

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

# Presence of this file means the dataset has already been fetched.
_MARKER_FILE = 'africa_admin_boundaries.geojson'


class Command(BaseCommand):
    help = 'Download the admin boundary dataset from Google Drive if not already present'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Re-download even if the dataset already exists',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show progress output',
        )

    def handle(self, *args, **options):
        verbose = options.get('verbose', False)
        force = options.get('force', False)

        data_dir = Path(__file__).resolve().parents[2] / 'data_geocoding'
        data_dir.mkdir(exist_ok=True)
        marker = data_dir / _MARKER_FILE

        if marker.exists() and not force:
            if verbose:
                self.stdout.write('Admin boundary data already present. Use --force to re-download.')
            return

        file_id = os.environ.get('ADMIN_BOUNDARY_DATA_GDRIVE_ID', '')
        if not file_id:
            logger.warning('ADMIN_BOUNDARY_DATA_GDRIVE_ID not set; skipping admin boundary data download')
            if verbose:
                self.stdout.write(self.style.WARNING(
                    'ADMIN_BOUNDARY_DATA_GDRIVE_ID not set. Skipping download.'
                ))
            return

        try:
            import gdown
        except ImportError:
            logger.warning('gdown not installed; skipping admin boundary data download')
            if verbose:
                self.stdout.write(self.style.WARNING('gdown not installed: pip install gdown'))
            return

        zip_path = data_dir / '_admin_boundary_data.zip'

        try:
            if verbose:
                self.stdout.write(f'Downloading admin boundary dataset (id={file_id}) ...')
            gdown.download(id=file_id, output=str(zip_path), quiet=not verbose)

            if verbose:
                self.stdout.write('Extracting ...')
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(data_dir.parent)

            self.stdout.write(self.style.SUCCESS('Admin boundary dataset ready.'))
        except Exception:
            logger.exception('Failed to download/extract admin boundary dataset')
            if verbose:
                self.stderr.write(self.style.ERROR('Failed to download admin boundary dataset'))
        finally:
            if zip_path.exists():
                zip_path.unlink()
