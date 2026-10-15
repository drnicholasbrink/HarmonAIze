"""
Utility functions for processing codebooks and extracting variable information.
"""
import csv
import html
import io
import json
import logging
import re
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from django.contrib import messages
from django.core.files.base import ContentFile
from django.shortcuts import redirect

logger = logging.getLogger(__name__)


VARIABLE_NAME_EXPANSIONS = {
    "age": "age",
    "sex": "sex",
    "gender": "gender",
    "dob": "date of birth",
    "bp": "blood pressure",
    "sbp": "systolic blood pressure",
    "dbp": "diastolic blood pressure",
    "hr": "heart rate",
    "rr": "respiratory rate",
    "temp": "temperature",
    "wt": "weight",
    "ht": "height",
    "bmi": "body mass index",
    "id": "identifier",
    "pid": "patient identifier",
    "pt": "patient",
    "adm": "admission",
    "dx": "diagnosis",
    "rx": "treatment",
    "addr": "address",
    "lat": "latitude",
    "lon": "longitude",
    "lng": "longitude",
    "loc": "location",
}

GEOLOCATION_HINTS = {"lat", "latitude", "lon", "lng", "longitude", "location", "address", "city", "country", "village", "district", "region", "site"}
CLIMATE_HINTS = {"temperature", "temp", "rain", "rainfall", "precip", "precipitation", "humidity", "weather", "wind", "solar", "climate"}
IDENTIFIER_HINTS = {"id", "identifier", "patient_id", "participant_id", "record_id", "study_id", "visit_id", "sample_id"}
DATE_HINTS = {"date", "time", "datetime", "timestamp", "visit_date", "admission_date", "discharge_date", "dob", "birth_date"}
BOOLEAN_VALUES = {"0", "1", "true", "false", "yes", "no", "y", "n", "t", "f"}


def _tokenize_variable_name(variable_name: str) -> list[str]:
    raw_tokens = re.split(r"[^a-zA-Z0-9]+", (variable_name or "").lower())
    tokens: list[str] = []
    for token in raw_tokens:
        if not token:
            continue
        expanded = VARIABLE_NAME_EXPANSIONS.get(token)
        if expanded:
            tokens.extend(expanded.split())
        else:
            tokens.append(token)
    return tokens


def _humanize_variable_name(variable_name: str) -> str:
    tokens = _tokenize_variable_name(variable_name)
    if not tokens:
        return str(variable_name or "")
    return " ".join(tokens).replace(" id", " ID").title().replace("Id", "ID")


def _infer_category_from_name(variable_name: str) -> str:
    name_tokens = set(_tokenize_variable_name(variable_name))
    if name_tokens & GEOLOCATION_HINTS:
        return "geolocation"
    if name_tokens & CLIMATE_HINTS:
        return "climate"
    return "health"


def _infer_unit_from_name(variable_name: str) -> str:
    lowered = (variable_name or "").lower()
    if "age" in lowered:
        return "years"
    if any(token in lowered for token in {"weight", "wt"}):
        return "kg"
    if any(token in lowered for token in {"height", "ht"}):
        return "cm"
    if "bmi" in lowered:
        return "kg/m^2"
    if any(token in lowered for token in {"sbp", "dbp", "pressure", "bp"}):
        return "mmHg"
    if "temp" in lowered or "temperature" in lowered:
        return "C"
    return ""


