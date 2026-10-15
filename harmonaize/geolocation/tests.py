import csv
import json
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from core.models import Attribute
from core.models import Location
from core.models import Observation
from core.models import Patient
from core.models import Project
from core.models import ProjectMembership
from core.models import Study
from geolocation.models import GeocodingResult
from geolocation.tasks import batch_validate_locations

User = get_user_model()


@pytest.fixture
def geolocation_users(db):
    user_a = User.objects.create_user(email="geo-a@example.com", password="pw12345")
    user_b = User.objects.create_user(email="geo-b@example.com", password="pw12345")
    return user_a, user_b


def _create_project_location(user, project_name, study_name, location_name, lat=None, lng=None):
    project = Project.objects.create(name=project_name, created_by=user)
    ProjectMembership.objects.create(project=project, user=user, role="owner")
    study = Study.objects.create(
        name=study_name,
        project=project,
        created_by=user,
        study_purpose="source",
        study_type="cohort",
    )
    attribute = Attribute.objects.create(
        variable_name=f"{study_name.lower()}_location_marker",
        variable_type="string",
        category="geolocation",
        source_type="source",
        study=study,
    )
    patient = Patient.objects.create(unique_id=f"{study_name}-patient")
    location = Location.objects.create(name=location_name, latitude=lat, longitude=lng)
    Observation.objects.create(
        patient=patient,
        location=location,
        attribute=attribute,
        text_value="observed",
    )
    return location


@pytest.fixture
def two_project_locations(geolocation_users):
    user_a, user_b = geolocation_users
    location_a = _create_project_location(
        user_a,
        "Geo Project A",
        "GeoStudyA",
        "Visible Clinic",
        lat=-17.1,
        lng=31.1,
    )
    location_b = _create_project_location(
        user_b,
        "Geo Project B",
        "GeoStudyB",
        "Hidden Clinic",
        lat=-18.2,
        lng=32.2,
    )
    return user_a, user_b, location_a, location_b


@pytest.mark.django_db
def test_location_status_api_only_returns_viewable_locations(client, two_project_locations):
    user_a, _user_b, location_a, location_b = two_project_locations
    client.force_login(user_a)

    response = client.get(reverse("geolocation:location_status_api"))

    assert response.status_code == 200
    payload = response.json()
    location_ids = {item["id"] for item in payload["locations"]}
    assert location_a.id in location_ids
    assert location_b.id not in location_ids


@pytest.mark.django_db
def test_validated_locations_map_only_renders_viewable_locations(client, two_project_locations):
    user_a, _user_b, location_a, location_b = two_project_locations
    client.force_login(user_a)

    response = client.get(reverse("geolocation:validated_locations_map"))

    assert response.status_code == 200
    body = response.content.decode()
    assert location_a.name in body
    assert location_b.name not in body


@pytest.mark.django_db
def test_validated_locations_csv_only_exports_viewable_locations(client, two_project_locations):
    user_a, _user_b, location_a, location_b = two_project_locations
    client.force_login(user_a)

    response = client.get(reverse("geolocation:download_validated_locations_csv"))

    assert response.status_code == 200
    rows = list(csv.DictReader(StringIO(response.content.decode())))
    exported_names = {row["Location Name"] for row in rows}
    assert location_a.name in exported_names
    assert location_b.name not in exported_names


@pytest.mark.django_db
def test_run_geocoding_only_processes_viewable_locations(client, two_project_locations):
    user_a, _user_b, location_a, location_b = two_project_locations
    location_a.latitude = None
    location_a.longitude = None
    location_a.save(update_fields=["latitude", "longitude"])
    location_b.latitude = None
    location_b.longitude = None
    location_b.save(update_fields=["latitude", "longitude"])
    client.force_login(user_a)

    def fake_geocode(_service, location, force_reprocess=False, user=None):
        return GeocodingResult.objects.create(
            location=location,
            location_name=location.name,
            created_by=user,
            hdx_success=True,
            hdx_lat=-17.9,
            hdx_lng=31.9,
        )

    with patch("geolocation.services.GeocodingService.check_validated_dataset", return_value=None):
        with patch("geolocation.services.GeocodingService.geocode_single_location", fake_geocode):
            response = client.post(
                reverse("geolocation:geocoding_api"),
                data=json.dumps({"action": "run_geocoding"}),
                content_type="application/json",
            )

    assert response.status_code == 200
    assert GeocodingResult.objects.filter(location=location_a, created_by=user_a).exists()
    assert not GeocodingResult.objects.filter(location=location_b, created_by=user_a).exists()


@pytest.mark.django_db
def test_batch_geocoding_rejects_explicit_inaccessible_location(client, two_project_locations):
    user_a, _user_b, _location_a, location_b = two_project_locations
    client.force_login(user_a)

    response = client.post(
        reverse("geolocation:start_batch_geocoding"),
        data=json.dumps({"location_ids": [location_b.id]}),
        content_type="application/json",
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_batch_validation_default_is_scoped_to_user(two_project_locations):
    user_a, user_b, _location_a, location_b = two_project_locations
    GeocodingResult.objects.create(
        location=location_b,
        location_name=location_b.name,
        created_by=user_b,
        hdx_success=True,
        hdx_lat=-18.2,
        hdx_lng=32.2,
    )

    result = batch_validate_locations.run(user_id=user_a.id)

    assert result["status"] == "completed"
    assert result["message"] == "No results to validate"
