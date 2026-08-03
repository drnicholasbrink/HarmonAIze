# geolocation/models.py
from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
from core.models import Location

User = get_user_model()


class ValidatedDataset(models.Model):
    """
    Arsenal of validated locations (POIs) - builds over time with each validation.

    This serves as a knowledge base of previously validated coordinates that can be:
    - Reused for matching location names
    - Referenced for quality validation
    - Exported as a curated dataset

    Contains locations validated through:
    - Initial data load (e.g., HDX validated locations)
    - User validation workflow
    - Manual coordinate entry
    """
    location_name = models.CharField(max_length=500, db_index=True)
    final_lat = models.FloatField()
    final_long = models.FloatField()
    source = models.CharField(max_length=50)
    country = models.CharField(max_length=100, blank=True)
    state_province = models.CharField(max_length=100, blank=True)
    county = models.CharField(max_length=100, blank=True)
    city_town = models.CharField(max_length=100, blank=True)
    ward = models.CharField(max_length=100, blank=True)
    suburb_village = models.CharField(max_length=100, blank=True)
    street = models.CharField(max_length=100, blank=True)
    house_number = models.CharField(max_length=50, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)

    # User ownership for security
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="validated_locations",
    )

    validated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('location_name', 'country', 'created_by')
        indexes = [
            models.Index(fields=['location_name']),
            models.Index(fields=['country', 'location_name']),
            models.Index(fields=['created_by']),
        ]
        db_table = 'geolocation_validationdataset'
        verbose_name = "Validated Location"
        verbose_name_plural = "Validated Locations (POI Arsenal)"

    def __str__(self):
        return f"{self.location_name} -> {self.final_lat}, {self.final_long}"


