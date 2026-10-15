import json

import pytest
from django.contrib.auth import get_user_model

from core.models import Attribute, Project, Study
from health.ai_harmonization_service import AIHarmonizationService
from health.dashboard_fragment_cache import schema_dashboard_cache_version
from health.models import HarmonizationAIRun, MappingRule, MappingSchema
from health.transformation_suggestion_service import TransformationSuggestionService


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
    assert "search hints" in prompt["instructions"]["candidate_policy"]
    assert "core clinical/statistical concept" in prompt["instructions"]["candidate_policy"]
    assert "Make a judgement call" in prompt["instructions"]["candidate_policy"]
    assert "relation_type and record context carry who the value belongs to" in prompt["instructions"]["candidate_policy"]
    assert "Prefer decision='map'" in prompt["instructions"]["decision_policy"]
    assert "Use decision='not_mappable' only" in prompt["instructions"]["decision_policy"]
    assert "Use decision='needs_review' for true ambiguity" in prompt["instructions"]["decision_policy"]
    assert "not_mappable" in prompt["instructions"]["candidate_policy"]
    assert "needs_review" in prompt["instructions"]["candidate_policy"]
    assert "primary participant" in prompt["instructions"]["relation_policy"]
    assert "relation_type='child'" in prompt["instructions"]["relation_policy"]
    assert "otherwise correct concept target" in prompt["instructions"]["relation_policy"]
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
    assert "primary participant" in policy["relation_policy"]["instruction"]
    assert "map the clinical concept to the best target" in policy["relation_policy"]["instruction"]
    assert "same core measurement" in policy["decision_policy"]["map"]
    assert "broad-vs-narrow mismatch" in policy["decision_policy"]["needs_review"]


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
    version_before = schema_dashboard_cache_version(schema.id)

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
    assert current_rule.needs_review is True
    assert "duplicate_conflict=True" in current_rule.ai_reasoning_summary
    assert schema_dashboard_cache_version(schema.id) > version_before


@pytest.mark.django_db
def test_ai_not_mappable_decision_clears_target_and_review_flag(ai_mapping_context):
    service = _service()
    schema = ai_mapping_context["schema"]
    run = ai_mapping_context["run"]
    current_rule = MappingRule.objects.create(
        schema=schema,
        source_attribute=ai_mapping_context["src_weight"],
        target_attribute=ai_mapping_context["target_weight"],
        role="value",
        needs_review=True,
    )

    warning = service._apply_result_to_mapping_rule(
        run=run,
        result={
            "source_attribute_id": ai_mapping_context["src_weight"].id,
            "decision": "not_mappable",
            "recommended_target_attribute_id": None,
            "target_source": "none",
            "duplicate_conflict": False,
            "confidence_label": "high",
            "reasoning_summary": "No target variable represents this source variable.",
            "evidence": [],
            "mapping_rule": {
                "not_mappable": True,
                "role": "value",
                "patient_id_attribute_id": None,
                "datetime_attribute_id": None,
                "location_attribute_id": None,
                "relation_type": "self",
                "relation_instance_order": None,
                "transform_code": "",
                "comments": "Confidently not mappable.",
                "uses_custom_patient_id": False,
                "uses_custom_datetime": False,
                "uses_custom_location": False,
                "uses_custom_relation": False,
            },
        },
    )

    current_rule.refresh_from_db()
    assert warning is None
    assert current_rule.target_attribute is None
    assert current_rule.not_mappable is True
    assert current_rule.needs_review is False


@pytest.mark.django_db
def test_ai_map_without_target_is_flagged_for_review(ai_mapping_context):
    service = _service()
    schema = ai_mapping_context["schema"]
    run = ai_mapping_context["run"]

    warning = service._apply_result_to_mapping_rule(
        run=run,
        result={
            "source_attribute_id": ai_mapping_context["src_weight"].id,
            "decision": "map",
            "recommended_target_attribute_id": None,
            "target_source": "none",
            "duplicate_conflict": False,
            "confidence_label": "low",
            "reasoning_summary": "A mapping should exist but no target was returned.",
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
                "comments": "",
                "uses_custom_patient_id": False,
                "uses_custom_datetime": False,
                "uses_custom_location": False,
                "uses_custom_relation": False,
            },
        },
    )

    rule = MappingRule.objects.get(schema=schema, source_attribute=ai_mapping_context["src_weight"])
    assert warning
    assert rule.target_attribute is None
    assert rule.needs_review is True
    assert "without a target attribute" in rule.ai_reasoning_summary


@pytest.mark.django_db
def test_ai_child_relation_decision_sets_child_relation_instance(ai_mapping_context):
    service = _service()
    schema = ai_mapping_context["schema"]
    run = ai_mapping_context["run"]

    warning = service._apply_result_to_mapping_rule(
        run=run,
        result={
            "source_attribute_id": ai_mapping_context["src_weight"].id,
            "decision": "map",
            "recommended_target_attribute_id": ai_mapping_context["target_weight"].id,
            "target_source": "catalog_fallback",
            "duplicate_conflict": False,
            "confidence_label": "high",
            "reasoning_summary": "The source variable describes a child-owned value.",
            "evidence": [],
            "mapping_rule": {
                "not_mappable": False,
                "role": "value",
                "patient_id_attribute_id": None,
                "datetime_attribute_id": None,
                "location_attribute_id": None,
                "relation_type": "child",
                "relation_instance_order": 1,
                "transform_code": "",
                "comments": "Child-owned mapping.",
                "uses_custom_patient_id": False,
                "uses_custom_datetime": False,
                "uses_custom_location": False,
                "uses_custom_relation": True,
            },
        },
    )

    rule = MappingRule.objects.get(schema=schema, source_attribute=ai_mapping_context["src_weight"])
    assert warning is None
    assert rule.target_attribute == ai_mapping_context["target_weight"]
    assert rule.relation_type == "child"
    assert rule.relation_name == "child_1"
    assert rule.inverse_relation_type


def test_transformation_prompt_includes_mapping_context_and_eda_summary():
    service = TransformationSuggestionService.__new__(TransformationSuggestionService)
    prompt = service._create_transformation_prompt(
        {
            "source": {
                "variable_name": "source_value",
                "display_name": "Source value",
                "description": "Source coded value",
                "variable_type": "string",
                "unit": "",
                "ontology_code": "",
            },
            "target": {
                "variable_name": "target_value",
                "display_name": "Target value",
                "description": "Target numeric value",
                "variable_type": "int",
                "unit": "",
                "ontology_code": "",
            },
            "mapping_context": {
                "role": "value",
                "relation_type": "child",
                "comments": "Observed source values use coded categories.",
            },
            "source_eda_summary": {
                "column_type": "categorical",
                "top_values": [
                    {"value": "1", "count": 12},
                    {"value": "0", "count": 8},
                ],
            },
        },
    )

    assert "CURRENT MAPPING CONTEXT" in prompt
    assert "SOURCE VARIABLE EDA SUMMARY" in prompt
    assert "observed encodings" in prompt
    assert '"relation_type": "child"' in prompt
    assert '"top_values"' in prompt
