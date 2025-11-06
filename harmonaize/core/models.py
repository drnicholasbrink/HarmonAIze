from django.contrib.auth import get_user_model
from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.urls import reverse
from django.conf import settings
from pgvector.django import VectorField
import ast

User = get_user_model()


class Project(models.Model):
    """
    Represents a research project that contains multiple studies.
    A project serves as the top-level organisational unit for harmonisation work.
    """
    # Basic project information
    name = models.CharField(max_length=200, help_text="Name of the project")
    description = models.TextField(
        blank=True, help_text="Detailed description of the project"
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="projects",
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Projects"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("core:project_detail", kwargs={"pk": self.pk})

    @property
    def study_count(self):
        """Return the number of studies in this project."""
        return self.studies.count()

    @property
    def source_study_count(self):
        """Return the number of source studies in this project."""
        return self.studies.filter(study_purpose="source").count()

    @property
    def target_study_count(self):
        """Return the number of target studies in this project."""
        return self.studies.filter(study_purpose="target").count()

    @property
    def total_variable_count(self):
        """Return the total number of variables across all studies in this project."""
        return sum(study.variable_count for study in self.studies.all())

    @property
    def harmonisation_progress(self):
        """Calculate harmonisation progress as percentage of completed studies."""
        total_studies = self.study_count
        if total_studies == 0:
            return 0

        completed_studies = self.studies.filter(
            status__in=["harmonised", "completed"],
        ).count()

        return round((completed_studies / total_studies) * 100)

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
    variable_name = models.CharField(max_length=200)
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
    # Field to distinguish between source and target variables
    source_type = models.CharField(
        max_length=10,
        choices=[
            ('source', 'Source'),
            ('target', 'Target'),
        ],
        default='source',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Vector embeddings for semantic search and similarity matching
    name_embedding = VectorField(
        dimensions=settings.EMBEDDING_DIMENSIONS,
        null=True,
        blank=True,
        help_text="Vector embedding of the variable name for semantic similarity search"
    )
    description_embedding = VectorField(
        dimensions=settings.EMBEDDING_DIMENSIONS,
        null=True,
        blank=True,
        help_text="Vector embedding of the description for semantic similarity search"
    )
    
    # t-SNE 2D projection coordinates for visualization
    name_tsne_x = models.FloatField(
        null=True,
        blank=True,
        help_text="X coordinate from t-SNE projection of name embedding",
    )
    name_tsne_y = models.FloatField(
        null=True,
        blank=True,
        help_text="Y coordinate from t-SNE projection of name embedding",
    )
    description_tsne_x = models.FloatField(
        null=True,
        blank=True,
        help_text="X coordinate from t-SNE projection of description embedding",
    )
    description_tsne_y = models.FloatField(
        null=True,
        blank=True,
        help_text="Y coordinate from t-SNE projection of description embedding",
    )

    def __str__(self):
        return self.display_name or self.variable_name

    def clean(self):
        if self.variable_type in ['float', 'int'] and not self.unit:
            raise ValidationError("Unit required for numeric types.")

    @property
    def has_name_embedding(self):
        """Check if the attribute has a name embedding."""
        try:
            return self.name_embedding is not None
        except AttributeError:
            # pgvector not installed or embedding fields don't exist
            return False

    @property
    def has_description_embedding(self):
        """Check if the attribute has a description embedding."""
        try:
            return self.description_embedding is not None
        except AttributeError:
            # pgvector not installed or embedding fields don't exist
            return False

    @property
    def has_embeddings(self):
        """Check if the attribute has both name and description embeddings."""
        return self.has_name_embedding and self.has_description_embedding

    @property
    def embeddings_status(self):
        """Return a human-readable embedding status."""
        try:
            if self.has_name_embedding and self.has_description_embedding:
                return "Complete"
            elif self.has_name_embedding or self.has_description_embedding:
                return "Partial"
            else:
                return "Pending"
        except AttributeError:
            # pgvector not installed or embedding fields don't exist
            return "N/A"
    
    @property
    def has_name_tsne(self):
        """Check if the attribute has name t-SNE coordinates."""
        return self.name_tsne_x is not None and self.name_tsne_y is not None
    
    @property
    def has_description_tsne(self):
        """Check if the attribute has description t-SNE coordinates."""
        return self.description_tsne_x is not None and self.description_tsne_y is not None
    
    @property
    def has_tsne_projections(self):
        """Check if the attribute has both name and description t-SNE projections."""
        return self.has_name_tsne and self.has_description_tsne
    
    @property
    def tsne_status(self):
        """Return a human-readable t-SNE projection status."""
        if self.has_name_tsne and self.has_description_tsne:
            return "Complete"
        elif self.has_name_tsne or self.has_description_tsne:
            return "Partial"
        else:
            return "Pending"
            
    class Meta:
        # Ensure unique variable names within each source type
        unique_together = ('variable_name', 'source_type')

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
        # Fetch attribute with deferred embedding fields to avoid pgvector issues
        try:
            if self.attribute_id:
                attr = Attribute.objects.defer('name_embedding', 'description_embedding').get(id=self.attribute_id)
                attr_name = attr.display_name
            else:
                attr_name = "Unknown"
        except Exception:
            attr_name = "Unknown"
        return f"{attr_name} for {entity} at {self.time}"

    def clean(self):
        # Fetch attribute with deferred embedding fields to avoid pgvector issues
        try:
            if self.attribute_id:
                attr = Attribute.objects.defer('name_embedding', 'description_embedding').get(id=self.attribute_id)
                expected_type = attr.variable_type
            else:
                return  # No attribute, skip validation
        except Exception:
            return  # Can't fetch attribute, skip validation

        # attribute now holds type info directly
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
        # Fetch attribute with deferred embedding fields to avoid pgvector issues
        try:
            if self.attribute_id:
                attr = Attribute.objects.defer('name_embedding', 'description_embedding').get(id=self.attribute_id)
                expected_type = attr.variable_type
            else:
                return None
        except Exception:
            return None

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

class Study(models.Model):
    """
    Represents a research study and its metadata.
    Can be either a source study (with data to harmonise) or target study (defining harmonisation targets).
    """
    
    # Study type choices (research design)
    RESEARCH_TYPE_CHOICES = [
        ('clinical_trial', 'Clinical Trial'),
        ('cohort', 'Cohort Study'),
        ('registry', 'Registry'),
        ('cross_sectional', 'Cross-sectional Study'),
        ('case_control', 'Case-Control Study'),
        ('other', 'Other'),
    ]
    
    # Study purpose choices (source vs target)
    STUDY_TYPE_CHOICES = [
        ('source', 'Source Study'),
        ('target', 'Target Study'),
    ]
    
    # Data use ontology choices for legal/ethical approval
    DATA_USE_CHOICES = [
        ('GRU', 'General Research Use'),
        ('HMB', 'Health/Medical/Biomedical Research'),
        ('DS', 'Disease-Specific Research'),
        ('POA', 'Population Origins/Ancestry Research'),
        ('RS', 'Research-Specific Restrictions'),
        ('IRB', 'Ethics Approval Required'),
        ('GS', 'Geographical Restrictions'),
        ('MOR', 'Publication Moratorium'),
        ('TS', 'Time Limit on Use'),
        ('US', 'User-Specific Restriction'),
        ('PS', 'Project-Specific Restriction'),
        ('IS', 'Institution-Specific Restriction'),
    ]
    
    # Basic study information
    name = models.CharField(max_length=200, help_text="Name of the study")
    description = models.TextField(blank=True, help_text="Brief description of the study")
    principal_investigator = models.CharField(max_length=200, blank=True, help_text="Principal investigator name")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='studies')
    
    # Project relationship
    project = models.ForeignKey(
        'Project', 
        on_delete=models.CASCADE, 
        related_name='studies',
        help_text="Project that this study belongs to"
    )
    
    # Study purpose (source vs target)
    study_purpose = models.CharField(
        max_length=10,
        choices=STUDY_TYPE_CHOICES,
        default='source',
        help_text="Whether this is a source study (data to harmonise) or target study (harmonisation target)"
    )
    
    # Research design type
    study_type = models.CharField(
        max_length=50,
        choices=RESEARCH_TYPE_CHOICES,
        help_text="Type of research study"
    )
    
    # Legal and ethical approval
    has_ethical_approval = models.BooleanField(
        default=False, 
        help_text="Does this study have ethical/IRB approval?"
    )
    data_use_permissions = models.JSONField(
        default=list, 
        blank=True,
        help_text="Data use permissions based on Data Use Ontology"
    )
    ethics_approval_number = models.CharField(
        max_length=100, 
        blank=True,
        help_text="Ethics committee/IRB approval number"
    )
    
    # Study characteristics
    has_dates = models.BooleanField(default=False, help_text="Does the study include date/time variables?")
    has_locations = models.BooleanField(default=False, help_text="Does the study include location data?")
    needs_geolocation = models.BooleanField(default=False, help_text="Do you need to geolocate addresses/facilities?")
    needs_climate_linkage = models.BooleanField(default=False, help_text="Do you want to link climate data?")
    
    # File uploads - consolidated for both source and target studies
    codebook = models.FileField(
        upload_to='studies/codebooks/',
        null=True, blank=True,
        help_text="Upload your codebook (CSV, Excel, SPSS, Stata, JSON, DB, etc.)"
    )
    codebook_format = models.CharField(
        max_length=20,
        blank=True,
        help_text="Auto-detected format of the codebook file"
    )
    protocol_file = models.FileField(
        upload_to='studies/protocols/',
        null=True, blank=True,
        help_text="Upload study protocol or additional documentation"
    )
    additional_files = models.FileField(
        upload_to='studies/additional/',
        null=True, blank=True,
        help_text="Upload additional study documentation"
    )
    
    # Study metadata (optional - can be extracted from protocol)
    sample_size = models.PositiveIntegerField(null=True, blank=True, help_text="Approximate sample size (if known)")
    study_period_start = models.DateField(null=True, blank=True, help_text="Study start date (optional)")
    study_period_end = models.DateField(null=True, blank=True, help_text="Study end date (optional)")
    geographic_scope = models.CharField(
        max_length=200, blank=True,
        help_text="Geographic scope (optional - e.g., 'Global', 'USA', 'Sub-Saharan Africa')"
    )
    
    # Variables/attributes relationship
    variables = models.ManyToManyField(
        Attribute,
        blank=True,
        related_name='studies',
        help_text="Variables/attributes included in this study"
    )
    
    # Workflow status
    STATUS_CHOICES = [
        ('created', 'Created'),
        ('codebook_uploaded', 'Source Codebook Uploaded'),
        ('variables_extracted', 'Variables Extracted'),
        ('variables_mapped', 'Variables Mapped'),
        ('harmonised', 'Harmonised'),
        ('completed', 'Completed'),
    ]
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='created')
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = "Studies"
    
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        # Auto-detect file format from codebook
        if self.codebook and not self.codebook_format:
            file_extension = self.codebook.name.split('.')[-1].lower()
            format_mapping = {
                'csv': 'csv',
                'xlsx': 'xlsx',
                'xls': 'excel',
                'sav': 'spss',
                'dta': 'stata',
                'json': 'json',
                'db': 'sqlite',
                'sqlite': 'sqlite',
                'sqlite3': 'sqlite',
                'xml': 'xml',
                'txt': 'text',
            }
            self.codebook_format = format_mapping.get(file_extension, 'unknown')
        
        super().save(*args, **kwargs)
    
    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('core:study_detail', kwargs={'pk': self.pk})
    
    @property
    def variable_count(self):
        """Return the number of variables associated with this study."""
        return self.attributes.count()
    
    def get_data_use_permissions_display(self):
        """Return human-readable data use permissions."""
        if not self.data_use_permissions:
            return "Not specified"
        
        permission_map = dict(self.DATA_USE_CHOICES)
        return [permission_map.get(perm, perm) for perm in self.data_use_permissions]
