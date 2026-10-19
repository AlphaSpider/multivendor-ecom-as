"""Template context shared by every page (emirate switcher, marketplace state)."""

from __future__ import annotations

from django.conf import settings


def marketplace(request):
    """
    Provide marketplace context to all templates.

    ``request.emirate`` is set by ``core.middleware.MarketplaceMiddleware``. The list
    of emirates is memoised on the request so the switcher costs at most one query.
    """
    emirate = getattr(request, "emirate", None)

    active_emirates = getattr(request, "_active_emirates_cache", None)
    if active_emirates is None:
        # Imported lazily: the context processor runs during template rendering, and
        # this keeps app loading order unconstrained.
        from locations.selectors import active_emirates

        active_emirates = list(active_emirates())
        request._active_emirates_cache = active_emirates

    return {
        "current_emirate": emirate,
        "active_emirates": active_emirates,
        "show_emirate_switcher": len(active_emirates) > 1,
        "site_name": "ManzilCart",
        "marketplace_per_page": settings.LISTINGS_PER_PAGE,
    }
