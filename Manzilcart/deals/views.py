"""HTTP views for offers and deals."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import TemplateView

from catalog.models import Listing
from core.exceptions import DomainError
from deals import services
from deals.forms import CancellationRequestForm, OfferForm
from deals.models import Deal, DealCancellation, DealOffer
from deals.selectors import deal_for_participant, deals_for
from locations.selectors import available_service_options


class DealListView(LoginRequiredMixin, TemplateView):
    template_name = "deals/list.html"
    login_url = "accounts:login"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["deals"] = deals_for(self.request.user)
        return context


class DealDetailView(LoginRequiredMixin, TemplateView):
    template_name = "deals/detail.html"
    login_url = "accounts:login"

    def get_deal(self) -> Deal:
        deal = deal_for_participant(
            self.kwargs["pk"], self.request.user, include_staff=True
        )
        if deal is None:
            raise Http404("Deal not found.")
        return deal

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        deal = self.get_deal()
        user = self.request.user
        pending = deal.offers.filter(status=DealOffer.Status.PENDING).first()
        open_cancellation = (
            deal.cancellations.filter(
                status__in=[
                    DealCancellation.Status.PENDING,
                    DealCancellation.Status.ESCALATED,
                ]
            )
            .order_by("-created_at")
            .first()
        )
        latest_cancellation = deal.cancellations.order_by("-created_at").first()
        is_buyer = user.pk == deal.buyer_id
        is_participant = deal.is_participant(user)

        can_respond_to_offer = bool(
            pending and pending.sender_id != user.pk and deal.status == deal.Status.NEGOTIATING
        )
        can_request_cancellation = bool(
            is_participant and deal.status == deal.Status.ACCEPTED
        )
        can_respond_to_cancellation = bool(
            open_cancellation
            and open_cancellation.status == DealCancellation.Status.PENDING
            and open_cancellation.requested_by_id != user.pk
        )
        can_escalate = bool(
            is_participant
            and deal.status
            in {deal.Status.ACCEPTED, deal.Status.CANCELLATION_REQUESTED}
            and latest_cancellation
            and latest_cancellation.status
            in {
                DealCancellation.Status.PENDING,
                DealCancellation.Status.REJECTED,
            }
        )

        selected_ids = set(
            deal.service_selections.values_list("service_option_id", flat=True)
        )
        context.update(
            {
                "deal": deal,
                "pending_offer": pending,
                "offers": deal.offers.select_related("sender").order_by("created_at", "id"),
                "events": deal.events.select_related("actor").order_by("created_at", "id"),
                "cancellations": deal.cancellations.select_related(
                    "requested_by", "responded_by"
                ).order_by("-created_at"),
                "open_cancellation": open_cancellation,
                "latest_cancellation": latest_cancellation,
                "is_buyer": is_buyer,
                "is_participant": is_participant,
                "can_respond_to_offer": can_respond_to_offer,
                "can_offer": deal.status == deal.Status.NEGOTIATING and not pending,
                "can_select_services": is_buyer and deal.status == deal.Status.NEGOTIATING,
                "can_request_cancellation": can_request_cancellation,
                "can_respond_to_cancellation": can_respond_to_cancellation,
                "can_escalate": can_escalate,
                "can_finalize": user.is_staff
                and deal.status
                in {deal.Status.ACCEPTED, deal.Status.CANCELLATION_REQUESTED},
                "offer_form": OfferForm(),
                "cancellation_form": CancellationRequestForm(),
                "available_services": available_service_options(deal.service_area),
                "selected_service_ids": selected_ids,
            }
        )
        return context


class StartDealView(LoginRequiredMixin, View):
    """Create a negotiation from a listing page."""

    login_url = "accounts:login"

    def post(self, request, *args, **kwargs):
        listing = get_object_or_404(
            Listing.objects.select_related("seller", "emirate", "service_area"),
            pk=kwargs["listing_id"],
        )
        form = OfferForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Enter a valid offer amount.")
            return redirect(listing.get_absolute_url())

        try:
            deal = services.create_deal(
                listing=listing,
                buyer=request.user,
                amount=form.cleaned_data["amount"],
                note=form.cleaned_data.get("note", ""),
                request=request,
            )
        except DomainError as exc:
            messages.error(request, exc.message)
            return redirect(listing.get_absolute_url())
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect(listing.get_absolute_url())

        messages.success(request, "Your offer has been sent to the seller.")
        return redirect("deals:detail", pk=deal.pk)


class DealActionView(LoginRequiredMixin, View):
    """One POST endpoint for the deal workflow actions."""

    login_url = "accounts:login"

    def post(self, request, *args, **kwargs):
        deal = deal_for_participant(
            kwargs["pk"], request.user, include_staff=True
        )
        if deal is None:
            raise Http404("Deal not found.")

        action = request.POST.get("action", "")
        handler = getattr(self, f"action_{action}", None)
        if handler is None:
            messages.error(request, "Unknown action.")
            return redirect("deals:detail", pk=deal.pk)

        try:
            message = handler(request, deal)
        except DomainError as exc:
            messages.error(request, exc.message)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            if message:
                messages.success(request, message)

        return redirect("deals:detail", pk=deal.pk)

    # -- offer actions -----------------------------------------------------
    def action_offer(self, request, deal):
        form = OfferForm(request.POST)
        if not form.is_valid():
            raise ValidationError("Enter a valid offer amount.")
        services.make_offer(
            deal,
            actor=request.user,
            amount=form.cleaned_data["amount"],
            note=form.cleaned_data.get("note", ""),
        )
        return "Offer sent."

    def action_counter(self, request, deal):
        form = OfferForm(request.POST)
        if not form.is_valid():
            raise ValidationError("Enter a valid counteroffer amount.")
        services.counter_offer(
            deal,
            actor=request.user,
            amount=form.cleaned_data["amount"],
            note=form.cleaned_data.get("note", ""),
        )
        return "Counteroffer sent."

    def action_accept(self, request, deal):
        services.accept_offer(deal, actor=request.user, request=request)
        return "Deal accepted. The item is now reserved."

    def action_reject(self, request, deal):
        services.reject_offer(
            deal, actor=request.user, note=request.POST.get("note", "")
        )
        return "Offer rejected."

    # -- services ----------------------------------------------------------
    def action_services(self, request, deal):
        services.set_service_selections(
            deal,
            actor=request.user,
            option_ids=request.POST.getlist("services"),
        )
        return "Optional services updated."

    # -- cancellation ------------------------------------------------------
    def action_request_cancellation(self, request, deal):
        form = CancellationRequestForm(request.POST)
        if not form.is_valid():
            raise ValidationError("Please explain why the deal should be cancelled.")
        services.request_cancellation(
            deal,
            actor=request.user,
            reason=form.cleaned_data["reason"],
            request=request,
        )
        return "Cancellation requested. The other party has been notified."

    def action_respond_cancellation(self, request, deal):
        cancellation = self._get_cancellation(request, deal)
        accept = request.POST.get("decision") == "accept"
        services.respond_to_cancellation(
            cancellation,
            actor=request.user,
            accept=accept,
            note=request.POST.get("note", ""),
            request=request,
        )
        return (
            "Cancellation accepted. The deal is cancelled and the item reopened."
            if accept
            else "Cancellation request rejected."
        )

    def action_escalate(self, request, deal):
        cancellation = self._get_cancellation(request, deal)
        services.escalate_cancellation(
            cancellation,
            actor=request.user,
            note=request.POST.get("note", ""),
            request=request,
        )
        return "Escalated to ManzilCart operations."

    def action_finalize(self, request, deal):
        services.finalize_cancellation(
            deal,
            actor=request.user,
            note=request.POST.get("note", ""),
            request=request,
        )
        return "Deal cancelled by operations; the item was reopened where possible."

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _get_cancellation(request, deal):
        cancellation_id = request.POST.get("cancellation_id")
        cancellation = deal.cancellations.filter(pk=cancellation_id).first()
        if cancellation is None:
            raise Http404("Cancellation request not found.")
        return cancellation
