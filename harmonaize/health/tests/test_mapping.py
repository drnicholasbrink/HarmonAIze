import os
import json
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from core.models import Attribute, Observation, Patient, Project, ProjectMembership, Study
from health.dashboard_fragment_cache import schema_dashboard_cache_version
from health.forms import MappingRuleForm
from health.models import HarmonizationAIRun, MappingRule, MappingSchema, RawDataFile, validate_safe_transform_code
from health.relationship_system import (
    RELATIONSHIP_SYSTEM_CATEGORY,
    RELATIONSHIP_DIRECTION,
    RELATIONSHIP_RELATED_PATIENT_ID,
    RELATIONSHIP_TYPE,
    relation_instance_choices,
)
from health.similarity_cache import cache_similarity_payload

User = get_user_model()

TEST_PASSWORD = os.environ.get("TEST_USER_PASSWORD", "pw12345")


@pytest.fixture
def user(db):
    # Custom user model uses email as USERNAME_FIELD
    return User.objects.create_user(
        email="tester@example.com",
        password=TEST_PASSWORD,
    )

@pytest.fixture
def project(user):
    project = Project.objects.create(name="Proj", created_by=user)
    ProjectMembership.objects.create(project=project, user=user, role="owner")
    return project

@pytest.fixture
def source_study(project, user):
    return Study.objects.create(
        name="SourceStudy",
        project=project,
        created_by=user,
        study_purpose="source",
        study_type="cohort",
    )

@pytest.fixture
def target_study(project, user):
    return Study.objects.create(
        name="TargetStudy",
        project=project,
        created_by=user,
        study_purpose="target",
        study_type="cohort",
    )

@pytest.fixture
def attributes(source_study, target_study):
    # Minimal attributes; attach via study.variables ManyToMany
    src_attr1 = Attribute.objects.create(
        variable_name="age",
        variable_type="int",
        category="health",
        source_type="source",
    )
    src_attr2 = Attribute.objects.create(
        variable_name="pid",
        variable_type="string",
        category="health",
        source_type="source",
    )
    tgt_attr1 = Attribute.objects.create(
        variable_name="AGE_YEARS",
        variable_type="int",
        category="health",
        source_type="target",
    )
    tgt_attr2 = Attribute.objects.create(
        variable_name="PATIENT_ID",
        variable_type="string",
        category="health",
        source_type="target",
    )
    source_study.variables.set([src_attr1, src_attr2])
    target_study.variables.set([tgt_attr1, tgt_attr2])
    return {
        "src_age": src_attr1,
        "src_pid": src_attr2,
        "tgt_age": tgt_attr1,
        "tgt_pid": tgt_attr2,
    }

@pytest.fixture
def schema(user, source_study, target_study):
    return MappingSchema.objects.create(
        source_study=source_study,
        target_study=target_study,
        created_by=user,
        comments="test",
    )

# ------------------- Model validation -------------------

def test_mapping_schema_purpose_validation(user, project, source_study, target_study):
    # Happy path already covered by fixture; now break purpose
    wrong_source = Study.objects.create(
        name="WrongSource",
        project=project,
        created_by=user,
        study_purpose="target",
        study_type="cohort",
    )
    ms = MappingSchema(
        source_study=wrong_source,
        target_study=target_study,
        created_by=user,
    )
    with pytest.raises(ValidationError):
        ms.clean()


def test_mapping_rule_non_self_requires_relation_metadata(schema, attributes):
    rule = MappingRule(
        schema=schema,
        source_attribute=attributes["src_pid"],
        target_attribute=attributes["tgt_pid"],
        role="value",
        relation_type="child",
    )
    with pytest.raises(ValidationError) as exc:
        rule.clean()
    assert "relation" in str(exc.value).lower()


def test_mapping_rule_rejects_relation_metadata_for_self(schema, attributes):
    rule = MappingRule(
        schema=schema,
        source_attribute=attributes["src_age"],
        target_attribute=attributes["tgt_age"],
        role="value",
        relation_type="self",
        relation_name="child_1",
    )
    with pytest.raises(ValidationError):
        rule.clean()


# ------------------- Transform safety -------------------

def test_validate_safe_transform_code_allows_simple_lambda():
    # Should not raise
    validate_safe_transform_code(
        "lambda value: int(value) if value else None",
    )


