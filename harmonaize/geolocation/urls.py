
from django.urls import path
from . import views

app_name = 'geolocation'

urlpatterns = [

    path('validation/', views.validation_map, name='validation_map'),
    

    path('dashboard/', views.ValidationDashboardView.as_view(), name='validation_dashboard'),
    path('', views.ValidationDashboardView.as_view(), name='dashboard_root'),  # Default route
    

    path('validated-map/', views.validated_locations_map, name='validated_locations_map'),
    

    path('api/validation/', views.validation_api, name='validation_api'),
    path('api/geocoding/', views.geocoding_api, name='geocoding_api'),
    path('api/bulk-actions/', views.bulk_validation_actions, name='bulk_validation_actions'),
    path('api/statistics/', views.validation_statistics, name='validation_statistics'),
    path('api/location-status/', views.location_status_api, name='location_status_api'),
    path('api/validation-queue/', views.validation_queue_api, name='validation_queue_api'),
    

    path('batch/geocoding/start/', views.start_batch_geocoding, name='start_batch_geocoding'),
    path('batch/validation/start/', views.start_batch_validation, name='start_batch_validation'),
    path('batch-progress/<str:task_id>/', views.batch_progress, name='batch_progress'),
]