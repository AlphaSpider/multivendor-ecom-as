"""
Read paths for geographic configuration.

Views and services never query ``Emirate``/``ServiceArea`` directly, so the
"active only" rule is applied in exactly one place. That is what stops a deactivated
emirate from still being reachable through a stale link or a cached session value.
"""

from __future__ import annotations

from django.conf import settings

from locations.models import Emirate, ServiceArea, ServiceOption

# Session bookkeeping for the last emirate the visitor chose.


def active_emirates():
    """Active emirates in display order (used by the landing page and switcher)."""
    return Emirate.objects.active().for_display()


def all_emirates():
    """Every emirate, including inactive ones (admin and reporting only)."""
    return Emirate.objects.for_display()


def active_emirate_for_slug(slug: str | None) -> Emirate | None:
    """Resolve a URL slug to an *active* emirate, or ``None``."""
    if not slug:
        return None
    return Emirate.objects.active().filter(slug=slug).first()


def emirate_for_slug_including_inactive(slug: str | None) -> Emirate | None:
    if not slug:
        return None
    return Emirate.objects.filter(slug=slug).first()


def emirate_from_session(request) -> Emirate | None:
    """
    Return the emirate remembered in the session.

    A remembered emirate that has since been deactivated is ignored (and forgotten),
    so deactivation takes effect immediately even for visitors with an old session.
    """
    if request is None:
        return None

    cached = getattr(request, "_emirate_from_session", None)
    if cached is not None:
        return cached if cached != "none" else None

    slug = request.session.get(settings.EMIRATE_SESSION_KEY) if hasattr(request, "session") else None
    emirate = active_emirate_for_slug(slug) if slug else None

    if slug and emirate is None:
        # Stale preference: drop it rather than keep resolving to nothing.
        try:
            del request.session[settings.EMIRATE_SESSION_KEY]
        except (KeyError, AttributeError):  # pragma: no cover
            pass

    request._emirate_from_session = emirate if emirate is not None else "none"
    return emirate


def remember_emirate_in_session(request, emirate: Emirate) -> None:
    """Persist the visitor's marketplace choice for subsequent requests."""
    if request is None or emirate is None:
        return
    try:
        request.session[settings.EMIRATE_SESSION_KEY] = emirate.slug
    except AttributeError:  # pragma: no cover - session-less request
        return
    request._emirate_from_session = emirate


def forget_emirate_in_session(request) -> None:
    try:
        del request.session[settings.EMIRATE_SESSION_KEY]
    except (KeyError, AttributeError):  # pragma: no cover
        pass
    request._emirate_from_session = "none"


def service_areas_for_emirate(emirate: Emirate | None, *, active_only: bool = True):
    """Service areas belonging to ``emirate``."""
    if emirate is None:
        return ServiceArea.objects.none()
    queryset = ServiceArea.objects.filter(emirate=emirate)
    if active_only:
        queryset = queryset.filter(is_active=True)
    return queryset.order_by("display_order", "name")


def active_service_area_for_slug(emirate: Emirate, slug: str | None) -> ServiceArea | None:
    if not emirate or not slug:
        return None
    return (
        ServiceArea.objects.filter(emirate=emirate, slug=slug, is_active=True)
        .select_related("emirate")
        .first()
    )


def available_service_options(service_area: ServiceArea | None):
    """
    Optional services offered in ``service_area``.

    Only options with an explicit ``is_available=True`` coverage row are returned.
    """
    if service_area is None:
        return ServiceOption.objects.none()
    return (
        ServiceOption.objects.filter(
            is_active=True,
            coverage__service_area=service_area,
            coverage__is_available=True,
        )
        .distinct()
        .order_by("display_order", "name")
    )


def service_option_by_id(option_id) -> ServiceOption | None:
    try:
        return ServiceOption.objects.filter(pk=option_id, is_active=True).first()
    except (TypeError, ValueError):
        return None
