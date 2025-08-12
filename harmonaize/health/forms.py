from django import forms
from django.forms import formset_factory

from core.models import Attribute, Study
from .models import MappingRule, MappingSchema, validate_safe_transform_code


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

    class Meta:
        model = MappingRule
        fields = [
            "source_attribute",
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
                attrs={"rows": 2, "placeholder": "lambda value: value"},
            ),
            "comments": forms.Textarea(attrs={"rows": 1}),
        }

    def __init__(self, *args, **kwargs):
        schema: MappingSchema = kwargs.pop("schema")
        super().__init__(*args, **kwargs)
        src_qs = schema.source_study.variables.all().order_by("variable_name")
        self.fields["source_attribute"].queryset = src_qs
        tgt_qs = schema.target_study.variables.all().order_by("variable_name")
        self.fields["target_attribute"].queryset = tgt_qs
        self.fields["target_attribute"].required = False  # Allow provisional saves
        
        # Set up patient_id and datetime attribute fields
        self.fields["patient_id_attribute"].queryset = src_qs
        self.fields["patient_id_attribute"].required = False
        self.fields["patient_id_attribute"].empty_label = "Select patient ID..."
        
        self.fields["datetime_attribute"].queryset = src_qs
        self.fields["datetime_attribute"].required = False
        self.fields["datetime_attribute"].empty_label = "Select datetime..."
        
        # Pre-populate from universal settings if enabled
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
        self.fields["related_relation_type"].required = False

    def clean(self):
        cleaned = super().clean()
        return cleaned

    def clean_transform_code(self):
        code = self.cleaned_data.get("transform_code", "") or ""
        if code.strip():
            validate_safe_transform_code(code)
        return code


MappingRuleFormSet = formset_factory(MappingRuleForm, extra=0)
