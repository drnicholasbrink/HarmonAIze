# geolocation/forms.py
from django import forms
from django.core.validators import MinValueValidator, MaxValueValidator
from .models import ValidationResult, GeocodingResult


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
