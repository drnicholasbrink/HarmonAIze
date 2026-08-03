from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db.models import Exists
from django.db.models import OuterRef
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from climate.models import ClimateDataRequest
from core.models import Attribute, Observation, Study

LAG_UNIT_TO_DAYS = {
    "days": 1,
    "weeks": 7,
    "months": 30,
    "years": 365,
}

LAG_UNIT_SHORT = {
    "days": "d",
    "weeks": "w",
    "months": "m",
    "years": "y",
}

RESERVED_HEALTH_ATTRIBUTE_TERMS = (
    "patient_id",
    "patient identifier",
    "participant_id",
    "participant identifier",
    "subject_id",
    "subject identifier",
    "person_id",
    "person identifier",
    "date",
    "datetime",
    "timestamp",
    "time",
    "location",
    "geolocation",
    "latitude",
    "longitude",
)


def safe_slug(value: str | None, fallback: str) -> str:
    slug = slugify((value or "").replace("_", "-"))
    slug = slug.replace("_", "-")
    return slug or fallback


def reserved_health_attribute_query() -> Q:
    query = Q(variable_type="datetime")
    for term in RESERVED_HEALTH_ATTRIBUTE_TERMS:
        query |= Q(variable_name__icontains=term)
        query |= Q(display_name__icontains=term)
    return query


def observed_target_attribute_queryset(target_study: Study):
    observed_subquery = Observation.objects.filter(attribute_id=OuterRef("pk"))
    return (
        Attribute.objects.filter(
            studies=target_study,
            source_type="target",
        )
        .annotate(has_observations=Exists(observed_subquery))
        .filter(has_observations=True)
        .distinct()
    )


def eligible_health_queryset(attribute_queryset):
    return attribute_queryset.filter(category="health").exclude(reserved_health_attribute_query())


@dataclass(slots=True)
class DatasetSelection:
    attributes: list[Attribute]
    outcome_attributes: list[Attribute]
    confounder_attributes: list[Attribute]
    climate_attributes: list[Attribute]
    location_attributes: list[Attribute]


@dataclass(slots=True)
class DatasetNaming:
    study_slug: str
    outcome_token: str
    lag_token: str
    shape_token: str

    @property
    def structured_slug(self) -> str:
        return "-".join(
            [
                self.study_slug,
                self.outcome_token,
                self.lag_token,
                self.shape_token,
            ],
        )


@dataclass(slots=True)
class DatasetArtifact:
    columns: list[str]
    rows: list[dict[str, Any]]
    naming: DatasetNaming
    lag_unit: str

    @property
    def row_count(self) -> int:
        return len(self.rows)


