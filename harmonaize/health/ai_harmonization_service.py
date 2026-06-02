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
        api_key = (settings.OPENAI_API_KEY or "").strip()
        if not api_key:
            msg = "OPENAI_API_KEY must be set in settings"
            raise ValueError(msg)

        self.client = OpenAI(api_key=api_key)
        self.model = getattr(settings, "OPENAI_TRANSFORMATION_MODEL", "gpt-5.4-mini")

    def refresh_run(self, run: HarmonizationAIRun) -> dict[str, Any]:
        """Run an AI refresh for either a schema batch or a selected attribute set."""
        schema = run.schema
        logger.info(
            "AI harmonization run started run_id=%s schema_id=%s source_study_id=%s target_study_id=%s trigger=%s threshold=%s candidates_per_variable=%s",
            run.id,
            schema.id,
            schema.source_study_id,
            schema.target_study_id,
            run.trigger_mode,
            run.run_below_confidence_grade,
            run.top_candidates_per_variable,
        )
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
            logger.info(
                "AI harmonization run has no eligible variables run_id=%s schema_id=%s requested_ids=%s threshold=%s",
                run.id,
                schema.id,
                sorted(requested_ids),
                run.run_below_confidence_grade,
            )
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
        bootstrap_response = self._bootstrap_run_context(
            run=run,
            vector_store_id=vector_store_id,
            fallback_file_ids=uploaded_file_ids[:4],
        )
        bootstrap_response_id = getattr(bootstrap_response, "id", None)
        if bootstrap_response_id:
            run.bootstrap_response_id = bootstrap_response_id
            run.openai_response_ids = [bootstrap_response_id]
            run.save(update_fields=["bootstrap_response_id", "openai_response_ids", "updated_at"])
        logger.info(
            "AI harmonization run prepared context run_id=%s schema_id=%s eligible_count=%s vector_store_id=%s uploaded_file_count=%s bootstrap_response_id=%s variables=%s",
            run.id,
            schema.id,
            len(eligible_attributes),
            vector_store_id or "",
            len(uploaded_file_ids),
            bootstrap_response_id or "",
            self._format_attribute_list_for_log(eligible_attributes),
        )
        processed_count = 0
        failed_count = 0
        warning_messages: list[str] = []
        response_ids: list[str] = []
        if bootstrap_response_id:
            response_ids.append(bootstrap_response_id)
        usage_summary: dict[str, int] = {}
        if bootstrap_response is not None:
            self._merge_usage_summary(usage_summary, self._extract_usage_summary(bootstrap_response))
        returned_source_ids: set[int] = set()

        for variable_number, attribute in enumerate(eligible_attributes, start=1):
            logger.info(
                "AI harmonization variable analysis started run_id=%s schema_id=%s variable_number=%s variable=%s",
                run.id,
                schema.id,
                variable_number,
                self._format_attribute_for_log(attribute),
            )
            response = self._request_structured_refresh_for_attribute(
                run=run,
                attribute=attribute,
                baseline=baseline,
                vector_store_id=vector_store_id,
                fallback_file_ids=uploaded_file_ids[:4],
                previous_response_id=bootstrap_response_id,
            )

            payload = self._extract_structured_payload(response)
            self._log_response_payload(
                label=f"harmonization_refresh_response_variable_{attribute.id}",
                response=response,
                payload=payload,
            )
            result = self._extract_single_result(payload)
            results = [result] if result else []
            logger.info(
                "AI harmonization variable response parsed run_id=%s schema_id=%s variable_number=%s response_id=%s status=%s result_count=%s usage=%s",
                run.id,
                schema.id,
                variable_number,
                getattr(response, "id", None) or "",
                getattr(response, "status", None) or "",
                len(results),
                self._extract_usage_summary(response),
            )
            returned_source_ids.update(
                int(item.get("source_attribute_id"))
                for item in results
                if isinstance(item, Mapping) and item.get("source_attribute_id") is not None
            )

            for item in results:
                logger.info(
                    "AI harmonization result received run_id=%s schema_id=%s variable_number=%s %s",
                    run.id,
                    schema.id,
                    variable_number,
                    self._format_result_for_log(run=run, result=item),
                )
                try:
                    warning_message = self._apply_result_to_mapping_rule(run=run, result=item)
                    if warning_message:
                        failed_count += 1
                        warning_messages.append(warning_message)
                    else:
                        processed_count += 1
                except Exception:
                    failed_count += 1
                    warning_messages.append(
                        self._build_item_failure_message(item, "Failed applying AI harmonization result.")
                    )
                    logger.exception(
                        "Failed applying AI harmonization result run_id=%s schema_id=%s variable_number=%s %s",
                        run.id,
                        schema.id,
                        variable_number,
                        self._format_result_for_log(run=run, result=item),
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

            self._merge_usage_summary(usage_summary, self._extract_usage_summary(response))
            response_id = getattr(response, "id", None)
            if response_id:
                response_ids.append(response_id)

            run.usage_summary = usage_summary
            run.openai_response_ids = response_ids
            run.save(
                update_fields=[
                    "usage_summary",
                    "openai_response_ids",
                    "updated_at",
                ],
            )
            logger.info(
                "AI harmonization variable analysis completed run_id=%s schema_id=%s variable_number=%s processed_total=%s failed_total=%s response_ids=%s usage_total=%s",
                run.id,
                schema.id,
                variable_number,
                processed_count,
                failed_count,
                response_ids,
                usage_summary,
            )

        missing_ids = {attribute.id for attribute in eligible_attributes} - returned_source_ids
        failed_count += len(missing_ids)
        if missing_ids:
            missing_attributes = [attribute for attribute in eligible_attributes if attribute.id in missing_ids]
            logger.warning(
                "AI harmonization missing results run_id=%s schema_id=%s missing_count=%s variables=%s",
                run.id,
                schema.id,
                len(missing_ids),
                self._format_attribute_list_for_log(missing_attributes),
            )
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

        logger.info(
            "AI harmonization run completed run_id=%s schema_id=%s processed=%s failed=%s responses=%s usage=%s",
            run.id,
            schema.id,
            processed_count,
            failed_count,
            response_ids,
            usage_summary,
        )
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

    def _get_batch_size(self, run: HarmonizationAIRun) -> int:
        configured_size = getattr(settings, "OPENAI_HARMONIZATION_BATCH_SIZE", 20)
        try:
            batch_size = int(configured_size)
        except (TypeError, ValueError):
            batch_size = 20
        return max(1, batch_size)

    def _chunks(self, values: list[Attribute], size: int) -> Iterable[list[Attribute]]:
        for index in range(0, len(values), size):
            yield values[index : index + size]

    def _merge_usage_summary(self, target: dict[str, int], source: Mapping[str, Any]) -> None:
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            value = source.get(key)
            if isinstance(value, int):
                target[key] = target.get(key, 0) + value

    def _extract_single_result(self, payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
        result = payload.get("result")
        if isinstance(result, Mapping):
            return result
        results = payload.get("results")
        if isinstance(results, list) and results and isinstance(results[0], Mapping):
            return results[0]
        return None

    def _format_attribute_for_log(self, attribute: Attribute | None) -> str:
        if attribute is None:
            return "<none>"
        return (
            f"id={attribute.id} name={attribute.variable_name!r} "
            f"display={attribute.display_name or ''!r} type={attribute.variable_type or ''!r}"
        )

    def _format_attribute_list_for_log(self, attributes: Iterable[Attribute]) -> list[str]:
        return [self._format_attribute_for_log(attribute) for attribute in attributes]

    def _format_result_for_log(self, *, run: HarmonizationAIRun, result: Any) -> str:
        if not isinstance(result, Mapping):
            return f"result_type={type(result).__name__} result={self._truncate_for_log(result)}"

        source_attribute = self._get_source_attribute(run, result.get("source_attribute_id"))
        target_id = result.get("recommended_target_attribute_id")
        target_attribute = None
        if target_id:
            target_attribute = run.schema.target_study.variables.filter(pk=target_id).first()

        mapping_payload = result.get("mapping_rule", {}) or {}
        if not isinstance(mapping_payload, Mapping):
            mapping_payload = {}

        reasoning = self._truncate_text(result.get("reasoning_summary") or "", 500)
        return (
            f"source=({self._format_attribute_for_log(source_attribute)}) "
            f"decision={result.get('decision') or ''!r} "
            f"target_source={result.get('target_source') or ''!r} "
            f"duplicate_conflict={result.get('duplicate_conflict')} "
            f"target=({self._format_attribute_for_log(target_attribute)}) "
            f"confidence={result.get('confidence_label') or ''!r} "
            f"role={mapping_payload.get('role') or ''!r} "
            f"not_mappable={mapping_payload.get('not_mappable')} "
            f"relation={mapping_payload.get('relation_type') or ''!r} "
            f"relation_order={mapping_payload.get('relation_instance_order')} "
            f"custom_patient_id={mapping_payload.get('uses_custom_patient_id')} "
            f"custom_datetime={mapping_payload.get('uses_custom_datetime')} "
            f"custom_location={mapping_payload.get('uses_custom_location')} "
            f"custom_relation={mapping_payload.get('uses_custom_relation')} "
            f"transform_chars={len((mapping_payload.get('transform_code') or '').strip())} "
            f"reasoning={reasoning!r}"
        )

    def _truncate_text(self, value: Any, limit: int) -> str:
        text = str(value or "")
        if len(text) <= limit:
            return text
        return f"{text[:limit]}... [truncated {len(text) - limit} chars]"

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

        context_file_ids = self._upload_run_context_files(run)
        uploaded_file_ids.extend(context_file_ids)
        run.context_file_ids = context_file_ids
        run.save(update_fields=["context_file_ids", "updated_at"])

        vector_store_id = None
        if uploaded_file_ids:
            try:
                vector_store_id = self._create_vector_store(uploaded_file_ids, run)
                if vector_store_id:
                    run.openai_vector_store_id = vector_store_id
                    run.save(update_fields=["openai_vector_store_id", "updated_at"])
            except Exception:
                logger.exception("Failed creating vector store for AI harmonization run %s", run.id)

        return vector_store_id, uploaded_file_ids

    def _upload_run_context_files(self, run: HarmonizationAIRun) -> list[str]:
        context_documents = {
            "target_variable_catalog.json": self._target_variable_catalog(run),
            "existing_mapping_rules.json": self._existing_mapping_rules_context(run),
            "mapping_policy.json": self._mapping_policy_context(run),
        }
        uploaded_ids: list[str] = []
        for filename, payload in context_documents.items():
            try:
                uploaded_id = self._upload_json_context_file(filename, payload)
            except Exception:
                logger.exception("Failed uploading AI harmonization context file %s for run %s", filename, run.id)
                continue
            if uploaded_id:
                uploaded_ids.append(uploaded_id)
        return uploaded_ids

    def _upload_json_context_file(self, filename: str, payload: Mapping[str, Any]) -> str | None:
        content = json.dumps(payload, ensure_ascii=True, indent=2).encode("utf-8")
        uploaded = self.client.files.create(
            file=(filename, content),
            purpose="user_data",
        )
        return getattr(uploaded, "id", None)

    def _attribute_payload(self, attribute: Attribute | None) -> dict[str, Any] | None:
        if attribute is None:
            return None
        return {
            "attribute_id": attribute.id,
            "variable_name": attribute.variable_name,
            "display_name": attribute.display_name or "",
            "description": attribute.description or "",
            "variable_type": attribute.variable_type or "",
            "unit": attribute.unit or "",
            "category": attribute.category or "",
            "ontology_code": attribute.ontology_code or "",
            "source_type": attribute.source_type or "",
        }

    def _target_variable_catalog(self, run: HarmonizationAIRun) -> dict[str, Any]:
        return {
            "schema_id": run.schema_id,
            "target_study": {
                "id": run.schema.target_study_id,
                "name": run.schema.target_study.name,
            },
            "target_variables": [
                self._attribute_payload(attribute)
                for attribute in run.schema.target_study.variables.order_by("variable_name")
            ],
        }

    def _existing_mapping_rules_context(self, run: HarmonizationAIRun) -> dict[str, Any]:
        rules = (
            MappingRule.objects.filter(schema=run.schema)
            .select_related(
                "source_attribute",
                "target_attribute",
                "patient_id_attribute",
                "datetime_attribute",
                "location_attribute",
                "ai_last_run",
            )
            .order_by("source_attribute__variable_name")
        )
        return {
            "schema_id": run.schema_id,
            "mappings": [
                {
                    "rule_id": rule.id,
                    "source_attribute": self._attribute_payload(rule.source_attribute),
                    "target_attribute": self._attribute_payload(rule.target_attribute),
                    "not_mappable": rule.not_mappable,
                    "role": rule.role,
                    "patient_id_attribute": self._attribute_payload(rule.patient_id_attribute),
                    "datetime_attribute": self._attribute_payload(rule.datetime_attribute),
                    "location_attribute": self._attribute_payload(rule.location_attribute),
                    "relation_type": rule.relation_type,
                    "relation_name": rule.relation_name,
                    "inverse_relation_type": rule.inverse_relation_type,
                    "inverse_relation_name": rule.inverse_relation_name,
                    "has_transform_code": bool(rule.transform_code),
                    "comments": rule.comments,
                    "ai_confidence_label": rule.ai_confidence_label,
                    "ai_reasoning_summary": rule.ai_reasoning_summary,
                    "ai_last_run_id": rule.ai_last_run_id,
                }
                for rule in rules
            ],
        }

    def _mapping_policy_context(self, run: HarmonizationAIRun) -> dict[str, Any]:
        return {
            "schema_id": run.schema_id,
            "universal_defaults": self._schema_defaults_payload(run),
            "relation_policy": {
                "allowed_relation_types": [choice[0] for choice in run.schema.RELATION_CHOICES],
                "repeatable_relation_types": sorted(REPEATABLE_RELATION_TYPES),
                "repeatable_names": "Use relation_instance_order only; the application constructs names as <relation_type>_<order>.",
                "non_repeatable_names": "For mother, father, parent, and spouse, leave relation_instance_order null.",
                "existing_instances": [
                    {"value": value, "label": label}
                    for value, label in relation_instance_choices(run.schema)
                    if value
                ],
            },
            "duplicate_policy": {
                "mode": "soft_avoid",
                "occupied_target_attribute_ids": self._occupied_target_attribute_ids(run),
                "instruction": "Avoid exact duplicate value mappings. If the best target is already occupied by another value rule, return duplicate_conflict=true and decision='needs_review'.",
            },
        }

    def _schema_defaults_payload(self, run: HarmonizationAIRun) -> dict[str, Any]:
        return {
            "auto_populate_enabled": run.schema.auto_populate_enabled,
            "universal_patient_id_attribute": self._attribute_payload(run.schema.universal_patient_id),
            "universal_datetime_attribute": self._attribute_payload(run.schema.universal_datetime),
            "universal_location_attribute": self._attribute_payload(run.schema.universal_location),
            "universal_relation_type": run.schema.universal_relation_type or "self",
        }

    def _occupied_target_attribute_ids(self, run: HarmonizationAIRun, *, exclude_source_attribute_id: int | None = None) -> list[int]:
        rules = MappingRule.objects.filter(
            schema=run.schema,
            not_mappable=False,
            target_attribute__isnull=False,
            role="value",
        )
        if exclude_source_attribute_id:
            rules = rules.exclude(source_attribute_id=exclude_source_attribute_id)
        return list(rules.values_list("target_attribute_id", flat=True).distinct())

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

        create_kwargs = {
            "name": f"harmonization-schema-{run.schema_id}-run-{run.id}",
            "metadata": {
                "schema_id": str(run.schema_id),
                "run_id": str(run.id),
                "purpose": "health_harmonization",
            },
            "expires_after": {
                "anchor": "last_active_at",
                "days": getattr(settings, "OPENAI_HARMONIZATION_VECTOR_STORE_TTL_DAYS", 7),
            },
        }
        try:
            store = vector_stores.create(**create_kwargs)
        except TypeError:
            create_kwargs.pop("expires_after", None)
            store = vector_stores.create(**create_kwargs)
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

    def _bootstrap_system_prompt(self) -> str:
        return (
            "You are preparing a reusable health-data harmonization context for later one-variable mapping calls. "
            "Read the attached codebooks, target variable catalog, existing mapping rules, relation policy, and duplicate policy. "
            "Summarize the target catalog structure, repeated naming patterns, common source-to-target mapping patterns, "
            "date/patient/location conventions, relation conventions, and occupied targets. "
            "Do not output row-level data or request identifiable values. This response will be referenced by later calls."
        )

    def _build_bootstrap_prompt(self, run: HarmonizationAIRun) -> str:
        return json.dumps(
            {
                "task": "bootstrap_harmonization_context",
                "run_id": run.id,
                "schema_id": run.schema_id,
                "source_study": {
                    "id": run.schema.source_study_id,
                    "name": run.schema.source_study.name,
                },
                "target_study": {
                    "id": run.schema.target_study_id,
                    "name": run.schema.target_study.name,
                },
                "instructions": [
                    "Use the vector store files as the durable context for subsequent variable-level calls.",
                    "Pay special attention to existing_mapping_rules.json because it contains human corrections and patterns.",
                    "Pay special attention to mapping_policy.json because it defines duplicate handling and custom patient/date/location/relation defaults.",
                    "Later calls will provide one source variable and top embedding candidates; use this bootstrap context when candidates are weak or missing.",
                ],
            },
            indent=2,
        )

    def _bootstrap_run_context(
        self,
        *,
        run: HarmonizationAIRun,
        vector_store_id: str | None,
        fallback_file_ids: list[str],
    ):
        tools = self._file_search_tools(vector_store_id)
        user_content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": self._build_bootstrap_prompt(run),
            },
        ]
        if not vector_store_id:
            for file_id in fallback_file_ids:
                user_content.append({"type": "input_file", "file_id": file_id})

        request_kwargs = {
            "model": self.model,
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": self._bootstrap_system_prompt()}],
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
            "tools": tools,
            "store": True,
        }
        if tools:
            request_kwargs["include"] = ["file_search_call.results"]

        logger.info(
            "AI harmonization bootstrap request run_id=%s schema_id=%s vector_store_id=%s context_file_ids=%s",
            run.id,
            run.schema_id,
            vector_store_id or "",
            run.context_file_ids,
        )
        try:
            response = self.client.responses.create(**request_kwargs)
        except (BadRequestError, APIError, RateLimitError) as exc:
            message = self._format_openai_exception(exc)
            logger.exception("OpenAI harmonization bootstrap request failed: %s", message)
            raise RuntimeError(message) from exc

        self._raise_for_failed_response(response)
        logger.info(
            "AI harmonization bootstrap response run_id=%s schema_id=%s response_id=%s status=%s usage=%s",
            run.id,
            run.schema_id,
            getattr(response, "id", None) or "",
            getattr(response, "status", None) or "",
            self._extract_usage_summary(response),
        )
        return response

    def _file_search_tools(self, vector_store_id: str | None) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        if vector_store_id:
            tools.append(
                {
                    "type": "file_search",
                    "vector_store_ids": [vector_store_id],
                    "max_num_results": 6,
                }
            )
        return tools

    def _request_structured_refresh_for_attribute(
        self,
        *,
        run: HarmonizationAIRun,
        attribute: Attribute,
        baseline: dict[int, list[dict[str, Any]]],
        vector_store_id: str | None,
        fallback_file_ids: list[str],
        previous_response_id: str | None,
    ):
        user_content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": self._build_user_prompt(run=run, attribute=attribute, baseline=baseline),
            },
        ]

        tools = self._file_search_tools(vector_store_id)
        if not vector_store_id:
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
            "store": True,
        }
        if previous_response_id:
            request_kwargs["previous_response_id"] = previous_response_id
        if tools:
            request_kwargs["include"] = ["file_search_call.results"]

        self._log_request_payload(
            label="harmonization_variable_refresh_request",
            payload={
                "run_id": run.id,
                "schema_id": run.schema_id,
                "model": self.model,
                "previous_response_id": previous_response_id or "",
                "attribute": self._attribute_payload(attribute),
                "tools": tools,
                "user_prompt": user_content[0]["text"],
            },
        )

        try:
            response = self.client.responses.create(**request_kwargs)
        except (BadRequestError, APIError, RateLimitError) as exc:
            message = self._format_openai_exception(exc)
            logger.exception("OpenAI harmonization refresh request failed: %s", message)
            raise RuntimeError(message) from exc

        logger.info(
            "OpenAI harmonization variable response created run_id=%s schema_id=%s response_id=%s status=%s source_attribute_id=%s",
            run.id,
            run.schema_id,
            getattr(response, "id", None) or "",
            getattr(response, "status", None) or "",
            attribute.id,
        )

        self._raise_for_failed_response(response)
        return response

    def _format_openai_exception(self, exc: Exception) -> str:
        body = getattr(exc, "body", None)
        if isinstance(body, Mapping):
            error = body.get("error")
            if isinstance(error, Mapping):
                message = error.get("message")
                code = error.get("code")
                if message and code:
                    return f"OpenAI API error ({code}): {message}"
                if message:
                    return f"OpenAI API error: {message}"

        response = getattr(exc, "response", None)
        if response is not None:
            try:
                data = response.json()
            except Exception:
                data = None
            if isinstance(data, Mapping):
                error = data.get("error")
                if isinstance(error, Mapping) and error.get("message"):
                    return f"OpenAI API error: {error['message']}"

        message = str(exc).strip()
        return f"OpenAI API error: {message}" if message else "OpenAI API request failed."

    def _raise_for_failed_response(self, response) -> None:
        status = getattr(response, "status", None)
        if status not in {"failed", "cancelled", "incomplete"}:
            return

        error = getattr(response, "error", None) or getattr(response, "incomplete_details", None)
        if isinstance(error, Mapping):
            message = error.get("message") or error.get("reason") or str(error)
        else:
            message = getattr(error, "message", None) or getattr(error, "reason", None) or str(error or "")
        raise RuntimeError(
            f"OpenAI response {getattr(response, 'id', '') or '<unknown>'} ended with status {status}: "
            f"{message or 'No error details returned.'}"
        )

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
        result_count = ""
        if isinstance(payload, Mapping) and isinstance(payload.get("results"), list):
            result_count = len(payload["results"])
        elif isinstance(payload, Mapping) and isinstance(payload.get("result"), Mapping):
            result_count = 1
        logger.info(
            "%s: response_id=%s status=%s result_count=%s usage=%s payload=%s",
            label,
            getattr(response, "id", None),
            getattr(response, "status", None),
            result_count,
            self._extract_usage_summary(response),
            self._truncate_for_log(payload),
        )

    def _poll_background_response(self, response_id: str):
        deadline = time.monotonic() + 600
        started_at = time.monotonic()
        try:
            response = self.client.responses.retrieve(response_id)
        except APIError as exc:
            message = self._format_openai_exception(exc)
            logger.exception("OpenAI background response polling failed: %s", message)
            raise RuntimeError(message) from exc
        logger.info(
            "OpenAI background response poll started response_id=%s status=%s",
            response_id,
            getattr(response, "status", None) or "",
        )
        while getattr(response, "status", None) in {"queued", "in_progress"}:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for OpenAI background response {response_id}")
            time.sleep(2)
            try:
                response = self.client.responses.retrieve(response_id)
            except APIError as exc:
                message = self._format_openai_exception(exc)
                logger.exception("OpenAI background response polling failed: %s", message)
                raise RuntimeError(message) from exc
            elapsed = int(time.monotonic() - started_at)
            if elapsed % 30 < 2:
                logger.info(
                    "OpenAI background response still running response_id=%s status=%s elapsed_seconds=%s",
                    response_id,
                    getattr(response, "status", None) or "",
                    elapsed,
                )
        logger.info(
            "OpenAI background response completed response_id=%s status=%s elapsed_seconds=%s usage=%s",
            response_id,
            getattr(response, "status", None) or "",
            int(time.monotonic() - started_at),
            self._extract_usage_summary(response),
        )
        self._raise_for_failed_response(response)
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
        rule, created = MappingRule.objects.get_or_create(
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

        transform_warning = None
        duplicate_conflict = False
        if target_id and target_attribute is None:
            transform_warning = (
                f"{rule.source_attribute.variable_name}: AI recommended target attribute {target_id}, "
                "but it is not part of the target study."
            )
        elif (
            target_attribute is not None
            and role == "value"
            and not bool(mapping_payload.get("not_mappable", decision == "not_mappable"))
            and self._target_is_occupied_by_another_rule(run, target_attribute.id, rule.source_attribute_id)
        ):
            duplicate_conflict = True
            transform_warning = (
                f"{rule.source_attribute.variable_name}: AI recommended {target_attribute.variable_name}, "
                "but that target is already mapped by another source variable. Existing mapping was preserved."
            )

        if not duplicate_conflict:
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
        if result.get("target_source") or result.get("duplicate_conflict") is not None:
            metadata_summary = (
                f"target_source={result.get('target_source') or 'none'}; "
                f"duplicate_conflict={bool(result.get('duplicate_conflict') or duplicate_conflict)}"
            )
            rule.ai_reasoning_summary = " ".join(
                part for part in [rule.ai_reasoning_summary, metadata_summary] if part
            ).strip()
        if transform_warning:
            rule.ai_reasoning_summary = " ".join(
                part for part in [rule.ai_reasoning_summary, transform_warning] if part
            ).strip()

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
        logger.info(
            "AI harmonization mapping rule saved run_id=%s schema_id=%s rule_id=%s created=%s source=(%s) target=(%s) not_mappable=%s role=%s relation=%s relation_name=%s confidence=%s transform_chars=%s",
            run.id,
            run.schema_id,
            rule.id,
            created,
            self._format_attribute_for_log(rule.source_attribute),
            self._format_attribute_for_log(rule.target_attribute),
            rule.not_mappable,
            rule.role,
            rule.relation_type,
            rule.relation_name,
            rule.ai_confidence_label,
            len(rule.transform_code or ""),
        )
        return transform_warning

    def _target_is_occupied_by_another_rule(
        self,
        run: HarmonizationAIRun,
        target_attribute_id: int,
        source_attribute_id: int,
    ) -> bool:
        return MappingRule.objects.filter(
            schema=run.schema,
            target_attribute_id=target_attribute_id,
            not_mappable=False,
            role="value",
        ).exclude(source_attribute_id=source_attribute_id).exists()

    def _get_source_attribute(self, run: HarmonizationAIRun, attribute_id: Any) -> Attribute | None:
        if not attribute_id:
            return None
        return run.schema.source_study.variables.filter(pk=attribute_id).first()

    def _build_user_prompt(
        self,
        *,
        run: HarmonizationAIRun,
        attribute: Attribute,
        baseline: dict[int, list[dict[str, Any]]],
    ) -> str:
        summary_stats_context = self._build_summary_stats_context(run=run, attributes=[attribute])
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

        current_rule = MappingRule.objects.filter(schema=run.schema, source_attribute=attribute).first()

        return json.dumps(
            {
                "task": "analyze_one_source_variable",
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
                    "candidate_policy": "Prefer candidate_targets. If none fit, use the target catalog and retrieved codebook context from the bootstrap/vector store.",
                    "duplicate_policy": "Avoid exact duplicate value mappings. If a recommended value target is already occupied by another source variable, set duplicate_conflict=true and decision='needs_review'.",
                },
                "schema_defaults": self._schema_defaults_payload(run),
                "reserved_target_attribute_ids": self._occupied_target_attribute_ids(
                    run,
                    exclude_source_attribute_id=attribute.id,
                ),
                "source_variable": {
                    **(self._attribute_payload(attribute) or {}),
                    "deidentified_summary_stats": summary_stats_by_variable.get(attribute.variable_name, {}),
                    "candidate_targets": candidates,
                },
                "current_mapping_rule": self._mapping_rule_payload(current_rule),
                "required_output": "Return exactly one complete result object. Include every mapping_rule setting even when it is null, false, self, or empty.",
            },
            indent=2,
        )

    def _mapping_rule_payload(self, rule: MappingRule | None) -> dict[str, Any] | None:
        if rule is None:
            return None
        return {
            "rule_id": rule.id,
            "target_attribute": self._attribute_payload(rule.target_attribute),
            "not_mappable": rule.not_mappable,
            "role": rule.role,
            "patient_id_attribute": self._attribute_payload(rule.patient_id_attribute),
            "datetime_attribute": self._attribute_payload(rule.datetime_attribute),
            "location_attribute": self._attribute_payload(rule.location_attribute),
            "relation_type": rule.relation_type,
            "relation_name": rule.relation_name,
            "has_transform_code": bool(rule.transform_code),
            "comments": rule.comments,
            "ai_confidence_label": rule.ai_confidence_label,
            "ai_reasoning_summary": rule.ai_reasoning_summary,
        }

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
            "candidate target attributes, prior bootstrap response, retrieved study documentation, target variable catalog, existing mapping rules, "
            "and any explicitly provided de-identified summary statistics to refresh exactly one mapping rule. "
            "Only use summary statistics when they are supplied in the prompt. Never infer or request row-level data, example records, or potentially identifiable values. "
            "Prefer target attributes from candidate_targets. If none are appropriate, use the target_variable_catalog and retrieved codebook context and set target_source accordingly. "
            "Avoid exact duplicate value mappings. If a value target is already reserved by another source variable, set duplicate_conflict=true and decision='needs_review'. "
            "Return all mapping settings for the source variable, including patient ID, datetime, location, relation, transform, and comments. "
            "Use relation_type='self' unless the source variable clearly describes another entity, such as a child, parent, spouse, sibling, mother, or father. "
            "If patient/date/location/relation settings differ from the schema defaults supplied in the prompt, set the corresponding uses_custom_* boolean to true and provide the explicit attribute ID or relation setting. "
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
        result_schema = {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "source_attribute_id",
                "decision",
                "recommended_target_attribute_id",
                "target_source",
                "duplicate_conflict",
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
                "target_source": {
                    "type": "string",
                    "enum": ["embedding_candidate", "catalog_fallback", "retrieved_codebook", "none"],
                },
                "duplicate_conflict": {"type": "boolean"},
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
                        "uses_custom_patient_id",
                        "uses_custom_datetime",
                        "uses_custom_location",
                        "uses_custom_relation",
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
                        "uses_custom_patient_id": {"type": "boolean"},
                        "uses_custom_datetime": {"type": "boolean"},
                        "uses_custom_location": {"type": "boolean"},
                        "uses_custom_relation": {"type": "boolean"},
                    },
                },
            },
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["result"],
            "properties": {
                "result": result_schema,
            },
        }


class LazyAIHarmonizationService:
    """Instantiate the OpenAI-backed service only when a run actually needs it."""

    _service: AIHarmonizationService | None = None

    def _get_service(self) -> AIHarmonizationService:
        if self._service is None:
            self._service = AIHarmonizationService()
        return self._service

    def refresh_run(self, run: HarmonizationAIRun) -> dict[str, Any]:
        return self._get_service().refresh_run(run)


ai_harmonization_service = LazyAIHarmonizationService()