def _infer_variable_type_from_series(series: pd.Series, variable_name: str) -> str:
    lowered = (variable_name or "").lower()
    non_null = series.dropna()
    sample = non_null.head(25)

    if any(hint in lowered for hint in IDENTIFIER_HINTS):
        return "string"

    if any(hint in lowered for hint in DATE_HINTS):
        parsed = pd.to_datetime(sample, errors="coerce")
        if len(sample) and parsed.notna().sum() >= max(1, int(len(sample) * 0.6)):
            return "datetime"

    if pd.api.types.is_bool_dtype(series):
        return "boolean"

    if len(sample):
        normalized = {str(value).strip().lower() for value in sample.tolist() if str(value).strip()}
        if normalized and normalized <= BOOLEAN_VALUES:
            return "boolean"

    if pd.api.types.is_integer_dtype(series):
        return "int"
    if pd.api.types.is_float_dtype(series):
        return "float"

    if len(sample):
        parsed = pd.to_datetime(sample, errors="coerce")
        if parsed.notna().sum() >= max(1, int(len(sample) * 0.8)):
            return "datetime"

    unique_count = non_null.nunique()
    if len(non_null) and unique_count and unique_count <= min(12, max(3, int(len(non_null) * 0.2))):
        return "categorical"

    return "string"


def _read_text_excerpt(file_path: str, max_chars: int = 12000) -> str:
    path = Path(file_path)
    if not path.exists():
        return ""

    suffix = path.suffix.lower()

    try:
        if suffix in {".txt", ".md", ".csv", ".json", ".xml", ".rtf"}:
            return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]

        if suffix == ".docx":
            with zipfile.ZipFile(path) as archive:
                xml_bytes = archive.read("word/document.xml")
            xml_text = xml_bytes.decode("utf-8", errors="ignore")
            stripped = re.sub(r"<[^>]+>", " ", xml_text)
            return html.unescape(re.sub(r"\s+", " ", stripped)).strip()[:max_chars]
    except Exception:
        logger.debug("Could not extract text from %s", file_path, exc_info=True)

    return ""


def _collect_study_context_fragments(study) -> list[dict[str, str]]:
    fragments: list[dict[str, str]] = []
    if not study:
        return fragments

    study_description = getattr(study, "description", "") or ""
    if study_description.strip():
        fragments.append({"label": "study description", "text": study_description.strip()})

    protocol_file = getattr(study, "protocol_file", None)
    protocol_path = getattr(protocol_file, "path", "") if protocol_file else ""
    protocol_name = Path(getattr(protocol_file, "name", "protocol") or "protocol").name if protocol_file else "protocol"
    if protocol_path:
        protocol_text = _read_text_excerpt(protocol_path)
        if protocol_text:
            fragments.append({"label": protocol_name, "text": protocol_text})

    documents = getattr(study, "documents", None)
    if documents is None:
        return fragments

    try:
        iterable = documents.all()
    except Exception:
        iterable = []

    for document in iterable:
        doc_text = _read_text_excerpt(getattr(getattr(document, "file", None), "path", ""))
        doc_name = getattr(document, "filename", None) or Path(getattr(getattr(document, "file", None), "name", "document")).name
        doc_description = getattr(document, "description", "") or ""
        combined = "\n".join(part for part in [doc_description.strip(), doc_text.strip()] if part).strip()
        if combined:
            fragments.append({"label": doc_name, "text": combined[:12000]})

    return fragments


def _find_context_snippet(variable_name: str, fragments: list[dict[str, str]]) -> str:
    if not fragments:
        return ""

    tokens = {token for token in _tokenize_variable_name(variable_name) if len(token) > 2}
    variable_lower = (variable_name or "").lower()
    best_match = ""
    best_score = 0

    for fragment in fragments:
        sentences = re.split(r"(?<=[.!?])\s+|\n+", fragment["text"])
        for sentence in sentences:
            normalized = sentence.strip()
            if not normalized:
                continue
            lowered = normalized.lower()
            score = 0
            if variable_lower and variable_lower in lowered:
                score += 4
            score += sum(1 for token in tokens if token in lowered)
            if score > best_score:
                best_score = score
                best_match = f"{normalized[:220]} (source: {fragment['label']})"

    return best_match


def _normalize_documentation_snippet(snippet: str) -> str:
    cleaned = re.sub(r"\s*\(source:.*\)$", "", (snippet or "").strip())
    return re.sub(r"\s+", " ", cleaned)[:240].strip()


