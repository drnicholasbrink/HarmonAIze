"""
Celery tasks for health data ingestion and processing.
Handles large file processing in the background.
"""

import logging
import pandas as pd
import numpy as np
from typing import Any
from datetime import datetime
from django.utils import timezone
from django.db import transaction, IntegrityError, DatabaseError
from django.db.models import F
from celery import shared_task
from celery.exceptions import Retry
from dateutil.parser import ParserError

from .models import RawDataFile, RawDataColumn
from core.models import Study, Attribute, Patient, Observation, TimeDimension

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=1, default_retry_delay=30)
def generate_eda_caches(self, raw_data_file_id: int, include_transformed: bool = True) -> dict[str, Any]:
    """Generate (or regenerate) EDA caches for a RawDataFile asynchronously.

    This task reads the raw uploaded file for source EDA and, if requested and available,
    builds transformed EDA from observations. It purposefully does NOT modify the core
    model schema—only existing cache JSON fields are updated. Any previous cache values
    are overwritten.

    Args:
        raw_data_file_id: PK of RawDataFile
        include_transformed: Whether to also attempt transformed EDA

    Returns:
        Dict with success flag and basic metadata.
    """
    from django.utils import timezone
    from .eda_service import (
        generate_eda_summary,
        generate_eda_summary_from_observations,
    )

    try:
        rdf = RawDataFile.objects.select_related("last_transformation_schema").get(id=raw_data_file_id)
    except RawDataFile.DoesNotExist:
        return {"success": False, "message": f"RawDataFile {raw_data_file_id} not found"}

    logger.info("[EDA TASK] Starting EDA cache generation for file %s", raw_data_file_id)
    result: dict[str, Any] = {"success": True, "file_id": raw_data_file_id, "generated": []}

    try:
        # Always regenerate source cache
        rdf.eda_cache_source = None
        rdf.eda_cache_source_generated_at = None
        rdf.save(update_fields=["eda_cache_source", "eda_cache_source_generated_at"])
        source_cache = generate_eda_summary(rdf)
        result["generated"].append("source")

        # Optionally regenerate transformed cache
        if include_transformed and rdf.transformation_status == "completed" and rdf.last_transformation_schema_id:
            try:
                target_study = rdf.last_transformation_schema.target_study
                transformed_cache = generate_eda_summary_from_observations(
                    rdf, target_study, is_transformed=True
                )
                result["generated"].append("transformed")
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception(
                    "[EDA TASK] Failed transformed EDA generation for file %s: %s",
                    raw_data_file_id,
                    exc,
                )
                result.setdefault("errors", []).append(str(exc))

        result["message"] = (
            "Generated EDA caches: " + ", ".join(result["generated"]) if result["generated"] else "No caches generated"
        )
        logger.info("[EDA TASK] Completed EDA cache generation for file %s: %s", raw_data_file_id, result["message"])
        return result
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("[EDA TASK] Unexpected failure for file %s", raw_data_file_id)
        return {"success": False, "file_id": raw_data_file_id, "message": str(exc)}


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def ingest_raw_data_file(self, raw_data_file_id: int) -> dict[str, Any]:
    """
    Main task to ingest a raw data file and create observations.
    
    Args:
        raw_data_file_id: ID of the RawDataFile to process
        
    Returns:
        Dict with processing results and statistics
    """
    try:
        # Get the raw data file
        raw_data_file = RawDataFile.objects.select_related("study", "uploaded_by").get(
            id=raw_data_file_id,
        )
        
        logger.info(
            "Starting ingestion of file %s",
            raw_data_file.original_filename,
        )

        # Validate prerequisites while preserving current status
        validation_result = _validate_file_for_ingestion(raw_data_file)
        if not validation_result["valid"]:
            raw_data_file.processing_status = "ingestion_error"
            raw_data_file.processing_message = validation_result["message"]
            raw_data_file.save(
                update_fields=["processing_status", "processing_message"],
            )
            return {
                "success": False,
                "message": validation_result["message"],
                "file_id": raw_data_file_id,
            }

        # Set status to processing after successful pre-checks
        raw_data_file.processing_status = "processing"
        raw_data_file.processing_message = "Starting data ingestion..."
        raw_data_file.save(
            update_fields=["processing_status", "processing_message"],
        )
        
        # Load and process the data
        df_result = _load_data_file(raw_data_file)
        if not df_result["success"]:
            raw_data_file.processing_status = "ingestion_error"
            raw_data_file.processing_message = df_result["message"]
            raw_data_file.save(
                update_fields=["processing_status", "processing_message"],
            )
            return {
                "success": False,
                "message": df_result["message"],
                "file_id": raw_data_file_id,
            }
        
        df = df_result['dataframe']
        
        # Process data in chunks to handle large files
        chunk_size = 1000  # Process 1000 rows at a time
        total_rows = len(df)
        processed_rows = 0
        created_observations = 0
        errors = []
        
        logger.info(f"Processing {total_rows} rows in chunks of {chunk_size}")
        
        for chunk_start in range(0, total_rows, chunk_size):
            chunk_end = min(chunk_start + chunk_size, total_rows)
            chunk_df = df.iloc[chunk_start:chunk_end]
            
            # Update progress
            progress = int((chunk_start / total_rows) * 100)
            raw_data_file.processing_message = (
                f"Processing rows {chunk_start + 1}-{chunk_end} of {total_rows} "
                f"({progress}%)"
            )
            raw_data_file.save(update_fields=["processing_message"])
            
            # Process chunk
            chunk_result = _process_data_chunk(raw_data_file, chunk_df, chunk_start)
            processed_rows += chunk_result["processed_rows"]
            created_observations += chunk_result["created_observations"]
            errors.extend(chunk_result["errors"])
            
            # Log progress without exposing data structure details
            obs_count = chunk_result["created_observations"]
            logger.debug(
                "Processed data chunk for file ID %s: %s observations created",
                raw_data_file_id,
                obs_count,
            )
        
        # Final status update
        if errors:
            first_errors = "; ".join(errors[:3])
            error_summary = (
                f"Completed with {len(errors)} errors. First few errors: "
                f"{first_errors}"
            )
            if len(errors) > 3:
                error_summary += f" (and {len(errors) - 3} more)"
            
            raw_data_file.processing_status = "processed_with_errors"
            raw_data_file.processing_message = error_summary
        else:
            raw_data_file.processing_status = "ingested"
            raw_data_file.processing_message = (
                "Successfully ingested "
                f"{created_observations} observations from {processed_rows} rows"
            )
        
        raw_data_file.processed_at = timezone.now()
        raw_data_file.save(
            update_fields=[
                "processing_status",
                "processing_message",
                "processed_at",
            ]
        )
        
        result = {
            'success': True,
            'message': raw_data_file.processing_message,
            'file_id': raw_data_file_id,
            'processed_rows': processed_rows,
            'created_observations': created_observations,
            'errors': errors,
            'total_rows': total_rows,
        }
        
        # Log summary without exposing sensitive data or error details
        logger.info(
            "Completed ingestion of file ID %s: processed %s rows, "
            "created %s observations, %s errors encountered",
            raw_data_file_id,
            processed_rows,
            created_observations,
            len(errors),
        )
        
        return result
        
    except RawDataFile.DoesNotExist:
        error_msg = f"RawDataFile with ID {raw_data_file_id} not found"
        logger.error(error_msg)
        return {"success": False, "message": error_msg, "file_id": raw_data_file_id}
        
    except Exception as exc:
        logger.exception(f"Error processing raw data file {raw_data_file_id}: {exc}")
        
        # Create detailed error message based on exception type
        error_details = _get_detailed_error_message(exc, raw_data_file_id)
        
        # Update file status
        try:
            raw_data_file = RawDataFile.objects.get(id=raw_data_file_id)
            raw_data_file.processing_status = "ingestion_error"
            raw_data_file.processing_message = error_details
            raw_data_file.save(
                update_fields=["processing_status", "processing_message"],
            )
        except Exception:
            pass
        
        # Retry on certain types of errors
        if (
            isinstance(exc, (ConnectionError, TimeoutError))
            and self.request.retries < self.max_retries
        ):
            logger.info(f"Retrying task due to {type(exc).__name__}")
            raise self.retry(countdown=60, exc=exc)
        
        return {
            "success": False,
            "message": error_details,
            "file_id": raw_data_file_id,
        }


