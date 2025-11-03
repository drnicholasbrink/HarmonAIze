import csv
import requests
import os
import tempfile
from django.core.management.base import BaseCommand
from django.db import transaction
from geolocation.models import HDXHealthFacility
class Command(BaseCommand):
    help = 'Import HDX Health Facilities data from CSV file or URL'

    def add_arguments(self, parser):
        parser.add_argument(
            '--file',
            type=str,
            help='Path to local CSV file containing HDX health facilities data'
        )
        parser.add_argument(
            '--url',
            type=str,
            help='URL to download HDX health facilities CSV data'
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing HDX facilities data before importing'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be imported without actually importing'
        )

    def handle(self, *args, **options):

        if not options['file'] and not options['url']:
            return

        # Clear existing data if requested (skip interactive prompt during auto-load)
        if options['clear'] and not options['dry_run']:
            count = HDXHealthFacility.objects.count()
            if count > 0:
                HDXHealthFacility.objects.all().delete()

        # Get CSV file path
        if options['url']:
            csv_file_path = self.download_csv(options['url'])
        else:
            csv_file_path = options['file']

        if not csv_file_path or not os.path.exists(csv_file_path):
            return

        # Import data
        try:
            self.import_csv(csv_file_path, options['dry_run'])
        except Exception:
            pass  # Silent failure
        finally:

            if options['url'] and csv_file_path and os.path.exists(csv_file_path):
                os.remove(csv_file_path)

    def download_csv(self, url):
        """Download CSV from URL to temporary file."""
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()

            # Create temporary file
            temp_file = tempfile.NamedTemporaryFile(mode='w+', suffix='.csv', delete=False)
            temp_file.write(response.text)
            temp_file.close()

            return temp_file.name

        except requests.RequestException:
            return None

    def import_csv(self, file_path, dry_run=False):
        """Import HDX facilities from CSV file."""
        imported = 0
        updated = 0
        errors = 0

        with open(file_path, 'r', encoding='utf-8') as csvfile:

            sample = csvfile.read(1024)
            csvfile.seek(0)

            # Detect delimiter
            delimiter = ','
            if '\t' in sample:
                delimiter = '\t'
            elif ';' in sample:
                delimiter = ';'

            reader = csv.DictReader(csvfile, delimiter=delimiter)

            # Define column mapping (flexible to handle different CSV formats)
            column_mapping = self.get_column_mapping(reader.fieldnames)

            if not column_mapping['facility_name'] or not column_mapping['latitude'] or not column_mapping['longitude']:
                return

            # Process rows
            for row_num, row in enumerate(reader, 2):  # Start from row 2 (after header)
                try:

                    facility_data = self.extract_facility_data(row, column_mapping)

                    if not facility_data:
                        errors += 1
                        continue

                    if dry_run:
                        imported += 1
                        continue

                    # Create or update facility
                    with transaction.atomic():
                        facility, created = HDXHealthFacility.objects.update_or_create(
                            facility_name=facility_data['facility_name'],
                            country=facility_data['country'],
                            district=facility_data['district'],
                            defaults=facility_data
                        )

                        if created:
                            imported += 1
                        else:
                            updated += 1

                except Exception:
                    errors += 1

        # Silent completion - no summary output

    def get_column_mapping(self, fieldnames):
        """Map CSV columns to model fields based on common naming patterns."""
        mapping = {
            'facility_name': None,
            'facility_type': None,
            'ownership': None,
            'ward': None,
            'district': None,
            'city': None,
            'province': None,
            'country': None,
            'latitude': None,
            'longitude': None,
            'source': None
        }

        # Convert to lowercase for easier matching
        fieldnames_lower = [f.lower() for f in fieldnames]
        

        name_patterns = {
            'facility_name': ['facility_name', 'name', 'facility', 'health_facility', 'hospital_name'],
            'facility_type': ['facility_type', 'type', 'facility_category', 'category'],
            'ownership': ['ownership', 'owner', 'management'],
            'ward': ['ward', 'sub_district'],
            'district': ['district', 'admin2', 'administrative_area'],
            'city': ['city', 'town', 'municipality'],
            'province': ['province', 'state', 'region', 'admin1'],
            'country': ['country', 'nation', 'iso3'],
            'latitude': ['latitude', 'lat', 'y_coord', 'y'],
            'longitude': ['longitude', 'lng', 'lon', 'long', 'x_coord', 'x'],
            'source': ['source', 'data_source', 'origin']
        }

        # Find best matches
        for field, patterns in name_patterns.items():
            for pattern in patterns:
                for i, col_name in enumerate(fieldnames_lower):
                    if pattern in col_name or col_name in pattern:
                        mapping[field] = fieldnames[i]  # Use original case
                        break
                if mapping[field]:
                    break

        return mapping

    def extract_facility_data(self, row, column_mapping):
        """Extract and validate facility data from CSV row."""
        try:

            facility_name = self.get_field_value(row, column_mapping['facility_name'])
            if not facility_name:
                return None

            # Get coordinates
            latitude = self.get_field_value(row, column_mapping['latitude'])
            longitude = self.get_field_value(row, column_mapping['longitude'])
            
            if not latitude or not longitude:
                return None

            # Convert coordinates to float
            try:
                latitude = float(latitude)
                longitude = float(longitude)
            except (ValueError, TypeError):
                return None

            # Validate coordinate ranges
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                return None

            # Extract other fields
            facility_data = {
                'facility_name': facility_name.strip(),
                'facility_type': self.get_field_value(row, column_mapping['facility_type']) or '',
                'ownership': self.get_field_value(row, column_mapping['ownership']) or '',
                'ward': self.get_field_value(row, column_mapping['ward']) or '',
                'district': self.get_field_value(row, column_mapping['district']) or '',
                'city': self.get_field_value(row, column_mapping['city']) or '',
                'province': self.get_field_value(row, column_mapping['province']) or '',
                'country': self.get_field_value(row, column_mapping['country']) or '',
                'hdx_latitude': latitude,
                'hdx_longitude': longitude,
                'source': self.get_field_value(row, column_mapping['source']) or 'HDX_Import'
            }

            return facility_data

        except Exception:
            return None

    def get_field_value(self, row, field_name):
        """Safely get field value from CSV row."""
        if not field_name or field_name not in row:
            return None
        
        value = row[field_name]
        if isinstance(value, str):
            value = value.strip()
            if value.lower() in ['', 'null', 'none', 'n/a', 'na']:
                return None
        
        return value