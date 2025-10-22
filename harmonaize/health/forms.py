from django import forms
from django.forms import formset_factory
from django.core.exceptions import ValidationError
from django_ace import AceWidget

from core.models import Attribute, Study
from .models import (
    MappingRule,
    MappingSchema,
    RawDataFile,
    validate_safe_transform_code,
)


class TargetAttributeWidget(forms.Select):
    """Custom widget for target attribute selection with metadata in data attributes."""
    
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        
        if value:
            # Try to get the attribute from the database to add metadata
            try:
                attr = Attribute.objects.get(pk=value)
                if not option.get('attrs'):
                    option['attrs'] = {}
                option['attrs'].update({
                    'data-display-name': attr.display_name or '',
                    'data-unit': attr.unit or '',
                    'data-variable-type': attr.variable_type or '',
                    'data-description': attr.description or '',
                    'data-ontology-code': attr.ontology_code or '',
                })
            except (Attribute.DoesNotExist, ValueError, TypeError):
                pass
        
        return option


class MappingSchemaForm(forms.ModelForm):
    class Meta:
        model = MappingSchema
        fields = [
            "target_study",
            "universal_patient_id",
            "universal_datetime",
            "universal_relation_type",
            "comments",
        ]

    def __init__(self, *args, **kwargs):
        source_study: Study = kwargs.pop("source_study")
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
    # Pre-set source_study so model.clean() can safely access it.
    # Avoid RelatedObjectDoesNotExist during validation.
        if not self.instance.pk:  # creation
            self.instance.source_study = source_study
        target_qs = Study.objects.filter(study_purpose="target")
        if user is not None:
            target_qs = target_qs.filter(created_by=user)
        if source_study.project_id:
            target_qs = target_qs.filter(project_id=source_study.project_id)
        self.fields["target_study"].queryset = target_qs.order_by("name")
        
        # For existing instances, allow partial updates without requiring target_study
        if self.instance and self.instance.pk:
            self.fields["target_study"].required = False
        
        # Set up querysets for universal settings fields
        source_attrs = source_study.variables.all().order_by("variable_name")
        self.fields["universal_patient_id"].queryset = source_attrs
        self.fields["universal_datetime"].queryset = source_attrs
        self.fields["universal_patient_id"].required = False
        self.fields["universal_datetime"].required = False
        # Patient / datetime assignment handled per-rule