def _validate_file_for_ingestion(raw_data_file: RawDataFile) -> dict[str, Any]:
    """Validate that a file is ready for ingestion."""
    
    # Check that file is not in a clearly pre-validation state
    # Allow statuses like processed (mapped), processing (queued/started),
    # ingestion_error (retry), ingested, processed_with_errors.
    if raw_data_file.processing_status in ['uploaded', 'validation_error', 'error']:
        return {
            "valid": False,
            "message": (
                "File must be validated and mapped before ingestion. "
                f"Current status: {raw_data_file.processing_status}"
            ),
        }
    
    # Check that study has variables
    if not raw_data_file.study.variables.exists():
        return {
            "valid": False,
            "message": (
                "Study must have variables extracted from codebook "
                "before ingesting data"
            ),
        }
    
    # Check that columns have been mapped
    mapped_columns = raw_data_file.columns.filter(mapped_variable__isnull=False)
    if not mapped_columns.exists():
        return {
            "valid": False,
            "message": (
                "No columns have been mapped to study variables. "
                "Please map columns first."
            ),
        }
    
    # Check that essential columns are mapped
    # Note: raw_data_file.patient_id_column stores the VARIABLE NAME from the codebook
    # We must resolve it to the actual raw column that is mapped to this variable.
    if raw_data_file.patient_id_column:
        pid_mapping = raw_data_file.columns.filter(
            mapped_variable__variable_name=raw_data_file.patient_id_column,
        ).first()
        if not pid_mapping:
            return {
                "valid": False,
                "message": (
                    "Selected participant ID variable "
                    f'"{raw_data_file.patient_id_column}" is not mapped to any file column.'
                ),
            }

    # Validate date column mapping if provided (date_column stores variable name)
    if raw_data_file.date_column:
        date_mapping = raw_data_file.columns.filter(
            mapped_variable__variable_name=raw_data_file.date_column,
        ).first()
        if not date_mapping:
            return {
                "valid": False,
                "message": (
                    "Selected date/time variable "
                    f'"{raw_data_file.date_column}" is not mapped to any file column.'
                ),
            }
    
    return {"valid": True, "message": "File validation passed"}


