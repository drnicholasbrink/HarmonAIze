"""
Utility functions for health data processing and validation.
"""
import pandas as pd
import logging
from typing import Dict, Any, List
from django.core.files.uploadedfile import UploadedFile
from django.contrib import messages
from django.http import HttpRequest
from core.models import Study

logger = logging.getLogger(__name__)


class MessageManager:
    """
    Centralized message management to prevent overwhelming users with notifications.
    """
    
    @staticmethod
    def add_message(request: HttpRequest, level: str, message: str, force: bool = False) -> bool:
        """
        Add a message with deduplication and rate limiting.
        
        Args:
            request: The HTTP request object
            level: Message level ('success', 'error', 'warning', 'info')
            message: The message content
            force: If True, bypass deduplication checks
            
        Returns:
            bool: True if message was added, False if filtered out
        """
        # Initialize session message tracking if not exists
        if '_messages_seen' not in request.session:
            request.session['_messages_seen'] = []
        
        # Create a simple hash of the message for deduplication
        message_hash = hash(f"{level}:{message.strip()}")
        
        # Check if we've seen this message recently (unless forced)
        seen_messages = set(request.session.get('_messages_seen', []))
        if not force and message_hash in seen_messages:
            return False

        # Add the message
        message_func = getattr(messages, level, messages.info)
        message_func(request, message)

        # Track the message to prevent future duplicates
        seen_messages.add(message_hash)

        # Limit tracking to last 20 messages to prevent session bloat
        if len(seen_messages) > 20:
            seen_messages = set(list(seen_messages)[-20:])

        request.session['_messages_seen'] = list(seen_messages)
        request.session.modified = True
        
        return True
    
    @staticmethod
    def success(request: HttpRequest, message: str, force: bool = False) -> bool:
        """Add a success message with deduplication."""
        return MessageManager.add_message(request, 'success', message, force)
    
    @staticmethod
    def error(request: HttpRequest, message: str, force: bool = False) -> bool:
        """Add an error message with deduplication.""" 
        return MessageManager.add_message(request, 'error', message, force)
    
    @staticmethod
    def warning(request: HttpRequest, message: str, force: bool = False) -> bool:
        """Add a warning message with deduplication."""
        return MessageManager.add_message(request, 'warning', message, force)
    
    @staticmethod
    def info(request: HttpRequest, message: str, force: bool = False) -> bool:
        """Add an info message with deduplication."""
        return MessageManager.add_message(request, 'info', message, force)
    
    @staticmethod
    def clear_seen_messages(request: HttpRequest):
        """Clear the message deduplication cache."""
        if '_messages_seen' in request.session:
            del request.session['_messages_seen']
            request.session.modified = True


def validate_raw_data_against_codebook(file: UploadedFile, study: Study) -> Dict[str, Any]:
    """
    Validate that the uploaded raw data file structure matches the study's codebook variables.
    
    This is a basic validation that checks:
    1. File can be read successfully
    2. Basic structure and column count
    3. Column names match or are compatible with study variables (future enhancement)
    
    Args:
        file: The uploaded file to validate
        study: The study to validate against
        
    Returns:
        dict: Validation result with 'is_valid' boolean and 'message' string
    """
    try:
        # Get the expected variables from the study's codebook
        expected_variables = list(study.variables.all().values_list('variable_name', flat=True))
        
        if not expected_variables:
            return {
                'is_valid': False,
                'message': 'Study has no variables defined in codebook. Please extract variables from codebook first.'
            }
        
        # Read the file to check its structure
        file_ext = file.name.split('.')[-1].lower()
        
        try:
            if file_ext == 'csv':
                df = pd.read_csv(file, nrows=5)  # Just read first few rows for validation
            elif file_ext in ['xlsx', 'xls']:
                df = pd.read_excel(file, nrows=5)
            elif file_ext == 'json':
                df = pd.read_json(file, lines=True, nrows=5)
            else:
                return {
                    'is_valid': False,
                    'message': f'File format {file_ext} not supported for validation.'
                }
        except Exception as e:
            return {
                'is_valid': False,
                'message': f'Could not read file: {str(e)}'
            }
        
        # Basic structure validation
        if df.empty:
            return {
                'is_valid': False,
                'message': 'File appears to be empty.'
            }
        
        # Get actual columns from the file
        actual_columns = list(df.columns)
        
        # For now, just log the comparison - in the future we can be more strict
        logger.info(f"Expected variables: {expected_variables}")
        logger.info(f"Actual columns: {actual_columns}")
        
        # Basic validation: check if file has reasonable number of columns
        if len(actual_columns) == 0:
            return {
                'is_valid': False,
                'message': 'File has no columns.'
            }
        
        # Future enhancement: More sophisticated column matching
        # For now, we'll allow the upload as long as the file is readable
        # and has some columns
        
        return {
            'is_valid': True,
            'message': f'File validation passed. Found {len(actual_columns)} columns in data file.',
            'details': {
                'expected_variables': expected_variables,
                'actual_columns': actual_columns,
                'file_rows': len(df),
                'file_columns': len(actual_columns)
            }
        }
        
    except Exception as e:
        logger.error(f"Error validating raw data file {file.name}: {str(e)}")
        return {
            'is_valid': False,
            'message': f'Validation error: {str(e)}'
        }


