"""Transformation suggestion service using OpenAI latest Responses API."""
import json
import logging
from collections.abc import Mapping
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from openai import APIError, BadRequestError, OpenAI, RateLimitError
from pydantic import BaseModel, ConfigDict, ValidationError

from core.models import Attribute, Study

from .summary_stats_context import build_deidentified_summary_stats_context

logger = logging.getLogger(__name__)


class TransformationSuggestionPayload(BaseModel):
    """Expected structured response format from OpenAI GPT-5."""

    model_config = ConfigDict(extra="forbid")

    transformation_needed: bool
    transformation_code: str
    explanation: str


class TransformationSuggestionService:
    """Service for generating transformation code suggestions using OpenAI."""

    LOG_PREVIEW_CHARS = 2500

    def __init__(self):
        """Initialize the transformation suggestion service with OpenAI client."""
        if not settings.OPENAI_API_KEY:
            msg = "OPENAI_API_KEY must be set in settings"
            raise ValueError(msg)

        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        # Use the latest GPT-5 family by default for structured output generation
        self.model = getattr(
            settings,
            "OPENAI_TRANSFORMATION_MODEL",
            "gpt-5.4-mini",
        )

    def suggest_transformation_code(
        self,
        source_attribute: Attribute,
        target_attribute: Attribute,
        *,
        source_study: Study | None = None,
        include_deidentified_summary_stats: bool = False,
        mapping_context: Mapping[str, Any] | None = None,
        source_eda_summary: Mapping[str, Any] | None = None,
    ) -> str | None:
        """Generate transformation code suggestion for a source/target pair."""
        result: str | None = None
        try:
            # Build comprehensive context for the transformation
            context = self._build_transformation_context(
                source_attribute,
                target_attribute,
                source_study=source_study,
                include_deidentified_summary_stats=include_deidentified_summary_stats,
                mapping_context=mapping_context,
                source_eda_summary=source_eda_summary,
            )

            # Skip call for obvious no-op mappings
            if not self._transformation_likely_needed(context):
                logger.info(
                    "No transformation suggested for %s -> %s",
                    source_attribute.variable_name,
                    target_attribute.variable_name,
                )
                result = ""
            else:
                result = self._generate_validated_transformation(
                    source_attribute,
                    target_attribute,
                    context,
                )
        except (ValueError, RuntimeError):
            logger.exception("Error generating transformation suggestion")
            result = None

        return result

    def _call_openai_for_structured_output(
        self,
        prompt: str,
    ) -> Mapping[str, Any] | None:
        """Call OpenAI Responses API requesting a strict JSON schema."""
        self._log_request_payload(
            label="transformation_suggestion_request",
            payload={
                "model": self.model,
                "prompt": prompt,
            },
        )
        try:
            response = self.client.responses.parse(
                model=self.model,
                input=[
                    {
                        "role": "system",
                        "content": [
                            {
                                "type": "input_text",
                                "text": self._get_system_prompt(),
                            },
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": prompt,
                            },
                        ],
                    },
                ],
                text_format=TransformationSuggestionPayload,
            )
        except (BadRequestError, APIError, RateLimitError):
            logger.exception("OpenAI Responses API call failed")
            return None

        payload = getattr(response, "output_parsed", None)
        if payload is None:
            payload = self._extract_structured_payload(response)
        if payload is None:
            logger.warning("Responses API returned no structured payload")
            return None

        validated = self._validate_payload(payload)
        if validated is None:
            logger.warning("Structured payload failed validation")
            return None

        self._log_response_payload(
            label="transformation_suggestion_response",
            response=response,
            payload=validated,
        )

        return validated

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

    def _extract_structured_payload(self, response: Any) -> Mapping[str, Any] | None:
        """Extract parsed structured output from the OpenAI Responses API."""
        output = getattr(response, "output", []) or []
        for block in output:
            contents = getattr(block, "content", []) or []
            for item in contents:
                parsed = getattr(item, "parsed", None)
                if isinstance(parsed, Mapping):
                    return parsed
                if isinstance(parsed, str):
                    fallback = self._coerce_text_payload(parsed)
                    if fallback is not None:
                        return fallback
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    fallback = self._coerce_text_payload(text)
                    if fallback is not None:
                        return fallback
        return None

    def _validate_payload(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Ensure payload provides the expected fields and types."""
        try:
            parsed = TransformationSuggestionPayload.model_validate(payload)
        except ValidationError as exc:
            logger.warning("Structured payload validation failed: %s", exc)
            return None

        return parsed.model_dump()

    def _coerce_text_payload(self, payload: str) -> Mapping[str, Any] | None:
        """Attempt to convert textual payloads into the structured schema."""
        try:
            loaded = json.loads(payload)
        except json.JSONDecodeError:
            logger.exception("Structured output text was not valid JSON")
            return None

        if isinstance(loaded, Mapping):
            return loaded

        logger.warning("Structured output text did not decode into a mapping")
        return None

    def _build_transformation_context(
        self,
        source_attr: Attribute,
        target_attr: Attribute,
        *,
        source_study: Study | None = None,
        include_deidentified_summary_stats: bool = False,
        mapping_context: Mapping[str, Any] | None = None,
        source_eda_summary: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build comprehensive context about the attributes for transformation."""
        context = {
            "source": {
                "variable_name": source_attr.variable_name,
                "display_name": source_attr.display_name or "",
                "description": source_attr.description or "",
                "variable_type": source_attr.variable_type or "",
                "unit": source_attr.unit or "",
                "ontology_code": source_attr.ontology_code or "",
            },
            "target": {
                "variable_name": target_attr.variable_name,
                "display_name": target_attr.display_name or "",
                "description": target_attr.description or "",
                "variable_type": target_attr.variable_type or "",
                "unit": target_attr.unit or "",
                "ontology_code": target_attr.ontology_code or "",
            },
        }

        if mapping_context:
            context["mapping_context"] = dict(mapping_context)

        if source_eda_summary:
            context["source_eda_summary"] = dict(source_eda_summary)

        if include_deidentified_summary_stats and source_study is not None:
            context["deidentified_summary_stats_context"] = build_deidentified_summary_stats_context(
                source_study=source_study,
                variable_names=[source_attr.variable_name],
            )

        return context

    def _transformation_likely_needed(self, context: dict[str, Any]) -> bool:
        """
        Heuristic to determine if transformation is likely needed.
        Skip OpenAI call for obvious cases where no transformation is needed.
        """
        source = context["source"]
        target = context["target"]

        def _normalize(value: Any) -> str:
            if value is None:
                return ""
            if isinstance(value, str):
                return value.strip().lower()
            return str(value).strip().lower()

        fields = (
            "variable_name",
            "display_name",
            "description",
            "variable_type",
            "unit",
            "ontology_code",
        )

        for field in fields:
            if _normalize(source.get(field, "")) != _normalize(target.get(field, "")):
                return True

        return False

    def _generate_validated_transformation(
        self,
        source_attribute: Attribute,
        target_attribute: Attribute,
        context: dict[str, Any],
    ) -> str | None:
        """Generate transform code and retry once if it fails the local validator."""
        validation_error = ""

        for attempt in range(2):
            prompt = self._create_transformation_prompt(
                context,
                validation_error=validation_error,
            )
            response_payload = self._call_openai_for_structured_output(prompt)
            if not response_payload:
                logger.warning("Empty or invalid response from OpenAI")
                return None

            transformation_needed = response_payload.get(
                "transformation_needed",
                True,
            )
            if not transformation_needed:
                logger.info(
                    "No transformation needed for %s -> %s",
                    source_attribute.variable_name,
                    target_attribute.variable_name,
                )
                return ""

            code = response_payload.get("transformation_code", "").strip()
            explanation = response_payload.get("explanation", "")
            if not code:
                logger.warning("No transformation code in OpenAI response")
                return None

            try:
                self._validate_transform_code(code)
            except DjangoValidationError as exc:
                validation_error = str(exc)
                logger.warning(
                    "Generated transform code failed local validation for %s -> %s on attempt %s: %s",
                    source_attribute.variable_name,
                    target_attribute.variable_name,
                    attempt + 1,
                    validation_error,
                )
                if attempt == 0:
                    continue
                return None

            logger.info(
                "Generated transformation for %s -> %s: %s",
                source_attribute.variable_name,
                target_attribute.variable_name,
                explanation,
            )
            return code

        return None

    def _validate_transform_code(self, code: str) -> None:
        """Use the same AST validator as the model layer before returning code to the UI."""
        from health.models import validate_safe_transform_code

        validate_safe_transform_code(code)

    def _create_transformation_prompt(
        self,
        context: dict[str, Any],
        validation_error: str = "",
    ) -> str:
        """Create a detailed prompt for transformation code generation."""
        source = context["source"]
        target = context["target"]
        mapping_context = context.get("mapping_context") or {}
        source_eda_summary = context.get("source_eda_summary") or {}
        summary_stats_context = context.get("deidentified_summary_stats_context") or {}
        retry_guidance = ""
        if validation_error:
            retry_guidance = f"""

PREVIOUS DRAFT REJECTED:
- The last candidate failed local validation with: {validation_error}
- Regenerate the code so every function call is inline and whitelisted.
- Do not invent helper functions such as parse_date(...), normalize_sex(...), to_grams(...), round_to_int(...), or any other named utility.
"""

        summary_context_block = ""
        mapping_context_block = ""
        source_eda_block = ""
        if mapping_context:
            mapping_context_block = f"""

CURRENT MAPPING CONTEXT:
{json.dumps(mapping_context, indent=2, default=str)}

Use this to understand the intended mapping, relation ownership, role, and any AI/human notes. Do not change the target or relation here; only generate value-level transform code when needed.
"""
        if source_eda_summary:
            source_eda_block = f"""

SOURCE VARIABLE EDA SUMMARY:
{json.dumps(source_eda_summary, indent=2, default=str)}

Use this aggregate EDA to infer observed encodings, ranges, missingness, high-cardinality behaviour, and whether category/type conversion is likely needed. Do not infer row-level values or identifiable data from it.
"""
        if summary_stats_context:
            summary_context_block = f"""

DE-IDENTIFIED SUMMARY DATA:
{json.dumps(summary_stats_context, indent=2)}

Use these aggregate summary statistics only when they help choose a safe transformation.
Do not infer raw values, example records, or identifiable data from them.
"""

        prompt = f"""
You are an expert data harmonisation specialist helping to transform health
and climate research data.
Analyse the source and target variables below and determine if a
transformation is needed.

SOURCE VARIABLE:
- Name: {source['variable_name']}
- Display Name: {source['display_name']}
- Description: {source['description']}
- Type: {source['variable_type']}
- Unit: {source['unit']}
- Ontology Code: {source['ontology_code']}

TARGET VARIABLE:
- Name: {target['variable_name']}
- Display Name: {target['display_name']}
- Description: {target['description']}
- Type: {target['variable_type']}
- Unit: {target['unit']}
- Ontology Code: {target['ontology_code']}
{mapping_context_block}
{source_eda_block}
{summary_context_block}

TASK:
1. Determine if a transformation is needed to map from source to target.
   Use the source EDA summary when available to understand observed encodings,
   ranges, missingness, and category values.
2. If no transformation is needed (variables already compatible), set
   transformation_needed to false.
3. If transformation is needed, generate safe Python code following these
   constraints:

TRANSFORMATION CODE REQUIREMENTS:
- Return exactly one lambda expression or exactly one function named
    transform(value).
- Do not define helper functions, nested functions, classes, imports, or any
    custom named utilities.
- Do not call any user-defined or invented functions.
- Every call must be one of the whitelisted built-ins below or a whitelisted
    string/list/dict method.
- Preferred form: lambda value: ... when possible.
- Handle None/empty values gracefully.
- Only use safe built-ins: str(), int(), float(), bool(), round(), abs(),
    min(), max(), len(), sum(), any(), all(), sorted(), reversed(), enumerate(),
    range(), zip(), list(), tuple(), dict(), set().
- Safe operators: +, -, *, /, %, **, comparisons, in/not in, is/is not,
    boolean and/or/not, ternary expressions, indexing and slicing.
- Safe control flow: if/else, comprehensions, and simple for-loops inside
    def transform(value): ... functions.
- String methods: .upper(), .lower(), .title(), .capitalize(), .strip(),
    .lstrip(), .rstrip(), .split(), .rsplit(), .replace(), .join(),
    .startswith(), .endswith(), .find(), .count(), .isdigit().
- Safe list methods: .append(), .extend(), .insert(), .remove(), .pop(),
    .clear(), .count(), .index(), .sort(), .reverse(), .copy().
- Safe dict methods: .keys(), .values(), .items(), .get(), .pop(), .clear(),
    .copy(), .update().
- No file operations, imports, or dangerous functions.
- Never call helper names like parse_date, parse_int, parse_time,
    normalize_sex, strip_whitespace, clamp_0_10_int, round_to_int,
    boolean_to_yes_no, map_nonempty_to_yes, standardize_mode_of_delivery,
    to_grams, or any similarly invented function.
- Return the same type as expected by target variable.
- Add input validation for edge cases.
- If using a loop, keep it bounded to the provided value and return the result.
- Do not use while-loops, try/except, class definitions, imports, or dunder
    attributes.

COMMON TRANSFORMATION PATTERNS:
- Unit conversion: lambda value: float(value) * 2.54 if value else None
  (inches to cm).
- Text standardisation: lambda value: value.upper().strip() if value else "".
- Category mapping: lambda value: 'Yes' if value == '1' else 'No' if value ==
  '0' else value.
- Extract data: lambda value: value.split(',')[0] if value else None.
- Type conversion: lambda value: int(float(value)) if value else None.
- Safe iterable cleanup:
    def transform(value):
            if not value:
                    return []
            cleaned = []
            for item in value.split(','):
                    item = item.strip()
                    if item:
                            cleaned.append(item)
            return cleaned
                {retry_guidance}

RESPOND WITH VALID JSON:
{{
    "transformation_needed": true/false,
    "transformation_code": "python code here or empty string",
    "explanation": "brief explanation of what the transformation does"
}}
"""
        return prompt.strip()

    def _get_system_prompt(self) -> str:
        """Get the system prompt that defines the AI's role and constraints."""
        return """
You are a data harmonisation expert specialising in health and climate
research data transformation.
Your role is to generate safe, reliable Python transformation code for mapping
between research variables.

CORE PRINCIPLES:
1. Safety first — only suggest transformations using whitelisted safe
   functions.
2. Handle edge cases — always check for None/empty values.
3. Use the mapping context and EDA summary to make a practical judgement about
   observed encodings, units, ranges, and category values. If the EDA shows a
   clear encoding or unit mismatch, generate a transform instead of returning a
   no-op.
4. Prefer simple, readable code over complex transformations.
5. Always return valid JSON in the exact format requested.
6. Stay inside the validator whitelist: safe built-ins, safe string/list/dict
    methods, comprehensions, and simple for-loops only.
7. Never invent helper functions or wrapper utilities. Inline every step in the
   lambda or in def transform(value).
8. Only use de-identified summary statistics when they are explicitly provided.
    Never request, infer, or rely on row-level or identifiable data.
9. Treat relation ownership and mapping role as context for interpreting the
   value, not as instructions to change the target mapping.

WHEN NO TRANSFORMATION IS NEEDED:
- Variables have identical names, types, units, and encodings.
- Variables are already compatible (e.g., both text fields with same meaning).
- Direct mapping is appropriate without any data manipulation.

WHEN TRANSFORMATION IS NEEDED:
- Unit conversion (e.g., inches to centimetres).
- Data type conversion (e.g., string to numeric).
- Category standardisation (e.g., Yes/No to 1/0).
- Text formatting (e.g., case normalisation).
- Value extraction (e.g., getting first part of compound values).
"""


# Global instance for easy import
transformation_suggestion_service = TransformationSuggestionService()
