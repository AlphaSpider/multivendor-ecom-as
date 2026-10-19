"""
Deal back office: inspect negotiations, offers, service selections and cancellation
requests; finalise disputed cancellations.

Deals and their history are never deleted from the admin. Offers, events and
cancellations are read-only inlines so every negotiation remains auditable.
"""

from __future__ import annotations

from django.contrib import admin, messages

from core.exceptions import DomainError
from deals import services
from deals.models import (
    Deal,
    DealCancellation,
    DealEvent,
    DealOffer,
    DealServiceSelection,
)


class DealOfferInline(admin.TabularInline):
    model = DealOffer
    extra = 0
    can_delete = False
    fields = ("sender", "amount", "status", "note", "in_response_to", "created_at", "responded_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class DealCancellationInline(admin.TabularInline):
    model = DealCancellation
    extra = 0
    can_delete = False
    fields = (
        "requested_by",
        "reason",
        "status",
        "responded_by",
        "response_note",
        "created_at",
        "responded_at",
        "resolved_at",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class DealServiceSelectionInline(admin.TabularInline):
    model = DealServiceSelection
    extra = 0
    can_delete = False
    fields = ("service_option", "created_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class DealEventInline(admin.TabularInline):
    model = DealEvent
    extra = 0
    can_delete = False
    fields = ("event_type", "actor", "summary", "metadata", "created_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Deal)
class DealAdmin(admin.ModelAdmin):
    list_display = (
        "reference_display",
        "listing",
        "buyer",
        "seller",
        "emirate",
        "status",
        "agreed_price",
        "accepted_at",
        "created_at",
    )
    list_filter = ("status", "emirate", "created_at")
    search_fields = (
        "id",
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
        "conversation",
        "status",
        "agreed_price",
        "accepted_offer",
        "accepted_at",
        "cancelled_at",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        ("Deal", {"fields": ("listing", "buyer", "seller", "emirate", "conversation")}),
        ("Outcome", {
            "fields": (
                "status",
                "agreed_price",
                "accepted_offer",
                "accepted_at",
                "cancelled_at",
            )
        }),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
    inlines = (
        DealOfferInline,
        DealServiceSelectionInline,
        DealCancellationInline,
        DealEventInline,
    )
    actions = ("finalize_selected_cancellations",)

    @admin.display(description="Reference")
    def reference_display(self, obj):
        return obj.reference

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Finalise cancellation for selected deals (operations)")
    def finalize_selected_cancellations(self, request, queryset):
        finalised = skipped = 0
        for deal in queryset:
            try:
                services.finalize_cancellation(
                    deal, actor=request.user, note="Finalised from Django Admin", request=request
                )
                finalised += 1
            except DomainError as exc:
                skipped += 1
                self.message_user(
                    request, f"{deal.reference}: {exc.message}", messages.WARNING
                )
        self.message_user(
            request,
            f"{finalised} deal(s) cancelled, {skipped} skipped.",
            messages.SUCCESS if finalised else messages.WARNING,
        )


@admin.register(DealCancellation)
class DealCancellationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "deal",
        "requested_by",
        "status",
        "created_at",
        "responded_at",
    )
    list_filter = ("status", "created_at")
    search_fields = ("deal__id", "deal__listing__title", "requested_by__phone")
    list_select_related = ("deal", "requested_by", "responded_by")
    readonly_fields = (
        "deal",
        "requested_by",
        "reason",
        "status",
        "responded_by",
        "response_note",
        "responded_at",
        "resolved_at",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DealOffer)
class DealOfferAdmin(admin.ModelAdmin):
    list_display = ("id", "deal", "sender", "amount", "status", "created_at", "responded_at")
    list_filter = ("status", "created_at")
    search_fields = ("deal__id", "deal__listing__title", "sender__phone")
    list_select_related = ("deal", "sender")
    readonly_fields = (
        "deal",
        "sender",
        "amount",
        "status",
        "note",
        "in_response_to",
        "created_at",
        "responded_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DealEvent)
class DealEventAdmin(admin.ModelAdmin):
    list_display = ("id", "deal", "event_type", "actor", "created_at")
    list_filter = ("event_type", "created_at")
    search_fields = ("deal__id", "deal__listing__title", "summary")
    list_select_related = ("deal", "actor")
    readonly_fields = ("deal", "actor", "event_type", "summary", "metadata", "created_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