class TargetStudyDatasetBuilder:
    def __init__(
        self,
        *,
        target_study: Study,
        outcome_attributes: Iterable[Attribute],
        confounder_attributes: Iterable[Attribute] | None = None,
        climate_attributes: Iterable[Attribute] | None = None,
        location_attributes: Iterable[Attribute] | None = None,
        dataset_shape: str = "long",
        source_studies: Iterable[Study] | None = None,
        max_lag_value: int = 0,
        deidentify: bool = False,
        exported_at: datetime | None = None,
    ):
        self.target_study = target_study
        self.dataset_shape = dataset_shape
        self.source_studies = list(source_studies or [])
        self.outcome_attributes = _ordered_unique_attributes(outcome_attributes)
        outcome_ids = {attribute.id for attribute in self.outcome_attributes}
        self.confounder_attributes = [
            attribute
            for attribute in _ordered_unique_attributes(confounder_attributes)
            if attribute.category == "health" and attribute.id not in outcome_ids
        ]
        self.climate_attributes = [
            attribute
            for attribute in _ordered_unique_attributes(climate_attributes)
            if attribute.category == "climate"
        ]
        self.location_attributes = [
            attribute
            for attribute in _ordered_unique_attributes(location_attributes)
            if attribute.category == "geolocation"
        ]
        self.max_lag_value = max(max_lag_value or 0, 0)
        self.deidentify = deidentify
        self.exported_at = exported_at or timezone.now()
        self.exported_at_str = self.exported_at.isoformat()
        self.lag_unit = get_climate_lag_unit_for_study(target_study)
        self.lag_unit_short = LAG_UNIT_SHORT.get(self.lag_unit, "d")
        self.lag_multiplier = LAG_UNIT_TO_DAYS.get(self.lag_unit, 1)

    @property
    def long_columns(self) -> list[str]:
        return [
            "project_id",
            "project_name",
            "target_study_id",
            "target_study_name",
            "patient_id",
            "outcome_variable_name",
            "outcome_display_name",
            "outcome_value",
            "outcome_datetime",
            "outcome_location_name",
            "row_role",
            "linkage_scope",
            "context_variable_name",
            "context_display_name",
            "context_category",
            "context_value",
            "lag_value",
            "lag_unit",
            "outcome_observation_id",
            "context_observation_id",
            "exported_at",
        ]

    @property
    def wide_columns(self) -> list[str]:
        return [
            "project_id",
            "project_name",
            "target_study_id",
            "target_study_name",
            "patient_id",
            "outcome_variable_name",
            "outcome_display_name",
            "outcome_value",
            "outcome_datetime",
            "outcome_location_name",
            *[f"confounder__{attribute.variable_name}" for attribute in self.confounder_attributes],
            *[f"location__{attribute.variable_name}" for attribute in self.location_attributes],
            "climate_lag_unit",
            *[
                f"climate__{attribute.variable_name}_{self.lag_unit_short}-{lag}"
                for attribute in self.climate_attributes
                for lag in range(0, self.max_lag_value + 1)
            ],
            "exported_at",
        ]

    def build_artifact(self) -> DatasetArtifact:
        if self.dataset_shape == "wide":
            rows = list(self.iter_wide_rows())
            columns = self.wide_columns
        else:
            rows = list(self.iter_long_rows())
            columns = self.long_columns
        return DatasetArtifact(
            columns=columns,
            rows=rows,
            naming=build_dataset_naming(
                target_study=self.target_study,
                outcome_attributes=self.outcome_attributes,
                lag_value=self.max_lag_value,
                lag_unit=self.lag_unit,
                dataset_shape=self.dataset_shape,
            ),
            lag_unit=self.lag_unit,
        )

    def iter_long_rows(self) -> Iterator[dict[str, Any]]:
        confounder_cache = self._build_confounder_cache()
        location_cache = self._build_location_cache()
        climate_cache = self._build_climate_cache()

        for observation in self._outcome_queryset().iterator(chunk_size=500):
            base_row, outcome_date = self._build_outcome_base_row(observation)
            yield {
                **base_row,
                "row_role": "outcome",
                "linkage_scope": "outcome_event",
                "context_variable_name": observation.attribute.variable_name,
                "context_display_name": observation.attribute.display_name or "",
                "context_category": observation.attribute.category,
                "context_value": serialize_observation_value(observation),
                "lag_value": "",
                "lag_unit": "",
                "outcome_observation_id": observation.id,
                "context_observation_id": observation.id,
                "exported_at": self.exported_at_str,
            }

            if observation.patient_id:
                for attribute in self.confounder_attributes:
                    cached = confounder_cache.get((observation.patient_id, attribute.id))
                    yield {
                        **base_row,
                        "row_role": "confounder",
                        "linkage_scope": "patient",
                        "context_variable_name": attribute.variable_name,
                        "context_display_name": attribute.display_name or "",
                        "context_category": attribute.category,
                        "context_value": cached["value"] if cached else "",
                        "lag_value": "",
                        "lag_unit": "",
                        "outcome_observation_id": observation.id,
                        "context_observation_id": cached["observation_id"] if cached else "",
                        "exported_at": self.exported_at_str,
                    }

            if observation.location_id and not self.deidentify:
                for attribute in self.location_attributes:
                    cached = location_cache.get((observation.location_id, attribute.id))
                    yield {
                        **base_row,
                        "row_role": "location",
                        "linkage_scope": "location",
                        "context_variable_name": attribute.variable_name,
                        "context_display_name": attribute.display_name or "",
                        "context_category": attribute.category,
                        "context_value": cached["value"] if cached else "",
                        "lag_value": "",
                        "lag_unit": "",
                        "outcome_observation_id": observation.id,
                        "context_observation_id": cached["observation_id"] if cached else "",
                        "exported_at": self.exported_at_str,
                    }

            if observation.location_id and outcome_date is not None:
                for attribute in self.climate_attributes:
                    for lag in range(0, self.max_lag_value + 1):
                        lag_days = lag * self.lag_multiplier
                        cached = climate_cache.get(
                            (observation.location_id, outcome_date - timedelta(days=lag_days), attribute.id),
                        )
                        yield {
                            **base_row,
                            "row_role": "climate_lag",
                            "linkage_scope": "time_location",
                            "context_variable_name": attribute.variable_name,
                            "context_display_name": attribute.display_name or "",
                            "context_category": attribute.category,
                            "context_value": cached["value"] if cached else "",
                            "lag_value": lag,
                            "lag_unit": self.lag_unit,
                            "outcome_observation_id": observation.id,
                            "context_observation_id": cached["observation_id"] if cached else "",
                            "exported_at": self.exported_at_str,
                        }

    def iter_wide_rows(self) -> Iterator[dict[str, Any]]:
        confounder_cache = self._build_confounder_cache()
        location_cache = self._build_location_cache()
        climate_cache = self._build_climate_cache()

        for observation in self._outcome_queryset().iterator(chunk_size=500):
            base_row, outcome_date = self._build_outcome_base_row(observation)
            row = {
                **{key: base_row[key] for key in [
                    "project_id",
                    "project_name",
                    "target_study_id",
                    "target_study_name",
                    "patient_id",
                    "outcome_variable_name",
                    "outcome_display_name",
                    "outcome_value",
                    "outcome_datetime",
                    "outcome_location_name",
                ]},
                "climate_lag_unit": self.lag_unit,
                "exported_at": self.exported_at_str,
            }

            for attribute in self.confounder_attributes:
                cached = confounder_cache.get((observation.patient_id, attribute.id)) if observation.patient_id else None
                row[f"confounder__{attribute.variable_name}"] = cached["value"] if cached else ""

            for attribute in self.location_attributes:
                cached = location_cache.get((observation.location_id, attribute.id)) if observation.location_id and not self.deidentify else None
                row[f"location__{attribute.variable_name}"] = cached["value"] if cached else ""

            for attribute in self.climate_attributes:
                for lag in range(0, self.max_lag_value + 1):
                    lag_days = lag * self.lag_multiplier
                    cached = None
                    if observation.location_id and outcome_date is not None:
                        cached = climate_cache.get(
                            (observation.location_id, outcome_date - timedelta(days=lag_days), attribute.id),
                        )
                    row[f"climate__{attribute.variable_name}_{self.lag_unit_short}-{lag}"] = cached["value"] if cached else ""

            yield row

    def _outcome_queryset(self):
        return (
            Observation.objects.filter(attribute__in=self.outcome_attributes)
            .select_related("patient", "location", "attribute", "time")
            .order_by(
                "patient_id",
                "location_id",
                "time__timestamp",
                "time__start_date",
                "time__end_date",
                "attribute_id",
                "id",
            )
        )

    def _build_confounder_cache(self) -> dict[tuple[int, int], dict[str, Any]]:
        cache: dict[tuple[int, int], dict[str, Any]] = {}
        if not self.confounder_attributes:
            return cache

        observations = (
            Observation.objects.filter(
                attribute__in=self.confounder_attributes,
                patient_id__isnull=False,
            )
            .select_related("attribute", "time")
            .order_by(
                "patient_id",
                "attribute_id",
                "time__timestamp",
                "time__start_date",
                "time__end_date",
                "id",
            )
        )
        for observation in observations.iterator(chunk_size=1000):
            cache[(observation.patient_id, observation.attribute_id)] = {
                "value": serialize_observation_value(observation),
                "observation_id": observation.id,
            }
        return cache

    def _build_location_cache(self) -> dict[tuple[int, int], dict[str, Any]]:
        cache: dict[tuple[int, int], dict[str, Any]] = {}
        if not self.location_attributes or self.deidentify:
            return cache

        observations = (
            Observation.objects.filter(attribute__in=self.location_attributes)
            .select_related("attribute")
            .order_by("location_id", "attribute_id", "id")
        )
        for observation in observations.iterator(chunk_size=1000):
            if not observation.location_id:
                continue
            cache[(observation.location_id, observation.attribute_id)] = {
                "value": serialize_observation_value(observation),
                "observation_id": observation.id,
            }
        return cache

    def _build_climate_cache(self) -> dict[tuple[int, Any, int], dict[str, Any]]:
        cache: dict[tuple[int, Any, int], dict[str, Any]] = {}
        if not self.climate_attributes:
            return cache

        observations = (
            Observation.objects.filter(attribute__in=self.climate_attributes)
            .select_related("attribute", "time")
            .order_by("location_id", "time__timestamp", "time__start_date", "time__end_date", "attribute_id", "id")
        )
        for observation in observations.iterator(chunk_size=1000):
            if not observation.location_id:
                continue
            climate_date = extract_observation_date(observation)
            if climate_date is None:
                continue
            cache[(observation.location_id, climate_date, observation.attribute_id)] = {
                "value": serialize_observation_value(observation),
                "observation_id": observation.id,
            }
        return cache

    def _build_outcome_base_row(self, observation: Observation) -> tuple[dict[str, Any], Any]:
        patient_identifier = ""
        if observation.patient_id:
            patient_identifier = getattr(observation.patient, "unique_id", None) or str(observation.patient_id)
        if self.deidentify:
            patient_identifier = hash_identifier(patient_identifier)

        location_name = ""
        if observation.location_id:
            location_name = getattr(observation.location, "name", "") or ""
        if self.deidentify and location_name:
            location_name = hash_identifier(location_name)

        return {
            "project_id": self.target_study.project_id,
            "project_name": self.target_study.project.name,
            "target_study_id": self.target_study.id,
            "target_study_name": self.target_study.name,
            "patient_id": patient_identifier,
            "outcome_variable_name": observation.attribute.variable_name,
            "outcome_display_name": observation.attribute.display_name or "",
            "outcome_value": serialize_observation_value(observation),
            "outcome_datetime": serialize_time_dimension(observation.time),
            "outcome_location_name": location_name,
        }, extract_observation_date(observation)


