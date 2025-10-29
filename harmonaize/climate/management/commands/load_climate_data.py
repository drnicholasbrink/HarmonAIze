from django.core.management.base import BaseCommand
from climate.models import ClimateSource, ClimateIndex, ClimateAggregate
from core.models import Observation, Location, TimeDimension
import random

class Command(BaseCommand):
    help = 'Load sample climate data'

    def handle(self, *args, **kwargs):
        source, _ = ClimateSource.objects.get_or_create(
            name="NOAA",
            defaults={
                'description': "National Oceanic and Atmospheric Administration",
                'source_url': "https://www.noaa.gov"
            }
        )

        observations = Observation.objects.all()
        for obs in observations:
            ClimateIndex.objects.update_or_create(
                observation=obs,
                defaults={
                    'model': source,
                    'index_type': 'Temperature Index',
                    'units': 'Celsius',
                    'notes': 'Synthetic sample index'
                }
            )

        locations = Location.objects.all()
        timeranges = TimeDimension.objects.all()
        if not timeranges.exists():
            self.stdout.write(self.style.WARNING("No time dimensions found. Load core time data first."))
            return

        for loc in locations:
            ClimateAggregate.objects.create(
                location=loc,
                time_range=random.choice(timeranges),
                mean_temperature=random.uniform(15.0, 30.0),
                total_rainfall=random.uniform(0, 200),
                humidity=random.uniform(40, 90),
                derived_from=source
            )

        self.stdout.write(self.style.SUCCESS("Climate sample data loaded successfully."))