def _load_data_file(raw_data_file: RawDataFile) -> dict[str, Any]:
    """Load data file into a pandas DataFrame."""
    
    try:
        file_path = raw_data_file.file.path
        
        # Load based on file format
        if raw_data_file.file_format == 'csv':
            df = pd.read_csv(file_path, low_memory=False)
        elif raw_data_file.file_format in ['xlsx', 'xls']:
            df = pd.read_excel(file_path)
        elif raw_data_file.file_format == 'json':
            df = pd.read_json(file_path)
        elif raw_data_file.file_format == 'txt':
            # Assume tab-separated
            df = pd.read_csv(file_path, sep='\t', low_memory=False)
        else:
            return {
                "success": False,
                "message": f"Unsupported file format: {raw_data_file.file_format}",
            }
        
        # Basic validation
        if df.empty:
            return {
                "success": False,
                "message": "Data file is empty",
            }
        
        logger.info(f"Loaded {len(df)} rows and {len(df.columns)} columns from {raw_data_file.original_filename}")
        
        return {
            "success": True,
            "dataframe": df,
            "rows": len(df),
            "columns": len(df.columns),
        }
        
    except Exception as exc:
        logger.error(f"Error loading data file {raw_data_file.original_filename}: {exc}")
        return {
            "success": False,
            "message": f"Error loading file: {str(exc)}",
        }


def _process_data_chunk(
    raw_data_file: RawDataFile,
    chunk_df: pd.DataFrame,
    chunk_start: int,
) -> dict[str, Any]:
    """Process a chunk of data rows."""
    
    processed_rows = 0
    created_observations = 0
    errors = []
    
    # Get column mappings (only SOURCE attributes should be ingested here)
    column_mappings = {}
    for column in raw_data_file.columns.filter(mapped_variable__isnull=False):
        attr = column.mapped_variable
        if getattr(attr, "source_type", "source") != "source":
            logger.debug(
                "Skipping mapped column %s -> %s because attribute is not source (source_type=%s)",
                column.column_name,
                getattr(attr, "variable_name", "<unknown>"),
                getattr(attr, "source_type", None),
            )
            continue
        column_mappings[column.column_name] = attr
    
    # Resolve patient ID column name from selected variable (stored on RawDataFile)
    patient_id_column_name = None
    if raw_data_file.patient_id_column:
        pid_mapping = raw_data_file.columns.filter(
            mapped_variable__variable_name=raw_data_file.patient_id_column,
        ).first()
        if pid_mapping:
            patient_id_column_name = pid_mapping.column_name
    # Fallback: if not explicitly selected, try any column mapped to a 'patient_id' variable
    if not patient_id_column_name:
        pid_fallback = raw_data_file.columns.filter(
            mapped_variable__variable_name="patient_id",
        ).first()
        if pid_fallback:
            patient_id_column_name = pid_fallback.column_name

    # Resolve date column name from selected variable (stored on RawDataFile)
    date_column_name = None
    if raw_data_file.date_column:
        date_mapping = raw_data_file.columns.filter(
            mapped_variable__variable_name=raw_data_file.date_column,
        ).first()
        if date_mapping:
            date_column_name = date_mapping.column_name
    
    # Process each row in the chunk
    for idx, row in chunk_df.iterrows():
        row_num = chunk_start + idx + 1
        
        try:
            with transaction.atomic():
                # Extract patient ID
                patient_id = None
                if patient_id_column_name and patient_id_column_name in row:
                    patient_id = str(row[patient_id_column_name]).strip()
                    if patient_id and patient_id.lower() not in ['nan', 'none', 'null', '']:
                        # Get or create patient
                        patient, _ = Patient.objects.get_or_create(unique_id=patient_id)
                    else:
                        patient = None
                else:
                    # Use row number as patient ID if no patient ID column-- need to be careful with this
                    patient_id = f"patient_{row_num}"
                    patient, _ = Patient.objects.get_or_create(unique_id=patient_id)
                
                # Extract date/time
                time_dimension = None
                if date_column_name and date_column_name in row:
                    date_value = row[date_column_name]
                    if pd.notna(date_value):
                        try:
                            # Best-effort parse for time; if it fails, skip time (ingest raw data only)
                            parsed_date = pd.to_datetime(date_value)
                            time_dimension, _ = TimeDimension.objects.get_or_create(
                                timestamp=parsed_date,
                            )
                        except (ValueError, TypeError, OverflowError, ParserError):
                            time_dimension = None
                
                # Process each mapped column
                row_observations = 0
                for column_name, variable in column_mappings.items():
                    if column_name in row:
                        value = row[column_name]
                        
                        # Skip null/empty values
                        if pd.isna(value) or str(value).strip() == "":
                            continue
                        
                        # Create observation
                        # Safety: ensure we're only creating observations for SOURCE attributes
                        if getattr(variable, "source_type", "source") != "source":
                            logger.debug(
                                "Not creating observation for non-source attribute %s (source_type=%s)",
                                getattr(variable, "variable_name", "<unknown>"),
                                getattr(variable, "source_type", None),
                            )
                            continue

                        obs_result = _create_observation(
                            patient=patient,
                            attribute=variable,
                            value=value,
                            time_dimension=time_dimension,
                            row_num=row_num,
                        )
                        
                        if obs_result["success"]:
                            row_observations += 1
                        else:
                            errors.append(obs_result["error"])
                
                created_observations += row_observations
                processed_rows += 1

        except (DatabaseError, IntegrityError, ValueError, TypeError, KeyError) as exc:
            # Format error without exposing sensitive row data in logs
            error_msg = _format_row_error(
                exc, row_num, patient_id_column_name or "", row,
            )
            errors.append(error_msg)
            # Log error type and row number only, not the full message with PII
            logger.warning(
                "Data processing error at row %s for file ID %s: %s",
                row_num,
                raw_data_file.id,
                type(exc).__name__,
            )
    
    return {
    "processed_rows": processed_rows,
    "created_observations": created_observations,
    "errors": errors,
    }


