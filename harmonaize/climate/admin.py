from django.contrib import admin
from .models import (
    ClimateDataSource,
    ClimateVariable,
    ClimateVariableMapping,
    ClimateDataRequest,
    ClimateDataCache,
)


@admin.register(ClimateDataSource)
class ClimateDataSourceAdmin(admin.ModelAdmin):
    list_display = ['name', 'source_type', 'is_active', 'global_coverage', 'last_checked']
    list_filter = ['source_type', 'is_active', 'global_coverage', 'requires_authentication']
    search_fields = ['name', 'description']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'source_type', 'description', 'is_active')
        }),
        ('API Configuration', {
            'fields': ('api_endpoint', 'api_key', 'requires_authentication')
        }),
        ('Data Characteristics', {
            'fields': (
                'spatial_resolution_m',
                'temporal_resolution_days',
                'data_start_date',
                'data_end_date'
            )
        }),
        ('Coverage', {
            'fields': ('global_coverage', 'coverage_description')
        }),
        ('Metadata', {
            'fields': ('last_checked', 'created_by', 'created_at', 'updated_at')
        }),
    )


@admin.register(ClimateVariable)
class ClimateVariableAdmin(admin.ModelAdmin):
    list_display = ['display_name', 'name', 'category', 'unit_symbol']
    list_filter = ['category', 'supports_temporal_aggregation', 'supports_spatial_aggregation']
    search_fields = ['name', 'display_name', 'description']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'display_name', 'category', 'description')
        }),
        ('Units and Measurement', {
            'fields': ('unit', 'unit_symbol', 'min_value', 'max_value')
        }),
        ('Aggregation Options', {
            'fields': (
                'supports_temporal_aggregation',
                'supports_spatial_aggregation',
                'default_aggregation_method'
            )
        }),
        ('Health Context', {
            'fields': ('health_relevance',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(ClimateVariableMapping)
class ClimateVariableMappingAdmin(admin.ModelAdmin):
    list_display = ['variable', 'data_source', 'source_variable_name', 'source_dataset']
    list_filter = ['data_source', 'variable__category']
    search_fields = ['source_variable_name', 'source_dataset', 'source_band']
    fieldsets = (
        ('Mapping', {
            'fields': ('variable', 'data_source')
        }),
        ('Source Information', {
            'fields': ('source_variable_name', 'source_dataset', 'source_band')
        }),
        ('Processing', {
            'fields': ('scale_factor', 'offset', 'extra_parameters')
        }),
    )


@admin.register(ClimateDataRequest)
class ClimateDataRequestAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'study',
        'data_source',
        'status',
        'progress_percentage',
        'requested_by',
        'requested_at'
    ]
    list_filter = ['status', 'data_source', 'temporal_aggregation', 'requested_at']
    search_fields = ['study__name', 'requested_by__email']
    readonly_fields = [
        'requested_at',
        'started_at',
        'completed_at',
        'progress_percentage',
        'duration'
    ]
    filter_horizontal = ['variables', 'locations']
    fieldsets = (
        ('Request Information', {
            'fields': ('study', 'requested_by', 'status', 'error_message')
        }),
        ('Configuration', {
            'fields': (
                'data_source',
                'variables',
                'locations',
                'start_date',
                'end_date',
                'temporal_aggregation',
                'spatial_buffer_km'
            )
        }),
        ('Processing', {
            'fields': (
                'total_locations',
                'processed_locations',
                'total_observations',
                'progress_percentage'
            )
        }),
        ('Timing', {
            'fields': ('requested_at', 'started_at', 'completed_at', 'duration')
        }),
        ('Advanced', {
            'fields': ('configuration',),
            'classes': ('collapse',)
        }),
    )
    
    def progress_percentage(self, obj):
        return f"{obj.progress_percentage}%"
    progress_percentage.short_description = "Progress"


@admin.register(ClimateDataCache)
class ClimateDataCacheAdmin(admin.ModelAdmin):
    list_display = [
        'variable',
        'location',
        'date',
        'value',
        'cached_at',
        'is_expired',
        'hit_count'
    ]
    list_filter = ['data_source', 'variable__category', 'cached_at']
    search_fields = ['location__name', 'variable__name']
    readonly_fields = ['cached_at', 'is_expired']
    date_hierarchy = 'date'
    
    def is_expired(self, obj):
        return obj.is_expired
    is_expired.boolean = True
    is_expired.short_description = "Expired"