def _build_codebook_description(
    *,
    variable_name: str,
    display_name: str,
    variable_type: str,
    unit: str,
    documentation_snippet: str,
) -> str:
    if documentation_snippet:
        description = _normalize_documentation_snippet(documentation_snippet)
        if unit and unit.lower() not in description.lower():
            description = f"{description.rstrip('.')} Recorded in {unit}."
        return description

    noun_phrase = display_name[:1].lower() + display_name[1:] if display_name else variable_name
    descriptions_by_type = {
        "datetime": f"Records the date or time associated with {noun_phrase}.",
        "boolean": f"Indicates whether {noun_phrase} is present for the observation.",
        "categorical": f"Captures the category or coded value for {noun_phrase}.",
        "float": f"Stores the measured value for {noun_phrase}.",
        "int": f"Stores the measured value for {noun_phrase}.",
        "string": f"Stores the recorded value for {noun_phrase}.",
    }
    description = descriptions_by_type.get(variable_type, f"Stores the recorded value for {noun_phrase}.")
    if unit:
        description = f"{description.rstrip('.')} Recorded in {unit}."
    return description


def infer_variables_from_dataframe(
    df: pd.DataFrame,
    study=None,
    source_label: str = "raw data file",
) -> List[Dict[str, Any]]:
    """Build variable metadata from raw columns when a study has no codebook.

    The original column name remains the canonical variable_name. Other fields are
    derived from column names, container-local type checks, and any readable study
    documentation such as protocol text or attached documents. Row-level values are
    never copied into the generated description text.
    """
    context_fragments = _collect_study_context_fragments(study)
    variables: List[Dict[str, Any]] = []

    for column_name in df.columns:
        variable_name = str(column_name)
        series = df[column_name]
        display_name = _humanize_variable_name(variable_name)
        variable_type = _infer_variable_type_from_series(series, variable_name)
        unit = _infer_unit_from_name(variable_name)
        category = _infer_category_from_name(variable_name)
        snippet = _find_context_snippet(variable_name, context_fragments)
        description = _build_codebook_description(
            variable_name=variable_name,
            display_name=display_name,
            variable_type=variable_type,
            unit=unit,
            documentation_snippet=snippet,
        )

        variables.append(
            {
                "variable_name": variable_name,
                "display_name": display_name,
                "description": description,
                "variable_type": variable_type,
                "unit": unit,
                "ontology_code": "",
                "category": category,
            }
        )

    return variables


def infer_variables_from_latest_raw_data(study) -> tuple[List[Dict[str, Any]], Any | None]:
    """Infer variable metadata from the newest raw data file for a study."""
    from health.models import RawDataFile

    raw_data_file = (
        RawDataFile.objects.filter(study=study)
        .exclude(file="")
        .order_by("-uploaded_at")
        .first()
    )
    if not raw_data_file or not raw_data_file.file:
        return [], None

    file_path = raw_data_file.file.path
    file_format = detect_file_format(file_path)
    if file_format == "csv":
        df = pd.read_csv(file_path, nrows=50)
    elif file_format in ["excel", "xlsx"]:
        df = pd.read_excel(file_path, nrows=50)
    elif file_format == "json":
        df = pd.read_json(file_path, lines=True, nrows=50)
    else:
        raise ValueError(f"Unsupported raw data format for inference: {file_format}")

    return infer_variables_from_dataframe(
        df,
        study=study,
        source_label=raw_data_file.original_filename or Path(file_path).name,
    ), raw_data_file


