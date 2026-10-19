"""
Auction back office: inspect auctions, bids, fallback rounds and history.

Auction and selection state is written by ``auctions.services`` from member actions
and the deadline worker. The admin therefore exposes the records read-only, apart
from a safe "cancel auction" action for operations.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.db.models import Max

from auctions import services
from auctions.models import Auction, AuctionEvent, AuctionSelection, Bid
from core.exceptions import DomainError


class BidInline(admin.TabularInline):
    model = Bid
    extra = 0
    can_delete = False
    fields = ("bidder", "amount", "client_bid_id", "created_at")
    readonly_fields = fields
    ordering = ("-amount", "created_at", "id")

    def has_add_permission(self, request, obj=None):
        return False


class AuctionSelectionInline(admin.TabularInline):
    model = AuctionSelection
    extra = 0
    can_delete = False
    fields = (
        "sequence",
        "bidder",
        "bid",
        "status",
        "confirmation_deadline",
        "responded_at",
        "created_at",
    )
    readonly_fields = fields
    ordering = ("sequence",)

    def has_add_permission(self, request, obj=None):
        return False


class AuctionEventInline(admin.TabularInline):
    model = AuctionEvent
    extra = 0
    can_delete = False
    fields = ("event_type", "actor", "summary", "metadata", "created_at")
    readonly_fields = fields
    ordering = ("created_at", "id")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Auction)
class AuctionAdmin(admin.ModelAdmin):
    list_display = (
        "reference_display",
        "listing",
        "seller",
        "emirate",
        "status",
        "start_at",
        "end_at",
        "reserve_price",
        "top_bid_display",
        "selected_bidder",
        "awarded_deal",
    )
    list_filter = ("status", "emirate", "start_at", "end_at", "created_at")
    search_fields = (
        "id",
        "listing__title",
        "listing__slug",
        "seller__phone",
        "seller__display_name",
        "selected_bidder__phone",
    )
    list_select_related = ("listing", "seller", "emirate", "selected_bidder", "awarded_deal")
    date_hierarchy = "created_at"
    readonly_fields = (
        "listing",
        "seller",
        "emirate",
        "start_at",
        "end_at",
        "reserve_price",
        "status",
        "seller_agreed_fallback",
        "selected_bid",
        "selected_bidder",
        "confirmation_deadline",
        "awarded_deal",
        "awarded_at",
        "outcome_note",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        ("Auction", {"fields": ("listing", "seller", "emirate", "seller_agreed_fallback")}),
        ("Schedule", {"fields": ("start_at", "end_at", "reserve_price", "status")}),
        (
            "Selection",
            {
                "fields": (
                    "selected_bid",
                    "selected_bidder",
                    "confirmation_deadline",
                )
            },
        ),
        ("Outcome", {"fields": ("awarded_deal", "awarded_at", "outcome_note")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
    inlines = (BidInline, AuctionSelectionInline, AuctionEventInline)
    actions = ("cancel_selected_auctions",)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(top_bid=Max("bids__amount"))

    @admin.display(description="Reference")
    def reference_display(self, obj):
        return obj.reference

    @admin.display(description="Highest bid", ordering="top_bid")
    def top_bid_display(self, obj):
        if obj.top_bid is None:
            return "—"
        return f"AED {obj.top_bid}"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Cancel selected scheduled/live auctions (operations)")
    def cancel_selected_auctions(self, request, queryset):
        cancelled = skipped = 0
        for auction in queryset:
            try:
                services.cancel_auction(
                    auction,
                    actor=request.user,
                    reason="Cancelled from Django Admin",
                    request=request,
                )
                cancelled += 1
            except DomainError as exc:
                skipped += 1
                self.message_user(
                    request, f"{auction.reference}: {exc.message}", messages.WARNING
                )
        self.message_user(
            request,
            f"{cancelled} auction(s) cancelled, {skipped} skipped.",
            messages.SUCCESS if cancelled else messages.WARNING,
        )


@admin.register(Bid)
class BidAdmin(admin.ModelAdmin):
    list_display = ("id", "auction", "bidder", "amount", "created_at")
    list_filter = ("created_at",)
    search_fields = ("auction__id", "auction__listing__title", "bidder__phone")
    list_select_related = ("auction", "bidder")
    readonly_fields = (
        "auction",
        "bidder",
        "amount",
        "client_bid_id",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuctionSelection)
class AuctionSelectionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "auction",
        "sequence",
        "bidder",
        "bid",
        "status",
        "confirmation_deadline",
        "responded_at",
        "created_at",
    )
    list_filter = ("status", "created_at", "confirmation_deadline")
    search_fields = (
        "auction__id",
        "auction__listing__title",
        "bidder__phone",
        "bidder__display_name",
    )
    list_select_related = ("auction", "bidder", "bid")
    readonly_fields = (
        "auction",
        "bidder",
        "bid",
        "sequence",
        "status",
        "confirmation_deadline",
        "responded_at",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuctionEvent)
class AuctionEventAdmin(admin.ModelAdmin):
    list_display = ("id", "auction", "event_type", "actor", "summary", "created_at")
    list_filter = ("event_type", "created_at")
    search_fields = ("auction__id", "auction__listing__title", "summary")
    list_select_related = ("auction", "actor")
    readonly_fields = (
        "auction",
        "actor",
        "event_type",
        "summary",
        "metadata",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
