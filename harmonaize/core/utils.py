"""
Utility functions for processing codebooks and extracting variable information.
"""
import pandas as pd
import sqlite3
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import logging
from django.shortcuts import redirect
from django.contrib import messages

logger = logging.getLogger(__name__)


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
        messages.error(request, f'No codebook file found for this {codebook_type} study.')
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
