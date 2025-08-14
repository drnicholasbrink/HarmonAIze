from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.forms import formset_factory
from django.views.decorators.http import require_http_methods
from core.models import Study, Attribute
from .models import MappingSchema, MappingRule
from core.forms import VariableConfirmationFormSetFactory
from .forms import MappingSchemaForm, MappingRuleForm


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
            "page_title": f"Start Harmonisation - {source_study.name}",
            "creating_new": True,
        },
    )
    schema = get_object_or_404(MappingSchema, id=schema_id, created_by=request.user)
    source_attrs = list(
        schema.source_study.variables.order_by("variable_name"),
    )

    # Existing rules mapped by source attribute id
    existing = {
        r.source_attribute_id: r for r in MappingRule.objects.filter(schema=schema)
    }

    rule_formset_factory = formset_factory(MappingRuleForm, extra=0)

    initial = []
    for attr in source_attrs:
        rule = existing.get(attr.id)
        initial.append({
            "source_attribute": attr.id,
            "target_attribute": getattr(rule, "target_attribute_id", None),
            "transform_code": getattr(rule, "transform_code", ""),
            "comments": getattr(rule, "comments", ""),
            "role": getattr(rule, "role", "value"),
            "related_relation_type": getattr(rule, "related_relation_type", ""),
        })

    if request.method == "POST":
        formset = rule_formset_factory(
            request.POST, form_kwargs={"schema": schema},
        )
        if formset.is_valid():
            # Persist each rule (including provisional/incomplete ones)
            for form, attr in zip(formset.forms, source_attrs, strict=True):
                target_attr = form.cleaned_data.get("target_attribute")
                transform_code = form.cleaned_data.get("transform_code", "")
                comments = form.cleaned_data.get("comments", "")
                role = form.cleaned_data.get("role", "value")
                related_relation_type = form.cleaned_data.get(
                    "related_relation_type", "",
                )

                if target_attr:
                    # Save complete mapping rule
                    rule, _created = MappingRule.objects.update_or_create(
                        schema=schema, source_attribute=attr,
                        defaults={
                            "target_attribute": target_attr,
                            "transform_code": transform_code,
                            "comments": comments,
                            "role": role,
                            "related_relation_type": (
                                related_relation_type
                                if role == "related_patient_id"
                                else ""
                            ),
                        },
                    )
                elif (transform_code or comments or role != "value"
                      or related_relation_type):
                    # Save provisional rule without target_attribute
                    rule, _created = MappingRule.objects.update_or_create(
                        schema=schema, source_attribute=attr,
                        defaults={
                            "target_attribute": None,
                            "transform_code": transform_code,
                            "comments": comments,
                            "role": role,
                            "related_relation_type": (
                                related_relation_type
                                if role == "related_patient_id"
                                else ""
                            ),
                        },
                    )
                else:
                    # Delete empty provisional rule if it exists
                    MappingRule.objects.filter(
                        schema=schema, source_attribute=attr,
                    ).delete()
            messages.success(request, "Mapping rules saved.")
            return redirect("core:study_detail", pk=schema.source_study_id)
        messages.error(request, "Please correct errors in the mapping rules.")
    else:
        formset = rule_formset_factory(
            initial=initial, form_kwargs={"schema": schema},
        )

    context = {
        "schema": schema,
        "source_study": schema.source_study,
        "target_study": schema.target_study,
        "formset": formset,
        "source_attrs": source_attrs,  # Add source attributes for template
        "page_title": "Edit Mapping",
        **_progress_flags(schema),
    }
    return render(request, "health/mapping_rules_form.html", context)


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
                    study=schema.target_study,
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
                    study=schema.target_study,
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
        messages.error(request, "No harmonization mappings found for this study.")
        return redirect("core:study_detail", study_id=study_id)

    return redirect("health:harmonization_dashboard", schema_id=mapping_schema.id)
