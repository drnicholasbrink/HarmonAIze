from django.urls import path
from django.views.generic import TemplateView

app_name = "features"
urlpatterns = [
    path("data_mapping/", TemplateView.as_view(template_name="features/data_mapping.html"), name="data_mapping"),
    path("data_cleaning/", TemplateView.as_view(template_name="features/data_cleaning.html"), name="data_cleaning"),
    path("data_integration/", TemplateView.as_view(template_name="features/data_integration.html"), name="data_integration"),
]
