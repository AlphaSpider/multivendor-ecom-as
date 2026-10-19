"""
Marketplace (emirate) resolution middleware.

Responsibilities
----------------
1. Work out which emirate the request belongs to:
   * path prefix (``/abu-dhabi/listings/``) wins, so shared links always open the
     right marketplace;
   * otherwise the session preference is used, so personal pages (``/my/``) keep the
     marketplace context in the header.
2. Expose it as ``request.emirate`` and in the request-scoped contextvar.
3. Remember the selection in the session when it came from the URL.
4. Never trust the browser: an unknown or inactive slug simply yields no emirate and
   the view decides how to respond (404 or marketplace picker).

This middleware is deliberately *not* an authorisation mechanism. Emirate scoping is
a marketplace filter; ownership/participant checks are enforced separately.
"""

from __future__ import annotations

import logging

from django.conf import settings

from core.emirate_context import reset_current_emirate, set_current_emirate

logger = logging.getLogger("manzilcart.core.middleware")


def _first_path_segment(path: str) -> str:
    stripped = path.strip("/")
    if not stripped:
        return ""
    return stripped.split("/", 1)[0]


class MarketplaceMiddleware:
    """Attach the current emirate to every request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        emirate = self._resolve_emirate(request)
        request.emirate = emirate
        # Memo for the context processor so we never query the emirate list twice.
        request._active_emirates_cache = None
        token = set_current_emirate(emirate)
        try:
            response = self.get_response(request)
        finally:
            reset_current_emirate(token)
        return response

    # -- internals ---------------------------------------------------------
    def _resolve_emirate(self, request):
        from locations.selectors import (
            active_emirate_for_slug,
            emirate_from_session,
            remember_emirate_in_session,
        )

        slug = _first_path_segment(request.path_info)
        reserved = set(settings.EMIRATE_RESERVED_SLUGS)

        if slug and slug not in reserved:
            emirate = active_emirate_for_slug(slug)
            if emirate is not None:
                if emirate_from_session(request) != emirate:
                    remember_emirate_in_session(request, emirate)
                return emirate
            # Unknown or inactive slug: leave emirate unset so the view can 404.

        return emirate_from_session(request)
