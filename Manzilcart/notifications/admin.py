"""Notification administration and the operations retry console."""

from __future__ import annotations

from django.contrib import admin, messages
from django.utils.html import format_html

from notifications.models import Notification
from notifications.services import retry_notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    """
    Delivery log / retry console.

    Access is staff-only. Recipients and message bodies can contain personal data (an
    OTP message obviously does), so this screen is restricted and rows are not
    deletable through the application: delivery history is operational evidence.
    """

    list_display = (
        "created_at",
        "channel",
        "kind",
        "recipient",
        "status",
        "attempts",
        "next_attempt_at",
        "sent_at",
    )
    list_filter = ("channel", "kind", "status", "created_at")
    search_fields = ("recipient", "idempotency_key", "entity_id", "provider_message_id")
    date_hierarchy = "created_at"
    readonly_fields = (
        "channel",
        "kind",
        "recipient",
        "subject",
        "body_preview",
        "status",
        "attempts",
        "max_attempts",
        "next_attempt_at",
        "last_error",
        "sent_at",
        "provider",
        "provider_message_id",
        "idempotency_key",
        "user",
        "emirate",
        "entity_type",
        "entity_id",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        ("Message", {"fields": ("channel", "kind", "recipient", "subject", "body_preview")}),
        ("Delivery", {
            "fields": (
                "status",
                "attempts",
                "max_attempts",
                "next_attempt_at",
                "last_error",
                "sent_at",
                "provider",
                "provider_message_id",
            )
        }),
        ("Traceability", {
            "fields": ("user", "emirate", "entity_type", "entity_id", "idempotency_key")
        }),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
    actions = ("retry_selected",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Message")
    def body_preview(self, obj):
        # Truncate: OTP bodies are short, but long deal mails should not dominate the page.
        text = (obj.body or "")[:400]
        return format_html("<pre style='white-space:pre-wrap'>{}</pre>", text)

    @admin.action(description="Retry selected notifications now")
    def retry_selected(self, request, queryset):
        retried = 0
        for notification in queryset.exclude(status=Notification.Status.SENT):
            retry_notification(notification, actor=request.user, request=request)
            retried += 1
        self.message_user(
            request,
            f"{retried} notification(s) re-queued.",
            messages.SUCCESS if retried else messages.WARNING,
        )