def _create_observation(
    patient: Patient | None,
    attribute: Attribute,
    value: Any,
    time_dimension: TimeDimension | None,
    row_num: int,
) -> dict[str, Any]:
    """Create a single observation from a data value."""
    
    try:
        # Prepare observation data; ingest raw values, try type when safe, fallback to text
        obs_data = {
            "patient": patient,
            "attribute": attribute,
            "time": time_dimension,
        }
        raw_text = str(value)
        try:
            if attribute.variable_type == "float":
                obs_data["float_value"] = float(value)
            elif attribute.variable_type == "int":
                obs_data["int_value"] = int(float(value))
            elif attribute.variable_type in ["string", "categorical"]:
                obs_data["text_value"] = raw_text
            elif attribute.variable_type == "boolean":
                if isinstance(value, bool):
                    obs_data["boolean_value"] = value
                else:
                    obs_data["text_value"] = raw_text
            elif attribute.variable_type == "datetime":
                try:
                    obs_data["datetime_value"] = pd.to_datetime(value)
                except (ValueError, TypeError, OverflowError, ParserError):
                    obs_data["text_value"] = raw_text
            else:
                obs_data["text_value"] = raw_text
        except (ValueError, TypeError):
            obs_data["text_value"] = raw_text
        
        # Create observation
        observation = Observation.objects.create(**obs_data)
        
        return {
            'success': True,
            'observation_id': observation.id,
        }
        
    except IntegrityError as exc:
        # Handle duplicate observations gracefully
        if 'unique_together' in str(exc).lower():
            return {
                'success': False,
                'error': (
                    f"Row {row_num}: Duplicate observation for "
                    f"{attribute.variable_name}"
                ),
            }
        return {
            "success": False,
            "error": (
                f"Row {row_num}: Database error creating observation for "
                f"{attribute.variable_name}: {str(exc)}"
            ),
        }
            
    except (DatabaseError, ValueError, TypeError) as exc:
        return {
            'success': False,
            'error': (
                f"Row {row_num}: Unexpected error creating observation for "
                f"{attribute.variable_name}: {str(exc)}"
            ),
        }


def _get_detailed_error_message(exception: Exception, file_id: int) -> str:
    """
    Convert various exception types to user-friendly error messages with specific guidance.
    """
    error_mappings = _get_error_mappings()
    
    exc_type = type(exception)
    exc_str = str(exception)
    
    # Check specific exception types first
    for error_type, handler in error_mappings.items():
        if isinstance(exception, error_type):
            return handler(exc_str)
    
    # Check exception type name for database errors
    database_message = _handle_database_errors(exc_type, exc_str)
    if database_message:
        return database_message
    
    # Check content-based patterns
    content_message = _handle_content_based_errors(exc_str)
    if content_message:
        return content_message
    
    # Generic fallback
    return _format_generic_error(exc_str, file_id)


