"""
Views for the emirate selection landing page, the marketplace home and switching.

URL shape (see ``Manzilcart/urls.py``)::

    /                       emirate picker
    /<emirate_slug>/        that emirate's marketplace
    /marketplace/switch/    change marketplace (POST)
    /<emirate_slug>/areas.json   active areas (used by progressive enhancement)
"""

from __future__ import annotations

from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View
from django.views.decorators.http import require_GET, require_POST

from core.exceptions import LocationError
from locations.mixins import EmirateRequiredMixin
from locations.models import Emirate
from locations.selectors import (
    active_emirate_for_slug,
    active_emirates,
    emirate_from_session,
    service_areas_for_emirate,
)
from locations.services import safe_redirect_target, switch_marketplace


class EmirateLandingView(View):
    """
    Site root: the visitor chooses a marketplace.

    Requirement: the visitor's location is never inferred from their IP address. If a
    previous choice is remembered it is *highlighted* rather than auto-applied, so the
    privacy-preserving default and a one-click "continue" both hold.
    """

    template_name = "locations/landing.html"

    def get(self, request):
        emirates = list(active_emirates())
        remembered = emirate_from_session(request)
        return render(
            request,
            self.template_name,
            {
                "emirates": emirates,
                "remembered_emirate": remembered,
                # Shown when every emirate has been deactivated.
                "no_emirates_available": not emirates,
            },
        )


class MarketplaceHomeView(EmirateRequiredMixin, View):
    """The chosen emirate's marketplace front page."""

    template_name = "locations/marketplace_home.html"

    def get(self, request, *args, **kwargs):
        from catalog.selectors import (
            active_categories,
            category_listing_counts,
            featured_listings,
            marketplace_listing_stats,
        )

        categories = list(active_categories())
        counts = category_listing_counts(self.emirate)
        for category in categories:
            category.listing_count = counts.get(category.pk, 0)

        return render(
            request,
            self.template_name,
            {
                "emirate": self.emirate,
                "service_areas": service_areas_for_emirate(self.emirate),
                "categories": categories,
                "featured_listings": featured_listings(self.emirate, limit=8),
                "listing_stats": marketplace_listing_stats(self.emirate),
            },
        )


@require_POST
def switch_marketplace_view(request):
    """
    Change the remembered marketplace and bounce back.

    The emirate arrives as a slug and is re-validated against the active set.
    """
    slug = (request.POST.get("emirate") or "").strip()
    emirate = active_emirate_for_slug(slug)

    if emirate is None:
        # Unknown or inactive: fall back to the picker with a friendly message.
        fallback = reverse("locations:landing")
        if slug:
            return redirect(f"{fallback}?invalid_emirate=1")
        return redirect(fallback)

    try:
        switch_marketplace(request, emirate)
    except LocationError:
        return redirect(reverse("locations:landing"))

    fallback = reverse("marketplace:home", kwargs={"emirate_slug": emirate.slug})
    target = safe_redirect_target(request.POST.get("next"), request, fallback)
    return redirect(target)


@require_GET
def service_area_options(request, emirate_slug: str):
    """
    Active service areas for an emirate, as JSON.

    Public by design: the area list is already visible on the registration form. It
    contains no personal data and cannot be used to write anything.
    """
    emirate = active_emirate_for_slug(emirate_slug)
    if emirate is None:
        raise Http404("Unknown marketplace.")
    areas = service_areas_for_emirate(emirate)
    return JsonResponse(
        {
            "emirate": {"name": emirate.name, "slug": emirate.slug},
            "areas": [{"name": area.name, "slug": area.slug} for area in areas],
        }
    )


def emirate_not_found(request, emirate_slug: str):
    """
    Graceful handling for an unknown or deactivated emirate URL.

    An existing-but-deactivated emirate is rendered exactly like an unknown one: both
    return 404 with the picker, and the message does not disclose whether the market
    ever existed. ``inactive_emirate`` is computed for staff-visible logging only.
    """
    context = {
        "requested_slug": emirate_slug,
        "emirates": list(active_emirates()),
    }
    return render(request, "locations/emirate_not_found.html", context, status=404)
