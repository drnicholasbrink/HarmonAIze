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


def process_csv_codebook(file_path: str) -> List[Dict[str, Any]]:
    """
    Process CSV codebook file and extract variable information.
    Assumes common CSV codebook structures.
    """
    try:
        df = pd.read_csv(file_path)
        variables = []
        
        # Common column name variations
        name_cols = ['variable', 'variable_name', 'var_name', 'name', 'field']
        label_cols = ['label', 'description', 'variable_label', 'desc']
        type_cols = ['type', 'data_type', 'variable_type', 'format']
        unit_cols = ['unit', 'units', 'measurement_unit']
        
        # Find actual column names
        name_col = next((col for col in name_cols if col in df.columns), df.columns[0])
        label_col = next((col for col in label_cols if col in df.columns), None)
        type_col = next((col for col in type_cols if col in df.columns), None)
        unit_col = next((col for col in unit_cols if col in df.columns), None)
        
        for _, row in df.iterrows():
            var_name = str(row[name_col]).strip()
            if not var_name or var_name.lower() in ['nan', 'none', '']:
                continue
                
            variable = {
                'variable_name': var_name,
                'display_name': str(row[label_col]).strip() if label_col and pd.notna(row[label_col]) else var_name,
                'description': str(row[label_col]).strip() if label_col and pd.notna(row[label_col]) else '',
                'variable_type': infer_variable_type(str(row[type_col]) if type_col and pd.notna(row[type_col]) else ''),
                'unit': str(row[unit_col]).strip() if unit_col and pd.notna(row[unit_col]) else '',
                'ontology_code': '',
            }
            variables.append(variable)
            
        return variables
        
    except Exception as e:
        logger.error(f"Error processing CSV codebook: {str(e)}")
        raise


def process_excel_codebook(file_path: str) -> List[Dict[str, Any]]:
    """
    Process Excel codebook file and extract variable information.
    """
    try:
        # Try to read the first sheet
        df = pd.read_excel(file_path, sheet_name=0)
        
        # Use similar logic as CSV processing
        return process_dataframe_codebook(df)
        
    except Exception as e:
        logger.error(f"Error processing Excel codebook: {str(e)}")
        raise


