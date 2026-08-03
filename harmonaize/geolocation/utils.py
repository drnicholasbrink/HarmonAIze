# geolocation/utils.py
"""
Utility functions for location CSV upload and processing.
Follows patterns from health/utils.py for consistency.
"""

import io
import pandas as pd
from typing import Dict, List, Any


def analyze_location_csv_columns(file_obj, file_ext: str = None) -> Dict[str, Any]:
    """
    Analyze location CSV columns for type inference and preview.

    Args:
        file_obj: File-like object or path string
        file_ext: File extension (csv, xlsx, xls, json). If None, inferred from file_obj if it's a string path.

    Returns:
        Dictionary containing:
        - success: bool
        - total_rows: int
        - columns: list of column names
        - column_analysis: dict with metadata for each column
    """
    try:
        if file_ext is None and isinstance(file_obj, str):
            file_ext = file_obj.split('.')[-1].lower()

        file_ext = file_ext.lower()

        # Read sample for analysis (first 100 rows for speed)
        if file_ext == 'csv':
            if isinstance(file_obj, (str, bytes)):
                df = pd.read_csv(file_obj, nrows=100)
                df_full = pd.read_csv(file_obj)
            else:
                content = file_obj.read()
                df = pd.read_csv(io.BytesIO(content), nrows=100)
                df_full = pd.read_csv(io.BytesIO(content))
            total_rows = len(df_full)
        elif file_ext in ['xlsx', 'xls']:
            if isinstance(file_obj, (str, bytes)):
                df = pd.read_excel(file_obj, nrows=100)
                df_full = pd.read_excel(file_obj, usecols=[0])
            else:
                content = file_obj.read()
                df = pd.read_excel(io.BytesIO(content), nrows=100)
                df_full = pd.read_excel(io.BytesIO(content), usecols=[0])
            total_rows = len(df_full)
        elif file_ext == 'json':
            if isinstance(file_obj, (str, bytes)):
                df = pd.read_json(file_obj, lines=True, nrows=100)
            else:
                content = file_obj.read()
                df = pd.read_json(io.BytesIO(content), lines=True, nrows=100)
            total_rows = 100  # Approximate for JSON
        else:
            return {
                'success': False,
                'error': f"Unsupported file format: {file_ext}"
            }

        # Analyze each column
        column_analysis = {}
        for col in df.columns:
            col_data = df[col].dropna()

            # Detect potential location columns
            is_location_name = detect_location_name_column(col, df[col])
            is_latitude = detect_latitude_column(col, df[col])
            is_longitude = detect_longitude_column(col, df[col])

            column_analysis[str(col)] = {
                'inferred_type': infer_column_type(df[col]),
                'non_null_count': int(len(col_data)),
                'unique_count': int(col_data.nunique()),
                'sample_values': [str(v) for v in col_data.head(5).tolist()],
                'is_potential_location_name': is_location_name,
                'is_potential_latitude': is_latitude,
                'is_potential_longitude': is_longitude,
            }

        return {
            'success': True,
            'total_rows': int(total_rows),
            'columns': [str(col) for col in df.columns],
            'column_analysis': column_analysis,
        }

    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


def infer_column_type(series: pd.Series) -> str:
    """
    Infer column type from pandas Series.

    Returns: 'text', 'float', 'integer', 'date', or 'datetime'
    """
    dtype_str = str(series.dtype)

    if 'float' in dtype_str:
        return 'float'
    elif 'int' in dtype_str:
        return 'integer'
    elif 'datetime' in dtype_str:
        return 'datetime'
    elif 'object' in dtype_str:
        # Try to detect dates in object columns
        non_null = series.dropna()
        if len(non_null) > 0:
            try:
                pd.to_datetime(non_null.head(10))
                return 'date'
            except:
                pass
        return 'text'
    else:
        return 'text'


def detect_location_name_column(col_name: str, col_data: pd.Series) -> bool:
    """
    Heuristic to detect if column is likely a location name.

    Checks:
    1. Column name contains location-related keywords
    2. Text data with many unique values (diverse locations)
    """
    col_name_lower = col_name.lower().strip()

    # Common patterns for location name columns
    location_keywords = [
        'location', 'place', 'area', 'site', 'facility', 'clinic', 'hospital',
        'name', 'province', 'district', 'region', 'city', 'town', 'village',
        'ward', 'suburb', 'neighborhood', 'venue'
    ]

    # Check column name
    if any(kw in col_name_lower for kw in location_keywords):
        return True

    # Check data characteristics (text with high uniqueness)
    if col_data.dtype == 'object':  # Text type
        non_null = col_data.dropna()
        if len(non_null) > 0:
            uniqueness_ratio = non_null.nunique() / len(non_null)
            # If more than 30% unique values, likely location names
            if uniqueness_ratio > 0.3:
                return True

    return False


def detect_latitude_column(col_name: str, col_data: pd.Series) -> bool:
    """
    Heuristic to detect if column is likely a latitude.

    Checks:
    1. Column name contains 'lat' or 'latitude'
    2. Numeric values in range -90 to 90
    """
    col_name_lower = col_name.lower().strip()

    # Name patterns
    lat_keywords = ['lat', 'latitude', 'y_coord', 'y_coordinate', 'northing']
    if any(kw in col_name_lower for kw in lat_keywords):
        return True

    # Value range check (latitude must be -90 to 90)
    try:
        numeric_data = pd.to_numeric(col_data, errors='coerce').dropna()
        if len(numeric_data) > 0:
            min_val = numeric_data.min()
            max_val = numeric_data.max()
            # Valid latitude range
            if -90 <= min_val and max_val <= 90:
                return True
    except:
        pass

    return False


def detect_longitude_column(col_name: str, col_data: pd.Series) -> bool:
    """
    Heuristic to detect if column is likely a longitude.

    Checks:
    1. Column name contains 'lon', 'lng', or 'longitude'
    2. Numeric values in range -180 to 180
    """
    col_name_lower = col_name.lower().strip()

    # Name patterns
    lon_keywords = ['lon', 'lng', 'long', 'longitude', 'x_coord', 'x_coordinate', 'easting']
    if any(kw in col_name_lower for kw in lon_keywords):
        return True

    # Value range check (longitude must be -180 to 180)
    try:
        numeric_data = pd.to_numeric(col_data, errors='coerce').dropna()
        if len(numeric_data) > 0:
            min_val = numeric_data.min()
            max_val = numeric_data.max()
            # Valid longitude range
            if -180 <= min_val and max_val <= 180:
                return True
    except:
        pass

    return False


def suggest_location_column_mappings(columns: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    Suggest which columns map to location_name, latitude, longitude.

    Args:
        columns: List of column metadata dicts with 'column_name' and detection flags

    Returns:
        Dictionary with suggested mappings:
        {
            'location_name': 'facility_name',
            'latitude': 'lat',
            'longitude': 'lon'
        }
    """
    suggestions = {
        'location_name': None,
        'latitude': None,
        'longitude': None,
    }

    for col_meta in columns:
        col_name = col_meta.get('column_name', '')

        # Location name (prioritize by detection flag)
        if col_meta.get('is_potential_location_name') and not suggestions['location_name']:
            suggestions['location_name'] = col_name

        # Latitude (prioritize by detection flag)
        if col_meta.get('is_potential_latitude') and not suggestions['latitude']:
            suggestions['latitude'] = col_name

        # Longitude (prioritize by detection flag)
        if col_meta.get('is_potential_longitude') and not suggestions['longitude']:
            suggestions['longitude'] = col_name

    return suggestions
