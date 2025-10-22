import csv
import io
import json
import logging
from datetime import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.http import (
    FileResponse,
    Http404,
    HttpResponseForbidden,
    JsonResponse,
    StreamingHttpResponse,
)
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.utils.text import slugify
from django.forms import formset_factory
from django.views.decorators.http import require_http_methods, require_POST
from django.db.models import Count
from django.contrib.postgres.aggregates import ArrayAgg

from core.models import Study, Attribute, Observation
from .models import MappingSchema, MappingRule, RawDataFile, RawDataColumn
from core.forms import VariableConfirmationFormSetFactory
from .forms import (
    ExportDataForm,
    MappingRuleForm,
    MappingSchemaForm,
    RawDataUploadForm,
)
from .utils import (
    validate_raw_data_against_codebook,
    analyze_raw_data_columns,
    suggest_column_mappings,
)
from .tasks import ingest_raw_data_file
from .eda_service import generate_eda_summary

logger = logging.getLogger(__name__)


def _user_can_export_raw_data(user, raw_data_file: RawDataFile) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if raw_data_file.uploaded_by_id == user.id:
        return True
    if raw_data_file.study_id and raw_data_file.study.created_by_id == user.id:
        return True
    if (
        raw_data_file.study_id
        and raw_data_file.study.project_id
        and raw_data_file.study.project.created_by_id == user.id
    ):
        return True
    if user.is_staff or user.is_superuser:
        return True
    else:
        return False


def _serialize_observation_value(observation: Observation) -> str:
    attribute = observation.attribute
    attr_type = getattr(attribute, "variable_type", None)
    preferred_candidates = []
    if attr_type == "float":
        preferred_candidates.append(observation.float_value)
    elif attr_type == "int":
        preferred_candidates.append(observation.int_value)
    elif attr_type in {"string", "categorical"}:
        preferred_candidates.append(observation.text_value)
    elif attr_type == "boolean":
        preferred_candidates.append(observation.boolean_value)
    elif attr_type == "datetime":
        preferred_candidates.append(observation.datetime_value)

    fallback_candidates = (
        observation.float_value,
        observation.int_value,
        observation.text_value,
        observation.boolean_value,
        observation.datetime_value,
    )

    value = next(
        (
            candidate
            for candidate in preferred_candidates + list(fallback_candidates)
            if candidate is not None and candidate != ""
        ),
        None,
    )

    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return ""
    return str(value)


def _serialize_time_dimension(time_dimension) -> str:
    if not time_dimension:
        return ""
    if getattr(time_dimension, "timestamp", None):
        return time_dimension.timestamp.isoformat()

    start = getattr(time_dimension, "start_date", None)
    end = getattr(time_dimension, "end_date", None)
    if start and end and start != end:
        return f"{start.isoformat()} / {end.isoformat()}"
    if start:
        return start.isoformat()
    if end:
        return end.isoformat()
    return ""


def _harmonised_csv_stream(queryset, schema, raw_data_file, exported_at: str, user_id: int):
    header = [
        "patient_id",
        "attribute_variable_name",
        "attribute_display_name",
        "variable_type",
        "value",
        "datetime",
        "location_name",
        "schema_id",
        "source_file_id",
        "exported_at",
        "observation_id",
    ]

    buffer = io.StringIO()
    writer = csv.writer(buffer)

    writer.writerow(header)
    buffer.seek(0)
    yield buffer.read()
    buffer.truncate(0)
    buffer.seek(0)

    row_count = 0
    for observation in queryset.iterator(chunk_size=1000):
        patient_identifier = ""
        if observation.patient_id:
            patient_identifier = (
                getattr(observation.patient, "unique_id", None)
                or str(observation.patient_id)
            )

        location_name = ""
        if observation.location_id:
            location_name = getattr(observation.location, "name", "") or ""

        writer.writerow(
            [
                patient_identifier,
                observation.attribute.variable_name,
                observation.attribute.display_name or "",
                observation.attribute.variable_type,
                _serialize_observation_value(observation),
                _serialize_time_dimension(observation.time),
                location_name,
                schema.id,
                raw_data_file.id,
                exported_at,
                observation.id,
            ]
        )

        buffer.seek(0)
        yield buffer.read()
        buffer.truncate(0)
        buffer.seek(0)
        row_count += 1

    logger.info(
        "User %s exported harmonised observations for raw data file %s (schema %s); rows=%s",
        user_id,
        raw_data_file.id,
        schema.id,
        row_count,
    )
@login_required
def map_codebook(request, study_id):
    """
    Map codebook columns to health attribute schema.
    This is the first step in the harmonisation process.
    Uses unified codebook processing utility.
    """
    study = get_object_or_404(Study, id=study_id, created_by=request.user, study_purpose='source')
    
    from core.utils import process_codebook_mapping
    result = process_codebook_mapping(request, study, codebook_type='source')
    
    # If result is a redirect, return it
    if hasattr(result, 'status_code'):
        return result
    
    # If result is a context dictionary, render the template
    if isinstance(result, dict):
        return render(request, 'health/map_codebook.html', result)
    
    # This shouldn't happen, but provide a fallback
    messages.error(request, 'Unexpected error processing source codebook mapping.')
    return redirect('core:study_detail', pk=study.pk)


@login_required 
def extract_variables(request, study_id):
    """
    Extract variables from codebook using the column mapping.
    Uses unified codebook processing utility.
    Placeholder for LLM integration to enhance variable metadata.
    """
    study = get_object_or_404(Study, id=study_id, created_by=request.user, study_purpose='source')
    
    from core.utils import process_codebook_extraction
    result = process_codebook_extraction(request, study, codebook_type='source')
    
    # TODO: LLM Integration placeholder
    # This is where we would enhance variable metadata using AI:
    # - Improve display names for health-specific context
    # - Generate descriptions for missing health variables
    # - Suggest appropriate health categories (vital signs, demographics, etc.)
    # - Recommend variable types based on health data standards
    # - Add SNOMED-CT, LOINC, or other health ontology codes
    # 
    # Example integration points:
    # variables_data = enhance_health_variables_with_llm(
    #     variables_data, 
    #     study_context=study,
    #     health_standards=['SNOMED-CT', 'LOINC', 'ICD-10']
    # )
    
    messages.info(
        request,
        'TODO: LLM integration will be added here to enhance variable metadata '
        'with health-specific knowledge and ontology mappings.'
    )
    
    return result


@login_required
def start_harmonisation(request, study_id):
    """Create a new MappingSchema and redirect to harmonization dashboard."""
    from .utils import MessageManager
    
    source_study = get_object_or_404(
        Study, id=study_id, created_by=request.user, study_purpose="source",
    )
    
    # Check if a mapping schema already exists
    existing_schema = MappingSchema.objects.filter(source_study=source_study).first()
    if existing_schema:
        MessageManager.info(request, "Using existing harmonization schema.")
        return redirect("health:harmonization_dashboard", schema_id=existing_schema.id)
    
    if request.method == "POST":
        form = MappingSchemaForm(
            request.POST, source_study=source_study, user=request.user,
        )
        if form.is_valid():
            schema: MappingSchema = form.save(commit=False)
            schema.source_study = source_study
            schema.created_by = request.user
            schema.save()
            MessageManager.success(
                request,
                "Harmonization schema created successfully!",
            )
            return redirect("health:harmonization_dashboard", schema_id=schema.id)
        MessageManager.error(request, "Please correct the errors below.")
    else:
        form = MappingSchemaForm(source_study=source_study, user=request.user)

    # Render schema creation form (GET)
    return render(
        request,
        "health/start_harmonisation.html",
        {"form": form, "study": source_study},
    )
@login_required
def start_eda_generation(request, file_id):
    """Start EDA generation if caches are absent. POST only for safety."""
    if request.method != 'POST':
        return HttpResponseBadRequest('POST required')
    raw_data_file = get_object_or_404(RawDataFile, id=file_id, uploaded_by=request.user)
    clear = request.POST.get('clear') == '1'
    if clear:
        raw_data_file.eda_cache_source = None
        raw_data_file.eda_cache_source_generated_at = None
        raw_data_file.eda_cache_transformed = None
        raw_data_file.eda_cache_transformed_generated_at = None
        raw_data_file.save(update_fields=[
            'eda_cache_source','eda_cache_source_generated_at',
            'eda_cache_transformed','eda_cache_transformed_generated_at'
        ])
    try:
        from .tasks import generate_eda_caches
        async_result = generate_eda_caches.delay(raw_data_file.id)
        return JsonResponse({'started': True, 'task_id': async_result.id})
    except Exception as exc:  # pragma: no cover
        logger.exception('Failed to enqueue EDA generation for file %s', raw_data_file.id)
        return JsonResponse({'started': False, 'error': str(exc)}, status=500)


@login_required
def eda_status(request, file_id):
    """Return JSON status for EDA cache availability so the frontend can poll."""
    raw_data_file = get_object_or_404(RawDataFile, id=file_id, uploaded_by=request.user)
    data = {
        'source_available': bool(raw_data_file.eda_cache_source),
        'source_generated_at': raw_data_file.eda_cache_source_generated_at.isoformat() if raw_data_file.eda_cache_source_generated_at else None,
        'transformed_available': bool(raw_data_file.eda_cache_transformed),
        'transformed_generated_at': raw_data_file.eda_cache_transformed_generated_at.isoformat() if raw_data_file.eda_cache_transformed_generated_at else None,
        'has_transformed_data': raw_data_file.transformation_status == 'completed' and raw_data_file.last_transformation_schema_id is not None,
    }
    return JsonResponse(data)


