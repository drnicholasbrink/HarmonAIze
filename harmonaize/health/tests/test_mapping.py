import os
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.models import Attribute, Observation, Patient, Project, Study
from health.forms import MappingRuleForm
from health.models import MappingRule, MappingSchema, validate_safe_transform_code
from health.relationship_system import (
    RELATIONSHIP_SYSTEM_CATEGORY,
    RELATIONSHIP_DIRECTION,
    RELATIONSHIP_RELATED_PATIENT_ID,
    RELATIONSHIP_TYPE,
    relation_instance_choices,
)

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
    return Project.objects.create(name="Proj", created_by=user)

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
    resp = client.post(url, {"target_study": target_study.id, "comments": "demo"})
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
    # ensure at least one source attribute so form renders rows
    url = reverse("health:harmonization_dashboard", kwargs={"schema_id": schema.id})
    resp = client.get(url)
    assert resp.status_code == HTTP_OK
    content = resp.content.decode().lower()
    # Check fragments from the help partial
    assert "mapping role" in content
    assert "relation type" in content


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
    url = reverse("health:mapping_schemas", kwargs={"study_id": schema.source_study_id})
    resp = client.get(url)
    assert resp.status_code in {HTTP_REDIRECT, HTTP_OK}
    if resp.status_code == HTTP_OK:
        content = resp.content.decode()
        assert schema.target_study.name in content
        assert "Harmonisation Schemas" in content
