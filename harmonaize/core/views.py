from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.urls import reverse
from django.views.generic import ListView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.conf import settings
from django.db import transaction
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.offline import plot
from .models import Study, Project, ProjectMembership, ProjectInvitation, Attribute, CodebookGenerationRun
from .forms import StudyCreationForm, ProjectCreationForm, ProjectInvitationForm
from health.models import RawDataFile
from core.tsne_service import tsne_service
from django.contrib.auth import get_user_model
from django.utils.crypto import get_random_string


@login_required
def upload_study(request):
    """
    Upload study page - entry point for creating new studies.
    Also handles codebook reupload for existing studies.
    """
    study_id = request.POST.get('study_id')  # For reupload functionality
    existing_study = None
    
    if study_id:
        existing_study = get_object_or_404(Study, id=study_id, project__members=request.user)
    
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
                study.status = 'codebook_uploaded'
                study.save(update_fields=['status'])
                
                messages.success(
                    request,
                    f'Codebook uploaded successfully! Previous {variables_count} variables were reset. '
                    f'Now let\'s extract variables from the codebook.'
                )
                
                # Redirect to extraction page based on study purpose
                if study.study_purpose == 'target':
                    return redirect('core:target_map_codebook', study_id=study.pk)
                else:
                    return redirect('health:map_codebook', study_id=study.pk)
            else:
                messages.error(request, 'Please correct the errors below.')
        else:
            # Handle new study creation
            form = StudyCreationForm(request.POST, request.FILES, user=request.user)
            if form.is_valid():
                study = form.save()
                
                if study.codebook:
                    study.status = 'codebook_uploaded'
                    study.save(update_fields=['status'])
                    messages.success(
                        request, 
                        f'Study "{study.name}" created successfully with codebook! '
                        f'Let\'s extract the variables from your codebook.'
                    )
                    
                    # Redirect to extraction page based on study purpose
                    if study.study_purpose == 'target':
                        return redirect('core:target_map_codebook', study_id=study.pk)
                    else:
                        return redirect('health:map_codebook', study_id=study.pk)
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
        # Filter studies where the user is a member of the project
        queryset = Study.objects.filter(project__members=self.request.user).distinct()
        
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
        all_studies = Study.objects.filter(project__members=self.request.user).distinct()
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
        # Allow access if user is a member of the project
        return Study.objects.filter(project__members=self.request.user).distinct()
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        study = self.object
        
        # Calculate variable statistics
        variables = study.variables.all()
        total_vars = variables.count()
        
        # Variable type distribution
        variable_types = {}
        for variable in variables:
            var_type = variable.variable_type
            variable_types[var_type] = variable_types.get(var_type, 0) + 1
        
        # Additional statistics
        variables_with_units = variables.exclude(unit='').count()
        variables_with_codes = variables.exclude(ontology_code='').count()
        
        # Embedding statistics
        variables_with_embeddings = variables.filter(
            name_embedding__isnull=False,
            description_embedding__isnull=False,
        ).count()
        embedding_progress = round((variables_with_embeddings / total_vars * 100), 1) if total_vars > 0 else 0
        
        # Calculate percentages for type distribution bars
        if total_vars > 0:
            for var_type in variable_types:
                variable_types[var_type] = {
                    'count': variable_types[var_type],
                    'percentage': (variable_types[var_type] / total_vars) * 100,
                }
        
        # For target studies, check if any source studies have been harmonised to it
        has_harmonised_sources = False
        if study.study_purpose == 'target':
            # target_mappings is the related_name for MappingSchema.target_study
            has_harmonised_sources = study.target_mappings.exists()
        
        context.update({
            'variable_types': variable_types,
            'variables_with_units': variables_with_units,
            'variables_with_codes': variables_with_codes,
            'total_variables': total_vars,
            'variables_with_embeddings': variables_with_embeddings,
            'embedding_progress': round(embedding_progress, 1),
            'has_harmonised_sources': has_harmonised_sources,
            'latest_codebook_run': study.codebook_generation_runs.first(),
        })
        
        return context


