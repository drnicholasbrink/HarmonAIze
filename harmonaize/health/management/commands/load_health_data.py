from django.core.management.base import BaseCommand
from health.models import HealthCondition, ImmunizationRecord
from core.models import Patient
from datetime import date
import random

class Command(BaseCommand):
    help = 'Load sample data for health app'

    def handle(self, *args, **kwargs):
        patients = Patient.objects.all()
        if not patients.exists():
            self.stdout.write(self.style.WARNING("No patients found. Load core data first."))
            return

        for patient in patients:
            HealthCondition.objects.create(
                patient=patient,
                name="Hypertension",
                diagnosis_date=date(2020, 5, 20),
                notes="Routine checkup diagnosis."
            )

            ImmunizationRecord.objects.create(
                patient=patient,
                vaccine_name="COVID-19 Vaccine",
                date_administered=date(2021, 6, 15),
                administered_by="Dr. Smith"
            )

        self.stdout.write(self.style.SUCCESS("Health sample data loaded successfully."))
