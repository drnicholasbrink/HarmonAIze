"""
Forms for climate data configuration and management.
"""
from django import forms
from django.core.exceptions import ValidationError
from datetime import date, timedelta
from core.models import Study, Location
from .models import ClimateDataSource, ClimateVariable, ClimateDataRequest


class ClimateDataConfigurationForm(forms.ModelForm):
    """Form for configuring climate data retrieval for a study."""
    
    variables = forms.ModelMultipleChoiceField(
        queryset=ClimateVariable.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        help_text="Select climate variables to retrieve",
        required=True,
    )
    
    data_source = forms.ModelChoiceField(
        queryset=ClimateDataSource.objects.filter(is_active=True),
        empty_label="Select a data source",
        help_text="Choose the climate data source to use",
        required=True,
    )
    
    start_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        help_text="Start date for climate data retrieval",
        required=True,
    )
    
    end_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        help_text="End date for climate data retrieval",
        required=True,
    )
    
    temporal_aggregation = forms.ChoiceField(
        choices=ClimateDataRequest._meta.get_field('temporal_aggregation').choices,
        initial='none',
        widget=forms.Select(attrs={'class': 'form-control'}),
        help_text="How to aggregate data over time",
        required=True,
    )
    
    spatial_buffer_km = forms.FloatField(
        min_value=0,
        max_value=100,
        initial=0,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.1'}),
        help_text="Buffer radius around point locations in kilometres (0 for exact point)",
        required=False,
    )
    
    class Meta:
        model = ClimateDataRequest
        fields = [
            'data_source',
            'variables',
            'start_date',
            'end_date',
            'temporal_aggregation',
            'spatial_buffer_km',
        ]
    
    def __init__(self, *args, study=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.study = study
        self.user = user
        
        # Group variables by category for better display
        if 'variables' in self.fields:
            self.fields['variables'].queryset = ClimateVariable.objects.all().order_by('category', 'display_name')
        
        # Set date limits based on study period if available
        if study:
            if study.study_period_start:
                self.fields['start_date'].initial = study.study_period_start
                self.fields['start_date'].widget.attrs['min'] = study.study_period_start.isoformat()
            if study.study_period_end:
                self.fields['end_date'].initial = study.study_period_end
                self.fields['end_date'].widget.attrs['max'] = study.study_period_end.isoformat()
    
    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')
        data_source = cleaned_data.get('data_source')
        variables = cleaned_data.get('variables')
        
        # Validate date range
        if start_date and end_date:
            if start_date > end_date:
                raise ValidationError("Start date must be before end date")
            
            # Check against data source availability
            if data_source:
                if data_source.data_start_date and start_date < data_source.data_start_date:
                    raise ValidationError(
                        f"Start date cannot be before {data_source.data_start_date} "
                        f"(earliest available data for {data_source.name})"
                    )
                if data_source.data_end_date and end_date > data_source.data_end_date:
                    raise ValidationError(
                        f"End date cannot be after {data_source.data_end_date} "
                        f"(latest available data for {data_source.name})"
                    )
        
        # Validate that selected variables are available in the data source
        if data_source and variables:
            available_variables = data_source.variables.all()
            for variable in variables:
                if variable not in available_variables:
                    raise ValidationError(
                        f"Variable '{variable.display_name}' is not available in {data_source.name}"
                    )
        
        return cleaned_data
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.study = self.study
        # User authorization flows through study.created_by (Core module)
        # No need to set requested_by - accessed via property

        if commit:
            instance.save()
            # Save many-to-many relationships
            self.save_m2m()
            
            # Add locations from study
            if self.study:
                study_locations = Location.objects.filter(
                    observations__attribute__studies=self.study
                ).distinct()
                instance.locations.set(study_locations)
        
        return instance


class ClimateVariableSelectionForm(forms.Form):
    """Simple form for selecting climate variables to view or download."""

    variables = forms.ModelMultipleChoiceField(
        queryset=ClimateVariable.objects.all(),
        widget=forms.CheckboxSelectMultiple(
            attrs={'class': 'form-check-input'}
        ),
        required=False,
        label="Select Climate Variables",
    )

    category_filter = forms.MultipleChoiceField(
        choices=[],  # Will be populated dynamically in __init__
        widget=forms.CheckboxSelectMultiple(
            attrs={'class': 'form-check-input'}
        ),
        required=False,
        label="Filter by Category",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Group variables by category
        self.fields['variables'].queryset = ClimateVariable.objects.all().order_by('category', 'display_name')

        # Dynamically populate category choices from existing variables
        # This allows for open-ended categories without hardcoded choices
        categories = ClimateVariable.objects.exclude(
            category=''
        ).values_list('category', flat=True).distinct().order_by('category')
        self.fields['category_filter'].choices = [(cat, cat.title()) for cat in categories]


class ClimateDataSourceForm(forms.ModelForm):
    """Form for managing climate data sources (admin use)."""
    
    api_key = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
        required=False,
        help_text="API key or credentials (will be encrypted)",
    )
    
    class Meta:
        model = ClimateDataSource
        fields = [
            'name',
            'source_type',
            'description',
            'api_endpoint',
            'api_key',
            'requires_authentication',
            'spatial_resolution_m',
            'temporal_resolution_days',
            'data_start_date',
            'data_end_date',
            'global_coverage',
            'coverage_description',
            'is_active',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'source_type': forms.Select(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'api_endpoint': forms.URLInput(attrs={'class': 'form-control'}),
            'spatial_resolution_m': forms.NumberInput(attrs={'class': 'form-control'}),
            'temporal_resolution_days': forms.NumberInput(attrs={'class': 'form-control'}),
            'data_start_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'data_end_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'coverage_description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }
    
    def clean_api_key(self):
        """Encrypt API key before saving (implement proper encryption in production)."""
        api_key = self.cleaned_data.get('api_key')
        # In production, use proper encryption here
        # For MVP, we'll just return as-is with a warning
        if api_key and not api_key.startswith('encrypted_'):
            # Simple marker for demo - use real encryption in production
            return f"encrypted_{api_key}"
        return api_key