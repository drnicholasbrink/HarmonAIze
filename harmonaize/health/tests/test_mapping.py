import os
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.models import Attribute, Project, Study
from health.forms import MappingRuleForm
from health.models import MappingRule, MappingSchema, validate_safe_transform_code

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
        source_type="source",
    )
    src_attr2 = Attribute.objects.create(
        variable_name="pid",
        variable_type="string",
        source_type="source",
    )
    tgt_attr1 = Attribute.objects.create(
        variable_name="AGE_YEARS",
        variable_type="int",
        source_type="target",
    )
    tgt_attr2 = Attribute.objects.create(
        variable_name="PATIENT_ID",
        variable_type="string",
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


def test_mapping_rule_role_requires_relation(schema, attributes):
    rule = MappingRule(
        schema=schema,
        source_attribute=attributes["src_pid"],
        target_attribute=attributes["tgt_pid"],
        role="related_patient_id",
    )
    with pytest.raises(ValidationError) as exc:
        rule.clean()
    assert "relation" in str(exc.value).lower()


def test_mapping_rule_role_rejects_relation_when_not_related(schema, attributes):
    rule = MappingRule(
        schema=schema,
        source_attribute=attributes["src_age"],
        target_attribute=attributes["tgt_age"],
        role="value",
        related_relation_type="self",
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


# ------------------- Form validation -------------------

def test_mapping_rule_form_role_relation(schema, attributes):
    form = MappingRuleForm(
        schema=schema,
        data={
            "source_attribute": attributes["src_pid"].id,
            "target_attribute": attributes["tgt_pid"].id,
            "role": "related_patient_id",
            "related_relation_type": "",  # missing
        },
    )
    assert not form.is_valid()
    assert "related_relation_type" in form.errors


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
def test_edit_mapping_persists_role_and_relation(client, user, schema, attributes):
    client.force_login(user)
    # Build formset POST payload
    url = reverse("health:edit_mapping", kwargs={"schema_id": schema.id})
    management = {
        "form-TOTAL_FORMS": "2",
        "form-INITIAL_FORMS": "0",
        "form-MIN_NUM_FORMS": "0",
        "form-MAX_NUM_FORMS": "1000",
    }
    form0 = {
        "form-0-source_attribute": attributes["src_age"].id,
        "form-0-target_attribute": attributes["tgt_age"].id,
        "form-0-role": "value",
        "form-0-related_relation_type": "",
        "form-0-transform_code": "lambda value: value",
        "form-0-comments": "age mapping",
    }
    form1 = {
        "form-1-source_attribute": attributes["src_pid"].id,
        "form-1-target_attribute": attributes["tgt_pid"].id,
        "form-1-role": "related_patient_id",
        "form-1-related_relation_type": "child",
        "form-1-transform_code": "",
        "form-1-comments": "child id",
    }
    payload = {**management, **form0, **form1}
    resp = client.post(url, payload)
    assert resp.status_code == HTTP_REDIRECT
    rules = {r.source_attribute.variable_name: r for r in schema.rules.all()}
    assert rules["age"].role == "value"
    assert rules["pid"].role == "related_patient_id"
    assert rules["pid"].related_relation_type == "child"


@pytest.mark.django_db
def test_edit_mapping_view_includes_role_help(client, user, schema, attributes):
    client.force_login(user)
    # ensure at least one source attribute so form renders rows
    url = reverse("health:edit_mapping", kwargs={"schema_id": schema.id})
    resp = client.get(url)
    assert resp.status_code == HTTP_OK
    content = resp.content.decode().lower()
    # Check fragments from the help partial
    assert "role guidance" in content
    assert "related patient id" in content


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

