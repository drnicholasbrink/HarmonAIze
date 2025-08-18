from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.urls import reverse
from django.views.generic import ListView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
import pandas as pd
from .models import Study, Project
from .forms import StudyCreationForm, ProjectCreationForm
from health.models import RawDataFile


@login_required
def upload_study(request):
    """
    Upload study page - entry point for creating new studies.
    Also handles codebook reupload for existing studies.
    """
    study_id = request.POST.get('study_id')  # For reupload functionality
    existing_study = None
    
    if study_id:
        existing_study = get_object_or_404(Study, id=study_id, created_by=request.user)
    
    if request.method == 'POST':
        if existing_study:
            # Handle codebook reupload for existing study
            form = StudyCreationForm(request.POST, request.FILES, user=request.user, instance=existing_study)
            if form.is_valid():
                # Clear existing variables and session data
                variables_count = existing_study.variables.count()
                existing_study.variables.clear()
                
                # Clear session data
                request.session.pop(f'variables_data_{existing_study.id}', None)
                request.session.pop(f'column_mapping_{existing_study.id}', None)
                
                # Save the study with new codebook
                study = form.save()
                study.status = 'draft'  # Reset status
                study.save()
                
                messages.success(
                    request,
                    f'Codebook updated successfully! Previous {variables_count} variables were reset. '
                    f'You can now restart the harmonisation workflow.'
                )
                
                return redirect('core:study_detail', pk=study.pk)
            else:
                messages.error(request, 'Please correct the errors below.')
        else:
            # Handle new study creation
            form = StudyCreationForm(request.POST, request.FILES, user=request.user)
            if form.is_valid():
                study = form.save()
                
                if study.codebook:
                    messages.success(
                        request, 
                        f'Study "{study.name}" created successfully with codebook file! '
                        f'Next, you can process the codebook to extract variables.'
                    )
                else:
                    messages.success(
                        request, 
                        f'Study "{study.name}" created successfully! '
                        f'You can upload a codebook file later from the study page.'
                    )
                
                return redirect('core:study_detail', pk=study.pk)
            else:
                messages.error(request, 'Please correct the errors below.')
    else:
        if existing_study:
            form = StudyCreationForm(user=request.user, instance=existing_study)
        else:
            form = StudyCreationForm(user=request.user)
    
    context = {
        'form': form,
        'existing_study': existing_study,
        'page_title': 'Upload New Codebook' if existing_study else 'Upload New Study'
    }
    
    return render(request, 'core/upload_study.html', context)