def _queue_codebook_generation_run(study, user, trigger_mode, attribute_ids=None):
    attribute_id_list = list(
        attribute_ids or study.variables.order_by("id").values_list("id", flat=True)
    )
    run = CodebookGenerationRun.objects.create(
        study=study,
        requested_by=user,
        trigger_mode=trigger_mode,
        total_attributes_count=len(attribute_id_list),
    )
    if attribute_id_list:
        run.requested_attributes.set(attribute_id_list)

    def _enqueue():
        from core.tasks import enrich_generated_codebook_descriptions

        task = enrich_generated_codebook_descriptions.delay(run.id)
        CodebookGenerationRun.objects.filter(id=run.id).update(
            celery_task_id=getattr(task, "id", ""),
        )

    transaction.on_commit(_enqueue)
    return run


@login_required
@require_http_methods(["POST"])
def generate_study_codebook(request, study_id):
    study = get_object_or_404(Study, id=study_id, project__members=request.user)

    if not study.variables.exists():
        messages.error(request, "Add variables to the study before generating a codebook.")
        return redirect("core:study_detail", pk=study_id)

    latest_run = study.codebook_generation_runs.first()
    if latest_run and latest_run.is_active:
        messages.warning(request, "A codebook generation run is already in progress for this study.")
        return redirect("core:study_detail", pk=study_id)

    run = _queue_codebook_generation_run(
        study,
        request.user,
        CodebookGenerationRun.TRIGGER_MANUAL,
    )

    messages.success(
        request,
        f"Codebook generation queued for {run.total_attributes_count} variables.",
    )
    return redirect("core:study_detail", pk=study_id)


@login_required
@require_http_methods(["GET"])
def codebook_generation_status(request, study_id):
    study = get_object_or_404(Study, id=study_id, project__members=request.user)
    run = study.codebook_generation_runs.first()

    if not run:
        return JsonResponse({
            "has_run": False,
            "is_active": False,
        })

    return JsonResponse({
        "has_run": True,
        "run_id": run.id,
        "status": run.status,
        "status_display": run.get_status_display(),
        "trigger_mode": run.trigger_mode,
        "progress_percentage": run.progress_percentage,
        "total": run.total_attributes_count,
        "processed": run.processed_attributes_count,
        "failed": run.failed_attributes_count,
        "is_active": run.is_active,
        "error_message": run.error_message,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "codebook_filename": run.codebook_filename,
    })


@login_required
def toggle_climate_linkage(request, pk):
    """Allow study owners to toggle the climate linkage flag from the study detail page."""
    study = get_object_or_404(Study, pk=pk, project__members=request.user)
    if request.method != "POST":
        return redirect("core:study_detail", pk=study.pk)

    new_value = bool(request.POST.get("needs_climate_linkage"))
    study.needs_climate_linkage = new_value
    study.save(update_fields=["needs_climate_linkage", "updated_at"] if hasattr(study, "updated_at") else ["needs_climate_linkage"])

    messages.success(
        request,
        "Climate linkage {} for this study.".format("enabled" if new_value else "disabled"),
    )
    return redirect("core:study_detail", pk=study.pk)