def _get_error_mappings():
    """Return mapping of exception types to handler functions."""
    return {
        FileNotFoundError: lambda _: "File could not be found. Please re-upload the file and try again.",
        PermissionError: lambda _: "Unable to access the file due to permission restrictions. Please check file permissions or re-upload.",
        pd.errors.EmptyDataError: lambda _: "The uploaded file appears to be empty or contains no readable data. Please check the file content.",
        pd.errors.ParserError: _handle_parser_error,
        UnicodeDecodeError: lambda _: "The file encoding is not supported. Please save your file in UTF-8 encoding and try again.",
        MemoryError: lambda _: "The file is too large to process. Please split your data into smaller files (recommended: under 100MB each).",
        (ConnectionError, TimeoutError): lambda _: "Connection timeout occurred. This may be due to system load. The process will automatically retry.",
    }


def _handle_parser_error(exc_str: str) -> str:
    """Handle pandas parser errors with specific guidance."""
    if "delimiter" in exc_str.lower() or "separator" in exc_str.lower():
        return "Unable to parse the file format. Please ensure your CSV file uses commas as separators or try a different file format."
    return "The file format appears to be corrupted or invalid. Please check the file and try uploading again."


def _handle_database_errors(exc_type: type, exc_str: str) -> str | None:
    """Handle database-related errors."""
    type_name = exc_type.__name__
    
    if "IntegrityError" in type_name:
        if "duplicate" in exc_str.lower() or "unique" in exc_str.lower():
            return "Data contains duplicate entries that violate database constraints. Please check for duplicate patient IDs or timestamps."
        return "Data integrity issue detected. Please check your data for invalid references or constraint violations."
    
    if "DatabaseError" in type_name or "OperationalError" in type_name:
        return "Database error occurred during processing. Please try again in a few minutes or contact system administrator if the problem persists."
    
    if "ValidationError" in type_name:
        return f"Data validation failed: {exc_str}"
    
    return None


def _handle_content_based_errors(exc_str: str) -> str | None:
    """Handle errors based on content patterns in the error string."""
    exc_lower = exc_str.lower()
    
    # Column mapping errors
    if "column" in exc_lower and ("not found" in exc_lower or "missing" in exc_lower):
        return "One or more required columns are missing from the file. Please check your column mappings and file structure."
    
    # Date parsing errors
    if "datetime" in exc_lower or "date" in exc_lower:
        return "Unable to parse date/time values in the file. Please ensure dates are in a recognized format (e.g., YYYY-MM-DD or MM/DD/YYYY)."
    
    # Type conversion errors
    if "convert" in exc_lower and ("float" in exc_lower or "int" in exc_lower):
        return "Data type mismatch detected. Please check that numeric columns contain only numbers and fix any text values in numeric fields."
    
    return None


def _format_generic_error(exc_str: str, file_id: int) -> str:
    """Format a generic error message with truncation if needed."""
    MAX_ERROR_LENGTH = 200
    
    if len(exc_str) > MAX_ERROR_LENGTH:
        exc_str = exc_str[:MAX_ERROR_LENGTH] + "..."
    
    return f"Processing error occurred: {exc_str}. If this error persists, please contact support with error ID {file_id}."


def _format_row_error(exception: Exception, row_num: int, patient_id_column: str, row_data) -> str:
    """Format row-specific error messages with context."""
    exc_str = str(exception)
    exc_type = type(exception).__name__
    
    # Try to get patient ID for context
    patient_context = ""
    if patient_id_column and patient_id_column in row_data:
        patient_id = row_data[patient_id_column]
        patient_context = f" (Patient ID: {patient_id})"
    
    # Provide specific guidance based on error type
    if "IntegrityError" in exc_type and "duplicate" in exc_str.lower():
        return f"Row {row_num}{patient_context}: Duplicate data detected. This patient may already have data for this time period."
    
    if "ValidationError" in exc_type:
        return f"Row {row_num}{patient_context}: Data validation failed - {exc_str}"
    
    if "DoesNotExist" in exc_str:
        return f"Row {row_num}{patient_context}: Reference to non-existent record. Check your data mappings."
    
    if "null value" in exc_str.lower():
        return f"Row {row_num}{patient_context}: Required field is missing. Please check for empty values in required columns."
    
    # Generic row error
    return f"Row {row_num}{patient_context}: {exc_str}"


