from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.views.generic import ListView, DetailView, CreateView
from django.urls import reverse, reverse_lazy
from django.http import JsonResponse, HttpResponse
from django.db.models import Count, Avg, Min, Max, Q
from django.utils import timezone
import json
import csv
from datetime import datetime, timedelta

from core.models import Study, Location, Observation, Attribute
from .models import (
    ClimateDataSource,
    ClimateVariable,
    ClimateDataRequest,
    ClimateDataCache,
)
from .forms import (
    ClimateDataConfigurationForm,
    ClimateVariableSelectionForm,
)
from .services import ClimateDataProcessor, SpatioTemporalMatcher


@login_required
def climate_landing_view(request):
    """
    Climate module landing page with overview and documentation.
    """
    # Get some basic stats for the landing page
    stats = {
        'active_sources': ClimateDataSource.objects.filter(is_active=True).count(),
        'total_variables': ClimateVariable.objects.count(),
        'user_requests': ClimateDataRequest.objects.filter(
            study__created_by=request.user
        ).count(),
    }
    
    # Get available data sources
    data_sources = ClimateDataSource.objects.filter(is_active=True)[:6]
    
    # Get variable categories
    variable_categories = ClimateVariable.objects.values('category').annotate(
        count=Count('id')
    ).order_by('category')
    
    context = {
        'stats': stats,
        'data_sources': data_sources,
        'variable_categories': variable_categories,
    }
    
    return render(request, 'climate/landing.html', context)


@login_required
def climate_dashboard_view(request):
    """
    Main climate module dashboard.
    """
    # Get user's studies that need climate linkage
    studies_needing_climate = Study.objects.filter(
        created_by=request.user,
        needs_climate_linkage=True
    )
    
    # Get recent climate requests
    recent_requests = ClimateDataRequest.objects.filter(
        study__created_by=request.user
    ).select_related('study', 'data_source').order_by('-requested_at')[:5]
    
    # Get climate statistics
    stats = {
        'active_sources': ClimateDataSource.objects.filter(is_active=True).count(),
        'total_variables': ClimateVariable.objects.count(),
        'studies_with_climate': studies_needing_climate.count(),
        'pending_requests': ClimateDataRequest.objects.filter(
            study__created_by=request.user,
            status='pending'
        ).count(),
        'completed_requests': ClimateDataRequest.objects.filter(
            study__created_by=request.user,
            status='completed'
        ).count(),
    }
    
    context = {
        'studies_needing_climate': studies_needing_climate[:5],
        'recent_requests': recent_requests,
        'stats': stats,
        'available_sources': ClimateDataSource.objects.filter(is_active=True)[:3],
    }
    
    return render(request, 'climate/dashboard.html', context)


@login_required
def climate_configuration_view(request, study_id):
    """
    Configure climate data retrieval for a study.
    """
    study = get_object_or_404(Study, pk=study_id, created_by=request.user)
    
    # Check if study has climate linkage enabled
    if not study.needs_climate_linkage:
        messages.warning(
            request,
            "This study does not have climate linkage enabled. "
            "Please enable it in the study settings first."
        )
        return redirect('core:study_detail', pk=study.pk)
    
    # Check for existing locations
    study_locations = Location.objects.filter(
        observations__attribute__studies=study
    ).distinct()
    
    if not study_locations.exists():
        messages.error(
            request,
            "No locations found for this study. "
            "Please upload study data with location information first."
        )
        return redirect('core:study_detail', pk=study.pk)
    
    if request.method == 'POST':
        form = ClimateDataConfigurationForm(
            request.POST,
            study=study,
            user=request.user
        )
        if form.is_valid():
            climate_request = form.save()
            messages.success(
                request,
                f"Climate data request created successfully. "
                f"Processing {climate_request.total_locations} locations..."
            )
            # In production, trigger async task here
            # For MVP, we'll process synchronously (or redirect to processing view)
            return redirect('climate:request_detail', pk=climate_request.pk)
    else:
        form = ClimateDataConfigurationForm(study=study, user=request.user)
    
    context = {
        'study': study,
        'form': form,
        'location_count': study_locations.count(),
        'available_sources': ClimateDataSource.objects.filter(is_active=True),
        'variable_categories': ClimateVariable.objects.values('category').annotate(
            count=Count('id')
        ).order_by('category'),
    }
    
    return render(request, 'climate/configure.html', context)


