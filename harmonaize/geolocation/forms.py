# geolocation/forms.py
from django import forms
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError
from .models import ValidationResult, LocationCSVUpload


class ManualCoordinateForm(forms.Form):
    """Form for manually entering or correcting coordinates."""

    latitude = forms.FloatField(
        validators=[MinValueValidator(-90.0), MaxValueValidator(90.0)],
        help_text="Latitude in decimal degrees (-90 to 90)",
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.000001',
            'placeholder': 'e.g., -17.8252'
        })
    )

    longitude = forms.FloatField(
        validators=[MinValueValidator(-180.0), MaxValueValidator(180.0)],
        help_text="Longitude in decimal degrees (-180 to 180)",
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.000001',
            'placeholder': 'e.g., 31.0335'
        })
    )

    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 3,
            'placeholder': 'Optional: Add notes about this manual entry'
        }),
        help_text="Optional notes about the manual coordinate entry"
    )

    def clean(self):
        cleaned_data = super().clean()
        lat = cleaned_data.get('latitude')
        lng = cleaned_data.get('longitude')

        if lat is not None and not (-90.0 <= lat <= 90.0):
            raise forms.ValidationError('Latitude must be between -90 and 90.')

        if lng is not None and not (-180.0 <= lng <= 180.0):
            raise forms.ValidationError('Longitude must be between -180 and 180.')

        return cleaned_data


class ValidationReviewForm(forms.ModelForm):
    """Form for reviewing and updating validation results."""

    class Meta:
        model = ValidationResult
        fields = ['manual_review_notes', 'validation_status']
        widgets = {
            'manual_review_notes': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 4,
                'placeholder': 'Add your review notes here...'
            }),
            'validation_status': forms.Select(attrs={
                'class': 'form-select'
            })
        }


class LocationSelectionForm(forms.Form):
    """Form for selecting locations to geocode."""

    location_ids = forms.MultipleChoiceField(
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'form-check-input'
        }),
        required=True,
        help_text="Select locations to geocode"
    )

    def __init__(self, *args, **kwargs):
        locations = kwargs.pop('locations', [])
        super().__init__(*args, **kwargs)

        # Build choices from locations
        choices = [(loc.id, f"{loc.name} ({loc.id})") for loc in locations]
        self.fields['location_ids'].choices = choices


NEW_CHOICE = "new"


class LocationCSVUploadForm(forms.ModelForm):
    """
    Form for uploading location CSV files (geolocation-only workflow).
    Follows the pattern from health/forms.py RawDataUploadForm.

    Each upload lands in a project as a location list (a source study), either
    an existing one or a new "geolocation only" project / list created here.
    """

    project_choice = forms.ChoiceField(
        label="Project",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    new_project_name = forms.CharField(
        label="New project name",
        max_length=200,
        required=False,
        initial="Geolocation only",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    study_choice = forms.CharField(
        label="Location list",
        required=False,
        initial=NEW_CHOICE,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    location_list_name = forms.CharField(
        label="Location list name",
        max_length=200,
        required=False,
        help_text="Defaults to the file name.",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Clinic sites 2026"}),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from core.models import Project

        self.user = user
        self.projects = (
            Project.objects.filter(members=user).distinct().order_by("-updated_at")
            if user is not None else Project.objects.none()
        )
        self.fields["project_choice"].choices = [(NEW_CHOICE, "+ New geolocation-only project")] + [
            (str(p.pk), p.name) for p in self.projects
        ]
        if not self.initial.get("project_choice"):
            self.initial["project_choice"] = NEW_CHOICE

    def clean(self):
        cleaned = super().clean()
        project_choice = cleaned.get("project_choice")
        study_choice = cleaned.get("study_choice") or NEW_CHOICE

        project = None
        if project_choice == NEW_CHOICE:
            if not (cleaned.get("new_project_name") or "").strip():
                self.add_error("new_project_name", "Name the new project.")
        elif project_choice:
            project = self.projects.filter(pk=project_choice).first()
            if project is None:
                self.add_error("project_choice", "Choose a project you are a member of.")

        study = None
        if study_choice != NEW_CHOICE:
            if project is None:
                self.add_error("study_choice", "A new project needs a new location list.")
            else:
                study = project.studies.filter(pk=study_choice, study_purpose="source").first()
                if study is None:
                    self.add_error("study_choice", "Choose a source study in this project.")

        cleaned["project"] = project
        cleaned["study"] = study
        return cleaned

    class Meta:
        model = LocationCSVUpload
        fields = ['file']
        widgets = {
            'file': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': '.csv,.xlsx,.xls,.json',
            }),
        }

    def clean_file(self):
        """Validate uploaded file."""
        file = self.cleaned_data.get('file')
        if not file:
            return file

        # File size validation (100MB limit)
        max_size = 100 * 1024 * 1024  # 100MB
        if file.size > max_size:
            raise ValidationError(
                f"File size exceeds 100MB limit. Your file is {file.size / (1024*1024):.1f}MB."
            )

        # File format validation
        file_ext = file.name.split('.')[-1].lower()
        if file_ext not in ['csv', 'xlsx', 'xls', 'json']:
            raise ValidationError(
                f"Unsupported file format: .{file_ext}. "
                "Only CSV, Excel (.xlsx, .xls), and JSON formats are supported."
            )

        return file