@shared_task(bind=True)
def process_multiple_files(self, file_ids: list[int]) -> dict[str, Any]:
    """
    Process multiple raw data files sequentially.
    
    Args:
        file_ids: List of RawDataFile IDs to process
        
    Returns:
        Dict with overall results
    """
    results = []
    total_files = len(file_ids)
    
    for i, file_id in enumerate(file_ids, 1):
        logger.info(f"Processing file {i}/{total_files}: ID {file_id}")
        
        try:
            result = ingest_raw_data_file.delay(file_id)
            # Wait for completion
            file_result = result.get()
            results.append(file_result)
            
        except Exception as exc:
            logger.error(f"Error processing file {file_id}: {exc}")
            results.append({
                'success': False,
                'message': f'Error processing file {file_id}: {str(exc)}',
                'file_id': file_id,
            })
    
    # Calculate summary
    successful = sum(1 for r in results if r.get('success', False))
    total_observations = sum(r.get('created_observations', 0) for r in results)
    total_rows = sum(r.get('processed_rows', 0) for r in results)
    
    return {
        'success': True,
        'message': f'Processed {total_files} files: {successful} successful, {total_files - successful} failed',
        'total_files': total_files,
        'successful_files': successful,
        'failed_files': total_files - successful,
        'total_observations': total_observations,
        'total_rows': total_rows,
        'file_results': results,
    }


@shared_task
def cleanup_failed_ingestions():
    """
    Cleanup task to handle failed ingestion attempts.
    Resets files that have been stuck in 'processing' status.
    """
    from django.utils import timezone
    from datetime import timedelta
    
    # Find files stuck in processing for more than 1 hour
    cutoff_time = timezone.now() - timedelta(hours=1)
    stuck_files = RawDataFile.objects.filter(
        processing_status="processing",
        updated_at__lt=cutoff_time,
    )
    
    updated_count = 0
    for file in stuck_files:
        file.processing_status = "ingestion_error"
        file.processing_message = "Processing timed out and was reset"
        file.save(update_fields=["processing_status", "processing_message"])
        updated_count += 1
        logger.warning(f"Reset stuck processing file: {file.original_filename}")
    
    return {
        "success": True,
        "message": f"Reset {updated_count} stuck files",
        "updated_count": updated_count,
    }


