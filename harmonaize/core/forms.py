from django import forms
from django.core.exceptions import ValidationError
from .models import Study


class StudyCreationForm(forms.ModelForm):
    """
    Form for creating a new study and uploading initial files.
    """
    
    # Data use permissions as a multi-select checkbox field
    data_use_permissions = forms.MultipleChoiceField(
        choices=Study.DATA_USE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text="Select all applicable data use permissions (based on Data Use Ontology)"
    )
    
    class Meta:
        model = Study
        fields = [
            'name',
            'description',
            'principal_investigator',
            'study_type',
            'has_ethical_approval',
            'ethics_approval_number',
            'data_use_permissions',
            'has_dates',
            'has_locations',
            'needs_geolocation',
            'needs_climate_linkage',
            'source_codebook',
            'protocol_file',
            'additional_files',
            'sample_size',
            'study_period_start',
            'study_period_end',
            'geographic_scope',
        ]
        
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Enter study name'
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Brief description of the study (optional)'
            }),
            'principal_investigator': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Principal investigator name (optional)'
            }),
            'study_type': forms.Select(attrs={
                'class': 'form-select'
            }),
            'ethics_approval_number': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'IRB/Ethics approval number (if applicable)'
            }),
            'sample_size': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': 'Approximate number of participants (optional)'
            }),
            'study_period_start': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'study_period_end': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date'
            }),
            'geographic_scope': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., Global, USA, Sub-Saharan Africa (optional)'
            }),
            'source_codebook': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': '.csv,.xlsx,.xls,.sav,.dta,.json,.db,.sqlite,.sqlite3,.xml,.txt'
            }),
            'protocol_file': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': '.pdf,.doc,.docx,.txt,.md'
            }),
            'additional_files': forms.FileInput(attrs={
                'class': 'form-control'
            }),
            # Boolean fields with custom styling
            'has_ethical_approval': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
            'has_dates': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
            'has_locations': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
            'needs_geolocation': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
            'needs_climate_linkage': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
        }
        
        help_texts = {
            'name': 'A descriptive name for your research study',
            'study_type': 'Select the type of research study',
            'has_ethical_approval': 'Check if your study has received ethical/IRB approval',
            'ethics_approval_number': 'Enter the approval number if you have ethical approval',
            'has_dates': 'Does your study include date/time variables that need harmonization?',
            'has_locations': 'Does your study include location data (addresses, facilities, etc.)?',
            'needs_geolocation': 'Do you need to convert addresses to coordinates?',
            'needs_climate_linkage': 'Do you want to link your health data with climate variables?',
            'source_codebook': 'Upload your source codebook in any supported format (CSV, Excel, SPSS, Stata, JSON, DB, etc.)',
            'protocol_file': 'Upload your study protocol or documentation (optional)',
            'additional_files': 'Upload any additional study files (optional)',
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        
        # Make data use permissions field render properly
        if 'data_use_permissions' in self.initial and self.initial['data_use_permissions']:
            self.fields['data_use_permissions'].initial = self.initial['data_use_permissions']

    def clean_source_codebook(self):
        """Validate the uploaded codebook file."""
        codebook = self.cleaned_data.get('source_codebook')
        if codebook:
            # Check file size (limit to 100MB)
            if codebook.size > 100 * 1024 * 1024:
                raise ValidationError("File size cannot exceed 100MB.")
            
            # Check file extension
            allowed_extensions = [
                '.csv', '.xlsx', '.xls', '.sav', '.dta', 
                '.json', '.db', '.sqlite', '.sqlite3', '.xml', '.txt'
            ]
            file_extension = '.' + codebook.name.split('.')[-1].lower()
            if file_extension not in allowed_extensions:
                raise ValidationError(
                    f"Unsupported file format. Allowed formats: {', '.join(allowed_extensions)}"
                )
        
        return codebook

    def clean(self):
        """Perform cross-field validation."""
        cleaned_data = super().clean()
        
        # If ethical approval is checked, require approval number
        has_approval = cleaned_data.get('has_ethical_approval')
        approval_number = cleaned_data.get('ethics_approval_number')
        
        if has_approval and not approval_number:
            raise ValidationError({
                'ethics_approval_number': 'Ethics approval number is required when ethical approval is indicated.'
            })
        
        # Validate date range
        start_date = cleaned_data.get('study_period_start')
        end_date = cleaned_data.get('study_period_end')
        
        if start_date and end_date and start_date > end_date:
            raise ValidationError({
                'study_period_end': 'Study end date must be after start date.'
            })
        
        return cleaned_data

    def save(self, commit=True):
        """Save the study instance."""
        study = super().save(commit=False)
        if self.user:
            study.created_by = self.user
        
        if commit:
            study.save()
            # Handle many-to-many field for data use permissions
            if 'data_use_permissions' in self.cleaned_data:
                study.data_use_permissions = self.cleaned_data['data_use_permissions']
                study.save()
        
        return study


class VariableForm(forms.Form):
    """
    Form for editing individual variable characteristics.
    """
    variable_name = forms.CharField(
        max_length=200,
        required=False,  # Made conditionally required in clean()
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'readonly': True
        }),
        help_text="Original variable name from the codebook"
    )
    
    display_name = forms.CharField(
        max_length=200,
        required=False,  # Made conditionally required in clean()
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Human-readable name for this variable'
        }),
        help_text="Display name for this variable"
    )
    
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 2,
            'placeholder': 'Brief description of this variable (optional)'
        }),
        help_text="Optional description of what this variable represents"
    )
    
    variable_type = forms.ChoiceField(
        choices=[
            ('', '-- Select Type --'),  # Add empty choice
            ('float', 'Float (decimal numbers)'),
            ('int', 'Integer (whole numbers)'),
            ('string', 'String (text)'),
            ('categorical', 'Categorical (predefined choices)'),
            ('boolean', 'Boolean (yes/no)'),
            ('datetime', 'Date/Time'),
        ],
        required=False,  # Made conditionally required in clean()
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text="Data type of this variable"
    )
    
    unit = forms.CharField(
        required=False,
        max_length=50,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g., kg, cm, °C'
        }),
        help_text="Unit of measurement (for numeric variables)"
    )
    
    ontology_code = forms.CharField(
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g., LOINC:33747-0'
        }),
        help_text="Ontology code if available (e.g., NCIT, SNOMED-CT)"
    )
    
    include = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        help_text="Include this variable in the study"
    )
    
    def clean(self):
        """
        Custom validation that only validates required fields if the variable is selected.
        """
        cleaned_data = super().clean()
        include = cleaned_data.get('include', False)
        
        # If the variable is not included, we don't need to validate other fields
        if not include:
            # Clear any validation errors for non-included variables
            self._errors.clear()
            return cleaned_data
        
        # If variable is included, ensure required fields are present
        variable_name = cleaned_data.get('variable_name')
        display_name = cleaned_data.get('display_name')
        variable_type = cleaned_data.get('variable_type')
        
        if include and not variable_name:
            self.add_error('variable_name', 'Variable name is required for included variables.')
        
        if include and not display_name:
            self.add_error('display_name', 'Display name is required for included variables.')
            
        if include and not variable_type:
            self.add_error('variable_type', 'Variable type is required for included variables.')
        
        return cleaned_data


