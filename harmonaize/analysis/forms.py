from django import forms
from django.db.models import Exists
from django.db.models import OuterRef
from django.db.models import Q

from core.models import Attribute
from core.models import Observation
from core.models import Project
from core.models import Study
from health import dataset_exports

from .models import AnalysisDatasetTemplate
from .models import AnalysisScheduleConfiguration


class AnalysisScheduleForm(forms.ModelForm):
    class Meta:
        model = AnalysisScheduleConfiguration
        fields = ["enabled", "interval_minutes"]
        widgets = {
            "enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "interval_minutes": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": 5,
                    "max": 10080,
                    "step": 5,
                }
            ),
        }

    def clean_interval_minutes(self):
        value = self.cleaned_data["interval_minutes"]
        if value < 5:
            raise forms.ValidationError("Interval must be at least 5 minutes.")
        if value > 10080:
            raise forms.ValidationError("Interval must be 7 days or less.")
        return value


class AnalysisSyncForm(forms.Form):
    project = forms.ModelChoiceField(
        queryset=Project.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
        empty_label="Select project",
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is None:
            return

        if user.is_superuser:
            queryset = Project.objects.all().order_by("name")
        else:
            queryset = (
                Project.objects.filter(
                    memberships__user=user,
                    memberships__role__in=["owner", "manager"],
                )
                .distinct()
                .order_by("name")
            )

        self.fields["project"].queryset = queryset


class AnalysisDatasetTemplateForm(forms.ModelForm):
    template_id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    RESERVED_HEALTH_ATTRIBUTE_TERMS = (
        "patient_id",
        "patient identifier",
        "participant_id",
        "participant identifier",
        "subject_id",
        "subject identifier",
        "person_id",
        "person identifier",
        "date",
        "datetime",
        "timestamp",
        "time",
        "location",
        "geolocation",
        "latitude",
        "longitude",
    )

    class Meta:
        model = AnalysisDatasetTemplate
        fields = [
            "project",
            "target_study",
            "source_studies",
            "dataset_shape",
            "deidentify",
            "max_lag_value",
            "outcome_attributes",
            "confounder_attributes",
            "climate_attributes",
            "location_attributes",
            "is_active",
            "notes",
        ]
        widgets = {
            "project": forms.Select(attrs={"class": "form-select analysis-refresh-control"}),
            "target_study": forms.Select(attrs={"class": "form-select analysis-refresh-control"}),
            "source_studies": forms.SelectMultiple(attrs={"class": "form-select analysis-refresh-control", "size": 5}),
            "dataset_shape": forms.RadioSelect,
            "deidentify": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "max_lag_value": forms.NumberInput(attrs={"class": "form-control analysis-refresh-control", "min": 0}),
            "outcome_attributes": forms.SelectMultiple(attrs={"class": "form-select", "size": 8}),
            "confounder_attributes": forms.SelectMultiple(attrs={"class": "form-select", "size": 8}),
            "climate_attributes": forms.SelectMultiple(attrs={"class": "form-select", "size": 8}),
            "location_attributes": forms.SelectMultiple(attrs={"class": "form-select", "size": 8}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["template_id"].initial = getattr(self.instance, "pk", None)
        self.duplicate_template = None
        self.duplicate_is_exact = False

        if user is not None and not user.is_superuser:
            project_queryset = (
                Project.objects.filter(
                    memberships__user=user,
                    memberships__role__in=["owner", "manager"],
                )
                .distinct()
                .order_by("name")
            )
        else:
            project_queryset = Project.objects.all().order_by("name")

        self.fields["project"].queryset = project_queryset

        project_value = self.data.get("project") or self.initial.get("project") or getattr(self.instance, "project_id", None)
        target_study_value = self.data.get("target_study") or self.initial.get("target_study") or getattr(self.instance, "target_study_id", None)

        target_queryset = Study.objects.filter(study_purpose="target")
        source_queryset = Study.objects.filter(study_purpose="source")
        attribute_queryset = Attribute.objects.none()

        if user is not None and not user.is_superuser:
            target_queryset = target_queryset.filter(project__in=project_queryset)
            source_queryset = source_queryset.filter(project__in=project_queryset)

        if project_value:
            target_queryset = target_queryset.filter(project_id=project_value)
            source_queryset = source_queryset.filter(project_id=project_value)

        if target_study_value:
            source_queryset = source_queryset.filter(
                source_mappings__target_study_id=target_study_value,
            ).distinct()

        self.fields["target_study"].queryset = target_queryset.order_by("name")
        self.fields["source_studies"].queryset = source_queryset.order_by("name")

        if target_study_value:
            observed_subquery = Observation.objects.filter(attribute_id=OuterRef("pk"))
            attribute_queryset = (
                Attribute.objects.filter(
                    studies__pk=target_study_value,
                    source_type="target",
                )
                .annotate(has_observations=Exists(observed_subquery))
                .filter(has_observations=True)
                .distinct()
            )

        eligible_health_queryset = self._eligible_health_queryset(attribute_queryset)
        selected_outcome_ids = self._data_getlist("outcome_attributes")
        if not selected_outcome_ids and self.instance.pk:
            selected_outcome_ids = [str(value) for value in self.instance.outcome_attributes.values_list("pk", flat=True)]

        self.fields["outcome_attributes"].queryset = eligible_health_queryset.order_by("display_name", "variable_name")
        self.fields["confounder_attributes"].queryset = eligible_health_queryset.exclude(
            pk__in=selected_outcome_ids,
        ).order_by("display_name", "variable_name")
        self.fields["climate_attributes"].queryset = attribute_queryset.filter(category="climate").order_by("display_name", "variable_name")
        self.fields["location_attributes"].queryset = attribute_queryset.filter(category="geolocation").order_by("display_name", "variable_name")

        self.fields["outcome_attributes"].help_text = (
            "Choose one primary outcome. Any observed health attribute can be used except identifier, date/time, and location-style fields."
        )
        self.fields["confounder_attributes"].help_text = "Patient-linked only. Confounders are not linked to time or location in exported datasets."

    def _data_getlist(self, key: str) -> list[str]:
        if hasattr(self.data, "getlist"):
            return self.data.getlist(key)
        value = self.data.get(key, []) if self.data else []
        if value in (None, ""):
            return []
        if isinstance(value, (list, tuple, set)):
            return [str(item) for item in value]
        return [str(value)]

    @classmethod
    def _reserved_health_attribute_query(cls) -> Q:
        query = Q(variable_type="datetime")
        for term in cls.RESERVED_HEALTH_ATTRIBUTE_TERMS:
            query |= Q(variable_name__icontains=term)
            query |= Q(display_name__icontains=term)
        return query

    def _eligible_health_queryset(self, attribute_queryset):
        return attribute_queryset.filter(category="health").exclude(self._reserved_health_attribute_query())

    @property
    def auto_name(self) -> str:
        target_study = self.cleaned_data.get("target_study") if hasattr(self, "cleaned_data") else None
        outcome_attributes = self.cleaned_data.get("outcome_attributes") if hasattr(self, "cleaned_data") else None
        dataset_shape = self.cleaned_data.get("dataset_shape") if hasattr(self, "cleaned_data") else None
        max_lag_value = self.cleaned_data.get("max_lag_value") if hasattr(self, "cleaned_data") else None

        def _safe_lag_value(raw_value) -> int:
            try:
                return int(raw_value or 0)
            except (TypeError, ValueError):
                return 0

        if target_study is None:
            target_study_id = self.data.get("target_study") or getattr(self.instance, "target_study_id", None)
            if target_study_id:
                target_study = self.fields["target_study"].queryset.filter(pk=target_study_id).first()

        if outcome_attributes is None:
            outcome_ids = self._data_getlist("outcome_attributes")
            outcome_attributes = self.fields["outcome_attributes"].queryset.filter(pk__in=outcome_ids)

        if target_study is None:
            return getattr(self.instance, "name", "target-study_lag0_long") or "target-study_lag0_long"

        naming = dataset_exports.build_dataset_naming(
            target_study=target_study,
            outcome_attributes=outcome_attributes or [],
            lag_value=max_lag_value if max_lag_value is not None else _safe_lag_value(self.data.get("max_lag_value") or getattr(self.instance, "max_lag_value", 0)),
            lag_unit=dataset_exports.get_climate_lag_unit_for_study(target_study),
            dataset_shape=dataset_shape or self.data.get("dataset_shape") or getattr(self.instance, "dataset_shape", "long"),
        )
        return naming.structured_slug

    def _selection_ids(self, queryset) -> set[int]:
        if not queryset:
            return set()
        return set(queryset.values_list("pk", flat=True))

    def _cleaned_signature(self, cleaned) -> dict:
        return {
            "dataset_shape": cleaned.get("dataset_shape"),
            "deidentify": bool(cleaned.get("deidentify")),
            "max_lag_value": cleaned.get("max_lag_value") or 0,
            "source_studies": self._selection_ids(cleaned.get("source_studies")),
            "outcomes": self._selection_ids(cleaned.get("outcome_attributes")),
            "confounders": self._selection_ids(cleaned.get("confounder_attributes")),
            "climate": self._selection_ids(cleaned.get("climate_attributes")),
            "locations": self._selection_ids(cleaned.get("location_attributes")),
        }

    def _instance_signature(self, instance: AnalysisDatasetTemplate) -> dict:
        return {
            "dataset_shape": instance.dataset_shape,
            "deidentify": bool(instance.deidentify),
            "max_lag_value": instance.max_lag_value or 0,
            "source_studies": set(instance.source_studies.values_list("pk", flat=True)),
            "outcomes": set(instance.outcome_attributes.values_list("pk", flat=True)),
            "confounders": set(instance.confounder_attributes.values_list("pk", flat=True)),
            "climate": set(instance.climate_attributes.values_list("pk", flat=True)),
            "locations": set(instance.location_attributes.values_list("pk", flat=True)),
        }

    def attach_duplicate_error(
        self,
        duplicate_template: AnalysisDatasetTemplate,
        *,
        exact_match: bool,
    ) -> None:
        self.duplicate_template = duplicate_template
        self.duplicate_is_exact = exact_match
        if exact_match:
            self.add_error(
                None,
                (
                    "An identical dataset template already exists for this project and target study. "
                    "Open the existing template to review or update its context instead of creating another one."
                ),
            )
        else:
            self.add_error(
                None,
                (
                    "This generated template slug already exists for the selected project and target study. "
                    "Open the existing template and update its context, or change the outcomes, lag, or shape to generate a different slug."
                ),
            )

    def clean(self):
        cleaned = super().clean()
        cleaned["name"] = self.auto_name
        project = cleaned.get("project")
        target_study = cleaned.get("target_study")
        source_studies = cleaned.get("source_studies")
        outcomes = cleaned.get("outcome_attributes")
        confounders = cleaned.get("confounder_attributes")
        climate_attributes = cleaned.get("climate_attributes")
        location_attributes = cleaned.get("location_attributes")

        if target_study and project and target_study.project_id != project.id:
            self.add_error("target_study", "Target study must belong to the selected project.")

        if source_studies and target_study:
            allowed_sources = Study.objects.filter(
                pk__in=source_studies.values_list("pk", flat=True),
                source_mappings__target_study=target_study,
            ).distinct()
            if allowed_sources.count() != source_studies.count():
                self.add_error("source_studies", "All selected source studies must be mapped into the target study.")

        if not outcomes:
            self.add_error("outcome_attributes", "Select one primary outcome attribute.")
        elif outcomes.count() != 1:
            self.add_error("outcome_attributes", "Select exactly one primary outcome attribute.")

        if outcomes and confounders:
            overlap = set(outcomes.values_list("pk", flat=True)).intersection(
                confounders.values_list("pk", flat=True),
            )
            if overlap:
                self.add_error("confounder_attributes", "Confounders must be different from the selected primary outcome.")

        for field_name, queryset, expected_category in [
            ("outcome_attributes", outcomes, "health"),
            ("confounder_attributes", confounders, "health"),
            ("climate_attributes", climate_attributes, "climate"),
            ("location_attributes", location_attributes, "geolocation"),
        ]:
            if queryset and queryset.exclude(category=expected_category).exists():
                self.add_error(field_name, f"All selected attributes must be {expected_category} attributes.")

        if project and target_study and cleaned["name"] and not self.errors:
            duplicate_queryset = AnalysisDatasetTemplate.objects.filter(
                project=project,
                target_study=target_study,
                name=cleaned["name"],
            )
            if self.instance.pk:
                duplicate_queryset = duplicate_queryset.exclude(pk=self.instance.pk)

            duplicate_template = duplicate_queryset.first()
            if duplicate_template:
                exact_match = self._instance_signature(duplicate_template) == self._cleaned_signature(cleaned)
                self.attach_duplicate_error(
                    duplicate_template,
                    exact_match=exact_match,
                )

        return cleaned