def save_generated_codebook_for_study(study, attributes: Optional[List[Any]] = None) -> str:
    """Write the current study attributes to the generated codebook CSV."""
    selected_attributes = attributes or list(study.variables.order_by("variable_name"))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "variable_name",
        "display_name",
        "description",
        "variable_type",
        "unit",
        "ontology_code",
        "category",
    ])

    for attr in selected_attributes:
        writer.writerow([
            attr.variable_name,
            attr.display_name,
            attr.description,
            attr.variable_type,
            attr.unit,
            attr.ontology_code,
            attr.category,
        ])

    csv_content = output.getvalue()
    output.close()

    codebook_filename = f"codebook_{study.name.lower().replace(' ', '_')}_generated.csv"
    study.codebook.save(codebook_filename, ContentFile(csv_content.encode("utf-8")), save=False)
    study.codebook_format = "csv"
    study.save(update_fields=["codebook", "codebook_format", "updated_at"] if hasattr(study, "updated_at") else ["codebook", "codebook_format"])
    return codebook_filename


def detect_file_format(file_path: str) -> str:
    """
    Detect the format of uploaded file based on extension.
    Returns standardized format string for codebook processing.
    """
    path = Path(file_path)
    extension = path.suffix.lower()
    
    format_mapping = {
        '.csv': 'csv',
        '.xlsx': 'xlsx',
        '.xls': 'excel', 
        '.sav': 'spss',
        '.dta': 'stata',
        '.json': 'json',
        '.db': 'sqlite',
        '.sqlite': 'sqlite',
        '.sqlite3': 'sqlite',
        '.xml': 'xml',
        '.txt': 'text',
    }
    
    return format_mapping.get(extension, 'unknown')





