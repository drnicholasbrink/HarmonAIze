"""
Load HOT OSM Points of Interest as geocoding sources.

Reads hotosm_*_points_of_interest_*.zip files and inserts named POIs
into HDXHealthFacility so the geocoding engine can match facility names
against them (alongside HDX data).

Usage:
    python manage.py load_hotosm_poi
    python manage.py load_hotosm_poi --source /path/to/zips
    python manage.py load_hotosm_poi --clear  # remove old HOTOSM rows first
    python manage.py load_hotosm_poi --all    # include unnamed POIs too
"""

import logging
import shutil
import tempfile
import zipfile
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

logger = logging.getLogger(__name__)

_DEFAULT_SOURCE = '/home/rutendo/PRECISE'

# HOT OSM tag fields that identify POI type
_TYPE_FIELDS = ('amenity', 'man_made', 'shop', 'tourism')


class Command(BaseCommand):
    help = 'Load HOT OSM POI ZIP files into the HDX health-facility geocoding index'

    def add_arguments(self, parser):
        parser.add_argument(
            '--source',
            default=_DEFAULT_SOURCE,
            help=f'Directory containing hotosm_*_points_of_interest_*.zip (default: {_DEFAULT_SOURCE})',
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Delete existing HOTOSM records before loading',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Load all POIs, not just those with a name',
        )

    def handle(self, *args, **options):
        try:
            import geopandas as gpd
        except ImportError:
            self.stderr.write(self.style.ERROR('geopandas required: pip install geopandas'))
            return

        from geolocation.models import HDXHealthFacility

        source_dir = Path(options['source'])
        if not source_dir.is_dir():
            self.stderr.write(self.style.ERROR(f'Source directory not found: {source_dir}'))
            return

        if options.get('clear'):
            n = HDXHealthFacility.objects.filter(source='HOTOSM').count()
            HDXHealthFacility.objects.filter(source='HOTOSM').delete()
            self.stdout.write(f'Cleared {n} existing HOTOSM records')

        zips = sorted(source_dir.glob('hotosm_*_points_of_interest_*.zip'))
        if not zips:
            self.stderr.write(self.style.ERROR(
                f'No hotosm_*_points_of_interest_*.zip files found in {source_dir}'
            ))
            return

        named_only = not options.get('all', False)
        total = 0

        for zip_path in zips:
            self.stdout.write(f'Loading {zip_path.name} ...')
            try:
                n = _load_zip(gpd, zip_path, named_only=named_only)
                self.stdout.write(self.style.SUCCESS(f'  {n} POIs imported'))
                total += n
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f'  FAILED — {exc}'))
                logger.exception('HOT OSM load failed: %s', zip_path.name)

        self.stdout.write(self.style.SUCCESS(f'\nTotal imported: {total} POIs'))


# ------------------------------------------------------------------ #

def _load_zip(gpd, zip_path: Path, named_only: bool = True) -> int:
    from geolocation.models import HDXHealthFacility

    tmp = tempfile.mkdtemp()
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)

        # Select the best shapefile: prefer one whose stem ends with '_points',
        # then '_polygons'; avoid '_lines'. Fall back to any .shp present.
        all_shp = sorted(Path(tmp).glob('*.shp'))
        if not all_shp:
            raise FileNotFoundError('No .shp file found in archive')

        def _rank(p):
            s = p.stem.lower()
            if s.endswith('_points') or s.endswith('_point'):
                return 0
            if s.endswith('_polygons') or s.endswith('_polygon'):
                return 1
            if s.endswith('_lines') or s.endswith('_line'):
                return 3
            return 2

        chosen_shp = sorted(all_shp, key=_rank)[0]

        gdf = gpd.read_file(str(chosen_shp))
        if gdf.crs and gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(epsg=4326)

        # Convert any non-Point geometries to centroids
        geom_types = set(gdf.geom_type.dropna().unique())
        if geom_types - {'Point', 'MultiPoint'}:
            gdf_proj = gdf.to_crs(epsg=3857)
            centroids = gdf_proj.geometry.centroid.to_crs(epsg=4326)
            gdf = gdf.copy()
            gdf['geometry'] = centroids

        batch = []
        imported = 0

        for _, row in gdf.iterrows():
            if row.geometry is None:
                continue

            name = _coalesce(row, 'name', 'name_en') or ''
            name = name.strip()

            if named_only and not name:
                continue

            if not name:
                # Fall back to type tag as a label (e.g. "amenity:hospital")
                for field in _TYPE_FIELDS:
                    val = _str(row.get(field))
                    if val:
                        name = f'{field}:{val}'
                        break

            if not name:
                continue

            facility_type = ''
            for field in _TYPE_FIELDS:
                val = _str(row.get(field))
                if val:
                    facility_type = val
                    break

            batch.append(HDXHealthFacility(
                facility_name=name[:500],
                facility_type=facility_type[:200],
                ownership='',
                ward='',
                district=_str(row.get('adm2_name'))[:200],
                city=_str(row.get('adm3_name'))[:200],
                province=_str(row.get('adm1_name'))[:200],
                country=_str(row.get('adm0_name'))[:200],
                hdx_latitude=float(row.geometry.y),
                hdx_longitude=float(row.geometry.x),
                source='HOTOSM',
            ))

            if len(batch) >= 500:
                with transaction.atomic():
                    HDXHealthFacility.objects.bulk_create(
                        batch, ignore_conflicts=True
                    )
                imported += len(batch)
                batch = []

        if batch:
            with transaction.atomic():
                HDXHealthFacility.objects.bulk_create(
                    batch, ignore_conflicts=True
                )
            imported += len(batch)

        return imported

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _coalesce(row, *fields):
    for f in fields:
        val = row.get(f)
        if val and str(val).strip().lower() not in ('none', 'nan', ''):
            return str(val).strip()
    return None


def _str(val) -> str:
    if val is None:
        return ''
    s = str(val).strip()
    return '' if s.lower() in ('none', 'nan') else s
