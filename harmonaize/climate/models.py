from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
from core.models import Study, Location, TimeDimension, Attribute, Observation


class ClimateDataSource(models.Model):
    """
    Represents an external climate data source (e.g., GEE, OpenWeather, ERA5).
    """
    SOURCE_TYPE_CHOICES = [
        ('gee', 'Google Earth Engine'),
        ('openweather', 'OpenWeather Climate'),
        ('era5', 'ERA5 Reanalysis'),
        ('worldclim', 'WorldClim'),
        ('chirps', 'CHIRPS Precipitation'),
        ('modis', 'MODIS Satellite'),
        ('custom', 'Custom API'),
    ]
    
    name = models.CharField(max_length=200, unique=True, help_text="Name of the climate data source")
    source_type = models.CharField(max_length=50, choices=SOURCE_TYPE_CHOICES)
    description = models.TextField(help_text="Description of the data source and its capabilities")
    
    # API Configuration
    api_endpoint = models.URLField(blank=True, help_text="Base API endpoint URL")
    api_key = models.CharField(max_length=500, blank=True, help_text="API key or credentials (encrypted)")
    requires_authentication = models.BooleanField(default=True)
    
    # Data characteristics
    spatial_resolution_m = models.FloatField(
        null=True, blank=True,
        help_text="Spatial resolution in metres"
    )
    temporal_resolution_days = models.FloatField(
        null=True, blank=True,
        help_text="Temporal resolution in days"
    )
    data_start_date = models.DateField(null=True, blank=True, help_text="Earliest available data")
    data_end_date = models.DateField(null=True, blank=True, help_text="Latest available data")
    
    # Coverage
    global_coverage = models.BooleanField(default=True, help_text="Does this source provide global coverage?")
    coverage_description = models.TextField(blank=True, help_text="Geographic coverage details")
    
    # Status
    is_active = models.BooleanField(default=True, help_text="Is this data source currently available?")
    last_checked = models.DateTimeField(null=True, blank=True, help_text="Last availability check")
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = "Climate Data Source"
        verbose_name_plural = "Climate Data Sources"
    
    def __str__(self):
        return f"{self.name} ({self.get_source_type_display()})"