class HDXHealthFacility(models.Model):
    """
    HDX Health Facilities dataset - acts as another geocoding source.
    This is populated from HDX data imports and used for facility matching.

    Note: This is reference data shared across all users (no created_by field).
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

    Links to core.Location model for integration with HarmonAIze toolkit.
    """

    VALIDATION_STATUS_CHOICES = [
        ('pending', 'Pending Validation'),
        ('validated', 'Validated'),
        ('rejected', 'Rejected'),
        ('needs_review', 'Needs Manual Review'),
    ]

    LOCATION_TYPE_CHOICES = [
        ('point', 'Point'),
        ('admin_boundary', 'Admin Boundary'),
        ('unknown', 'Unknown'),
    ]

    # Link to core Location model
    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name='geocoding_results',
        help_text="Link to core Location model"
    )

    # User ownership for security
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="geocoding_results",
    )

    location_name = models.CharField(max_length=500, db_index=True)

    # Location type (point vs admin boundary)
    location_type = models.CharField(
        max_length=20,
        choices=LOCATION_TYPE_CHOICES,
        default='unknown',
        help_text="Type of location: point (facility/POI) or admin_boundary (province/district)"
    )

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

    # User-Provided Coordinates (from CSV upload - treated as an independent source)
    user_provided_lat = models.FloatField(
        null=True,
        blank=True,
        help_text="Latitude provided directly in the uploaded CSV file"
    )
    user_provided_lng = models.FloatField(
        null=True,
        blank=True,
        help_text="Longitude provided directly in the uploaded CSV file"
    )
    user_provided_success = models.BooleanField(default=False)
    user_provided_error = models.TextField(blank=True)

    # Admin Boundary Results (5th geocoding source)
    admin_boundary_lat = models.FloatField(
        null=True,
        blank=True,
        help_text="Centroid latitude from admin boundary match"
    )
    admin_boundary_lng = models.FloatField(
        null=True,
        blank=True,
        help_text="Centroid longitude from admin boundary match"
    )
    admin_boundary_success = models.BooleanField(default=False)
    admin_boundary_error = models.TextField(blank=True)
    admin_boundary_match = models.JSONField(
        null=True,
        blank=True,
        help_text="Boundary match details: {source_file, feature_index, osm_id, name, admin_level}"
    )
    admin_level = models.CharField(
        max_length=10,
        blank=True,
        help_text="Administrative level (e.g., '2' for country, '4' for province, '6' for district)"
    )

    # Boundary validation result (for point-in-polygon checks)
    boundary_validation = models.JSONField(
        null=True,
        blank=True,
        help_text="Validation result from boundary checking: {is_valid, actual_country, warnings, severity}"
    )

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

    # Intelligent location parsing results
    parsed_location_data = models.JSONField(
        null=True,
        blank=True,
        help_text="Parsed location components (country, city, facility) from intelligent parsing"
    )

    # Per-source comparison metrics relative to validated ground truth
    source_comparison_metrics = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "Computed after validation: per-source distance to ground truth, accuracy flags, "
            "and inter-source pairwise distances. Used for research/paper metrics."
        )
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
            models.Index(fields=['created_by']),
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
            self.hdx_success,
            self.admin_boundary_success,
            self.user_provided_success,
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
        if self.admin_boundary_success:
            apis.append('admin_boundary')
        if self.user_provided_success:
            apis.append('user_provided')
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
        if self.admin_boundary_success:
            results['admin_boundary'] = {
                'lat': self.admin_boundary_lat,
                'lng': self.admin_boundary_lng,
                'match': self.admin_boundary_match,
                'admin_level': self.admin_level
            }
        return results

    def compute_source_comparison_metrics(self, final_lat, final_lng, selected_source):
        """
        Compute and persist per-source accuracy metrics relative to the accepted ground truth.

        Called immediately after a location is validated so that research exports and
        aggregate metrics endpoints always have pre-computed per-source data available.

        Stores distances to ground truth, accuracy flags at 1 km and 5 km thresholds,
        and pairwise inter-source distances for every source that returned coordinates.
        """
        import math
        from django.utils import timezone

        def _haversine_km(lat1, lng1, lat2, lng2):
            lat1, lng1, lat2, lng2 = map(math.radians, [lat1, lng1, lat2, lng2])
            a = (math.sin((lat2 - lat1) / 2) ** 2
                 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2)
            return 2 * math.asin(math.sqrt(a)) * 6371

        all_sources = ['hdx', 'arcgis', 'google', 'nominatim', 'admin_boundary', 'user_provided']
        source_metrics = {}

        for source in all_sources:
            succeeded = getattr(self, f"{source}_success", False)
            s_lat = getattr(self, f"{source}_lat", None)
            s_lng = getattr(self, f"{source}_lng", None)

            if succeeded and s_lat is not None and s_lng is not None:
                dist_km = _haversine_km(s_lat, s_lng, final_lat, final_lng)
                source_metrics[source] = {
                    'succeeded': True,
                    'coordinates': {'lat': s_lat, 'lng': s_lng},
                    'distance_to_ground_truth_km': round(dist_km, 3),
                    'is_accurate_1km': dist_km <= 1.0,
                    'is_accurate_5km': dist_km <= 5.0,
                    'is_selected': source == selected_source,
                }
            else:
                source_metrics[source] = {
                    'succeeded': False,
                    'coordinates': None,
                    'distance_to_ground_truth_km': None,
                    'is_accurate_1km': None,
                    'is_accurate_5km': None,
                    'is_selected': source == selected_source,
                }

        # Pairwise inter-source distances (all succeeded sources)
        succeeded_sources = {s: m for s, m in source_metrics.items() if m['succeeded']}
        names = list(succeeded_sources.keys())
        pairwise = []
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                s1, s2 = names[i], names[j]
                c1 = succeeded_sources[s1]['coordinates']
                c2 = succeeded_sources[s2]['coordinates']
                pairwise.append({
                    'sources': [s1, s2],
                    'distance_km': round(_haversine_km(c1['lat'], c1['lng'], c2['lat'], c2['lng']), 3),
                })

        max_inter_km = max((p['distance_km'] for p in pairwise), default=0.0)

        sources_within_1km = [s for s, m in source_metrics.items() if m['is_accurate_1km']]
        sources_within_5km = [s for s, m in source_metrics.items() if m['is_accurate_5km']]
        sources_beyond_5km = [s for s, m in source_metrics.items()
                               if m['succeeded'] and not m['is_accurate_5km']]

        self.source_comparison_metrics = {
            'computed_at': timezone.now().isoformat(),
            'ground_truth': {'lat': final_lat, 'lng': final_lng, 'source': selected_source},
            'source_metrics': source_metrics,
            'inter_source_distances': pairwise,
            'summary': {
                'total_sources_attempted': len(all_sources),
                'total_sources_succeeded': len(succeeded_sources),
                'sources_failed': len(all_sources) - len(succeeded_sources),
                'sources_accurate_within_1km': len(sources_within_1km),
                'sources_accurate_within_5km': len(sources_within_5km),
                'sources_beyond_5km_count': len(sources_beyond_5km),
                'max_inter_source_distance_km': round(max_inter_km, 3),
                'has_discrepancy_beyond_5km': max_inter_km > 5.0,
                'sources_within_1km': sources_within_1km,
                'sources_within_5km': sources_within_5km,
                'sources_beyond_5km': sources_beyond_5km,
            },
        }
        self.save(update_fields=['source_comparison_metrics'])
        return self.source_comparison_metrics


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

    # User ownership for security
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="validation_results",
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
            models.Index(fields=['created_by']),
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