def test_validate_safe_transform_code_blocks_disallowed_call():
    with pytest.raises(ValidationError):
        validate_safe_transform_code("lambda value: __import__('os').system('echo x')")


def test_validate_safe_transform_code_allows_simple_for_loop():
    validate_safe_transform_code(
        """def transform(value):
    if not value:
        return []
    cleaned = []
    for item in value.split(','):
        item = item.strip()
        if item:
            cleaned.append(item)
    return cleaned"""
    )


def test_validate_safe_transform_code_blocks_for_else():
    with pytest.raises(ValidationError):
        validate_safe_transform_code(
            """def transform(value):
    for item in value:
        return item
    else:
        return None"""
        )


# ------------------- Form validation -------------------

def test_mapping_rule_form_infers_relation_instance(schema, attributes):
    form = MappingRuleForm(
        schema=schema,
        data={
            "source_attribute": attributes["src_pid"].id,
            "target_attribute": attributes["tgt_pid"].id,
            "role": "value",
            "relation_type": "child",
            "relation_instance": "",
            "create_relation_instance": "child",
        },
    )
    assert form.is_valid()
    rule = form.save(commit=False)
    assert rule.relation_name == "child_1"
    assert rule.inverse_relation_type == "parent"


@pytest.mark.django_db
def test_mapping_rule_form_does_not_advance_from_only_current_instance(schema, attributes):
    rule = MappingRule.objects.create(
        schema=schema,
        source_attribute=attributes["src_age"],
        target_attribute=attributes["tgt_age"],
        role="value",
        relation_type="child",
        relation_name="child_1",
        inverse_relation_type="parent",
        inverse_relation_name="parent",
    )

    form = MappingRuleForm(
        schema=schema,
        instance=rule,
        data={
            "source_attribute": attributes["src_age"].id,
            "target_attribute": attributes["tgt_age"].id,
            "role": "value",
            "relation_type": "child",
            "relation_instance": "",
            "create_relation_instance": "child",
        },
    )

    assert not form.is_valid()
    assert "already uses the next relation instance" in str(form.errors)


# ------------------- View workflow -------------------
HTTP_REDIRECT = 302


@pytest.mark.django_db
def test_start_harmonisation_view(client, user, source_study, target_study):
    client.force_login(user)
    url = reverse("health:start_harmonisation", kwargs={"study_id": source_study.id})
    resp = client.post(url, {
        "target_study": target_study.id,
        "universal_relation_type": "self",
        "comments": "demo",
    })
    assert resp.status_code == HTTP_REDIRECT
    assert MappingSchema.objects.filter(
        source_study=source_study,
        target_study=target_study,
    ).exists()


@pytest.mark.django_db
def test_dashboard_persists_relation_metadata(client, user, schema, attributes):
    client.force_login(user)
    url = reverse("health:harmonization_dashboard", kwargs={"schema_id": schema.id})
    prefix = f"variable_{attributes['src_age'].id}"
    payload = {
        f"{prefix}-source_attribute": attributes["src_age"].id,
        f"{prefix}-target_attribute": attributes["tgt_age"].id,
        f"{prefix}-role": "value",
        f"{prefix}-relation_type": "child",
        f"{prefix}-relation_instance": "",
        f"{prefix}-create_relation_instance": "child",
        f"{prefix}-transform_code": "lambda value: value",
        f"{prefix}-comments": "child age mapping",
    }
    resp = client.post(url, payload)
    assert resp.status_code == HTTP_REDIRECT
    rules = {r.source_attribute.variable_name: r for r in schema.rules.all()}
    assert rules["age"].role == "value"
    assert rules["age"].relation_type == "child"
    assert rules["age"].relation_name == "child_1"
    assert rules["age"].inverse_relation_type == "parent"


@pytest.mark.django_db
def test_relation_instance_choices_only_include_existing_instances(schema, attributes):
    MappingRule.objects.create(
        schema=schema,
        source_attribute=attributes["src_age"],
        target_attribute=attributes["tgt_age"],
        role="value",
        relation_type="child",
        relation_name="child_1",
        inverse_relation_type="parent",
        inverse_relation_name="parent",
    )

    values = [value for value, _label in relation_instance_choices(schema)]

    assert "existing:child:child_1" in values
    assert "create:child" not in values


