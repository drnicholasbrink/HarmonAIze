from django.contrib import admin
from .models import Patient, Location, TimeDimension, Attribute, Observation, Study, Project


@admin.register(Study)
class StudyAdmin(admin.ModelAdmin):
    list_display = ['name', 'created_by', 'study_type', 'status', 'created_at']
    list_filter = ['status', 'study_type', 'has_ethical_approval', 'has_dates', 'has_locations', 'needs_geolocation', 'needs_climate_linkage', 'created_at']
    search_fields = ['name', 'description', 'principal_investigator']
    readonly_fields = ['created_at', 'updated_at', 'codebook_format']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'description', 'principal_investigator', 'created_by', 'study_type')
        }),
        ('Legal & Ethical', {
            'fields': ('has_ethical_approval', 'ethics_approval_number', 'data_use_permissions')
        }),
        ('Study Characteristics', {
            'fields': ('has_dates', 'has_locations', 'needs_geolocation', 'needs_climate_linkage')
        }),
        ('Files', {
            'fields': ('codebook', 'codebook_format', 'protocol_file', 'additional_files')
        }),
        ('Metadata', {
            'fields': ('sample_size', 'study_period_start', 'study_period_end', 'geographic_scope')
        }),
        ('Variables', {
            'fields': ('variables',)
        }),
        ('Workflow', {
            'fields': ('status',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ['unique_id', 'created_at']
    search_fields = ['unique_id']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ['name', 'latitude', 'longitude', 'created_at'
]
    search_fields = ['name']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(TimeDimension)
class TimeDimensionAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'created_at']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(Attribute)
class AttributeAdmin(admin.ModelAdmin):
    list_display = ['variable_name', 'display_name', 'variable_type', 'category', 'embeddings_status_display', 'created_at']
    list_filter = ['variable_type', 'category', 'source_type']
    search_fields = ['variable_name', 'display_name', 'description']
    readonly_fields = ['created_at', 'updated_at', 'embeddings_status', 'has_name_embedding', 'has_description_embedding']
    actions = ['generate_embeddings']

    fieldsets = (
        ('Basic Information', {
            'fields': ('variable_name', 'display_name', 'description', 'unit', 'ontology_code')
        }),
        ('Classification', {
            'fields': ('variable_type', 'category', 'source_type')
        }),
        ('Embeddings', {
            'fields': ('embeddings_status', 'has_name_embedding', 'has_description_embedding'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def get_queryset(self, request):
        """Override queryset to defer embedding fields when pgvector is not installed."""
        qs = super().get_queryset(request)
        try:
            # Try to access embedding fields - if this fails, defer them
            qs = qs.defer('name_embedding', 'description_embedding')
        except Exception:
            pass
        return qs

    def embeddings_status_display(self, obj):
        """Display embedding status with colored indicators."""
        try:
            status = obj.embeddings_status
            if status == "Complete":
                return f'<span style="color: green;">✓ {status}</span>'
            elif status == "Partial":
                return f'<span style="color: orange;">⚠ {status}</span>'
            else:
                return f'<span style="color: red;">⧗ {status}</span>'
        except Exception:
            # pgvector not installed or embedding columns don't exist
            return '<span style="color: gray;">N/A</span>'

    embeddings_status_display.short_description = 'Embedding Status'
    embeddings_status_display.allow_tags = True

    def generate_embeddings(self, request, queryset):
        """Admin action to generate embeddings for selected attributes."""
        try:
            from core.tasks import generate_attribute_embeddings

            count = 0
            for attribute in queryset:
                generate_attribute_embeddings.delay(attribute.id)
                count += 1

            self.message_user(
                request,
                f"Queued embedding generation for {count} attribute(s). Check Celery logs for progress."
            )
        except Exception as e:
            self.message_user(
                request,
                f"Embedding generation not available: {str(e)}",
                level='error'
            )

    generate_embeddings.short_description = "Generate embeddings for selected attributes"


@admin.register(Observation)
class ObservationAdmin(admin.ModelAdmin):
    list_display = ['get_attribute_name', 'patient', 'location', 'time', 'value', 'created_at']
    list_filter = ['attribute__category', 'attribute__variable_type', 'created_at']
    search_fields = ['attribute__variable_name', 'patient__unique_id', 'location__name']
    readonly_fields = ['created_at', 'updated_at']
    list_select_related = ['patient', 'location', 'time']  # Don't include attribute here

    def get_attribute_name(self, obj):
        """Get attribute name without loading embedding fields."""
        if obj.attribute_id:
            from .models import Attribute
            # Fetch attribute with deferred fields to avoid pgvector issues
            try:
                attr = Attribute.objects.defer('name_embedding', 'description_embedding').get(id=obj.attribute_id)
                return str(attr)
            except Attribute.DoesNotExist:
                return None
        return None

    get_attribute_name.short_description = 'Attribute'
    get_attribute_name.admin_order_field = 'attribute__variable_name'


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ['name', 'created_by', 'study_count', 'created_at', 'updated_at']
    list_filter = ['created_at', 'updated_at', 'created_by']
    search_fields = ['name', 'description']
    readonly_fields = ['created_at', 'updated_at', 'study_count', 'harmonisation_progress']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'description', 'created_by')
        }),
        ('Progress & Statistics', {
            'fields': ('study_count', 'harmonisation_progress'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def study_count(self, obj):
        """Display the number of studies in this project"""
        return obj.study_count
    study_count.short_description = 'Studies'
    
    def harmonisation_progress(self, obj):
        """Display harmonisation progress as a percentage"""
        progress = obj.harmonisation_progress
        return f"{progress:.1f}%" if progress is not None else "N/A"
    harmonisation_progress.short_description = 'Progress'
