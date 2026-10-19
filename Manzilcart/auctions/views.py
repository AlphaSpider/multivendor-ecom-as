"""
HTTP views for auctions.

Sellers create an auction from one of their published listings, bidders place bids
from the emirate-scoped detail page, and the seller explicitly locks the current
highest eligible bid. The bidder then confirms or declines through a signed,
participant-only link. Deadline processing itself lives in the management command,
not in a request/response cycle.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from auctions import services
from auctions.forms import AuctionForm, BidForm
from auctions.models import Auction, AuctionSelection
from auctions.selectors import (
    active_auctions_for_emirate,
    auction_for_emirate,
    auctions_for_user,
    ended_auctions_for_emirate,
    pending_selection_for,
    selections_visible_to,
)
from catalog.models import Listing
from core.exceptions import (
    DomainError,
    PermissionDeniedError,
    StateTransitionError,
)
from locations.mixins import EmirateRequiredMixin


def _apply_service_errors(form, exc: ValidationError) -> None:
    """Map a service-layer ``ValidationError`` onto the right form fields."""
    if hasattr(exc, "message_dict"):
        for field, errors in exc.message_dict.items():
            for error in errors:
                form.add_error(field if field in form.fields else None, error)
    else:
        form.add_error(None, " ".join(exc.messages))


def _get_auction(pk: int, emirate, user) -> Auction:
    auction = auction_for_emirate(pk, emirate)
    if auction is None:
        raise Http404("Auction not found.")
    if auction.listing.published_at is None:
        allowed = bool(
            getattr(user, "is_authenticated", False)
            and (user.pk == auction.seller_id or user.is_staff)
        )
        if not allowed:
            # Same response as a missing auction: never disclose an unpublished item.
            raise Http404("Auction not found.")
    return auction


class AuctionListView(EmirateRequiredMixin, TemplateView):
    """Emirate-scoped auction board: active first, then recent outcomes."""

    template_name = "auctions/list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        active = active_auctions_for_emirate(self.emirate)
        paginator = Paginator(active, settings.AUCTIONS_PER_PAGE)
        page = paginator.get_page(self.request.GET.get("page"))

        context.update(
            {
                "emirate": self.emirate,
                "auctions": page.object_list,
                "page_obj": page,
                "total_active": paginator.count,
                "ended_auctions": ended_auctions_for_emirate(self.emirate)[:12],
                "now": timezone.now(),
            }
        )
        return context


class AuctionDetailView(EmirateRequiredMixin, TemplateView):
    """Auction detail with public bid amounts and privacy-aware bidder labels."""

    template_name = "auctions/detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        request = self.request
        user = request.user
        auction = _get_auction(self.kwargs["pk"], self.emirate, user)

        now = timezone.now()
        is_authenticated = bool(getattr(user, "is_authenticated", False))
        is_seller = is_authenticated and user.pk == auction.seller_id
        is_staff = is_authenticated and user.is_staff

        bids = list(
            auction.bids.select_related("bidder").order_by("-amount", "created_at", "id")
        )
        highest_eligible = services.highest_eligible_bid(auction)
        top_bid = bids[0] if bids else None
        increment = Decimal(settings.AUCTION_MIN_BID_INCREMENT)
        minimum_bid = top_bid.amount + increment if top_bid else Decimal("1.00")

        viewer_bid_count = (
            auction.bids.filter(bidder=user).count() if is_authenticated else 0
        )
        can_bid = bool(
            is_authenticated
            and not is_seller
            and auction.is_open_for_bids
            and auction.listing.is_public
            and getattr(user, "is_mobile_verified", False)
            and viewer_bid_count < settings.AUCTION_MAX_BIDS_PER_USER
        )

        # Bidder identities stay private to other bidders. The seller and staff need
        # them for operations; everyone else sees stable ordinal labels.
        labels: dict[int, str] = {}
        for bid in bids:
            if bid.bidder_id not in labels:
                labels[bid.bidder_id] = f"Bidder {len(labels) + 1}"
            bid.is_viewer = is_authenticated and bid.bidder_id == user.pk
            if bid.is_viewer:
                bid.viewer_name = "You"
            elif is_seller or is_staff:
                bid.viewer_name = (
                    bid.bidder.display_name
                    or bid.bidder.short_name
                    or f"Member {bid.bidder_id}"
                )
            else:
                bid.viewer_name = labels[bid.bidder_id]

        pending = (
            auction.selections.filter(status=AuctionSelection.Status.PENDING)
            .select_related("bid", "bidder")
            .order_by("-sequence")
            .first()
        )
        viewer_selection = None
        selection_url = ""
        if is_authenticated:
            viewer_selection = (
                auction.selections.filter(bidder=user)
                .select_related("bid")
                .order_by("-sequence")
                .first()
            )
            if viewer_selection is not None:
                selection_url = reverse(
                    "auctions:selection",
                    kwargs={"token": services.selection_token(viewer_selection)},
                )

        can_lock = bool(
            is_seller
            and auction.status == Auction.Status.LIVE
            and now < auction.end_at
            and highest_eligible is not None
        )
        has_bids = bool(bids)
        can_cancel = bool(
            auction.status
            in {Auction.Status.SCHEDULED, Auction.Status.LIVE}
            and (
                (is_seller and not has_bids)
                or (is_staff and auction.status in {Auction.Status.SCHEDULED, Auction.Status.LIVE})
            )
        )

        context.update(
            {
                "emirate": self.emirate,
                "auction": auction,
                "listing": auction.listing,
                "now": now,
                "bids": bids,
                "top_bid": top_bid,
                "highest_eligible": highest_eligible,
                "minimum_bid": minimum_bid,
                "is_seller": is_seller,
                "is_staff_viewer": is_staff,
                "can_bid": can_bid,
                "viewer_bid_count": viewer_bid_count,
                "bid_limit": settings.AUCTION_MAX_BIDS_PER_USER,
                "bid_form": BidForm(),
                "pending_selection": pending,
                "viewer_selection": viewer_selection,
                "selection_url": selection_url,
                "selections": selections_visible_to(auction, user),
                "events": auction.events.select_related("actor").order_by(
                    "created_at", "id"
                ),
                "can_lock": can_lock,
                "can_cancel": can_cancel,
                "reserve_met": highest_eligible is not None,
            }
        )
        return context


class AuctionCreateView(LoginRequiredMixin, EmirateRequiredMixin, View):
    """Create an auction for one of the member's published listings."""

    template_name = "auctions/form.html"
    login_url = "accounts:login"

    def get_listing(self) -> Listing:
        listing = get_object_or_404(
            Listing.objects.select_related(
                "seller", "emirate", "service_area", "category"
            ),
            slug=self.kwargs["listing_slug"],
        )
        if listing.emirate_id != self.emirate.pk:
            raise Http404("Listing not found.")
        if listing.seller_id != self.request.user.pk and not self.request.user.is_staff:
            raise Http404("Listing not found.")
        return listing

    def get(self, request, *args, **kwargs):
        listing = self.get_listing()
        return render(
            request,
            self.template_name,
            {
                "emirate": self.emirate,
                "listing": listing,
                "form": AuctionForm(),
                "confirmation_hours": settings.AUCTION_CONFIRMATION_WINDOW_HOURS,
            },
        )

    def post(self, request, *args, **kwargs):
        listing = self.get_listing()
        form = AuctionForm(request.POST)

        if form.is_valid():
            try:
                auction = services.create_auction(
                    listing=listing,
                    seller=request.user,
                    start_at=form.cleaned_data["start_at"],
                    end_at=form.cleaned_data["end_at"],
                    reserve_price=form.cleaned_data.get("reserve_price"),
                    agree_fallback=form.cleaned_data["agree_fallback"],
                    request=request,
                )
            except ValidationError as exc:
                _apply_service_errors(form, exc)
            except PermissionDeniedError:
                raise Http404("Listing not found.")
            except StateTransitionError as exc:
                form.add_error(None, exc.message)
            else:
                messages.success(
                    request,
                    "Your auction is set up. Bidders can see it in the marketplace.",
                )
                return redirect(
                    "auction_marketplace:detail",
                    emirate_slug=self.emirate.slug,
                    pk=auction.pk,
                )

        return render(
            request,
            self.template_name,
            {
                "emirate": self.emirate,
                "listing": listing,
                "form": form,
                "confirmation_hours": settings.AUCTION_CONFIRMATION_WINDOW_HOURS,
            },
            status=400,
        )


