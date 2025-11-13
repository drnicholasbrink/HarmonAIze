import csv
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from geolocation.models import ValidatedDataset

User = get_user_model()


class Command(BaseCommand):
    help = 'Load validated location data (POI arsenal) from CSV file'

    def add_arguments(self, parser):
        parser.add_argument('csv_file', type=str, nargs='?', default=None)

    def handle(self, *args, **options):
        csv_file_path = options.get('csv_file')

        # If no file specified, try default location
        if not csv_file_path:
            import os
            default_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                'data_geocoding',
                'Validated_locations.csv'
            )
            if os.path.exists(default_path):
                csv_file_path = default_path
            else:
                return  # Silent exit if no file

        if not csv_file_path or not os.path.exists(csv_file_path):
            return  # Silent exit

        # Get or create system user for data loading
        system_user, _ = User.objects.get_or_create(
            email='system@harmonaize.org',
            defaults={
                'name': 'System',
                'is_active': False,  # Inactive system user
            }
        )

        with open(csv_file_path, newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                try:
                    location_name = row.get('location_name', '').strip()
                    country = row.get('country', '').strip()

                    if not location_name or not country:
                        continue

                    # Check if the entry already exists for this user
                    validation_entry, created = ValidatedDataset.objects.get_or_create(
                        location_name=location_name,
                        country=country,
                        created_by=system_user,
                        defaults={
                            'final_lat': float(row.get('final_lat', 0)),
                            'final_long': float(row.get('final_long', 0)),
                            'source': row.get('source', 'Imported'),
                            'state_province': row.get('state/province', ''),
                            'county': row.get('county', ''),
                            'city_town': row.get('city/town', ''),
                            'ward': row.get('ward', ''),
                            'suburb_village': row.get('suburb/village', ''),
                            'street': row.get('street', ''),
                            'house_number': row.get('house number', ''),
                            'postal_code': row.get('postal code', ''),
                        }
                    )
                except Exception:
                    continue  # Silent failure for individual rows

                # Silent loading - no console output