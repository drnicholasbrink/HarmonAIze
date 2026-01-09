"""
URLs for the climate app.
"""
from django.urls import path
from . import views

app_name = 'climate'

urlpatterns = [
    # Climate dashboard (main entry point)
    path('', views.climate_dashboard_view, name='dashboard'),

    # Climate configuration
    path('configure/<int:study_id>/', views.climate_configuration_view, name='configure'),

    # Climate request management
    path('requests/', views.ClimateRequestListView.as_view(), name='request_list'),
    path('request/<int:pk>/', views.ClimateRequestDetailView.as_view(), name='request_detail'),
    path('request/<int:request_id>/export/', views.climate_data_export_view, name='data_export'),

    # Core Integration Demo
    path('core-integration/', views.core_integration_view, name='core_integration'),

    # HTMX Partials
    path('partials/data-source-preview/', views.data_source_preview_partial, name='data_source_preview_partial'),
    path('partials/variable-list/', views.variable_list_partial, name='variable_list_partial'),
    path('partials/request-status/<int:request_id>/', views.request_status_partial, name='request_status_partial'),

    # API endpoints
    path('api/request/<int:request_id>/process/', views.process_climate_request_api, name='process_request_api'),
    path('api/request/<int:request_id>/status/', views.climate_request_status_api, name='request_status_api'),
    path('api/data-sources/', views.data_sources_api, name='data_sources_api'),
    path('api/variables/', views.climate_variables_api, name='variables_api'),
]