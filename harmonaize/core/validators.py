"""Shared validators for uploaded files."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.template.defaultfilters import filesizeformat


def validate_upload_size(uploaded_file):
    """Reject uploads larger than ``settings.MAX_UPLOAD_FILE_SIZE``."""
    limit = getattr(settings, "MAX_UPLOAD_FILE_SIZE", None)
    if limit and uploaded_file.size > limit:
        raise ValidationError(
            f"File is too large ({filesizeformat(uploaded_file.size)}). "
            f"The maximum upload size is {filesizeformat(limit)}.",
        )
