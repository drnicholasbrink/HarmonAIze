import json

import pytest
from django.contrib.auth import get_user_model

from core.models import Attribute, Project, Study
from health.ai_harmonization_service import AIHarmonizationService
from health.models import HarmonizationAIRun, MappingRule, MappingSchema


User = get_user_model()


@pytest.fixture
def ai_mapping_context(db):
    user = User.objects.create_user(email="ai-service@example.com", password="pw12345")
    project = Project.objects.create(name="AI Service Project", created_by=user)
    source_study = Study.objects.create(
        name="Source Study",
        project=project,
        created_by=user,
        study_purpose="source",
        study_type="cohort",
    )
    target_study = Study.objects.create(
        name="Target Study",
        project=project,
        created_by=user,
        study_purpose="target",
        study_type="cohort",
    )
    src_age = Attribute.objects.create(
        variable_name="age_years",
        display_name="Age",
        description="Age in years",
        variable_type="int",
        category="health",
        source_type="source",
    )
    src_weight = Attribute.objects.create(
        variable_name="weight_kg",
        display_name="Weight",
        description="Weight in kilograms",
        variable_type="float",
        category="health",
        source_type="source",
    )
    target_age = Attribute.objects.create(
        variable_name="AGE",
        display_name="Age",
        description="Target age in years",
        variable_type="int",
        category="health",
        source_type="target",
    )
    target_weight = Attribute.objects.create(
        variable_name="WEIGHT",
        display_name="Weight",
        description="Target weight in kilograms",
        variable_type="float",
        category="health",
        source_type="target",
    )
    source_study.variables.set([src_age, src_weight])
    target_study.variables.set([target_age, target_weight])
    schema = MappingSchema.objects.create(
        source_study=source_study,
        target_study=target_study,
        created_by=user,
    )
    run = HarmonizationAIRun.objects.create(
        schema=schema,
        requested_by=user,
        top_candidates_per_variable=10,
    )
    return {
        "schema": schema,
        "run": run,
        "src_age": src_age,
        "src_weight": src_weight,
        "target_age": target_age,
        "target_weight": target_weight,
    }


def _service():
    return AIHarmonizationService.__new__(AIHarmonizationService)


@pytest.mark.django_db
def test_variable_prompt_is_compact_and_uses_top_10_candidates(ai_mapping_context):
    service = _service()
    run = ai_mapping_context["run"]
    source_attribute = ai_mapping_context["src_age"]
    target = ai_mapping_context["target_age"]
    baseline = {
        source_attribute.id: [
            {
                "attribute_id": target.id,
                "variable_name": target.variable_name,
                "display_name": target.display_name,
                "description": target.description,
                "variable_type": target.variable_type,
                "unit": target.unit,
                "combined_similarity": 0.91,
                "confidence_grade": "excellent",
            }
            for _ in range(10)
        ]
    }

    prompt = json.loads(service._build_user_prompt(run=run, attribute=source_attribute, baseline=baseline))

    assert prompt["task"] == "analyze_one_source_variable"
    assert prompt["instructions"]["top_candidates_per_variable"] == 10
    assert len(prompt["source_variable"]["candidate_targets"]) == 10
    assert "mappings" not in prompt
    assert "target_variables" not in prompt
    assert prompt["reserved_target_attribute_ids"] == []


@pytest.mark.django_db
def test_context_payloads_include_target_catalog_and_existing_mappings(ai_mapping_context):
    service = _service()
    run = ai_mapping_context["run"]
    MappingRule.objects.create(
        schema=ai_mapping_context["schema"],
        source_attribute=ai_mapping_context["src_age"],
        target_attribute=ai_mapping_context["target_age"],
        role="value",
    )

    catalog = service._target_variable_catalog(run)
    mappings = service._existing_mapping_rules_context(run)
    policy = service._mapping_policy_context(run)

    assert {item["attribute_id"] for item in catalog["target_variables"]} == {
        ai_mapping_context["target_age"].id,
        ai_mapping_context["target_weight"].id,
    }
    assert mappings["mappings"][0]["target_attribute"]["attribute_id"] == ai_mapping_context["target_age"].id
    assert policy["duplicate_policy"]["occupied_target_attribute_ids"] == [ai_mapping_context["target_age"].id]


@pytest.mark.django_db
def test_duplicate_ai_target_is_rejected_without_overwriting_manual_mapping(ai_mapping_context):
    service = _service()
    schema = ai_mapping_context["schema"]
    run = ai_mapping_context["run"]
    MappingRule.objects.create(
        schema=schema,
        source_attribute=ai_mapping_context["src_age"],
        target_attribute=ai_mapping_context["target_age"],
        role="value",
    )
    current_rule = MappingRule.objects.create(
        schema=schema,
        source_attribute=ai_mapping_context["src_weight"],
        target_attribute=ai_mapping_context["target_weight"],
        role="value",
    )

    warning = service._apply_result_to_mapping_rule(
        run=run,
        result={
            "source_attribute_id": ai_mapping_context["src_weight"].id,
            "decision": "needs_review",
            "recommended_target_attribute_id": ai_mapping_context["target_age"].id,
            "target_source": "catalog_fallback",
            "duplicate_conflict": True,
            "confidence_label": "low",
            "reasoning_summary": "Age target looks similar but is already used.",
            "evidence": [],
            "mapping_rule": {
                "not_mappable": False,
                "role": "value",
                "patient_id_attribute_id": None,
                "datetime_attribute_id": None,
                "location_attribute_id": None,
                "relation_type": "self",
                "relation_instance_order": None,
                "transform_code": "",
                "comments": "Needs review due to duplicate target.",
                "uses_custom_patient_id": False,
                "uses_custom_datetime": False,
                "uses_custom_location": False,
                "uses_custom_relation": False,
            },
        },
    )

    current_rule.refresh_from_db()
    assert warning
    assert current_rule.target_attribute_id == ai_mapping_context["target_weight"].id
    assert "duplicate_conflict=True" in current_rule.ai_reasoning_summary
