from django.urls import path, include
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
    path(
        "mapping/<int:schema_id>/finalize/",
        views.finalize_harmonisation,
        name="finalize_harmonisation",
    ),
    
    # Data ingestion URLs
    path(
        "upload-raw-data/",
        views.upload_raw_data,
        name="upload_raw_data",
    ),
    path(
        "studies/<int:study_id>/upload-raw-data/",
        views.upload_raw_data,
        name="upload_raw_data_for_study",
    ),
    path(
        "raw-data/",
        views.raw_data_list,
        name="raw_data_list",
    ),
    path(
        "raw-data/<int:file_id>/",
        views.raw_data_detail,
        name="raw_data_detail",
    ),
    path(
        "raw-data/<int:file_id>/reupload/",
        views.reupload_raw_data,
        name="reupload_raw_data",
    ),
    path(
        "raw-data/<int:file_id>/validate/",
        views.validate_raw_data,
        name="validate_raw_data",
    ),
    path(
        "raw-data/<int:file_id>/map-columns/",
        views.map_raw_data_columns,
        name="map_raw_data_columns",
    ),
    path(
        "raw-data/<int:file_id>/start-ingestion/",
        views.start_data_ingestion,
        name="start_data_ingestion",
    ),
    path(
        "raw-data/<int:file_id>/ingestion-status/",
        views.ingestion_status,
        name="ingestion_status",
    ),
    path(
        "raw-data/<int:file_id>/delete/",
        views.delete_raw_data,
        name="delete_raw_data",
    ),
    
    # API endpoints
    path(
        "api/study/<int:study_id>/variables/",
        views.study_variables_api,
        name="study_variables_api",
    ),
]