@login_required
@require_http_methods(["POST"])
def update_study_field(request, pk):
    """
    API endpoint to update individual study fields via AJAX.
    Accepts JSON data with field name and value, or file uploads for file fields.
    """
    import json
    
    study = get_object_or_404(Study, pk=pk, project__members=request.user)
    
    # Handle file uploads (multipart form data)
    if request.content_type and 'multipart/form-data' in request.content_type:
        field_name = request.POST.get('field')
        allowed_file_fields = ['protocol_file', 'additional_files']
        
        if field_name not in allowed_file_fields:
            messages.error(request, f'Field {field_name} is not a valid file field.')
            return redirect('core:study_detail', pk=pk)
        
        if field_name in request.FILES:
            uploaded_file = request.FILES[field_name]
            setattr(study, field_name, uploaded_file)
            study.save()
            messages.success(request, f'File uploaded successfully.')
        else:
            messages.error(request, 'No file was uploaded.')
        
        return redirect('core:study_detail', pk=pk)
    
    # Handle JSON data for regular field updates
    try:
        data = json.loads(request.body)
        field_name = data.get('field')
        field_value = data.get('value')
        
        # Define allowed fields for security
        allowed_fields = [
            'name', 'description', 'principal_investigator', 'study_type',
            'has_ethical_approval', 'ethics_approval_number', 'has_dates',
            'has_locations', 'needs_geolocation', 'needs_climate_linkage',
            'sample_size', 'study_period_start', 'study_period_end',
            'geographic_scope', 'data_use_permissions'
        ]
        
        if field_name not in allowed_fields:
            return JsonResponse({'success': False, 'error': f'Field {field_name} is not editable'}, status=400)
        
        # Handle special field types
        if field_name in ['has_ethical_approval', 'has_dates', 'has_locations', 'needs_geolocation', 'needs_climate_linkage']:
            field_value = field_value in [True, 'true', 'True', '1', 1, 'on']
        elif field_name == 'sample_size':
            field_value = int(field_value) if field_value else None
        elif field_name in ['study_period_start', 'study_period_end']:
            from datetime import datetime
            if field_value:
                field_value = datetime.strptime(field_value, '%Y-%m-%d').date()
            else:
                field_value = None
        elif field_name == 'data_use_permissions':
            # Ensure it's a list
            if isinstance(field_value, str):
                field_value = [field_value] if field_value else []
        
        # Update the field
        setattr(study, field_name, field_value)
        study.save()
        
        # Get display value for response
        display_value = field_value
        if field_name == 'study_type':
            display_value = study.get_study_type_display()
        elif field_name in ['has_ethical_approval', 'has_dates', 'has_locations', 'needs_geolocation', 'needs_climate_linkage']:
            display_value = 'Yes' if field_value else 'No'
        
        return JsonResponse({
            'success': True,
            'field': field_name,
            'value': field_value,
            'display_value': display_value
        })
        
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
def download_codebook(request, pk):
    """
    Download the codebook file for a study with proper filename and content-disposition.
    Forces the browser to download the file rather than display it.
    """
    from django.http import FileResponse, Http404
    import os
    import mimetypes
    
    study = get_object_or_404(Study, pk=pk, project__members=request.user)
    
    if not study.codebook:
        raise Http404("No codebook file found for this study.")
    
    # Get the file
    codebook_file = study.codebook
    
    # Determine the filename - use study name + format extension
    original_extension = os.path.splitext(codebook_file.name)[1]
    if not original_extension:
        # Try to get extension from codebook_format
        format_to_ext = {
            'csv': '.csv',
            'xlsx': '.xlsx',
            'xls': '.xls',
            'json': '.json',
            'spss': '.sav',
            'stata': '.dta',
            'sqlite': '.db',
            'xml': '.xml',
            'text': '.txt',
        }
        original_extension = format_to_ext.get(study.codebook_format, '.dat')
    
    # Create a clean filename from study name
    clean_name = "".join(c for c in study.name if c.isalnum() or c in (' ', '-', '_')).strip()
    clean_name = clean_name.replace(' ', '_')
    download_filename = f"{clean_name}_codebook{original_extension}"
    
    # Determine content type
    content_type, _ = mimetypes.guess_type(download_filename)
    if not content_type:
        content_type = 'application/octet-stream'
    
    # Create the response
    response = FileResponse(
        codebook_file.open('rb'),
        content_type=content_type,
        as_attachment=True,
        filename=download_filename
    )
    
    return response


@login_required
@require_http_methods(["POST"])
def delete_study_file(request, pk, file_type):
    """
    Delete a specific file from a study (codebook or protocol).
    """
    from .models import StudyDocument
    
    study = get_object_or_404(Study, pk=pk, project__members=request.user)
    
    if file_type == 'codebook':
        if study.codebook:
            study.codebook.delete(save=False)
            study.codebook = None
            study.codebook_format = ''
            study.status = 'created'  # Reset status since codebook is gone
            study.save()
            messages.success(request, 'Codebook deleted successfully.')
        else:
            messages.warning(request, 'No codebook to delete.')
    elif file_type == 'protocol':
        if study.protocol_file:
            study.protocol_file.delete(save=False)
            study.protocol_file = None
            study.save()
            messages.success(request, 'Protocol file deleted successfully.')
        else:
            messages.warning(request, 'No protocol file to delete.')
    else:
        messages.error(request, 'Invalid file type.')
    
    return redirect('core:study_detail', pk=pk)


