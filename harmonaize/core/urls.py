from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    # Upload workflow
    path('upload/', views.upload_study, name='upload'),
    
    # Study management
    path('dashboard/', views.study_dashboard, name='dashboard'),
    path('studies/', views.StudyListView.as_view(), name='study_list'),
    path('studies/<int:pk>/', views.StudyDetailView.as_view(), name='study_detail'),
    
    # Codebook processing workflow
    path('studies/<int:study_id>/process-codebook/', views.process_codebook, name='process_codebook'),
    path('studies/<int:study_id>/confirm-variables/', views.confirm_variables, name='confirm_variables'),
]
