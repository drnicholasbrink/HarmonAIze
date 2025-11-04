"""Transformation suggestion service using OpenAI latest Responses API."""
import json
import logging
from collections.abc import Mapping
from typing import Any

from django.conf import settings
from openai import APIError, BadRequestError, OpenAI, RateLimitError
from pydantic import BaseModel, ConfigDict, ValidationError

from core.models import Attribute

logger = logging.getLogger(__name__)


class TransformationSuggestionPayload(BaseModel):
    """Expected structured response format from OpenAI GPT-5."""

    model_config = ConfigDict(extra="forbid")

    transformation_needed: bool
    transformation_code: str
    explanation: str


class TransformationSuggestionService:
    """Service for generating transformation code suggestions using OpenAI."""

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
            "gpt-5.1",
        )

    def suggest_transformation_code(
        self,
        source_attribute: Attribute,
        target_attribute: Attribute,
    ) -> str | None:
        """Generate transformation code suggestion for a source/target pair."""
        result: str | None = None
        try:
            # Build comprehensive context for the transformation
            context = self._build_transformation_context(
                source_attribute,
                target_attribute,
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
                # Generate the transformation using OpenAI Responses API
                prompt = self._create_transformation_prompt(context)
                response_payload = self._call_openai_for_structured_output(prompt)
                if not response_payload:
                    logger.warning("Empty or invalid response from OpenAI")
                    result = None
                else:
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
                        result = ""
                    else:
                        code = response_payload.get("transformation_code", "")
                        explanation = response_payload.get("explanation", "")
                        if code:
                            logger.info(
                                "Generated transformation for %s -> %s: %s",
                                source_attribute.variable_name,
                                target_attribute.variable_name,
                                explanation,
                            )
                            result = code.strip()
                        else:
                            logger.warning(
                                "No transformation code in OpenAI response",
                            )
                            result = None
        except (ValueError, RuntimeError):
            logger.exception("Error generating transformation suggestion")
            result = None

        return result

    def _call_openai_for_structured_output(
        self,
        prompt: str,
    ) -> Mapping[str, Any] | None:
        """Call OpenAI Responses API requesting a strict JSON schema."""
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

        return validated

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
    ) -> dict[str, Any]:
        """Build comprehensive context about the attributes for transformation."""
        return {
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

    def _create_transformation_prompt(self, context: dict[str, Any]) -> str:
        """Create a detailed prompt for transformation code generation."""
        source = context["source"]
        target = context["target"]

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

TASK:
1. Determine if a transformation is needed to map from source to target.
2. If no transformation is needed (variables already compatible), set
   transformation_needed to false.
3. If transformation is needed, generate safe Python code following these
   constraints:

TRANSFORMATION CODE REQUIREMENTS:
- Use lambda: lambda value: value.upper().strip() if value else "".
- OR multi-line functions like: def transform(value): return processed_value
- Handle None/empty values gracefully.
- Only use safe methods: str(), int(), float(), bool(), round(), abs(),
  min(), max(), len().
- String methods: .upper(), .lower(), .strip(), .split(), .replace(), .join(),
  .startswith(), .endswith().
- No file operations, imports, or dangerous functions.
- Return the same type as expected by target variable.
- Add input validation for edge cases.

COMMON TRANSFORMATION PATTERNS:
- Unit conversion: lambda value: float(value) * 2.54 if value else None
  (inches to cm).
- Text standardisation: lambda value: value.upper().strip() if value else "".
- Category mapping: lambda value: 'Yes' if value == '1' else 'No' if value ==
  '0' else value.
- Extract data: lambda value: value.split(',')[0] if value else None.
- Type conversion: lambda value: int(float(value)) if value else None.

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
3. Be conservative — if unsure whether transformation is needed, return
   transformation_needed: false.
4. Prefer simple, readable code over complex transformations.
5. Always return valid JSON in the exact format requested.

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