@pytest.mark.django_db
def test_dashboard_includes_relation_controls(client, user, schema, attributes):
    client.force_login(user)
    url = reverse(
        "health:mapping_variable_card",
        kwargs={"schema_id": schema.id, "attribute_id": attributes["src_age"].id},
    )
    resp = client.get(url)
    assert resp.status_code == HTTP_OK
    content = resp.content.decode().lower()
    assert "mapping role" in content
    assert "relation type" in content
    assert "target attribute details" in content
    assert "mapping-target-details" in content
    assert "mapping-suggestions" in content
    assert "mapping-transform" in content
    assert "django-ace-widget" in content
    assert 'data-mode="python"' in content
    assert 'data-theme="github"' in content
    assert 'data-basicautocompletion="true"' in content
    assert 'data-liveautocompletion="true"' in content
    assert "ext-language_tools.js" in content
    assert "mapping-accordion-section" in content
    assert "mapping-card-section" not in content
    assert "pin-card-button" not in content


@pytest.mark.django_db
def test_dashboard_shell_renders_summaries_without_full_forms(client, user, schema, attributes):
    client.force_login(user)
    url = reverse("health:harmonization_dashboard", kwargs={"schema_id": schema.id})
    resp = client.get(url)
    assert resp.status_code == HTTP_OK
    content = resp.content.decode()
    assert "variable-summary-card" in content
    assert "page-size-select" not in content
    assert f'mapping-form-{attributes["src_age"].id}' not in content
    assert MappingRule.objects.count() == 0


@pytest.mark.django_db
def test_expanded_summary_copy_is_hidden_by_css(client, user, schema, attributes):
    client.force_login(user)
    url = reverse("health:harmonization_dashboard", kwargs={"schema_id": schema.id})
    resp = client.get(url)
    assert resp.status_code == HTTP_OK
    content = resp.content.decode()
    assert ".variable-summary-card.is-expanded > .variable-summary-details" in content
    assert ".variable-summary-card.is-expanded > .variable-summary-main .variable-meta" in content
    assert ".variable-summary-card.is-expanded > .variable-summary-main .completion-badge" in content


@pytest.mark.django_db
def test_variable_summaries_support_page_size_and_filter(client, user, schema, attributes):
    MappingRule.objects.create(
        schema=schema,
        source_attribute=attributes["src_age"],
        target_attribute=attributes["tgt_age"],
        role="value",
    )
    client.force_login(user)
    url = reverse("health:mapping_variable_summaries", kwargs={"schema_id": schema.id})
    resp = client.get(url, {"page_size": "10", "completion": "complete"})
    assert resp.status_code == HTTP_OK
    content = resp.content.decode()
    assert "age" in content
    assert "Context:</strong> Default schema context" in content
    assert "pid" not in content


@pytest.mark.django_db
def test_variable_card_context_and_comments_copy(client, user, schema, attributes):
    MappingRule.objects.create(
        schema=schema,
        source_attribute=attributes["src_age"],
        target_attribute=attributes["tgt_age"],
        patient_id_attribute=attributes["src_pid"],
        role="value",
        comments="   ",
    )
    client.force_login(user)
    url = reverse(
        "health:mapping_variable_card",
        kwargs={"schema_id": schema.id, "attribute_id": attributes["src_age"].id},
    )
    resp = client.get(url)
    assert resp.status_code == HTTP_OK
    content = resp.content.decode()
    assert "Patient: pid" in content
    assert "Use schema default" in content
    assert "recommended" not in content
    assert "Select patient, date/time, or location fields" in content
    assert "No notes yet" in content
    assert "Notes present" not in content


@pytest.mark.django_db
def test_variable_suggestions_return_cached_payload_without_openai_key(
    client,
    user,
    schema,
    attributes,
    settings,
):
    settings.OPENAI_API_KEY = ""
    cache_similarity_payload(
        schema,
        {
            str(attributes["src_age"].id): [
                {
                    "attribute_id": attributes["tgt_age"].id,
                    "variable_name": "AGE_YEARS",
                    "display_name": "Age Years",
                    "description": "Age in years",
                    "combined_similarity": 0.91,
                    "name_similarity": 0.88,
                    "description_similarity": 0.93,
                    "confidence_label": "Excellent Match",
                }
            ]
        },
    )
    client.force_login(user)
    url = reverse(
        "health:mapping_variable_suggestions",
        kwargs={"schema_id": schema.id, "attribute_id": attributes["src_age"].id},
    )
    resp = client.get(url)
    assert resp.status_code == HTTP_OK
    data = resp.json()
    assert data["success"] is True
    assert data["suggestions"][0]["attribute_id"] == attributes["tgt_age"].id


