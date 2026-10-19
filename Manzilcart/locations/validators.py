"""Validators for geographic configuration."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError

_ALPHANUMERIC_SLUG_CHARS = set(
    "abcdefghijklmnopqrstuvwxyz0123456789-_"
)


def validate_slug_allowed(value: str) -> None:
    """
    Reject slugs that would collide with reserved application routes.

    An emirate or area slug becomes a URL segment, so an emirate called ``admin`` or
    ``accounts`` would shadow core routes. This check makes that impossible.
    """
    if not value:
        return
    reserved = {item.lower() for item in settings.EMIRATE_RESERVED_SLUGS}
    reserved |= {"emirate", "emirates", "location", "locations", "service-area", "service-areas"}
    if value.lower() in reserved:
        raise ValidationError(
            f"'{value}' is reserved by the application and cannot be used as a slug."
        )


def validate_slug_is_lowercase(value: str) -> None:
    if value and value != value.lower():
        raise ValidationError("Slugs must be lowercase.")


def validate_slug_charset(value: str) -> None:
    """Reject anything that is not URL-safe slug material."""
    if not value:
        return
    invalid = sorted({char for char in value if char not in _ALPHANUMERIC_SLUG_CHARS})
    if invalid:
        raise ValidationError(
            "Slugs may only contain letters, numbers, hyphens and underscores."
        )