@login_required
def approve_mapping(request, schema_id):
    """Approve a harmonisation mapping schema.

    Conditions:
    - Requires at least one mapping rule that is not marked not_mappable and has a target attribute.
    - Idempotent: if already approved, simply inform user.
    Side-effects:
    - Sets approval metadata.
    - Optionally kicks off background transformation if raw data files exist and no transformations currently running.
    """
    schema = get_object_or_404(MappingSchema, id=schema_id)

    if schema.status == 'approved':
        messages.info(request, 'Mapping schema already approved.')
        return redirect('health:harmonization_dashboard', schema_id=schema.id)

    # Ensure at least one complete rule
    complete_rules_qs = MappingRule.objects.filter(
        schema=schema,
        not_mappable=False,
        target_attribute__isnull=False,
    )
    if not complete_rules_qs.exists():
        messages.error(request, 'Cannot approve schema without at least one completed mapping rule.')
        return redirect('health:harmonization_dashboard', schema_id=schema.id)

    schema.status = 'approved'
    schema.approved_by = request.user
    schema.approved_at = timezone.now()
    schema.save(update_fields=['status', 'approved_by', 'approved_at'])
    messages.success(request, 'Mapping schema approved.')

    # Trigger transformation if raw data files exist
    raw_files = RawDataFile.objects.filter(study=schema.source_study)
    if raw_files.exists():
        # Queue transformation task; best-effort
        try:
            from .tasks import transform_observations_for_schema
            transform_observations_for_schema.delay(schema.id)
            messages.info(request, 'Started background harmonisation transformation.')
        except Exception:  # pragma: no cover
            logger.exception('Failed to enqueue transformation for schema %s', schema.id)
            messages.warning(request, 'Approved, but could not start transformation task. You may retry from the dashboard.')
    else:
        messages.info(request, 'No raw data files found yet; upload files to start transformations.')

    return redirect('health:harmonization_dashboard', schema_id=schema.id)


@login_required
def finalize_harmonisation(request, schema_id):
    """Mark the study as harmonised after approval."""
    schema = get_object_or_404(MappingSchema, id=schema_id, created_by=request.user)
    if schema.status != "approved":
        messages.error(request, "You must approve the mapping before finalising.")
        return redirect("health:harmonization_dashboard", schema_id=schema.id)
    study = schema.source_study
    study.status = "harmonised"
    study.save(update_fields=["status"])
    messages.success(request, f"Study '{study.name}' marked as harmonised.")
    return redirect("core:study_detail", pk=study.pk)


@login_required
@require_http_methods(["POST"])  
def rerun_harmonisation_transformations(request, schema_id):
    """Re-queue harmonised observation generation for an approved mapping schema."""
    from .utils import MessageManager
    
    schema = get_object_or_404(MappingSchema, id=schema_id, created_by=request.user)

    if schema.status != "approved":
        MessageManager.error(
            request,
            "You can only re-run harmonisation after the mapping has been approved.",
        )
        return redirect("health:harmonization_dashboard", schema_id=schema.id)

    from .tasks import transform_observations_for_schema

    # Check current transformation status to provide appropriate messaging
    source_files = RawDataFile.objects.filter(study=schema.source_study)
    current_statuses = list(source_files.values_list('transformation_status', flat=True).distinct())
    
    if 'in_progress' in current_statuses:
        transformation_message = "Restarting stuck or slow transformation..."
        success_message = "Transformation restarted. Previous in-progress transformation was cancelled."
    elif 'failed' in current_statuses:
        transformation_message = "Retrying failed transformation..."
        success_message = "Failed transformation is being retried. Check raw data files for progress updates."
    else:
        transformation_message = "Queued for harmonisation re-run..."
        success_message = "Previous harmonised results cleared and re-run started. Check raw data files for progress updates."

    try:
        source_files.update(
            transformation_status="in_progress",
            transformation_started_at=timezone.now(),
            transformation_message=transformation_message,
            last_transformation_schema=schema,
        )
    except Exception:
        # Non-critical: we still proceed with re-queuing even if status update fails.
        pass

    try:
        task = transform_observations_for_schema.delay(schema.id, delete_existing=True)
    except Exception:
        logger.exception("Failed to re-queue harmonisation for schema %s", schema.id)
        MessageManager.error(
            request,
            "We could not start the harmonisation re-run. Please try again shortly.",
        )
        return redirect("health:harmonization_dashboard", schema_id=schema.id)

    MessageManager.success(request, success_message)
    logger.info(
        "Re-running harmonisation task %s for schema %s by user %s (previous status: %s)",
        getattr(task, "id", "unknown"),
        schema.id,
        request.user.id,
        current_statuses,
    )
    return redirect("health:harmonization_dashboard", schema_id=schema.id)


def _apply_universal_mappings(schema):
    """Helper function to apply universal mappings to schema rules"""
    mapping_rules = MappingRule.objects.filter(schema=schema)

    # Apply universal patient_id mapping
    if schema.universal_patient_id:
        patient_id_rules = mapping_rules.filter(role="patient_id")
        for rule in patient_id_rules:
            if not rule.target_attribute:
                patient_id_attr = Attribute.objects.filter(
                    studies=schema.target_study,
                    variable_name="patient_id",
                ).first()

                if patient_id_attr:
                    rule.target_attribute = patient_id_attr
                    rule.save()

    # Apply universal datetime mapping
    if schema.universal_datetime:
        datetime_rules = mapping_rules.filter(role="datetime")
        for rule in datetime_rules:
            if not rule.target_attribute:
                datetime_attr = Attribute.objects.filter(
                    studies=schema.target_study,
                    variable_name="datetime",
                ).first()

                if datetime_attr:
                    rule.target_attribute = datetime_attr
                    rule.save()


@login_required
def select_variables(request, study_id):  # noqa: C901 (complexity accepted temporarily)
    """
    Let user select which extracted variables to include in the study.
    """
    study = get_object_or_404(
        Study, id=study_id, created_by=request.user, study_purpose="source",
    )
    
    # Get variables data from session
    variables_data = request.session.get(f"variables_data_{study.id}")
    if not variables_data:
        messages.error(
            request,
            "No variable data found. Please extract variables first.",
        )
        return redirect(
            "health:extract_variables", study_id=study.id,
        )
    
    # Handle form submission (user selecting variables)
    if request.method == "POST":
        try:
            formset = VariableConfirmationFormSetFactory(
                data=request.POST, variables_data=variables_data,
            )
        except (ValueError, TypeError) as exc:
            messages.error(
                request,
                f"Form processing error: {exc!s}. Please try again.",
            )
            return redirect(
                "health:select_variables", study_id=study.id,
            )
        
        if formset.is_valid():
            included_variables = formset.get_included_variables()
            
            if not included_variables:
                messages.error(request, "You must include at least one variable in your study.")
            else:
                # Create Attribute objects for included variables
                created_attributes = []
                for var_data in included_variables:
                    try:
                        attribute, _created = Attribute.objects.get_or_create(
                            variable_name=var_data["variable_name"],
                            source_type="source",  # ensure classification
                            defaults={
                                "display_name": var_data.get(
                                    "display_name", var_data["variable_name"],
                                ),
                                "description": var_data.get("description", ""),
                                "variable_type": var_data.get(
                                    "variable_type", "string",
                                ),
                                "unit": var_data.get("unit", ""),
                                "ontology_code": var_data.get(
                                    "ontology_code", "",
                                ),
                                "category": var_data.get(
                                    "category", "health",
                                ),  # Use extracted category or default to health
                            },
                        )
                        created_attributes.append(attribute)
                    except (ValueError, TypeError) as exc:
                        messages.error(
                            request,
                            f"Error creating variable {var_data['variable_name']}: {exc!s}",
                        )
                        return render(
                            request,
                            "health/select_variables.html",
                            {
                                "study": study,
                                "formset": formset,
                                "variables_count": len(variables_data),
                                "page_title": f"Select Health Variables - {study.name}",
                            },
                        )
                # Post-processing after variable creation
                study.variables.set(created_attributes)
                study.status = "variables_extracted"
                study.save(update_fields=["status"])
                # Clear session data
                request.session.pop(f"variables_data_{study.id}", None)
                request.session.pop(f"column_mapping_{study.id}", None)
                messages.success(
                    request,
                    (
                        f"Successfully added {len(included_variables)} variables to your study! "
                        "You can now proceed with health data harmonisation."
                    ),
                )
                # If a raw data file exists for this study, redirect to validation step
                raw_data_file = study.raw_data_files.order_by('-uploaded_at').first()
                if raw_data_file:
                    return redirect('health:validate_raw_data', file_id=raw_data_file.id)
                return redirect("core:study_detail", pk=study.pk)
        else:
            # Collect and display specific formset errors
            error_messages = []
            
            # Check for non-field errors
            if formset.non_form_errors():
                error_messages.extend(
                    [f"Form error: {err}" for err in formset.non_form_errors()],
                )
            
            # Check for individual form errors (only for selected variables)
            for i, form in enumerate(formset):
                # Only report errors for forms that are marked for inclusion
                is_included = (
                    form.cleaned_data.get("include", False)
                    if form.cleaned_data
                    else False
                )
                if form.errors and is_included:
                    variable_name = form.cleaned_data.get(
                        "variable_name",
                        f"Variable {i+1}",
                    )
                    for field, fld_errors in form.errors.items():
                        error_messages.extend(
                            [
                                f"{variable_name} - {field}: {err}"
                                for err in fld_errors
                            ],
                        )
            
            if error_messages:
                for error_msg in error_messages:
                    messages.error(request, error_msg)
            else:
                messages.error(request, "Please correct the form errors and try again.")
    else:
        # Create the formset with initial data
        try:
            formset = VariableConfirmationFormSetFactory(
                variables_data=variables_data,
            )
        except (ValueError, TypeError) as exc:
            messages.error(
                request,
                f"Error creating form: {exc!s}. Please check your variable data.",
            )
            return redirect(
                "health:extract_variables", study_id=study.id,
            )
    
    context = {
        "study": study,
        "formset": formset,
        "variables_count": len(variables_data),
        "page_title": f"Select Health Variables - {study.name}",
    }
    return render(request, "health/select_variables.html", context)


