from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils.text import slugify

from core.models import Attribute, Observation, Patient, Study, TimeDimension


RELATIONSHIP_SYSTEM_CATEGORY = "relationship_system"

RELATIONSHIP_EDGE_ID = "relationship_edge_id"
RELATIONSHIP_SOURCE_PATIENT_ID = "relationship_source_patient_id"
RELATIONSHIP_RELATED_PATIENT_ID = "relationship_related_patient_id"
RELATIONSHIP_TYPE = "relationship_type"
RELATIONSHIP_NAME = "relationship_name"
RELATIONSHIP_DIRECTION = "relationship_direction"
RELATIONSHIP_SCHEMA_ID = "relationship_schema_id"

RELATIONSHIP_ATTRIBUTE_DEFINITIONS: tuple[dict[str, str], ...] = (
    {
        "variable_name": RELATIONSHIP_EDGE_ID,
        "display_name": "Relationship edge ID",
        "description": "Stable identifier for one relationship edge record.",
    },
    {
        "variable_name": RELATIONSHIP_SOURCE_PATIENT_ID,
        "display_name": "Relationship source patient ID",
        "description": "Patient/entity that owns this relationship edge.",
    },
    {
        "variable_name": RELATIONSHIP_RELATED_PATIENT_ID,
        "display_name": "Relationship related patient ID",
        "description": "Patient/entity referenced by this relationship edge.",
    },
    {
        "variable_name": RELATIONSHIP_TYPE,
        "display_name": "Relationship type",
        "description": "Relationship type from source entity to related entity.",
    },
    {
        "variable_name": RELATIONSHIP_NAME,
        "display_name": "Relationship instance name",
        "description": "Stable relationship instance label, such as child_1.",
    },
    {
        "variable_name": RELATIONSHIP_DIRECTION,
        "display_name": "Relationship direction",
        "description": "Direction of the relationship edge, such as forward or inverse.",
    },
    {
        "variable_name": RELATIONSHIP_SCHEMA_ID,
        "display_name": "Relationship mapping schema ID",
        "description": "Mapping schema that produced the relationship edge.",
    },
)

RELATIONSHIP_ATTRIBUTE_NAMES = tuple(
    definition["variable_name"] for definition in RELATIONSHIP_ATTRIBUTE_DEFINITIONS
)

REPEATABLE_RELATION_TYPES = {"child", "sibling", "other"}

INVERSE_RELATION_DEFAULTS = {
    "child": ("parent", "parent"),
    "parent": ("child", "child"),
    "mother": ("child", "child"),
    "father": ("child", "child"),
    "spouse": ("spouse", "spouse"),
    "sibling": ("sibling", "sibling"),
    "other": ("other", "other"),
}


@dataclass(frozen=True, slots=True)
class RelationshipExportMetadata:
    source_patient_id: str
    relation_type: str
    relation_name: str


def normalize_relation_name(value: Any, *, prefix: str | None = None) -> str:
    raw = str(value or "").strip().replace("_", "-")
    slug = slugify(raw).replace("-", "_")
    if prefix and slug and not slug.startswith(f"{prefix}_") and slug != prefix:
        slug = f"{prefix}_{slug}"
    if not slug:
        raise ValidationError("Relation name cannot be blank.")
    return slug


def is_suffix_safe_relation_name(value: str) -> bool:
    if not value:
        return False
    return normalize_relation_name(value) == value


def infer_inverse_relation(relation_type: str) -> tuple[str, str]:
    return INVERSE_RELATION_DEFAULTS.get(relation_type, ("other", "other"))


def relation_name_from_order(relation_type: str, order: int | None = None) -> str:
    if relation_type in REPEATABLE_RELATION_TYPES:
        if order is None or order < 1:
            raise ValidationError("Repeatable relation types require a positive relation instance order.")
        return f"{relation_type}_{order}"
    return relation_type


