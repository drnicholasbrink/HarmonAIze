from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.urls import reverse
from django.views.generic import ListView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from .models import Study
from .forms import StudyCreationForm


@login_required
def upload_study(request):
    """
    Upload study page - entry point for creating new studies and uploading codebooks.
    """
    if request.method == 'POST':
        form = StudyCreationForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            study = form.save()
            messages.success(
                request, 
                f'Study "{study.name}" created successfully!'
            )
            
            # If a codebook was uploaded, process it immediately
            if study.source_codebook:
                return redirect('core:process_codebook', study_id=study.pk)
            else:
                return redirect('core:study_detail', pk=study.pk)
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = StudyCreationForm(user=request.user)
    
    return render(request, 'core/upload_study.html', {
        'form': form,
        'page_title': 'Upload New Study'
    })


class StudyListView(LoginRequiredMixin, ListView):
    """
    List view for user's studies.
    """
    model = Study
    template_name = 'core/study_list.html'
    context_object_name = 'studies'
    paginate_by = 10
    
    def get_queryset(self):
        return Study.objects.filter(created_by=self.request.user)


class StudyDetailView(LoginRequiredMixin, DetailView):
    """
    Detail view for a specific study.
    """
    model = Study
    template_name = 'core/study_detail.html'
    context_object_name = 'study'
    
    def get_queryset(self):
        return Study.objects.filter(created_by=self.request.user)


@login_required
def study_dashboard(request):
    """
    Main dashboard showing user's studies and quick actions.
    """
    recent_studies = Study.objects.filter(created_by=request.user)[:5]
    
    context = {
        'recent_studies': recent_studies,
        'total_studies': Study.objects.filter(created_by=request.user).count(),
    }
    
    return render(request, 'core/dashboard.html', context)


@login_required
def process_codebook(request, study_id):
    """
    Process uploaded codebook and extract variables.
    Step 2 of the workflow after study creation.
    """
    study = get_object_or_404(Study, id=study_id, created_by=request.user)
    
    if not study.source_codebook:
        messages.error(request, 'No codebook file found for this study.')
        return redirect('core:study_detail', pk=study.pk)
    
    try:
        # Import the utility functions
        from .utils import process_codebook, validate_variables
        
        # Process the codebook file
        file_path = study.source_codebook.path
        variables_data, detected_format = process_codebook(file_path)
        
        # Update the study with detected format
        study.source_codebook_format = detected_format
        study.save()
        
        # Validate and clean the variables
        variables_data = validate_variables(variables_data)
        
        # Store variables data in session for the confirmation step
        request.session[f'variables_data_{study.id}'] = variables_data
        
        messages.success(
            request,
            f'Successfully extracted {len(variables_data)} variables from your {detected_format.upper()} codebook. '
            'Please review and confirm the variable characteristics below.'
        )
        
        return redirect('core:confirm_variables', study_id=study.id)
        
    except Exception as e:
        messages.error(
            request,
            f'Error processing codebook: {str(e)}. Please check your file format and try again.'
        )
        return redirect('core:study_detail', pk=study.pk)


@login_required
def confirm_variables(request, study_id):
    """
    Show confirmation form for extracted variables.
    Step 3 of the workflow.
    """
    study = get_object_or_404(Study, id=study_id, created_by=request.user)
    
    # Get variables data from session
    variables_data = request.session.get(f'variables_data_{study.id}', [])
    
    if not variables_data:
        messages.error(
            request,
            'No variable data found. Please process your codebook first.'
        )
        return redirect('core:process_codebook', study_id=study.id)
    
    from .forms import VariableConfirmationFormSetFactory
    
    if request.method == 'POST':
        formset = VariableConfirmationFormSetFactory(
            request.POST,
            variables_data=variables_data
        )
        
        if formset.is_valid():
            # Get the included variables
            included_variables = formset.get_included_variables()
            
            # Create Attribute objects for included variables
            from .models import Attribute
            created_attributes = []
            
            for var_data in included_variables:
                attribute, created = Attribute.objects.get_or_create(
                    variable_name=var_data['variable_name'],
                    defaults={
                        'display_name': var_data['display_name'],
                        'description': var_data['description'],
                        'variable_type': var_data['variable_type'],
                        'category': var_data['category'],
                        'unit': var_data['unit'],
                        'ontology_code': var_data['ontology_code'],
                    }
                )
                created_attributes.append(attribute)
            
            # Associate variables with the study
            study.variables.set(created_attributes)
            study.status = 'processing'
            study.save()
            
            # Clear session data
            if f'variables_data_{study.id}' in request.session:
                del request.session[f'variables_data_{study.id}']
            
            messages.success(
                request,
                f'Successfully confirmed {len(included_variables)} variables for your study. '
                'You can now proceed with data harmonisation.'
            )
            
            return redirect('core:study_detail', pk=study.pk)
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        formset = VariableConfirmationFormSetFactory(
            variables_data=variables_data
        )
    
    context = {
        'study': study,
        'formset': formset,
        'variables_count': len(variables_data),
        'page_title': f'Confirm Variables - {study.name}'
    }
    
    return render(request, 'core/confirm_variables.html', context)