@pytest.mark.django_db
def test_uncached_variable_suggestions_return_ready_without_background_task(
    client,
    user,
    schema,
    attributes,
):
    client.force_login(user)
    url = reverse(
        "health:mapping_variable_suggestions",
        kwargs={"schema_id": schema.id, "attribute_id": attributes["src_pid"].id},
    )
    resp = client.get(url)
    assert resp.status_code == HTTP_OK
    data = resp.json()
    assert data["success"] is True
    assert data["status"] == "ready"
    assert data["suggestions"] == []


@pytest.mark.django_db
def test_variable_save_endpoint_creates_one_mapping_rule(client, user, schema, attributes):
    client.force_login(user)
    version_before = schema_dashboard_cache_version(schema.id)
    prefix = f"variable_{attributes['src_age'].id}"
    url = reverse(
        "health:save_mapping_variable",
        kwargs={"schema_id": schema.id, "attribute_id": attributes["src_age"].id},
    )
    resp = client.post(url, {
        f"{prefix}-target_attribute": attributes["tgt_age"].id,
        f"{prefix}-role": "value",
        f"{prefix}-relation_type": "child",
        f"{prefix}-relation_instance": "",
        f"{prefix}-create_relation_instance": "child",
        f"{prefix}-transform_code": "lambda value: value",
        f"{prefix}-comments": "child age mapping",
    })
    assert resp.status_code == HTTP_OK
    data = resp.json()
    assert data["success"] is True
    assert data["completion"] == "complete"
    rule = MappingRule.objects.get(schema=schema, source_attribute=attributes["src_age"])
    assert rule.target_attribute == attributes["tgt_age"]
    assert rule.relation_type == "child"
    assert rule.relation_name == "child_1"
    assert MappingRule.objects.count() == 1
    assert schema_dashboard_cache_version(schema.id) > version_before


@pytest.mark.django_db
def test_variable_save_endpoint_marks_needs_review(client, user, schema, attributes):
    client.force_login(user)
    prefix = f"variable_{attributes['src_age'].id}"
    url = reverse(
        "health:save_mapping_variable",
        kwargs={"schema_id": schema.id, "attribute_id": attributes["src_age"].id},
    )
    resp = client.post(url, {
        f"{prefix}-target_attribute": attributes["tgt_age"].id,
        f"{prefix}-needs_review": "on",
        f"{prefix}-role": "value",
        f"{prefix}-relation_type": "self",
    })

    assert resp.status_code == HTTP_OK
    data = resp.json()
    assert data["success"] is True
    assert data["completion"] == "needs-review"
    assert data["is_complete"] is False
    rule = MappingRule.objects.get(schema=schema, source_attribute=attributes["src_age"])
    assert rule.needs_review is True