def get_climate_lag_unit_for_study(study: Study | None) -> str:
    if not study:
        return "days"

    request_qs = ClimateDataRequest.objects.filter(study=study)
    request = (
        request_qs.filter(status="completed")
        .order_by("-completed_at", "-requested_at")
        .first()
        or request_qs.order_by("-requested_at").first()
    )

    if not request:
        return "days"

    configuration = request.configuration or {}
    return configuration.get("lag_unit") or "days"


def resolve_combined_export_selection(
    *,
    target_study: Study,
    categories: Iterable[str] | None = None,
    selected_attributes: Iterable[Attribute] | None = None,
    source_studies: Iterable[Study] | None = None,
    deidentify: bool = False,
) -> DatasetSelection:
    category_values = list(categories or [])

    attrs_qs = observed_target_attribute_queryset(target_study)
    if selected_attributes:
        attribute_ids = [attribute.pk for attribute in selected_attributes if getattr(attribute, "pk", None)]
        attrs_qs = attrs_qs.filter(
            pk__in=attribute_ids,
        )
    else:
        if category_values:
            category_filter = Q()
            non_health_categories = [value for value in category_values if value != "health"]
            if "health" in category_values:
                category_filter |= Q(pk__in=eligible_health_queryset(attrs_qs).values("pk"))
            if non_health_categories:
                category_filter |= Q(category__in=non_health_categories)
            attrs_qs = attrs_qs.filter(category_filter)
        else:
            attrs_qs = attrs_qs.none()

    attributes = list(
        attrs_qs.distinct().order_by("category", "display_name", "variable_name"),
    )

    eligible_health_ids = set(eligible_health_queryset(attrs_qs).values_list("pk", flat=True))
    outcome_attributes = [
        attribute
        for attribute in attributes
        if attribute.category == "health" and attribute.id in eligible_health_ids
    ]
    confounder_attributes: list[Attribute] = []
    climate_attributes = [attribute for attribute in attributes if attribute.category == "climate"]
    location_attributes = []
    if not deidentify:
        location_attributes = [attribute for attribute in attributes if attribute.category == "geolocation"]

    return DatasetSelection(
        attributes=attributes,
        outcome_attributes=outcome_attributes,
        confounder_attributes=confounder_attributes,
        climate_attributes=climate_attributes,
        location_attributes=location_attributes,
    )


