from django.urls import path
from . import views

app_name = "health"

urlpatterns = [
    # Health data harmonisation workflow
    path(
        "studies/<int:study_id>/map-codebook/",
        views.map_codebook,
        name="map_codebook",
    ),
    path(
        "studies/<int:study_id>/extract-variables/",
        views.extract_variables,
        name="extract_variables",
    ),
    path(
        "studies/<int:study_id>/select-variables/",
        views.select_variables,
        name="select_variables",
    ),
    # Variable management
    path(
        "studies/<int:study_id>/reset-variables/",
        views.reset_variables,
        name="reset_variables",
    ),
    # Harmonisation mapping workflow
    path(
        "studies/<int:study_id>/start-harmonisation/",
        views.start_harmonisation,
        name="start_harmonisation",
    ),
    path(
        "mapping/<int:schema_id>/dashboard/",
        views.harmonization_dashboard,
        name="harmonization_dashboard",
    ),
    path(
        "study/<int:study_id>/harmonization-dashboard/",
        views.study_harmonization_dashboard,
        name="study_harmonization_dashboard",
    ),
    path(
        "mapping/<int:schema_id>/approve/",
        views.approve_mapping,
        name="approve_mapping",
    ),
]