class AuctionBidView(LoginRequiredMixin, EmirateRequiredMixin, View):
    """Place a bid on a live auction (server-side validation only)."""

    login_url = "accounts:login"

    def post(self, request, *args, **kwargs):
        auction = _get_auction(kwargs["pk"], self.emirate, request.user)
        form = BidForm(request.POST)

        if not form.is_valid():
            messages.error(request, "Enter a valid bid amount.")
        else:
            try:
                bid, created = services.place_bid(
                    auction,
                    bidder=request.user,
                    amount=form.cleaned_data["amount"],
                    client_bid_id=form.cleaned_data.get("client_bid_id"),
                    request=request,
                )
            except DomainError as exc:
                messages.error(request, exc.message)
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
            else:
                if created:
                    messages.success(
                        request, f"Your bid of AED {bid.amount} has been placed."
                    )
                else:
                    messages.info(request, "That bid was already recorded.")

        return redirect(
            "auction_marketplace:detail",
            emirate_slug=self.emirate.slug,
            pk=auction.pk,
        )


class AuctionLockView(LoginRequiredMixin, EmirateRequiredMixin, View):
    """Seller action: freeze bidding and invite the highest eligible bidder."""

    login_url = "accounts:login"

    def post(self, request, *args, **kwargs):
        auction = _get_auction(kwargs["pk"], self.emirate, request.user)
        raw_bid_id = request.POST.get("bid_id", "")

        if not raw_bid_id.isdigit():
            messages.error(request, "Choose a valid bid to lock.")
        else:
            try:
                selection = services.lock_bid(
                    auction,
                    seller=request.user,
                    bid_id=int(raw_bid_id),
                    request=request,
                )
            except DomainError as exc:
                messages.error(request, exc.message)
            else:
                messages.success(
                    request,
                    f"Bid locked at AED {selection.bid.amount}. "
                    "The bidder has been asked to confirm.",
                )

        return redirect(
            "auction_marketplace:detail",
            emirate_slug=self.emirate.slug,
            pk=auction.pk,
        )