@login_required
def reset_variables(request, study_id):
    """
    Reset all variables for a study and clear related session data.
    """
    study = get_object_or_404(
        Study,
        id=study_id,
        created_by=request.user,
        study_purpose="source",
    )
    
    if request.method == "POST":
        # Clear all variables associated with the study
        variables_count = study.variables.count()
        study.variables.clear()

        # Clear session data related to this study
        request.session.pop(f"variables_data_{study.id}", None)
        request.session.pop(f"column_mapping_{study.id}", None)

        # Reset study status
        study.status = "created"
        study.save(update_fields=["status"])

        messages.success(
            request,
            f"Successfully reset {variables_count} variables. "
            f"You can now restart the harmonisation workflow.",
        )
        return redirect("core:study_detail", pk=study.pk)
    
    # If GET request, just redirect back
    return redirect("core:study_detail", pk=study.pk)


@login_required
def harmonization_dashboard(request, schema_id):
    """Unified harmonization dashboard for mapping all variables."""
    schema = get_object_or_404(MappingSchema, id=schema_id)
    source_attrs = list(schema.source_study.variables.order_by("variable_name"))
    
    if not source_attrs:
        messages.error(request, "No variables found for this schema.")
        return redirect("health:mapping_list")
    
    # Handle universal settings form
    if request.method == 'POST' and 'update_universal_settings' in request.POST:
        universal_form = MappingSchemaForm(
            request.POST, 
            instance=schema,
            source_study=schema.source_study,
            user=request.user,
        )
        if universal_form.is_valid():
            schema = universal_form.save(commit=False)
            schema.auto_populate_enabled = True  # Always enable auto-population
            schema.save()
            messages.success(request, "Universal settings updated successfully.")
            
            # Apply universal settings to existing rules if requested
            if schema.auto_populate_enabled:
                _apply_universal_mappings(schema)
                messages.info(request, "Universal settings applied to existing mappings.")
            
            return redirect("health:harmonization_dashboard", schema_id=schema_id)
        else:
            error_details = "; ".join(
                [f"{field}: {', '.join(errors)}" for field, errors in universal_form.errors.items()]
            )
            messages.error(
                request,
                f"Please correct the errors in universal settings. {error_details}"
            )
    
    # Handle apply universal settings to existing mappings
    elif request.method == 'POST' and 'apply_universal_settings' in request.POST:
        try:
            _apply_universal_mappings(schema)
            messages.success(request, "Universal settings applied to all existing mappings.")
        except Exception as e:
            messages.error(request, f"Error applying universal settings: {e}")
        return redirect("health:harmonization_dashboard", schema_id=schema_id)
    
    # Handle individual variable mapping
    elif request.method == 'POST':
        # Process individual variable forms
        updated_count = 0
        errors = []
        processed_variable_name = None
        is_complete = False
        
        for attr in source_attrs:
            field_prefix = f"variable_{attr.id}"
            if any(key.startswith(field_prefix) for key in request.POST.keys()):
                # Store the variable name for AJAX response
                processed_variable_name = attr.variable_name
                
                # Get or create mapping rule
                mapping_rule, created = MappingRule.objects.get_or_create(
                    schema=schema,
                    source_attribute=attr,
                    defaults={
                        'role': 'value',
                        'related_relation_type': schema.universal_relation_type,
                    }
                )
                
                # Create form with prefix
                form = MappingRuleForm(
                    request.POST, 
                    instance=mapping_rule, 
                    schema=schema,
                    prefix=field_prefix
                )
                
                if form.is_valid():
                    rule = form.save(commit=False)
                    rule.source_attribute = attr
                    rule.schema = schema
                    rule.save()
                    updated_count += 1
                    is_complete = rule.target_attribute is not None
                else:
                    for field, field_errors in form.errors.items():
                        for error in field_errors:
                            errors.append(f"{attr.variable_name} - {field}: {error}")
        
        if updated_count > 0:
            messages.success(request, f"Updated {updated_count} variable mappings.")
        
        if errors:
            for error in errors[:5]:  # Show first 5 errors
                messages.error(request, error)
            if len(errors) > 5:
                messages.error(request, f"... and {len(errors) - 5} more errors.")
            
            # Return JSON response for AJAX requests
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': False,
                    'errors': errors[:10],  # Return first 10 errors for AJAX
                })
        
        if not errors:
            # Return JSON response for AJAX requests
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': True,
                    'message': f'Updated {updated_count} variable mappings.',
                    'updated_count': updated_count,
                    'variable_name': processed_variable_name,
                    'is_complete': is_complete,
                })
            return redirect("health:harmonization_dashboard", schema_id=schema_id)
    
    # Initialize forms for all variables
    variable_forms = []
    
    # Only create universal form with POST data if we're updating universal settings
    if request.method == 'POST' and 'update_universal_settings' in request.POST:
        universal_form = MappingSchemaForm(
            request.POST, 
            instance=schema, 
            source_study=schema.source_study, 
            user=request.user,
        )
    else:
        # Ensure auto_populate_enabled is always True for new instances
        if not schema.auto_populate_enabled:
            schema.auto_populate_enabled = True
            schema.save()
        
        universal_form = MappingSchemaForm(
            instance=schema, 
            source_study=schema.source_study, 
            user=request.user,
        )
    
    for attr in source_attrs:
        # Get or create mapping rule
        mapping_rule, created = MappingRule.objects.get_or_create(
            schema=schema,
            source_attribute=attr,
            defaults={
                "role": "value",
                "related_relation_type": schema.universal_relation_type or "",
                "patient_id_attribute": schema.universal_patient_id,
                "datetime_attribute": schema.universal_datetime,
            },
        )
        
        # Auto-populate role from universal settings if enabled and rule is new
        if created and schema.auto_populate_enabled:
            if schema.universal_patient_id == attr:
                mapping_rule.role = "patient_id"
            elif schema.universal_datetime == attr:
                mapping_rule.role = "datetime"
            mapping_rule.save()
        
        # Pre-populate form fields with universal settings if they're not already set
        if schema.auto_populate_enabled:
            if not mapping_rule.patient_id_attribute and schema.universal_patient_id:
                mapping_rule.patient_id_attribute = schema.universal_patient_id
            if not mapping_rule.datetime_attribute and schema.universal_datetime:
                mapping_rule.datetime_attribute = schema.universal_datetime
            if not mapping_rule.related_relation_type and schema.universal_relation_type:
                mapping_rule.related_relation_type = schema.universal_relation_type
            # Save only if we made changes and the rule already exists
            if not created:
                mapping_rule.save()
        
        # Create form with prefix for this variable
        form = MappingRuleForm(
            instance=mapping_rule, 
            schema=schema,
            prefix=f"variable_{attr.id}",
        )
        
        variable_forms.append({
            'attribute': attr,
            'form': form,
            'mapping_rule': mapping_rule,
            'is_complete': mapping_rule.target_attribute is not None or mapping_rule.not_mappable,
            'instance': mapping_rule,  # Add instance for template access
        })
    
    # Progress tracking - count mapped variables and not mappable variables as "complete"
    completed_rules = sum(1 for vf in variable_forms if vf['is_complete'])
    not_mappable_count = sum(1 for vf in variable_forms if vf['mapping_rule'].not_mappable)
    mappable_variables = len(source_attrs) - not_mappable_count
    progress_percent = int((completed_rules / len(source_attrs)) * 100) if source_attrs else 0
    
    # Get raw data files for the source study
    raw_data_files = RawDataFile.objects.filter(study=schema.source_study).order_by('-uploaded_at')
    has_raw_data = raw_data_files.exists()
    
    context = {
        "schema": schema,
        "universal_form": universal_form,
        "variable_forms": variable_forms,
        "total_variables": len(source_attrs),
        "completed_rules": completed_rules,
        "not_mappable_count": not_mappable_count,
        "progress_percent": progress_percent,
        "raw_data_files": raw_data_files,
        "has_raw_data": has_raw_data,
        "page_title": "Harmonise Study Dashboard",
    }
    
    return render(request, 'health/harmonization_dashboard.html', context)