class StudyListView(LoginRequiredMixin, ListView):
    """
    List view for user's studies, filtered by study purpose.
    """
    model = Study
    template_name = 'core/study_list.html'
    context_object_name = 'studies'
    paginate_by = 10
    
    def get_queryset(self):
        queryset = Study.objects.filter(created_by=self.request.user)
        
        # Filter by study purpose if specified in query params
        study_purpose = self.request.GET.get('purpose')
        if study_purpose in ['source', 'target']:
            queryset = queryset.filter(study_purpose=study_purpose)
        
        return queryset.order_by('-created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get the current filter
        current_purpose = self.request.GET.get('purpose')
        
        # Separate source and target studies for counts and navigation
        all_studies = Study.objects.filter(created_by=self.request.user)
        source_studies = all_studies.filter(study_purpose='source')
        target_studies = all_studies.filter(study_purpose='target')
        
        context.update({
            'current_purpose': current_purpose,
            'source_studies': source_studies,
            'target_studies': target_studies,
            'source_count': source_studies.count(),
            'target_count': target_studies.count(),
            'has_target_study': target_studies.exists(),
            'showing_all': current_purpose is None,
            'showing_source': current_purpose == 'source',
            'showing_target': current_purpose == 'target',
        })
        
        return context


class StudyDetailView(LoginRequiredMixin, DetailView):
    """
    Detail view for a specific study with enhanced variable statistics.
    """
    model = Study
    template_name = 'core/study_detail.html'
    context_object_name = 'study'
    
    def get_queryset(self):
        return Study.objects.filter(created_by=self.request.user)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        study = self.object
        
        # Calculate variable statistics
        variables = study.variables.all()
        
        # Variable type distribution
        variable_types = {}
        for variable in variables:
            var_type = variable.variable_type
            variable_types[var_type] = variable_types.get(var_type, 0) + 1
        
        # Additional statistics
        variables_with_units = variables.exclude(unit='').count()
        variables_with_codes = variables.exclude(ontology_code='').count()
        
        # Calculate percentages for type distribution bars
        total_vars = variables.count()
        if total_vars > 0:
            for var_type in variable_types:
                variable_types[var_type] = {
                    'count': variable_types[var_type],
                    'percentage': (variable_types[var_type] / total_vars) * 100
                }
        
        context.update({
            'variable_types': variable_types,
            'variables_with_units': variables_with_units,
            'variables_with_codes': variables_with_codes,
            'total_variables': total_vars,
        })
        
        return context


@login_required
def study_dashboard(request):
    """
    Main dashboard showing user's studies and quick actions.
    """
    all_studies = Study.objects.filter(created_by=request.user)
    source_studies = all_studies.filter(study_purpose='source')
    target_studies = all_studies.filter(study_purpose='target')
    
    recent_source_studies = source_studies.order_by('-created_at')[:5]
    recent_target_studies = target_studies.order_by('-created_at')[:3]  # Show multiple target databases
    
    # Get project information
    all_projects = Project.objects.filter(created_by=request.user)
    recent_projects = all_projects.order_by('-created_at')[:3]
    
    # Calculate total variables across all studies
    total_variables = 0
    for study in all_studies:
        total_variables += study.variables.count()
    
    # Raw data file stats
    user_files = RawDataFile.objects.filter(uploaded_by=request.user)
    raw_data_status_counts = {
        'uploaded': user_files.filter(processing_status='uploaded').count(),
        'validation_error': user_files.filter(processing_status='validation_error').count(),
        'validated': user_files.filter(processing_status='validated').count(),
        'processed': user_files.filter(processing_status='processed').count(),
        'processing': user_files.filter(processing_status='processing').count(),
        'ingestion_error': user_files.filter(processing_status='ingestion_error').count(),
        'ingested': user_files.filter(processing_status='ingested').count(),
        'processed_with_errors': user_files.filter(processing_status='processed_with_errors').count(),
        'error': user_files.filter(processing_status='error').count(),
    }

    context = {
        'source_studies': recent_source_studies,
        'source_studies_count': source_studies.count(),
        'target_studies_count': target_studies.count(),
        'target_studies': recent_target_studies,
        'has_target_study': target_studies.exists(),
        'total_studies': all_studies.count(),
        'total_variables': total_variables,
        'total_projects': all_projects.count(),
        'recent_projects': recent_projects,
        'raw_data_status_counts': raw_data_status_counts,
        'raw_data_total_files': user_files.count(),
    }
    
    return render(request, 'core/dashboard.html', context)

# Project Views
@login_required
def create_project(request):
    """
    Create a new project to organise studies.
    """
    if request.method == 'POST':
        form = ProjectCreationForm(request.POST, user=request.user)
        if form.is_valid():
            project = form.save()
            
            messages.success(
                request,
                f'Project "{project.name}" created successfully! '
                f'You can now add studies to this project.'
            )
            
            return redirect('core:project_detail', pk=project.pk)
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = ProjectCreationForm(user=request.user)
    
    context = {
        'form': form,
        'page_title': 'Create New Project',
    }
    
    return render(request, 'core/create_project.html', context)


class ProjectDetailView(LoginRequiredMixin, DetailView):
    """
    Detail view for a specific project showing its studies and progress.
    """
    model = Project
    template_name = 'core/project_detail.html'
    context_object_name = 'project'
    
    def get_queryset(self):
        return Project.objects.filter(created_by=self.request.user)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.object
        
        # Get project studies using the correct relationship
        source_studies = project.studies.filter(study_purpose='source').order_by('-created_at')
        target_studies = project.studies.filter(study_purpose='target').order_by('-created_at')
        
        # Calculate project statistics
        total_variables = 0
        for study in source_studies:
            total_variables += study.variables.count()
        for study in target_studies:
            total_variables += study.variables.count()
        
        context.update({
            'source_studies': source_studies,
            'target_studies': target_studies,
            'source_studies_count': source_studies.count(),
            'target_studies_count': target_studies.count(),
            'total_variables': total_variables,
            'has_target_study': target_studies.exists(),
        })
        
        return context


class ProjectListView(LoginRequiredMixin, ListView):
    """
    List view for user's projects.
    """
    model = Project
    template_name = 'core/project_list.html'
    context_object_name = 'projects'
    paginate_by = 10
    
    def get_queryset(self):
        return Project.objects.filter(created_by=self.request.user).order_by('-created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Calculate aggregate statistics
        total_projects = self.get_queryset().count()
        total_studies = Study.objects.filter(created_by=self.request.user).count()
        
        context.update({
            'total_projects': total_projects,
            'total_studies': total_studies,
        })
        
        return context

# Target Codebook Views - Following the same pattern as health views
@login_required
def create_target_study(request):
    """
    Create a new target database for defining harmonisation targets.
    Multiple target databases are allowed per user.
    """
    if request.method == 'POST':
        form = StudyCreationForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            study = form.save(commit=False)
            study.study_purpose = 'target'  # Set as target study
            study.save()
            
            # Handle many-to-many field for data use permissions
            if 'data_use_permissions' in form.cleaned_data:
                study.data_use_permissions = form.cleaned_data['data_use_permissions']
                study.save()
            
            messages.success(
                request,
                f'Target database "{study.name}" created successfully! '
                f'This will define your harmonisation standards. You can now proceed to define your target variables.'
            )
            
            # If codebook was uploaded, proceed to mapping
            if study.codebook:
                return redirect('core:target_map_codebook', study_id=study.id)
            else:
                return redirect('core:study_detail', pk=study.pk)
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = StudyCreationForm(user=request.user)
    
    context = {
        'form': form,
        'page_title': 'Create Target Database',
        'study_type': 'target',
    }
    
    return render(request, 'core/create_target_study.html', context)


@login_required
def target_map_codebook(request, study_id):
    """
    Map target database codebook columns to attribute schema.
    Uses unified codebook processing utility.
    """
    study = get_object_or_404(Study, id=study_id, created_by=request.user, study_purpose='target')
    
    from core.utils import process_codebook_mapping
    result = process_codebook_mapping(request, study, codebook_type='target')
    
    # If result is a redirect, return it
    if hasattr(result, 'status_code'):
        return result
    
    # If result is a context dictionary, render the template
    if isinstance(result, dict):
        return render(request, 'core/target_map_codebook.html', result)
    
    # This shouldn't happen, but provide a fallback
    messages.error(request, 'Unexpected error processing target database codebook mapping.')
    return redirect('core:study_detail', pk=study.pk)


@login_required 
def target_extract_variables(request, study_id):
    """
    Extract target database variables from codebook using the column mapping.
    Uses unified codebook processing utility.
    """
    study = get_object_or_404(Study, id=study_id, created_by=request.user, study_purpose='target')
    
    from core.utils import process_codebook_extraction
    return process_codebook_extraction(request, study, codebook_type='target')


@login_required
def target_select_variables(request, study_id):
    """
    Let user select which extracted target database variables to include in the study.
    """
    study = get_object_or_404(Study, id=study_id, created_by=request.user, study_purpose='target')
    
    # Get variables data from session
    variables_data = request.session.get(f'target_variables_data_{study.id}')
    if not variables_data:
        messages.error(request, 'No target database variable data found. Please extract variables first.')
        return redirect('core:target_extract_variables', study_id=study.id)
    
    # Handle form submission (user selecting variables)
    if request.method == 'POST':
        from core.forms import VariableConfirmationFormSetFactory
        from core.models import Attribute
        
        try:
            formset = VariableConfirmationFormSetFactory(
                data=request.POST,
                variables_data=variables_data
            )
        except Exception as e:
            messages.error(request, f'Form processing error: {str(e)}. Please try again.')
            return redirect('core:target_select_variables', study_id=study.id)
        
        if formset.is_valid():
            included_variables = formset.get_included_variables()
            
            if not included_variables:
                messages.error(request, 'You must include at least one target database variable in your database.')
            else:
                # Create Attribute objects for included target database variables
                created_attributes = []
                
                for var_data in included_variables:
                    try:
                        attribute, created = Attribute.objects.get_or_create(
                            variable_name=var_data['variable_name'],
                            source_type='target',  # Explicitly set source_type for target variables
                            defaults={
                                'display_name': var_data.get('display_name', var_data['variable_name']),
                                'description': var_data.get('description', ''),
                                'variable_type': var_data.get('variable_type', 'string'),
                                'unit': var_data.get('unit', ''),
                                'ontology_code': var_data.get('ontology_code', ''),
                                'category': 'health',  # Default category for target variables
                            }
                        )
                        created_attributes.append(attribute)
                    except Exception as e:
                        messages.error(request, f'Error creating target database variable {var_data["variable_name"]}: {str(e)}')
                        context = {
                            'study': study,
                            'formset': formset,
                            'variables_count': len(variables_data),
                            'page_title': f'Select Target Database Variables - {study.name}',
                            'study_type': 'target',
                        }
                        return render(request, 'core/target_select_variables.html', context)
                
                # Associate variables with the study
                study.variables.set(created_attributes)
                study.status = 'variables_extracted'
                study.save()
                
                # Clear session data
                request.session.pop(f'target_variables_data_{study.id}', None)
                request.session.pop(f'target_column_mapping_{study.id}', None)
                
                messages.success(
                    request,
                    f'Successfully added {len(included_variables)} target database variables to your database! '
                    f'Your harmonisation targets are now defined.'
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
            return redirect('core:target_extract_variables', study_id=study.id)
    
    context = {
        'study': study,
        'formset': formset,
        'variables_count': len(variables_data),
        'page_title': f'Select Target Database Variables - {study.name}',
        'study_type': 'target',
    }
    
    return render(request, 'core/target_select_variables.html', context)


@login_required
def target_reset_variables(request, study_id):
    """
    Reset all target database variables for a study and clear related session data.
    """
    study = get_object_or_404(Study, id=study_id, created_by=request.user, study_purpose='target')
    
    if request.method == 'POST':
        # Clear all target variables associated with the study
        variables_count = study.variables.filter(source_type='target').count()
        study.variables.filter(source_type='target').delete()
        
        # Clear session data related to this study
        request.session.pop(f'target_variables_data_{study.id}', None)
        request.session.pop(f'target_column_mapping_{study.id}', None)
        
        # Reset study status
        study.status = 'created'
        study.save()
        
        messages.success(
            request,
            f'Successfully reset {variables_count} target database variables. '
            f'You can now restart the target database definition workflow.'
        )
        
        return redirect('core:study_detail', pk=study.pk)
    
    # If GET request, just redirect back
    return redirect('core:study_detail', pk=study.pk)

@login_required
def delete_study(request, study_id):
    """
    Delete a study and clean up all associated data, including uploaded files.
    """
    study = get_object_or_404(Study, id=study_id, created_by=request.user)
    
    if request.method == 'POST':
        study_name = study.name
        study_purpose = study.get_study_purpose_display()
        
        # Clean up uploaded files
        files_to_delete = []
        if study.codebook and study.codebook.name:
            files_to_delete.append(study.codebook.path)
        if study.protocol_file and study.protocol_file.name:
            files_to_delete.append(study.protocol_file.path)
        if study.additional_files and study.additional_files.name:
            files_to_delete.append(study.additional_files.path)
        
        # Delete physical files from filesystem
        import os
        for file_path in files_to_delete:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except OSError as e:
                # Log the error but don't fail the deletion
                print(f"Warning: Could not delete file {file_path}: {e}")
        
        # Clear associated variables 
        variables_count = study.variables.count()
        study.variables.clear()
        
        # Clear session data related to this study
        session_keys_to_clear = [
            f'variables_data_{study.id}',
            f'column_mapping_{study.id}',
            f'target_variables_data_{study.id}',
            f'target_column_mapping_{study.id}',
        ]
        for key in session_keys_to_clear:
            request.session.pop(key, None)
        
        # Delete the study (this will cascade to related observations if any)
        study.delete()
        
        messages.success(
            request,
            f'Successfully deleted {study_purpose.lower()} "{study_name}" and '
            f'cleaned up {variables_count} associated variables and all uploaded files.'
        )
        
        return redirect('core:study_list')
    
    # If GET request, just redirect back
    messages.warning(request, 'Invalid request. Studies can only be deleted via proper form submission.')
    return redirect('core:study_detail', pk=study.pk)
