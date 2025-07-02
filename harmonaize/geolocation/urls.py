# geolocation/urls.py
from django.urls import path
from . import views

app_name = 'geolocation'

urlpatterns = [
    # Main validation interface
    path('validation/', views.validation_map, name='validation_map'),
    
    # Dashboard
    path('dashboard/', views.ValidationDashboardView.as_view(), name='dashboard'),
    path('', views.ValidationDashboardView.as_view(), name='dashboard_root'),  # Default route
    
    # API endpoints
    path('api/validation/', views.validation_api, name='validation_api'),
    path('api/geocoding/', views.geocoding_api, name='geocoding_api'),
    path('api/bulk-actions/', views.bulk_validation_actions, name='bulk_validation_actions'),
    path('api/statistics/', views.validation_statistics, name='validation_statistics'),
    
    # Validation queue API for real data display
    path('api/validation-queue/', views.validation_queue_api, name='validation_queue_api'),
]