"""Helpers for storing user-uploaded files under unguessable paths."""

import os
import uuid

from django.utils import timezone
from django.utils.deconstruct import deconstructible


@deconstructible
class RandomizedUploadPath:
    """``upload_to`` callable producing ``<prefix>/%Y/%m/%d/<uuid4>/<filename>``.

    The random directory makes stored paths unguessable while keeping the
    original filename as the last path component, so code that derives a
    display or download name from ``os.path.basename(file.name)`` still works.
    """

    def __init__(self, prefix):
        self.prefix = prefix.strip("/")

    def __call__(self, instance, filename):
        name = os.path.basename(filename) or "upload"
        date_part = timezone.now().strftime("%Y/%m/%d")
        return f"{self.prefix}/{date_part}/{uuid.uuid4().hex}/{name}"

    def __eq__(self, other):
        return isinstance(other, RandomizedUploadPath) and self.prefix == other.prefix

    def __hash__(self):
        return hash(self.prefix)
