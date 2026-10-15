from __future__ import annotations

import logging
from typing import Any

from django.core.cache import cache
from django.db.models import Max
from django.utils import timezone

from core.models import Attribute

from .models import MappingSchema

logger = logging.getLogger(__name__)

CACHE_TIMEOUT_SECONDS = 60 * 60 * 6
VERSION = "v2"


def mapping_similarity_cache_key(schema: MappingSchema) -> str:
    source_freshness = _study_variable_freshness(schema.source_study_id)
    target_freshness = _study_variable_freshness(schema.target_study_id)
    return (
        f"mapping_similarity:{VERSION}:schema:{schema.id}:"
        f"source:{source_freshness}:target:{target_freshness}"
    )


def mapping_variable_similarity_cache_key(schema: MappingSchema, attribute_id: int) -> str:
    return f"{mapping_similarity_cache_key(schema)}:source:{attribute_id}"


def get_cached_similarity_payload(schema: MappingSchema) -> dict[str, Any] | None:
    payload = cache.get(mapping_similarity_cache_key(schema))
    return payload if isinstance(payload, dict) else None


def get_similarity_payload(schema: MappingSchema, *, refresh: bool = False) -> dict[str, Any]:
    if not refresh:
        payload = get_cached_similarity_payload(schema)
        if payload:
            return payload
    return compute_similarity_payload(schema.id, refresh=refresh)


def cache_similarity_payload(
    schema: MappingSchema,
    suggestions: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    payload = {
        "schema_id": schema.id,
        "source_study_name": schema.source_study.name,
        "target_study_name": schema.target_study.name,
        "generated_at": timezone.now().isoformat(),
        "suggestions": suggestions,
    }
    cache.set(mapping_similarity_cache_key(schema), payload, CACHE_TIMEOUT_SECONDS)
    return payload


def get_variable_similarity_suggestions(
    schema: MappingSchema,
    source_attribute: Attribute,
    *,
    limit: int = 5,
    refresh: bool = False,
) -> list[dict[str, Any]]:
    whole_payload = get_cached_similarity_payload(schema)
    whole_suggestions = (whole_payload or {}).get("suggestions") or {}
    if not refresh and str(source_attribute.id) in whole_suggestions:
        return whole_suggestions[str(source_attribute.id)]

    key = mapping_variable_similarity_cache_key(schema, source_attribute.id)
    if not refresh:
        cached = cache.get(key)
        if isinstance(cached, list):
            return cached

    from core.similarity_service import similarity_service

    target_attributes = list(
        Attribute.objects.filter(
            studies__id=schema.target_study_id,
            source_type="target",
            name_embedding__isnull=False,
        ).distinct()
    )
    suggestions = _format_similarity_matches(
        similarity_service.find_similar_attributes(
            source_attribute,
            target_attributes,
            limit=limit,
        )
    )
    cache.set(key, suggestions, CACHE_TIMEOUT_SECONDS)
    return suggestions


def compute_similarity_payload(schema_id: int, *, refresh: bool = False) -> dict[str, Any]:
    from core.similarity_service import similarity_service

    schema = MappingSchema.objects.select_related(
        "source_study",
        "target_study",
    ).get(pk=schema_id)

    suggestions = similarity_service.get_mapping_suggestions(
        source_study_id=schema.source_study_id,
        target_study_id=schema.target_study_id,
        limit_per_source=5,
    )
    formatted = {
        str(source_attr_id): _format_similarity_matches(matches)
        for source_attr_id, matches in suggestions.items()
    }
    return cache_similarity_payload(schema, formatted)


def _format_similarity_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "attribute_id": match["attribute_id"],
            "variable_name": match["variable_name"],
            "display_name": match["display_name"],
            "description": match["description"],
            "variable_type": match["variable_type"],
            "unit": match["unit"],
            "combined_similarity": match["combined_similarity"],
            "name_similarity": match["name_similarity"],
            "description_similarity": match["description_similarity"],
            "confidence_grade": match["confidence_grade"],
            "confidence_label": match["confidence_label"],
            "confidence_color": match["confidence_color"],
            "has_description_match": match["has_description_match"],
        }
        for match in matches
    ]


def _study_variable_freshness(study_id: int) -> str:
    freshness = (
        Attribute.objects.filter(studies__id=study_id)
        .aggregate(updated_at=Max("updated_at"))
        .get("updated_at")
    )
    if freshness is None:
        return "empty"
    return str(int(freshness.timestamp()))