def build_dataset_naming(
    *,
    target_study: Study,
    outcome_attributes: Iterable[Attribute],
    lag_value: int,
    lag_unit: str,
    dataset_shape: str,
) -> DatasetNaming:
    ordered_outcomes = _ordered_unique_attributes(outcome_attributes)
    study_slug = safe_slug(target_study.name, f"target-study-{target_study.id}")

    if not ordered_outcomes:
        outcome_token = "all-outcomes"
    else:
        first_token = safe_slug(
            ordered_outcomes[0].variable_name or ordered_outcomes[0].display_name,
            f"outcome-{ordered_outcomes[0].id}",
        )
        if len(ordered_outcomes) == 1:
            outcome_token = first_token
        else:
            outcome_token = f"{first_token}-plus{len(ordered_outcomes) - 1}"

    lag_short = LAG_UNIT_SHORT.get(lag_unit, "d")
    lag_token = f"lag{max(lag_value, 0)}{lag_short}"
    shape_token = dataset_shape or "long"

    return DatasetNaming(
        study_slug=study_slug,
        outcome_token=outcome_token,
        lag_token=lag_token,
        shape_token=shape_token,
    )


def hash_identifier(value: str) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def serialize_observation_value(observation: Observation) -> str:
    attribute = observation.attribute
    attribute_type = getattr(attribute, "variable_type", None)
    preferred_candidates = []
    if attribute_type == "float":
        preferred_candidates.append(observation.float_value)
    elif attribute_type == "int":
        preferred_candidates.append(observation.int_value)
    elif attribute_type in {"string", "categorical"}:
        preferred_candidates.append(observation.text_value)
    elif attribute_type == "boolean":
        preferred_candidates.append(observation.boolean_value)
    elif attribute_type == "datetime":
        preferred_candidates.append(observation.datetime_value)

    fallback_candidates = (
        observation.float_value,
        observation.int_value,
        observation.text_value,
        observation.boolean_value,
        observation.datetime_value,
    )

    value = next(
        (
            candidate
            for candidate in preferred_candidates + list(fallback_candidates)
            if candidate is not None and candidate != ""
        ),
        None,
    )

    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return ""
    return str(value)


