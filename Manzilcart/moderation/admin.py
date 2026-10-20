"""
Listing and auction moderation in Django Admin.

The specification asks for a *small* back office, not a separate moderation product.
This module therefore provides:

* moderation queues (pending items first),
* bulk approval,
* a per-item review screen where a rejection reason can be written,
* append-only ``ListingReview`` / ``AuctionReview`` rows plus an ``AuditEvent`` for
  every decision.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Case, IntegerField, Max, Value, When
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html

from auctions import services as auction_services
from auctions.admin import AuctionEventInline, AuctionSelectionInline, BidInline
from auctions.models import Auction, AuctionImage
from catalog import services
from catalog.models import Listing
from core.exceptions import StateTransitionError
from moderation.forms import ModerationDecisionForm
from moderation.models import ListingReview


class AuctionImageInline(admin.TabularInline):
    model = AuctionImage
    extra = 0
    can_delete = False
    fields = (
        "thumbnail",
        "caption",
        "is_cover",
        "display_order",
        "width",
        "height",
        "byte_size",
    )
    readonly_fields = fields
    ordering = ("display_order", "id")

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="Preview")
    def thumbnail(self, obj):
        if obj is None or not obj.image:
            return "—"
        return format_html(
            '<a href="{url}" target="_blank"><img src="{url}" '
            'style="height:70px;border-radius:6px"></a>',
            url=obj.image.url,
        )


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "status",
        "seller",
        "emirate",
        "service_area",
        "asking_price",
        "submitted_at",
        "published_at",
    )
    list_filter = ("status", "emirate", "condition", "created_at")
    search_fields = ("title", "slug", "seller__phone", "seller__display_name")
    list_select_related = ("seller", "emirate", "service_area", "category")
    date_hierarchy = "created_at"
    autocomplete_fields = ("category", "emirate", "service_area")
    readonly_fields = (
        "seller",
        "slug",
        "emirate",
        "service_area",
        "status",
        "created_at",
        "updated_at",
        "submitted_at",
        "approved_at",
        "approved_by",
        "published_at",
        "reserved_at",
        "review_link",
        "gallery",
    )
    fieldsets = (
        ("Listing", {"fields": ("title", "category", "condition", "asking_price", "description")}),
        ("Location", {"fields": ("emirate", "service_area")}),
        (
            "Publication",
            {
                "fields": (
                    "status",
                    "moderation_note",
                    "submitted_at",
                    "approved_at",
                    "approved_by",
                    "published_at",
                    "reserved_at",
                )
            },
        ),
        ("Seller", {"fields": ("seller", "slug")}),
        ("Media", {"fields": ("gallery",)}),
        ("Review", {"fields": ("review_link",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
    actions = ("approve_selected",)

    # -- permissions -------------------------------------------------------
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if "status__exact" not in request.GET:
            queryset = queryset.annotate(
                _queue=Case(
                    When(status=Listing.Status.PENDING, then=Value(0)),
                    When(status=Listing.Status.REJECTED, then=Value(1)),
                    default=Value(2),
                    output_field=IntegerField(),
                )
            ).order_by("_queue", "submitted_at", "id")
        return queryset

    # -- columns -----------------------------------------------------------
    @admin.display(description="Photos")
    def gallery(self, obj):
        images = list(obj.images.all()[:6])
        if not images:
            return "No photos"
        return format_html(
            " ".join(
                '<a href="{url}" target="_blank"><img src="{url}" '
                'style="height:70px;border-radius:6px;margin:2px"></a>'.format(
                    url=image.image.url
                )
                for image in images
            )
        )

    @admin.display(description="Review")
    def review_link(self, obj):
        if obj is None or obj.pk is None:
            return "Save first"
        url = reverse("admin:moderation_review", args=[obj.pk])
        return format_html('<a class="button" href="{}">Open review screen</a>', url)

    # -- actions -----------------------------------------------------------
    @admin.action(description="Approve and publish selected listings")
    def approve_selected(self, request, queryset):
        approved = skipped = 0
        for listing in queryset:
            previous = listing.status
            try:
                services.transition_listing(
                    listing,
                    Listing.Status.APPROVED,
                    actor=request.user,
                    request=request,
                )
                ListingReview.objects.create(
                    listing=listing,
                    reviewer=request.user,
                    decision=ListingReview.Decision.APPROVE,
                    previous_status=previous,
                    new_status=Listing.Status.APPROVED,
                )
                approved += 1
            except (ValidationError, StateTransitionError) as exc:
                skipped += 1
                self.message_user(
                    request,
                    f"'{listing.title}' was skipped: {exc}",
                    messages.WARNING,
                )
        self.message_user(
            request,
            f"{approved} listing(s) approved, {skipped} skipped.",
            messages.SUCCESS if approved else messages.WARNING,
        )

    # -- custom review screen ---------------------------------------------
    def get_urls(self):
        custom = [
            path(
                "<int:listing_id>/review/",
                self.admin_site.admin_view(self.review_view),
                name="moderation_review",
            ),
        ]
        return custom + super().get_urls()

    def review_view(self, request, listing_id: int):
        if not self.has_change_permission(request):
            raise PermissionDenied

        listing = get_object_or_404(
            Listing.objects.select_related("seller", "emirate", "service_area", "category"),
            pk=listing_id,
        )
        form = ModerationDecisionForm(request.POST or None)

        if request.method == "POST" and form.is_valid():
            decision = form.cleaned_data["decision"]
            reason = form.cleaned_data.get("reason", "")
            target = (
                Listing.Status.APPROVED
                if decision == "approve"
                else Listing.Status.REJECTED
            )
            previous = listing.status
            try:
                services.transition_listing(
                    listing, target, actor=request.user, reason=reason, request=request
                )
            except (ValidationError, Exception) as exc:  # noqa: BLE001 - surfaced to staff
                messages.error(request, f"Could not update the listing: {exc}")
            else:
                ListingReview.objects.create(
                    listing=listing,
                    reviewer=request.user,
                    decision=(
                        ListingReview.Decision.APPROVE
                        if decision == "approve"
                        else ListingReview.Decision.REJECT
                    ),
                    reason=reason,
                    previous_status=previous,
                    new_status=target,
                )
                messages.success(
                    request,
                    f"'{listing.title}' was {'approved' if decision == 'approve' else 'rejected'}.",
                )
                return redirect(reverse("admin:catalog_listing_changelist"))

        context = {
            **self.admin_site.each_context(request),
            "title": f"Review listing: {listing.title}",
            "object": listing,
            "object_kind": "listing",
            "object_label": listing.title,
            "seller": listing.seller,
            "location": f"{listing.service_area.name}, {listing.emirate.name}",
            "meta": (
                f"{listing.category.name} · {listing.get_condition_display()} · "
                f"AED {listing.asking_price:,.2f}"
            ),
            "description": listing.description,
            "images": listing.images.all(),
            "reviews": listing.reviews.select_related("reviewer")[:10],
            "changelist_url": reverse("admin:catalog_listing_changelist"),
            "form": form,
            "opts": self.model._meta,
        }
        return render(request, "admin/moderation/review.html", context)


@admin.register(Auction)
class AuctionAdmin(admin.ModelAdmin):
    """Moderation-first auction back office; lifecycle is written by services."""

    list_display = (
        "reference_display",
        "title",
        "moderation_status",
        "status",
        "seller",
        "emirate",
        "service_area",
        "start_at",
        "end_at",
        "top_bid_display",
        "review_link",
    )
    list_filter = (
        "moderation_status",
        "status",
        "emirate",
        "condition",
        "created_at",
    )
    search_fields = (
        "id",
        "title",
        "seller__phone",
        "seller__display_name",
        "selected_bidder__phone",
    )
    list_select_related = (
        "seller",
        "emirate",
        "service_area",
        "category",
        "selected_bidder",
        "awarded_deal",
    )
    date_hierarchy = "created_at"
    readonly_fields = (
        "seller",
        "title",
        "description",
        "category",
        "condition",
        "emirate",
        "service_area",
        "start_at",
        "end_at",
        "reserve_price",
        "status",
        "moderation_status",
        "moderation_note",
        "submitted_at",
        "approved_at",
        "approved_by",
        "seller_agreed_fallback",
        "selected_bid",
        "selected_bidder",
        "confirmation_deadline",
        "awarded_deal",
        "awarded_at",
        "outcome_note",
        "review_link",
        "gallery",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            "Auction item",
            {
                "fields": (
                    "title",
                    "description",
                    "category",
                    "condition",
                )
            },
        ),
        ("Location", {"fields": ("emirate", "service_area")}),
        (
            "Schedule",
            {
                "fields": (
                    "start_at",
                    "end_at",
                    "reserve_price",
                    "status",
                    "seller_agreed_fallback",
                )
            },
        ),
        (
            "Moderation",
            {
                "fields": (
                    "moderation_status",
                    "moderation_note",
                    "submitted_at",
                    "approved_at",
                    "approved_by",
                    "review_link",
                )
            },
        ),
        ("Seller", {"fields": ("seller",)}),
        ("Media", {"fields": ("gallery",)}),
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
    inlines = (AuctionImageInline, BidInline, AuctionSelectionInline, AuctionEventInline)
    actions = ("approve_selected",)

    def get_queryset(self, request):
        queryset = super().get_queryset(request).annotate(top_bid=Max("bids__amount"))
        if "moderation_status__exact" not in request.GET:
            queryset = queryset.annotate(
                _queue=Case(
                    When(
                        moderation_status=Auction.ModerationStatus.PENDING,
                        then=Value(0),
                    ),
                    When(
                        moderation_status=Auction.ModerationStatus.REJECTED,
                        then=Value(1),
                    ),
                    default=Value(2),
                    output_field=IntegerField(),
                )
            ).order_by("_queue", "submitted_at", "id")
        return queryset

    @admin.display(description="Reference")
    def reference_display(self, obj):
        return obj.reference

    @admin.display(description="Highest bid", ordering="top_bid")
    def top_bid_display(self, obj):
        if obj.top_bid is None:
            return "—"
        return f"AED {obj.top_bid}"

    @admin.display(description="Photos")
    def gallery(self, obj):
        images = list(obj.images.all()[:6])
        if not images:
            return "No photos"
        return format_html(
            " ".join(
                '<a href="{url}" target="_blank"><img src="{url}" '
                'style="height:70px;border-radius:6px;margin:2px"></a>'.format(
                    url=image.image.url
                )
                for image in images
            )
        )

    @admin.display(description="Review")
    def review_link(self, obj):
        if obj is None or obj.pk is None:
            return "Save first"
        url = reverse("admin:moderation_auction_review", args=[obj.pk])
        return format_html('<a class="button" href="{}">Open review screen</a>', url)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Approve selected auctions")
    def approve_selected(self, request, queryset):
        approved = skipped = 0
        for auction in queryset:
            try:
                auction_services.approve_auction(
                    auction, reviewer=request.user, request=request
                )
                approved += 1
            except (ValidationError, StateTransitionError) as exc:
                skipped += 1
                self.message_user(
                    request,
                    f"{auction.reference}: {exc}",
                    messages.WARNING,
                )
        self.message_user(
            request,
            f"{approved} auction(s) approved, {skipped} skipped.",
            messages.SUCCESS if approved else messages.WARNING,
        )

    # -- custom review screen ---------------------------------------------
    def get_urls(self):
        custom = [
            path(
                "<int:auction_id>/review/",
                self.admin_site.admin_view(self.review_view),
                name="moderation_auction_review",
            ),
        ]
        return custom + super().get_urls()

    def review_view(self, request, auction_id: int):
        if not self.has_change_permission(request):
            raise PermissionDenied

        auction = get_object_or_404(
            Auction.objects.select_related(
                "seller", "emirate", "service_area", "category"
            ),
            pk=auction_id,
        )
        form = ModerationDecisionForm(request.POST or None)

        if request.method == "POST" and form.is_valid():
            decision = form.cleaned_data["decision"]
            reason = form.cleaned_data.get("reason", "")
            try:
                if decision == "approve":
                    auction_services.approve_auction(
                        auction, reviewer=request.user, request=request
                    )
                else:
                    auction_services.reject_auction(
                        auction,
                        reviewer=request.user,
                        reason=reason,
                        request=request,
                    )
            except (ValidationError, StateTransitionError) as exc:
                messages.error(request, f"Could not update the auction: {exc}")
            else:
                messages.success(
                    request,
                    f"'{auction.title}' was "
                    f"{'approved' if decision == 'approve' else 'rejected'}.",
                )
                return redirect(reverse("admin:moderation_auction_changelist"))

        context = {
            **self.admin_site.each_context(request),
            "title": f"Review auction: {auction.title}",
            "object": auction,
            "object_kind": "auction",
            "object_label": auction.title,
            "seller": auction.seller,
            "location": f"{auction.service_area.name}, {auction.emirate.name}",
            "meta": (
                f"{auction.category.name} · {auction.get_condition_display()} · "
                f"{auction.get_moderation_status_display()} · "
                f"{auction.get_status_display()}"
            ),
            "description": auction.description,
            "images": auction.images.all(),
            "reviews": auction.reviews.select_related("reviewer")[:10],
            "changelist_url": reverse("admin:moderation_auction_changelist"),
            "form": form,
            "opts": self.model._meta,
        }
        return render(request, "admin/moderation/review.html", context)