@login_required
def study_harmonization_dashboard(request, study_id):
    """Redirect to harmonization dashboard for a study's most recent mapping schema."""
    study = get_object_or_404(Study, id=study_id)

    # Find the most recent mapping schema for this study as source
    mapping_schema = MappingSchema.objects.filter(
        source_study=study).order_by("-created_at").first()

    if not mapping_schema:
        messages.info(request, "No harmonization schema found. Let's create one first.")
        return redirect("health:start_harmonisation", study_id=study_id)

    return redirect("health:harmonization_dashboard", schema_id=mapping_schema.id)


# Data Ingestion Views

@login_required
def upload_raw_data(request, study_id=None):
    """
    Upload raw data files for a study.
    Simple initial implementation focusing on file upload and basic validation.
    """
    study = None
    if study_id:
        study = get_object_or_404(Study, id=study_id, study_purpose='source')
        # Check if user has permission to upload data for this study
        if study.created_by != request.user:
            messages.error(request, "You don't have permission to upload data for this study.")
            return redirect('core:study_detail', pk=study.pk)
    
    # Check if study has variables
    has_variables = study.variables.exists() if study else False
    
    if request.method == 'POST':
        form = RawDataUploadForm(request.POST, request.FILES, user=request.user)
        
        # Make patient_id_column and date_column optional if study has no variables
        if not has_variables:
            form.fields['patient_id_column'].required = False
            form.fields['date_column'].required = False
        
        if form.is_valid():
            raw_data_file = form.save(commit=False)
            raw_data_file.uploaded_by = request.user
            
            # If study was pre-selected, use it
            if study:
                raw_data_file.study = study
            else:
                raw_data_file.study = form.cleaned_data.get("study")

            # Persist selected column names onto the model
            raw_data_file.patient_id_column = form.cleaned_data.get("patient_id_column") or ""
            raw_data_file.date_column = form.cleaned_data.get("date_column") or ""

            # Compute checksum for duplicate detection
            import hashlib
            upload = request.FILES.get('file')
            checksum = ''
            if upload:
                pos = upload.tell() if hasattr(upload, 'tell') else None
                try:
                    upload.seek(0)
                except Exception:
                    pass
                hasher = hashlib.sha256()
                for chunk in upload.chunks():
                    hasher.update(chunk)
                checksum = hasher.hexdigest()
                # reset pointer for subsequent reads and save
                try:
                    upload.seek(0)
                except Exception:
                    pass
                raw_data_file.checksum = checksum

            # Duplicate check within same study by checksum
            duplicate = None
            if raw_data_file.study and checksum:
                duplicate = RawDataFile.objects.filter(
                    study=raw_data_file.study,
                    checksum=checksum,
                ).first()
            if duplicate:
                messages.warning(
                    request,
                    f"An identical file ('{duplicate.original_filename}') was already uploaded for this study on {duplicate.uploaded_at:%Y-%m-%d %H:%M}. Upload cancelled.",
                )
                context = {
                    'form': form,
                    'study': raw_data_file.study,
                    'has_variables': has_variables,
                    'page_title': f"Upload Raw Data{' for ' + raw_data_file.study.name if raw_data_file.study else ''}",
                    'duplicate_found': True,
                }
                return render(request, 'health/upload_raw_data.html', context)

            # If study has no variables, extract columns from the file and redirect to variable selection
            if raw_data_file.study and not has_variables:
                try:
                    # Extract columns from uploaded file using existing validation function
                    file_ext = upload.name.split('.')[-1].lower()
                    
                    # Read file to get columns
                    import pandas as pd
                    if file_ext == 'csv':
                        df = pd.read_csv(upload, nrows=5)
                    elif file_ext in ['xlsx', 'xls']:
                        df = pd.read_excel(upload, nrows=5)
                    elif file_ext == 'json':
                        df = pd.read_json(upload, lines=True, nrows=5)
                    else:
                        raise ValueError(f"Unsupported file format: {file_ext}")
                    
                    # Reset file pointer for later use
                    try:
                        upload.seek(0)
                    except Exception:
                        pass
                    
                    columns = list(df.columns)
                    
                    if columns:
                        # Create variables data structure for select_variables view
                        variables_data = []
                        for col_name in columns:
                            variables_data.append({
                                "variable_name": str(col_name),
                                "display_name": str(col_name),
                                "description": f"Auto-extracted from raw data file: {raw_data_file.original_filename}",
                                "variable_type": "string",  # Default to string, user can change
                                "unit": "",
                                "ontology_code": "",
                                "category": "health",
                            })
                        
                        # Save the file first (but don't process yet)
                        raw_data_file.processing_status = 'pending'
                        raw_data_file.processing_message = 'Waiting for variable selection'
                        raw_data_file.save()
                        
                        # Store variables data in session
                        request.session[f"variables_data_{raw_data_file.study.id}"] = variables_data
                        
                        messages.info(
                            request,
                            f"No variables found for this study. Extracted {len(columns)} columns from your data file. "
                            f"Please review and select which variables to include.",
                        )
                        
                        # Redirect to select_variables to let user review
                        return redirect('health:select_variables', study_id=raw_data_file.study.id)
                    else:
                        raise ValueError("No columns found in file")
                        
                except Exception as e:
                    messages.error(
                        request,
                        f"Could not extract columns from file: {e}. "
                        f"Please ensure the file is a valid CSV, Excel, or JSON file with column headers.",
                    )
                    # Delete the raw data file since we can't process it
                    if raw_data_file.id:
                        raw_data_file.delete()
                    context = {
                        'form': form,
                        'study': raw_data_file.study,
                        'has_variables': has_variables,
                        'page_title': f"Upload Raw Data{' for ' + raw_data_file.study.name if raw_data_file.study else ''}",
                    }
                    return render(request, 'health/upload_raw_data.html', context)

            # Basic validation and column comparison against codebook
            mismatch_details = None
            if upload and raw_data_file.study:
                try:
                    validation_result = validate_raw_data_against_codebook(upload, raw_data_file.study)
                    details = validation_result.get('details') or {}
                    expected = details.get('expected_variables') or []
                    actual = details.get('actual_columns') or []
                    raw_data_file.detected_columns = actual
                    raw_data_file.expected_attributes = expected
                    # Compute simple mismatches
                    expected_set = {e.strip() for e in expected}
                    actual_set = {a.strip() for a in actual}
                    missing = sorted(list(expected_set - actual_set))
                    extra = sorted(list(actual_set - expected_set))
                    raw_data_file.missing_attributes = missing
                    raw_data_file.extra_columns = extra
                    raw_data_file.has_attribute_mismatches = bool(missing or extra)
                    if raw_data_file.has_attribute_mismatches:
                        mismatch_details = {
                            'missing': missing,
                            'extra': extra,
                            'expected_count': len(expected_set),
                            'actual_count': len(actual_set),
                        }
                        messages.warning(
                            request,
                            "The uploaded file's columns do not fully match the study's attributes. Review differences below.",
                        )
                except Exception as exc:
                    # Non-fatal: proceed but note issue
                    raw_data_file.processing_message = f"Validation warning: {exc}"
            
            raw_data_file.save()
            
            messages.success(
                request, 
                f"Successfully uploaded {raw_data_file.original_filename}. "
                f"File will be processed shortly."
            )
            
            # Redirect to file detail view (to be created) or back to study
            if raw_data_file.study:
                if mismatch_details:
                    # Show detail page with mismatches
                    return redirect('health:raw_data_detail', file_id=raw_data_file.id)
                return redirect('core:study_detail', pk=raw_data_file.study.pk)
            return redirect('health:raw_data_list')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        initial_data = {}
        if study:
            initial_data['study'] = study
        form = RawDataUploadForm(initial=initial_data, user=request.user)
    
    context = {
        'form': form,
        'study': study,
        'has_variables': has_variables,
        'page_title': f'Upload Raw Data{" for " + study.name if study else ""}',
    }
    
    return render(request, 'health/upload_raw_data.html', context)


@login_required
@require_http_methods(["POST"])
def reupload_raw_data(request, file_id):
    raw_data_file = get_object_or_404(
        RawDataFile,
        id=file_id,
        uploaded_by=request.user,
    )

    if raw_data_file.processing_status == "processing":
        messages.error(
            request,
            "Data ingestion is currently running for this file. Stop the job before re-uploading.",
        )
        return redirect("health:raw_data_detail", file_id=file_id)

    if raw_data_file.transformation_status == "in_progress":
        messages.error(
            request,
            "Transformation is currently in progress for this file. Wait for it to finish before re-uploading.",
        )
        return redirect("health:raw_data_detail", file_id=file_id)

    upload = request.FILES.get("raw_data_file")
    if not upload:
        messages.error(request, "Please select a file to upload.")
        return redirect("health:raw_data_detail", file_id=file_id)

    import hashlib

    if hasattr(upload, "seek"):
        try:
            upload.seek(0)
        except Exception:
            pass

    hasher = hashlib.sha256()
    for chunk in upload.chunks():
        hasher.update(chunk)
    checksum = hasher.hexdigest()

    if hasattr(upload, "seek"):
        try:
            upload.seek(0)
        except Exception:
            pass

    duplicate = (
        RawDataFile.objects.filter(study=raw_data_file.study, checksum=checksum)
        .exclude(id=raw_data_file.id)
        .first()
    )
    if duplicate:
        messages.warning(
            request,
            (
                "An identical file was already uploaded for this study on "
                f"{duplicate.uploaded_at:%Y-%m-%d %H:%M}."
            ),
        )
        return redirect("health:raw_data_detail", file_id=file_id)

    raw_data_file.columns.all().delete()

    if raw_data_file.file and raw_data_file.file.name:
        raw_data_file.file.delete(save=False)

    raw_data_file.reset_processing_state()
    raw_data_file.uploaded_at = timezone.now()
    raw_data_file.original_filename = upload.name
    raw_data_file.file_format = ""
    raw_data_file.file_size = upload.size
    raw_data_file.checksum = checksum
    raw_data_file.processing_message = "File re-uploaded; processing steps reset."

    raw_data_file.file.save(upload.name, upload, save=False)
    raw_data_file.save()

    messages.success(
        request,
        "Source data file replaced successfully. Please restart validation, mapping, and ingestion steps.",
    )

    logger.info(
        "Raw data file %s (ID %s) re-uploaded by %s",
        raw_data_file.original_filename,
        raw_data_file.id,
        request.user.username,
    )

    return redirect("health:raw_data_detail", file_id=file_id)