def infer_next_relation_name(*, schema, relation_type: str, exclude_rule_id: int | None = None) -> str:
    if relation_type in REPEATABLE_RELATION_TYPES:
        existing_qs = (
            schema.rules.filter(relation_type=relation_type)
            .exclude(relation_name="")
        )
        if exclude_rule_id:
            existing_qs = existing_qs.exclude(pk=exclude_rule_id)
        existing = set(existing_qs.values_list("relation_name", flat=True))
        index = 1
        while f"{relation_type}_{index}" in existing:
            index += 1
        return f"{relation_type}_{index}"
    return relation_type


def relation_instance_choices(schema) -> list[tuple[str, str]]:
    choices: list[tuple[str, str]] = [("", "Select relation instance")]
    existing = (
        schema.rules.exclude(relation_type="self")
        .exclude(relation_name="")
        .values_list("relation_type", "relation_name")
        .distinct()
        .order_by("relation_type", "relation_name")
    )
    for relation_type, relation_name in existing:
        choices.append((f"existing:{relation_type}:{relation_name}", f"Use existing {relation_name} ({relation_type})"))
    return choices


def relation_instance_previews(schema) -> dict[str, dict[str, str]]:
    previews: dict[str, dict[str, str]] = {}
    for relation_type, label in schema.RELATION_CHOICES:
        if relation_type == "self":
            continue
        relation_name = infer_next_relation_name(schema=schema, relation_type=relation_type)
        inverse_type, inverse_name = infer_inverse_relation(relation_type)
        previews[relation_type] = {
            "relation_type": relation_type,
            "relation_label": label,
            "relation_name": relation_name,
            "inverse_relation_type": inverse_type,
            "inverse_relation_name": inverse_name,
        }
    return previews


def resolve_relation_instance_choice(*, schema, relation_type: str, choice: str) -> str:
    if relation_type == "self":
        return ""
    if not choice:
        raise ValidationError("Choose an existing relation instance or create a new one.")
    if choice.startswith("existing:"):
        _, existing_type, existing_name = choice.split(":", 2)
        if existing_type != relation_type:
            raise ValidationError("Selected relation instance does not match the relation type.")
        return existing_name
    raise ValidationError("Selected relation instance is not valid.")


def relationship_attribute_query() -> Q:
    return Q(category=RELATIONSHIP_SYSTEM_CATEGORY) | Q(variable_name__in=RELATIONSHIP_ATTRIBUTE_NAMES)


def is_relationship_system_attribute(attribute: Attribute | None) -> bool:
    if attribute is None:
        return False
    return (
        attribute.category == RELATIONSHIP_SYSTEM_CATEGORY
        or attribute.variable_name in RELATIONSHIP_ATTRIBUTE_NAMES
    )


def relationship_attributes_queryset(study: Study):
    return study.variables.filter(
        source_type="target",
        variable_name__in=RELATIONSHIP_ATTRIBUTE_NAMES,
    )


def validate_relationship_attribute_compatibility(study: Study) -> list[str]:
    errors: list[str] = []
    reserved = Attribute.objects.filter(
        studies=study,
        variable_name__in=RELATIONSHIP_ATTRIBUTE_NAMES,
    )
    for attribute in reserved:
        if attribute.source_type != "target":
            errors.append(f"{attribute.variable_name} must be a target attribute.")
        if attribute.variable_type != "string":
            errors.append(f"{attribute.variable_name} must have string variable_type.")
        if attribute.category != RELATIONSHIP_SYSTEM_CATEGORY:
            errors.append(
                f"{attribute.variable_name} must use category '{RELATIONSHIP_SYSTEM_CATEGORY}'."
            )
    return errors


