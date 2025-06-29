"""
Utility functions for processing codebooks and extracting variable information.
"""
import pandas as pd
from pathlib import Path
from typing import Dict, List, Optional, Any
import logging
from django.shortcuts import redirect
from django.contrib import messages

logger = logging.getLogger(__name__)


def detect_file_format(file_path: str) -> str:
    """
    Detect the format of uploaded file based on extension.
    """
    path = Path(file_path)
    extension = path.suffix.lower()
    
    format_mapping = {
        '.csv': 'csv',
        '.xlsx': 'excel',
        '.xls': 'excel', 
        '.sav': 'spss',
        '.dta': 'stata',
        '.json': 'json',
        '.db': 'sqlite',
        '.sqlite': 'sqlite',
        '.xml': 'xml',
        '.txt': 'text',
    }
    
    return format_mapping.get(extension, 'unknown')


def infer_variable_type(type_hint: str) -> str:
    """
    Infer variable type from string hint.
    """
    if not type_hint:
        return 'string'
    
    type_hint = str(type_hint).lower().strip()
    
    # Numeric types
    if any(word in type_hint for word in ['int', 'integer', 'numeric', 'number']):
        return 'int'
    elif any(word in type_hint for word in ['float', 'double', 'decimal', 'real']):
        return 'float'
    elif any(word in type_hint for word in ['bool', 'boolean', 'binary']):
        return 'boolean'
    elif any(word in type_hint for word in ['date', 'time', 'datetime', 'timestamp']):
        return 'datetime'
    elif any(word in type_hint for word in ['categorical', 'category', 'factor', 'enum']):
        return 'categorical'
    else:
        return 'string'


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
        
        # Process each row
        for _, row in df.iterrows():
            var_name = str(row[var_name_col]).strip()
            if not var_name or var_name.lower() in ['nan', 'none', '']:
                continue
            
            # Extract data from mapped columns (with fallbacks)
            variable = {
                'variable_name': var_name,
                'display_name': _get_column_value(row, column_mapping.get('display_name'), df.columns, var_name.replace('_', ' ').title()),
                'description': _get_column_value(row, column_mapping.get('description'), df.columns, ''),
                'variable_type': infer_variable_type(_get_column_value(row, column_mapping.get('variable_type'), df.columns, '')),
                'unit': _get_column_value(row, column_mapping.get('unit'), df.columns, ''),
                'ontology_code': '',
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
        Either a redirect response or context dictionary for rendering
    """
    if not study.codebook:
        messages.error(request, f'No {codebook_type} codebook found. Please upload a codebook first.')
        return redirect('core:study_detail', pk=study.pk)

    try:
        # Read the first few rows for preview
        file_path = study.codebook.path
        detected_format = detect_file_format(file_path)
        
        if detected_format == 'csv':
            df = pd.read_csv(file_path, nrows=5)
        elif detected_format in ['excel', 'xlsx']:
            df = pd.read_excel(file_path, nrows=5)
        else:
            messages.error(request, f'Unsupported file format: {detected_format}')
            return redirect('core:study_detail', pk=study.pk)
        
        columns = df.columns.tolist()
        sample_data = df.to_dict('records')
        
        if request.method == 'POST':
            # Process form submission
            column_mapping = {}
            
            # Required field
            var_name_col = request.POST.get('variable_name_column')
            if not var_name_col:
                messages.error(request, 'Variable name column is required.')
            elif var_name_col not in columns:
                messages.error(request, f'Column "{var_name_col}" not found in codebook.')
            else:
                column_mapping['variable_name'] = var_name_col
                
                # Optional fields
                for field in ['display_name', 'description', 'variable_type', 'unit']:
                    col = request.POST.get(f'{field}_column')
                    if col and col in columns:
                        column_mapping[field] = col
                
                # Store mapping in session with appropriate key
                session_key = f'{codebook_type}_column_mapping_{study.id}' if codebook_type == 'target' else f'column_mapping_{study.id}'
                request.session[session_key] = column_mapping
                
                messages.success(request, f'{codebook_type.title()} codebook columns mapped successfully!')
                
                # Redirect to extraction step
                if codebook_type == 'target':
                    return redirect('core:target_extract_variables', study_id=study.id)
                else:
                    return redirect('health:extract_variables', study_id=study.id)
        
        # Return context for GET request or form errors
        context = {
            'study': study,
            'columns': columns,
            'sample_data': sample_data,
            'detected_format': detected_format,
            'codebook_type': codebook_type,
            'page_title': f'Map {codebook_type.title()} Codebook - {study.name}'
        }
        
        return context
        
    except Exception as e:
        logger.error(f"Error processing {codebook_type} codebook mapping: {str(e)}")
        messages.error(request, f'Error reading {codebook_type} codebook: {str(e)}')
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
