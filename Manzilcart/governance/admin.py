"""
Admin integration for the governance system.

* ``CustomerChatAdminMixin`` - embeds the "Customer chat about this post" panel
  on the change form of any moderated model, with HTMX-style partial endpoints
  registered through ``ModelAdmin.get_urls()``. The panel markup sits *outside*
  the main admin ``<form>`` (its buttons are ``type="button"`` and sending uses
  ``fetch``), so a chat send can never trigger the product form's save flow.
* plain admin registrations for the chat models so staff can see raw rows.
"""

from __future__ import annotations

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils.html import escape

from governance import services
from governance.models import ListingChatMessage, ListingChatThread

MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024


class CustomerChatAdminMixin:
    """Add the contextual chat panel + fetch endpoints to a ModelAdmin."""

    governance_chat_enabled = True
    # Deliberately a named template (not a global admin override): this child
    # extends Django's own change_form.html from OUTSIDE the main form.
    change_form_template = "admin/governance/change_form_with_chat.html"

    # -- change form wiring --------------------------------------------------
    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        if obj is not None and self.governance_chat_enabled:
            thread = services.get_thread(obj)
            context["governance_chat"] = {
                "thread": thread,
                "messages": list(thread.messages.select_related("sender")[:100]),
                "messages_url": self._chat_messages_url(obj),
                "send_url": self._chat_send_url(obj),
                "attachment_value_error": None,
            }
        return super().render_change_form(
            request, context, add=add, change=change, form_url=form_url, obj=obj
        )

    def _chat_messages_url(self, obj) -> str:
        raise NotImplementedError

    def _chat_send_url(self, obj) -> str:
        raise NotImplementedError

    # -- fetch endpoints ------------------------------------------------------
    def get_urls(self):
        custom = [
            path(
                "<path:object_id>/governance-chat/messages/",
                self.admin_site.admin_view(self.chat_messages_view),
                name=f"governance_chat_messages_{self.model._meta.model_name}",
            ),
            path(
                "<path:object_id>/governance-chat/send/",
                self.admin_site.admin_view(self.chat_send_view),
                name=f"governance_chat_send_{self.model._meta.model_name}",
            ),
        ]
        return custom + super().get_urls()

    def _chat_item(self, request, object_id):
        return get_object_or_404(self.model, pk=object_id)

    def _chat_thread(self, request, object_id):
        item = self._chat_item(request, object_id)
        return item, services.get_thread(item)

    @property
    def _messages_template(self):
        return "admin/governance/chat_messages.html"

    def chat_messages_view(self, request, object_id):
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied
        item, thread = self._chat_thread(request, object_id)
        services.mark_customer_messages_read(thread)
        return render_messages(request, thread)

    def chat_send_view(self, request, object_id):
        if request.method != "POST" or not self.has_change_permission(request):
            raise PermissionDenied
        item, thread = self._chat_thread(request, object_id)

        # Simple image/file attachment guard mirroring listing rules.
        attachment = request.FILES.get("attachment")
        if attachment is not None and attachment.size > MAX_ATTACHMENT_BYTES:
            response = HttpResponse("Attachment is too large (5 MB maximum).", status=400)
            return response

        try:
            with transaction.atomic():
                services.create_message(
                    thread,
                    sender=request.user,
                    message=request.POST.get("message", ""),
                    attachment=attachment,
                )
        except Exception as exc:  # noqa: BLE001 - surfaced inside the chat panel
            return HttpResponse(escape(str(exc)), status=400)

        return render_messages(request, thread)


def render_messages(request, thread: ListingChatThread) -> HttpResponse:
    """Partial HTML for the message list; also used after sends."""
    from django.shortcuts import render

    return render(
        request,
        "admin/governance/chat_messages.html",
        {"thread": thread, "messages": thread.messages.select_related("sender")[:150]},
    )


@admin.register(ListingChatThread)
class ListingChatThreadAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "item_display",
        "customer",
        "status",
        "last_message_at",
        "unread",
    )
    list_filter = ("status", "content_type")
    search_fields = ("customer__phone", "customer__display_name")
    list_select_related = ("customer", "content_type")

    @admin.display(description="Item")
    def item_display(self, obj):
        return str(obj.item)

    @admin.display(description="Unread (staff)")
    def unread(self, obj):
        return obj.unread_messages_for_staff()


@admin.register(ListingChatMessage)
class ListingChatMessageAdmin(admin.ModelAdmin):
    list_display = ("thread", "sender", "short_message", "is_read", "created_at")
    list_filter = ("is_read", "created_at")
    search_fields = ("message", "sender__phone")
    list_select_related = ("thread", "sender")

    @admin.display(description="Message")
    def short_message(self, obj):
        return obj.message[:80]