@login_required  
def raw_data_list(request):
    """
    List all raw data files uploaded by the user.
    Optionally filter by study if study parameter is provided.
    """
    # Get all raw data files for studies the user has access to
    raw_data_files = RawDataFile.objects.filter(
        uploaded_by=request.user
    ).select_related('study', 'uploaded_by').order_by('-uploaded_at')
    
    # Filter by study if provided
    study_filter = request.GET.get('study')
    filtered_study = None
    if study_filter:
        try:
            filtered_study = get_object_or_404(
                Study, 
                id=study_filter, 
                created_by=request.user
            )
            raw_data_files = raw_data_files.filter(study=filtered_study)
        except (ValueError, Http404):
            # Invalid study ID or user doesn't have access
            pass
    
    # Calculate some statistics for display
    total_files = raw_data_files.count()
    total_size = sum([f.file_size for f in raw_data_files if f.file_size])
    status_counts = {
        'uploaded': raw_data_files.filter(processing_status='uploaded').count(),
        'validation_error': raw_data_files.filter(processing_status='validation_error').count(),
        'validated': raw_data_files.filter(processing_status='validated').count(),
        'processed': raw_data_files.filter(processing_status='processed').count(),
        'processing': raw_data_files.filter(processing_status='processing').count(),
        'ingestion_error': raw_data_files.filter(processing_status='ingestion_error').count(),
        'ingested': raw_data_files.filter(processing_status='ingested').count(),
        'processed_with_errors': raw_data_files.filter(processing_status='processed_with_errors').count(),
        'error': raw_data_files.filter(processing_status='error').count(),
    }
    
    context = {
        'raw_data_files': raw_data_files,
        'filtered_study': filtered_study,
        'total_files': total_files,
        'total_size': total_size,
        'status_counts': status_counts,
        'page_title': f'Raw Data Files{" for " + filtered_study.name if filtered_study else ""}',
    }
    
    return render(request, 'health/raw_data_list.html', context)


@login_required
def raw_data_detail(request, file_id):
    """
    View details of a specific raw data file including column information.
    Supports viewing source data, transformed data, or both side-by-side.
    """
    raw_data_file = get_object_or_404(
        RawDataFile, 
        id=file_id, 
        uploaded_by=request.user
    )
    
    # Get view mode from query parameter (source, transformed, or both)
    view_mode = request.GET.get('view_mode', 'source')
    if view_mode not in ['source', 'transformed', 'both']:
        view_mode = 'source'
    
    # Determine if transformed data is available
    has_transformed_data = (
        raw_data_file.transformation_status == 'completed' and
        raw_data_file.last_transformation_schema_id is not None
    )
    
    # Get column information if available
    columns = raw_data_file.columns.all().order_by('column_index')
    
    # Status flags for cleaner template logic
    status = raw_data_file.processing_status
    validation_completed = status in ['validated', 'processed', 'processing', 'ingestion_error', 'ingested', 'processed_with_errors']
    mapping_completed = status in ['processed', 'processing', 'ingestion_error', 'ingested', 'processed_with_errors']
    
    # New async approach: only read existing caches; do not generate synchronously
    eda_summary = raw_data_file.eda_cache_source if view_mode in ['source', 'both'] else None
    eda_summary_transformed = None
    if view_mode in ['transformed', 'both'] and has_transformed_data:
        eda_summary_transformed = raw_data_file.eda_cache_transformed

    correlation_rows = []
    if isinstance(eda_summary, dict):
        corr = eda_summary.get("correlation")
        if isinstance(corr, dict) and corr.get("labels") and corr.get("matrix"):
            correlation_rows = list(zip(corr["labels"], corr["matrix"]))

    correlation_rows_transformed = []
    if isinstance(eda_summary_transformed, dict):
        corr = eda_summary_transformed.get("correlation")
        if isinstance(corr, dict) and corr.get("labels") and corr.get("matrix"):
            correlation_rows_transformed = list(zip(corr["labels"], corr["matrix"]))

    # Duplicate detection is now on-demand via background task
    # No longer runs automatically on page load for performance
    context = {
        'raw_data_file': raw_data_file,
        'columns': columns,
        'page_title': f'Raw Data: {raw_data_file.original_filename}',
        'validation_completed': validation_completed,
        'mapping_completed': mapping_completed,
        'view_mode': view_mode,
        'has_transformed_data': has_transformed_data,
        'eda_summary': eda_summary,
        'eda_summary_json': eda_summary,
        'eda_correlation_rows': correlation_rows,
        'eda_summary_transformed': eda_summary_transformed,
        'eda_summary_transformed_json': eda_summary_transformed,
        'eda_correlation_rows_transformed': correlation_rows_transformed,
        'eda_async_mode': True,
        'eda_source_available': bool(raw_data_file.eda_cache_source),
        'eda_transformed_available': bool(raw_data_file.eda_cache_transformed),
        'can_export_data': _user_can_export_raw_data(request.user, raw_data_file),
        'harmonised_export_available': has_transformed_data,
    }
    
    return render(request, 'health/raw_data_detail.html', context)


@login_required
@require_http_methods(["GET", "POST"])
def export_raw_data(request, file_id):
    raw_data_file = get_object_or_404(
        RawDataFile.objects.select_related(
            "study",
            "study__project",
            "last_transformation_schema__target_study",
        ),
        id=file_id,
    )

    if not _user_can_export_raw_data(request.user, raw_data_file):
        return HttpResponseForbidden(
            "You do not have permission to export data for this file."
        )

    schema = raw_data_file.last_transformation_schema
    harmonised_available = (
        raw_data_file.transformation_status == "completed"
        and schema is not None
        and schema.target_study_id is not None
    )

    if request.method == "POST":
        form = ExportDataForm(
            request.POST,
            harmonised_available=harmonised_available,
        )
        if form.is_valid():
            export_type = form.cleaned_data["export_type"]
            if export_type == "original":
                file_field = raw_data_file.file
                if not file_field or not file_field.name:
                    form.add_error(
                        None,
                        "The original uploaded file is no longer available on disk.",
                    )
                else:
                    try:
                        file_field.open("rb")
                    except FileNotFoundError:
                        form.add_error(
                            None,
                            "The stored file could not be located. Please re-upload before exporting.",
                        )
                    else:
                        response = FileResponse(
                            file_field,
                            as_attachment=True,
                            filename=raw_data_file.original_filename,
                        )
                        logger.info(
                            "User %s exported original raw data file %s",
                            request.user.id,
                            raw_data_file.id,
                        )
                        return response
            elif export_type == "harmonised":
                if not harmonised_available:
                    form.add_error(
                        None,
                        "Harmonised export is not available for this file yet.",
                    )
                else:
                    target_study = schema.target_study
                    target_attribute_ids = list(
                        target_study.variables.filter(source_type="target").values_list(
                            "id", flat=True
                        )
                    )
                    if not target_attribute_ids:
                        form.add_error(
                            None,
                            "No harmonised attributes were found for the target study.",
                        )
                    else:
                        observations = (
                            Observation.objects.filter(
                                attribute_id__in=target_attribute_ids
                            )
                            .select_related("patient", "attribute", "time", "location")
                            .order_by("id")
                        )

                        window_start = (
                            raw_data_file.transformation_started_at
                            or raw_data_file.transformed_at
                            or schema.created_at
                        )
                        if window_start:
                            observations = observations.filter(updated_at__gte=window_start)

                        export_timestamp = timezone.now()
                        exported_at = export_timestamp.isoformat()
                        timestamp_component = export_timestamp.strftime("%Y%m%d_%H%M%S")
                        study_slug = slugify(schema.target_study.name) or f"target-study-{schema.target_study.pk}"
                        filename = f"{study_slug}_harmonised_{timestamp_component}.csv"

                        response = StreamingHttpResponse(
                            _harmonised_csv_stream(
                                observations,
                                schema,
                                raw_data_file,
                                exported_at,
                                request.user.id,
                            ),
                            content_type="text/csv",
                        )
                        response["Content-Disposition"] = f'attachment; filename="{filename}"'
                        return response
    else:
        form = ExportDataForm(harmonised_available=harmonised_available)

    context = {
        "raw_data_file": raw_data_file,
        "form": form,
        "harmonised_available": harmonised_available,
    }
    return render(request, "health/export_data.html", context)


