from django import forms
from django.forms import formset_factory
from django.core.exceptions import ValidationError
from django_ace import AceWidget

from core.models import Attribute, Study
from . import dataset_exports
from .models import (
    MappingRule,
    MappingSchema,
    RawDataFile,
    validate_safe_transform_code,
)
from .relationship_system import RELATIONSHIP_SYSTEM_CATEGORY
from .relationship_system import (
    infer_next_relation_name,
    infer_inverse_relation,
    relation_instance_choices,
    resolve_relation_instance_choice,
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
            "universal_location",
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
            target_qs = target_qs.filter(project__members=user).distinct()
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
        self.fields["universal_location"].queryset = source_attrs
        self.fields["universal_patient_id"].required = False
        self.fields["universal_datetime"].required = False
        self.fields["universal_location"].required = False
        # Patient / datetime assignment handled per-rule


class MappingRuleForm(forms.ModelForm):
    source_attribute = forms.ModelChoiceField(
        queryset=Attribute.objects.none(),
        disabled=True,
    )
    relation_instance = forms.ChoiceField(
        required=False,
        choices=[],
        label="Relation instance",
    )
    create_relation_instance = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
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
            "needs_review",
            "role",
            "patient_id_attribute",
            "datetime_attribute",
            "relation_type",
            "location_attribute",
            "target_attribute",
            "relation_instance",
            "create_relation_instance",
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
                extensions=["language_tools"],
                basicautocompletion=True,
                liveautocompletion=True,
            ),
            "comments": forms.Textarea(
                attrs={"rows": 2, "placeholder": "Optional notes about this mapping..."}
            ),
            "not_mappable": forms.CheckboxInput(
                attrs={"class": "not-mappable-checkbox"}
            ),
            "needs_review": forms.CheckboxInput(
                attrs={"class": "needs-review-checkbox"}
            ),
            "use_custom_settings": forms.CheckboxInput(
                attrs={"class": "custom-settings-checkbox"}
            ),
            "target_attribute": TargetAttributeWidget(),
        }

    def __init__(self, *args, **kwargs):
        schema: MappingSchema = kwargs.pop("schema")
        self._schema = schema
        super().__init__(*args, **kwargs)

        src_qs = schema.source_study.variables.all().order_by("variable_name")
        tgt_qs = schema.target_study.variables.all().order_by("variable_name")

        self.fields["source_attribute"].queryset = src_qs
        self.fields["target_attribute"].queryset = tgt_qs
        self.fields["target_attribute"].required = False  # Allow provisional saves
        if (
            not getattr(self.instance, "source_attribute_id", None)
            and self.is_bound
            and self.data
        ):
            posted_source = self.data.get(self.add_prefix("source_attribute"))
            if posted_source:
                self.fields["source_attribute"].initial = posted_source

        # Default role to "value"; do not require
        self.fields["role"].initial = "value"
        self.fields["role"].required = False

        # Set up patient_id and datetime attribute fields (for custom overrides)
        self.fields["patient_id_attribute"].queryset = src_qs
        self.fields["patient_id_attribute"].required = False
        self.fields["patient_id_attribute"].empty_label = "Use schema default"
        self.fields["patient_id_attribute"].help_text = (
            "Select the source field that identifies the patient for this mapping"
        )

        self.fields["datetime_attribute"].queryset = src_qs
        self.fields["datetime_attribute"].required = False
        self.fields["datetime_attribute"].empty_label = "Use schema default"
        self.fields["datetime_attribute"].help_text = (
            "Select the source field that gives the date or time for this mapping"
        )

        self.fields["location_attribute"].queryset = src_qs
        self.fields["location_attribute"].required = False
        self.fields["location_attribute"].empty_label = "Use schema default"
        self.fields["location_attribute"].help_text = (
            "Select the source field that gives the location for this mapping"
        )

        self.fields["relation_type"].required = False
        self.fields["relation_type"].help_text = "Entity that owns this mapped value"
        self.fields["relation_instance"].choices = relation_instance_choices(schema)
        self.fields["relation_instance"].help_text = "Choose an existing relation instance, or create the next one with New."
        if self.instance and self.instance.relation_type and self.instance.relation_name:
            self.fields["relation_instance"].initial = (
                f"existing:{self.instance.relation_type}:{self.instance.relation_name}"
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

            if schema.universal_relation_type and not getattr(self.instance, "relation_type", None):
                self.fields["relation_type"].initial = schema.universal_relation_type

            if (
                schema.universal_location
                and not getattr(self.instance, "location_attribute_id", None)
            ):
                self.fields["location_attribute"].initial = (
                    schema.universal_location
                )

        # Help text for better UX
        self.fields["not_mappable"].help_text = (
            "Mark this variable as intentionally not mapped to any target variable"
        )
        self.fields["needs_review"].help_text = (
            "Flag this mapping for human review before approval"
        )
        self.fields["use_custom_settings"].help_text = (
            "Select patient, date/time, or location fields that differ from the schema defaults."
        )
        self.fields["use_custom_settings"].initial = any(
            (
                getattr(self.instance, "patient_id_attribute_id", None)
                and self.instance.patient_id_attribute_id != schema.universal_patient_id_id,
                getattr(self.instance, "datetime_attribute_id", None)
                and self.instance.datetime_attribute_id != schema.universal_datetime_id,
                getattr(self.instance, "location_attribute_id", None)
                and self.instance.location_attribute_id != schema.universal_location_id,
            )
        )
        self.fields["role"].help_text = (
            "Value: Standard mapping to target variable | "
            "Patient ID: Use as patient identifier | "
            "Date/Time: Use as timestamp | "
            "Location: Use as location name"
        )
        self.fields["target_attribute"].help_text = (
            "Select the target variable this source variable maps to"
        )

    def clean(self):
        cleaned = super().clean()
        # If variable marked as not mappable, do not enforce role/target
        if cleaned.get("not_mappable"):
            cleaned["role"] = cleaned.get("role") or "value"
            cleaned["needs_review"] = False
            cleaned["relation_type"] = "self"
            cleaned["relation_instance"] = ""
            cleaned["create_relation_instance"] = ""
            self._sync_relation_metadata_for_model_validation(cleaned)
            # target_attribute can remain empty
            return cleaned
        relation_type = cleaned.get("relation_type") or "self"
        cleaned["relation_type"] = relation_type
        if relation_type == "self":
            cleaned["relation_instance"] = ""
            cleaned["create_relation_instance"] = ""
        else:
            schema = self.instance.schema if self.instance and self.instance.schema_id else self._schema
            create_relation_type = cleaned.get("create_relation_instance") or ""
            if create_relation_type:
                if create_relation_type != relation_type:
                    self.add_error(
                        "relation_instance",
                        "Create intent does not match the selected relation type.",
                    )
                else:
                    next_relation_name = infer_next_relation_name(
                        schema=schema,
                        relation_type=relation_type,
                        exclude_rule_id=self.instance.pk if self.instance else None,
                    )
                    if (
                        self.instance
                        and self.instance.pk
                        and self.instance.relation_type == relation_type
                        and self.instance.relation_name == next_relation_name
                    ):
                        self.add_error(
                            "relation_instance",
                            "This mapping already uses the next relation instance. Use it in another mapping before creating the next one.",
                        )
                    else:
                        cleaned["relation_name"] = next_relation_name
            else:
                try:
                    cleaned["relation_name"] = resolve_relation_instance_choice(
                        schema=schema,
                        relation_type=relation_type,
                        choice=cleaned.get("relation_instance") or "",
                    )
                except ValidationError as exc:
                    self.add_error("relation_instance", exc)
        self._sync_relation_metadata_for_model_validation(cleaned)
        return cleaned

    def _sync_relation_metadata_for_model_validation(self, cleaned):
        relation_type = cleaned.get("relation_type") or "self"
        self.instance.relation_type = relation_type
        if relation_type == "self":
            self.instance.relation_name = ""
            self.instance.inverse_relation_type = ""
            self.instance.inverse_relation_name = ""
            return

        relation_name = cleaned.get("relation_name")
        if not relation_name:
            relation_name = self.instance.relation_name or "invalid_relation_instance"
        self.instance.relation_name = relation_name
        self.instance.inverse_relation_type, self.instance.inverse_relation_name = infer_inverse_relation(relation_type)

    def save(self, commit=True):
        instance = super().save(commit=False)
        relation_type = self.cleaned_data.get("relation_type") or "self"
        instance.relation_type = relation_type
        if relation_type == "self":
            instance.relation_name = ""
            instance.inverse_relation_type = ""
            instance.inverse_relation_name = ""
        else:
            instance.relation_name = self.cleaned_data.get("relation_name") or instance.relation_name
            instance.inverse_relation_type, instance.inverse_relation_name = infer_inverse_relation(relation_type)
        if commit:
            instance.save()
            self.save_m2m()
        return instance

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
    location_column = forms.ChoiceField(
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'}),
        help_text="Select the variable from your codebook that contains location names"
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
        qs = Study.objects.filter(study_purpose='source')
        if user:
             qs = qs.filter(project__members=user).distinct()
        self.fields['study'].queryset = qs.order_by('name')
        
        # Set up patient ID, date, and location column choices based on selected study
        self.fields['patient_id_column'].choices = [('', 'Select participant ID variable...')]
        self.fields['date_column'].choices = [('', 'Select date/time variable...')]
        self.fields['location_column'].choices = [('', 'Select location variable (optional)...')]
        
        # Update field labels
        self.fields['patient_id_column'].label = "Participant ID Variable"
        self.fields['date_column'].label = "Date/Time Variable"
        self.fields['location_column'].label = "Location Variable"
        
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
            if self.instance.location_column:
                self.initial['location_column'] = self.instance.location_column
        elif self.initial.get('study'):
            # If an initial study is provided (e.g., preselected in the view) and no form data posted yet
            try:
                study = Study.objects.get(pk=self.initial['study'])
                self._update_column_choices(study)
                self._prefill_location_column(study)
            except (ValueError, TypeError, Study.DoesNotExist):
                pass

    def _prefill_location_column(self, study):
        """Best-effort prefill for location when there is a clear single candidate.

        This is intentionally conservative: we only auto-select when exactly one
        study variable looks like a location name field. Users can change it anytime.
        """
        if self.initial.get('location_column'):
            return

        candidates = study.variables.filter(variable_type__in=['string', 'categorical'])
        preferred_names = {
            'location', 'site', 'facility', 'clinic', 'hospital', 'center',
            'centre', 'city', 'country', 'region', 'state', 'province'
        }

        matching = [
            var.variable_name
            for var in candidates
            if var.variable_name and var.variable_name.strip().lower() in preferred_names
        ]

        if len(matching) == 1:
            self.initial['location_column'] = matching[0]
    
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

        # Locations can generally be string/categorical
        location_variables = study.variables.filter(
            variable_type__in=['string', 'categorical']
        )
        location_choices = [('', 'Select location variable (optional)...')]
        location_choices.extend([
            (var.variable_name, f"{var.display_name or var.variable_name} ({var.variable_name})")
            for var in location_variables
        ])
        self.fields['location_column'].choices = location_choices
    
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
        location_column = cleaned_data.get('location_column')
        
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

            if location_column and location_column not in study_variable_names:
                raise ValidationError({
                    'location_column': 'Selected location variable is not part of this study\'s codebook.'
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
        instance.location_column = self.cleaned_data.get('location_column', '')
        
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


class CombinedExportForm(forms.Form):
    """Select studies, variables, and format for combined (harmonised) export."""

    CATEGORY_CHOICES = (
        ("health", "Health"),
        ("geolocation", "Geolocation"),
        ("climate", "Climate"),
    )

    FORMAT_CHOICES = (
        ("long", "Long format"),
        ("wide", "Wide format"),
    )

    FILE_FORMAT_CHOICES = (
        ("csv", "CSV"),
        ("parquet", "Parquet"),
    )

    max_lag_days = forms.IntegerField(
        required=False,
        min_value=0,
        initial=0,
        label="Max lag",
        help_text=(
            "Maximum lag to include for climate variables (inclusive of 0). "
            "Units follow the climate request (days/weeks/months/years); re-request climate data with a larger lag if you need more history."
        ),
    )

    target_study = forms.ModelChoiceField(
        queryset=Study.objects.none(),
        required=True,
        label="Target study",
        help_text="Transformed (harmonised) study to export from.",
    )

    source_studies = forms.ModelMultipleChoiceField(
        queryset=Study.objects.none(),
        required=False,
        label="Source studies",
        help_text=(
            "Filter to observations produced from these source studies. "
            "Only source studies mapped into the selected target appear here."
        ),
    )

    categories = forms.MultipleChoiceField(
        choices=CATEGORY_CHOICES,
        required=True,
        initial=[c for c, _ in CATEGORY_CHOICES],
        widget=forms.CheckboxSelectMultiple,
        label="Variable categories",
    )

    attributes = forms.ModelMultipleChoiceField(
        queryset=Attribute.objects.none(),
        required=False,
        label="Specific variables (optional)",
        help_text=(
            "Leave empty to include all variables in the selected categories. "
            "Health exports include eligible observed health variables only; identifier, date/time, and location-style health fields are excluded."
        ),
        widget=forms.SelectMultiple(attrs={"size": 12}),
    )

    export_format = forms.ChoiceField(
        choices=FORMAT_CHOICES,
        initial="long",
        required=True,
        label="Export format",
        widget=forms.RadioSelect,
    )

    file_format = forms.ChoiceField(
        choices=FILE_FORMAT_CHOICES,
        initial="csv",
        required=True,
        label="Download file type",
        widget=forms.RadioSelect,
    )

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        base_target_qs = Study.objects.filter(study_purpose="target")
        if user and user.is_authenticated:
             if user.is_staff or user.is_superuser:
                 pass
             else:
                 base_target_qs = base_target_qs.filter(project__members=user).distinct()
        self.fields["target_study"].queryset = base_target_qs.order_by("name")

        target_study = self.initial.get("target_study") or self.data.get("target_study")
        if target_study:
            try:
                target_obj = base_target_qs.filter(pk=target_study).first()
            except Exception:
                target_obj = None
        else:
            target_obj = None

        source_qs = Study.objects.filter(study_purpose="source")
        if user and user.is_authenticated:
             if user.is_staff or user.is_superuser:
                 pass
             else:
                source_qs = source_qs.filter(project__members=user).distinct()
        if target_obj:
            source_qs = source_qs.filter(source_mappings__target_study=target_obj).distinct()
        self.fields["source_studies"].queryset = source_qs.order_by("name")

        if target_obj:
            observed_attributes = dataset_exports.observed_target_attribute_queryset(target_obj)
            eligible_health = dataset_exports.eligible_health_queryset(observed_attributes)
            visible_attribute_ids = list(eligible_health.values_list("pk", flat=True)) + list(
                observed_attributes.exclude(category__in=["health", RELATIONSHIP_SYSTEM_CATEGORY]).values_list("pk", flat=True),
            )
            self.fields["attributes"].queryset = (
                observed_attributes.filter(pk__in=visible_attribute_ids)
                .order_by("category", "display_name", "variable_name")
            )
        else:
            self.fields["attributes"].queryset = Attribute.objects.none()

    def clean_categories(self):
        categories = self.cleaned_data.get("categories") or []
        if not categories:
            raise forms.ValidationError("Select at least one category to export.")
        return categories

    def clean(self):
        cleaned = super().clean()
        target = cleaned.get("target_study")
        source_studies = cleaned.get("source_studies")
        attrs = cleaned.get("attributes")

        if attrs and target:
            # Ensure selected attributes belong to the target study
            invalid = attrs.exclude(studies=target)
            if invalid.exists():
                raise forms.ValidationError(
                    "All selected variables must belong to the target study."
                )

        # Ensure selected source studies are actually mapped to the target
        if source_studies and target:
            allowed = Study.objects.filter(
                pk__in=source_studies.values_list("pk", flat=True),
                source_mappings__target_study=target,
            )
            if allowed.count() != source_studies.count():
                raise forms.ValidationError(
                    "All chosen source studies must be mapped into the target study."
                )

        return cleaned