@transaction.atomic
def get_or_create_relationship_attributes(study: Study) -> dict[str, Attribute]:
    errors = validate_relationship_attribute_compatibility(study)
    if errors:
        raise ValidationError(errors)

    attributes: dict[str, Attribute] = {}
    for definition in RELATIONSHIP_ATTRIBUTE_DEFINITIONS:
        variable_name = definition["variable_name"]
        attribute = study.variables.filter(variable_name=variable_name).first()
        if attribute is None:
            attribute = Attribute.objects.create(
                variable_name=variable_name,
                source_type="target",
                display_name=definition["display_name"],
                description=definition["description"],
                variable_type="string",
                category=RELATIONSHIP_SYSTEM_CATEGORY,
            )
        attribute.display_name = definition["display_name"]
        attribute.description = definition["description"]
        attribute.variable_type = "string"
        attribute.category = RELATIONSHIP_SYSTEM_CATEGORY
        attribute.source_type = "target"
        attribute.save(
            update_fields=[
                "display_name",
                "description",
                "variable_type",
                "category",
                "source_type",
                "updated_at",
            ],
        )
        study.variables.add(attribute)
        attributes[variable_name] = attribute
    return attributes


def generated_patient_id(base_patient_id: str, relation_name: str) -> str:
    return f"{base_patient_id}_{relation_name}"


def relationship_edge_id(
    *,
    schema_id: int,
    source_patient_id: str,
    relation_type: str,
    relation_name: str,
    direction: str,
) -> str:
    return "__".join(
        [
            f"schema_{schema_id}",
            source_patient_id,
            relation_type,
            relation_name,
            direction,
        ],
    )


def create_relationship_edge_time(source_time: TimeDimension | None) -> TimeDimension:
    if source_time is None:
        return TimeDimension.objects.create()
    return TimeDimension.objects.create(
        timestamp=source_time.timestamp,
        start_date=source_time.start_date,
        end_date=source_time.end_date,
    )


def delete_relationship_observations_for_schema(*, target_study: Study, schema_id: int) -> int:
    schema_attr = target_study.variables.filter(
        variable_name=RELATIONSHIP_SCHEMA_ID,
        category=RELATIONSHIP_SYSTEM_CATEGORY,
    ).first()
    if schema_attr is None:
        return 0

    schema_rows = list(
        Observation.objects.filter(
            attribute=schema_attr,
            text_value=str(schema_id),
        ).values("patient_id", "location_id", "time_id")
    )
    if not schema_rows:
        return 0

    system_attr_ids = list(
        relationship_attributes_queryset(target_study).values_list("id", flat=True)
    )
    if not system_attr_ids:
        return 0

    delete_filter = Q()
    for row in schema_rows:
        delete_filter |= Q(
            patient_id=row["patient_id"],
            location_id=row["location_id"],
            time_id=row["time_id"],
        )
    deleted_count, _ = Observation.objects.filter(
        delete_filter,
        attribute_id__in=system_attr_ids,
    ).delete()
    return deleted_count


def write_relationship_edge(
    *,
    attributes: dict[str, Attribute],
    patient: Patient,
    source_patient_id: str,
    related_patient_id: str,
    relation_type: str,
    relation_name: str,
    direction: str,
    schema_id: int,
    time: TimeDimension,
) -> None:
    edge_id = relationship_edge_id(
        schema_id=schema_id,
        source_patient_id=source_patient_id,
        relation_type=relation_type,
        relation_name=relation_name,
        direction=direction,
    )
    values = {
        RELATIONSHIP_EDGE_ID: edge_id,
        RELATIONSHIP_SOURCE_PATIENT_ID: source_patient_id,
        RELATIONSHIP_RELATED_PATIENT_ID: related_patient_id,
        RELATIONSHIP_TYPE: relation_type,
        RELATIONSHIP_NAME: relation_name,
        RELATIONSHIP_DIRECTION: direction,
        RELATIONSHIP_SCHEMA_ID: str(schema_id),
    }
    for variable_name, value in values.items():
        Observation.objects.update_or_create(
            patient=patient,
            location=None,
            attribute=attributes[variable_name],
            time=time,
            defaults={"text_value": value},
        )