def process_dataframe_codebook(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    Common logic for processing pandas DataFrame (used by CSV and Excel processors).
    """
    variables = []
    
    # Common column name variations (expanded for better matching)
    name_cols = [
        'variable', 'variable_name', 'var_name', 'varname', 'name', 'field', 
        'field_name', 'column', 'column_name', 'colname', 'code', 'var_id',
        'attribute', 'item', 'indicator', 'metric', 'measure'
    ]
    label_cols = [
        'label', 'display_name', 'displayname', 'variable_label', 'var_label',
        'varlabel', 'title', 'caption', 'heading', 'short_name', 'friendly_name',
        'human_name', 'readable_name', 'pretty_name'
    ]
    desc_cols = [
        'description', 'desc', 'descr', 'definition', 'detail', 'details',
        'explanation', 'info', 'information', 'notes', 'comment', 'comments',
        'long_description', 'full_description', 'help', 'help_text'
    ]
    type_cols = [
        'type', 'data_type', 'datatype', 'dtype', 'variable_type', 'vartype',
        'var_type', 'format', 'field_type', 'col_type', 'class', 'value_type'
    ]
    unit_cols = [
        'unit', 'units', 'measurement_unit', 'uom', 'unit_of_measure',
        'measurement', 'scale'
    ]
    
    # Find actual column names (case-insensitive)
    df_cols_lower = [col.lower().replace(' ', '_').replace('-', '_') for col in df.columns]
    
    def find_column(search_cols):
        """Find matching column from search list."""
        for i, col in enumerate(df_cols_lower):
            if col in [s.lower() for s in search_cols]:
                return df.columns[i]
        return None
    
    name_col = find_column(name_cols) or df.columns[0]
    label_col = find_column(label_cols)
    desc_col = find_column(desc_cols)
    type_col = find_column(type_cols)
    unit_col = find_column(unit_cols)
    
    for _, row in df.iterrows():
        var_name = str(row[name_col]).strip()
        if not var_name or var_name.lower() in ['nan', 'none', '', 'null']:
            continue
        
        # Smart display_name extraction with multiple fallbacks
        display_name = ''
        if label_col and pd.notna(row[label_col]):
            display_name = str(row[label_col]).strip()
        
        if not display_name:
            # Check if variable name is already human-readable
            if ' ' in var_name or (var_name[0].isupper() and '_' not in var_name and '-' not in var_name):
                display_name = var_name
            else:
                display_name = var_name.replace('_', ' ').replace('-', ' ').title()
        
        # Description from dedicated column or fallback to label
        description = ''
        if desc_col and pd.notna(row[desc_col]):
            description = str(row[desc_col]).strip()
        elif label_col and pd.notna(row[label_col]):
            description = str(row[label_col]).strip()
            
        variable = {
            'variable_name': var_name,
            'display_name': display_name,
            'description': description,
            'variable_type': infer_variable_type(str(row[type_col]) if type_col and pd.notna(row[type_col]) else ''),
            'unit': str(row[unit_col]).strip() if unit_col and pd.notna(row[unit_col]) else '',
            'ontology_code': '',
        }
        variables.append(variable)
        
    return variables


def normalize_variable_dict(var_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize a variable dictionary to match our expected structure.
    """
    # Common key mappings (expanded for better coverage)
    name_keys = [
        'name', 'variable_name', 'var_name', 'varname', 'field', 'field_name',
        'column', 'column_name', 'colname', 'code', 'attribute', 'item'
    ]
    label_keys = [
        'label', 'display_name', 'displayname', 'variable_label', 'var_label',
        'title', 'caption', 'heading', 'short_name', 'friendly_name'
    ]
    desc_keys = [
        'description', 'desc', 'descr', 'definition', 'detail', 'details',
        'explanation', 'info', 'notes', 'comment', 'long_description'
    ]
    type_keys = [
        'type', 'data_type', 'datatype', 'dtype', 'variable_type', 'vartype',
        'format', 'field_type', 'col_type', 'class'
    ]
    unit_keys = ['unit', 'units', 'measurement_unit', 'uom', 'unit_of_measure']
    
    def get_first_value(keys, default=''):
        for key in keys:
            if key in var_dict and var_dict[key]:
                return str(var_dict[key]).strip()
        return default
    
    var_name = get_first_value(name_keys)
    
    # Smart display_name extraction
    display_name = get_first_value(label_keys, '')
    if not display_name:
        # Check if variable name is already human-readable
        if var_name and (' ' in var_name or (var_name[0].isupper() and '_' not in var_name and '-' not in var_name)):
            display_name = var_name
        elif var_name:
            display_name = var_name.replace('_', ' ').replace('-', ' ').title()
        else:
            display_name = ''
    
    # Description from desc_keys or fallback to label
    description = get_first_value(desc_keys, '')
    if not description:
        description = get_first_value(label_keys, '')
    
    return {
        'variable_name': var_name,
        'display_name': display_name,
        'description': description,
        'variable_type': infer_variable_type(get_first_value(type_keys)),
        'unit': get_first_value(unit_keys),
        'ontology_code': var_dict.get('ontology_code', ''),
    }


def infer_variable_type(type_hint: str) -> str:
    """
    Infer variable type from various type hints.
    """
    if not type_hint:
        return 'string'
    
    type_hint = type_hint.lower().strip()
    
    # Float/numeric patterns
    if any(pattern in type_hint for pattern in ['float', 'double', 'numeric', 'decimal', 'real']):
        return 'float'
    
    # Integer patterns
    if any(pattern in type_hint for pattern in ['int', 'integer', 'whole', 'count']):
        return 'int'
    
    # Boolean patterns
    if any(pattern in type_hint for pattern in ['bool', 'boolean', 'logical', 'binary', 'yes/no']):
        return 'boolean'
    
    # Date/time patterns
    if any(pattern in type_hint for pattern in ['date', 'time', 'datetime', 'timestamp']):
        return 'datetime'
    
    # Categorical patterns
    if any(pattern in type_hint for pattern in ['categorical', 'factor', 'enum', 'choice']):
        return 'categorical'
    
    # Default to string
    return 'string'


def sqlite_type_to_variable_type(sqlite_type: str) -> str:
    """
    Convert SQLite column type to our variable type.
    """
    if not sqlite_type:
        return 'string'
    
    sqlite_type = sqlite_type.upper()
    
    if 'INT' in sqlite_type:
        return 'int'
    elif any(t in sqlite_type for t in ['REAL', 'FLOAT', 'DOUBLE']):
        return 'float'
    elif 'BOOL' in sqlite_type:
        return 'boolean'
    elif any(t in sqlite_type for t in ['DATE', 'TIME']):
        return 'datetime'
    else:
        return 'string'


def validate_variables(variables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Validate and clean extracted variables.
    """
    valid_variables = []
    
    for var in variables:
        # Skip variables without names
        if not var.get('variable_name'):
            continue
        
        # Ensure all required fields exist
        var.setdefault('display_name', var['variable_name'].replace('_', ' ').title())
        var.setdefault('description', '')
        var.setdefault('variable_type', 'string')
        var.setdefault('unit', '')
        var.setdefault('category', 'health')
        var.setdefault('ontology_code', '')
        
        # Validate variable_type
        valid_types = ['float', 'int', 'string', 'categorical', 'boolean', 'datetime']
        if var['variable_type'] not in valid_types:
            var['variable_type'] = 'string'
        
        valid_variables.append(var)
    
    return valid_variables


def extract_variables_from_codebook(file_path: str, column_mapping: Dict[str, str]) -> List[Dict[str, Any]]:
    """
    Extract variables from codebook using user-defined column mapping.
    Simple approach that reads the mapped columns directly.
    """
    try:
        file_format = detect_file_format(file_path)
        
        # Read the file
        if file_format == 'csv':
            df = pd.read_csv(file_path)
        elif file_format in ['excel', 'xlsx']:
            df = pd.read_excel(file_path)
        else:
            raise ValueError(f"Unsupported format: {file_format}")
        
        variables = []
        
        # Get the mapped column name for variable names (required)
        var_name_col = column_mapping.get('variable_name')
        if not var_name_col or var_name_col not in df.columns:
            raise ValueError("Variable name column must be specified and exist in the file")
        
        # Get display_name column for fallback logic
        display_name_col = column_mapping.get('display_name')
        
        # Process each row
        for _, row in df.iterrows():
            var_name = str(row[var_name_col]).strip()
            if not var_name or var_name.lower() in ['nan', 'none', '']:
                continue
            
            # Smart display_name extraction with multiple fallbacks:
            # 1. Use mapped display_name column if it has a value
            # 2. Use variable name as-is if it looks like a readable name (has spaces/proper case)
            # 3. Convert variable_name to title case (replace underscores with spaces)
            display_name_value = _get_column_value(row, display_name_col, df.columns, '')
            
            if not display_name_value:
                # Check if variable name is already human-readable
                # (contains spaces, or is already in proper case with no underscores)
                if ' ' in var_name or (var_name[0].isupper() and '_' not in var_name and '-' not in var_name):
                    display_name_value = var_name
                else:
                    # Convert snake_case or kebab-case to Title Case
                    display_name_value = var_name.replace('_', ' ').replace('-', ' ').title()
            
            # Extract data from mapped columns (with fallbacks)
            variable = {
                'variable_name': var_name,
                'display_name': display_name_value,
                'description': _get_column_value(row, column_mapping.get('description'), df.columns, ''),
                'variable_type': infer_variable_type(_get_column_value(row, column_mapping.get('variable_type'), df.columns, '')),
                'unit': _get_column_value(row, column_mapping.get('unit'), df.columns, ''),
                'ontology_code': _get_column_value(row, column_mapping.get('ontology_code'), df.columns, ''),
                'category': _get_column_value(row, column_mapping.get('category'), df.columns, ''),
            }
            variables.append(variable)
        
        logger.info(f"Extracted {len(variables)} variables using column mapping")
        return variables
        
    except Exception as e:
        logger.error(f"Error extracting variables from codebook: {str(e)}")
        raise


def process_codebook_mapping(request, study, codebook_type='source'):
    """
    Unified function to handle codebook column mapping for both source and target studies.
    
    Args:
        request: Django request object
        study: Study instance
        codebook_type: 'source' or 'target'
    
    Returns:
        Tuple of (df, detected_format, context) or redirect response
    """
    if not study.codebook:
        try:
            inferred_variables, raw_data_file = infer_variables_from_latest_raw_data(study)
        except Exception as exc:
            logger.warning("Could not infer variables from raw data for study %s: %s", study.pk, exc)
            inferred_variables, raw_data_file = [], None

        if inferred_variables:
            session_variables_key = (
                f'{codebook_type}_variables_data_{study.id}'
                if codebook_type == 'target'
                else f'variables_data_{study.id}'
            )
            request.session[session_variables_key] = inferred_variables
            request.session[f'extract_from_raw_{study.id}'] = True
            messages.info(
                request,
                (
                    f'No codebook was found, so HarmonAIze drafted {len(inferred_variables)} '
                    f'variable definitions from the latest raw data file '
                    f'({raw_data_file.original_filename if raw_data_file else "raw upload"}) '
                    f'using column names, local type checks, and any available protocol or study documents without copying row-level values into the generated codebook.'
                ),
            )
            if codebook_type == 'target':
                return redirect('core:target_select_variables', study_id=study.id)
            return redirect('health:select_variables', study_id=study.id)

        messages.error(
            request,
            f'No codebook file found for this {codebook_type} study. Upload a raw data file or add protocol documentation so variable metadata can be inferred.',
        )
        return redirect('core:study_detail', pk=study.pk)
    
    try:
        # Analyse the codebook file structure
        file_path = study.codebook.path
        detected_format = detect_file_format(file_path)
        
        # Read first few rows to show user the structure
        if detected_format == 'csv':
            df = pd.read_csv(file_path, nrows=5)
        elif detected_format in ['excel', 'xlsx']:
            df = pd.read_excel(file_path, nrows=5)
        else:
            messages.error(request, f'Unsupported file format: {detected_format}')
            return redirect('core:study_detail', pk=study.pk)
        
        # Update study with detected format
        study.codebook_format = detected_format
        study.save()
        
        if request.method == 'POST':
            # User has mapped the columns, store mapping and proceed
            column_mapping = {
                'variable_name': request.POST.get('variable_name_column'),
                'display_name': request.POST.get('display_name_column'),
                'description': request.POST.get('description_column'),
                'variable_type': request.POST.get('variable_type_column'),
                'unit': request.POST.get('unit_column'),
                'ontology_code': request.POST.get('ontology_code_column'),
                'category': request.POST.get('category_column'),
            }
            
            # Validate that at least variable_name is mapped
            if not column_mapping['variable_name']:
                messages.error(request, 'Variable name column mapping is required.')
                
                # Re-generate suggestions for error display
                from health.utils import suggest_column_mappings
                import json
                
                codebook_columns = df.columns.tolist()
                expected_fields = ['variable_name', 'display_name', 'description', 'variable_type', 
                                  'unit', 'ontology_code', 'category']
                suggestions = suggest_column_mappings(codebook_columns, expected_fields)
                suggested_mappings = {}
                for suggestion in suggestions:
                    if suggestion['confidence'] in ['high', 'medium']:
                        suggested_mappings[suggestion['suggested_variable']] = suggestion['column_name']
                
                context = {
                    'study': study,
                    'columns': df.columns.tolist(),
                    'sample_data': df.to_dict('records'),
                    'detected_format': detected_format,
                    'page_title': f'Map {codebook_type.title()} Codebook - {study.name}',
                    'study_type': codebook_type,
                    'suggested_mappings': json.dumps(suggested_mappings),  # Serialize as JSON
                }
                return context
            
            # Store mapping in session and proceed to variable extraction
            session_key = f'{codebook_type}_column_mapping_{study.id}' if codebook_type == 'target' else f'column_mapping_{study.id}'
            request.session[session_key] = column_mapping
            messages.success(request, f'Column mapping saved! Proceeding to extract {codebook_type} variables.')
            
            # Return extraction URL based on type
            if codebook_type == 'target':
                return redirect('core:target_extract_variables', study_id=study.id)
            else:
                return redirect('health:extract_variables', study_id=study.id)
        
        # Generate column mapping suggestions
        from health.utils import suggest_column_mappings
        import json
        
        codebook_columns = df.columns.tolist()
        
        # Define expected attribute field names
        expected_fields = ['variable_name', 'display_name', 'description', 'variable_type', 
                          'unit', 'ontology_code', 'category']
        
        # Get automated suggestions for matching
        suggestions = suggest_column_mappings(codebook_columns, expected_fields)
        
        # Create a mapping of field names to suggested column names (high confidence only)
        suggested_mappings = {}
        for suggestion in suggestions:
            if suggestion['confidence'] in ['high', 'medium']:
                # Map the field name to the column name
                suggested_mappings[suggestion['suggested_variable']] = suggestion['column_name']
        
        # Show column mapping interface
        context = {
            'study': study,
            'columns': df.columns.tolist(),
            'sample_data': df.to_dict('records'),
            'detected_format': detected_format,
            'page_title': f'Map {codebook_type.title()} Codebook - {study.name}',
            'study_type': codebook_type,
            'suggested_mappings': json.dumps(suggested_mappings),  # Serialize as JSON
        }
        
        return context
        
    except Exception as e:
        messages.error(
            request,
            f'Error analysing {codebook_type} codebook: {str(e)}. Please check your file format and try again.'
        )
        return redirect('core:study_detail', pk=study.pk)


def process_codebook_extraction(request, study, codebook_type='source'):
    """
    Unified function to handle variable extraction for both source and target studies.
    
    Args:
        request: Django request object
        study: Study instance
        codebook_type: 'source' or 'target'
    
    Returns:
        Redirect response
    """
    # Get column mapping from session
    session_key = f'{codebook_type}_column_mapping_{study.id}' if codebook_type == 'target' else f'column_mapping_{study.id}'
    column_mapping = request.session.get(session_key)
    
    if not column_mapping:
        messages.error(request, f'No column mapping found. Please map your {codebook_type} codebook columns first.')
        if codebook_type == 'target':
            return redirect('core:target_map_codebook', study_id=study.id)
        else:
            return redirect('health:map_codebook', study_id=study.id)
    
    try:
        # Extract variables using the column mapping
        file_path = study.codebook.path
        variables_data = extract_variables_from_codebook(file_path, column_mapping)
        
        # Store extracted variables in session
        session_variables_key = f'{codebook_type}_variables_data_{study.id}' if codebook_type == 'target' else f'variables_data_{study.id}'
        request.session[session_variables_key] = variables_data
        
        messages.success(
            request,
            f'Successfully extracted {len(variables_data)} {codebook_type} variables from your codebook! '
            f'Review and select which variables to include {"as harmonisation targets" if codebook_type == "target" else "in your study"}.'
        )
        
        # Return selection URL based on type
        if codebook_type == 'target':
            return redirect('core:target_select_variables', study_id=study.id)
        else:
            return redirect('health:select_variables', study_id=study.id)
        
    except Exception as e:
        messages.error(
            request,
            f'Error extracting {codebook_type} variables: {str(e)}. Please check your column mapping.'
        )
        if codebook_type == 'target':
            return redirect('core:target_map_codebook', study_id=study.id)
        else:
            return redirect('health:map_codebook', study_id=study.id)


def _get_column_value(row, column_name: Optional[str], available_columns: List[str], default_value: str = '') -> str:
    """
    Helper function to safely get value from a mapped column.
    """
    if column_name and column_name in available_columns and pd.notna(row[column_name]):
        return str(row[column_name]).strip()
    return default_value