def _compile_transform_callable(code: str):
    """Compile transform code into a callable. Supports lambda or def transform(value)."""
    if not code or not code.strip():
        return None

    safe_builtins = {
        "int": int,
        "float": float,
        "str": str,
        "bool": bool,
        "round": round,
        "abs": abs,
        "min": min,
        "max": max,
        "len": len,
        "sum": sum,
        "any": any,
        "all": all,
        "sorted": sorted,
        "reversed": reversed,
    }
    safe_globals = {"__builtins__": safe_builtins}

    code_str = code.strip()
    try:
        if code_str.startswith("lambda"):
            func = eval(code_str, safe_globals, {})
            return func
        # Expect a def transform(value): ...
        local_ns = {}
        exec(code_str, safe_globals, local_ns)
        if "transform" in local_ns and callable(local_ns["transform"]):
            return local_ns["transform"]
    except Exception:
        logger.exception("Failed to compile transform code")
        return None
    return None


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def transform_observations_for_schema(
    self,
    schema_id: int,
    delete_existing: bool = False,
) -> dict[str, Any]:
    """Apply MappingRules to existing observations to create harmonised target observations.

    Args:
        schema_id: MappingSchema primary key.
        delete_existing: When True, remove previously harmonised observations for this schema's
            target study before reapplying mappings.
    """
    try:
        from .models import MappingSchema, MappingRule

        schema = MappingSchema.objects.select_related(
            "source_study", "target_study"
        ).get(id=schema_id)

        rules = (
            MappingRule.objects.select_related("source_attribute", "target_attribute")
            .filter(
                schema=schema,
                not_mappable=False,
                target_attribute__isnull=False,
                source_attribute__source_type="source",
                target_attribute__source_type="target",
            )
        )

        transformed = 0
        skipped = 0
        deleted_count = 0
        errors: list[str] = []

        target_attribute_ids: list[int] = []
        if delete_existing:
            target_attribute_ids = list(
                schema.target_study.variables.filter(source_type="target").values_list("id", flat=True)
            )
            if target_attribute_ids:
                delete_qs = Observation.objects.filter(attribute_id__in=target_attribute_ids)
                if schema.created_at:
                    delete_qs = delete_qs.filter(created_at__gte=schema.created_at)
                deleted_count, _ = delete_qs.delete()
                logger.info(
                    "Deleted %s prior harmonised observations for schema %s before re-processing",
                    deleted_count,
                    schema_id,
                )

        # Mark all raw files for this source study as transformation in progress
        try:
            source_files = RawDataFile.objects.filter(study=schema.source_study)
            now = timezone.now()
            transformation_message = "Applying approved mapping schema..."
            if delete_existing and deleted_count:
                transformation_message = (
                    f"Cleared {deleted_count} harmonised observations; reapplying mapping..."
                )
            source_files.update(
                transformation_status="in_progress",
                transformation_started_at=now,
                transformation_message=transformation_message,
                last_transformation_schema=schema,
                transformed_at=None if delete_existing else F("transformed_at"),
            )
        except Exception:
            logger.debug("Unable to mark raw files as in_progress for schema %s", schema_id)

        # Determine time window: transform observations created after schema creation
        window_start = schema.created_at

        for rule in rules:
            transform_func = _compile_transform_callable(rule.transform_code or "")
            # Source observations for this attribute
            qs = Observation.objects.filter(attribute=rule.source_attribute)
            if window_start:
                qs = qs.filter(created_at__gte=window_start)

            for src_obs in qs.iterator():
                try:
                    value = src_obs.value
                    if value is None:
                        skipped += 1
                        continue
                    if transform_func:
                        try:
                            value = transform_func(value)
                        except Exception as te:
                            skipped += 1
                            logger.debug("Transform error on obs %s: %s", src_obs.id, te)
                            continue
                    if value is None:
                        skipped += 1
                        continue

                    # Prepare defaults according to target attribute type
                    defaults: dict[str, Any] = {"patient": src_obs.patient, "location": src_obs.location, "time": src_obs.time}
                    tgt_type = rule.target_attribute.variable_type
                    try:
                        if tgt_type == "float":
                            defaults["float_value"] = float(value)
                        elif tgt_type == "int":
                            defaults["int_value"] = int(float(value))
                        elif tgt_type in ["string", "categorical"]:
                            defaults["text_value"] = str(value)
                        elif tgt_type == "boolean":
                            if isinstance(value, bool):
                                defaults["boolean_value"] = value
                            else:
                                defaults["text_value"] = str(value)
                        elif tgt_type == "datetime":
                            try:
                                defaults["datetime_value"] = pd.to_datetime(value)
                            except Exception:
                                defaults["text_value"] = str(value)
                        else:
                            defaults["text_value"] = str(value)
                    except (ValueError, TypeError):
                        defaults["text_value"] = str(value)

                    # Upsert target observation
                    obj, created = Observation.objects.get_or_create(
                        patient=src_obs.patient,
                        location=src_obs.location,
                        attribute=rule.target_attribute,
                        time=src_obs.time,
                        defaults=defaults,
                    )
                    if not created:
                        # Update values if existing
                        for k, v in defaults.items():
                            setattr(obj, k, v)
                        obj.save(update_fields=list(defaults.keys()))
                    transformed += 1
                except Exception as exc:
                    errors.append(str(exc))
                    logger.debug("Error transforming obs %s: %s", src_obs.id, exc)

        message_parts = []
        if delete_existing:
            message_parts.append(f"Deleted {deleted_count} harmonised observations before re-run.")
        message_parts.append(
            f"Transformed {transformed} observations; skipped {skipped}; rules applied: {rules.count()}"
        )
        message = " ".join(message_parts)
        logger.info("%s for schema %s", message, schema_id)
        # Mark files as completed
        try:
            done_time = timezone.now()
            updates = {
                "transformation_status": "completed",
                "transformed_at": done_time,
                "transformation_message": message,
            }
            RawDataFile.objects.filter(study=schema.source_study).update(**updates)
        except Exception:
            logger.debug("Unable to mark raw files as completed for schema %s", schema_id)
        return {
            "success": True,
            "message": message,
            "transformed": transformed,
            "skipped": skipped,
            "rules": rules.count(),
            "deleted": deleted_count,
            "errors": errors[:10],
        }
    except Exception as exc:
        logger.exception("Failed transforming observations for schema %s: %s", schema_id, exc)
        
        # Determine if this is a retryable error
        retryable_errors = (DatabaseError, IntegrityError, ConnectionError, TimeoutError)
        is_retryable = isinstance(exc, retryable_errors)
        
        # Try to retry if it's a retryable error and we haven't exceeded max retries
        if is_retryable and self.request.retries < self.max_retries:
            logger.warning(
                "Retryable error in transformation task for schema %s (attempt %d/%d): %s",
                schema_id, self.request.retries + 1, self.max_retries + 1, str(exc)
            )
            # Update status to indicate retry
            try:
                RawDataFile.objects.filter(last_transformation_schema_id=schema_id).update(
                    transformation_status="in_progress",
                    transformation_message=f"Retrying transformation (attempt {self.request.retries + 2}/{self.max_retries + 1})...",
                )
            except Exception:
                pass
            
            # Retry with exponential backoff
            retry_delay = self.default_retry_delay * (2 ** self.request.retries)
            raise self.retry(countdown=retry_delay, exc=exc)
        
        # Mark files as failed after max retries or non-retryable error
        error_message = str(exc)
        if self.request.retries >= self.max_retries:
            error_message = f"Failed after {self.max_retries + 1} attempts: {error_message}"
        
        try:
            RawDataFile.objects.filter(last_transformation_schema_id=schema_id).update(
                transformation_status="failed",
                transformation_message=error_message,
            )
        except Exception:
            pass
        return {"success": False, "message": error_message}


