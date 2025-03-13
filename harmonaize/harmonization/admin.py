from django.contrib import admin

# Register your models here.
# harmonization/admin.py
from .models import (
    Patient,
    Location,
    TimeDimension,
    Attribute,
    Observation
)

@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("unique_id", "date_of_birth", "sex", "created_at", "updated_at")
    search_fields = ("unique_id",)

@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("name", "country", "region", "latitude", "longitude", "altitude")
    search_fields = ("name", "country", "region")
    list_filter = ("country", "region")

@admin.register(TimeDimension)
class TimeDimensionAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "start_date", "end_date", "resolution", "created_at", "updated_at")
    list_filter = ("resolution",)

@admin.register(Attribute)
class AttributeAdmin(admin.ModelAdmin):
    list_display = ("name", "display_name", "category", "unit", "created_at", "updated_at")
    search_fields = ("name", "display_name")
    list_filter = ("category",)

@admin.register(Observation)
class ObservationAdmin(admin.ModelAdmin):
    list_display = ("patient", "attribute", "location", "time", "numeric_value", "text_value", "created_at")
    search_fields = ("text_value",)
    list_filter = ("attribute", "location", "patient")
