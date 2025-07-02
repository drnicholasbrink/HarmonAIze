import csv
from django.core.management.base import BaseCommand
from geolocation.models import ValidationDataset

class Command(BaseCommand):
    help = 'Load validation data from CSV file'

    def add_arguments(self, parser):
        parser.add_argument('csv_file', type=str)

    def handle(self, *args, **options):
        csv_file_path = options['csv_file']

        with open(csv_file_path, newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                location_name = row['location_name']
                country = row['country']
                
                # Check if the entry already exists
                validation_entry, created = ValidationDataset.objects.get_or_create(
                    location_name=location_name,
                    country=country,
                    defaults={
                        'final_lat': row['final_lat'],
                        'final_long': row['final_long'],
                        'source': row['source'],
                        'state_province': row['state/province'],
                        'county': row['county'],
                        'city_town': row['city/town'],
                        'ward': row['ward'],
                        'suburb_village': row['suburb/village'],
                        'street': row['street'],
                        'house_number': row['house number'],
                        'postal_code': row['postal code'],
                    }
                )
                
                if created:
                    self.stdout.write(self.style.SUCCESS(f'Successfully added: {location_name}, {country}'))
                else:
                    self.stdout.write(self.style.WARNING(f'Entry already exists: {location_name}, {country}'))

        self.stdout.write(self.style.SUCCESS('Finished loading validation data.'))