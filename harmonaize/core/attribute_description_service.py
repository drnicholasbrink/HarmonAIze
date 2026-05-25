"""OpenAI-backed RAG service for privacy-safe codebook description generation."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from django.conf import settings
from openai import APIError, BadRequestError, OpenAI, RateLimitError

from core.models import Attribute, StudyDocument

logger = logging.getLogger(__name__)


class AttributeDescriptionRAGService:
    """Generate human-style attribute descriptions from study documents and schema-only metadata."""

    def __init__(self) -> None:
        if not settings.OPENAI_API_KEY:
            msg = "OPENAI_API_KEY must be set in settings"
            raise ValueError(msg)

        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = getattr(settings, "OPENAI_TRANSFORMATION_MODEL", "gpt-5")

    def enrich_study(self, study, *, attribute_ids: list[int] | None = None) -> dict[str, Any]:
        queryset = study.variables.order_by("variable_name")
        if attribute_ids:
            queryset = queryset.filter(id__in=attribute_ids)
        attributes = list(queryset)

        if not attributes:
            return {
                "updated_attribute_ids": [],
                "usage_summary": {},
                "openai_response_ids": [],
                "message": "No attributes were eligible for description enrichment.",
            }

        vector_store_id, uploaded_file_ids = self._prepare_retrieval_context(study)
        response = self._request_structured_descriptions(
            study=study,
            attributes=attributes,
            vector_store_id=vector_store_id,
            fallback_file_ids=uploaded_file_ids[:4],
        )

        payload = self._extract_structured_payload(response)
        results = payload.get("results", []) if isinstance(payload, Mapping) else []

        updated_attribute_ids: list[int] = []
        for item in results:
            if not isinstance(item, Mapping):
                continue
            attribute_id = int(item.get("attribute_id"))
            attribute = next((candidate for candidate in attributes if candidate.id == attribute_id), None)
            if attribute is None:
                continue

            description = (item.get("description") or "").strip()
            if not description:
                continue

            attribute.description = description
            if not attribute.display_name and item.get("display_name"):
                attribute.display_name = str(item.get("display_name")).strip()
                attribute.save(update_fields=["description", "display_name", "updated_at"])
            else:
                attribute.save(update_fields=["description", "updated_at"])
            updated_attribute_ids.append(attribute.id)

        return {
            "updated_attribute_ids": updated_attribute_ids,
            "usage_summary": self._extract_usage_summary(response),
            "openai_response_ids": [value for value in [getattr(response, "id", None)] if value],
            "message": f"Updated {len(updated_attribute_ids)} attribute description(s) using study-document RAG.",
        }

    def _prepare_retrieval_context(self, study) -> tuple[str | None, list[str]]:
        uploaded_file_ids: list[str] = []
        file_fields = []

        if study.codebook:
            file_fields.append(study.codebook)
        if study.protocol_file:
            file_fields.append(study.protocol_file)

        docs = StudyDocument.objects.filter(study=study).order_by("id")
        file_fields.extend(document.file for document in docs if document.file)

        for file_field in file_fields:
            try:
                uploaded = self._upload_file(file_field)
            except Exception:
                logger.exception("Failed uploading file %s for attribute description RAG", getattr(file_field, "name", "unknown"))
                continue
            if uploaded:
                uploaded_file_ids.append(uploaded)

        vector_store_id = None
        if uploaded_file_ids:
            try:
                vector_store_id = self._create_vector_store(uploaded_file_ids, study_id=study.id)
            except Exception:
                logger.exception("Failed creating vector store for study %s attribute description enrichment", study.id)

        return vector_store_id, uploaded_file_ids

    def _upload_file(self, file_field) -> str | None:
        file_path = getattr(file_field, "path", None)
        if file_path:
            with open(file_path, "rb") as upload_stream:
                uploaded = self.client.files.create(file=upload_stream, purpose="user_data")
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

    def _create_vector_store(self, file_ids: list[str], *, study_id: int) -> str | None:
        vector_stores = self._get_vector_stores_client()
        if vector_stores is None:
            return None

        store = vector_stores.create(name=f"study-{study_id}-attribute-description-rag")
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

    def _request_structured_descriptions(
        self,
        *,
        study,
        attributes: list[Attribute],
        vector_store_id: str | None,
        fallback_file_ids: list[str],
    ):
        user_content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": self._build_user_prompt(study=study, attributes=attributes),
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
                    "content": [{"type": "input_text", "text": self._system_prompt()}],
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "attribute_description_enrichment",
                    "strict": True,
                    "schema": self._response_schema(),
                }
            },
            "tools": tools,
        }

        use_background = len(attributes) > 3
        if use_background:
            request_kwargs["background"] = True
            request_kwargs["store"] = True

        try:
            response = self.client.responses.create(**request_kwargs)
        except (BadRequestError, APIError, RateLimitError):
            logger.exception("OpenAI attribute description request failed")
            raise

        if use_background:
            response = self._poll_background_response(response.id)

        return response

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

    def _build_user_prompt(self, *, study, attributes: list[Attribute]) -> str:
        return json.dumps(
            {
                "study": {
                    "id": study.id,
                    "name": study.name,
                    "purpose": getattr(study, "study_purpose", "source"),
                },
                "privacy_constraints": {
                    "row_level_data_shared": False,
                    "allowed_signals": [
                        "variable_name",
                        "display_name",
                        "variable_type",
                        "unit",
                        "category",
                        "study protocol",
                        "study documents",
                        "generated codebook",
                    ],
                    "prohibited_content": [
                        "patient values",
                        "sample rows",
                        "example values",
                        "quoted raw data",
                    ],
                },
                "task": "Write concise codebook descriptions that read like a human-authored data dictionary entry. Do not mention that the description was inferred, generated, estimated, or produced by AI.",
                "attributes": [
                    {
                        "attribute_id": attribute.id,
                        "variable_name": attribute.variable_name,
                        "display_name": attribute.display_name or "",
                        "current_description": attribute.description or "",
                        "variable_type": attribute.variable_type or "",
                        "unit": attribute.unit or "",
                        "category": attribute.category or "",
                    }
                    for attribute in attributes
                ],
            },
            indent=2,
        )

    def _system_prompt(self) -> str:
        return (
            "You are writing study codebook descriptions for health research variables. "
            "Use only the supplied schema-level metadata and retrieved study documentation. "
            "Do not request, reference, or fabricate row-level values, patient examples, or sample records. "
            "Descriptions must read like a human-authored codebook entry, not like system output. "
            "Do not use phrases such as inferred, generated, estimated, likely, or based on examples. "
            "When documentation is limited, write the safest specific description supported by the variable name, data type, unit, and retrieved documents. "
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
                        "required": ["attribute_id", "display_name", "description", "reasoning_summary"],
                        "properties": {
                            "attribute_id": {"type": "integer"},
                            "display_name": {"type": "string"},
                            "description": {"type": "string"},
                            "reasoning_summary": {"type": "string"},
                        },
                    },
                }
            },
        }