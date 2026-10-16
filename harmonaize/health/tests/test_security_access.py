"""Access-control and export-hardening tests for the health app.

Covers GHSA-m6wv-mrr8-8498 (project membership on schema/study/attribute
views), GHSA-fgxj-f48g-q364 (state-changing views require POST) and
GHSA-2jvh-jw2p-h998 (CSV formula injection in exports).
"""
import csv
import io
import os
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.urls import URLPattern, reverse

from core.models import Attribute, Observation, Patient, Project, ProjectMembership, Study
from health import urls as health_urls
from health.models import MappingRule, MappingSchema
from health.similarity_cache import cache_similarity_payload
from health.views import _csv_export_cell, _harmonised_csv_stream

User = get_user_model()

TEST_PASSWORD = os.environ.get("TEST_USER_PASSWORD", "pw12345")

GUARDED_KWARGS = {"schema_id", "study_id", "attribute_id"}
DENIED_STATUSES = {403, 404}
METHOD_NOT_ALLOWED = 405


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="owner@example.com", password=TEST_PASSWORD)


@pytest.fixture
def outsider(db):
    return User.objects.create_user(email="outsider@example.com", password=TEST_PASSWORD)


@pytest.fixture
def project(owner):
    project = Project.objects.create(name="Secured", created_by=owner)
    ProjectMembership.objects.create(project=project, user=owner, role="owner")
    return project


@pytest.fixture
def source_study(project, owner):
    return Study.objects.create(
        name="Source",
        project=project,
        created_by=owner,
        study_purpose="source",
        study_type="cohort",
    )


@pytest.fixture
def target_study(project, owner):
    return Study.objects.create(
        name="Target",
        project=project,
        created_by=owner,
        study_purpose="target",
        study_type="cohort",
    )


@pytest.fixture
def attributes(source_study, target_study):
    src_age = Attribute.objects.create(
        variable_name="age", variable_type="int", category="health", source_type="source",
    )
    src_pid = Attribute.objects.create(
        variable_name="pid", variable_type="string", category="health", source_type="source",
    )
    tgt_age = Attribute.objects.create(
        variable_name="AGE_YEARS", variable_type="int", category="health", source_type="target",
    )
    source_study.variables.set([src_age, src_pid])
    target_study.variables.set([tgt_age])
    return {"src_age": src_age, "src_pid": src_pid, "tgt_age": tgt_age}


@pytest.fixture
def schema(owner, source_study, target_study, attributes):
    return MappingSchema.objects.create(
        source_study=source_study,
        target_study=target_study,
        created_by=owner,
    )


def _guarded_patterns():
    patterns = []
    for pattern in health_urls.urlpatterns:
        if not isinstance(pattern, URLPattern):
            continue
        converters = set(pattern.pattern.converters)
        if converters & GUARDED_KWARGS:
            assert converters <= GUARDED_KWARGS, (
                f"{pattern.name} takes {converters}; extend this test to cover it"
            )
            patterns.append(pattern)
    return patterns


def _kwargs_for(pattern, schema, source_study, attributes):
    values = {
        "schema_id": schema.id,
        "study_id": source_study.id,
        "attribute_id": attributes["src_age"].id,
    }
    return {name: values[name] for name in pattern.pattern.converters}


def test_guarded_pattern_discovery_finds_schema_views():
    names = {pattern.name for pattern in _guarded_patterns()}
    for expected in (
        "harmonization_dashboard",
        "mapping_variable_summaries",
        "mapping_variable_card",
        "save_mapping_variable",
        "mapping_variable_eda",
        "mapping_variable_suggestions",
        "approve_similarity_threshold",
        "study_harmonization_dashboard",
        "similarity_suggestions_api",
        "approve_mapping",
        "finalize_harmonisation",
    ):
        assert expected in names


@pytest.mark.django_db
@pytest.mark.parametrize("pattern", _guarded_patterns(), ids=lambda p: p.name)
def test_non_member_is_denied_on_every_guarded_url(
    client, outsider, schema, source_study, attributes, pattern,
):
    client.force_login(outsider)
    url = reverse(
        f"health:{pattern.name}",
        kwargs=_kwargs_for(pattern, schema, source_study, attributes),
    )
    statuses = {
        "GET": client.get(url).status_code,
        "POST": client.post(url, {}).status_code,
    }
    for method, status in statuses.items():
        assert status in DENIED_STATUSES | {METHOD_NOT_ALLOWED}, (method, status)
    assert set(statuses.values()) & DENIED_STATUSES, statuses

    # Nothing was written on the owner's schema.
    assert not MappingRule.objects.filter(schema=schema).exists()
    schema.refresh_from_db()
    assert schema.status != "approved"


