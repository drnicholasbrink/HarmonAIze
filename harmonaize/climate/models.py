from django.db import models
from core.models import Attribute, Observation, Location, TimeDimension

class ClimateSource(models.Model):
    """
    Represents a climate data provider (e.g. ECMWF, NOAA).
    """
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    source_url = models.TextField(blank=True)

    def __str__(self):
        return self.name

class ClimateIndex(models.Model):
    """
    Represents a specific climate observation index.
    """
    observation = models.OneToOneField(Observation, on_delete=models.CASCADE)
    model = models.ForeignKey(ClimateSource, on_delete=models.SET_NULL, null=True)
    index_type = models.CharField(max_length=100)
    units = models.CharField(max_length=50, blank=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.index_type} from {self.model}"

class ClimateAggregate(models.Model):
    """
    Aggregated climate data over a time range.
    """
    location = models.ForeignKey(Location, on_delete=models.CASCADE)
    time_range = models.ForeignKey(TimeDimension, on_delete=models.SET_NULL, null=True)
    mean_temperature = models.FloatField(null=True, blank=True)
    total_rainfall = models.FloatField(null=True, blank=True)
    humidity = models.FloatField(null=True, blank=True)
    derived_from = models.ForeignKey(ClimateSource, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return f"Climate Aggregate for {self.location} ({self.time_range})"
