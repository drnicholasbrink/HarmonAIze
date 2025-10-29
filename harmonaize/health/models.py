from django.db import models
from core.models import Patient, Observation, Attribute, TimeDimension, Study
from core.models import Location  # used in harmonized record

class HealthCondition(models.Model):
    """
    Represents a diagnosed health condition or disease.
    """
    name = models.CharField(max_length=255)
    icd10_code = models.CharField(max_length=20, blank=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name

class HarmonizedHealthRecord(models.Model):
    """
    Harmonized patient health record linked to core concepts.
    """
    patient = models.ForeignKey(Patient, on_delete=models.SET_NULL, null=True)
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True)
    condition = models.ForeignKey(HealthCondition, on_delete=models.SET_NULL, null=True)
    time = models.ForeignKey(TimeDimension, on_delete=models.SET_NULL, null=True)
    age = models.IntegerField(null=True, blank=True)
    gender = models.CharField(max_length=10, blank=True)
    outcome = models.CharField(max_length=100, blank=True)
    source_observation = models.ForeignKey(Observation, on_delete=models.SET_NULL, null=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.condition} for {self.patient}"

class ImmunizationRecord(models.Model):
    """
    Tracks immunizations for a patient.
    """
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="immunizations")
    vaccine_name = models.CharField(max_length=200)
    date_administered = models.DateField()
    administered_by = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return f"{self.vaccine_name} for {self.patient.unique_id}"

class HealthStudyMetadata(models.Model):
    """
    Extends the Study model with health-specific metadata.
    """
    study = models.OneToOneField(Study, on_delete=models.CASCADE, related_name="health_metadata")
    includes_genetic_data = models.BooleanField(default=False)
    includes_lab_results = models.BooleanField(default=False)

    def __str__(self):
        return f"Health metadata for {self.study.name}"
