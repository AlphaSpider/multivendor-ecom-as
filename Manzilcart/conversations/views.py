"""HTTP views for conversations.

The WebSocket consumer handles live delivery; these views own the page, the message
history pagination, thread creation, and a no-JavaScript send fallback. Every view is
login-only and every conversation lookup is participant-scoped.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from catalog.models import Listing
from auctions.models import Auction
from auctions import services as auction_services
from conversations import services
from conversations.selectors import (
    conversation_for_participant,
    conversations_for,
    messages_for,
)
from core.exceptions import DomainError
from deals.forms import OfferForm
from deals.models import Deal, DealOffer


class ConversationListView(LoginRequiredMixin, TemplateView):
    template_name = "conversations/list.html"
    login_url = "accounts:login"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["conversations"] = conversations_for(self.request.user)
        return context


class ConversationDetailView(LoginRequiredMixin, TemplateView):
    template_name = "conversations/detail.html"
    login_url = "accounts:login"

    def get(self, request, *args, **kwargs):
        conversation = self.get_conversation()
        services.mark_conversation_read(conversation, reader=request.user)
        return super().get(request, *args, **kwargs)

    def get_conversation(self):
        conversation = conversation_for_participant(
            self.kwargs["pk"], self.request.user
        )
        if conversation is None:
            raise Http404("Conversation not found.")
        return conversation

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        conversation = self.get_conversation()
        user = self.request.user

        # Newest first for pagination, then reversed for display. This keeps the
        # "load older" cursor stable when new messages arrive while reading.
        queryset = messages_for(conversation).order_by("-created_at", "-id")
        paginator = Paginator(queryset, settings.MESSAGES_PER_PAGE)
        page_obj = paginator.get_page(self.request.GET.get("page"))
        page_messages = sorted(page_obj.object_list, key=lambda m: (m.created_at, m.pk))

        is_seller = conversation.seller_id == user.pk
        context.update(
            {
                "conversation": conversation,
                "item": conversation,
                "listing": conversation.listing,
                "other": conversation.other_participant(user),
                "messages": page_messages,
                "page_obj": page_obj,
                "is_seller": is_seller,
                "max_message_length": settings.CHAT_MESSAGE_MAX_LENGTH,
            }
        )

        # Auction-specific context: expose bid locking info for the seller.
        if conversation.auction_id:
            auction = conversation.auction
            now = timezone.now()
            highest_eligible = auction_services.highest_eligible_bid(auction)

            bids = list(
                auction.bids.select_related("bidder")
                .order_by("-amount", "created_at", "id")
            )
            top_bid = bids[0] if bids else None
            increment = Decimal(settings.AUCTION_MIN_BID_INCREMENT)
            minimum_bid = top_bid.amount + increment if top_bid else Decimal("1.00")

            can_lock = bool(
                is_seller
                and auction.is_public
                and auction.status == Auction.Status.LIVE
                and now < auction.end_at
                and highest_eligible is not None
            )

            context.update(
                {
                    "auction": auction,
                    "auction_bids": bids,
                    "auction_top_bid": top_bid,
                    "auction_minimum_bid": minimum_bid,
                    "auction_highest_eligible": highest_eligible,
                    "can_lock": can_lock,
                    "auction_emirate_slug": auction.emirate.slug,
                    "auction_now": now,
                }
            )

        # Listing-specific context: deal / offer panel for the buyer.
        if conversation.listing_id and not is_seller:
            listing = conversation.listing
            # Find the buyer's open negotiation for this listing, if any.
            existing_deal = (
                Deal.objects.filter(
                    listing=listing,
                    buyer=user,
                )
                .exclude(status=Deal.Status.CANCELLED)
                .select_related("accepted_offer")
                .first()
            )
            pending_offer = None
            if existing_deal:
                pending_offer = (
                    existing_deal.offers
                    .filter(status=DealOffer.Status.PENDING)
                    .first()
                )
            # Buyer can start a fresh deal if: listing is publicly available,
            # no existing negotiation, and listing isn't reserved/sold.
            can_start_deal = bool(
                listing.is_public
                and listing.status not in {"reserved", "sold"}
                and existing_deal is None
            )
            # If already negotiating, they can make an offer if none is pending.
            can_offer_in_deal = bool(
                existing_deal
                and existing_deal.status == Deal.Status.NEGOTIATING
                and pending_offer is None
            )

            context.update(
                {
                    "listing_asking_price": listing.asking_price,
                    "existing_deal": existing_deal,
                    "pending_offer": pending_offer,
                    "can_start_deal": can_start_deal,
                    "can_offer_in_deal": can_offer_in_deal,
                    "offer_form": OfferForm(),
                }
            )

        return context


class StartConversationView(LoginRequiredMixin, View):
    """Create (or reopen) the buyer's thread for a listing."""

    login_url = "accounts:login"

    def post(self, request, *args, **kwargs):
        listing = get_object_or_404(
            Listing.objects.select_related("seller", "emirate"),
            pk=kwargs["listing_id"],
        )
        try:
            conversation = services.start_conversation(
                listing, request.user, request=request
            )
        except DomainError as exc:
            messages.error(request, exc.message)
            return redirect(listing.get_absolute_url())
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect(listing.get_absolute_url())

        return redirect("conversations:detail", pk=conversation.pk)


class StartAuctionConversationView(LoginRequiredMixin, View):
    """Create (or reopen) the buyer's thread for a standalone auction."""

    login_url = "accounts:login"

    def post(self, request, *args, **kwargs):
        auction = get_object_or_404(
            Auction.objects.select_related("seller", "emirate"),
            pk=kwargs["auction_id"],
            moderation_status=Auction.ModerationStatus.APPROVED,
        )
        try:
            conversation = services.start_conversation(
                auction=auction, buyer=request.user, request=request
            )
        except DomainError as exc:
            messages.error(request, exc.message)
            return redirect(auction.get_absolute_url())
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect(auction.get_absolute_url())

        return redirect("conversations:detail", pk=conversation.pk)


class SendMessageView(LoginRequiredMixin, View):
    """Progressive-enhancement fallback when JavaScript/WebSocket is unavailable."""

    login_url = "accounts:login"

    def post(self, request, *args, **kwargs):
        conversation = conversation_for_participant(kwargs["pk"], request.user)
        if conversation is None:
            raise Http404("Conversation not found.")

        try:
            message, _created = services.send_message(
                conversation,
                sender=request.user,
                body=request.POST.get("body", ""),
                request=request,
            )
        except DomainError as exc:
            messages.error(request, exc.message)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            url = reverse("conversations:detail", kwargs={"pk": conversation.pk})
            return redirect(f"{url}#message-{message.pk}")

        return redirect("conversations:detail", pk=conversation.pk)