@login_required
def rerun_eda(request, file_id):
    """Clear and regenerate EDA caches for the specified raw data file.

    Regenerates source EDA and transformed EDA (if applicable) immediately,
    then redirects back to the detail view preserving the current view_mode.
    """
    raw_data_file = get_object_or_404(RawDataFile, id=file_id, uploaded_by=request.user)
    view_mode = request.GET.get('view_mode', 'source')

    # Clear caches first
    raw_data_file.eda_cache_source = None
    raw_data_file.eda_cache_source_generated_at = None
    raw_data_file.eda_cache_transformed = None
    raw_data_file.eda_cache_transformed_generated_at = None
    raw_data_file.save(update_fields=[
        'eda_cache_source',
        'eda_cache_source_generated_at',
        'eda_cache_transformed',
        'eda_cache_transformed_generated_at',
    ])

    # Regenerate synchronously
    try:
        from .eda_service import generate_eda_summary, generate_eda_summary_from_observations
        generate_eda_summary(raw_data_file)
        if (
            raw_data_file.transformation_status == 'completed' and
            raw_data_file.last_transformation_schema_id
        ):
            target_study = raw_data_file.last_transformation_schema.target_study
            generate_eda_summary_from_observations(raw_data_file, target_study, is_transformed=True)
        messages.success(request, 'Exploratory analysis regenerated successfully.')
    except Exception as exc:  # pragma: no cover
        logger.exception('Failed to regenerate EDA for file %s', raw_data_file.id)
        messages.error(request, f'Could not regenerate analysis: {exc}')

    redirect_url = reversed('health:raw_data_detail', kwargs={'file_id': raw_data_file.id})
    if view_mode in ['source', 'transformed', 'both']:
        redirect_url = f"{redirect_url}?view_mode={view_mode}"
    return redirect(redirect_url)


@login_required
def validate_raw_data(request, file_id):
    """
    Validate raw data file against study codebook.
    This integrates with the existing variable extraction workflow.
    """
    raw_data_file = get_object_or_404(
        RawDataFile, 
        id=file_id, 
        uploaded_by=request.user
    )
    
    # Check if study has variables extracted from codebook
    if not raw_data_file.study.variables.exists():
        messages.error(
            request, 
            "Please extract variables from the study codebook first before validating raw data.",
        )
        return redirect("health:map_codebook", study_id=raw_data_file.study.id)
    
    if request.method == "POST":
        # Validate the file
        try:
            with raw_data_file.file.open("rb") as f:
                validation_result = validate_raw_data_against_codebook(f, raw_data_file.study)
            
            if validation_result["is_valid"]:
                # Update file status
                raw_data_file.processing_status = "validated"
                raw_data_file.processing_message = validation_result["message"]
                raw_data_file.save()  # Save status immediately
                
                # Analyze and store column information
                analysis_result = analyze_raw_data_columns(raw_data_file.file.path)
                if analysis_result["success"]:
                    # Store column metadata
                    raw_data_file.rows_count = analysis_result["total_rows"]
                    raw_data_file.columns_count = analysis_result["total_columns"]
                    raw_data_file.save()  # Save additional metadata
                    
                    # Create or update RawDataColumn objects
                    for idx, col_name in enumerate(analysis_result["columns"]):
                        col_analysis = analysis_result["column_analysis"].get(col_name, {})
                        
                        RawDataColumn.objects.update_or_create(
                            raw_data_file=raw_data_file,
                            column_name=col_name,
                            defaults={
                                "column_index": idx,
                                "inferred_type": col_analysis.get("data_type", "text"),
                                "sample_values": col_analysis.get("sample_values", []),
                                "non_null_count": col_analysis.get("non_null_count", 0),
                                "unique_count": col_analysis.get("unique_count", 0),
                            },
                        )
                else:
                    # Analysis failed, but validation was successful
                    logger.warning(
                        "File validation succeeded but column analysis failed for file %s: %s",
                        raw_data_file.id, analysis_result.get("message", "Unknown error")
                    )
                
                messages.success(request, f"File validated successfully! {validation_result['message']}")
                return redirect("health:map_raw_data_columns", file_id=raw_data_file.id)

            raw_data_file.processing_status = "validation_error"
            raw_data_file.processing_message = validation_result["message"]
            raw_data_file.save()
            
            messages.error(request, f"Validation failed: {validation_result['message']}")
                
        except Exception as e:
            raw_data_file.processing_status = "validation_error"
            raw_data_file.processing_message = f"Validation error: {str(e)}"
            raw_data_file.save()
            
            messages.error(request, f"Error during validation: {str(e)}")
    
    context = {
        "raw_data_file": raw_data_file,
        "page_title": f"Validate {raw_data_file.original_filename}",
        "study_variables": raw_data_file.study.variables.all(),
    }
    
    return render(request, "health/validate_raw_data.html", context)


@login_required
def map_raw_data_columns(request, file_id):
    """
    Map raw data columns to study variables.
    This prepares data for integration with the existing harmonization workflow.
    """
    raw_data_file = get_object_or_404(
        RawDataFile, 
        id=file_id, 
        uploaded_by=request.user,
        processing_status__in=["validated", "processed", "processing", "ingestion_error", "ingested", "processed_with_errors"],
    )
    
    columns = raw_data_file.columns.all().order_by("column_index")
    study_variables = raw_data_file.study.variables.all()
    
    if request.method == "POST":
        # Process column mapping form
        mappings_created = 0
        for column in columns:
            mapped_variable_id = request.POST.get(f'column_{column.id}_mapping')
            if mapped_variable_id:
                try:
                    mapped_variable = study_variables.get(id=mapped_variable_id)
                    column.mapped_variable = mapped_variable
                    column.save()
                    mappings_created += 1
                except study_variables.model.DoesNotExist:
                    pass
        
        if mappings_created > 0:
            raw_data_file.processing_status = 'processed'
            raw_data_file.processing_message = f'Mapped {mappings_created} columns to study variables'
            raw_data_file.save()
            
            messages.success(
                request, 
                f'Successfully mapped {mappings_created} columns! '
                f'Raw data is now ready for harmonization analysis.'
            )
            
            # Check if study has harmonization mappings set up
            if raw_data_file.study.source_mappings.exists():
                messages.info(
                    request,
                    'This study already has harmonization mappings. '
                    'Raw data integration will use existing mappings.'
                )
                return redirect('health:study_harmonization_dashboard', study_id=raw_data_file.study.id)
            else:
                messages.info(
                    request,
                    'Next step: Set up harmonization mappings to a target study. '
                    'This will define how your raw data maps to standardized variables.'
                )
                return redirect('health:start_harmonisation', study_id=raw_data_file.study.id)
        else:
            messages.warning(request, 'No column mappings were created.')
    
    # Generate mapping suggestions and create pre-fill mapping
    column_names = [col.column_name for col in columns]
    variable_names = [var.variable_name for var in study_variables]
    suggestions = suggest_column_mappings(column_names, variable_names)
    
    # Create a mapping of column names to suggested variable IDs for pre-filling
    suggested_mappings = {}
    for suggestion in suggestions:
        if suggestion['confidence'] in ['high', 'medium']:  # Only pre-fill high and medium confidence
            # Find the variable ID for the suggested variable name
            for variable in study_variables:
                if variable.variable_name == suggestion['suggested_variable']:
                    suggested_mappings[suggestion['column_name']] = variable.id
                    break
    
    context = {
        'raw_data_file': raw_data_file,
        'columns': columns,
        'study_variables': study_variables,
        'suggested_mappings': suggested_mappings,
        'page_title': f'Map Columns in {raw_data_file.original_filename}',
    }
    
    return render(request, 'health/map_raw_data_columns.html', context)