@login_required
def climate_data_preview_view(request, request_id):
    """
    Preview retrieved climate data before finalising integration.
    """
    climate_request = get_object_or_404(
        ClimateDataRequest,
        pk=request_id,
        study__created_by=request.user
    )
    
    # Get sample observations
    climate_attributes = Attribute.objects.filter(
        category='climate',
        variable_name__in=[f"climate_{v.name}" for v in climate_request.variables.all()]
    )
    
    sample_observations = Observation.objects.filter(
        attribute__in=climate_attributes,
        location__in=climate_request.locations.all(),
        time__timestamp__gte=timezone.make_aware(
            datetime.combine(climate_request.start_date, datetime.min.time())
        ),
        time__timestamp__lte=timezone.make_aware(
            datetime.combine(climate_request.end_date, datetime.min.time())
        )
    ).select_related('location', 'attribute', 'time')[:100]
    
    # Aggregate statistics
    stats = {}
    for variable in climate_request.variables.all():
        attr = climate_attributes.filter(variable_name=f"climate_{variable.name}").first()
        if attr:
            var_stats = Observation.objects.filter(
                attribute=attr,
                location__in=climate_request.locations.all()
            ).aggregate(
                count=Count('id'),
                avg_value=Avg('float_value'),
                min_value=Min('float_value'),
                max_value=Max('float_value')
            )
            stats[variable.display_name] = var_stats
    
    context = {
        'climate_request': climate_request,
        'sample_observations': sample_observations,
        'statistics': stats,
        'can_process': climate_request.status == 'pending',
    }
    
    return render(request, 'climate/preview.html', context)


@login_required
def climate_integration_view(request, request_id):
    """
    Execute climate data integration with study.
    """
    climate_request = get_object_or_404(
        ClimateDataRequest,
        pk=request_id,
        study__created_by=request.user
    )
    
    if climate_request.status != 'pending':
        messages.warning(
            request,
            f"This request has already been {climate_request.get_status_display().lower()}."
        )
        return redirect('climate:request_detail', pk=climate_request.pk)
    
    if request.method == 'POST':
        # Process the climate data request
        processor = ClimateDataProcessor(climate_request)
        result = processor.process_request()
        
        if result['status'] == 'success':
            messages.success(
                request,
                f"Successfully integrated {result['total_observations']} climate observations "
                f"for study '{climate_request.study.name}'."
            )
        else:
            messages.error(
                request,
                f"Error processing climate data: {result.get('error', 'Unknown error')}"
            )
        
        return redirect('climate:request_detail', pk=climate_request.pk)
    
    context = {
        'climate_request': climate_request,
        'location_count': climate_request.locations.count(),
        'variable_count': climate_request.variables.count(),
        'estimated_observations': (
            climate_request.locations.count() *
            climate_request.variables.count() *
            (climate_request.end_date - climate_request.start_date).days
        ),
    }
    
    return render(request, 'climate/integration_confirm.html', context)


class ClimateRequestListView(LoginRequiredMixin, ListView):
    """List view for climate data requests."""
    model = ClimateDataRequest
    template_name = 'climate/request_list.html'
    context_object_name = 'requests'
    paginate_by = 10
    
    def get_queryset(self):
        return ClimateDataRequest.objects.filter(
            study__created_by=self.request.user
        ).select_related('study', 'data_source').prefetch_related('variables')


class ClimateRequestDetailView(LoginRequiredMixin, DetailView):
    """Detail view for a climate data request."""
    model = ClimateDataRequest
    template_name = 'climate/request_detail.html'
    context_object_name = 'climate_request'
    
    def get_queryset(self):
        return ClimateDataRequest.objects.filter(
            study__created_by=self.request.user
        ).select_related('study', 'data_source').prefetch_related('variables', 'locations')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get observations created by this request
        climate_attributes = Attribute.objects.filter(
            category='climate',
            variable_name__in=[f"climate_{v.name}" for v in self.object.variables.all()]
        )
        
        context['observation_count'] = Observation.objects.filter(
            attribute__in=climate_attributes,
            location__in=self.object.locations.all(),
            time__timestamp__gte=timezone.make_aware(
                datetime.combine(self.object.start_date, datetime.min.time())
            ),
            time__timestamp__lte=timezone.make_aware(
                datetime.combine(self.object.end_date, datetime.min.time())
            )
        ).count()
        
        return context


