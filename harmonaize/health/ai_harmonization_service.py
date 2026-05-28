"""OpenAI-backed AI refresh service for health harmonization mapping rules."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from openai import APIError, BadRequestError, OpenAI, RateLimitError

from core.models import Attribute, StudyDocument
from core.similarity_service import similarity_service

from .models import HarmonizationAIRun, MappingRule
from .relationship_system import (
    REPEATABLE_RELATION_TYPES,
    infer_inverse_relation,
    infer_next_relation_name,
    relation_instance_choices,
    relation_name_from_order,
)
from .summary_stats_context import build_deidentified_summary_stats_context

logger = logging.getLogger(__name__)


class AIHarmonizationService:
    """Refresh mapping rules using OpenAI Responses and hosted retrieval tools."""

    LOG_PREVIEW_CHARS = 4000

    GRADE_ORDER = {
        "very_poor": 0,
        "poor": 1,
        "fair": 2,
        "good": 3,
        "excellent": 4,
    }

    def __init__(self) -> None:
        if not settings.OPENAI_API_KEY:
            msg = "OPENAI_API_KEY must be set in settings"
            raise ValueError(msg)

        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = getattr(settings, "OPENAI_TRANSFORMATION_MODEL", "gpt-5")

    def refresh_run(self, run: HarmonizationAIRun) -> dict[str, Any]:
        """Run an AI refresh for either a schema batch or a selected attribute set."""
        schema = run.schema
        source_attributes = list(schema.source_study.variables.order_by("variable_name"))
        baseline = similarity_service.get_mapping_suggestions(
            source_study_id=schema.source_study_id,
            target_study_id=schema.target_study_id,
            limit_per_source=run.top_candidates_per_variable,
        )

        requested_ids = {int(value) for value in (run.requested_attribute_ids or [])}
        eligible_attributes = [
            attribute
            for attribute in source_attributes
            if self._attribute_is_eligible(
                attribute_id=attribute.id,
                baseline_matches=baseline.get(attribute.id, []),
                requested_ids=requested_ids,
                threshold_grade=run.run_below_confidence_grade,
            )
        ]

        if not eligible_attributes:
            return {
                "processed_attributes_count": 0,
                "failed_attributes_count": 0,
                "usage_summary": {},
                "openai_response_ids": [],
                "message": "No eligible attributes found for AI refresh.",
            }

        eligible_attribute_ids = [attribute.id for attribute in eligible_attributes]
        run.requested_attribute_ids = eligible_attribute_ids
        run.processed_attributes_count = 0
        run.failed_attributes_count = 0
        run.save(
            update_fields=[
                "requested_attribute_ids",
                "processed_attributes_count",
                "failed_attributes_count",
                "updated_at",
            ]
        )

        vector_store_id, uploaded_file_ids = self._prepare_retrieval_context(run)
        response = self._request_structured_refresh(
            run=run,
            attributes=eligible_attributes,
            baseline=baseline,
            vector_store_id=vector_store_id,
            fallback_file_ids=uploaded_file_ids[:4],
        )

        payload = self._extract_structured_payload(response)
        self._log_response_payload(
            label="harmonization_refresh_response",
            response=response,
            payload=payload,
        )
        results = payload.get("results", []) if isinstance(payload, Mapping) else []

        processed_count = 0
        failed_count = 0
        warning_messages: list[str] = []
        for item in results:
            try:
                warning_message = self._apply_result_to_mapping_rule(run=run, result=item)
                processed_count += 1
                if warning_message:
                    warning_messages.append(warning_message)
            except Exception:
                failed_count += 1
                warning_messages.append(self._build_item_failure_message(item, "Failed applying AI harmonization result."))
                logger.exception(
                    "Failed applying AI harmonization result for schema %s",
                    schema.id,
                )
            run.processed_attributes_count = processed_count
            run.failed_attributes_count = failed_count
            run.save(
                update_fields=[
                    "processed_attributes_count",
                    "failed_attributes_count",
                    "updated_at",
                ]
            )

        missing_ids = {attribute.id for attribute in eligible_attributes} - {
            int(item.get("source_attribute_id"))
            for item in results
            if isinstance(item, Mapping) and item.get("source_attribute_id") is not None
        }
        failed_count += len(missing_ids)
        if missing_ids:
            warning_messages.extend(
                [f"No AI result returned for source attribute {attribute_id}." for attribute_id in sorted(missing_ids)]
            )
            run.processed_attributes_count = processed_count
            run.failed_attributes_count = failed_count
            run.save(
                update_fields=[
                    "processed_attributes_count",
                    "failed_attributes_count",
                    "updated_at",
                ]
            )

        usage_summary = self._extract_usage_summary(response)
        response_ids = [value for value in [getattr(response, "id", None)] if value]

        return {
            "processed_attributes_count": processed_count,
            "failed_attributes_count": failed_count,
            "usage_summary": usage_summary,
            "openai_response_ids": response_ids,
            "message": self._build_run_message(processed_count, failed_count, warning_messages),
        }

    def _build_run_message(
        self,
        processed_count: int,
        failed_count: int,
        warning_messages: list[str],
    ) -> str:
        summary = f"Processed {processed_count} attribute(s) with {failed_count} failure(s)."
        if not warning_messages:
            return summary
        preview = " | ".join(warning_messages[:5])
        if len(warning_messages) > 5:
            preview += f" | +{len(warning_messages) - 5} more issue(s)."
        return f"{summary} {preview}"

    def _build_item_failure_message(self, item: Any, default_message: str) -> str:
        if isinstance(item, Mapping):
            attribute_id = item.get("source_attribute_id")
            if attribute_id is not None:
                return f"Source attribute {attribute_id}: {default_message}"
        return default_message

    def _attribute_is_eligible(
        self,
        *,
        attribute_id: int,
        baseline_matches: list[dict[str, Any]],
        requested_ids: set[int],
        threshold_grade: str,
    ) -> bool:
        if requested_ids and attribute_id not in requested_ids:
            return False

        if threshold_grade == "all":
            return True

        if not baseline_matches:
            return True

        best_grade = baseline_matches[0].get("confidence_grade") or "very_poor"
        return self.GRADE_ORDER.get(best_grade, -1) < self.GRADE_ORDER.get(threshold_grade, 4)

    def _prepare_retrieval_context(self, run: HarmonizationAIRun) -> tuple[str | None, list[str]]:
        uploaded_file_ids: list[str] = []
        file_fields = []

        if run.include_existing_codebooks:
            if run.schema.source_study.codebook:
                file_fields.append(run.schema.source_study.codebook)
            if run.schema.target_study.codebook:
                file_fields.append(run.schema.target_study.codebook)

        if run.include_protocol:
            if run.schema.source_study.protocol_file:
                file_fields.append(run.schema.source_study.protocol_file)
            if run.schema.target_study.protocol_file:
                file_fields.append(run.schema.target_study.protocol_file)

        if run.include_additional_documents:
            docs = StudyDocument.objects.filter(
                study__in=[run.schema.source_study, run.schema.target_study],
            ).order_by("study_id", "id")
            file_fields.extend(document.file for document in docs if document.file)

        for file_field in file_fields:
            try:
                uploaded = self._upload_file(file_field)
            except Exception:
                logger.exception("Failed uploading file %s for AI harmonization", getattr(file_field, "name", "unknown"))
                continue
            if uploaded:
                uploaded_file_ids.append(uploaded)

        vector_store_id = None
        if uploaded_file_ids:
            try:
                vector_store_id = self._create_vector_store(uploaded_file_ids, run)
            except Exception:
                logger.exception("Failed creating vector store for AI harmonization run %s", run.id)

        return vector_store_id, uploaded_file_ids

    def _upload_file(self, file_field) -> str | None:
        file_path = getattr(file_field, "path", None)
        if file_path:
            with open(file_path, "rb") as upload_stream:
                uploaded = self.client.files.create(
                    file=upload_stream,
                    purpose="user_data",
                )
            return getattr(uploaded, "id", None)

        file_field.open("rb")
        try:
            if hasattr(file_field.file, "seek"):
                file_field.file.seek(0)
            file_bytes = file_field.read()
            uploaded = self.client.files.create(
                file=(Path(getattr(file_field, "name", "upload.bin")).name, file_bytes),
                purpose="user_data",
            )
        finally:
            file_field.close()

        return getattr(uploaded, "id", None)

    def _create_vector_store(self, file_ids: list[str], run: HarmonizationAIRun) -> str | None:
        vector_stores = self._get_vector_stores_client()
        if vector_stores is None:
            return None

        store = vector_stores.create(
            name=f"harmonization-schema-{run.schema_id}-run-{run.id}",
        )
        file_batches = getattr(vector_stores, "file_batches", None)
        if file_batches is None:
            return None

        if hasattr(file_batches, "create_and_poll"):
            file_batches.create_and_poll(vector_store_id=store.id, file_ids=file_ids)
        elif hasattr(file_batches, "create"):
            file_batches.create(vector_store_id=store.id, file_ids=file_ids)
        else:
            return None

        return getattr(store, "id", None)

    def _get_vector_stores_client(self):
        if hasattr(self.client, "vector_stores"):
            return self.client.vector_stores

        beta_client = getattr(self.client, "beta", None)
        if beta_client is not None and hasattr(beta_client, "vector_stores"):
            return beta_client.vector_stores

        return None

    def _request_structured_refresh(
        self,
        *,
        run: HarmonizationAIRun,
        attributes: list[Attribute],
        baseline: dict[int, list[dict[str, Any]]],
        vector_store_id: str | None,
        fallback_file_ids: list[str],
    ):
        user_content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": self._build_user_prompt(run=run, attributes=attributes, baseline=baseline),
            },
        ]

        tools: list[dict[str, Any]] = []
        if vector_store_id:
            tools.append(
                {
                    "type": "file_search",
                    "vector_store_ids": [vector_store_id],
                    "max_num_results": 6,
                }
            )
        else:
            for file_id in fallback_file_ids:
                user_content.append({"type": "input_file", "file_id": file_id})

        request_kwargs = {
            "model": self.model,
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": self._system_prompt(),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "harmonization_mapping_refresh",
                    "strict": True,
                    "schema": self._response_schema(),
                }
            },
            "tools": tools,
        }

        use_background = bool(run.use_openai_background and len(attributes) > 3)
        if use_background:
            request_kwargs["background"] = True
            request_kwargs["store"] = True

        self._log_request_payload(
            label="harmonization_refresh_request",
            payload={
                "run_id": run.id,
                "schema_id": run.schema_id,
                "model": self.model,
                "use_background": use_background,
                "attribute_ids": [attribute.id for attribute in attributes],
                "tools": tools,
                "user_prompt": user_content[0]["text"],
            },
        )

        try:
            response = self.client.responses.create(**request_kwargs)
        except (BadRequestError, APIError, RateLimitError):
            logger.exception("OpenAI harmonization refresh request failed")
            raise

        if use_background:
            response = self._poll_background_response(response.id)

        return response

    def _truncate_for_log(self, value: Any) -> str:
        if isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value, default=str, ensure_ascii=True, indent=2)
            except TypeError:
                text = str(value)
        if len(text) <= self.LOG_PREVIEW_CHARS:
            return text
        return f"{text[:self.LOG_PREVIEW_CHARS]}... [truncated {len(text) - self.LOG_PREVIEW_CHARS} chars]"

    def _log_request_payload(self, *, label: str, payload: Any) -> None:
        logger.info("%s: %s", label, self._truncate_for_log(payload))

    def _log_response_payload(self, *, label: str, response: Any, payload: Any) -> None:
        logger.info(
            "%s: response_id=%s payload=%s",
            label,
            getattr(response, "id", None),
            self._truncate_for_log(payload),
        )

    def _poll_background_response(self, response_id: str):
        deadline = time.monotonic() + 600
        response = self.client.responses.retrieve(response_id)
        while getattr(response, "status", None) in {"queued", "in_progress"}:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for OpenAI background response {response_id}")
            time.sleep(2)
            response = self.client.responses.retrieve(response_id)
        return response

    def _extract_structured_payload(self, response) -> Mapping[str, Any]:
        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str) and output_text.strip():
            return json.loads(output_text)

        for item in getattr(response, "output", []) or []:
            for content in getattr(item, "content", []) or []:
                text = getattr(content, "text", None)
                if isinstance(text, str) and text.strip():
                    return json.loads(text)

        raise ValueError("OpenAI response did not contain a structured payload")

    def _extract_usage_summary(self, response) -> dict[str, Any]:
        usage = getattr(response, "usage", None)
        if not usage:
            return {}

        return {
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }

    def _apply_result_to_mapping_rule(self, *, run: HarmonizationAIRun, result: Mapping[str, Any]) -> str | None:
        source_attribute_id = int(result["source_attribute_id"])
        rule, _ = MappingRule.objects.get_or_create(
            schema=run.schema,
            source_attribute_id=source_attribute_id,
            defaults={"role": "value"},
        )

        mapping_payload = result.get("mapping_rule", {}) or {}
        target_id = result.get("recommended_target_attribute_id")
        decision = result.get("decision") or "map"

        target_attribute = None
        if target_id:
            target_attribute = run.schema.target_study.variables.filter(pk=target_id).first()

        role = mapping_payload.get("role") or rule.role or "value"
        if role not in dict(MappingRule.ROLE_CHOICES):
            role = "value"

        rule.target_attribute = target_attribute
        rule.not_mappable = bool(mapping_payload.get("not_mappable", decision == "not_mappable"))
        rule.role = role
        rule.patient_id_attribute = self._get_source_attribute(run, mapping_payload.get("patient_id_attribute_id"))
        rule.datetime_attribute = self._get_source_attribute(run, mapping_payload.get("datetime_attribute_id"))
        rule.location_attribute = self._get_source_attribute(run, mapping_payload.get("location_attribute_id"))

        previous_relation_type = rule.relation_type
        previous_relation_name = rule.relation_name
        relation_type = mapping_payload.get("relation_type") or "self"
        valid_relations = {choice[0] for choice in run.schema.RELATION_CHOICES}
        rule.relation_type = relation_type if relation_type in valid_relations else "self"
        if rule.relation_type == "self":
            rule.relation_name = ""
            rule.inverse_relation_type = ""
            rule.inverse_relation_name = ""
        else:
            relation_order = mapping_payload.get("relation_instance_order")
            if relation_order is not None:
                try:
                    relation_order = int(relation_order)
                    rule.relation_name = relation_name_from_order(
                        rule.relation_type,
                        relation_order,
                    )
                except (TypeError, ValueError, ValidationError):
                    relation_order = None
                    rule.relation_name = ""
            if not rule.relation_name and rule.relation_type not in REPEATABLE_RELATION_TYPES:
                rule.relation_name = relation_name_from_order(rule.relation_type)
            if (
                not rule.relation_name
                and previous_relation_type == rule.relation_type
                and previous_relation_name
            ):
                rule.relation_name = previous_relation_name
            if not rule.relation_name:
                rule.relation_name = infer_next_relation_name(
                    schema=run.schema,
                    relation_type=rule.relation_type,
                    exclude_rule_id=rule.pk,
                )
            rule.inverse_relation_type, rule.inverse_relation_name = infer_inverse_relation(
                rule.relation_type,
            )

        transform_code = (mapping_payload.get("transform_code") or "").strip()
        rule.transform_code = transform_code
        if not rule.comments:
            rule.comments = (mapping_payload.get("comments") or result.get("reasoning_summary") or "").strip()

        rule.ai_last_refreshed_at = timezone.now()
        rule.ai_last_run = run
        rule.ai_confidence_label = (result.get("confidence_label") or "").strip()
        rule.ai_reasoning_summary = (result.get("reasoning_summary") or "").strip()
        transform_warning = None

        try:
            rule.full_clean()
        except ValidationError as exc:
            transform_warning = (
                f"{rule.source_attribute.variable_name}: generated transform code was rejected and cleared ({exc})."
            )
            logger.warning(
                "AI-populated mapping rule transform code failed validation for source attribute %s: %s",
                source_attribute_id,
                exc,
            )
            rule.transform_code = ""
            if transform_warning not in rule.ai_reasoning_summary:
                rule.ai_reasoning_summary = " ".join(
                    part for part in [rule.ai_reasoning_summary, transform_warning] if part
                ).strip()
            rule.full_clean()

        rule.save()
        return transform_warning

    def _get_source_attribute(self, run: HarmonizationAIRun, attribute_id: Any) -> Attribute | None:
        if not attribute_id:
            return None
        return run.schema.source_study.variables.filter(pk=attribute_id).first()

    def _build_user_prompt(
        self,
        *,
        run: HarmonizationAIRun,
        attributes: Iterable[Attribute],
        baseline: dict[int, list[dict[str, Any]]],
    ) -> str:
        summary_stats_context = self._build_summary_stats_context(run=run, attributes=attributes)
        summary_stats_by_variable = {}
        if isinstance(summary_stats_context, Mapping):
            candidate_summary_stats = summary_stats_context.get("variables", {})
            if isinstance(candidate_summary_stats, Mapping):
                summary_stats_by_variable = candidate_summary_stats
            elif candidate_summary_stats:
                logger.warning(
                    "Ignoring malformed deidentified summary stats for run %s because 'variables' is not a mapping",
                    run.id,
                )
        elif summary_stats_context:
            logger.warning(
                "Ignoring malformed deidentified summary stats for run %s because the context is not a mapping",
                run.id,
            )
            summary_stats_context = {}

        variables_payload = []
        for attribute in attributes:
            candidates = []
            for match in baseline.get(attribute.id, []):
                candidates.append(
                    {
                        "attribute_id": match.get("attribute_id"),
                        "variable_name": match.get("variable_name"),
                        "display_name": match.get("display_name"),
                        "description": match.get("description"),
                        "variable_type": match.get("variable_type"),
                        "unit": match.get("unit"),
                        "combined_similarity": match.get("combined_similarity"),
                        "confidence_grade": match.get("confidence_grade"),
                    }
                )

            variables_payload.append(
                {
                    "source_attribute_id": attribute.id,
                    "variable_name": attribute.variable_name,
                    "display_name": attribute.display_name or "",
                    "description": attribute.description or "",
                    "variable_type": attribute.variable_type or "",
                    "unit": attribute.unit or "",
                    "ontology_code": attribute.ontology_code or "",
                    "deidentified_summary_stats": summary_stats_by_variable.get(attribute.variable_name, {}),
                    "candidate_targets": candidates,
                }
            )

        return json.dumps(
            {
                "source_study": {
                    "id": run.schema.source_study_id,
                    "name": run.schema.source_study.name,
                },
                "target_study": {
                    "id": run.schema.target_study_id,
                    "name": run.schema.target_study.name,
                },
                "instructions": {
                    "run_below_confidence_grade": run.run_below_confidence_grade,
                    "top_candidates_per_variable": run.top_candidates_per_variable,
                    "include_deidentified_summary_stats": bool(summary_stats_context),
                },
                "relation_naming_policy": {
                    "repeatable_relation_types": sorted(REPEATABLE_RELATION_TYPES),
                    "repeatable_names": "Use relation_instance_order only; the application constructs names as <relation_type>_<order>, for example child_1.",
                    "non_repeatable_names": "For mother, father, parent, and spouse, leave relation_instance_order null; the application uses the relation type as the instance name.",
                    "existing_instances": [
                        {"value": value, "label": label}
                        for value, label in relation_instance_choices(run.schema)
                        if value
                    ],
                },
                "deidentified_summary_stats_context": summary_stats_context,
                "variables": variables_payload,
            },
            indent=2,
        )

    def _build_summary_stats_context(
        self,
        *,
        run: HarmonizationAIRun,
        attributes: Iterable[Attribute],
    ) -> dict[str, Any]:
        if not run.include_deidentified_summary_stats:
            return {}

        summary_context = build_deidentified_summary_stats_context(
            source_study=run.schema.source_study,
            variable_names=[attribute.variable_name for attribute in attributes],
        )
        if not summary_context:
            logger.info(
                "Skipping deidentified summary stats for run %s because no eligible cached summary data was found",
                run.id,
            )
            return {}
        return summary_context

    def _system_prompt(self) -> str:
        return (
            "You are a health data harmonization assistant. Use the supplied source variable metadata, "
            "candidate target attributes, retrieved study documentation, and any explicitly provided de-identified summary statistics to refresh mapping rules. "
            "Only use summary statistics when they are supplied in the prompt. Never infer or request row-level data, example records, or potentially identifiable values. "
            "Only recommend target attributes from the candidate_targets list for each variable unless "
            "you determine the variable is not mappable. Keep transform code safe and simple. "
            "Use relation_type='self' unless the source variable clearly describes another entity, such as a child, parent, spouse, sibling, mother, or father. "
            "Do not map source variables directly to relationship-system target attributes. "
            "For non-self repeatable mappings, provide relation_instance_order only when the source variable clearly encodes the instance order, such as child 1 or sibling 2. "
            "Do not provide free-text relation instance names; the application constructs stable names and inverse relation metadata. "
            "If transform code is needed, return exactly one inline lambda expression or one def transform(value) function. "
            "Do not invent helper functions, utility wrappers, or extra defs. "
            "Only use whitelisted built-ins such as int, float, str, bool, round, abs, min, max, len, sum, any, all, sorted, reversed, enumerate, range, zip, list, tuple, dict, and set, plus safe string/list/dict methods like strip, lower, upper, split, replace, append, get, items, and update. "
            "Never emit custom calls such as parse_date, parse_int, parse_time, normalize_sex, strip_whitespace, clamp_0_10_int, round_to_int, boolean_to_yes_no, map_nonempty_to_yes, standardize_mode_of_delivery, to_grams, or any similarly invented function. "
            "Return valid JSON that exactly matches the provided schema."
        )

    def _response_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["results"],
            "properties": {
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "source_attribute_id",
                            "decision",
                            "recommended_target_attribute_id",
                            "confidence_label",
                            "reasoning_summary",
                            "evidence",
                            "mapping_rule",
                        ],
                        "properties": {
                            "source_attribute_id": {"type": "integer"},
                            "decision": {
                                "type": "string",
                                "enum": ["map", "not_mappable", "needs_review"],
                            },
                            "recommended_target_attribute_id": {
                                "type": ["integer", "null"],
                            },
                            "confidence_label": {"type": "string"},
                            "reasoning_summary": {"type": "string"},
                            "evidence": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["source", "quote", "relevance"],
                                    "properties": {
                                        "source": {"type": "string"},
                                        "quote": {"type": "string"},
                                        "relevance": {"type": "string"},
                                    },
                                },
                            },
                            "mapping_rule": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "not_mappable",
                                    "role",
                                    "patient_id_attribute_id",
                                    "datetime_attribute_id",
                                    "location_attribute_id",
                                    "relation_type",
                                    "relation_instance_order",
                                    "transform_code",
                                    "comments",
                                ],
                                "properties": {
                                    "not_mappable": {"type": "boolean"},
                                    "role": {
                                        "type": "string",
                                        "enum": [
                                            "value",
                                            "patient_id",
                                            "datetime",
                                            "location",
                                        ],
                                    },
                                    "patient_id_attribute_id": {"type": ["integer", "null"]},
                                    "datetime_attribute_id": {"type": ["integer", "null"]},
                                    "location_attribute_id": {"type": ["integer", "null"]},
                                    "relation_type": {
                                        "type": "string",
                                        "enum": ["self", "child", "parent", "father", "mother", "spouse", "sibling", "other"],
                                    },
                                    "relation_instance_order": {"type": ["integer", "null"]},
                                    "transform_code": {"type": "string"},
                                    "comments": {"type": "string"},
                                },
                            },
                        },
                    },
                }
            },
        }


ai_harmonization_service = AIHarmonizationService()