class MappingRuleForm(forms.ModelForm):
    source_attribute = forms.ModelChoiceField(
        queryset=Attribute.objects.none(),
        disabled=True,
    )

    # Form-only field for toggling custom settings visibility
    use_custom_settings = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "custom-settings-toggle"}),
        label="Use custom settings",
        help_text=(
            "Check to override universal settings for this specific mapping"
        ),
    )

    class Meta:
        model = MappingRule
        fields = [
            "source_attribute",
            "not_mappable",
            "role",
            "patient_id_attribute",
            "datetime_attribute",
            "related_relation_type",
            "target_attribute",
            "transform_code",
            "comments",
        ]
        widgets = {
            "transform_code": AceWidget(
                mode="python",
                theme="github",
                width="100%",
                height="150px",
                showprintmargin=True,
                showinvisibles=False,
                usesofttabs=True,
                tabsize=4,
                fontsize="14px",
                toolbar=True,
                wordwrap=False,
                readonly=False,
                showgutter=True,  # To hide/show line numbers
                behaviours=True,  # To disable auto-append of quote when quotes are entered
                useworker=True,
                extensions=None,
                basicautocompletion=False,
                liveautocompletion=False,
            ),
            "comments": forms.Textarea(
                attrs={"rows": 2, "placeholder": "Optional notes about this mapping..."}
            ),
            "not_mappable": forms.CheckboxInput(
                attrs={"class": "not-mappable-checkbox"}
            ),
            "use_custom_settings": forms.CheckboxInput(
                attrs={"class": "custom-settings-checkbox"}
            ),
            "target_attribute": TargetAttributeWidget(),
        }

    def __init__(self, *args, **kwargs):
        schema: MappingSchema = kwargs.pop("schema")
        super().__init__(*args, **kwargs)

        src_qs = schema.source_study.variables.all().order_by("variable_name")
        tgt_qs = schema.target_study.variables.all().order_by("variable_name")

        self.fields["source_attribute"].queryset = src_qs
        self.fields["target_attribute"].queryset = tgt_qs
        self.fields["target_attribute"].required = False  # Allow provisional saves

        # Default role to "value"; do not require
        self.fields["role"].initial = "value"
        self.fields["role"].required = False

        # Set up patient_id and datetime attribute fields (for custom overrides)
        self.fields["patient_id_attribute"].queryset = src_qs
        self.fields["patient_id_attribute"].required = False
        self.fields["patient_id_attribute"].empty_label = (
            "Use universal setting (recommended)"
        )
        self.fields["patient_id_attribute"].help_text = (
            "Override universal patient ID setting for this specific mapping"
        )

        self.fields["datetime_attribute"].queryset = src_qs
        self.fields["datetime_attribute"].required = False
        self.fields["datetime_attribute"].empty_label = (
            "Use universal setting (recommended)"
        )
        self.fields["datetime_attribute"].help_text = (
            "Override universal datetime setting for this specific mapping"
        )

        # Relation type for related patient mappings
        self.fields["related_relation_type"].required = False
        self.fields["related_relation_type"].empty_label = (
            "Use universal setting (recommended)"
        )
        self.fields["related_relation_type"].help_text = (
            "Only needed when role is not 'Value'"
        )

        # Pre-populate from universal settings if enabled and no custom value set
        if schema.auto_populate_enabled:
            if (
                schema.universal_patient_id
                and not getattr(self.instance, "patient_id_attribute_id", None)
            ):
                self.fields["patient_id_attribute"].initial = (
                    schema.universal_patient_id
                )

            if (
                schema.universal_datetime
                and not getattr(self.instance, "datetime_attribute_id", None)
            ):
                self.fields["datetime_attribute"].initial = (
                    schema.universal_datetime
                )

            if (
                schema.universal_relation_type
                and not getattr(self.instance, "related_relation_type", None)
            ):
                self.fields["related_relation_type"].initial = (
                    schema.universal_relation_type
                )

        # Help text for better UX
        self.fields["not_mappable"].help_text = (
            "Check if this variable cannot be mapped to any target variable"
        )
        self.fields["use_custom_settings"].help_text = (
            "Override universal settings with custom patient ID/datetime for this mapping"
        )
        self.fields["role"].help_text = (
            "Value: Standard mapping to target variable | "
            "Patient ID: Use as patient identifier | "
            "Date/Time: Use as timestamp | "
            "Related Patient ID: For family/related person data"
        )
        self.fields["target_attribute"].help_text = (
            "Select the target variable this source variable maps to"
        )

    def clean(self):
        cleaned = super().clean()
        # If variable marked as not mappable, do not enforce role/target
        if cleaned.get("not_mappable"):
            cleaned["role"] = cleaned.get("role") or "value"
            # target_attribute can remain empty
            return cleaned
        return cleaned

    def clean_transform_code(self):
        code = self.cleaned_data.get("transform_code", "") or ""
        if code.strip():
            validate_safe_transform_code(code)
        return code


MappingRuleFormSet = formset_factory(MappingRuleForm, extra=0)


