"""Small shared utilities."""

from __future__ import annotations

import secrets

from django.db import models
from django.utils.text import slugify


def unique_slug(
    model: type[models.Model],
    value: str,
    *,
    slug_field: str = "slug",
    max_length: int | None = None,
    fallback: str = "item",
    max_attempts: int = 50,
) -> str:
    """
    Build a URL slug that is unique for ``model``.

    Deterministic suffixes (``-2``, ``-3`` ...) keep URLs readable; after a number of
    collisions we append random characters so a hostile actor cannot exhaust the
    namespace or infer how many similar listings exist.

    The caller must still handle the rare race between computing and saving the slug;
    the database unique constraint is the final authority.
    """
    field = model._meta.get_field(slug_field)
    limit = max_length or field.max_length or 50

    base = slugify(value or "", allow_unicode=False) or fallback
    base = base[:limit]
    candidate = base
    if not model._default_manager.filter(**{slug_field: candidate}).exists():
        return candidate

    for index in range(2, max_attempts):
        suffix = f"-{index}"
        candidate = f"{base[: limit - len(suffix)]}{suffix}"
        if not model._default_manager.filter(**{slug_field: candidate}).exists():
            return candidate

    # Deterministic suffixes exhausted: fall back to random characters.
    while True:
        suffix = f"-{secrets.token_hex(4)}"
        candidate = f"{base[: limit - len(suffix)]}{suffix}"
        if not model._default_manager.filter(**{slug_field: candidate}).exists():
            return candidate
