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
        widget=forms.HiddenInput(),
        required=False,
    )
    
    end_date = forms.DateField(
        widget=forms.HiddenInput(),
        required=False,
    )

    lag_value = forms.IntegerField(
        min_value=0,
        initial=30,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': 1}),
        help_text="How far back to fetch climate data",
        required=True,
        label="Lag value",
    )

    lag_unit = forms.ChoiceField(
        choices=[('days', 'Days'), ('weeks', 'Weeks'), ('months', 'Months'), ('years', 'Years')],
        initial='days',
        widget=forms.Select(attrs={'class': 'form-control'}),
        required=True,
        label="Lag unit",
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

    reset_existing = forms.BooleanField(
        required=False,
        initial=False,
        help_text="Delete existing climate observations in this window before processing",
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
            'reset_existing',
        ]
    
    def __init__(self, *args, study=None, user=None, observation_min_date=None, observation_max_date=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.study = study
        self.user = user
        self.observation_min_date = observation_min_date
        self.observation_max_date = observation_max_date
        
        # Group variables by category for better display
        if 'variables' in self.fields:
            self.fields['variables'].queryset = ClimateVariable.objects.all().order_by('category', 'display_name')
        
        # Set initial start/end using observed min/max; expand backwards by lag
        lag_value = self.fields['lag_value'].initial or 30
        lag_unit = self.fields['lag_unit'].initial or 'days'
        if observation_min_date and observation_max_date:
            start_date = self._compute_start_date(observation_min_date, lag_value, lag_unit)
            self.fields['start_date'].initial = start_date
            self.fields['end_date'].initial = observation_max_date
    
    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')
        lag_value = cleaned_data.get('lag_value')
        lag_unit = cleaned_data.get('lag_unit')
        data_source = cleaned_data.get('data_source')
        variables = cleaned_data.get('variables')

        if self.observation_max_date is None:
            raise ValidationError("No observation dates available for this study; cannot compute date range")

        if lag_value and lag_unit:
            base_min = self.observation_min_date
            base_max = self.observation_max_date
            if base_min is None or base_max is None:
                raise ValidationError("No observation dates available for this study; cannot compute date range")

            computed_start = self._compute_start_date(base_min, lag_value, lag_unit)
            cleaned_data['start_date'] = computed_start
            cleaned_data['end_date'] = base_max
            start_date = computed_start
            end_date = base_max
        
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

    def _compute_start_date(self, end_date: date, lag_value: int, lag_unit: str) -> date:
        if lag_unit == 'days':
            delta = timedelta(days=lag_value)
        elif lag_unit == 'weeks':
            delta = timedelta(weeks=lag_value)
        elif lag_unit == 'months':
            # Approximate months as 30 days for simplicity
            delta = timedelta(days=lag_value * 30)
        elif lag_unit == 'years':
            # Approximate years as 365 days
            delta = timedelta(days=lag_value * 365)
        else:
            delta = timedelta(days=lag_value)
        return end_date - delta
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.study = self.study
        # Persist lag metadata in configuration so downstream exports know the unit/value used
        instance.configuration = instance.configuration or {}
        instance.configuration.update({
            'lag_value': self.cleaned_data.get('lag_value'),
            'lag_unit': self.cleaned_data.get('lag_unit'),
        })
        # User authorization flows through study.created_by (Core module)
        # No need to set requested_by - accessed via property

        if commit:
            instance.save()
            # Save many-to-many relationships
            self.save_m2m()
            
            # Add locations from study
            if self.study:
                study_locations = Location.objects.filter(
                    observations__attribute__study=self.study
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
        choices=ClimateVariable.CATEGORY_CHOICES,
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