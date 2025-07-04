from django.core.management.base import BaseCommand
from geolocation.models import GeolocationData
from core.models import Location

class Command(BaseCommand):
    help = 'Load sample geolocation data'

    def handle(self, *args, **kwargs):
        locations = Location.objects.all()
        if not locations.exists():
            self.stdout.write(self.style.WARNING("No locations found. Load core data first."))
            return

        for location in locations:
            GeolocationData.objects.update_or_create(
                location=location,
                defaults={
                    'country': 'South Africa',
                    'province': 'Gauteng',
                    'district': 'Johannesburg',
                    'city': 'Sandton',
                    'postal_code': '2196',
                    'raw_response': {
                        'source': 'MockGeoService',
                        'accuracy': 'high'
                    }
                }
            )

        self.stdout.write(self.style.SUCCESS("Geolocation sample data loaded successfully."))