@pytest.mark.django_db
def test_clear_mapping_rules_deletes_rules_and_resets_schema(client, user, schema, attributes):
    MappingRule.objects.create(
        schema=schema,
        source_attribute=attributes["src_age"],
        target_attribute=attributes["tgt_age"],
        role="value",
    )
    MappingRule.objects.create(
        schema=schema,
        source_attribute=attributes["src_pid"],
        not_mappable=True,
        role="value",
    )
    schema.status = "approved"
    schema.approved_by = user
    schema.approved_at = timezone.now()
    schema.save(update_fields=["status", "approved_by", "approved_at"])
    version_before = schema_dashboard_cache_version(schema.id)

    client.force_login(user)
    url = reverse("health:clear_mapping_rules", kwargs={"schema_id": schema.id})
    resp = client.post(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")

    assert resp.status_code == HTTP_OK
    data = resp.json()
    assert data["success"] is True
    assert data["deleted_count"] == 2
    assert data["completed_rules"] == 0
    assert MappingRule.objects.filter(schema=schema).count() == 0
    schema.refresh_from_db()
    assert schema.status == "provisional"
    assert schema.approved_by is None
    assert schema.approved_at is None
    assert schema_dashboard_cache_version(schema.id) > version_before


@pytest.mark.django_db
def test_ai_run_status_partial_exposes_refresh_metadata(client, user, schema):
    run = HarmonizationAIRun.objects.create(
        schema=schema,
        requested_by=user,
        status="running",
        requested_attribute_ids=list(schema.source_study.variables.values_list("id", flat=True)),
        processed_attributes_count=2,
        failed_attributes_count=1,
    )
    client.force_login(user)
    url = reverse("health:ai_harmonization_run_status_partial", kwargs={"run_id": run.id})
    resp = client.get(url, {"live": "1"})

    assert resp.status_code == HTTP_OK
    content = resp.content.decode()
    assert f'data-run-id="{run.id}"' in content
    assert 'data-run-status="running"' in content
    assert 'data-processed-count="2"' in content
    assert 'data-failed-count="1"' in content


@pytest.mark.django_db
def test_variable_eda_absence_renders_not_ready(client, user, schema, attributes):
    RawDataFile.objects.create(
        study=schema.source_study,
        file="raw_data/example.csv",
        original_filename="example.csv",
        file_format="csv",
        file_size=10,
        uploaded_by=user,
    )
    client.force_login(user)
    url = reverse(
        "health:mapping_variable_eda",
        kwargs={"schema_id": schema.id, "attribute_id": attributes["src_age"].id},
    )
    resp = client.get(url)
    assert resp.status_code == HTTP_OK
    assert "EDA not ready" in resp.content.decode()


@pytest.mark.django_db
def test_variable_eda_renders_cached_dashboard_html(client, user, schema, attributes):
    RawDataFile.objects.create(
        study=schema.source_study,
        file="raw_data/example.csv",
        original_filename="example.csv",
        file_format="csv",
        file_size=10,
        uploaded_by=user,
        eda_cache_source={
            "numeric_columns": [
                {
                    "name": "age",
                    "count": 12,
                    "missing": 0,
                    "mean": 42,
                    "dashboard_html": "<div id='age-plotly'>Plotly figure</div>",
                },
            ],
        },
    )
    client.force_login(user)
    url = reverse(
        "health:mapping_variable_eda",
        kwargs={"schema_id": schema.id, "attribute_id": attributes["src_age"].id},
    )
    resp = client.get(url)
    assert resp.status_code == HTTP_OK
    content = resp.content.decode()
    assert "Interactive EDA Figures" in content
    assert "age-plotly" in content


@pytest.mark.django_db
def test_variable_eda_renders_categorical_top_values_without_label(client, user, schema, attributes):
    RawDataFile.objects.create(
        study=schema.source_study,
        file="raw_data/example.csv",
        original_filename="example.csv",
        file_format="csv",
        file_size=10,
        uploaded_by=user,
        eda_cache_source={
            "categorical_columns": [
                {
                    "name": "age",
                    "unique": 2,
                    "missing": 0,
                    "top_values": [
                        {"value": "No", "count": 707, "percentage": 70.7},
                        {"value": "Yes", "count": 293, "percentage": 29.3},
                    ],
                },
            ],
        },
    )
    client.force_login(user)
    url = reverse(
        "health:mapping_variable_eda",
        kwargs={"schema_id": schema.id, "attribute_id": attributes["src_age"].id},
    )
    resp = client.get(url)
    assert resp.status_code == HTTP_OK
    content = resp.content.decode()
    assert "No (707)" in content
    assert "Yes (293)" in content


@pytest.mark.django_db
def test_transformation_suggestion_api_passes_mapping_context_and_eda(
    client,
    user,
    schema,
    attributes,
    monkeypatch,
):
    MappingRule.objects.create(
        schema=schema,
        source_attribute=attributes["src_age"],
        target_attribute=attributes["tgt_age"],
        relation_type="child",
        relation_name="child_1",
        comments="Use coded source values for transform context.",
    )
    RawDataFile.objects.create(
        study=schema.source_study,
        file="raw_data/example.csv",
        original_filename="example.csv",
        file_format="csv",
        file_size=10,
        uploaded_by=user,
        eda_cache_source={
            "categorical_columns": [
                {
                    "name": "age",
                    "unique": 2,
                    "missing": 0,
                    "top_values": [
                        {"value": "1", "count": 12, "percentage": 60},
                        {"value": "0", "count": 8, "percentage": 40},
                    ],
                    "dashboard_html": "<div>large chart should not be sent</div>",
                },
            ],
        },
    )
    captured = {}

    def fake_suggest(source_attribute, target_attribute, **kwargs):
        captured["source_attribute"] = source_attribute
        captured["target_attribute"] = target_attribute
        captured.update(kwargs)
        return "lambda value: int(value) if value else None"

    monkeypatch.setattr(
        "health.transformation_suggestion_service.transformation_suggestion_service.suggest_transformation_code",
        fake_suggest,
    )

    client.force_login(user)
    url = reverse("health:transformation_suggestion_api")
    resp = client.post(
        url,
        data=json.dumps({
            "schema_id": schema.id,
            "source_attribute_id": attributes["src_age"].id,
            "target_attribute_id": attributes["tgt_age"].id,
            "mapping_context": {"role": "value", "relation_type": "child"},
        }),
        content_type="application/json",
    )

    assert resp.status_code == HTTP_OK
    assert resp.json()["transformation_code"] == "lambda value: int(value) if value else None"
    assert captured["mapping_context"]["relation_type"] == "child"
    assert "Use coded source values" in captured["mapping_context"]["comments"]
    assert captured["source_eda_summary"]["column_type"] == "categorical"
    assert captured["source_eda_summary"]["top_values"][0]["value"] == "1"
    assert "dashboard_html" not in captured["source_eda_summary"]


@pytest.mark.django_db
def test_approve_mapping_view(client, user, schema, attributes):
    client.force_login(user)
    # create one rule to satisfy approval condition
    MappingRule.objects.create(
        schema=schema,
        source_attribute=attributes["src_age"],
        target_attribute=attributes["tgt_age"],
        role="value",
    )
    url = reverse("health:approve_mapping", kwargs={"schema_id": schema.id})
    resp = client.get(url)
    assert resp.status_code == HTTP_REDIRECT
    schema.refresh_from_db()
    assert schema.status == "approved"
    assert schema.approved_by == user
    assert schema.approved_at is not None


@pytest.mark.django_db
def test_transform_routes_child_mapping_to_generated_patient(schema, attributes):
    from health.tasks import transform_observations_for_schema

    patient = Patient.objects.create(unique_id="P001")
    Observation.objects.create(
        patient=patient,
        attribute=attributes["src_age"],
        int_value=7,
    )
    MappingRule.objects.create(
        schema=schema,
        source_attribute=attributes["src_age"],
        target_attribute=attributes["tgt_age"],
        role="value",
        relation_type="child",
        relation_name="child_1",
        inverse_relation_type="parent",
        inverse_relation_name="parent",
    )

    result = transform_observations_for_schema.run(schema.id)

    assert result["success"] is True
    child = Patient.objects.get(unique_id="P001_child_1")
    assert Observation.objects.filter(
        patient=child,
        attribute=attributes["tgt_age"],
        int_value=7,
    ).exists()
    system_attrs = schema.target_study.variables.filter(category=RELATIONSHIP_SYSTEM_CATEGORY)
    assert system_attrs.count() == 7
    assert Observation.objects.filter(
        patient=patient,
        attribute__variable_name=RELATIONSHIP_TYPE,
        text_value="child",
    ).exists()
    assert Observation.objects.filter(
        patient=patient,
        attribute__variable_name=RELATIONSHIP_RELATED_PATIENT_ID,
        text_value="P001_child_1",
    ).exists()
    assert Observation.objects.filter(
        patient=child,
        attribute__variable_name=RELATIONSHIP_DIRECTION,
        text_value="inverse",
    ).exists()


HTTP_OK = 200


@pytest.mark.django_db
def test_mapping_schemas_list_view(client, user, schema):
    client.force_login(user)
    url = reverse("health:study_harmonization_dashboard", kwargs={"study_id": schema.source_study_id})
    resp = client.get(url)
    assert resp.status_code == HTTP_REDIRECT
    assert str(schema.id) in resp["Location"]