def build_relationship_export_lookup(target_study: Study) -> dict[str, RelationshipExportMetadata]:
    rows = Observation.objects.filter(
        attribute__in=relationship_attributes_queryset(target_study),
        attribute__category=RELATIONSHIP_SYSTEM_CATEGORY,
    ).select_related("patient", "attribute", "time")

    grouped: dict[tuple[int | None, int | None], dict[str, str]] = {}
    patient_ids: dict[tuple[int | None, int | None], str] = {}
    for row in rows.iterator(chunk_size=1000):
        key = (row.patient_id, row.time_id)
        grouped.setdefault(key, {})[row.attribute.variable_name] = row.text_value
        if row.patient_id and row.patient:
            patient_ids[key] = row.patient.unique_id

    lookup: dict[str, RelationshipExportMetadata] = {}
    for key, values in grouped.items():
        if values.get(RELATIONSHIP_DIRECTION) != "forward":
            continue
        related_id = values.get(RELATIONSHIP_RELATED_PATIENT_ID) or ""
        source_id = values.get(RELATIONSHIP_SOURCE_PATIENT_ID) or patient_ids.get(key, "")
        relation_type = values.get(RELATIONSHIP_TYPE) or ""
        relation_name = values.get(RELATIONSHIP_NAME) or ""
        if related_id:
            lookup[related_id] = RelationshipExportMetadata(
                source_patient_id=source_id,
                relation_type=relation_type,
                relation_name=relation_name,
            )
    return lookup


def relationship_summary_from_observations(target_study: Study) -> dict[str, Any]:
    rows = Observation.objects.filter(
        attribute__in=relationship_attributes_queryset(target_study),
        attribute__category=RELATIONSHIP_SYSTEM_CATEGORY,
    ).select_related("patient", "attribute", "time")
    grouped: dict[tuple[int | None, int | None], dict[str, str]] = {}
    for row in rows.iterator(chunk_size=1000):
        grouped.setdefault((row.patient_id, row.time_id), {})[row.attribute.variable_name] = row.text_value

    required = set(RELATIONSHIP_ATTRIBUTE_NAMES)
    generated_patients: set[str] = set()
    source_patients: set[str] = set()
    by_type: dict[str, int] = {}
    by_name: dict[str, int] = {}
    by_direction: dict[str, int] = {}
    edge_keys: set[str] = set()
    inverse_keys: set[str] = set()
    incomplete_groups = 0

    for values in grouped.values():
        missing = required - set(values)
        if missing:
            incomplete_groups += 1
        direction = values.get(RELATIONSHIP_DIRECTION, "")
        relation_type = values.get(RELATIONSHIP_TYPE, "")
        relation_name = values.get(RELATIONSHIP_NAME, "")
        source_id = values.get(RELATIONSHIP_SOURCE_PATIENT_ID, "")
        related_id = values.get(RELATIONSHIP_RELATED_PATIENT_ID, "")
        if relation_type:
            by_type[relation_type] = by_type.get(relation_type, 0) + 1
        if relation_name:
            by_name[relation_name] = by_name.get(relation_name, 0) + 1
        if direction:
            by_direction[direction] = by_direction.get(direction, 0) + 1
        if direction == "forward":
            if related_id:
                generated_patients.add(related_id)
            if source_id:
                source_patients.add(source_id)
            edge_keys.add(f"{source_id}|{related_id}")
        elif direction == "inverse":
            inverse_keys.add(f"{related_id}|{source_id}")

    return {
        "generated_related_patient_count": len(generated_patients),
        "source_patients_with_related_count": len(source_patients),
        "counts_by_relationship_type": by_type,
        "counts_by_relationship_name": by_name,
        "counts_by_relationship_direction": by_direction,
        "missing_inverse_edge_count": len(edge_keys - inverse_keys),
        "incomplete_edge_group_count": incomplete_groups,
    }
