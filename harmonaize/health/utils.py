"""
Utility functions for health data processing and validation.
"""
import pandas as pd
import logging
from typing import Dict, Any, List
from django.core.files.uploadedfile import UploadedFile
from core.models import Study

logger = logging.getLogger(__name__)


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
        
        if file_ext == 'csv':
            df = pd.read_csv(file_path, nrows=100)  # Read first 100 rows for analysis
        elif file_ext in ['xlsx', 'xls']:
            df = pd.read_excel(file_path, nrows=100)
        elif file_ext == 'json':
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
            'total_rows': len(df),
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
    Suggest mappings between raw data columns and study variables using simple matching.
    
    Args:
        raw_columns: List of column names from raw data file
        study_variables: List of variable names from study codebook
        
    Returns:
        List[dict]: List of suggestions with column_name, suggested_variable, confidence, reason
    """
    suggestions = []
    
    # Simple string matching algorithm
    for raw_col in raw_columns:
        raw_col_lower = raw_col.lower().strip()
        best_match = None
        best_confidence = 0.0
        best_reason = ""
        
        for study_var in study_variables:
            study_var_lower = study_var.lower().strip()
            confidence = 0.0
            reason = ""
            
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