@login_required
@require_http_methods(["POST"])
def add_study_document(request, pk):
    """
    Add an additional document to a study.
    """
    from .models import StudyDocument
    
    study = get_object_or_404(Study, pk=pk, project__members=request.user)
    
    if 'file' in request.FILES:
        doc = StudyDocument.objects.create(
            study=study,
            file=request.FILES['file'],
            document_type=request.POST.get('document_type', 'additional'),
            description=request.POST.get('description', ''),
            uploaded_by=request.user
        )
        messages.success(request, f'Document "{doc.filename}" uploaded successfully.')
    else:
        messages.error(request, 'No file was uploaded.')
    
    return redirect('core:study_detail', pk=pk)


@login_required
@require_http_methods(["POST"])
def delete_study_document(request, pk, doc_id):
    """
    Delete an additional document from a study.
    """
    from .models import StudyDocument
    
    doc = get_object_or_404(StudyDocument, pk=doc_id, study__pk=pk, study__project__members=request.user)
    filename = doc.filename
    
    doc.file.delete(save=False)
    doc.delete()
    
    messages.success(request, f'Document "{filename}" deleted successfully.')
    return redirect('core:study_detail', pk=pk)


@login_required
def download_study_document(request, pk, doc_id):
    """
    Download an additional document from a study.
    """
    from django.http import FileResponse, Http404
    from .models import StudyDocument
    import mimetypes
    
    doc = get_object_or_404(StudyDocument, pk=doc_id, study__pk=pk, study__project__members=request.user)
    
    content_type, _ = mimetypes.guess_type(doc.filename)
    if not content_type:
        content_type = 'application/octet-stream'
    
    response = FileResponse(
        doc.file.open('rb'),
        content_type=content_type,
        as_attachment=True,
        filename=doc.filename
    )
    
    return response


@login_required
@require_http_methods(["POST"])
def update_attribute(request, attribute_id):
    """
    API endpoint to update an attribute/variable via AJAX.
    Accepts JSON data with field values.
    """
    import json
    
    attribute = get_object_or_404(Attribute, pk=attribute_id)
    
    # Check user has access to the study
    if attribute.study and not attribute.study.project.members.filter(pk=request.user.pk).exists():
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)
    
    try:
        data = json.loads(request.body)
        
        # Define allowed fields for security
        allowed_fields = [
            'display_name', 'description', 'unit', 'ontology_code', 'variable_type', 'category'
        ]
        
        updated_fields = []
        for field_name, field_value in data.items():
            if field_name in allowed_fields:
                # Validate variable_type choices
                if field_name == 'variable_type':
                    valid_types = ['float', 'int', 'string', 'categorical', 'boolean', 'datetime']
                    if field_value not in valid_types:
                        return JsonResponse({'success': False, 'error': f'Invalid variable type: {field_value}'}, status=400)
                
                # Validate category choices
                if field_name == 'category':
                    valid_categories = ['health', 'climate', 'geolocation']
                    if field_value not in valid_categories:
                        return JsonResponse({'success': False, 'error': f'Invalid category: {field_value}'}, status=400)
                
                setattr(attribute, field_name, field_value)
                updated_fields.append(field_name)
        
        if updated_fields:
            attribute.save()
            # Clear embeddings if name or description changed (they need to be regenerated)
            if 'description' in updated_fields:
                attribute.description_embedding = None
                attribute.save(update_fields=['description_embedding'])
        
        return JsonResponse({
            'success': True,
            'updated_fields': updated_fields,
            'attribute': {
                'id': attribute.id,
                'variable_name': attribute.variable_name,
                'display_name': attribute.display_name,
                'description': attribute.description,
                'unit': attribute.unit,
                'ontology_code': attribute.ontology_code,
                'variable_type': attribute.variable_type,
                'category': attribute.category,
            }
        })
        
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
def study_dashboard(request):
    """
    Main dashboard showing user's studies and quick actions.
    """
    all_studies = Study.objects.filter(project__members=request.user).distinct()
    source_studies = all_studies.filter(study_purpose='source')
    target_studies = all_studies.filter(study_purpose='target')
    
    recent_source_studies = source_studies.order_by('-created_at')[:5]
    recent_target_studies = target_studies.order_by('-created_at')[:3]  # Show multiple target databases
    
    # Get project information
    all_projects = Project.objects.filter(members=request.user).distinct()
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
            
            # Add creator as owner
            ProjectMembership.objects.create(
                user=request.user,
                project=project,
                role='owner'
            )
            
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
        return Project.objects.filter(members=self.request.user).distinct()
    
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
        return Project.objects.filter(members=self.request.user).order_by('-created_at').distinct()
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Calculate aggregate statistics
        total_projects = self.get_queryset().count()
        total_studies = Study.objects.filter(project__members=self.request.user).distinct().count()
        
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
    # Get project from query parameter if provided (e.g., from harmonisation flow)
    project_id = request.GET.get('project')
    initial_data = {}
    if project_id:
        initial_data['project'] = project_id
    
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
        form = StudyCreationForm(user=request.user, initial=initial_data)
    
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
    study = get_object_or_404(Study, id=study_id, project__members=request.user, study_purpose='target')
    
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
    study = get_object_or_404(Study, id=study_id, project__members=request.user, study_purpose='target')
    
    from core.utils import process_codebook_extraction
    return process_codebook_extraction(request, study, codebook_type='target')


