from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
import ast

class Patient(models.Model):
    """
    Represents an individual patient or participant.
    """
    unique_id = models.CharField(max_length=100, unique=True, help_text="Unique identifier.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Patient {self.unique_id}"

class Location(models.Model):
    """
    Geospatial and administrative info about a place.
    Attributes (e.g., climate, population, etc.) are attached via LocationAttribute.
    """
    name = models.CharField(max_length=200, blank=True, help_text="Place name (clinic, city, etc.)")
    latitude = models.FloatField(
        null=True, blank=True,
        validators=[MinValueValidator(-90.0), MaxValueValidator(90.0)],
        help_text="Latitude in decimal degrees (-90 to 90)."
    )
    longitude = models.FloatField(
        null=True, blank=True,
        validators=[MinValueValidator(-180.0), MaxValueValidator(180.0)],
        help_text="Longitude in decimal degrees (-180 to 180)."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name or f"Location #{self.pk}"

    def clean(self):
        if self.latitude is not None and not (-90.0 <= self.latitude <= 90.0):
            raise ValidationError("Latitude must be between -90 and 90.")
        if self.longitude is not None and not (-180.0 <= self.longitude <= 180.0):
            raise ValidationError("Longitude must be between -180 and 180.")

class TimeDimension(models.Model):
    """
    Flexible time indexing: timestamp or range.
    """
    timestamp = models.DateTimeField(null=True, blank=True)
    start_date = models.DateTimeField(null=True, blank=True)
    end_date = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        if self.timestamp:
            return f"Time {self.timestamp}"
        elif self.start_date and self.end_date:
            return f"Time {self.start_date} to {self.end_date}"
        return f"TimeDimension #{self.pk}"

class Attribute(models.Model):
    """
    Metadata for each variable/attribute.
    """
    variable_name = models.CharField(max_length=200, unique=True)
    display_name = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    unit = models.CharField(max_length=50, blank=True)
    ontology_code = models.CharField(max_length=100, blank=True)
    variable_type = models.CharField(
        max_length=50,
        choices=[
            ('float', 'Float'),
            ('int', 'Integer'),
            ('string', 'String'),
            ('categorical', 'Categorical'),
            ('boolean', 'Boolean'),
            ('datetime', 'Datetime'),
        ]
    )
    #category field to classify attributes from a dropdown list
    category = models.CharField(max_length=100, 
            choices=[
            ('health', 'Health'),
            ('climate', 'Climate'),
            ('geolocation', 'Geolocation'),
        ],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.display_name or self.variable_name

    def clean(self):
        if self.variable_type in ['float', 'int'] and not self.unit:
            raise ValidationError("Unit required for numeric types.")

class Observation(models.Model):
    """
    Central table for a single measurement/observation.
    Can be linked to a patient, location, or other entity.
    """
    patient = models.ForeignKey('Patient', on_delete=models.CASCADE, null=True, blank=True, related_name='observations')
    location = models.ForeignKey('Location', on_delete=models.CASCADE, null=True, blank=True, related_name='observations')
    attribute = models.ForeignKey(Attribute, on_delete=models.CASCADE, related_name='observations')
    time = models.ForeignKey('TimeDimension', on_delete=models.SET_NULL, null=True, blank=True, related_name='observations')
    float_value = models.FloatField(null=True, blank=True)
    int_value = models.IntegerField(null=True, blank=True)
    text_value = models.TextField(blank=True)
    boolean_value = models.BooleanField(null=True, blank=True)
    datetime_value = models.DateTimeField(null=True, blank=True)
    # Automatically track creation and updates
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        entity = self.patient or self.location or "Unknown"
        return f"{self.attribute.display_name} for {entity} at {self.time}"

    def clean(self):
        expected_type = self.attribute.variable_type  # attribute now holds type info directly
        value_fields = {
            'float': self.float_value,
            'int': self.int_value,
            'string': self.text_value,
            'categorical': self.text_value,
            'boolean': self.boolean_value,
            'datetime': self.datetime_value,
        }
        #see which field is populated
        populated_fields = [key for key, value in value_fields.items() if value is not None]
        if expected_type not in populated_fields:
            #convert populated field to expected type
            if expected_type == 'float' and self.int_value is not None:
                self.float_value = float(self.int_value)
            elif expected_type == 'int' and self.float_value is not None:
                self.int_value = int(self.float_value)
            elif expected_type in ['string', 'categorical'] and (self.float_value is not None or self.int_value is not None):
                self.text_value = str(self.float_value or self.int_value)
            elif expected_type == 'boolean' and self.text_value is not None:
                self.boolean_value = ast.literal_eval(self.text_value.lower())
            elif expected_type == 'datetime' and self.text_value is not None:
                try:
                    self.datetime_value = ast.literal_eval(self.text_value)
                except ValueError:
                    raise ValidationError("Invalid datetime format.")
        # Ensure at least one value field is populated
        if not any(value_fields.values()):
            raise ValidationError(f"Observation must have a value for {expected_type} type.")
        if not self.patient and not self.location:
            raise ValidationError("Observation must be linked to a patient or a location.")

    @property
    def value(self):
        expected_type = self.attribute.variable_type
        if expected_type in ['float', 'int']:
            return self.float_value if expected_type == 'float' else self.int_value
        elif expected_type in ['string', 'categorical']:
            return self.text_value
        elif expected_type == 'boolean':
            return self.boolean_value
        elif expected_type == 'datetime':
            return self.datetime_value
        return None

    class Meta:
        unique_together = ('patient', 'location', 'attribute', 'time')