class RawDataUploadForm(forms.ModelForm):
    """
    Form for uploading raw data files.
    Includes participant ID and date column selectors based on study variables.
    """
    
    # Override fields to use ChoiceField instead of ModelChoiceField
    patient_id_column = forms.ChoiceField(
        required=True,
        widget=forms.Select(attrs={'class': 'form-control'}),
        help_text="Select the variable from your codebook that contains participant identifiers"
    )
    date_column = forms.ChoiceField(
        required=True,
        widget=forms.Select(attrs={'class': 'form-control'}),
        help_text="Select the variable from your codebook that contains dates or timestamps"
    )
    
    class Meta:
        model = RawDataFile
        fields = ['study', 'file']  # Remove patient_id_column and date_column from Meta
        widgets = {
            'study': forms.Select(attrs={
                'class': 'form-control',
                'help_text': 'Select the study this data belongs to'
            }),
            'file': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': '.csv,.xlsx,.xls,.json,.txt'
            }),
        }
    
    def __init__(self, *args, **kwargs):
        # Allow filtering studies by user or other criteria
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        
        # Only show source studies (ones that can have raw data)
        self.fields['study'].queryset = Study.objects.filter(
            study_purpose='source'
        ).order_by('name')
        
        # Set up patient ID and date column choices based on selected study
        self.fields['patient_id_column'].choices = [('', 'Select participant ID variable...')]
        self.fields['date_column'].choices = [('', 'Select date/time variable...')]
        
        # Update field labels
        self.fields['patient_id_column'].label = "Participant ID Variable"
        self.fields['date_column'].label = "Date/Time Variable"
        
        # If study is already selected (e.g., from initial data or GET parameter)
        if 'study' in self.data:
            try:
                study_id = int(self.data.get('study'))
                study = Study.objects.get(pk=study_id)
                self._update_column_choices(study)
            except (ValueError, TypeError, Study.DoesNotExist):
                pass
        elif self.instance and self.instance.pk and self.instance.study:
            # If we're editing an existing instance
            study = self.instance.study
            self._update_column_choices(study)
            
            # Set initial values for existing instance
            if self.instance.patient_id_column:
                self.initial['patient_id_column'] = self.instance.patient_id_column
            if self.instance.date_column:
                self.initial['date_column'] = self.instance.date_column
    
    def _update_column_choices(self, study):
        """Update the column choice fields based on the selected study."""
        # Filter to only show string/categorical variables for patient ID
        patient_id_variables = study.variables.filter(
            variable_type__in=['string', 'categorical']
        )
        patient_id_choices = [('', 'Select participant ID variable...')]
        patient_id_choices.extend([
            (var.variable_name, f"{var.display_name or var.variable_name} ({var.variable_name})")
            for var in patient_id_variables
        ])
        self.fields['patient_id_column'].choices = patient_id_choices
        
        # Filter to show datetime or string variables for date column  
        date_variables = study.variables.filter(
            variable_type__in=['datetime', 'string']
        )
        date_choices = [('', 'Select date/time variable...')]
        date_choices.extend([
            (var.variable_name, f"{var.display_name or var.variable_name} ({var.variable_name})")
            for var in date_variables
        ])
        self.fields['date_column'].choices = date_choices
    
    def clean_file(self):
        file = self.cleaned_data.get('file')
        if not file:
            return file
        
        # Basic file validation
        max_size = 50 * 1024 * 1024  # 50MB
        if file.size > max_size:
            raise ValidationError(f"File size ({file.size / 1024 / 1024:.1f}MB) exceeds maximum allowed size (50MB).")
        
        # Check file extension
        allowed_extensions = ['.csv', '.xlsx', '.xls', '.json', '.txt']
        file_ext = '.' + file.name.split('.')[-1].lower()
        if file_ext not in allowed_extensions:
            raise ValidationError(f"File type '{file_ext}' not supported. Allowed types: {', '.join(allowed_extensions)}")
        
        return file
    
    def clean(self):
        cleaned_data = super().clean()
        study = cleaned_data.get('study')
        file = cleaned_data.get('file')
        patient_id_column = cleaned_data.get('patient_id_column')
        date_column = cleaned_data.get('date_column')
        
        if study and file:
            # Validate that the selected columns are actually part of the study
            study_variable_names = list(study.variables.values_list('variable_name', flat=True))
            
            if patient_id_column and patient_id_column not in study_variable_names:
                raise ValidationError({
                    'patient_id_column': 'Selected participant ID variable is not part of this study\'s codebook.'
                })
            
            if date_column and date_column not in study_variable_names:
                raise ValidationError({
                    'date_column': 'Selected date variable is not part of this study\'s codebook.'
                })
            
            # Validate file content against codebook
            try:
                from health.utils import validate_raw_data_against_codebook
                validation_result = validate_raw_data_against_codebook(file, study)
                if not validation_result['is_valid']:
                    raise ValidationError(f"Data validation failed: {validation_result['message']}")
            except Exception as e:
                # Don't block upload if validation utility fails, but log the issue
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Raw data validation failed for file {file.name}: {str(e)}")
        
        return cleaned_data
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        
        # Set the original filename
        if instance.file:
            instance.original_filename = instance.file.name
        
        # Set patient_id_column and date_column from the form data
        instance.patient_id_column = self.cleaned_data.get('patient_id_column', '')
        instance.date_column = self.cleaned_data.get('date_column', '')
        
        if commit:
            instance.save()
        return instance


class ColumnMappingForm(forms.Form):
    """
    Form for mapping columns after file upload.
    This will be used in a future step to configure patient ID and date columns.
    """
    patient_id_column = forms.ChoiceField(
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'}),
        help_text="Select the column containing patient identifiers"
    )
    date_column = forms.ChoiceField(
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'}),
        help_text="Select the column containing dates/timestamps"
    )
    
    def __init__(self, *args, **kwargs):
        columns = kwargs.pop('columns', [])
        super().__init__(*args, **kwargs)
        
        # Create choices from column names
        column_choices = [('', '-- Select Column --')] + [(col, col) for col in columns]
        self.fields['patient_id_column'].choices = column_choices
        self.fields['date_column'].choices = column_choices


class ExportDataForm(forms.Form):
    """Collect the desired export format for a RawDataFile."""

    EXPORT_TYPE_CHOICES = (
        ("original", "Original Uploaded File"),
        ("harmonised", "Harmonised Long-format CSV"),
    )

    export_type = forms.ChoiceField(
        choices=EXPORT_TYPE_CHOICES,
        widget=forms.RadioSelect,
        initial="original",
        label="Select export type",
        help_text=(
            "Choose whether to download the raw upload or the harmonised long-format data."
        ),
    )

    def __init__(self, *args, **kwargs):
        self.harmonised_available = kwargs.pop("harmonised_available", False)
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        selection = cleaned.get("export_type")
        if selection == "harmonised" and not self.harmonised_available:
            self.add_error(
                "export_type",
                "Harmonised export is not available for this file yet.",
            )
        return cleaned