class ClimateVariable(models.Model):
    """
    Defines available climate variables that can be retrieved from data sources.
    """
    VARIABLE_CATEGORY_CHOICES = [
        ('temperature', 'Temperature'),
        ('precipitation', 'Precipitation'),
        ('humidity', 'Humidity'),
        ('wind', 'Wind'),
        ('solar', 'Solar Radiation'),
        ('vegetation', 'Vegetation Index'),
        ('air_quality', 'Air Quality'),
        ('extreme_events', 'Extreme Events'),
        ('other', 'Other'),
    ]
    
    # Basic information
    name = models.CharField(max_length=100, unique=True, help_text="Variable name (e.g., 'temperature_2m')")
    display_name = models.CharField(max_length=200, help_text="Human-readable name")
    description = models.TextField(help_text="Detailed description of the variable")
    category = models.CharField(max_length=50, choices=VARIABLE_CATEGORY_CHOICES)
    
    # Units and measurement
    unit = models.CharField(max_length=50, help_text="Measurement unit (e.g., '°C', 'mm', 'm/s')")
    unit_symbol = models.CharField(max_length=20, help_text="Unit symbol for display")
    
    # Data characteristics
    min_value = models.FloatField(null=True, blank=True, help_text="Typical minimum value")
    max_value = models.FloatField(null=True, blank=True, help_text="Typical maximum value")
    
    # Source mapping
    data_sources = models.ManyToManyField(
        ClimateDataSource,
        through='ClimateVariableMapping',
        related_name='variables',
        help_text="Data sources that provide this variable"
    )
    
    # Aggregation options
    supports_temporal_aggregation = models.BooleanField(
        default=True,
        help_text="Can this variable be aggregated over time?"
    )
    supports_spatial_aggregation = models.BooleanField(
        default=True,
        help_text="Can this variable be aggregated spatially?"
    )
    default_aggregation_method = models.CharField(
        max_length=20,
        choices=[
            ('mean', 'Mean'),
            ('sum', 'Sum'),
            ('min', 'Minimum'),
            ('max', 'Maximum'),
            ('median', 'Median'),
        ],
        default='mean'
    )
    
    # Health relevance
    health_relevance = models.TextField(
        blank=True,
        help_text="Description of how this variable relates to health outcomes"
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['category', 'display_name']
        verbose_name = "Climate Variable"
        verbose_name_plural = "Climate Variables"
    
    def __str__(self):
        return f"{self.display_name} ({self.unit_symbol})"


class ClimateVariableMapping(models.Model):
    """
    Maps climate variables to their specific identifiers in different data sources.
    """
    variable = models.ForeignKey(ClimateVariable, on_delete=models.CASCADE)
    data_source = models.ForeignKey(ClimateDataSource, on_delete=models.CASCADE)
    
    # Source-specific information
    source_variable_name = models.CharField(
        max_length=200,
        help_text="Variable name in the source system"
    )
    source_dataset = models.CharField(
        max_length=200,
        blank=True,
        help_text="Dataset or collection name in the source"
    )
    source_band = models.CharField(
        max_length=100,
        blank=True,
        help_text="Band name for multi-band datasets"
    )
    
    # Processing parameters
    scale_factor = models.FloatField(
        default=1.0,
        help_text="Multiplication factor to convert to standard units"
    )
    offset = models.FloatField(
        default=0.0,
        help_text="Offset to add after scaling"
    )
    
    # Additional configuration
    extra_parameters = models.JSONField(
        default=dict,
        blank=True,
        help_text="Source-specific parameters as JSON"
    )
    
    class Meta:
        unique_together = ('variable', 'data_source')
        verbose_name = "Variable Mapping"
        verbose_name_plural = "Variable Mappings"
    
    def __str__(self):
        return f"{self.variable.name} in {self.data_source.name}"


class ClimateDataRequest(models.Model):
    """
    Tracks climate data retrieval requests for studies.
    """
    REQUEST_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]
    
    # Study linkage
    study = models.ForeignKey(
        Study,
        on_delete=models.CASCADE,
        related_name='climate_requests',
        help_text="Study requesting climate data"
    )
    
    # Request configuration
    data_source = models.ForeignKey(
        ClimateDataSource,
        on_delete=models.SET_NULL,
        null=True,
        help_text="Climate data source to use"
    )
    variables = models.ManyToManyField(
        ClimateVariable,
        help_text="Climate variables to retrieve"
    )
    
    # Spatial-temporal parameters
    locations = models.ManyToManyField(
        Location,
        help_text="Locations to retrieve data for"
    )
    start_date = models.DateField(help_text="Start date for data retrieval")
    end_date = models.DateField(help_text="End date for data retrieval")
    
    # Aggregation settings
    temporal_aggregation = models.CharField(
        max_length=20,
        choices=[
            ('none', 'None (raw data)'),
            ('daily', 'Daily'),
            ('weekly', 'Weekly'),
            ('monthly', 'Monthly'),
            ('seasonal', 'Seasonal'),
            ('annual', 'Annual'),
        ],
        default='none'
    )
    spatial_buffer_km = models.FloatField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Buffer radius around point locations in kilometres"
    )
    
    # Request status
    status = models.CharField(
        max_length=20,
        choices=REQUEST_STATUS_CHOICES,
        default='pending'
    )
    error_message = models.TextField(blank=True, help_text="Error details if request failed")
    
    # Processing metadata
    total_locations = models.IntegerField(default=0, help_text="Total number of locations to process")
    processed_locations = models.IntegerField(default=0, help_text="Number of locations processed")
    total_observations = models.IntegerField(default=0, help_text="Total observations created")
    
    # Request tracking
    requested_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    # Configuration
    configuration = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional configuration parameters"
    )
    
    class Meta:
        ordering = ['-requested_at']
        verbose_name = "Climate Data Request"
        verbose_name_plural = "Climate Data Requests"
    
    def __str__(self):
        return f"Climate request for {self.study.name} ({self.get_status_display()})"
    
    @property
    def progress_percentage(self):
        """Calculate progress as percentage of locations processed."""
        if self.total_locations == 0:
            return 0
        return round((self.processed_locations / self.total_locations) * 100)
    
    @property
    def duration(self):
        """Calculate processing duration."""
        if self.started_at and self.completed_at:
            return self.completed_at - self.started_at
        elif self.started_at:
            return timezone.now() - self.started_at
        return None

    @property
    def requested_by(self):
        """Get the user who owns the study (and thus this request).
        Authorization flows through the Core module via Study ownership.
        """
        return self.study.created_by if self.study else None

    def user_can_access(self, user):
        """Check if a user can access this climate data request.
        Access is determined by Study ownership in the Core module.
        """
        return self.study.created_by == user if self.study else False


class ClimateDataCache(models.Model):
    """
    Caches retrieved climate data to avoid redundant API calls.
    """
    # Cache key components
    data_source = models.ForeignKey(ClimateDataSource, on_delete=models.CASCADE)
    variable = models.ForeignKey(ClimateVariable, on_delete=models.CASCADE)
    location = models.ForeignKey(Location, on_delete=models.CASCADE)
    date = models.DateField()
    
    # Cached data
    value = models.FloatField()
    quality_flag = models.CharField(
        max_length=20,
        blank=True,
        help_text="Data quality indicator"
    )
    
    # Cache metadata
    cached_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(
        help_text="When this cache entry expires"
    )
    hit_count = models.IntegerField(
        default=0,
        help_text="Number of times this cache entry was used"
    )
    
    class Meta:
        unique_together = ('data_source', 'variable', 'location', 'date')
        indexes = [
            models.Index(fields=['expires_at']),
            models.Index(fields=['location', 'date']),
        ]
        verbose_name = "Climate Data Cache"
        verbose_name_plural = "Climate Data Cache Entries"
    
    def __str__(self):
        return f"Cached {self.variable.name} at {self.location} on {self.date}"
    
    @property
    def is_expired(self):
        """Check if cache entry has expired."""
        return timezone.now() > self.expires_at
    
    def save(self, *args, **kwargs):
        # Set default expiration to 30 days if not specified
        if not self.expires_at:
            self.expires_at = timezone.now() + timezone.timedelta(days=30)
        super().save(*args, **kwargs)
