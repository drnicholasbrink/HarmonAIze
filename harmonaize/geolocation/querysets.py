from core.models import Location


def viewable_locations_for_user(user):
    """Locations visible through studies in projects the user belongs to."""
    if not getattr(user, "is_authenticated", False):
        return Location.objects.none()

    return Location.objects.filter(
        observations__attribute__study__project__members=user
    ).distinct()
