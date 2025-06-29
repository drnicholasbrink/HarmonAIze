from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from core.models import Study, Attribute


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
def select_variables(request, study_id):
    """
    Let user select which extracted variables to include in the study.
    """
    study = get_object_or_404(Study, id=study_id, created_by=request.user, study_purpose='source')
    
    # Get variables data from session
    variables_data = request.session.get(f'variables_data_{study.id}')
    if not variables_data:
        messages.error(request, 'No variable data found. Please extract variables first.')
        return redirect('health:extract_variables', study_id=study.id)
    
    # Handle form submission (user selecting variables)
    if request.method == 'POST':
        from core.forms import VariableConfirmationFormSetFactory
        
        try:
            formset = VariableConfirmationFormSetFactory(
                data=request.POST,
                variables_data=variables_data
            )
        except Exception as e:
            messages.error(request, f'Form processing error: {str(e)}. Please try again.')
            return redirect('health:select_variables', study_id=study.id)
        
        if formset.is_valid():
            included_variables = formset.get_included_variables()
            
            if not included_variables:
                messages.error(request, 'You must include at least one variable in your study.')
            else:
                # Create Attribute objects for included variables
                created_attributes = []
                
                for var_data in included_variables:
                    try:
                        attribute, created = Attribute.objects.get_or_create(
                            variable_name=var_data['variable_name'],
                            source_type='source',  # Explicitly set source_type for health variables
                            defaults={
                                'display_name': var_data.get('display_name', var_data['variable_name']),
                                'description': var_data.get('description', ''),
                                'variable_type': var_data.get('variable_type', 'string'),
                                'unit': var_data.get('unit', ''),
                                'ontology_code': var_data.get('ontology_code', ''),
                                'category': 'health',  # Set category for health app
                            }
                        )
                        created_attributes.append(attribute)
                    except Exception as e:
                        messages.error(request, f'Error creating variable {var_data["variable_name"]}: {str(e)}')
                        context = {
                            'study': study,
                            'formset': formset,
                            'variables_count': len(variables_data),
                            'page_title': f'Select Health Variables - {study.name}'
                        }
                        return render(request, 'health/select_variables.html', context)
                
                # Associate variables with the study
                study.variables.set(created_attributes)
                study.status = 'processing'
                study.save()
                
                # Clear session data
                request.session.pop(f'variables_data_{study.id}', None)
                request.session.pop(f'column_mapping_{study.id}', None)
                
                messages.success(
                    request,
                    f'Successfully added {len(included_variables)} variables to your study! '
                    f'You can now proceed with health data harmonisation.'
                )
                
                return redirect('core:study_detail', pk=study.pk)
        else:
            # Collect and display specific formset errors
            error_messages = []
            
            # Check for non-field errors
            if formset.non_form_errors():
                for error in formset.non_form_errors():
                    error_messages.append(f"Form error: {error}")
            
            # Check for individual form errors (only for selected variables)
            for i, form in enumerate(formset):
                # Only report errors for forms that are marked for inclusion
                is_included = form.cleaned_data.get('include', False) if form.cleaned_data else False
                if form.errors and is_included:
                    variable_name = form.cleaned_data.get('variable_name', f'Variable {i+1}')
                    for field, errors in form.errors.items():
                        for error in errors:
                            error_messages.append(f"{variable_name} - {field}: {error}")
            
            if error_messages:
                for error_msg in error_messages:
                    messages.error(request, error_msg)
            else:
                messages.error(request, 'Please correct the form errors and try again.')
    else:
        # Create the formset with initial data
        from core.forms import VariableConfirmationFormSetFactory
        
        try:
            # Create formset with proper initialization
            formset = VariableConfirmationFormSetFactory(
                variables_data=variables_data
            )
        except Exception as e:
            messages.error(request, f'Error creating form: {str(e)}. Please check your variable data.')
            return redirect('health:extract_variables', study_id=study.id)
    
    context = {
        'study': study,
        'formset': formset,
        'variables_count': len(variables_data),
        'page_title': f'Select Health Variables - {study.name}'
    }
    
    return render(request, 'health/select_variables.html', context)


@login_required
def reset_variables(request, study_id):
    """
    Reset all variables for a study and clear related session data.
    """
    study = get_object_or_404(Study, id=study_id, created_by=request.user, study_purpose='source')
    
    if request.method == 'POST':
        # Clear all variables associated with the study
        variables_count = study.variables.count()
        study.variables.clear()
        
        # Clear session data related to this study
        request.session.pop(f'variables_data_{study.id}', None)
        request.session.pop(f'column_mapping_{study.id}', None)
        
        # Reset study status
        study.status = 'draft'
        study.save()
        
        messages.success(
            request,
            f'Successfully reset {variables_count} variables. '
            f'You can now restart the harmonisation workflow.'
        )
        
        return redirect('core:study_detail', pk=study.pk)
    
    # If GET request, just redirect back
    return redirect('core:study_detail', pk=study.pk)
