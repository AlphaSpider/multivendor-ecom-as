"""
HTTP views for standalone auctions.

Members request an auction through an application; staff review it, create the
auction and manage its lifecycle. Bidders place bids from the emirate-scoped detail
page. Operations lock the highest eligible bid from the admin bid monitor, then the
bidder confirms or declines through a signed, participant-only link. Deadline
processing itself lives in the management command, not in a request/response cycle.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from accounts.eligibility import SellerEligibleMixin
from auctions import services
from auctions.forms import AuctionApplicationForm, AuctionForm, BidForm
from auctions.models import Auction, AuctionApplication, AuctionSelection
from auctions.selectors import (
    active_auctions_for_emirate,
    auction_for_emirate,
    auctions_for_user,
    ended_auctions_for_emirate,
    pending_selection_for,
    selections_visible_to,
)
from core.exceptions import DomainError
from core.models import AuditEvent
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
    approved = auction.moderation_status == Auction.ModerationStatus.APPROVED
    allowed = bool(
        getattr(user, "is_authenticated", False)
        and (user.pk == auction.seller_id or user.is_staff)
    )
    if not approved and not allowed:
        # Same response as a missing auction: never disclose a draft or rejected item.
        raise Http404("Auction not found.")
    return auction


def _get_application(pk: int, user) -> AuctionApplication:
    application = get_object_or_404(
        AuctionApplication.objects.select_related(
            "applicant", "emirate", "service_area", "category", "auction"
        ).prefetch_related("images"),
        pk=pk,
    )
    if application.applicant_id != user.pk and not user.is_staff:
        raise Http404("Application not found.")
    return application


def _validate_uploads(uploaded_files) -> list[str]:
    from core.images import validate_image_upload

    errors: list[str] = []
    for index, uploaded in enumerate(uploaded_files, start=1):
        try:
            validate_image_upload(uploaded)
        except ValidationError as exc:
            errors.append(f"Photo {index}: {' '.join(exc.messages)}")
    return errors


class AuctionListView(EmirateRequiredMixin, TemplateView):
    """Paginated board: approved live/scheduled auctions, then recent outcomes."""

    template_name = "auctions/list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        active = active_auctions_for_emirate(self.emirate)
        paginator = Paginator(active, settings.AUCTIONS_PER_PAGE)
        page = paginator.get_page(self.request.GET.get("page"))

        create_url = reverse(
            "auction_marketplace:apply",
            kwargs={"emirate_slug": self.emirate.slug},
        )

        context.update(
            {
                "emirate": self.emirate,
                "auctions": page.object_list,
                "page_obj": page,
                "total_active": paginator.count,
                "ended_auctions": ended_auctions_for_emirate(self.emirate)[:12],
                "now": timezone.now(),
                "create_url": create_url,
            }
        )
        return context


class AuctionDetailView(EmirateRequiredMixin, TemplateView):
    """Standalone auction detail with public bid amounts and privacy-aware labels."""

    template_name = "auctions/detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        request = self.request
        user = request.user
        auction = _get_auction(self.kwargs["pk"], self.emirate, user)

        now = timezone.now()
        auction = services.refresh_auction_state(auction, now=now)
        is_authenticated = bool(getattr(user, "is_authenticated", False))
        is_seller = is_authenticated and user.pk == auction.seller_id
        is_staff = is_authenticated and user.is_staff

        bids = list(
            auction.bids.select_related("bidder")
            .filter(voided_at__isnull=True)
            .order_by("-amount", "created_at", "id")
        )
        highest_eligible = services.highest_eligible_bid(auction)
        top_bid = bids[0] if bids else None
        increment = Decimal(settings.AUCTION_MIN_BID_INCREMENT)
        starting_price = auction.starting_price
        minimum_bid = (
            max(top_bid.amount + increment, starting_price)
            if top_bid is not None
            else starting_price
        )

        viewer_bid_count = (
            auction.bids.filter(bidder=user).count() if is_authenticated else 0
        )
        can_bid = bool(
            is_authenticated
            and not is_seller
            and auction.is_open_for_bids
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

        # The marketplace team now runs the auction lifecycle from the back office.
        # Sellers can follow the item, but no longer lock or cancel it themselves.
        can_lock = False
        can_cancel = False

        context.update(
            {
                "emirate": self.emirate,
                "auction": auction,
                "images": auction.images.all(),
                "cover_image": auction.cover_image,
                "now": now,
                "bids": bids,
                "top_bid": top_bid,
                "highest_eligible": highest_eligible,
                "starting_price": starting_price,
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
                "can_edit": False,
                "reserve_met": highest_eligible is not None,
            }
        )
        return context


class AuctionCreateView(EmirateRequiredMixin, View):
    """Legacy customer auction URL now points at the application workflow."""

    def get(self, request, *args, **kwargs):
        return redirect(
            "auction_marketplace:apply", emirate_slug=self.emirate.slug
        )

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)


class AuctionApplyView(
    LoginRequiredMixin, SellerEligibleMixin, EmirateRequiredMixin, View
):
    """Member application form: staff review and list the item later."""

    template_name = "auctions/applications/form.html"
    login_url = "accounts:login"

    def _context(self, form):
        return {
            "emirate": self.emirate,
            "form": form,
            "mode": "create",
            "min_images": settings.MIN_IMAGES_PER_LISTING,
            "max_images": settings.MAX_IMAGES_PER_LISTING,
        }

    def get(self, request, *args, **kwargs):
        initial = {}
        profile_area = getattr(request.user, "service_area", None)
        if profile_area is not None and profile_area.emirate_id == self.emirate.pk:
            initial["service_area"] = profile_area.pk
        form = AuctionApplicationForm(emirate=self.emirate, initial=initial)
        return render(request, self.template_name, self._context(form))

    def post(self, request, *args, **kwargs):
        form = AuctionApplicationForm(
            request.POST, request.FILES, emirate=self.emirate
        )
        images = request.FILES.getlist("images")
        action = request.POST.get("action", "submit")
        for error in _validate_uploads(images):
            form.add_error("images", error)
        if action == "submit" and len(images) < settings.MIN_IMAGES_PER_LISTING:
            form.add_error(
                "images",
                f"Add at least {settings.MIN_IMAGES_PER_LISTING} photos before submitting.",
            )
        if not form.is_valid():
            return render(request, self.template_name, self._context(form), status=400)

        try:
            with transaction.atomic():
                application = services.create_application(
                    applicant=request.user,
                    emirate=self.emirate,
                    title=form.cleaned_data["title"],
                    description=form.cleaned_data["description"],
                    category=form.cleaned_data["category"],
                    condition=form.cleaned_data["condition"],
                    service_area=form.cleaned_data["service_area"],
                    proposed_starting_bid=form.cleaned_data["proposed_starting_bid"],
                    preferred_start_at=form.cleaned_data.get("preferred_start_at"),
                    preferred_end_at=form.cleaned_data.get("preferred_end_at"),
                    request=request,
                )
                for uploaded in images:
                    services.add_application_image(
                        application, uploaded, actor=request.user
                    )
                if action == "submit":
                    application = services.submit_application(
                        application, actor=request.user, request=request
                    )
        except ValidationError as exc:
            _apply_service_errors(form, exc)
            return render(request, self.template_name, self._context(form), status=400)
        except DomainError as exc:
            form.add_error(None, exc.message)
            return render(request, self.template_name, self._context(form), status=400)

        if action == "submit":
            messages.success(
                request,
                "Application sent. Our team will review it and let you know the outcome.",
            )
        else:
            messages.success(
                request,
                "Application draft saved. Add your photos and submit it when you are ready.",
            )
        return redirect("auctions:application_detail", pk=application.pk)


class MyApplicationsView(LoginRequiredMixin, TemplateView):
    template_name = "auctions/applications/list.html"
    login_url = "accounts:login"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["applications"] = (
            AuctionApplication.objects.filter(applicant=self.request.user)
            .select_related("emirate", "service_area", "category", "auction")
            .prefetch_related("images")
        )
        return context


class AuctionApplicationDetailView(LoginRequiredMixin, TemplateView):
    template_name = "auctions/applications/detail.html"
    login_url = "accounts:login"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        application = _get_application(self.kwargs["pk"], self.request.user)
        context.update(
            {
                "application": application,
                "images": application.images.all(),
                "can_edit": (
                    application.applicant_id == self.request.user.pk
                    and application.is_editable_by_applicant
                ),
                "can_withdraw": (
                    application.applicant_id == self.request.user.pk
                    and application.auction_id is None
                    and application.status
                    not in {
                        AuctionApplication.Status.LISTED,
                        AuctionApplication.Status.REJECTED,
                        AuctionApplication.Status.WITHDRAWN,
                    }
                ),
                "history": AuditEvent.objects.filter(
                    entity_type="auctions.auctionapplication",
                    entity_id=str(application.pk),
                ).select_related("actor")[:30],
            }
        )
        return context


class AuctionApplicationUpdateView(LoginRequiredMixin, TemplateView):
    template_name = "auctions/applications/form.html"
    login_url = "accounts:login"

    def get_application(self) -> AuctionApplication:
        application = _get_application(self.kwargs["pk"], self.request.user)
        if application.applicant_id != self.request.user.pk:
            raise Http404("Application not found.")
        if not application.is_editable_by_applicant:
            messages.error(
                self.request,
                "This application can no longer be edited. Check the status below.",
            )
            raise Http404("Application is not editable.")
        return application

    def _context(self, form, application):
        emirate = application.emirate
        return {
            "emirate": emirate,
            "form": form,
            "application": application,
            "images": application.images.all(),
            "mode": "edit",
            "min_images": settings.MIN_IMAGES_PER_LISTING,
            "max_images": settings.MAX_IMAGES_PER_LISTING,
        }

    def get(self, request, *args, **kwargs):
        application = self.get_application()
        form = AuctionApplicationForm(
            instance=application, emirate=application.emirate
        )
        return render(
            request, self.template_name, self._context(form, application)
        )

    def post(self, request, *args, **kwargs):
        application = self.get_application()
        form = AuctionApplicationForm(
            request.POST,
            request.FILES,
            instance=application,
            emirate=application.emirate,
        )
        images = request.FILES.getlist("images")
        action = request.POST.get("action", "submit")
        for error in _validate_uploads(images):
            form.add_error("images", error)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                self._context(form, application),
                status=400,
            )

        remove_ids = []
        for raw in request.POST.getlist("remove_images"):
            if str(raw).isdigit():
                remove_ids.append(int(raw))
        try:
            with transaction.atomic():
                application = services.update_application(
                    application,
                    actor=request.user,
                    title=form.cleaned_data["title"],
                    description=form.cleaned_data["description"],
                    category=form.cleaned_data["category"],
                    condition=form.cleaned_data["condition"],
                    service_area=form.cleaned_data["service_area"],
                    proposed_starting_bid=form.cleaned_data["proposed_starting_bid"],
                    preferred_start_at=form.cleaned_data.get("preferred_start_at"),
                    preferred_end_at=form.cleaned_data.get("preferred_end_at"),
                    request=request,
                )
                if remove_ids:
                    for image in application.images.filter(pk__in=remove_ids):
                        services.delete_application_image(
                            application, image, actor=request.user
                        )
                for uploaded in images:
                    services.add_application_image(
                        application, uploaded, actor=request.user
                    )
                cover_id = request.POST.get("cover_image", "")
                if cover_id.isdigit():
                    cover = application.images.filter(pk=int(cover_id)).first()
                    if cover is not None:
                        services.set_application_cover(
                            application, cover, actor=request.user
                        )
                if action == "submit":
                    application = services.submit_application(
                        application, actor=request.user, request=request
                    )
        except ValidationError as exc:
            _apply_service_errors(form, exc)
            return render(
                request,
                self.template_name,
                self._context(form, application),
                status=400,
            )
        except DomainError as exc:
            form.add_error(None, exc.message)
            return render(
                request,
                self.template_name,
                self._context(form, application),
                status=400,
            )

        if action == "submit":
            messages.success(request, "Application resubmitted for review.")
        else:
            messages.success(request, "Application changes saved.")
        return redirect("auctions:application_detail", pk=application.pk)


class AuctionApplicationWithdrawView(LoginRequiredMixin, View):
    login_url = "accounts:login"

    def post(self, request, *args, **kwargs):
        application = _get_application(self.kwargs["pk"], request.user)
        try:
            services.withdraw_application(
                application, actor=request.user, request=request
            )
        except DomainError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, "Application withdrawn.")
        return redirect("auctions:application_detail", pk=application.pk)


class AuctionUpdateView(LoginRequiredMixin, EmirateRequiredMixin, View):
    """Legacy member edit endpoint: auction content is now staff-managed."""

    login_url = "accounts:login"

    def get(self, request, *args, **kwargs):
        auction = _get_auction(kwargs["pk"], self.emirate, request.user)
        messages.info(
            request,
            "Auction details are managed by the ManzilCart team. Contact support if "
            "something needs to change.",
        )
        return redirect(
            "auction_marketplace:detail",
            emirate_slug=self.emirate.slug,
            pk=auction.pk,
        )

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)


class AuctionBidView(LoginRequiredMixin, EmirateRequiredMixin, View):
    """Place a bid on a live auction (server-side validation only)."""

    login_url = "accounts:login"

    def post(self, request, *args, **kwargs):
        auction = _get_auction(kwargs["pk"], self.emirate, request.user)
        auction = services.refresh_auction_state(auction)
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
    """Legacy seller lock URL: staff now lock bids from the bid monitor."""

    login_url = "accounts:login"

    def post(self, request, *args, **kwargs):
        auction = _get_auction(kwargs["pk"], self.emirate, request.user)
        messages.info(
            request,
            "Bid locking is handled by the ManzilCart team from the auction monitor.",
        )
        return redirect(
            "auction_marketplace:detail",
            emirate_slug=self.emirate.slug,
            pk=auction.pk,
        )


class AuctionCancelView(LoginRequiredMixin, EmirateRequiredMixin, View):
    """Legacy member cancellation URL; operations cancel from the back office."""

    login_url = "accounts:login"

    def post(self, request, *args, **kwargs):
        auction = _get_auction(kwargs["pk"], self.emirate, request.user)
        if not request.user.is_staff:
            messages.error(
                request,
                "Auction cancellation is handled by the ManzilCart team. "
                "Please contact support.",
            )
            return redirect(
                "auction_marketplace:detail",
                emirate_slug=self.emirate.slug,
                pk=auction.pk,
            )
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
    """Auctions the member is selling in or bidding on, including drafts/rejections."""

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
                    "can_edit": False,
                }
            )
        context["rows"] = rows
        context["applications"] = (
            AuctionApplication.objects.filter(applicant=user)
            .select_related("emirate", "category", "auction")
            .prefetch_related("images")
        )
        context["now"] = timezone.now()

        emirate = getattr(self.request, "emirate", None) or getattr(
            user, "emirate", None
        )
        if emirate is not None and getattr(emirate, "is_active", False):
            context["create_url"] = reverse(
                "auction_marketplace:apply",
                kwargs={"emirate_slug": emirate.slug},
            )
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
            "images": auction.images.all(),
            "cover_image": auction.cover_image,
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
