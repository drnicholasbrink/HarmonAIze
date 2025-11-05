from django.urls import path
from . import views

app_name = 'health'

urlpatterns = [
    # Health data harmonisation workflow
    path('studies/<int:study_id>/map-codebook/', views.map_codebook, name='map_codebook'),
    path('studies/<int:study_id>/extract-variables/', views.extract_variables, name='extract_variables'),
    path('studies/<int:study_id>/select-variables/', views.select_variables, name='select_variables'),
    # Variable management
    path('studies/<int:study_id>/reset-variables/', views.reset_variables, name='reset_variables'),
]
