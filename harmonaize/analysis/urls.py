from django.urls import path

from . import views

app_name = "analysis"

urlpatterns = [
    path("", views.analysis_dashboard, name="dashboard"),
    path("partials/status/", views.dashboard_status_partial, name="status_partial"),
    path("partials/template-manager/", views.template_manager_partial, name="template_manager_partial"),
    path("partials/template-builder/", views.dataset_template_builder_partial, name="template_builder_partial"),
    path("run-now/", views.run_export_now, name="run_now"),
    path("templates/save/", views.save_dataset_template, name="save_template"),
    path("templates/delete/", views.delete_dataset_template, name="delete_template"),
    path("schedule/", views.update_schedule, name="update_schedule"),
]
