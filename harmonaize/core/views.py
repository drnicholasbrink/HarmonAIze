from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.urls import reverse
from django.views.generic import ListView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
import pandas as pd
from .models import Study
from .forms import StudyCreationForm


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
                
                if study.source_codebook:
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
    recent_studies = Study.objects.filter(created_by=request.user)[:5]
    
    context = {
        'recent_studies': recent_studies,
        'total_studies': Study.objects.filter(created_by=request.user).count(),
    }
    
    return render(request, 'core/dashboard.html', context)
