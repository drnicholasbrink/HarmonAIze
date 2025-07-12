from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    # Dashboard and main views
    path('dashboard/', views.study_dashboard, name='dashboard'),
    
    # Project management
    path('projects/', views.ProjectListView.as_view(), name='project_list'),
    path('projects/create/', views.create_project, name='create_project'),
    path('projects/<int:pk>/', views.ProjectDetailView.as_view(), name='project_detail'),
    
    # Study workflow - simple upload and view
    path('upload/', views.upload_study, name='upload'),
    path('studies/', views.StudyListView.as_view(), name='study_list'),
    path('studies/<int:pk>/', views.StudyDetailView.as_view(), name='study_detail'),
    path('studies/<int:study_id>/delete/', views.delete_study, name='delete_study'),
    
    # Target codebook workflow
    path('target/create/', views.create_target_study, name='create_target_study'),
    path('target/<int:study_id>/map/', views.target_map_codebook, name='target_map_codebook'),
    path('target/<int:study_id>/extract/', views.target_extract_variables, name='target_extract_variables'),
    path('target/<int:study_id>/select/', views.target_select_variables, name='target_select_variables'),
    path('target/<int:study_id>/reset/', views.target_reset_variables, name='target_reset_variables'),
]