@pytest.mark.django_db
def test_non_member_cannot_save_mapping_rule(client, outsider, schema, attributes):
    client.force_login(outsider)
    attribute = attributes["src_age"]
    url = reverse(
        "health:save_mapping_variable",
        kwargs={"schema_id": schema.id, "attribute_id": attribute.id},
    )
    prefix = f"variable_{attribute.id}"
    resp = client.post(url, {
        f"{prefix}-target_attribute": attributes["tgt_age"].id,
        f"{prefix}-role": "value",
        f"{prefix}-relation_type": "self",
        f"{prefix}-transform_code": "value",
    })
    assert resp.status_code == 404
    assert not MappingRule.objects.filter(schema=schema).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name",
    [
        "harmonization_dashboard",
        "mapping_variable_summaries",
        "mapping_variable_card",
        "mapping_variable_eda",
        "mapping_variable_suggestions",
        "similarity_suggestions_api",
        "study_variables_api",
        "target_attribute_details_api",
    ],
)
def test_member_still_gets_ok(client, owner, schema, source_study, attributes, name):
    cache_similarity_payload(schema, {})
    client.force_login(owner)
    pattern = next(p for p in health_urls.urlpatterns if getattr(p, "name", None) == name)
    url = reverse(
        f"health:{name}",
        kwargs=_kwargs_for(pattern, schema, source_study, attributes),
    )
    assert client.get(url).status_code == 200


@pytest.mark.django_db
def test_member_study_harmonization_dashboard_redirects(client, owner, schema, source_study):
    client.force_login(owner)
    url = reverse("health:study_harmonization_dashboard", kwargs={"study_id": source_study.id})
    resp = client.get(url)
    assert resp.status_code == 302
    assert resp.url == reverse("health:harmonization_dashboard", kwargs={"schema_id": schema.id})


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["approve_mapping", "finalize_harmonisation"])
def test_state_changing_views_reject_get(client, owner, schema, name):
    client.force_login(owner)
    url = reverse(f"health:{name}", kwargs={"schema_id": schema.id})
    resp = client.get(url)
    assert resp.status_code == METHOD_NOT_ALLOWED
    schema.refresh_from_db()
    assert schema.status != "approved"


@pytest.mark.django_db
def test_approve_and_finalize_work_via_post(client, owner, schema, source_study, attributes):
    MappingRule.objects.create(
        schema=schema,
        source_attribute=attributes["src_age"],
        target_attribute=attributes["tgt_age"],
        role="value",
    )
    client.force_login(owner)
    resp = client.post(reverse("health:approve_mapping", kwargs={"schema_id": schema.id}))
    assert resp.status_code == 302
    schema.refresh_from_db()
    assert schema.status == "approved"

    resp = client.post(reverse("health:finalize_harmonisation", kwargs={"schema_id": schema.id}))
    assert resp.status_code == 302
    source_study.refresh_from_db()
    assert source_study.status == "harmonised"


@pytest.mark.django_db
def test_plain_member_cannot_approve(client, project, schema, attributes):
    member = User.objects.create_user(email="member@example.com", password=TEST_PASSWORD)
    ProjectMembership.objects.create(project=project, user=member, role="member")
    MappingRule.objects.create(
        schema=schema,
        source_attribute=attributes["src_age"],
        target_attribute=attributes["tgt_age"],
        role="value",
    )
    client.force_login(member)
    resp = client.post(reverse("health:approve_mapping", kwargs={"schema_id": schema.id}))
    assert resp.status_code == 403
    schema.refresh_from_db()
    assert schema.status != "approved"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("=HYPERLINK(\"http://evil\")", "'=HYPERLINK(\"http://evil\")"),
        ("+cmd", "'+cmd"),
        ("-2+3", "'-2+3"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("-3.5", "-3.5"),
        ("+1e5", "+1e5"),
        ("plain", "plain"),
        (-7, -7),
        (None, None),
    ],
)
def test_csv_export_cell(value, expected):
    assert _csv_export_cell(value) == expected


@pytest.mark.django_db
def test_harmonised_csv_export_prefixes_formula_cells(schema, target_study):
    text_attr = Attribute.objects.create(
        variable_name="note", variable_type="string", category="health", source_type="target",
    )
    num_attr = Attribute.objects.create(
        variable_name="delta", variable_type="float", category="health", source_type="target",
    )
    target_study.variables.add(text_attr, num_attr)
    patient = Patient.objects.create(unique_id="@evil")
    Observation.objects.create(patient=patient, attribute=text_attr, text_value="=1+1")
    Observation.objects.create(patient=patient, attribute=num_attr, float_value=-2.5)

    queryset = Observation.objects.select_related(
        "patient", "attribute", "time", "location",
    ).order_by("id")
    content = "".join(
        _harmonised_csv_stream(
            queryset,
            schema,
            SimpleNamespace(id=1),
            "2026-01-01T00:00:00",
            user_id=0,
        )
    )
    rows = list(csv.DictReader(io.StringIO(content)))
    assert rows[0]["value"] == "'=1+1"
    assert rows[0]["patient_id"] == "'@evil"
    assert rows[1]["value"] == "-2.5"
