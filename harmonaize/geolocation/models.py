# geolocation/models.py
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone


class ValidationDataset(models.Model):
    """
    Historical validated locations - grows with each geocoding task.
    This acts as a cache/knowledge base of previously validated coordinates.
    """
    location_name = models.CharField(max_length=500, db_index=True)
    final_lat = models.FloatField()
    final_long = models.FloatField()
    source = models.CharField(max_length=50)  # e.g., 'hdx', 'google', 'manual', etc.
    country = models.CharField(max_length=100, blank=True)
    state_province = models.CharField(max_length=100, blank=True)
    county = models.CharField(max_length=100, blank=True)
    city_town = models.CharField(max_length=100, blank=True)
    ward = models.CharField(max_length=100, blank=True)
    suburb_village = models.CharField(max_length=100, blank=True)
    street = models.CharField(max_length=100, blank=True)
    house_number = models.CharField(max_length=50, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    validated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('location_name', 'country')
        indexes = [ 
            models.Index(fields=['location_name']),
            models.Index(fields=['country', 'location_name']),
        ]
        db_table = 'geolocation_validationdataset'

    def __str__(self):
        return f"{self.location_name} -> {self.final_lat}, {self.final_long}"


class HDXHealthFacility(models.Model):
    """
    HDX Health Facilities dataset - acts as another geocoding source.
    This is populated from HDX data imports and used for facility matching.
    """
    facility_name = models.CharField(max_length=500, db_index=True)
    facility_type = models.CharField(max_length=200, blank=True)
    ownership = models.CharField(max_length=200, blank=True)
    ward = models.CharField(max_length=200, blank=True)
    district = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=200, blank=True)
    province = models.CharField(max_length=200, blank=True)
    country = models.CharField(max_length=200, blank=True)
    hdx_longitude = models.FloatField()
    hdx_latitude = models.FloatField()
    source = models.CharField(max_length=200, blank=True)
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ('facility_name', 'country', 'district')
        indexes = [
            models.Index(fields=['facility_name']),
            models.Index(fields=['country', 'facility_name']),
            models.Index(fields=['facility_type']),
            models.Index(fields=['district', 'facility_name']),
        ]
        verbose_name = "HDX Health Facility"
        verbose_name_plural = "HDX Health Facilities"
        db_table = 'geolocation_hdxhealthfacility'
    
    def __str__(self):
        return f"{self.facility_name} - {self.district}, {self.country}"
    
    @property
    def coordinates(self):
        """Return coordinates as tuple."""
        return (self.hdx_latitude, self.hdx_longitude)
    
    @property
    def full_address(self):
        """Build full address string."""
        parts = [
            self.facility_name,
            self.ward,
            self.district,
            self.city,
            self.province,
            self.country
        ]
        return ", ".join([part for part in parts if part])


class GeocodingResult(models.Model):
    """
    Stores intermediate geocoding results from different APIs before validation.
    Each location gets one record here with results from all attempted sources.
    """
    
    VALIDATION_STATUS_CHOICES = [
        ('pending', 'Pending Validation'),
        ('validated', 'Validated'),
        ('rejected', 'Rejected'),
        ('needs_review', 'Needs Manual Review'),
    ]
    
    location_name = models.CharField(max_length=500, db_index=True)
    
    # ArcGIS Results
    arcgis_lat = models.FloatField(null=True, blank=True)
    arcgis_lng = models.FloatField(null=True, blank=True)
    arcgis_success = models.BooleanField(default=False)
    arcgis_error = models.TextField(blank=True)
    arcgis_raw_response = models.JSONField(null=True, blank=True)
    
    # Google Results
    google_lat = models.FloatField(null=True, blank=True)
    google_lng = models.FloatField(null=True, blank=True)
    google_success = models.BooleanField(default=False)
    google_error = models.TextField(blank=True)
    google_raw_response = models.JSONField(null=True, blank=True)
    
    # Nominatim (OpenStreetMap) Results
    nominatim_lat = models.FloatField(null=True, blank=True)
    nominatim_lng = models.FloatField(null=True, blank=True)
    nominatim_success = models.BooleanField(default=False)
    nominatim_error = models.TextField(blank=True)
    nominatim_raw_response = models.JSONField(null=True, blank=True)
    
    # HDX Results
    hdx_lat = models.FloatField(null=True, blank=True)
    hdx_lng = models.FloatField(null=True, blank=True)
    hdx_success = models.BooleanField(default=False)
    hdx_error = models.TextField(blank=True)
    hdx_facility_match = models.ForeignKey(
        HDXHealthFacility, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        help_text="Matched HDX facility if found"
    )
    
    # Legacy validation fields (kept for compatibility)
    validation_status = models.CharField(
        max_length=20, 
        choices=VALIDATION_STATUS_CHOICES, 
        default='pending'
    )
    selected_source = models.CharField(max_length=20, blank=True)
    final_lat = models.FloatField(null=True, blank=True)
    final_lng = models.FloatField(null=True, blank=True)
    
    # Enhanced coordinate analysis
    coordinate_variance = models.FloatField(
        null=True, 
        blank=True,
        help_text="Variance between different geocoding sources"
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    validated_by = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['location_name']),
            models.Index(fields=['validation_status']),
            models.Index(fields=['created_at']),
        ]
        db_table = 'geolocation_geocodingresult'
    
    def __str__(self):
        return f"{self.location_name} ({self.validation_status})"
    
    @property
    def has_any_results(self):
        """Check if any API returned results."""
        return any([
            self.arcgis_success, 
            self.google_success, 
            self.nominatim_success,
            self.hdx_success
        ])
    
    @property
    def successful_apis(self):
        """Return list of APIs that returned successful results."""
        apis = []
        if self.arcgis_success:
            apis.append('arcgis')
        if self.google_success:
            apis.append('google')
        if self.nominatim_success:
            apis.append('nominatim')
        if self.hdx_success:
            apis.append('hdx')
        return apis
    
    @property
    def results_summary(self):
        """Return a summary of all results."""
        results = {}
        if self.arcgis_success:
            results['arcgis'] = {'lat': self.arcgis_lat, 'lng': self.arcgis_lng}
        if self.google_success:
            results['google'] = {'lat': self.google_lat, 'lng': self.google_lng}
        if self.nominatim_success:
            results['nominatim'] = {'lat': self.nominatim_lat, 'lng': self.nominatim_lng}
        if self.hdx_success:
            results['hdx'] = {'lat': self.hdx_lat, 'lng': self.hdx_lng}
        return results


