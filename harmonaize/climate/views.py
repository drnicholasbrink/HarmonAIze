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


# HTMX Partial Views for Dynamic UI
# ================================================================================

@login_required
def data_source_preview_partial(request):
    """
    HTMX partial: Return data source preview card.
    """
    source_id = request.GET.get('source_id')

    if not source_id:
        return render(request, 'climate/partials/data_source_preview.html', {
            'source': None,
        })

    try:
        source = ClimateDataSource.objects.get(pk=source_id, is_active=True)

        # Get variable count for this source
        variable_count = ClimateVariable.objects.filter(
            data_sources=source
        ).count()

        context = {
            'source': source,
            'variable_count': variable_count,
        }

        return render(request, 'climate/partials/data_source_preview.html', context)

    except ClimateDataSource.DoesNotExist:
        return render(request, 'climate/partials/data_source_preview.html', {
            'source': None,
        })


@login_required
def variable_list_partial(request):
    """
    HTMX partial: Return filtered variable list with category filtering.
    """
    source_id = request.GET.get('source_id')
    filter_category = request.GET.get('category')
    selected_var_ids = request.GET.getlist('selected')

    # Start with all variables
    variables = ClimateVariable.objects.all()

    # Filter by data source if provided
    if source_id:
        try:
            source = ClimateDataSource.objects.get(pk=source_id)
            variables = variables.filter(data_sources=source)
        except ClimateDataSource.DoesNotExist:
            pass

    # Filter by category if provided
    if filter_category:
        variables = variables.filter(category=filter_category)

    # Get category counts for filter buttons
    if source_id:
        categories = ClimateVariable.objects.filter(
            data_sources__pk=source_id
        ).values('category').annotate(count=Count('id')).order_by('category')
    else:
        categories = ClimateVariable.objects.values('category').annotate(
            count=Count('id')
        ).order_by('category')

    # Convert selected IDs to integers for template comparison
    selected_variables = [int(vid) for vid in selected_var_ids if vid.isdigit()]

    context = {
        'variables': variables.order_by('category', 'display_name'),
        'categories': categories,
        'source_id': source_id,
        'filter_category': filter_category,
        'selected_variables': selected_variables,
    }

    return render(request, 'climate/partials/variable_list.html', context)


@login_required
def request_status_partial(request, request_id):
    """
    HTMX partial: Return climate request status for polling.
    """
    try:
        climate_request = ClimateDataRequest.objects.get(
            pk=request_id,
            study__created_by=request.user
        )

        # Calculate progress percentage
        if climate_request.total_locations > 0:
            progress = int((climate_request.processed_locations / climate_request.total_locations) * 100)
        else:
            progress = 0

        context = {
            'request': climate_request,
            'progress': progress,
        }

        return render(request, 'climate/partials/request_status.html', context)

    except ClimateDataRequest.DoesNotExist:
        return HttpResponse('<div class="alert alert-danger">Request not found</div>')