def serialize_time_dimension(time_dimension) -> str:
    if not time_dimension:
        return ""
    if getattr(time_dimension, "timestamp", None):
        return time_dimension.timestamp.isoformat()

    start = getattr(time_dimension, "start_date", None)
    end = getattr(time_dimension, "end_date", None)
    if start and end and start != end:
        return f"{start.isoformat()} / {end.isoformat()}"
    if start:
        return start.isoformat()
    if end:
        return end.isoformat()
    return ""


def extract_observation_date(observation: Observation):
    time_dimension = observation.time
    if not time_dimension:
        return None
    if getattr(time_dimension, "timestamp", None):
        return time_dimension.timestamp.date()
    if getattr(time_dimension, "start_date", None):
        return time_dimension.start_date.date()
    if getattr(time_dimension, "end_date", None):
        return time_dimension.end_date.date()
    return None


def build_csv_stream(columns: list[str], rows: Iterable[dict[str, Any]]) -> Iterator[str]:
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    buffer.seek(0)
    yield buffer.read()
    buffer.truncate(0)
    buffer.seek(0)

    for row in rows:
        writer.writerow(row)
        buffer.seek(0)
        yield buffer.read()
        buffer.truncate(0)
        buffer.seek(0)


def _ordered_unique_attributes(attributes: Iterable[Attribute] | None) -> list[Attribute]:
    ordered: list[Attribute] = []
    seen: set[int] = set()
    for attribute in attributes or []:
        if not attribute or attribute.id in seen:
            continue
        ordered.append(attribute)
        seen.add(attribute.id)
    return ordered