@login_required
def study_variables_api(request, study_id):
    """
    API endpoint to fetch variables for a study for dynamic form population.
    Returns JSON with participant_id_variables and date_variables.
    """
    from django.http import JsonResponse
    
    try:
        study = get_object_or_404(Study, id=study_id, study_purpose='source')
        
        # Check permission
        if study.created_by != request.user:
            return JsonResponse({'error': 'Permission denied'}, status=403)
        
        # Get participant ID variables (string/categorical types)
        participant_id_variables = study.variables.filter(
            variable_type__in=['string', 'categorical']
        ).values('variable_name', 'display_name', 'variable_type')
        
        # Get date variables (datetime/string types)  
        date_variables = study.variables.filter(
            variable_type__in=['datetime', 'string']
        ).values('variable_name', 'display_name', 'variable_type')
        
        return JsonResponse({
            'participant_id_variables': list(participant_id_variables),
            'date_variables': list(date_variables),
            'total_variables': study.variables.count()
        })
        
    except Study.DoesNotExist:
        return JsonResponse({'error': 'Study not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def similarity_suggestions_api(request, schema_id):
    """
    API endpoint to get similarity-based mapping suggestions for a schema.
    Returns JSON with similarity suggestions for all source attributes.
    """
    from django.http import JsonResponse
    from core.similarity_service import similarity_service
    
    try:
        schema = get_object_or_404(MappingSchema, id=schema_id)
        
        # Check permission - user must have access to the source study
        if schema.source_study.created_by != request.user:
            return JsonResponse({'error': 'Permission denied'}, status=403)
        
        # Get similarity suggestions
        suggestions = similarity_service.get_mapping_suggestions(
            source_study_id=schema.source_study.id,
            target_study_id=schema.target_study.id,
            limit_per_source=5,  # Return top 5 suggestions per variable
        )
        
        # Format suggestions for JSON response
        formatted_suggestions = {}
        for source_attr_id, matches in suggestions.items():
            formatted_suggestions[str(source_attr_id)] = [
                {
                    'attribute_id': match['attribute_id'],
                    'variable_name': match['variable_name'],
                    'display_name': match['display_name'],
                    'description': match['description'],
                    'variable_type': match['variable_type'],
                    'unit': match['unit'],
                    'combined_similarity': match['combined_similarity'],
                    'name_similarity': match['name_similarity'],
                    'description_similarity': match['description_similarity'],
                    'confidence_grade': match['confidence_grade'],
                    'confidence_label': match['confidence_label'],
                    'confidence_color': match['confidence_color'],
                    'has_description_match': match['has_description_match'],
                }
                for match in matches
            ]
        
        return JsonResponse({
            'suggestions': formatted_suggestions,
            'schema_id': schema_id,
            'source_study_name': schema.source_study.name,
            'target_study_name': schema.target_study.name,
        })
        
    except MappingSchema.DoesNotExist:
        return JsonResponse({'error': 'Mapping schema not found'}, status=404)
    except Exception as e:
        logger.exception("Error getting similarity suggestions")
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def target_attribute_details_api(request, attribute_id):
    """
    API endpoint to get detailed information about a target attribute.
    Returns JSON with comprehensive attribute details.
    """
    from django.http import JsonResponse
    
    try:
        attribute = get_object_or_404(Attribute, id=attribute_id)
        
        # Check permission - user must have access to at least one study that uses this attribute
        user_studies = Study.objects.filter(created_by=request.user)
        accessible_studies = attribute.studies.filter(id__in=user_studies.values_list('id', flat=True))
        
        if not accessible_studies.exists():
            return JsonResponse({"error": "Permission denied"}, status=403)
        
        # Get attribute details
        attribute_data = {
            "attribute_id": attribute.id,
            "variable_name": attribute.variable_name,
            "display_name": attribute.display_name,
            "description": attribute.description,
            "variable_type": attribute.variable_type,
            "unit": attribute.unit,
            "ontology_code": attribute.ontology_code,
            "values_count": getattr(attribute, 'values_count', None),
        }
        
        # Get the first accessible study for context
        study = accessible_studies.first()
        
        return JsonResponse({
            "attribute": attribute_data,
            "study_name": study.name if study else "Unknown",
        })
        
    except Attribute.DoesNotExist:
        return JsonResponse({"error": "Attribute not found"}, status=404)
    except Exception as e:
        logger.exception("Error getting target attribute details")
        return JsonResponse({"error": str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
def transformation_suggestion_api(request):
    """
    API endpoint to generate transformation code suggestions using OpenAI.
    Expects JSON with source_attribute_id and target_attribute_id.
    Returns transformation code or empty string if no transformation needed.
    """
    try:
        import json
        from .transformation_suggestion_service import transformation_suggestion_service
        
        # Parse request JSON
        data = json.loads(request.body)
        source_attribute_id = data.get("source_attribute_id")
        target_attribute_id = data.get("target_attribute_id")
        
        if not source_attribute_id or not target_attribute_id:
            return JsonResponse({
                "error": "Both source_attribute_id and target_attribute_id are required"
            }, status=400)
        
        # Get attributes and check permissions
        try:
            source_attribute = Attribute.objects.get(id=source_attribute_id)
            target_attribute = Attribute.objects.get(id=target_attribute_id)
        except Attribute.DoesNotExist:
            return JsonResponse({"error": "One or both attributes not found"}, status=404)
        
        # Check user has access to both attributes via studies
        user_studies = Study.objects.filter(created_by=request.user)
        
        source_accessible = source_attribute.studies.filter(
            id__in=user_studies.values_list('id', flat=True)
        ).exists()
        target_accessible = target_attribute.studies.filter(
            id__in=user_studies.values_list('id', flat=True)
        ).exists()
        
        if not source_accessible or not target_accessible:
            return JsonResponse({"error": "Permission denied"}, status=403)
        
        # Generate transformation suggestion
        transformation_code = transformation_suggestion_service.suggest_transformation_code(
            source_attribute, target_attribute
        )
        
        # Handle different response cases
        if transformation_code is None:
            return JsonResponse({
                "error": "Failed to generate transformation suggestion. Please try again."
            }, status=500)
        elif transformation_code == "":
            return JsonResponse({
                "success": True,
                "transformation_needed": False,
                "transformation_code": "",
                "message": "No transformation needed - variables are already compatible"
            })
        else:
            return JsonResponse({
                "success": True,
                "transformation_needed": True,
                "transformation_code": transformation_code,
                "message": "Transformation code generated successfully"
            })
            
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON in request body"}, status=400)
    except Exception as e:
        logger.exception("Error generating transformation suggestion")
        return JsonResponse({
            "error": "An error occurred while generating the suggestion"
        }, status=500)


@login_required
@require_http_methods(["POST"])
def start_data_ingestion(request, file_id):
    """
    Start the data ingestion process for a raw data file.
    This queues the ingestion task with Celery for background processing.
    """
    raw_data_file = get_object_or_404(
        RawDataFile, 
        id=file_id, 
        uploaded_by=request.user,
        processing_status__in=["processed", "ingestion_error", "ingested", "processed_with_errors"],
    )
    
    # Check prerequisites
    if not raw_data_file.study.variables.exists():
        messages.error(
            request,
            "Study must have variables extracted from codebook before ingesting data.",
        )
        return redirect("health:raw_data_detail", file_id=file_id)
    
    # Check that columns have been mapped
    mapped_columns = raw_data_file.columns.filter(mapped_variable__isnull=False)
    if not mapped_columns.exists():
        messages.error(
            request,
            "No columns have been mapped to study variables. Please map columns first.",
        )
        return redirect("health:map_raw_data_columns", file_id=file_id)
    
    # Check if already ingesting
    if raw_data_file.processing_status == "processing":
        messages.info(request, "Data ingestion is already in progress for this file.")
        return redirect("health:raw_data_detail", file_id=file_id)
    
    # Check if already ingested
    if raw_data_file.processing_status in ["ingested", "processed_with_errors"]:
        messages.warning(
            request,
            "This file has already been ingested. "
            "Are you sure you want to re-ingest? This will create duplicate observations.",
        )
        # Could add a confirmation step here if needed
    
    try:
        # Queue the ingestion task
        task = ingest_raw_data_file.delay(raw_data_file.id)
        
        # Update file status
        raw_data_file.processing_status = "processing"
        raw_data_file.processing_message = f"Queued for ingestion. Task ID: {task.id}"
        raw_data_file.save(update_fields=["processing_status", "processing_message"])
        
        messages.success(
            request,
            f"Data ingestion started for {raw_data_file.original_filename}. "
            f"Processing will continue in the background. "
            f"You can check the progress on the file detail page.",
        )
        
        logger.info(
            f"Started data ingestion task {task.id} for file {raw_data_file.id} "
            f"({raw_data_file.original_filename}) by user {request.user.username}"
        )
        
    except Exception as exc:
        logger.error(f"Error starting data ingestion for file {file_id}: {exc}")
        messages.error(
            request,
            f"Failed to start data ingestion: {str(exc)}",
        )
    
    return redirect("health:raw_data_detail", file_id=file_id)


@login_required
def ingestion_status(request, file_id):
    """
    API endpoint to check the ingestion status of a raw data file.
    Returns JSON with current status and progress information.
    """
    try:
        raw_data_file = get_object_or_404(
            RawDataFile,
            id=file_id,
            uploaded_by=request.user,
        )
        
        # Get observation count for this file's data
        observation_count = 0
        if raw_data_file.processing_status in ["ingested", "processed_with_errors"]:
            # Count observations created from this file's mapped columns
            mapped_attributes = raw_data_file.columns.filter(
                mapped_variable__isnull=False,
            ).values_list("mapped_variable", flat=True)
            
            if mapped_attributes:
                observation_count = Observation.objects.filter(
                    attribute__in=mapped_attributes,
                ).count()
        
        response_data = {
            "file_id": raw_data_file.id,
            "filename": raw_data_file.original_filename,
            "status": raw_data_file.processing_status,
            "message": raw_data_file.processing_message or "",
            "processed_at": (
                raw_data_file.processed_at.isoformat()
                if raw_data_file.processed_at
                else None
            ),
            "transformation": {
                "status": raw_data_file.transformation_status,
                "status_display": raw_data_file.get_transformation_status_display(),
                "message": raw_data_file.transformation_message or "",
                "started_at": (
                    raw_data_file.transformation_started_at.isoformat()
                    if raw_data_file.transformation_started_at
                    else None
                ),
                "completed_at": (
                    raw_data_file.transformed_at.isoformat()
                    if raw_data_file.transformed_at
                    else None
                ),
                "schema_id": (
                    raw_data_file.last_transformation_schema_id
                    if raw_data_file.last_transformation_schema_id
                    else None
                ),
            },
            "observation_count": observation_count,
            "file_info": {
                "rows": raw_data_file.rows_count,
                "columns": raw_data_file.columns_count,
                "mapped_columns": raw_data_file.columns.filter(
                    mapped_variable__isnull=False,
                ).count(),
            },
        }
        
        return JsonResponse(response_data)
        
    except RawDataFile.DoesNotExist:
        return JsonResponse({"error": "File not found"}, status=404)
    except Exception as exc:
        logger.error(f"Error checking ingestion status for file {file_id}: {exc}")
        return JsonResponse({"error": str(exc)}, status=500)


@login_required
def delete_raw_data(request, file_id):
    """
    Delete a raw data file with comprehensive impact analysis and warnings.
    Provides detailed information about what will be deleted and what will remain.
    """
    raw_data_file = get_object_or_404(
        RawDataFile, 
        id=file_id, 
        uploaded_by=request.user,
    )
    
    if request.method == "GET":
        # Calculate deletion impact
        deletion_impact = _calculate_deletion_impact(raw_data_file)
        
        context = {
            "raw_data_file": raw_data_file,
            "deletion_impact": deletion_impact,
            "page_title": f"Delete {raw_data_file.original_filename}",
        }
        
        return render(request, "health/delete_raw_data.html", context)
    
    # POST request - process deletion
    # Validate confirmation checkboxes
    required_confirmations = ["confirm_understand", "confirm_no_undo"]
    
    # Add observation confirmation if there are observations
    deletion_impact = _calculate_deletion_impact(raw_data_file)
    if deletion_impact["observations_count"] > 0:
        required_confirmations.append("confirm_observations")
    
    # Check all required confirmations
    missing_confirmations = [
        confirmation for confirmation in required_confirmations
        if not request.POST.get(confirmation)
    ]
    
    # Validate study name confirmation
    study_name_confirmation = request.POST.get("study_name_confirmation", "").strip()
    study_name_valid = study_name_confirmation == raw_data_file.study.name
    
    if missing_confirmations:
        messages.error(
            request,
            "Please confirm all checkboxes before proceeding with deletion.",
        )
        return redirect("health:delete_raw_data", file_id=file_id)
    
    if not study_name_valid:
        messages.error(
            request,
            f"Study name confirmation failed. Please type '{raw_data_file.study.name}' exactly as shown.",
        )
        return redirect("health:delete_raw_data", file_id=file_id)
    
    # Perform the deletion
    try:
        deletion_result = _perform_raw_data_deletion(raw_data_file)
        
        messages.success(
            request,
            f"Raw data file '{raw_data_file.original_filename}' has been "
            f"permanently deleted. {deletion_result['summary']}",
        )
        
        # Log the deletion for audit purposes
        logger.warning(
            "Raw data file deleted: %s (ID: %s) by user %s. Impact: %s",
            raw_data_file.original_filename,
            raw_data_file.id,
            request.user.username,
            deletion_result["details"],
        )
        
        return redirect("health:raw_data_list")
        
    except (OSError, IOError, ValueError) as e:
        logger.exception("Error deleting raw data file %s", file_id)
        messages.error(
            request,
            f"An error occurred while deleting the file: {e!s}",
        )
        return redirect("health:delete_raw_data", file_id=file_id)


def _calculate_deletion_impact(raw_data_file):
    """
    Calculate the impact of deleting a raw data file.
    Returns information about what will be deleted and what will remain.
    """
    from core.models import Observation, Patient
    
    # Direct deletions (CASCADE relationships)
    columns_count = raw_data_file.columns.count()
    
    # Indirect impact analysis
    study = raw_data_file.study
    
    # Observations are not directly linked to raw data files, but we can estimate
    # by looking at observations created around the ingestion time
    observations_count = 0
    patients_count = 0
    
    if raw_data_file.processing_status in ['ingested', 'processed_with_errors']:
        # Estimate observations created from this file
        # This is an approximation based on study observations created after file upload
        study_observations = Observation.objects.filter(
            attribute__study=study,
            created_at__gte=raw_data_file.uploaded_at
        )
        observations_count = study_observations.count()
        
        # Count patients in the study
        study_patients = Patient.objects.filter(study=study)
        patients_count = study_patients.count()
    
    return {
        'columns_count': columns_count,
        'observations_count': observations_count,
        'patients_count': patients_count,
        'file_size': raw_data_file.file_size,
        'has_physical_file': bool(raw_data_file.file and raw_data_file.file.name),
    }


def _perform_raw_data_deletion(raw_data_file):
    """
    Perform the actual deletion of a raw data file and related data.
    Returns a summary of what was deleted.
    """
    import os
    from django.conf import settings
    
    filename = raw_data_file.original_filename
    file_id = raw_data_file.id
    columns_count = raw_data_file.columns.count()
    
    # Track physical file deletion
    physical_file_deleted = False
    physical_file_path = None
    
    # Delete physical file if it exists
    if raw_data_file.file and raw_data_file.file.name:
        try:
            physical_file_path = raw_data_file.file.path
            if os.path.exists(physical_file_path):
                os.remove(physical_file_path)
                physical_file_deleted = True
        except Exception as e:
            logger.warning(f"Could not delete physical file {physical_file_path}: {e}")
    
    # Delete the database record (this will CASCADE to RawDataColumn)
    raw_data_file.delete()
    
    # Prepare summary
    summary_parts = []
    if physical_file_deleted:
        summary_parts.append("Physical file removed from storage")
    if columns_count > 0:
        summary_parts.append(f"{columns_count} column mappings deleted")
    
    summary = ". ".join(summary_parts) + "." if summary_parts else "File record removed."
    
    details = {
        'file_id': file_id,
        'filename': filename,
        'columns_deleted': columns_count,
        'physical_file_deleted': physical_file_deleted,
        'physical_file_path': physical_file_path,
    }
    
    return {
        'summary': summary,
        'details': details,
    }


@login_required
@require_POST
def delete_duplicates(request, file_id):
    """
    Start background task to delete ALL duplicate observations.
    Keeps only the first occurrence in each group.
    """
    raw_data_file = get_object_or_404(RawDataFile, id=file_id)
    
    # Check permissions
    if raw_data_file.study.created_by != request.user:
        messages.error(request, "You don't have permission to modify this data.")
        return redirect('health:raw_data_list')
    
    # Start the background deletion task
    from .tasks import delete_duplicates_task
    task = delete_duplicates_task.delay(file_id)
    
    # Store task ID in session for status checking
    request.session[f'delete_duplicates_task_{file_id}'] = task.id
    
    # Inform user
    data_type = 'source' if raw_data_file.study.study_purpose == 'source' else 'transformed'
    messages.info(
        request,
        f"Duplicate deletion started in the background for {data_type} data. "
        f"This may take a few moments. The page will update when complete."
    )
    
    return redirect('health:raw_data_detail', file_id=file_id)


@login_required
def check_delete_duplicates_status(request, file_id):
    """Check status of duplicate deletion background task."""
    from celery.result import AsyncResult
    
    # Get task ID from session
    task_id = request.session.get(f'delete_duplicates_task_{file_id}')
    
    if not task_id:
        return JsonResponse({
            'status': 'not_started',
            'message': 'No deletion task found',
        })
    
    # Check task status
    task = AsyncResult(task_id)
    
    if task.ready():
        # Task completed
        result = task.get()
        
        # Clear task ID from session
        if f'delete_duplicates_task_{file_id}' in request.session:
            del request.session[f'delete_duplicates_task_{file_id}']
        
        return JsonResponse({
            'status': 'completed',
            'success': result.get('success', False),
            'message': result.get('message', ''),
            'deleted_count': result.get('deleted_count', 0),
            'duplicate_groups': result.get('duplicate_groups', 0),
        })
    elif task.failed():
        return JsonResponse({
            'status': 'failed',
            'message': str(task.info),
        })
    else:
        return JsonResponse({'status': 'running'})


@login_required
@require_POST
def start_duplicate_detection(request, file_id):
    """Start background task to detect duplicates in raw data file."""
    raw_data_file = get_object_or_404(
        RawDataFile.objects.select_related('study'),
        id=file_id,
        study__project__created_by=request.user,
    )
    
    # Import the task
    from .tasks import detect_duplicates_task
    
    # Start the background task
    task = detect_duplicates_task.delay(file_id)
    
    messages.success(
        request,
        "Duplicate detection started in the background. This may take a few moments. "
        "The page will refresh automatically when complete.",
    )
    
    # Store task ID in session for status checking
    request.session[f'duplicate_detection_task_{file_id}'] = task.id
    
    return redirect('health:raw_data_detail', file_id=file_id)


@login_required
def check_duplicate_detection_status(request, file_id):
    """Check status of duplicate detection background task."""
    from celery.result import AsyncResult
    
    # Get task ID from session
    task_id = request.session.get(f'duplicate_detection_task_{file_id}')
    
    if not task_id:
        return JsonResponse({
            'status': 'not_started',
            'message': 'No duplicate detection task found',
        })
    
    # Check task status
    task = AsyncResult(task_id)
    
    if task.ready():
        # Task completed
        result = task.get()
        
        # Clear task ID from session
        if f'duplicate_detection_task_{file_id}' in request.session:
            del request.session[f'duplicate_detection_task_{file_id}']
        
        return JsonResponse({
            'status': 'completed',
            'success': result.get('success', False),
            'message': result.get('message', ''),
            'duplicates': result.get('duplicates', {}),
            'conflicts': result.get('conflicts', {}),
        })
    elif task.failed():
        # Task failed
        return JsonResponse({
            'status': 'failed',
            'message': 'Duplicate detection failed. Please try again.',
        })
    else:
        # Still running
        return JsonResponse({
            'status': 'running',
            'message': 'Duplicate detection in progress...',
        })
