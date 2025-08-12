"""
URLs for the climate app.
"""
from django.urls import path
from . import views

app_name = 'climate'

urlpatterns = [
    # Climate dashboard
    path('', views.climate_dashboard_view, name='dashboard'),
    
    # Climate configuration
    path('configure/<int:study_id>/', views.climate_configuration_view, name='configure'),
    
    # Climate request management
    path('requests/', views.ClimateRequestListView.as_view(), name='request_list'),
    path('request/<int:pk>/', views.ClimateRequestDetailView.as_view(), name='request_detail'),
    path('request/<int:request_id>/preview/', views.climate_data_preview_view, name='data_preview'),
    path('request/<int:request_id>/integrate/', views.climate_integration_view, name='integration'),
    path('request/<int:request_id>/export/', views.climate_data_export_view, name='data_export'),
    
    # API endpoints
    path('api/variables/', views.climate_variables_api_view, name='variables_api'),
    path('api/request/<int:request_id>/status/', views.climate_status_api_view, name='status_api'),
]