class AuctionCancelView(LoginRequiredMixin, EmirateRequiredMixin, View):
    """Cancel a scheduled/live auction. The seller may only cancel before bids."""

    login_url = "accounts:login"

    def post(self, request, *args, **kwargs):
        auction = _get_auction(kwargs["pk"], self.emirate, request.user)
        try:
            services.cancel_auction(
                auction,
                actor=request.user,
                reason=request.POST.get("reason", ""),
                request=request,
            )
        except DomainError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, "Auction cancelled.")

        return redirect(
            "auction_marketplace:detail",
            emirate_slug=self.emirate.slug,
            pk=auction.pk,
        )


class MyAuctionsView(LoginRequiredMixin, TemplateView):
    """Auctions the member is selling in or bidding on."""

    template_name = "auctions/my_auctions.html"
    login_url = "accounts:login"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        rows = []
        for auction in auctions_for_user(user):
            pending = pending_selection_for(auction, user)
            selection_url = (
                reverse(
                    "auctions:selection",
                    kwargs={"token": services.selection_token(pending)},
                )
                if pending is not None
                else ""
            )
            rows.append(
                {
                    "auction": auction,
                    "is_seller": auction.seller_id == user.pk,
                    "pending_selection": pending,
                    "selection_url": selection_url,
                }
            )
        context["rows"] = rows
        context["now"] = timezone.now()
        return context


class AuctionSelectionView(LoginRequiredMixin, View):
    """
    Secure bidder confirmation page.

    The signed token identifies one selection. Only the invited bidder may act on it;
    staff may inspect the page but cannot answer on the bidder's behalf.
    """

    template_name = "auctions/selection.html"
    login_url = "accounts:login"

    def get_selection(self) -> AuctionSelection:
        selection = services.selection_from_token(self.kwargs["token"])
        if selection is None:
            raise Http404("Confirmation link not found.")
        user = self.request.user
        if user.pk != selection.bidder_id and not user.is_staff:
            raise Http404("Confirmation link not found.")
        return selection

    def _context(self, selection: AuctionSelection) -> dict:
        request = self.request
        auction = selection.auction
        now = timezone.now()
        is_bidder = request.user.pk == selection.bidder_id
        can_respond = bool(
            is_bidder
            and selection.status == AuctionSelection.Status.PENDING
            and now <= selection.confirmation_deadline
            and auction.status == Auction.Status.LOCKED
            and auction.selected_bid_id == selection.bid_id
        )
        return {
            "selection": selection,
            "auction": auction,
            "listing": auction.listing,
            "bid": selection.bid,
            "now": now,
            "is_bidder": is_bidder,
            "can_respond": can_respond,
            "deal": auction.awarded_deal if auction.awarded_deal_id else None,
        }

    def get(self, request, *args, **kwargs):
        selection = self.get_selection()
        return render(request, self.template_name, self._context(selection))

    def post(self, request, *args, **kwargs):
        selection = self.get_selection()
        auction = selection.auction

        if request.user.pk != selection.bidder_id:
            messages.error(request, "Only the invited bidder can answer this offer.")
            return redirect("auctions:selection", token=self.kwargs["token"])

        action = request.POST.get("action")
        try:
            if action == "confirm":
                deal = services.confirm_selection(
                    selection, actor=request.user, request=request
                )
                messages.success(
                    request,
                    f"Auction awarded at AED {deal.agreed_price}. "
                    "The seller has been told to arrange handover with you.",
                )
                return redirect("deals:detail", pk=deal.pk)

            if action == "decline":
                services.decline_selection(
                    selection,
                    actor=request.user,
                    reason=request.POST.get("reason", ""),
                    request=request,
                )
                messages.info(
                    request,
                    "You declined this offer. The auction may be offered to the next "
                    "highest bidder.",
                )
                return redirect("auctions:selection", token=self.kwargs["token"])

            messages.error(request, "Unknown action.")
        except DomainError as exc:
            messages.error(request, exc.message)

        return redirect("auctions:selection", token=self.kwargs["token"])
