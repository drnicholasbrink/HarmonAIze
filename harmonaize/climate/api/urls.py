"""
API URLs for climate data functionality.
"""
from django.urls import path
from . import views

app_name = 'climate_api'

urlpatterns = [
    # Data sources
    path('sources/', views.ClimateDataSourceListView.as_view(), name='source-list'),
    path('sources/<int:source_id>/variables/', views.variables_for_source, name='source-variables'),
    
    # Variables
    path('variables/', views.ClimateVariableListView.as_view(), name='variable-list'),
    
    # Climate data requests
    path('requests/', views.ClimateDataRequestListCreateView.as_view(), name='request-list'),
    path('requests/<int:pk>/', views.ClimateDataRequestDetailView.as_view(), name='request-detail'),
    path('requests/<int:request_id>/status/', views.climate_request_status, name='request-status'),
    path('requests/<int:request_id>/start/', views.start_climate_processing, name='request-start'),
    path('requests/<int:request_id>/summary/', views.climate_data_summary, name='request-summary'),
    
    # Study integration
    path('studies/<int:study_id>/locations/', views.study_locations, name='study-locations'),
]