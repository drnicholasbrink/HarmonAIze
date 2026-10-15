from django.db.models import Q

from core.models import Location


def viewable_locations_for_user(user):
    """Locations visible through studies in projects the user belongs to,
    plus locations the user uploaded directly (e.g. via the location CSV upload)."""
    if not getattr(user, "is_authenticated", False):
        return Location.objects.none()

    return Location.objects.filter(
        Q(observations__attribute__study__project__members=user) | Q(created_by=user)
    ).distinct()
