from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from core.models import Study

from .models import RawDataFile


def build_deidentified_summary_stats_context(
    *,
    source_study: Study,
    variable_names: Iterable[str],
) -> dict[str, Any]:
    raw_data_file = get_latest_cached_summary_file(source_study)
    if raw_data_file is None:
        return {}

    cache = raw_data_file.eda_cache_source or {}
    if not isinstance(cache, Mapping) or not cache.get("available"):
        return {}

    variable_summaries = extract_variable_summary_stats(
        cache=cache,
        requested_names={name for name in variable_names if name},
    )
    if not variable_summaries:
        return {}

    summary = cache.get("summary") if isinstance(cache.get("summary"), Mapping) else {}
    return {
        "source_raw_data_file": {
            "id": raw_data_file.id,
            "original_filename": raw_data_file.original_filename,
            "generated_at": raw_data_file.eda_cache_source_generated_at.isoformat()
            if raw_data_file.eda_cache_source_generated_at
            else None,
        },
        "dataset_summary": {
            "row_count": summary.get("row_count"),
            "column_count": summary.get("column_count"),
            "sampled_rows": summary.get("sampled_rows"),
            "missing_values": summary.get("missing_values"),
            "privacy_threshold": summary.get("privacy_threshold"),
            "included_columns": summary.get("included_columns"),
            "excluded_columns": summary.get("excluded_columns"),
            "numeric_columns": summary.get("numeric_columns"),
            "categorical_columns": summary.get("categorical_columns"),
            "text_columns": summary.get("text_columns"),
        },
        "variables": variable_summaries,
    }


def get_latest_cached_summary_file(source_study: Study) -> RawDataFile | None:
    return (
        RawDataFile.objects.filter(
            study=source_study,
            eda_cache_source__isnull=False,
        )
        .order_by("-eda_cache_source_generated_at", "-uploaded_at")
        .first()
    )


def extract_variable_summary_stats(
    *,
    cache: Mapping[str, Any],
    requested_names: set[str],
) -> dict[str, Any]:
    requested_lookup = {normalize_column_name(name): name for name in requested_names}
    result: dict[str, Any] = {}

    for column in cache.get("numeric_columns", []) or []:
        if not isinstance(column, Mapping):
            continue
        target_name = requested_lookup.get(normalize_column_name(column.get("name")))
        if not target_name:
            continue
        result[target_name] = {
            "column_type": "numeric",
            "count": column.get("count"),
            "missing": column.get("missing"),
            "mean": column.get("mean"),
            "median": column.get("median"),
            "std": column.get("std"),
            "variance": column.get("variance"),
            "skewness": column.get("skewness"),
            "min": column.get("min"),
            "max": column.get("max"),
            "p10": column.get("p10"),
            "p90": column.get("p90"),
        }

    for column in cache.get("categorical_columns", []) or []:
        if not isinstance(column, Mapping):
            continue
        target_name = requested_lookup.get(normalize_column_name(column.get("name")))
        if not target_name:
            continue
        result[target_name] = {
            "column_type": "categorical",
            "unique": column.get("unique"),
            "missing": column.get("missing"),
            "other_count": column.get("other_count"),
            "top_value_count": len(column.get("top_values", []) or []),
        }

    for column in cache.get("string_columns", []) or []:
        if not isinstance(column, Mapping):
            continue
        target_name = requested_lookup.get(normalize_column_name(column.get("name")))
        if not target_name:
            continue
        result[target_name] = {
            "column_type": "text",
            "unique_count": column.get("unique_count"),
            "high_cardinality": bool(column.get("high_cardinality")),
            "token_count": len(column.get("tokens", []) or []),
        }

    return result


def normalize_column_name(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()