@login_required
def target_select_variables(request, study_id):
    """
    Let user select which extracted target database variables to include in the study.
    """
    study = get_object_or_404(Study, id=study_id, project__members=request.user, study_purpose='target')
    
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
                            study=study,
                            defaults={
                                'display_name': var_data.get('display_name', var_data['variable_name']),
                                'description': var_data.get('description', ''),
                                'variable_type': var_data.get('variable_type', 'string'),
                                'unit': var_data.get('unit', ''),
                                'ontology_code': var_data.get('ontology_code', ''),
                                'category': var_data.get('category', 'health'),  # Use extracted category or default to health
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
                study.save(update_fields=['status'])
                
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
    study = get_object_or_404(Study, id=study_id, project__members=request.user, study_purpose='target')
    
    if request.method == 'POST':
        # Clear all target variables associated with the study
        variables_count = study.variables.filter(source_type='target').count()
        study.variables.filter(source_type='target').delete()
        
        # Clear session data related to this study
        request.session.pop(f'target_variables_data_{study.id}', None)
        request.session.pop(f'target_column_mapping_{study.id}', None)
        
        # Reset study status
        study.status = 'created'
        study.save(update_fields=['status'])
        
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
    study = get_object_or_404(Study, id=study_id, project__members=request.user)
    
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


# Embedding generation views
# ==========================

@login_required
def generate_study_embeddings(request, study_id):
    """
    Generate embeddings for all attributes in a study.
    """
    study = get_object_or_404(Study, id=study_id, project__members=request.user)
    
    if request.method == 'POST':
        # Check if OpenAI API key is configured before queuing tasks
        if not settings.OPENAI_API_KEY:
            messages.error(
                request,
                "OpenAI API key is not configured. Please set the OPENAI_API_KEY environment variable "
                "to enable AI-powered embedding generation. See the documentation for setup instructions."
            )
            return redirect('core:study_detail', pk=study_id)
        
        from core.tasks import generate_embeddings_for_study
        
        # Queue the embedding generation task
        task = generate_embeddings_for_study.delay(study_id)
        
        # Count attributes to give user feedback
        attribute_count = study.variables.count()
        
        messages.success(
            request,
            f"Embedding generation queued for {attribute_count} attributes in study '{study.name}'. "
            f"This may take a few minutes. Check back later to see the progress."
        )
        
    return redirect('core:study_detail', pk=study_id)


@login_required
@require_http_methods(["GET"])
def embedding_progress(request, study_id):
    """
    API endpoint to check embedding generation progress for a study.
    Returns JSON with current progress.
    """
    study = get_object_or_404(Study, id=study_id, project__members=request.user)

    total_variables = study.variables.count()
    variables_with_embeddings = study.variables.filter(
        name_embedding__isnull=False,
        description_embedding__isnull=False,
    ).count()

    if total_variables > 0:
        progress_percentage = round(
            (variables_with_embeddings / total_variables * 100), 1,
        )
    else:
        progress_percentage = 0

    is_complete = variables_with_embeddings == total_variables

    return JsonResponse({
        "total": total_variables,
        "completed": variables_with_embeddings,
        "percentage": progress_percentage,
        "is_complete": is_complete,
    })


@login_required
def generate_attribute_embedding(request, attribute_id):
    """
    Generate embeddings for a single attribute.
    """
    from core.models import Attribute
    
    attribute = get_object_or_404(Attribute, id=attribute_id)
    
    # Check if user has access to this attribute (via their studies)
    user_studies = Study.objects.filter(project__members=request.user).distinct()
    if not user_studies.filter(variables=attribute).exists():
        messages.error(request, "You don't have permission to generate embeddings for this attribute.")
        return redirect('core:study_list')
    
    if request.method == 'POST':
        # Check if OpenAI API key is configured before queuing tasks
        if not settings.OPENAI_API_KEY:
            messages.error(
                request,
                "OpenAI API key is not configured. Please set the OPENAI_API_KEY environment variable "
                "to enable AI-powered embedding generation."
            )
            # Find a study that contains this attribute to redirect to
            study = user_studies.filter(variables=attribute).first()
            if study:
                return redirect('core:study_detail', pk=study.pk)
            return redirect('core:study_list')
        
        from core.tasks import generate_attribute_embeddings
        
        # Queue the embedding generation task
        task = generate_attribute_embeddings.delay(attribute_id)
        
        messages.success(
            request,
            f"Embedding generation queued for attribute '{attribute.variable_name}'. "
            f"This may take a moment. Check back to see the progress."
        )
        
        # Safely handle redirect to a known internal page
        # Find a study that contains this attribute to redirect to
        study = user_studies.filter(variables=attribute).first()
        if study:
            return redirect('core:study_detail', pk=study.pk)
        
        # Fallback to study list if no study found
        return redirect('core:study_list')
    
    # If GET request, redirect back 
    return redirect('core:study_list')


@login_required
@require_http_methods(["POST"])
def generate_project_tsne(request, project_id):
    """
    Generate t-SNE projections for all attributes in a project.
    """
    from core.tasks import generate_tsne_projections_for_project
    
    project = get_object_or_404(Project, id=project_id, members=request.user)
    
    # Get embedding type from POST data
    embedding_type = request.POST.get('embedding_type', 'both')
    if embedding_type not in ['name', 'description', 'both']:
        embedding_type = 'both'
    
    # Queue the t-SNE generation task
    task = generate_tsne_projections_for_project.delay(project_id, embedding_type)
    
    messages.success(
        request,
        f"t-SNE projection generation queued for project '{project.name}'. "
        f"This may take a few minutes. You can continue working while projections are computed."
    )
    
    # Redirect back to the project detail page
    return redirect('core:project_detail', pk=project_id)


@login_required
@require_http_methods(["GET"])
def tsne_progress(request, project_id):
    """
    Get t-SNE projection progress for a project (AJAX endpoint).
    """
    from core.tasks import check_tsne_projection_progress
    
    # Validate user has access to this project
    project = get_object_or_404(Project, id=project_id, members=request.user)
    
    # Get progress information using the validated project_id
    result = check_tsne_projection_progress(project.id)
    
    if result.get("success"):
        return JsonResponse({
            "total": result["total_attributes"],
            "name_completed": result["name_projections"],
            "description_completed": result["description_projections"],
            "name_percentage": result["name_percentage"],
            "description_percentage": result["description_percentage"],
            "is_complete": result["is_complete"],
        })
    
    return JsonResponse({
        "error": result.get("error", "Unknown error"),
    }, status=500)


@login_required
def tsne_visualization(request, project_id):
    """
    Display the t-SNE visualization page with server-side Plotly generation.
    """
    project = get_object_or_404(Project, id=project_id, members=request.user)
    
    # Get embedding type from query parameters
    embedding_type = request.GET.get("type", "name")
    if embedding_type not in ["name", "description"]:
        embedding_type = "name"
    
    # Get visualization data
    df = tsne_service.get_projection_data_for_visualization(
        project=project,
        embedding_type=embedding_type,
    )
    
    # Check if we have data
    if df.empty:
        messages.warning(
            request,
            f"No {embedding_type} t-SNE projections available for "
            f"project '{project.name}'. Please generate projections first.",
        )
        return redirect("core:project_detail", pk=project_id)
    
    # Create grouped legend with separate traces for source and target studies
    # Create figure
    fig = go.Figure()

    # Get unique studies
    studies = df["study_name"].unique()

    # Color palette for studies
    colors = px.colors.qualitative.Set1[:len(studies)]
    study_colors = dict(zip(studies, colors, strict=True))

    # Symbol mapping
    symbol_map = {"source": "circle", "target": "triangle-up"}

    # Create traces for each combination of study and source type
    for source_type in ["source", "target"]:  # Order matters for legend grouping
        source_df = df[df["source_type"] == source_type]
        if source_df.empty:
            continue

        for study in studies:
            study_df = source_df[source_df["study_name"] == study]
            if study_df.empty:
                continue

            # Create legend group name
            legend_name = (
                "Source Studies" if source_type == "source" else "Target Studies"
            )
            trace_name = f"{study}"

            fig.add_trace(go.Scatter(
                x=study_df["x"],
                y=study_df["y"],
                mode="markers",
                name=trace_name,
                legendgroup=legend_name,
                legendgrouptitle_text=legend_name,
                marker={
                    "symbol": symbol_map[source_type],
                    "color": study_colors[study],
                    "size": 8,
                    "opacity": 0.7,
                    "line": {"width": 1, "color": "white"},
                },
                customdata=study_df[
                    ["variable_name", "description", "category", "source_type"]
                ].values,
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "Study: " + study + "<br>"
                    "Category: %{customdata[2]}<br>"
                    "Type: %{customdata[3]}<br>"
                    "Description: %{customdata[1]}<br>"
                    "<extra></extra>"
                ),
            ))

    # Update layout
    fig.update_layout(
        template="plotly_white",
        width=900,
        height=600,
        title={
            "text": (
                f"t-SNE Visualization: {embedding_type.title()} "
                f"Embeddings for {project.name}"
            ),
            "x": 0.5,
        },
        xaxis_title="t-SNE Dimension 1",
        yaxis_title="t-SNE Dimension 2",
        showlegend=True,
        legend={
            "orientation": "v",
            "yanchor": "top",
            "y": 1,
            "xanchor": "left",
            "x": 1.01,
            "groupclick": "toggleitem",
        },
    )
    
    # Convert to HTML div using Plotly offline
    plot_div = plot(fig, output_type="div", include_plotlyjs=True)
    
    context = {
        "project": project,
        "embedding_type": embedding_type,
        "plot_div": plot_div,
        "data_count": len(df),
        "categories": (
            df["category"].unique().tolist() if not df.empty else []
        ),
        "variable_types": (
            df["variable_type"].unique().tolist() if not df.empty else []
        ),
        "source_types": (
            df["source_type"].unique().tolist() if not df.empty else []
        ),
    }
    
    return render(request, "core/tsne_visualization.html", context)