@login_required
def climate_data_export_view(request, request_id):
    """
    Export climate data as CSV.
    """
    climate_request = get_object_or_404(
        ClimateDataRequest,
        pk=request_id,
        study__created_by=request.user,
        status='completed'
    )
    
    # Get climate observations
    climate_attributes = Attribute.objects.filter(
        category='climate',
        variable_name__in=[f"climate_{v.name}" for v in climate_request.variables.all()]
    )
    
    observations = Observation.objects.filter(
        attribute__in=climate_attributes,
        location__in=climate_request.locations.all(),
        time__timestamp__gte=timezone.make_aware(
            datetime.combine(climate_request.start_date, datetime.min.time())
        ),
        time__timestamp__lte=timezone.make_aware(
            datetime.combine(climate_request.end_date, datetime.min.time())
        )
    ).select_related('location', 'attribute', 'time').order_by('time__timestamp', 'location')
    
    # Create CSV response
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="climate_data_{climate_request.id}.csv"'
    
    writer = csv.writer(response)
    writer.writerow([
        'Date',
        'Location',
        'Latitude',
        'Longitude',
        'Variable',
        'Value',
        'Unit'
    ])
    
    for obs in observations:
        writer.writerow([
            obs.time.timestamp.date() if obs.time else '',
            obs.location.name if obs.location else '',
            obs.location.latitude if obs.location else '',
            obs.location.longitude if obs.location else '',
            obs.attribute.display_name,
            obs.float_value,
            obs.attribute.unit
        ])
    
    return response


@login_required
def climate_variables_api_view(request):
    """
    API endpoint to get available climate variables for a data source.
    """
    source_id = request.GET.get('source_id')
    
    if not source_id:
        return JsonResponse({'error': 'source_id parameter required'}, status=400)
    
    try:
        data_source = ClimateDataSource.objects.get(pk=source_id, is_active=True)
        variables = data_source.variables.all().values(
            'id',
            'name',
            'display_name',
            'category',
            'unit_symbol',
            'description'
        )
        
        return JsonResponse({
            'source': data_source.name,
            'variables': list(variables)
        })
    except ClimateDataSource.DoesNotExist:
        return JsonResponse({'error': 'Data source not found'}, status=404)


@login_required
def climate_demo_view(request):
    """
    Interactive demo page for climate data integration.
    """
    # Sample data for demonstration - using African clinical study locations
    sample_data = {
        'study_locations': [
            {'name': 'Maputo Central Hospital, Mozambique', 'lat': -25.9653, 'lon': 32.5892, 'clinic_type': 'Central Hospital'},
            {'name': 'Chris Hani Baragwanath Hospital, South Africa', 'lat': -26.2708, 'lon': 27.9147, 'clinic_type': 'Academic Hospital'},
            {'name': 'Kenyatta National Hospital, Kenya', 'lat': -1.3018, 'lon': 36.8081, 'clinic_type': 'National Referral Hospital'},
            {'name': 'Manhiça Health Research Centre, Mozambique', 'lat': -25.4069, 'lon': 32.8073, 'clinic_type': 'Research Centre'},
            {'name': 'Kilifi County Hospital, Kenya', 'lat': -3.5053, 'lon': 39.8502, 'clinic_type': 'County Hospital'},
        ],
        'climate_variables': [
            {'name': 'temperature_mean', 'display_name': 'Mean Temperature', 'unit': '°C'},
            {'name': 'precipitation_total', 'display_name': 'Total Precipitation', 'unit': 'mm'},
            {'name': 'humidity_relative', 'display_name': 'Relative Humidity', 'unit': '%'},
            {'name': 'temperature_max', 'display_name': 'Maximum Temperature', 'unit': '°C'},
            {'name': 'wind_speed', 'display_name': 'Wind Speed', 'unit': 'm/s'},
        ],
        'sample_results': [
            {'location': 'Maputo Central Hospital, Mozambique', 'date': '2023-01-15', 'temperature': 26.8, 'precipitation': 84.2, 'humidity': 76, 'temp_max': 31.2, 'wind_speed': 3.1},
            {'location': 'Chris Hani Baragwanath Hospital, South Africa', 'date': '2023-01-15', 'temperature': 22.4, 'precipitation': 45.7, 'humidity': 68, 'temp_max': 28.9, 'wind_speed': 2.8},
            {'location': 'Kenyatta National Hospital, Kenya', 'date': '2023-01-15', 'temperature': 18.6, 'precipitation': 12.3, 'humidity': 61, 'temp_max': 24.7, 'wind_speed': 1.9},
            {'location': 'Manhiça Health Research Centre, Mozambique', 'date': '2023-01-15', 'temperature': 27.1, 'precipitation': 88.5, 'humidity': 78, 'temp_max': 32.0, 'wind_speed': 2.9},
            {'location': 'Kilifi County Hospital, Kenya', 'date': '2023-01-15', 'temperature': 25.9, 'precipitation': 18.6, 'humidity': 73, 'temp_max': 29.4, 'wind_speed': 4.2},
        ]
    }
    
    context = {
        'sample_data': sample_data,
        'available_sources': ClimateDataSource.objects.filter(is_active=True)[:3],
        'variable_categories': ClimateVariable.objects.values('category').annotate(
            count=Count('id')
        ).order_by('category'),
    }
    
    return render(request, 'climate/demo.html', context)