@shared_task(bind=True)
def detect_duplicates_task(self, raw_data_file_id: int) -> dict[str, Any]:
    """
    Background task to detect duplicate observations in a raw data file.
    
    Args:
        raw_data_file_id: ID of the RawDataFile to check
        
    Returns:
        Dict with duplicate detection results
    """
    try:
        from .duplicate_detection import (
            find_duplicate_observations,
            find_multi_value_observations,
        )
        
        raw_data_file = RawDataFile.objects.get(id=raw_data_file_id)
        
        logger.info(
            "Starting duplicate detection for file %s (ID: %s)",
            raw_data_file.original_filename,
            raw_data_file_id,
        )
        
        # Run duplicate detection with limits for performance
        duplicates_info = find_duplicate_observations(
            raw_data_file=raw_data_file,
            limit=100,  # Only get details for first 100 groups
        )
        
        conflicts_info = find_multi_value_observations(
            raw_data_file=raw_data_file,
            limit=50,  # Only get details for first 50 conflicts
        )
        
        # Store results in a cache field on the model if you add one
        # For now, just return the results
        
        result = {
            'success': True,
            'file_id': raw_data_file_id,
            'duplicates': duplicates_info,
            'conflicts': conflicts_info,
            'message': (
                f"Found {duplicates_info.get('total_duplicates', 0)} duplicate observations "
                f"in {duplicates_info.get('duplicate_groups', 0)} groups, "
                f"and {conflicts_info.get('total_conflicts', 0)} data conflicts"
            ),
        }
        
        logger.info(
            "Duplicate detection complete for file %s: %s",
            raw_data_file.original_filename,
            result['message'],
        )
        
        return result
        
    except RawDataFile.DoesNotExist:
        error_msg = f"RawDataFile with ID {raw_data_file_id} not found"
        logger.error(error_msg)
        return {
            'success': False,
            'file_id': raw_data_file_id,
            'message': error_msg,
        }
        
    except Exception as exc:
        logger.exception(
            "Error detecting duplicates for raw data file %s: %s",
            raw_data_file_id,
            exc,
        )
        return {
            'success': False,
            'file_id': raw_data_file_id,
            'message': f"Error detecting duplicates: {str(exc)}",
        }


@shared_task(bind=True)
def delete_duplicates_task(self, raw_data_file_id: int) -> dict[str, Any]:
    """
    Background task to delete ALL duplicate observations in a raw data file.
    Keeps the first occurrence of each duplicate group.
    
    Args:
        raw_data_file_id: ID of the RawDataFile to clean
        
    Returns:
        Dict with deletion results
    """
    try:
        from django.db.models import Count
        from django.contrib.postgres.aggregates import ArrayAgg
        from core.models import Observation
        
        raw_data_file = RawDataFile.objects.get(id=raw_data_file_id)
        
        logger.info(
            "Starting duplicate deletion for file %s (ID: %s)",
            raw_data_file.original_filename,
            raw_data_file_id,
        )
        
        # Find ALL duplicate groups (no limit) using direct database query
        # Note: Observations are linked to raw data files through attribute->study relationship
        duplicate_groups = (
            Observation.objects.filter(attribute__studies=raw_data_file.study)
            .values('patient_id', 'attribute_id', 'time_id', 'location_id',
                   'float_value', 'int_value', 'text_value', 'boolean_value', 'datetime_value')
            .annotate(
                count=Count('id'),
                ids=ArrayAgg('id', ordering='id')  # Get all IDs, ordered
            )
            .filter(count__gt=1)  # Only groups with duplicates
        )
        
        # Collect IDs to delete (keep first, delete rest)
        ids_to_delete = []
        total_groups = 0
        
        for group in duplicate_groups:
            obs_ids = group['ids']
            if len(obs_ids) > 1:
                total_groups += 1
                # Keep the first ID, delete the rest
                ids_to_delete.extend(obs_ids[1:])
        
        # Perform deletion
        if ids_to_delete:
            deleted_count = Observation.objects.filter(id__in=ids_to_delete).delete()[0]
            message = (
                f"Deleted {deleted_count} duplicate observations across {total_groups} groups. "
                f"Kept the first occurrence of each duplicate."
            )
            success = True
        else:
            deleted_count = 0
            message = "No duplicates found to delete."
            success = True
        
        logger.info(
            "Duplicate deletion complete for file %s: %s",
            raw_data_file.original_filename,
            message,
        )
        
        return {
            'success': success,
            'file_id': raw_data_file_id,
            'deleted_count': deleted_count,
            'duplicate_groups': total_groups,
            'message': message,
        }
        
    except RawDataFile.DoesNotExist:
        error_msg = f"RawDataFile with ID {raw_data_file_id} not found"
        logger.error(error_msg)
        return {
            'success': False,
            'file_id': raw_data_file_id,
            'message': error_msg,
        }
        
    except Exception as exc:
        logger.exception(
            "Error deleting duplicates for raw data file %s: %s",
            raw_data_file_id,
            exc,
        )
        return {
            'success': False,
            'file_id': raw_data_file_id,
            'message': f"Error deleting duplicates: {str(exc)}",
        }
