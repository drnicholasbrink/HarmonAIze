"""
Convert GADM admin boundary ZIP files to GeoJSON for the geocoding engine.

GADM format: {ISO3}_adm.zip with {ISO3}_adm0.shp / adm1.shp / adm2.shp
Output: {ISO3}_AdminBoundaries.geojson in geolocation/data_geocoding/

Admin level mapping:
  adm0 → admin_level 2  (country)
  adm1 → admin_level 4  (province / state)
  adm2 → admin_level 6  (district / county)

Usage:
    python manage.py load_gadm_boundaries
    python manage.py load_gadm_boundaries --source /path/to/zips --force
    python manage.py load_gadm_boundaries --country ZWE --rebuild-index
"""

import json
import logging
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

_LEVEL_MAP = {
    'adm0': '2',
    'adm1': '4',
    'adm2': '6',
}

_DEFAULT_SOURCE = '/home/rutendo/PRECISE'


class Command(BaseCommand):
    help = 'Convert GADM *_adm.zip files to GeoJSON for the boundary geocoder'

    def add_arguments(self, parser):
        parser.add_argument(
            '--source',
            default=_DEFAULT_SOURCE,
            help=f'Directory containing *_adm.zip files (default: {_DEFAULT_SOURCE})',
        )
        parser.add_argument(
            '--country',
            help='Process only this ISO3 code (e.g. ZWE). Omit to process all.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Overwrite existing GeoJSON files',
        )
        parser.add_argument(
            '--rebuild-index',
            action='store_true',
            help='Rebuild the boundary name index after loading',
        )
        parser.add_argument(
            '--save-index',
            action='store_true',
            help='Save the rebuilt index to disk (--rebuild-index implied)',
        )

    def handle(self, *args, **options):
        try:
            import geopandas as gpd
        except ImportError:
            self.stderr.write(self.style.ERROR(
                'geopandas is required: pip install geopandas'
            ))
            return

        source_dir = Path(options['source'])
        if not source_dir.is_dir():
            self.stderr.write(self.style.ERROR(f'Source directory not found: {source_dir}'))
            return

        data_dir = Path(__file__).resolve().parents[2] / 'data_geocoding'
        data_dir.mkdir(exist_ok=True)

        if options.get('country'):
            zips = sorted(source_dir.glob(f"{options['country'].upper()}_adm.zip"))
        else:
            zips = sorted(source_dir.glob('*_adm.zip'))

        if not zips:
            self.stderr.write(self.style.ERROR(f'No *_adm.zip files found in {source_dir}'))
            return

        self.stdout.write(f'Found {len(zips)} GADM ZIP(s) → {data_dir}')
        success = skipped = failed = 0

        for zip_path in zips:
            iso3 = zip_path.stem.split('_')[0].upper()
            out_file = data_dir / f'{iso3}_AdminBoundaries.geojson'

            if out_file.exists() and not options.get('force'):
                self.stdout.write(f'  {iso3}: skip (exists)')
                skipped += 1
                continue

            self.stdout.write(f'  {iso3}: converting ...')
            try:
                count = self._convert(gpd, zip_path, iso3, out_file)
                kb = out_file.stat().st_size / 1024
                self.stdout.write(self.style.SUCCESS(
                    f'  {iso3}: {count} features → {kb:.0f} KB'
                ))
                success += 1
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f'  {iso3}: FAILED — {exc}'))
                logger.exception('GADM conversion failed for %s', iso3)
                failed += 1

        self.stdout.write(self.style.SUCCESS(
            f'\nResult: {success} converted, {skipped} skipped, {failed} failed'
        ))

        if options.get('rebuild_index') or options.get('save_index'):
            self._rebuild_index(save=options.get('save_index', False))

    # ------------------------------------------------------------------ #

    def _convert(self, gpd, zip_path: Path, iso3: str, out_file: Path) -> int:
        """Extract ZIP, read shapefiles, merge admin levels, write GeoJSON."""
        tmp = tempfile.mkdtemp()
        try:
            with zipfile.ZipFile(zip_path) as z:
                z.extractall(tmp)

            features = []
            for level, admin_level in _LEVEL_MAP.items():
                shp = os.path.join(tmp, f'{iso3}_{level}.shp')
                if not os.path.exists(shp):
                    continue

                gdf = gpd.read_file(shp)
                if gdf.crs and gdf.crs.to_epsg() != 4326:
                    gdf = gdf.to_crs(epsg=4326)

                for _, row in gdf.iterrows():
                    if row.geometry is None:
                        continue
                    features.append(_make_feature(row, iso3, admin_level))

            if not features:
                raise ValueError('No features extracted')

            with open(out_file, 'w', encoding='utf-8') as fh:
                json.dump({'type': 'FeatureCollection', 'features': features}, fh)

            return len(features)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _rebuild_index(self, save: bool = False):
        self.stdout.write('\nRebuilding boundary index ...')
        from django.core.cache import cache
        from geolocation.admin_boundary_service import (
            AdminBoundaryGeocoder,
            get_admin_boundary_geocoder,
        )

        cache.delete(AdminBoundaryGeocoder.INDEX_CACHE_KEY)
        geocoder = get_admin_boundary_geocoder()
        geocoder._index = None
        index = geocoder._get_index()
        count = len(index.get('names', {}))
        self.stdout.write(self.style.SUCCESS(f'Index built: {count} names'))

        if save:
            if geocoder.save_index_to_file():
                idx_path = geocoder.data_dir / geocoder.INDEX_FILE_NAME
                mb = idx_path.stat().st_size / 1024 / 1024
                self.stdout.write(self.style.SUCCESS(
                    f'Saved index → {geocoder.INDEX_FILE_NAME} ({mb:.2f} MB)'
                ))
            else:
                self.stderr.write(self.style.ERROR('Failed to save index file'))


# ------------------------------------------------------------------ #

def _make_feature(row, iso3: str, admin_level: str) -> dict:
    """Build a GeoJSON feature from a GADM row with normalised properties."""
    name0 = str(row.get('NAME_0') or '')
    iso_val = str(row.get('ISO') or iso3)

    props = {
        'admin_level': admin_level,
        'NAME_0': name0,
        'ISO': iso_val,
        'country': name0,
    }

    if admin_level in ('4', '6'):
        name1 = str(row.get('NAME_1') or '')
        if name1:
            props['NAME_1'] = name1
        # VARNAME_1 may be pipe-separated alternate names
        var1 = str(row.get('VARNAME_1') or '')
        if var1:
            variants = [v.strip() for v in var1.split('|') if v.strip()]
            if variants:
                props['alt_name'] = variants[0]
                for i, v in enumerate(variants[1:], 1):
                    props[f'alt_name_{i}'] = v

    if admin_level == '6':
        name2 = str(row.get('NAME_2') or '')
        if name2:
            props['NAME_2'] = name2
        var2 = str(row.get('VARNAME_2') or '')
        if var2:
            variants = [v.strip() for v in var2.split('|') if v.strip()]
            if variants:
                props['alt_name'] = variants[0]
                for i, v in enumerate(variants[1:], 1):
                    props[f'alt_name_{i}'] = v

    return {
        'type': 'Feature',
        'properties': props,
        'geometry': row.geometry.__geo_interface__,
    }