class ValidationResult(models.Model):
    """
    Store AI validation results for geocoding results with enhanced analysis.
    This is where the AI validation logic stores its analysis and recommendations.
    """
    
    VALIDATION_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('validated', 'Validated'),
        ('needs_review', 'Needs Review'),
        ('rejected', 'Rejected'),
        ('manual_override', 'Manual Override'),
    ]
    
    geocoding_result = models.OneToOneField(
        GeocodingResult,
        on_delete=models.CASCADE,
        related_name='validation'
    )
    
    # AI-generated confidence scores
    confidence_score = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="Overall confidence score (0.0 to 1.0)"
    )
    
    api_agreement_score = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="How well different APIs agree on coordinates"
    )
    
    reverse_geocoding_score = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="Score from reverse geocoding validation (0.0 to 1.0)"
    )
    
    distance_confidence = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="Confidence based on coordinate clustering"
    )
    
    source_reliability_score = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="Reliability score of the recommended source"
    )
    
    # AI recommendations
    recommended_lat = models.FloatField(null=True, blank=True)
    recommended_lng = models.FloatField(null=True, blank=True)
    recommended_source = models.CharField(max_length=20, blank=True)
    
    # Validation status and metadata
    validation_status = models.CharField(
        max_length=20,
        choices=VALIDATION_STATUS_CHOICES,
        default='pending'
    )
    
    validation_metadata = models.JSONField(
        null=True,
        blank=True,
        help_text="Enhanced validation data including reverse geocoding results and AI analysis"
    )
    
    # Manual review fields
    manual_review_notes = models.TextField(blank=True)
    manual_lat = models.FloatField(null=True, blank=True)
    manual_lng = models.FloatField(null=True, blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    validated_by = models.CharField(max_length=100, blank=True)
    
    class Meta:
        db_table = 'geolocation_validation_result'
        ordering = ['-confidence_score', 'geocoding_result__location_name']
        indexes = [
            models.Index(fields=['validation_status']),
            models.Index(fields=['confidence_score']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        return f"Validation for {self.geocoding_result.location_name} (Confidence: {self.confidence_score:.2f})"
    
    @property
    def final_coordinates(self):
        """Get the final coordinates to use (manual override > recommended)."""
        if self.manual_lat is not None and self.manual_lng is not None:
            return (self.manual_lat, self.manual_lng)
        elif self.recommended_lat is not None and self.recommended_lng is not None:
            return (self.recommended_lat, self.recommended_lng)
        return None
    
    @property
    def needs_attention(self):
        """Check if this validation result needs human attention."""
        return (
            self.validation_status in ['needs_review', 'pending'] or
            self.confidence_score < 0.6
        )
    
    @property
    def confidence_level(self):
        """Get human-readable confidence level."""
        if self.confidence_score >= 0.8:
            return 'High'
        elif self.confidence_score >= 0.6:
            return 'Medium'
        else:
            return 'Low'
    
    @property
    def reverse_geocoding_results(self):
        """Extract reverse geocoding results from metadata."""
        if self.validation_metadata:
            return self.validation_metadata.get('reverse_geocoding_results', {})
        return {}
    
    @property
    def coordinates_analysis(self):
        """Extract coordinates analysis from metadata."""
        if self.validation_metadata:
            return self.validation_metadata.get('coordinates_analysis', {})
        return {}
    
    @property
    def ai_recommendation(self):
        """Extract AI recommendation from metadata."""
        if self.validation_metadata:
            return self.validation_metadata.get('recommendation', {})
        return {}

    def save(self, *args, **kwargs):
        """Override save to automatically set validated_at when status changes."""
        if self.validation_status == 'validated' and not self.validated_at:
            self.validated_at = timezone.now()
        super().save(*args, **kwargs)