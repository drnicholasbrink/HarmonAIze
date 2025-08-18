from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, Http404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.forms import formset_factory
from django.views.decorators.http import require_http_methods
import logging

from core.models import Study, Attribute, Observation
from .models import MappingSchema, MappingRule, RawDataFile, RawDataColumn
from core.forms import VariableConfirmationFormSetFactory
from .forms import MappingSchemaForm, MappingRuleForm, RawDataUploadForm
from .utils import (
    validate_raw_data_against_codebook,
    analyze_raw_data_columns,
    suggest_column_mappings,
)
from .tasks import ingest_raw_data_file

logger = logging.getLogger(__name__)


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
    source_study = get_object_or_404(
        Study, id=study_id, created_by=request.user, study_purpose="source",
    )
    
    # Check if a mapping schema already exists
    existing_schema = MappingSchema.objects.filter(source_study=source_study).first()
    if existing_schema:
        messages.info(request, "Using existing harmonization schema.")
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
            messages.success(
                request,
                "Harmonization schema created successfully!",
            )
            return redirect("health:harmonization_dashboard", schema_id=schema.id)
        messages.error(request, "Please correct the errors below.")
    else:
        form = MappingSchemaForm(source_study=source_study, user=request.user)
    
    return render(
        request,
        "health/harmonization_dashboard.html",
        {
            "study": source_study,
            "form": form,
            "universal_form": None,  # Placeholder to prevent template errors
            "page_title": f"Start Harmonisation - {source_study.name}",
            "creating_new": True,
        },
    )


@login_required
def approve_mapping(request, schema_id):
    schema = get_object_or_404(MappingSchema, id=schema_id, created_by=request.user)
    
    # Validation for approval
    complete_rules = schema.rules.filter(target_attribute__isnull=False)
    incomplete_rules = schema.rules.filter(target_attribute__isnull=True)
    
    if not complete_rules.exists():
        messages.error(
            request, 
            "Add at least one complete mapping rule before approving. "
            "Complete rules must have both source and target attributes.",
        )
        return redirect("health:harmonization_dashboard", schema_id=schema.id)
    
    # Check for required patient ID mapping
    if not complete_rules.filter(role="patient_id").exists():
        messages.error(
            request,
            "You must map at least one patient ID field before approval.",
        )
        return redirect("health:harmonization_dashboard", schema_id=schema.id)
    
    # Clean up any incomplete provisional rules before approval
    if incomplete_rules.exists():
        incomplete_count = incomplete_rules.count()
        incomplete_rules.delete()
        messages.info(
            request,
            f"Removed {incomplete_count} incomplete provisional mappings "
            f"during approval.",
        )

    schema.status = "approved"
    schema.approved_by = request.user
    schema.approved_at = timezone.now()
    schema.save(update_fields=["status", "approved_by", "approved_at"])
    messages.success(
        request, 
        f"Mapping approved with {complete_rules.count()} complete rules.",
    )
    return redirect("core:study_detail", pk=schema.source_study_id)


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
                                "category": "health",
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
                
                # Associate variables with the study
                study.variables.set(created_attributes)
                study.status = "processing"
                study.save()
                
                # Clear session data
                request.session.pop(f"variables_data_{study.id}", None)
                request.session.pop(f"column_mapping_{study.id}", None)
                
                messages.success(
                    request,
                    f"Successfully added {len(included_variables)} variables to your study! "
                    f"You can now proceed with health data harmonisation.",
                )
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
        study.status = "draft"
        study.save()

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
    
    context = {
        "schema": schema,
        "universal_form": universal_form,
        "variable_forms": variable_forms,
        "total_variables": len(source_attrs),
        "completed_rules": completed_rules,
        "not_mappable_count": not_mappable_count,
        "progress_percent": progress_percent,
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
    
    if request.method == 'POST':
        form = RawDataUploadForm(request.POST, request.FILES, user=request.user)
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
                    'page_title': f"Upload Raw Data{' for ' + raw_data_file.study.name if raw_data_file.study else ''}",
                    'duplicate_found': True,
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
        'page_title': f'Upload Raw Data{" for " + study.name if study else ""}',
    }
    
    return render(request, 'health/upload_raw_data.html', context)


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
        'validated': raw_data_files.filter(processing_status='validated').count(),
        'processed': raw_data_files.filter(processing_status='processed').count(),
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
    """
    raw_data_file = get_object_or_404(
        RawDataFile, 
        id=file_id, 
        uploaded_by=request.user
    )
    
    # Get column information if available
    columns = raw_data_file.columns.all().order_by('column_index')
    
    # Status flags for cleaner template logic
    status = raw_data_file.processing_status
    validation_completed = status in ['validated', 'processed', 'processing', 'ingestion_error', 'ingested', 'processed_with_errors']
    mapping_completed = status in ['processed', 'processing', 'ingestion_error', 'ingested', 'processed_with_errors']
    
    context = {
        'raw_data_file': raw_data_file,
        'columns': columns,
        'page_title': f'Raw Data: {raw_data_file.original_filename}',
        'validation_completed': validation_completed,
        'mapping_completed': mapping_completed,
    }
    
    return render(request, 'health/raw_data_detail.html', context)


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
