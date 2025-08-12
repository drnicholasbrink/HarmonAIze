"""
API views for climate data functionality.
"""
from rest_framework import generics, status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from django.db.models import Count, Avg, Min, Max
from django.shortcuts import get_object_or_404
from core.models import Study, Attribute, Observation
from ..models import (
    ClimateDataSource,
    ClimateVariable,
    ClimateDataRequest,
)
from .serializers import (
    ClimateDataSourceSerializer,
    ClimateVariableSerializer,
    ClimateDataRequestSerializer,
    ClimateDataRequestCreateSerializer,
    ClimateProcessingStatusSerializer,
    ClimateDataSummarySerializer,
)


class ClimateDataSourceListView(generics.ListAPIView):
    """List all active climate data sources."""
    queryset = ClimateDataSource.objects.filter(is_active=True)
    serializer_class = ClimateDataSourceSerializer
    permission_classes = [permissions.IsAuthenticated]


class ClimateVariableListView(generics.ListAPIView):
    """List climate variables, optionally filtered by data source."""
    serializer_class = ClimateVariableSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        queryset = ClimateVariable.objects.all()
        
        # Filter by data source if specified
        source_id = self.request.query_params.get('source_id')
        if source_id:
            try:
                data_source = ClimateDataSource.objects.get(pk=source_id, is_active=True)
                queryset = data_source.variables.all()
            except ClimateDataSource.DoesNotExist:
                queryset = ClimateVariable.objects.none()
        
        # Filter by category if specified
        category = self.request.query_params.get('category')
        if category:
            queryset = queryset.filter(category=category)
        
        return queryset.order_by('category', 'display_name')


class ClimateDataRequestListCreateView(generics.ListCreateAPIView):
    """List and create climate data requests."""
    serializer_class = ClimateDataRequestSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        return ClimateDataRequest.objects.filter(
            study__created_by=self.request.user
        ).select_related('study', 'data_source').prefetch_related('variables')
    
    def get_serializer_class(self):
        if self.request.method == 'POST':
            return ClimateDataRequestCreateSerializer
        return ClimateDataRequestSerializer
    
    def perform_create(self, serializer):
        serializer.save(requested_by=self.request.user)


class ClimateDataRequestDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, update, or delete a climate data request."""
    serializer_class = ClimateDataRequestSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        return ClimateDataRequest.objects.filter(
            study__created_by=self.request.user
        ).select_related('study', 'data_source').prefetch_related('variables', 'locations')


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def climate_request_status(request, request_id):
    """Get processing status for a climate data request."""
    climate_request = get_object_or_404(
        ClimateDataRequest,
        pk=request_id,
        study__created_by=request.user
    )
    
    serializer = ClimateProcessingStatusSerializer({
        'status': climate_request.status,
        'progress': climate_request.progress_percentage,
        'processed_locations': climate_request.processed_locations,
        'total_locations': climate_request.total_locations,
        'total_observations': climate_request.total_observations,
        'error_message': climate_request.error_message,
        'started_at': climate_request.started_at,
        'completed_at': climate_request.completed_at,
    })
    
    return Response(serializer.data)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def start_climate_processing(request, request_id):
    """Start processing a climate data request."""
    climate_request = get_object_or_404(
        ClimateDataRequest,
        pk=request_id,
        study__created_by=request.user,
        status='pending'
    )
    
    # In a production environment, this would trigger an async task
    # For MVP, we'll simulate immediate processing start
    from ..services import ClimateDataProcessor
    
    try:
        processor = ClimateDataProcessor(climate_request)
        result = processor.process_request()
        
        # Return updated status
        serializer = ClimateProcessingStatusSerializer({
            'status': climate_request.status,
            'progress': climate_request.progress_percentage,
            'processed_locations': climate_request.processed_locations,
            'total_locations': climate_request.total_locations,
            'total_observations': climate_request.total_observations,
            'error_message': climate_request.error_message,
        })
        
        return Response(serializer.data)
        
    except Exception as e:
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def climate_data_summary(request, request_id):
    """Get summary statistics for climate data from a request."""
    climate_request = get_object_or_404(
        ClimateDataRequest,
        pk=request_id,
        study__created_by=request.user,
        status='completed'
    )
    
    # Get climate observations for this request
    climate_attributes = Attribute.objects.filter(
        category='climate',
        variable_name__in=[f"climate_{v.name}" for v in climate_request.variables.all()]
    )
    
    summaries = []
    for variable in climate_request.variables.all():
        attr = climate_attributes.filter(variable_name=f"climate_{variable.name}").first()
        if attr:
            stats = Observation.objects.filter(
                attribute=attr,
                location__in=climate_request.locations.all()
            ).aggregate(
                count=Count('id'),
                min_value=Min('float_value'),
                max_value=Max('float_value'),
                avg_value=Avg('float_value')
            )
            
            if stats['count'] > 0:
                summaries.append({
                    'variable_name': variable.name,
                    'variable_display_name': variable.display_name,
                    'count': stats['count'],
                    'min_value': stats['min_value'],
                    'max_value': stats['max_value'],
                    'avg_value': stats['avg_value'],
                    'unit': variable.unit,
                })
    
    serializer = ClimateDataSummarySerializer(summaries, many=True)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def variables_for_source(request, source_id):
    """Get available variables for a specific data source."""
    try:
        data_source = ClimateDataSource.objects.get(pk=source_id, is_active=True)
        variables = data_source.variables.all().order_by('category', 'display_name')
        serializer = ClimateVariableSerializer(variables, many=True)
        
        return Response({
            'source': {
                'id': data_source.id,
                'name': data_source.name,
                'type': data_source.get_source_type_display(),
            },
            'variables': serializer.data
        })
        
    except ClimateDataSource.DoesNotExist:
        return Response(
            {'error': 'Data source not found'},
            status=status.HTTP_404_NOT_FOUND
        )


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def study_locations(request, study_id):
    """Get locations available for climate data linkage for a study."""
    study = get_object_or_404(Study, pk=study_id, created_by=request.user)
    
    # Get locations with coordinates from this study
    from core.models import Location
    locations = Location.objects.filter(
        observations__attribute__studies=study,
        latitude__isnull=False,
        longitude__isnull=False
    ).distinct().values('id', 'name', 'latitude', 'longitude')
    
    return Response({
        'study': study.name,
        'location_count': locations.count(),
        'locations': list(locations)
    })