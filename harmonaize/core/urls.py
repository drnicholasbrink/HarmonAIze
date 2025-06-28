from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    # Dashboard and main views
    path('dashboard/', views.study_dashboard, name='dashboard'),
    
    # Study workflow - simple upload and view
    path('upload/', views.upload_study, name='upload'),
    path('studies/', views.StudyListView.as_view(), name='study_list'),
    path('studies/<int:pk>/', views.StudyDetailView.as_view(), name='study_detail'),
]