@login_required
def climate_api_docs_view(request):
    """
    API documentation page for climate endpoints.
    """
    # Sample API endpoints and their documentation
    api_endpoints = [
        {
            'method': 'GET',
            'endpoint': '/climate/api/variables/',
            'description': 'List available climate variables for a data source',
            'parameters': [
                {'name': 'source_id', 'type': 'integer', 'required': True, 'description': 'ID of the climate data source'}
            ],
            'response_example': {
                'source': 'ERA5 Reanalysis',
                'variables': [
                    {'id': 1, 'name': 'temperature_2m', 'display_name': 'Temperature at 2m', 'category': 'temperature', 'unit_symbol': '°C'}
                ]
            }
        },
        {
            'method': 'GET',
            'endpoint': '/climate/api/request/{request_id}/status/',
            'description': 'Check the processing status of a climate data request',
            'parameters': [
                {'name': 'request_id', 'type': 'integer', 'required': True, 'description': 'ID of the climate data request'}
            ],
            'response_example': {
                'status': 'processing',
                'progress': 75,
                'processed_locations': 15,
                'total_locations': 20,
                'total_observations': 1250
            }
        }
    ]
    
    context = {
        'api_endpoints': api_endpoints,
        'base_url': request.build_absolute_uri('/').rstrip('/'),
    }
    
    return render(request, 'climate/api_docs.html', context)


@login_required  
def climate_improvements_view(request):
    """
    Summary page documenting climate module improvements.
    """
    improvements = {
        'ui_enhancements': [
            'Modernized climate dashboard with Cupertino design system',
            'Added visual workflow indicators showing 4-step climate integration process',
            'Implemented real-time progress tracking with animated progress bars',
            'Enhanced status monitoring with live updates and pulse indicators',
            'Improved responsive design for mobile and tablet devices'
        ],
        'new_features': [
            'Created comprehensive climate module landing page',
            'Built interactive demo showcasing climate data integration workflow',
            'Added API documentation with interactive testing interface',
            'Implemented code examples in Python, JavaScript, R, and cURL',
            'Created step-by-step guided demo with sample data'
        ],
        'documentation': [
            'Added comprehensive API reference with live examples',
            'Created user guides for climate data integration',
            'Built FAQ section for common climate module questions',
            'Added tooltips and contextual help throughout interface',
            'Implemented breadcrumb navigation for better orientation'
        ],
        'integration': [
            'Added climate module link to main dashboard quick actions',
            'Improved navigation between climate module pages',
            'Enhanced status tracking with real-time updates',
            'Better error handling and user feedback',
            'Consistent design integration with main application'
        ]
    }
    
    context = {
        'improvements': improvements,
    }
    
    return render(request, 'climate/improvements.html', context)


@login_required
def climate_status_api_view(request, request_id):
    """
    API endpoint to check climate request processing status.
    """
    try:
        climate_request = ClimateDataRequest.objects.get(
            pk=request_id,
            study__created_by=request.user
        )
        
        return JsonResponse({
            'status': climate_request.status,
            'progress': climate_request.progress_percentage,
            'processed_locations': climate_request.processed_locations,
            'total_locations': climate_request.total_locations,
            'total_observations': climate_request.total_observations,
            'error_message': climate_request.error_message,
        })
    except ClimateDataRequest.DoesNotExist:
        return JsonResponse({'error': 'Request not found'}, status=404)
