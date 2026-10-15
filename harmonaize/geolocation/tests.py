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

    def fake_geocode(_service, location, force_reprocess=False, user=None, spatial_filter=None):
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


def test_uploaded_locations_visible_only_to_uploader(geolocation_users):
    from geolocation.querysets import viewable_locations_for_user

    user_a, user_b = geolocation_users
    uploaded = Location.objects.create(name="Uploaded Clinic", created_by=user_a)

    assert list(viewable_locations_for_user(user_a)) == [uploaded]
    assert not viewable_locations_for_user(user_b).exists()


def test_manual_coordinate_update_rejects_non_viewable_location(client, geolocation_users):
    user_a, user_b = geolocation_users
    uploaded = Location.objects.create(name="Uploaded Clinic", created_by=user_a)
    url = reverse("geolocation:manual_coordinate_update")
    payload = {"location_id": uploaded.id, "latitude": -26.2, "longitude": 28.0}

    client.force_login(user_b)
    response = client.post(url, data=json.dumps(payload), content_type="application/json")
    assert response.status_code == 404
    uploaded.refresh_from_db()
    assert uploaded.latitude is None

    client.force_login(user_a)
    response = client.post(url, data=json.dumps(payload), content_type="application/json")
    assert response.status_code == 200
    uploaded.refresh_from_db()
    assert uploaded.latitude == pytest.approx(-26.2)


# ---------------------------------------------------------------------------
# Project / study scoping and the geolocation-only workflow
# ---------------------------------------------------------------------------


def _add_study_location(user, project, study_name, location_name):
    """Add a source study with one observed location to an existing project."""
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
    location = Location.objects.create(name=location_name)
    Observation.objects.create(patient=patient, location=location, attribute=attribute, text_value="observed")
    return study, location


@pytest.fixture
def two_projects_one_user(geolocation_users):
    user, _other = geolocation_users
    project_1 = Project.objects.create(name="Scope Project 1", created_by=user)
    ProjectMembership.objects.create(project=project_1, user=user, role="owner")
    project_2 = Project.objects.create(name="Scope Project 2", created_by=user)
    ProjectMembership.objects.create(project=project_2, user=user, role="owner")
    study_1a, loc_1a = _add_study_location(user, project_1, "ScopeStudy1A", "Clinic 1A")
    study_1b, loc_1b = _add_study_location(user, project_1, "ScopeStudy1B", "Clinic 1B")
    study_2, loc_2 = _add_study_location(user, project_2, "ScopeStudy2", "Clinic 2")
    return {
        "user": user,
        "project_1": project_1,
        "project_2": project_2,
        "study_1a": study_1a,
        "loc_1a": loc_1a,
        "loc_1b": loc_1b,
        "loc_2": loc_2,
    }


def _status_ids(client, **params):
    response = client.get(reverse("geolocation:location_status_api"), params)
    assert response.status_code == 200
    return {item["id"] for item in response.json()["locations"]}


@pytest.mark.django_db
def test_location_status_scopes_to_project_study_and_view_all(client, two_projects_one_user):
    data = two_projects_one_user
    client.force_login(data["user"])

    assert _status_ids(client, project=data["project_1"].pk) == {data["loc_1a"].id, data["loc_1b"].id}
    assert _status_ids(client, project=data["project_1"].pk, study=data["study_1a"].pk) == {data["loc_1a"].id}
    assert _status_ids(client, project=data["project_2"].pk) == {data["loc_2"].id}
    assert _status_ids(client, project="all") == {data["loc_1a"].id, data["loc_1b"].id, data["loc_2"].id}


@pytest.mark.django_db
def test_dashboard_scope_is_remembered_for_apis(client, two_projects_one_user):
    data = two_projects_one_user
    client.force_login(data["user"])

    response = client.get(reverse("geolocation:validation_dashboard"), {"project": data["project_2"].pk})
    assert response.status_code == 200
    assert response.context["scope"].project == data["project_2"]

    # No parameters: the API follows the dashboard's last selection
    assert _status_ids(client) == {data["loc_2"].id}


@pytest.mark.django_db
def test_scope_ignores_projects_user_is_not_member_of(client, two_projects_one_user, two_project_locations):
    data = two_projects_one_user
    _user_a, user_b, _location_a, location_b = two_project_locations
    foreign_project = Project.objects.get(name="Geo Project B")
    client.force_login(data["user"])

    ids = _status_ids(client, project=foreign_project.pk)

    assert location_b.id not in ids


@pytest.mark.django_db
def test_batch_geocoding_without_ids_is_limited_to_scope(client, two_projects_one_user):
    data = two_projects_one_user
    client.force_login(data["user"])

    with patch("geolocation.views.batch_geocode_locations.delay") as delay:
        delay.return_value.id = "task-1"
        response = client.post(
            reverse("geolocation:start_batch_geocoding") + f"?project={data['project_2'].pk}",
            data=json.dumps({}),
            content_type="application/json",
        )

    assert response.status_code == 200
    kwargs = delay.call_args.kwargs
    assert kwargs["location_ids"] is None
    assert kwargs["scope_location_ids"] == [data["loc_2"].id]


