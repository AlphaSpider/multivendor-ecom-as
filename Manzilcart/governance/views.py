"""Customer-side views for the admin ↔ seller marketplace chat.

The poster of a fixed-price post or auction replies to staff feedback here,
outside Django Admin: ``/my/support/`` lists their threads,
``/my/support/<pk>/`` is the conversation with a reply form. Admin messages are
marked read when the seller opens the thread.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.http import Http404
from django.db.models import Count, F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from auctions.models import Auction
from catalog.models import Listing
from governance import services
from governance.models import ListingChatThread


class SupportThreadListView(LoginRequiredMixin, View):
    template_name = "governance/support_threads.html"

    def get(self, request, *args, **kwargs):
        threads = (
            ListingChatThread.objects.filter(customer=request.user)
            .select_related("content_type")
            .annotate(
                unread_count=Count(
                    "messages",
                    filter=Q(messages__is_read=False) & ~Q(messages__sender_id=F("customer_id")),
                )
            )
            .order_by("-last_message_at", "-id")
        )
        return render(
            request,
            self.template_name,
            {"threads": threads},
        )


class SupportThreadDetailView(LoginRequiredMixin, View):
    template_name = "governance/support_thread.html"

    def _thread(self, request):
        thread = get_object_or_404(
            ListingChatThread.objects.select_related("content_type", "customer"),
            pk=self.kwargs["pk"],
        )
        if thread.customer_id != request.user.pk:
            # A 404 (not 403) so the existence of someone else's moderation
            # thread is not disclosed.
            raise Http404("This conversation does not exist.")
        return thread

    # -- thread context helpers ------------------------------------------
    def _item_context(self, thread) -> dict:
        """Sidebar facts about the item the thread is anchored to.

        Generic FKs can dangle when an item was deleted; every key falls back
        so the page stays renderable.
        """
        item = thread.item
        listing = item if isinstance(item, Listing) else None
        auction = item if isinstance(item, Auction) else None
        context = {
            "item": item,
            "item_kind": "",
            "item_title": "",
            "item_cover_url": "",
            "item_price": None,
            "item_area_name": "",
            "item_emirate_name": "",
            "item_status": "",
            "item_moderation": "",
            "item_url": "",
            "edit_url": "",
            "can_edit_item": False,
        }
        if item is None:
            context["item_title"] = "Item removed"
            return context

        context["item_kind"] = "listing" if listing else "auction"
        context["item_title"] = item.title
        context["item_url"] = item.get_absolute_url()

        if listing:
            # A listing's review state IS its status (draft…changes_requested).
            context["item_price"] = listing.asking_price
            context["item_status"] = item.get_status_display()
            context["item_area_name"] = listing.service_area.name if listing.service_area_id else ""
            context["item_emirate_name"] = (
                listing.emirate.name if listing.emirate_id else ""
            )
            cover = listing.cover_image
            context["item_cover_url"] = cover.image.url if cover else ""
            # The seller edit form lives under the item's emirate slug.
            if listing.emirate_id and listing.status in (
                Listing.Status.CHANGES_REQUESTED,
                Listing.Status.REJECTED,
            ):
                context["item_moderation"] = item.get_status_display()
                context["edit_url"] = reverse(
                    "catalog:listing_edit",
                    kwargs={
                        "emirate_slug": listing.emirate.slug,
                        "slug": listing.slug,
                    },
                )
                context["can_edit_item"] = True
        else:
            context["item_status"] = item.get_status_display()
            context["item_moderation"] = item.get_moderation_status_display()
            context["item_area_name"] = (
                item.service_area.name if item.service_area_id else ""
            )
            context["item_emirate_name"] = item.emirate.name if item.emirate_id else ""
            cover = item.cover_image
            context["item_cover_url"] = cover.image.url if cover else ""
            if item.emirate_id and item.is_editable_by_owner and item.moderation_status in (
                Auction.ModerationStatus.CHANGES_REQUESTED,
                Auction.ModerationStatus.REJECTED,
            ):
                context["edit_url"] = reverse(
                    "auctions:edit",
                    kwargs={"emirate_slug": item.emirate.slug, "pk": item.pk},
                )
                context["can_edit_item"] = True
        return context

    def get(self, request, *args, **kwargs):
        thread = self._thread(request)
        services.mark_staff_messages_read(thread)
        # NOTE: ``chat_messages`` instead of ``messages`` - a context variable
        # called ``messages`` would shadow the Django messages framework in
        # base.html and render chat rows as flash alerts.
        context = {
            "thread": thread,
            "chat_messages": thread.messages.select_related("sender"),
            "can_reply": thread.status != ListingChatThread.Status.RESOLVED,
            "max_message_length": 4000,
        }
        context.update(self._item_context(thread))
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        thread = self._thread(request)
        if thread.status == ListingChatThread.Status.RESOLVED:
            messages.error(request, "This conversation is closed. Contact support to reopen it.")
            return redirect("governance:support_thread", pk=thread.pk)

        message = request.POST.get("message", "")
        attachment = request.FILES.get("attachment")
        try:
            services.create_message(
                thread, sender=request.user, message=message, attachment=attachment
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(part for parts in exc.message_dict.values() for part in parts))
        else:
            messages.success(request, "Your message was sent to the marketplace team.")
        return redirect("governance:support_thread", pk=thread.pk)
