"""
API serializers for climate data models.
"""
from rest_framework import serializers
from core.models import Location
from ..models import (
    ClimateDataSource,
    ClimateVariable,
    ClimateVariableMapping,
    ClimateDataRequest,
    ClimateDataCache,
)


class ClimateDataSourceSerializer(serializers.ModelSerializer):
    """Serializer for climate data sources."""
    
    class Meta:
        model = ClimateDataSource
        fields = [
            'id',
            'name',
            'source_type',
            'description',
            'spatial_resolution_m',
            'temporal_resolution_days',
            'data_start_date',
            'data_end_date',
            'global_coverage',
            'is_active',
        ]
        read_only_fields = ['id']


class ClimateVariableSerializer(serializers.ModelSerializer):
    """Serializer for climate variables."""
    
    class Meta:
        model = ClimateVariable
        fields = [
            'id',
            'name',
            'display_name',
            'description',
            'category',
            'unit',
            'unit_symbol',
            'min_value',
            'max_value',
            'supports_temporal_aggregation',
            'supports_spatial_aggregation',
            'default_aggregation_method',
            'health_relevance',
        ]
        read_only_fields = ['id']


class ClimateVariableMappingSerializer(serializers.ModelSerializer):
    """Serializer for variable mappings."""
    variable = ClimateVariableSerializer(read_only=True)
    data_source = ClimateDataSourceSerializer(read_only=True)
    
    class Meta:
        model = ClimateVariableMapping
        fields = [
            'id',
            'variable',
            'data_source',
            'source_variable_name',
            'source_dataset',
            'source_band',
            'scale_factor',
            'offset',
            'extra_parameters',
        ]
        read_only_fields = ['id']


class LocationSerializer(serializers.ModelSerializer):
    """Simple location serializer for climate requests."""
    
    class Meta:
        model = Location
        fields = ['id', 'name', 'latitude', 'longitude']
        read_only_fields = ['id']


class ClimateDataRequestSerializer(serializers.ModelSerializer):
    """Serializer for climate data requests."""
    variables = ClimateVariableSerializer(many=True, read_only=True)
    data_source = ClimateDataSourceSerializer(read_only=True)
    locations = LocationSerializer(many=True, read_only=True)
    study_name = serializers.CharField(source='study.name', read_only=True)
    requested_by_name = serializers.CharField(
        source='requested_by.get_full_name',
        read_only=True
    )
    
    class Meta:
        model = ClimateDataRequest
        fields = [
            'id',
            'study',
            'study_name',
            'data_source',
            'variables',
            'locations',
            'start_date',
            'end_date',
            'temporal_aggregation',
            'spatial_buffer_km',
            'status',
            'error_message',
            'total_locations',
            'processed_locations',
            'total_observations',
            'progress_percentage',
            'requested_by',
            'requested_by_name',
            'requested_at',
            'started_at',
            'completed_at',
            'duration',
            'configuration',
        ]
        read_only_fields = [
            'id',
            'study_name',
            'progress_percentage',
            'duration',
            'requested_at',
            'started_at',
            'completed_at',
        ]


class ClimateDataRequestCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating climate data requests."""
    variable_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        help_text="List of climate variable IDs to retrieve"
    )
    
    class Meta:
        model = ClimateDataRequest
        fields = [
            'study',
            'data_source',
            'variable_ids',
            'start_date',
            'end_date',
            'temporal_aggregation',
            'spatial_buffer_km',
            'configuration',
        ]
    
    def create(self, validated_data):
        variable_ids = validated_data.pop('variable_ids')
        request = super().create(validated_data)
        
        # Add variables
        variables = ClimateVariable.objects.filter(id__in=variable_ids)
        request.variables.set(variables)
        
        # Add locations from study
        study_locations = Location.objects.filter(
            observations__attribute__studies=request.study
        ).distinct()
        request.locations.set(study_locations)
        request.total_locations = study_locations.count()
        request.save()
        
        return request


class ClimateDataCacheSerializer(serializers.ModelSerializer):
    """Serializer for cached climate data."""
    variable = ClimateVariableSerializer(read_only=True)
    location = LocationSerializer(read_only=True)
    data_source = ClimateDataSourceSerializer(read_only=True)
    
    class Meta:
        model = ClimateDataCache
        fields = [
            'id',
            'data_source',
            'variable',
            'location',
            'date',
            'value',
            'quality_flag',
            'cached_at',
            'expires_at',
            'hit_count',
            'is_expired',
        ]
        read_only_fields = ['id', 'cached_at', 'is_expired']


class ClimateDataSummarySerializer(serializers.Serializer):
    """Serializer for climate data summary statistics."""
    variable_name = serializers.CharField()
    variable_display_name = serializers.CharField()
    count = serializers.IntegerField()
    min_value = serializers.FloatField()
    max_value = serializers.FloatField()
    avg_value = serializers.FloatField()
    unit = serializers.CharField()


class ClimateProcessingStatusSerializer(serializers.Serializer):
    """Serializer for processing status updates."""
    status = serializers.CharField()
    progress = serializers.IntegerField()
    processed_locations = serializers.IntegerField()
    total_locations = serializers.IntegerField()
    total_observations = serializers.IntegerField()
    error_message = serializers.CharField(required=False, allow_blank=True)
    started_at = serializers.DateTimeField(required=False, allow_null=True)
    completed_at = serializers.DateTimeField(required=False, allow_null=True)