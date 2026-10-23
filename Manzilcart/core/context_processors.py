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

    # Unread chat badge for the navigation. Memoised per request like the emirates
    # so shared renders cost at most one extra indexed count.
    user = getattr(request, "user", None)
    unread_messages = getattr(request, "_unread_messages_cache", None)
    if unread_messages is None:
        if getattr(user, "is_authenticated", False):
            from conversations.selectors import unread_total_for

            unread_messages = unread_total_for(user)
        else:
            # Anonymous visitors get the login page when they tap Messages anyway.
            unread_messages = 0
        request._unread_messages_cache = unread_messages

    return {
        "current_emirate": emirate,
        "active_emirates": active_emirates,
        "show_emirate_switcher": len(active_emirates) > 1,
        "unread_messages": unread_messages,
        "site_name": "ManzilCart",
        "marketplace_per_page": settings.LISTINGS_PER_PAGE,
    }