class LocationCSVUpload(models.Model):
    """
    Track location CSV file uploads and column mappings.
    Follows the same pattern as health/models.py RawDataFile for consistency.
    """

    PROCESSING_STATUS_CHOICES = [
        ('uploaded', 'Uploaded'),
        ('validated', 'Validated'),
        ('processed', 'Processed'),
        ('ingested', 'Ingested'),
        ('error', 'Error'),
    ]

    # File information
    file = models.FileField(upload_to='location_csv/%Y/%m/%d/')
    original_filename = models.CharField(max_length=255)
    file_format = models.CharField(max_length=20, default='csv')
    file_size = models.PositiveIntegerField(help_text="File size in bytes")

    # Column mappings (user-selected)
    location_name_column = models.CharField(
        max_length=200,
        blank=True,
        help_text="Column containing location names (required)"
    )
    latitude_column = models.CharField(
        max_length=200,
        blank=True,
        help_text="Column containing latitude values (optional)"
    )
    longitude_column = models.CharField(
        max_length=200,
        blank=True,
        help_text="Column containing longitude values (optional)"
    )

    # Detected metadata
    detected_columns = models.JSONField(
        default=list,
        help_text="List of all columns detected in the file"
    )
    total_rows = models.PositiveIntegerField(
        default=0,
        help_text="Total number of data rows in the file"
    )

    # Processing status
    processing_status = models.CharField(
        max_length=30,
        choices=PROCESSING_STATUS_CHOICES,
        default='uploaded'
    )
    processing_message = models.TextField(
        blank=True,
        help_text="Status message or error details"
    )

    # Ingestion results
    locations_created = models.PositiveIntegerField(
        default=0,
        help_text="Number of Location objects created from this upload"
    )
    locations_skipped = models.PositiveIntegerField(
        default=0,
        help_text="Number of rows skipped (duplicates or invalid data)"
    )

    # Duplicate detection
    checksum = models.CharField(
        max_length=64,
        blank=True,
        help_text="SHA256 checksum for duplicate detection"
    )

    # Tracking
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='location_uploads'
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-uploaded_at']
        indexes = [
            models.Index(fields=['uploaded_by', 'uploaded_at']),
            models.Index(fields=['processing_status']),
            models.Index(fields=['checksum']),
        ]
        verbose_name = "Location CSV Upload"
        verbose_name_plural = "Location CSV Uploads"

    def __str__(self):
        return f"{self.original_filename} ({self.processing_status})"

    @property
    def is_processed(self):
        """Check if file has been fully processed."""
        return self.processing_status in ['processed', 'ingested']

    @property
    def has_coordinates(self):
        """Check if upload includes coordinate columns."""
        return bool(self.latitude_column and self.longitude_column)


class LocationCSVColumn(models.Model):
    """
    Metadata about individual columns in a location CSV upload.
    Follows the same pattern as health/models.py RawDataColumn.
    """

    INFERRED_TYPE_CHOICES = [
        ('text', 'Text'),
        ('float', 'Float'),
        ('integer', 'Integer'),
        ('date', 'Date'),
        ('datetime', 'DateTime'),
    ]

    upload = models.ForeignKey(
        LocationCSVUpload,
        on_delete=models.CASCADE,
        related_name='columns'
    )
    column_name = models.CharField(max_length=200)
    column_index = models.PositiveIntegerField(help_text="0-based index in the file")

    # Detected metadata
    inferred_type = models.CharField(
        max_length=50,
        choices=INFERRED_TYPE_CHOICES,
        default='text'
    )
    sample_values = models.JSONField(
        default=list,
        help_text="Sample values from this column for preview"
    )
    non_null_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of non-null values"
    )
    unique_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of unique values"
    )

    # Auto-detection hints
    is_potential_location_name = models.BooleanField(
        default=False,
        help_text="System detected this might be a location name column"
    )
    is_potential_latitude = models.BooleanField(
        default=False,
        help_text="System detected this might be a latitude column"
    )
    is_potential_longitude = models.BooleanField(
        default=False,
        help_text="System detected this might be a longitude column"
    )

    class Meta:
        ordering = ['column_index']
        unique_together = ['upload', 'column_name']
        verbose_name = "Location CSV Column"
        verbose_name_plural = "Location CSV Columns"

    def __str__(self):
        return f"{self.column_name} ({self.inferred_type})"