def analyze_raw_data_columns(file_path: str) -> Dict[str, Any]:
    """
    Analyze the columns in a raw data file to help with column mapping.
    
    Args:
        file_path: Path to the raw data file
        
    Returns:
        dict: Analysis results including column names, types, and sample data
    """
    try:
        # Detect file format and read
        file_ext = file_path.split('.')[-1].lower()
        
        # First, get the total row count without loading all data
        if file_ext == 'csv':
            # Count rows efficiently for CSV
            with open(file_path, 'r') as f:
                total_rows = sum(1 for _ in f) - 1  # -1 for header
            df = pd.read_csv(file_path, nrows=100)  # Read first 100 rows for analysis
        elif file_ext in ['xlsx', 'xls']:
            # For Excel, need to read to count (less efficient but necessary)
            df_full = pd.read_excel(file_path)
            total_rows = len(df_full)
            df = df_full.head(100)
            del df_full  # Free memory
        elif file_ext == 'json':
            # For JSON lines, count lines
            with open(file_path, 'r') as f:
                total_rows = sum(1 for _ in f)
            df = pd.read_json(file_path, lines=True, nrows=100)
        else:
            raise ValueError(f"Unsupported file format: {file_ext}")
        
        # Analyze columns
        column_analysis = {}
        for col in df.columns:
            col_data = df[col].dropna()
            
            column_analysis[col] = {
                'data_type': str(col_data.dtype),
                'non_null_count': len(col_data),
                'null_count': df[col].isnull().sum(),
                'unique_count': col_data.nunique(),
                'sample_values': col_data.head(5).tolist() if len(col_data) > 0 else []
            }
        
        return {
            'success': True,
            'total_rows': total_rows,  # Actual total rows from file
            'total_columns': len(df.columns),
            'columns': list(df.columns),
            'column_analysis': column_analysis,
            'sample_data': df.head(3).to_dict('records')
        }
        
    except Exception as e:
        logger.error(f"Error analyzing raw data file {file_path}: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }


def suggest_column_mappings(raw_columns: List[str], study_variables: List[str]) -> List[dict]:
    """
    Suggest mappings between raw data columns and study variables using enhanced matching.
    
    This function is used for two purposes:
    1. Mapping codebook columns to schema fields (variable_name, display_name, etc.)
    2. Mapping raw data columns to study variables
    
    Args:
        raw_columns: List of column names from raw data file or codebook
        study_variables: List of variable names or expected field names
        
    Returns:
        List[dict]: List of suggestions with column_name, suggested_variable, confidence, reason
    """
    suggestions = []
    
    # Define common synonyms for schema field mapping
    # This helps when mapping codebook columns to expected attribute fields
    field_synonyms = {
        'variable_name': [
            'variable', 'var', 'var_name', 'varname', 'variable_name', 'variablename',
            'name', 'field', 'field_name', 'fieldname', 'column', 'column_name', 
            'colname', 'col_name', 'attribute', 'attr', 'code', 'var_id', 'varid',
            'item', 'item_name', 'indicator', 'metric', 'measure', 'variable_id'
        ],
        'display_name': [
            'display_name', 'displayname', 'display', 'label', 'variable_label',
            'var_label', 'varlabel', 'title', 'caption', 'heading', 'header',
            'short_name', 'shortname', 'friendly_name', 'human_name', 'readable_name',
            'pretty_name', 'ui_name', 'ui_label', 'full_name', 'display_label'
        ],
        'description': [
            'description', 'desc', 'descr', 'describe', 'definition', 'detail',
            'details', 'explanation', 'info', 'information', 'notes', 'comment',
            'comments', 'remark', 'remarks', 'long_description', 'full_description',
            'variable_description', 'var_desc', 'help', 'help_text', 'tooltip'
        ],
        'variable_type': [
            'type', 'data_type', 'datatype', 'dtype', 'variable_type', 'vartype',
            'var_type', 'format', 'field_type', 'fieldtype', 'col_type', 'coltype',
            'class', 'data_class', 'storage_type', 'measure_type', 'value_type'
        ],
        'unit': [
            'unit', 'units', 'uom', 'measurement_unit', 'measure_unit', 'unit_of_measure',
            'measurement', 'scale', 'unit_type', 'unit_name', 'unit_label'
        ],
        'ontology_code': [
            'ontology', 'ontology_code', 'ontology_id', 'code', 'standard_code',
            'snomed', 'snomed_code', 'snomed_ct', 'loinc', 'loinc_code', 'icd',
            'icd_code', 'icd10', 'icd_10', 'mesh', 'mesh_code', 'umls', 'umls_code',
            'concept_id', 'concept_code', 'terminology_code', 'standard_id'
        ],
        'category': [
            'category', 'cat', 'group', 'grouping', 'section', 'domain', 'area',
            'topic', 'theme', 'class', 'classification', 'module', 'block',
            'variable_category', 'var_category', 'data_category', 'type_category'
        ]
    }
    
    for raw_col in raw_columns:
        raw_col_lower = raw_col.lower().strip().replace(' ', '_').replace('-', '_')
        best_match = None
        best_confidence = 0.0
        best_reason = ""
        
        for study_var in study_variables:
            study_var_lower = study_var.lower().strip()
            confidence = 0.0
            reason = ""
            
            # Check for field synonym matches (highest priority for schema mapping)
            synonyms = field_synonyms.get(study_var_lower, [])
            if synonyms:
                # Check exact synonym match (highest confidence)
                if raw_col_lower in synonyms:
                    confidence = 0.95
                    reason = f"Exact match: '{raw_col}' is a known synonym for '{study_var}'"
                else:
                    # For substring matches, prioritize longer/more specific synonyms
                    # Sort synonyms by length descending to check more specific ones first
                    sorted_synonyms = sorted(synonyms, key=len, reverse=True)
                    
                    for syn in sorted_synonyms:
                        # Only match if the synonym is meaningful (at least 4 chars)
                        # and forms a significant part of the column name
                        if len(syn) >= 4 and syn in raw_col_lower:
                            # Calculate how much of the column name the synonym covers
                            coverage = len(syn) / len(raw_col_lower)
                            # Higher confidence for higher coverage
                            confidence = 0.75 + (coverage * 0.15)  # Range: 0.75-0.90
                            reason = f"Contains synonym '{syn}' for '{study_var}'"
                            break
                    
                    # If no long synonym match, try shorter ones but with lower confidence
                    if confidence == 0.0:
                        for syn in sorted_synonyms:
                            if len(syn) >= 3 and syn in raw_col_lower:
                                confidence = 0.65
                                reason = f"Contains short synonym '{syn}' for '{study_var}'"
                                break
                    
                    # Check if column name is contained in any synonym (reverse match)
                    if confidence == 0.0 and len(raw_col_lower) >= 3:
                        for syn in synonyms:
                            if raw_col_lower in syn:
                                confidence = 0.70
                                reason = f"Column '{raw_col}' matches part of synonym pattern"
                                break
            
            # If no synonym match, try standard matching
            if confidence == 0.0:
                # Exact match (highest confidence)
                if raw_col_lower == study_var_lower:
                    confidence = 1.0
                    reason = "Exact name match"
                # One contains the other (high confidence)
                elif raw_col_lower in study_var_lower:
                    confidence = 0.8
                    reason = f"Column name '{raw_col}' found in variable '{study_var}'"
                elif study_var_lower in raw_col_lower:
                    confidence = 0.8
                    reason = f"Variable name '{study_var}' found in column '{raw_col}'"
                # Similar words/patterns (medium confidence)
                else:
                    # Check for common word patterns
                    raw_words = set(raw_col_lower.replace('_', ' ').replace('-', ' ').split())
                    var_words = set(study_var_lower.replace('_', ' ').replace('-', ' ').split())
                    
                    # Calculate word overlap
                    common_words = raw_words.intersection(var_words)
                    if common_words and len(common_words) >= 1:
                        overlap_ratio = len(common_words) / max(len(raw_words), len(var_words))
                        confidence = 0.3 + (overlap_ratio * 0.4)  # 0.3-0.7 range
                        reason = f"Common words: {', '.join(common_words)}"
            
            # Keep track of best match for this column
            if confidence > best_confidence:
                best_confidence = confidence
                best_match = study_var
                best_reason = reason
        
        # Only include suggestions above minimum confidence threshold
        if best_match and best_confidence >= 0.3:
            # Determine confidence category for display
            if best_confidence >= 0.8:
                confidence_label = "high"
            elif best_confidence >= 0.5:
                confidence_label = "medium" 
            else:
                confidence_label = "low"
                
            suggestions.append({
                'column_name': raw_col,
                'suggested_variable': best_match,
                'confidence': confidence_label,
                'confidence_score': best_confidence,
                'reason': best_reason
            })
    
    # Sort by confidence score (highest first)
    suggestions.sort(key=lambda x: x['confidence_score'], reverse=True)
    
    return suggestions