@login_required
def tsne_data_api(request, project_id):
    """
    API endpoint to get t-SNE data for visualization (AJAX).
    Note: This is kept for backward compatibility but the main visualization
    now uses server-side Plotly generation.
    """
    project = get_object_or_404(Project, id=project_id, members=request.user)
    
    # Get embedding type from query parameters
    embedding_type = request.GET.get("type", "name")
    if embedding_type not in ["name", "description"]:
        embedding_type = "name"
    
    # Get visualization data
    df = tsne_service.get_projection_data_for_visualization(
        project=project,
        embedding_type=embedding_type,
    )
    
    if df.empty:
        return JsonResponse({
            "error": f"No {embedding_type} t-SNE projections available",
            "data": [],
        })
    
    # Convert DataFrame to JSON format
    data = df.to_dict("records")
    
    return JsonResponse({
        "success": True,
        "data": data,
        "count": len(data),
        "embedding_type": embedding_type,
        "project_name": project.name,
    })


# Project Membership Views

@login_required
def project_members(request, pk):
    """
    List members of a project and handle invitations.
    """
    project = get_object_or_404(Project, pk=pk, members=request.user)
    
    # Check permission to view members (generally all members can see who else is a member)
    # But only owner/manager can invite
    
    current_membership = get_object_or_404(ProjectMembership, project=project, user=request.user)
    can_manage = current_membership.role in ['owner', 'manager']
    
    members = ProjectMembership.objects.filter(project=project).select_related('user')
    invitations = ProjectInvitation.objects.filter(project=project, status='pending') if can_manage else []
    
    form = ProjectInvitationForm()
    
    context = {
        'project': project,
        'members': members,
        'invitations': invitations,
        'form': form,
        'can_manage': can_manage,
        'page_title': f'Members - {project.name}'
    }
    return render(request, 'core/project_members.html', context)

