"""
Validation of geographic relationships.

Called by every write path (registration, listing create/update, deal acceptance)
because the browser's ``emirate``/``service_area`` values are never trusted. The
same invariant is *also* enforced by a composite foreign key in PostgreSQL
(``core.db.geo``); these functions exist to produce a helpful message instead of an
``IntegrityError``, and to apply the "must still be active" business rule, which the
database constraint intentionally does not encode.
"""

from __future__ import annotations

from core.exceptions import LocationError
from locations.models import Emirate, ServiceArea


def validate_emirate_service_area(
    emirate: Emirate | None,
    service_area: ServiceArea | None,
    *,
    require_active: bool = True,
) -> tuple[Emirate, ServiceArea]:
    """
    Validate that ``service_area`` is a real, offered area of ``emirate``.

    Returns:
        The validated pair, for convenient assignment.

    Raises:
        LocationError: when the pair is missing, mismatched or inactive.
    """
    if emirate is None:
        raise LocationError("Please choose an emirate.")
    if service_area is None:
        raise LocationError("Please choose your area.")

    if require_active and not emirate.is_active:
        raise LocationError("This marketplace is not currently available.")

    if service_area.emirate_id != emirate.pk:
        # Deliberately vague: do not confirm that the area exists in another emirate.
        raise LocationError("That area is not available in the selected emirate.")

    if require_active and not service_area.is_active:
        raise LocationError("That area is not currently served. Please choose another area.")

    return emirate, service_area


def validate_registration_location(
    emirate_slug: str | None, service_area_slug: str | None
) -> tuple[Emirate, ServiceArea]:
    """
    Resolve and validate a registration's location from untrusted input.

    Both values arrive from the browser, so the relationship is re-checked against
    the database rather than trusted.
    """
    from locations.selectors import active_emirate_for_slug, active_service_area_for_slug

    emirate = active_emirate_for_slug(emirate_slug)
    if emirate is None:
        raise LocationError("This marketplace is not currently available.")

    service_area = active_service_area_for_slug(emirate, service_area_slug)
    return validate_emirate_service_area(emirate, service_area)
