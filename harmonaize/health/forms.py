from django import forms
from django.forms import formset_factory

from core.models import Attribute, Study
from .models import MappingRule, MappingSchema, validate_safe_transform_code


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
        widget=forms.CheckboxInput(attrs={'class': 'custom-settings-toggle'}),
        label="Use custom settings",
        help_text="Check to override universal settings for this specific mapping"
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
            "transform_code": forms.Textarea(
                attrs={
                    "rows": 3,
                }
            ),
            "comments": forms.Textarea(attrs={"rows": 2, "placeholder": "Optional notes about this mapping..."}),
            "not_mappable": forms.CheckboxInput(attrs={"class": "not-mappable-checkbox"}),
            "use_custom_settings": forms.CheckboxInput(attrs={"class": "custom-settings-checkbox"}),
            "target_attribute": TargetAttributeWidget(),
        }

    def __init__(self, *args, **kwargs):
        schema: MappingSchema = kwargs.pop("schema")
        super().__init__(*args, **kwargs)
        src_qs = schema.source_study.variables.all().order_by("variable_name")
        self.fields["source_attribute"].queryset = src_qs
        tgt_qs = schema.target_study.variables.all().order_by("variable_name")
        self.fields["target_attribute"].queryset = tgt_qs
        self.fields["target_attribute"].required = False  # Allow provisional saves
        
        # Default role to "value" for standard mapping to target variable
        self.fields["role"].initial = "value"
        
        # Set up patient_id and datetime attribute fields (for custom overrides)
        self.fields["patient_id_attribute"].queryset = src_qs
        self.fields["patient_id_attribute"].required = False
        self.fields["patient_id_attribute"].empty_label = "Use universal setting (recommended)"
        self.fields["patient_id_attribute"].help_text = "Override universal patient ID setting for this specific mapping"
        
        self.fields["datetime_attribute"].queryset = src_qs
        self.fields["datetime_attribute"].required = False
        self.fields["datetime_attribute"].empty_label = "Use universal setting (recommended)"
        self.fields["datetime_attribute"].help_text = "Override universal datetime setting for this specific mapping"
        
        # Relation type for related patient mappings
        self.fields["related_relation_type"].required = False
        self.fields["related_relation_type"].empty_label = "Use universal setting (recommended)"
        self.fields["related_relation_type"].help_text = "Only needed when role is not 'Value'"
        
        # Pre-populate from universal settings if enabled and no custom value set
        if schema.auto_populate_enabled:
            # For patient_id field
            if (schema.universal_patient_id and
                not getattr(self.instance, "patient_id_attribute_id", None)):
                self.fields["patient_id_attribute"].initial = (
                    schema.universal_patient_id)

            # For datetime field
            if (schema.universal_datetime and
                not getattr(self.instance, "datetime_attribute_id", None)):
                self.fields["datetime_attribute"].initial = (
                    schema.universal_datetime)

            # For relation type
            if (schema.universal_relation_type and
                not getattr(self.instance, "related_relation_type", None)):
                self.fields["related_relation_type"].initial = (
                    schema.universal_relation_type)

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
        return super().clean()

    def clean_transform_code(self):
        code = self.cleaned_data.get("transform_code", "") or ""
        if code.strip():
            validate_safe_transform_code(code)
        return code


MappingRuleFormSet = formset_factory(MappingRuleForm, extra=0)