@login_required
def invite_member(request, pk):
    """
    Process member invitation.
    """
    project = get_object_or_404(Project, pk=pk, members=request.user)
    
    # Check permissions
    if not ProjectMembership.objects.filter(
        project=project, 
        user=request.user, 
        role__in=['owner', 'manager']
    ).exists():
        messages.error(request, "You do not have permission to invite members.")
        return redirect('core:project_members', pk=pk)

    if request.method == 'POST':
        form = ProjectInvitationForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            role = form.cleaned_data['role']
            
            # Check if user is already a member
            User = get_user_model()
            try:
                user = User.objects.get(email=email)
                if ProjectMembership.objects.filter(project=project, user=user).exists():
                    messages.warning(request, f"User {email} is already a member of this project.")
                    return redirect('core:project_members', pk=pk)
            except User.DoesNotExist:
                # User doesn't exist yet, that's fine
                pass
            
            # Check for pending invitation
            if ProjectInvitation.objects.filter(project=project, email=email, status='pending').exists():
                messages.warning(request, f"An invitation is already pending for {email}.")
                return redirect('core:project_members', pk=pk)

            # Create invitation
            key = get_random_string(64)
            invitation = ProjectInvitation.objects.create(
                email=email,
                project=project,
                role=role,
                invited_by=request.user,
                key=key
            )
            
            # Generate invite URL
            invite_url = request.build_absolute_uri(reverse('core:accept_invite', kwargs={'key': key}))
            
            # In a real app, send email here. For now, display message.
            messages.success(
                request, 
                f"Invitation created for {email}. Share this link: {invite_url}"
            )
            
            return redirect('core:project_members', pk=pk)
    
    return redirect('core:project_members', pk=pk)

