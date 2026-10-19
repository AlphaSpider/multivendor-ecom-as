"""Emirate switching and other location write operations."""

from __future__ import annotations

from django.utils.http import url_has_allowed_host_and_scheme

from core.exceptions import LocationError
from locations.models import Emirate
from locations.selectors import remember_emirate_in_session


def switch_marketplace(request, emirate: Emirate) -> None:
    """
    Remember ``emirate`` as the visitor's marketplace for this session.

    Deactivated emirates cannot be selected even if a stale form posts their slug.
    """
    if emirate is None or not emirate.is_active:
        raise LocationError("This marketplace is not currently available.")
    remember_emirate_in_session(request, emirate)


def safe_redirect_target(candidate: str | None, request, fallback: str) -> str:
    """
    Only honour ``next``/``redirect_to`` values that point back at this site.

    Prevents the switch endpoint from becoming an open redirect.
    """
    if candidate and url_has_allowed_host_and_scheme(
        url=candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return fallback
