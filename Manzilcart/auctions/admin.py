"""
Auction back office: inspect auctions, bids, fallback rounds and history.

Auction and selection state is written by ``auctions.services`` from member actions
and the deadline worker. The admin therefore exposes the records read-only, apart
from a safe "cancel auction" action for operations.
"""

from __future__ import annotations

from django.contrib import admin

from auctions.models import AuctionEvent, AuctionSelection, Bid


class BidInline(admin.TabularInline):
    model = Bid
    extra = 0
    can_delete = False
    fields = ("bidder", "amount", "client_bid_id", "voided_at", "created_at")
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


@admin.register(Bid)
class BidAdmin(admin.ModelAdmin):
    list_display = ("id", "auction", "bidder", "amount", "created_at")
    list_filter = ("created_at",)
    search_fields = ("auction__id", "auction__title", "bidder__phone")
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
        "auction__title",
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
    search_fields = ("auction__id", "auction__title", "summary")
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
