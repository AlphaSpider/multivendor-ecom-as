"""
Read-only conversation inspection for staff.

Support staff occasionally need to resolve a dispute. They can read a thread, but they
cannot create, edit, or delete messages: the conversation is evidence, and rewriting it
would destroy that evidence. Abusive accounts are handled through the normal account
administration, not by altering chat history.
"""

from __future__ import annotations

from django.contrib import admin
from django.utils.html import format_html

from conversations.models import Conversation, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    can_delete = False
    fields = ("sender", "body", "created_at", "read_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "listing",
        "buyer",
        "seller",
        "emirate",
        "last_message_at",
        "created_at",
    )
    list_filter = ("emirate", "created_at")
    search_fields = (
        "listing__title",
        "buyer__phone",
        "buyer__display_name",
        "seller__phone",
        "seller__display_name",
    )
    list_select_related = ("listing", "buyer", "seller", "emirate")
    date_hierarchy = "created_at"
    readonly_fields = (
        "listing",
        "buyer",
        "seller",
        "emirate",
        "last_message_at",
        "created_at",
        "updated_at",
    )
    inlines = (MessageInline,)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        # Messages are referenced by PROTECT/CASCADE history; never delete a thread.
        return False


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "sender", "short_body", "created_at", "read_at")
    list_filter = ("created_at", "read_at")
    search_fields = (
        "body",
        "sender__phone",
        "sender__display_name",
        "conversation__listing__title",
    )
    list_select_related = ("conversation", "sender")
    date_hierarchy = "created_at"
    readonly_fields = (
        "conversation",
        "sender",
        "body",
        "client_message_id",
        "created_at",
        "read_at",
    )

    @admin.display(description="Message")
    def short_body(self, obj):
        return format_html("{}", obj.body[:80] + ("…" if len(obj.body) > 80 else ""))

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