def _run_location_list_upload(client, settings, tmp_path, csv_text, **fields):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from geolocation.models import LocationCSVUpload

    settings.MEDIA_ROOT = str(tmp_path)
    upload_file = SimpleUploadedFile("clinic_sites.csv", csv_text.encode(), content_type="text/csv")
    response = client.post(reverse("geolocation:upload_location_csv"), {"file": upload_file, **fields})
    assert response.status_code == 302, getattr(response, "context", None) and response.context["form"].errors
    upload = LocationCSVUpload.objects.latest("uploaded_at")

    response = client.post(
        reverse("geolocation:map_location_columns", args=[upload.id]),
        {"location_name_column": "facility", "latitude_column": "", "longitude_column": ""},
    )
    assert response.status_code == 302
    return upload


@pytest.mark.django_db
def test_geolocation_only_workflow_creates_project_and_location_list(client, settings, tmp_path, geolocation_users):
    user, other = geolocation_users
    client.force_login(user)

    upload = _run_location_list_upload(
        client, settings, tmp_path, "facility\nMbare Clinic\nBudiriro Clinic\n",
        project_choice="new", new_project_name="Geolocation only", study_choice="new", location_list_name="",
    )
    response = client.post(reverse("geolocation:ingest_location_csv", args=[upload.id]), {"replace_existing": "true"})

    upload.refresh_from_db()
    project, study = upload.project, upload.study
    assert project.name == "Geolocation only"
    assert ProjectMembership.objects.filter(project=project, user=user, role="owner").exists()
    assert study.project == project
    assert study.study_purpose == "source"
    assert study.name == "clinic_sites"
    assert set(Location.objects.filter(study=study).values_list("name", flat=True)) == {"Mbare Clinic", "Budiriro Clinic"}
    assert response.status_code == 302
    assert response.url.endswith(f"?project={project.pk}&study={study.pk}")

    # The list is scoped like any other study, and invisible to non-members
    assert len(_status_ids(client, project=project.pk, study=study.pk)) == 2
    client.force_login(other)
    assert not _status_ids(client, project="all")


@pytest.mark.django_db
def test_location_list_upload_into_existing_project_sits_alongside_study_locations(
    client, settings, tmp_path, two_projects_one_user,
):
    data = two_projects_one_user
    client.force_login(data["user"])

    upload = _run_location_list_upload(
        client, settings, tmp_path, "facility\nExtra Site\n",
        project_choice=str(data["project_1"].pk), study_choice="new", location_list_name="Extra sites",
    )
    client.post(reverse("geolocation:ingest_location_csv", args=[upload.id]), {"replace_existing": "true"})

    extra = Location.objects.get(name="Extra Site")
    assert extra.study.name == "Extra sites"
    assert _status_ids(client, project=data["project_1"].pk) == {data["loc_1a"].id, data["loc_1b"].id, extra.id}
    assert extra.id not in _status_ids(client, project=data["project_2"].pk)


@pytest.mark.django_db
def test_replace_existing_only_clears_the_target_location_list(client, settings, tmp_path, two_projects_one_user):
    data = two_projects_one_user
    user = data["user"]
    client.force_login(user)
    legacy = Location.objects.create(name="Legacy Upload", created_by=user)
    first = _run_location_list_upload(
        client, settings, tmp_path, "facility\nOld Site\n",
        project_choice=str(data["project_1"].pk), study_choice="new", location_list_name="Sites",
    )
    client.post(reverse("geolocation:ingest_location_csv", args=[first.id]), {"replace_existing": "true"})

    second = _run_location_list_upload(
        client, settings, tmp_path, "facility\nNew Site\n",
        project_choice=str(data["project_1"].pk), study_choice=str(first.study_id),
    )
    client.post(reverse("geolocation:ingest_location_csv", args=[second.id]), {"replace_existing": "true"})

    assert list(Location.objects.filter(study=first.study).values_list("name", flat=True)) == ["New Site"]
    assert Location.objects.filter(pk__in=[legacy.pk, data["loc_1a"].pk, data["loc_2"].pk]).count() == 3


@pytest.mark.django_db
def test_upload_rejects_project_user_is_not_member_of(client, settings, tmp_path, two_project_locations):
    from django.core.files.uploadedfile import SimpleUploadedFile

    user_a, _user_b, _location_a, _location_b = two_project_locations
    foreign_project = Project.objects.get(name="Geo Project B")
    settings.MEDIA_ROOT = str(tmp_path)
    client.force_login(user_a)

    response = client.post(reverse("geolocation:upload_location_csv"), {
        "file": SimpleUploadedFile("x.csv", b"facility\nA\n", content_type="text/csv"),
        "project_choice": str(foreign_project.pk),
        "study_choice": "new",
    })

    assert response.status_code == 200
    assert "project_choice" in response.context["form"].errors
    assert not Study.objects.filter(project=foreign_project, created_by=user_a).exists()
