from django.db.models import Q

from core.models import Location, Observation

# Locations are resolved through ``IN (SELECT location_id ...)`` semi-joins rather
# than OR-ed joins across observations + DISTINCT: studies can hold hundreds of
# thousands of observations, and the join form makes Postgres scan all of them.


def _observed_location_ids(**observation_filters):
    return Observation.objects.filter(
        location__isnull=False, **observation_filters
    ).values("location_id")


def viewable_locations_for_user(user):
    """Locations visible through studies in projects the user belongs to,
    plus locations the user uploaded directly (e.g. via the location CSV upload)."""
    if not getattr(user, "is_authenticated", False):
        return Location.objects.none()

    return Location.objects.filter(
        Q(pk__in=_observed_location_ids(attribute__study__project__members=user))
        | Q(study__project__in=user.joined_projects.values("pk"))
        | Q(created_by=user)
    )


def scoped_locations(user, scope):
    """Viewable locations narrowed to the active project / study scope.

    A location belongs to a study either through its observations (normal
    project workflow) or because it was uploaded into that study as a
    location list (geolocation-only workflow).
    """
    locations = viewable_locations_for_user(user)
    if scope.study is not None:
        return locations.filter(
            Q(pk__in=_observed_location_ids(attribute__study=scope.study)) | Q(study=scope.study)
        )
    if scope.project is not None:
        return locations.filter(
            Q(pk__in=_observed_location_ids(attribute__study__project=scope.project))
            | Q(study__project=scope.project)
        )
    return locations


def scoped_geocoding_results(user, scope):
    """The user's geocoding results for locations in scope.

    Geocoding results are cached per user by location name, so a result can be
    linked to a same-named location in another project; match on name as well.
    """
    from .models import GeocodingResult

    results = GeocodingResult.objects.filter(created_by=user)
    if scope.is_all:
        return results
    locations = scoped_locations(user, scope)
    # Resolve the (small) set of result ids once, so callers can chain many cheap counts.
    result_ids = list(
        results.filter(
            Q(location__in=locations.values("pk")) | Q(location_name__in=locations.values("name"))
        ).values_list("pk", flat=True)
    )
    return results.filter(pk__in=result_ids)


def scoped_validations(user, scope):
    """The user's validation results for locations in scope."""
    from .models import ValidationResult

    validations = ValidationResult.objects.filter(created_by=user)
    if scope.is_all:
        return validations
    return validations.filter(geocoding_result__in=scoped_geocoding_results(user, scope).values("pk"))
