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
    
    # Project Membership
    path('projects/<int:pk>/members/', views.project_members, name='project_members'),
    path('projects/<int:pk>/invite/', views.invite_member, name='invite_member'),
    path('projects/<int:project_id>/members/<int:member_id>/remove/', views.remove_member, name='remove_member'),
    path('invite/accept/<str:key>/', views.accept_invite, name='accept_invite'),
    
    # Study workflow - simple upload and view
    path('upload/', views.upload_study, name='upload'),
    path('studies/', views.StudyListView.as_view(), name='study_list'),
    path('studies/<int:pk>/', views.StudyDetailView.as_view(), name='study_detail'),
    path('studies/<int:pk>/update-field/', views.update_study_field, name='update_study_field'),
    path('studies/<int:pk>/download-codebook/', views.download_codebook, name='download_codebook'),
    path('studies/<int:pk>/delete-file/<str:file_type>/', views.delete_study_file, name='delete_study_file'),
    path('studies/<int:pk>/toggle-climate/', views.toggle_climate_linkage, name='study_toggle_climate'),
    path('studies/<int:study_id>/delete/', views.delete_study, name='delete_study'),
    path('studies/<int:study_id>/generate-codebook/', views.generate_study_codebook, name='generate_study_codebook'),
    path('studies/<int:study_id>/codebook-generation-status/', views.codebook_generation_status, name='codebook_generation_status'),
    
    # Study documents (additional files)
    path('studies/<int:pk>/documents/add/', views.add_study_document, name='add_study_document'),
    path('studies/<int:pk>/documents/<int:doc_id>/delete/', views.delete_study_document, name='delete_study_document'),
    path('studies/<int:pk>/documents/<int:doc_id>/download/', views.download_study_document, name='download_study_document'),
    
    # Target codebook workflow
    path('target/create/', views.create_target_study, name='create_target_study'),
    path('target/<int:study_id>/map/', views.target_map_codebook, name='target_map_codebook'),
    path('target/<int:study_id>/extract/', views.target_extract_variables, name='target_extract_variables'),
    path('target/<int:study_id>/select/', views.target_select_variables, name='target_select_variables'),
    path('target/<int:study_id>/reset/', views.target_reset_variables, name='target_reset_variables'),
    
    # Embedding generation
    path('studies/<int:study_id>/generate-embeddings/', views.generate_study_embeddings, name='generate_study_embeddings'),
    path('studies/<int:study_id>/embedding-progress/', views.embedding_progress, name='embedding_progress'),
    path('attributes/<int:attribute_id>/generate-embedding/', views.generate_attribute_embedding, name='generate_attribute_embedding'),
    path('attributes/<int:attribute_id>/update/', views.update_attribute, name='update_attribute'),
    
    # t-SNE visualization
    path('projects/<int:project_id>/tsne/generate/', views.generate_project_tsne, name='generate_project_tsne'),
    path('projects/<int:project_id>/tsne/progress/', views.tsne_progress, name='tsne_progress'),
    path('projects/<int:project_id>/tsne/visualization/', views.tsne_visualization, name='tsne_visualization'),
    path('projects/<int:project_id>/tsne/data/', views.tsne_data_api, name='tsne_data_api'),
]
