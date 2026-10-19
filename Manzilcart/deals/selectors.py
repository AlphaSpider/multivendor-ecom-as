"""Read queries for deals."""

from __future__ import annotations

from django.db.models import Q

from deals.models import Deal


def deals_for(user):
    """Every deal the user is buying or selling, newest first."""
    return (
        Deal.objects.filter(Q(buyer=user) | Q(seller=user))
        .select_related(
            "listing",
            "listing__category",
            "listing__service_area",
            "listing__emirate",
            "buyer",
            "seller",
            "emirate",
            "accepted_offer",
        )
        .order_by("-created_at", "-id")
    )


def deal_for_participant(pk: int, user, *, include_staff: bool = False) -> Deal | None:
    """
    Fetch a deal only when ``user`` is a participant (or staff, when requested).

    ``None`` is returned for missing and unauthorised deals alike, so a 404 never
    confirms that someone else's deal exists.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    queryset = Deal.objects.filter(pk=pk)
    if not (include_staff and getattr(user, "is_staff", False)):
        queryset = queryset.filter(Q(buyer=user) | Q(seller=user))
    return (
        queryset.select_related(
            "listing",
            "listing__category",
            "listing__service_area",
            "listing__emirate",
            "listing__seller",
            "buyer",
            "seller",
            "emirate",
            "accepted_offer",
        )
        .prefetch_related(
            "offers__sender",
            "events__actor",
            "cancellations__requested_by",
            "cancellations__responded_by",
            "service_selections__service_option",
        )
        .first()
    )
