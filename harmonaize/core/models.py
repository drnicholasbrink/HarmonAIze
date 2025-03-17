from django.db import models


class Patient(models.Model):
    """
    Represents an individual patient or participant in the dataset.

    In real-world scenarios, you may store only a unique identifier 
    (like a hashed ID) to avoid storing personally identifiable 
    information here. Additional demographic fields (age, sex) can 
    also be stored if relevant, but be mindful of privacy regulations.
    """
    unique_id = models.CharField(
        max_length=100, 
        unique=True,
        help_text="A unique identifier for the patient (not necessarily PHI)."
    )
    date_of_birth = models.DateField(null=True, blank=True)
    sex = models.CharField(
        max_length=10, 
        blank=True, 
        help_text="e.g., 'M', 'F', 'Other', or 'Unknown'"
    )

    # Optionally link to a 'home location' if relevant
    # home_location = models.ForeignKey('Location', on_delete=models.SET_NULL, null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Patient {self.unique_id}"


class Location(models.Model):
    """
    Stores geospatial and administrative info about a place,
    which could be a clinic, city, region, country, etc.
    """
    name = models.CharField(max_length=200, help_text="Place name (e.g. clinic, city, region).", blank=True)
    country = models.CharField(max_length=100, blank=True)
    region = models.CharField(max_length=100, blank=True, help_text="State/province or large administrative area")
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    altitude = models.FloatField(null=True, blank=True, help_text="Altitude in meters")

    # Hierarchical relationships if you want parent-child locations
    parent_location = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sub_locations',
        help_text="Optional parent location for hierarchical grouping."
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name if self.name else f"Location #{self.pk}"


class TimeDimension(models.Model):
    """
    Handles time indexing in a flexible way.
    You might store a single datetime, or a start-end range, 
    or even year-month-day-hour fields for advanced queries.
    """
    # For a single time point
    timestamp = models.DateTimeField(
        null=True,
        blank=True,
        help_text="If you have an exact timestamp for the observation."
    )

    # Optionally store a range
    start_date = models.DateTimeField(null=True, blank=True)
    end_date = models.DateTimeField(null=True, blank=True)

    # You can store a reference to the resolution (daily, monthly, etc.)
    resolution = models.CharField(
        max_length=50,
        blank=True,
        help_text="E.g., 'daily', 'monthly', 'annual' for climate data."
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        if self.timestamp:
            return f"TimeDimension (timestamp={self.timestamp})"
        elif self.start_date and self.end_date:
            return f"TimeDimension (range={self.start_date} to {self.end_date})"
        return f"TimeDimension #{self.pk}"


class Attribute(models.Model):
    """
    A flexible model describing what kind of attribute is being observed. 
    This could be 'blood_pressure_systolic', 'HIV_status', 'max_temp', etc.

    The category field helps differentiate between clinical vs. climate vs. other categories.
    The unit field is optional but helps interpret numeric values consistently.
    """
    name = models.CharField(max_length=200, help_text="Name of the attribute (e.g. 'blood_pressure_systolic').")
    display_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="A user-friendly label (e.g. 'Systolic Blood Pressure')."
    )
    category = models.CharField(
        max_length=100,
        blank=True,
        help_text="e.g., 'clinical', 'climate', 'demographic', etc."
    )
    unit = models.CharField(
        max_length=50, 
        blank=True,
        help_text="Unit of measurement if numeric (e.g. 'mmHg', 'Celsius', etc.)"
    )

    # Potential for linking to external ontologies
    # ontology_ref = models.URLField(blank=True, help_text="Link to an external ontology definition if relevant.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Observation(models.Model):
    """
    The central table capturing a single 'measurement' or 'observation' 
    for a given patient at a certain time & location, about a specific attribute.

    We allow multiple ways to store the 'value':
     - numeric_value for float/integer
     - text_value for strings (lab result text, categories)
     - possibly a JSON field for more complex data, if needed
    """
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='observations')
    attribute = models.ForeignKey(Attribute, on_delete=models.CASCADE, related_name='observations')
    
    # In some scenarios, location might be more relevant to climate data or clinic site.
    # It's optional in case some observations only have a time, no specific place.
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True, related_name='observations')

    # If you need to link an observation to a time or time range, reference TimeDimension.
    time = models.ForeignKey(TimeDimension, on_delete=models.SET_NULL, null=True, blank=True, related_name='observations')

    # Store the actual measurement or data.
    numeric_value = models.FloatField(null=True, blank=True, help_text="If the observation is numeric.")
    text_value = models.TextField(blank=True, help_text="If the observation is textual or categorical.")

    # Optionally track the data type to interpret or enforce constraints 
    # (could also rely on attribute.unit or attribute.category).
    data_type = models.CharField(
        max_length=50,
        blank=True,
        help_text="e.g., 'float', 'int', 'string', 'categorical'."
    )
    
    # Timestamps for the record itself, not the measurement time (which is in TimeDimension).
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.attribute.name} for {self.patient} at {self.time}"

    class Meta:
        """
        You can define unique_together or indexes to avoid duplicate 
        observations if that fits your domain logic:
        
        unique_together = ('patient', 'attribute', 'time', 'location')
        """
        pass
