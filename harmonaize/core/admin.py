from django.contrib import admin

# Register your models here.
from .models import Patient, Location, TimeDimension, Attribute, Observation

admin.site.register(Patient)
admin.site.register(Location)
admin.site.register(TimeDimension)
admin.site.register(Attribute)
admin.site.register(Observation)