def process_json_codebook(file_path: str) -> List[Dict[str, Any]]:
    """
    Process JSON codebook file and extract variable information.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        variables = []
        
        # Handle different JSON structures
        if isinstance(data, list):
            # Array of variable objects
            for item in data:
                if isinstance(item, dict):
                    variables.append(normalize_variable_dict(item))
        elif isinstance(data, dict):
            if 'variables' in data:
                # Nested structure with variables key
                for var_data in data['variables']:
                    variables.append(normalize_variable_dict(var_data))
            else:
                # Flat structure - each key is a variable
                for var_name, var_data in data.items():
                    if isinstance(var_data, dict):
                        var_info = normalize_variable_dict(var_data)
                        var_info['variable_name'] = var_name
                        variables.append(var_info)
        
        return variables
        
    except Exception as e:
        logger.error(f"Error processing JSON codebook: {str(e)}")
        raise


def process_sqlite_codebook(file_path: str) -> List[Dict[str, Any]]:
    """
    Process SQLite database file and extract table schema as variables.
    """
    try:
        conn = sqlite3.connect(file_path)
        cursor = conn.cursor()
        
        # Get all table names
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        
        variables = []
        
        for table_name, in tables:
            # Get column information for each table
            cursor.execute(f"PRAGMA table_info({table_name});")
            columns = cursor.fetchall()
            
            for col_info in columns:
                cid, name, data_type, not_null, default_value, pk = col_info
                
                variable = {
                    'variable_name': f"{table_name}.{name}",
                    'display_name': name.replace('_', ' ').title(),
                    'description': f"Column {name} from table {table_name}",
                    'variable_type': sqlite_type_to_variable_type(data_type),
                    'unit': '',
                    'ontology_code': '',
                }
                variables.append(variable)
        
        conn.close()
        return variables
        
    except Exception as e:
        logger.error(f"Error processing SQLite codebook: {str(e)}")
        raise


def process_dataframe_codebook(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    Common logic for processing pandas DataFrame (used by CSV and Excel processors).
    """
    variables = []
    
    # Common column name variations
    name_cols = ['variable', 'variable_name', 'var_name', 'name', 'field', 'column']
    label_cols = ['label', 'description', 'variable_label', 'desc', 'display_name']
    type_cols = ['type', 'data_type', 'variable_type', 'format', 'dtype']
    unit_cols = ['unit', 'units', 'measurement_unit', 'uom']
    
    # Find actual column names (case-insensitive)
    df_cols_lower = [col.lower() for col in df.columns]
    
    name_col = next((df.columns[i] for i, col in enumerate(df_cols_lower) 
                    if col in [n.lower() for n in name_cols]), df.columns[0])
    label_col = next((df.columns[i] for i, col in enumerate(df_cols_lower) 
                     if col in [n.lower() for n in label_cols]), None)
    type_col = next((df.columns[i] for i, col in enumerate(df_cols_lower) 
                    if col in [n.lower() for n in type_cols]), None)
    unit_col = next((df.columns[i] for i, col in enumerate(df_cols_lower) 
                    if col in [n.lower() for n in unit_cols]), None)
    
    for _, row in df.iterrows():
        var_name = str(row[name_col]).strip()
        if not var_name or var_name.lower() in ['nan', 'none', '', 'null']:
            continue
            
        variable = {
            'variable_name': var_name,
            'display_name': str(row[label_col]).strip() if label_col and pd.notna(row[label_col]) else var_name.replace('_', ' ').title(),
            'description': str(row[label_col]).strip() if label_col and pd.notna(row[label_col]) else '',
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
    # Common key mappings
    name_keys = ['name', 'variable_name', 'var_name', 'field', 'column']
    label_keys = ['label', 'display_name', 'description', 'desc']
    type_keys = ['type', 'data_type', 'variable_type', 'dtype']
    unit_keys = ['unit', 'units', 'measurement_unit']
    
    def get_first_value(keys, default=''):
        for key in keys:
            if key in var_dict and var_dict[key]:
                return str(var_dict[key]).strip()
        return default
    
    var_name = get_first_value(name_keys)
    
    return {
        'variable_name': var_name,
        'display_name': get_first_value(label_keys, var_name.replace('_', ' ').title()),
        'description': get_first_value(label_keys),
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


def process_codebook(file_path: str, file_format: str = None) -> Tuple[List[Dict[str, Any]], str]:
    """
    Main function to process a codebook file and extract variable information.
    
    Returns:
        Tuple of (variables_list, detected_format)
    """
    if not file_format:
        file_format = detect_file_format(file_path)
    
    try:
        if file_format == 'csv':
            variables = process_csv_codebook(file_path)
        elif file_format == 'excel':
            variables = process_excel_codebook(file_path)
        elif file_format == 'json':
            variables = process_json_codebook(file_path)
        elif file_format == 'sqlite':
            variables = process_sqlite_codebook(file_path)
        else:
            # For unknown formats, try to read as CSV first
            try:
                variables = process_csv_codebook(file_path)
                file_format = 'csv'
            except:
                raise ValueError(f"Unsupported file format: {file_format}")
        
        logger.info(f"Successfully processed {len(variables)} variables from {file_format} codebook")
        return variables, file_format
        
    except Exception as e:
        logger.error(f"Error processing codebook: {str(e)}")
        raise


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


def _get_column_value(row, column_name: Optional[str], available_columns: List[str], default_value: str = '') -> str:
    """
    Helper function to safely get value from a mapped column.
    """
    if column_name and column_name in available_columns and pd.notna(row[column_name]):
        return str(row[column_name]).strip()
    return default_value