def accept_invite(request, key):
    """
    Handle invitation acceptance.
    """
    invitation = get_object_or_404(ProjectInvitation, key=key, status='pending')
    
    if request.user.is_authenticated:
        if request.user.email.lower() != invitation.email.lower():
             messages.error(
                 request, 
                 f"This invitation is for {invitation.email}, but you are logged in as {request.user.email}."
             )
             return redirect('core:project_list')

        # Add to project
        ProjectMembership.objects.create(
            user=request.user,
            project=invitation.project,
            role=invitation.role
        )
        invitation.status = 'accepted'
        invitation.save()
        
        messages.success(request, f"Welcome to {invitation.project.name}!")
        return redirect('core:project_detail', pk=invitation.project.pk)
    else:
        # Store key in session and redirect to login
        # You might need a custom login view or middleware to handle this redirect after login
        # For now, we'll prompt them to login
        login_url = reverse('account_login')
        messages.info(request, f"Please log in as {invitation.email} to accept the invitation.")
        
        return redirect(f"{login_url}?next={request.path}")

@login_required
def remove_member(request, project_id, member_id):
    """
    Remove a member from the project.
    """
    project = get_object_or_404(Project, pk=project_id, members=request.user)
    
    # Check permissions (must be owner or manager)
    current_membership = get_object_or_404(ProjectMembership, project=project, user=request.user)
    if current_membership.role not in ['owner', 'manager']:
        messages.error(request, "You do not have permission to remove members.")
        return redirect('core:project_members', pk=project_id)
        
    member_to_remove = get_object_or_404(ProjectMembership, id=member_id, project=project)
    
    # Prevent removing oneself if owner (unless another owner exists - simplified logic here)
    if member_to_remove.user == request.user:
        messages.error(request, "You cannot remove yourself. Please leave the project instead.")
        return redirect('core:project_members', pk=project_id)
        
    # Prevent manager removing owner
    if member_to_remove.role == 'owner' and current_membership.role == 'manager':
        messages.error(request, "Managers cannot remove owners.")
        return redirect('core:project_members', pk=project_id)

    user_name = member_to_remove.user.email
    member_to_remove.delete()
    messages.success(request, f"{user_name} removed from project.")
    
    return redirect('core:project_members', pk=project_id)
