from django.db import models
from core.models import Location

class GeolocationData(models.Model):
    """
    Stores external geolocation metadata.
    """
    location = models.OneToOneField(Location, on_delete=models.CASCADE, related_name="geolocation_data")
    country = models.CharField(max_length=100, blank=True)
    province = models.CharField(max_length=100, blank=True)
    district = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    raw_response = models.JSONField(blank=True, null=True)

    def __str__(self):
        return f"Geo info for {self.location.name or self.location.pk}"

class AdminUnit(models.Model):
    """
    Represents an administrative unit (e.g., country, province, district).
    """
    name = models.CharField(max_length=255)
    level = models.IntegerField()
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        return f"{self.name} (Level {self.level})"

class Facility(models.Model):
    """
    Represents a health or other facility linked to a location.
    """
    name = models.CharField(max_length=255)
    location = models.OneToOneField(Location, on_delete=models.CASCADE)
    facility_type = models.CharField(max_length=100)
    code = models.CharField(max_length=50)

    def __str__(self):
        return self.name

class GeoBoundary(models.Model):
    """
    Stores GeoJSON boundary data for admin units.
    """
    admin_unit = models.ForeignKey(AdminUnit, on_delete=models.CASCADE)
    geojson = models.JSONField()

    def __str__(self):
        return f"Boundary for {self.admin_unit.name}"
