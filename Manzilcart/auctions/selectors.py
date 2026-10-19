"""Read queries for auction pages and dashboards."""

from __future__ import annotations

from django.db.models import Max, Q

from auctions.models import Auction, AuctionSelection

ACTIVE_STATUSES = (
    Auction.Status.SCHEDULED,
    Auction.Status.LIVE,
    Auction.Status.LOCKED,
)
ENDED_STATUSES = (
    Auction.Status.AWARDED,
    Auction.Status.UNSOLD,
    Auction.Status.CANCELLED,
)


def _auction_queryset():
    return (
        Auction.objects.select_related(
            "listing",
            "listing__category",
            "listing__service_area",
            "listing__emirate",
            "listing__seller",
            "seller",
            "emirate",
            "selected_bid",
            "selected_bidder",
            "awarded_deal",
        )
        .prefetch_related("listing__images")
        .annotate(top_bid=Max("bids__amount"))
    )


def active_auctions_for_emirate(emirate):
    """Auctions a visitor can still watch or bid on, soonest deadline first."""
    return (
        _auction_queryset()
        .filter(
            emirate=emirate,
            status__in=ACTIVE_STATUSES,
            listing__published_at__isnull=False,
        )
        .order_by("end_at", "id")
    )


def ended_auctions_for_emirate(emirate):
    """Recently finished auctions, newest outcome first."""
    return (
        _auction_queryset()
        .filter(
            emirate=emirate,
            status__in=ENDED_STATUSES,
            listing__published_at__isnull=False,
        )
        .order_by("-end_at", "-id")
    )


def auction_for_emirate(pk: int, emirate) -> Auction | None:
    return (
        _auction_queryset()
        .filter(pk=pk, emirate=emirate)
        .first()
    )


def auctions_for_user(user):
    """Auctions the member is selling in, bidding on or has been offered."""
    return (
        _auction_queryset()
        .filter(
            Q(seller=user)
            | Q(bids__bidder=user)
            | Q(selections__bidder=user)
            | Q(selected_bidder=user)
        )
        .distinct()
        .order_by("-created_at", "-id")
    )


def active_auction_for_listing(listing) -> Auction | None:
    return (
        Auction.objects.filter(listing=listing, status__in=ACTIVE_STATUSES)
        .order_by("-created_at", "-id")
        .first()
    )


def pending_selection_for(auction: Auction, user) -> AuctionSelection | None:
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return (
        auction.selections.filter(
            bidder=user, status=AuctionSelection.Status.PENDING
        )
        .select_related("bid")
        .first()
    )


def selections_visible_to(auction: Auction, user):
    """
    Fallback history for the seller and staff; bidders see only their own rounds.

    Public visitors get no selection rows at all: who was offered the auction is
    operational information, even though the amounts themselves are public.
    """
    queryset = auction.selections.select_related("bidder", "bid").order_by(
        "sequence", "id"
    )
    if user is not None and getattr(user, "is_authenticated", False):
        if user.pk == auction.seller_id or getattr(user, "is_staff", False):
            return queryset
        return queryset.filter(bidder=user)
    return queryset.none()
