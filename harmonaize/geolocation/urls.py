# geolocation/urls.py
from django.urls import path
from . import views

app_name = 'geolocation'

urlpatterns = [
    # Main validation interface
    path('validation/', views.validation_map, name='validation_map'),
    
    # Dashboard
    path('dashboard/', views.ValidationDashboardView.as_view(), name='validation_dashboard'),
    path('', views.ValidationDashboardView.as_view(), name='dashboard_root'),  # Default route
    
    # API endpoints
    path('api/validation/', views.validation_api, name='validation_api'),
    path('api/geocoding/', views.geocoding_api, name='geocoding_api'),
    path('api/bulk-actions/', views.bulk_validation_actions, name='bulk_validation_actions'),
    path('api/statistics/', views.validation_statistics, name='validation_statistics'),
    
    # NEW: Location status API for comprehensive dashboard table
    path('api/location-status/', views.location_status_api, name='location_status_api'),
    
    # Validation queue API for real data display
    path('api/validation-queue/', views.validation_queue_api, name='validation_queue_api'),
    # Add this to your urlpatterns:
    path('validated-map/', views.validated_locations_map, name='validated_locations_map'),
]