class VariableConfirmationFormSet(forms.BaseFormSet):
    """
    Formset for confirming multiple variables extracted from codebook.
    """
    
    def __init__(self, *args, **kwargs):
        self.variables_data = kwargs.pop('variables_data', [])
        # Set initial data if not already provided
        if 'initial' not in kwargs and self.variables_data:
            kwargs['initial'] = self.variables_data
        super().__init__(*args, **kwargs)
    
    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        if index < len(self.variables_data):
            kwargs['initial'] = self.variables_data[index]
        return kwargs
    
    def total_form_count(self):
        """Return the total number of forms in this formset."""
        if hasattr(self, 'variables_data') and self.variables_data:
            return len(self.variables_data)
        return super().total_form_count()
    
    def _construct_form(self, i, **kwargs):
        """Construct and return the form."""
        if i < len(self.variables_data):
            # Set initial data for this form
            if 'initial' not in kwargs:
                kwargs['initial'] = {}
            kwargs['initial'].update(self.variables_data[i])
        return super()._construct_form(i, **kwargs)
    
    def clean(self):
        """
        Validate that at least one variable is selected for inclusion.
        Only validate forms that are marked for inclusion.
        """
        if any(self.errors):
            # Only check if there are errors in forms that are included
            included_forms_with_errors = []
            for form in self.forms:
                if form.cleaned_data.get('include', False) and form.errors:
                    included_forms_with_errors.append(form)
            
            # If there are errors only in non-included forms, clear them
            if not included_forms_with_errors:
                for form in self.forms:
                    if not form.cleaned_data.get('include', False):
                        form._errors.clear()
        
        included_count = 0
        for form in self.forms:
            if form.cleaned_data.get('include', False):
                included_count += 1
        
        if included_count == 0:
            raise ValidationError("You must include at least one variable in your study.")
    
    def get_included_variables(self):
        """
        Return only the variables that are marked for inclusion.
        """
        included = []
        for form in self.forms:
            if form.is_valid() and form.cleaned_data.get('include', False):
                included.append(form.cleaned_data)
        return included


# Create the formset factory with dynamic extra parameter
def VariableConfirmationFormSetFactory(*args, **kwargs):
    """
    Factory function to create a VariableConfirmationFormSet with dynamic extra parameter.
    """
    variables_data = kwargs.get('variables_data', [])
    extra = len(variables_data) if variables_data else 0
    
    # Create the formset class
    FormSetClass = forms.formset_factory(
        VariableForm,
        formset=VariableConfirmationFormSet,
        extra=extra,
        can_delete=False
    )
    
    # Remove variables_data from kwargs before passing to formset
    formset_kwargs = kwargs.copy()
    if 'variables_data' in formset_kwargs:
        formset_kwargs['variables_data'] = variables_data
    
    return FormSetClass(*args, **formset_kwargs)
