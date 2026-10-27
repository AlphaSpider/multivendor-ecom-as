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
from django.db import transaction
from django.db.models import (
    Case,
    Count,
    F,
    IntegerField,
    Max,
    Prefetch,
    Q,
    Subquery,
    TextField,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html, mark_safe

from auctions import services as auction_services
from auctions.admin import AuctionEventInline, AuctionSelectionInline, BidInline
from auctions.models import Auction, AuctionImage
from catalog.models import Listing, ListingImage
from core.exceptions import StateTransitionError
from governance import services as governance_services
from governance.admin import CustomerChatAdminMixin
from governance.models import ListingChatMessage
from moderation.forms import ModerationDecisionForm


def _cover_thumbnail(images) -> str:
    """Small clickable cover photo for changelist rows ('no N+1' via prefetch)."""
    images = list(images)
    if not images:
        return "—"
    cover = next((image for image in images if image.is_cover), images[0])
    return format_html(
        '<a href="{url}" target="_blank" title="Open full size">'
        '<img class="gov-thumb" src="{url}" alt=""></a>',
        url=cover.image.url,
    )


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
class ListingAdmin(CustomerChatAdminMixin, admin.ModelAdmin):
    list_display = (
        "photo_preview",
        "title",
        "status",
        "seller",
        "emirate",
        "service_area",
        "asking_price",
        "submitted_at",
        "published_at",
        "chat_badge",
        "review_link",
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
        "reviewed_by",
        "reviewed_at",
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
        (
            "Governance",
            {
                "fields": (
                    "admin_notes",
                    "reviewed_by",
                    "reviewed_at",
                    "review_link",
                )
            },
        ),
        ("Seller", {"fields": ("seller", "slug")}),
        ("Media", {"fields": ("gallery",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
    actions = ("approve_selected", "request_changes_selected", "suspend_selected")

    # -- permissions -------------------------------------------------------
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        queryset = (
            super()
            .get_queryset(request)
            .annotate(
                # Single thread per listing (generic FK): correlated subquery,
                # not a join — keeps the changelist free of N+1 queries.
                unread_chat=governance_services.unread_staff_replies_subquery(
                    governance_services.content_type_for("listing")
                ),
            )
            .prefetch_related(
                Prefetch(
                    "images",
                    queryset=ListingImage.objects.order_by(
                        "-is_cover", "display_order", "id"
                    ),
                )
            )
        )
        if "status__exact" not in request.GET:
            queryset = queryset.annotate(
                _queue=Case(
                    When(status=Listing.Status.PENDING, then=Value(0)),
                    When(status=Listing.Status.REJECTED, then=Value(1)),
                    When(
                        status=Listing.Status.CHANGES_REQUESTED,
                        then=Value(1),
                    ),
                    default=Value(2),
                    output_field=IntegerField(),
                )
            ).order_by("_queue", "submitted_at", "id")
        return queryset

    @admin.display(description="Chat", ordering="unread_chat")
    def chat_badge(self, obj):
        if obj.unread_chat:
            return format_html(
                '<span class="governance-badge governance-badge-unread" title="'
                '{} unread customer message(s)">💬 {}</span>',
                obj.unread_chat,
                obj.unread_chat,
            )
        return "—"

    # -- columns -----------------------------------------------------------
    @admin.display(description="Photos")
    def gallery(self, obj):
        images = list(obj.images.all()[:6])
        if not images:
            return "No photos"
        cells = [
            format_html(
                '<a href="{}" target="_blank"><img src="{}" '
                'style="height:70px;border-radius:6px;margin:2px"></a>',
                image.image.url,
                image.image.url,
            )
            for image in images
        ]
        return mark_safe(" ".join(cells))

    @admin.display(description="Preview")
    def photo_preview(self, obj):
        return _cover_thumbnail(obj.images.all())

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
            try:
                with transaction.atomic():
                    governance_services.approve_listing(
                        listing,
                        reviewer=request.user,
                        request=request,
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

    # -- bulk request-changes / suspend via an intermediate page -----------
    @admin.action(description="Request changes for selected listings")
    def request_changes_selected(self, request, queryset):
        return self._redirect_to_decide(request, queryset, "request_changes")

    @admin.action(description="Suspend selected listings (delist immediately)")
    def suspend_selected(self, request, queryset):
        return self._redirect_to_decide(request, queryset, "suspend")

    def _redirect_to_decide(self, request, queryset, mode: str):
        ids = list(queryset.values_list("pk", flat=True))
        from urllib.parse import urlencode

        return redirect(
            reverse("admin:governance_listing_decide") + "?" + urlencode({"ids": ids, "mode": mode})
        )

    def decide_view(self, request):
        if not self.has_change_permission(request):
            raise PermissionDenied
        ids = [value for value in (request.GET or request.POST).get("ids", "").split(",") if value]
        mode = request.GET.get("mode") or request.POST.get("mode") or "request_changes"
        listings = Listing.objects.filter(pk__in=[int(pk) for pk in ids]).select_related(
            "seller", "emirate", "service_area", "category"
        )

        if request.method == "POST":
            note = (request.POST.get("note") or "").strip()
            applied = skipped = 0
            try:
                for listing in listings:
                    try:
                        with transaction.atomic():
                            if mode == "suspend":
                                governance_services.suspend_item(
                                    listing,
                                    reviewer=request.user,
                                    reason=note,
                                    request=request,
                                )
                            else:
                                governance_services.request_changes(
                                    listing,
                                    reviewer=request.user,
                                    note=note,
                                    request=request,
                                )
                        applied += 1
                    except (ValidationError, StateTransitionError) as exc:
                        skipped += 1
                        self.message_user(request, f"{listing.title}: {exc}", messages.WARNING)
            finally:
                self.message_user(
                    request,
                    f"{applied} listing(s) updated, {skipped} skipped.",
                    messages.SUCCESS if applied else messages.WARNING,
                )
            return redirect(reverse("admin:catalog_listing_changelist"))

        context = {
            **self.admin_site.each_context(request),
            "title": (
                "Request changes from sellers"
                if mode == "request_changes"
                else "Suspend listings"
            ),
            "mode": mode,
            "listings": listings,
            "listing_count": listings.count(),
            "opts": self.model._meta,
        }
        return render(request, "admin/governance/bulk_decide.html", context)

    # -- custom review screen ---------------------------------------------
    def get_urls(self):
        custom = [
            path(
                "<int:listing_id>/review/",
                self.admin_site.admin_view(self.review_view),
                name="moderation_review",
            ),
            path(
                "governance/decide/",
                self.admin_site.admin_view(self.decide_view),
                name="governance_listing_decide",
            ),
        ]
        return custom + super().get_urls()

    # -- governance chat endpoint wiring ------------------------------------
    def _chat_messages_url(self, obj) -> str:
        return reverse(
            "admin:governance_chat_messages_listing", args=[obj.pk]
        )

    def _chat_send_url(self, obj) -> str:
        return reverse("admin:governance_chat_send_listing", args=[obj.pk])

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
            try:
                if decision == "approve":
                    with transaction.atomic():
                        governance_services.approve_listing(
                            listing, reviewer=request.user, request=request
                        )
                elif decision == "request_changes":
                    with transaction.atomic():
                        governance_services.request_changes(
                            listing,
                            reviewer=request.user,
                            note=reason,
                            request=request,
                        )
                elif decision == "suspend":
                    with transaction.atomic():
                        governance_services.suspend_item(
                            listing,
                            reviewer=request.user,
                            reason=reason,
                            request=request,
                        )
                else:
                    with transaction.atomic():
                        governance_services.reject_item(
                            listing,
                            reviewer=request.user,
                            reason=reason,
                            request=request,
                        )
            except (ValidationError, Exception) as exc:  # noqa: BLE001 - surfaced to staff
                messages.error(request, f"Could not update the listing: {exc}")
            else:
                labels = {
                    "approve": "approved",
                    "request_changes": "sent back for changes",
                    "suspend": "suspended",
                    "reject": "rejected",
                }
                messages.success(request, f"'{listing.title}' was {labels[decision]}.")
                return redirect(reverse("admin:catalog_listing_changelist"))

        context = {
            **self.admin_site.each_context(request),
            "title": f"Review listing: {listing.title}",
            "object": listing,
            "object_kind": "listing",
            "object_label": listing.title,
            "seller": listing.seller,
            "seller_admin_url": reverse(
                "admin:accounts_user_change", args=[listing.seller.pk]
            ),
            "photos": list(listing.images.order_by("-is_cover", "display_order", "id")),
            "facts": self._listing_facts(listing),
            "description": listing.description,
            "reviews": listing.reviews.select_related("reviewer")[:10],
            "changelist_url": reverse("admin:catalog_listing_changelist"),
            "form": form,
            "opts": self.model._meta,
            "governance_chat": self._chat_context(listing),
        }
        return render(request, "admin/moderation/review.html", context)

    @staticmethod
    def _listing_facts(listing) -> list[dict]:
        from django.utils import timezone

        return [
            {"label": "Status", "value": listing.get_status_display(), "pill": listing.status},
            {"label": "Reference", "value": f"Listing #{listing.pk}"},
            {"label": "Category", "value": listing.category.name},
            {"label": "Condition", "value": listing.get_condition_display()},
            {"label": "Asking price", "value": f"AED {listing.asking_price:,.2f}"},
            {
                "label": "Location",
                "value": f"{listing.service_area.name}, {listing.emirate.name}",
            },
            {
                "label": "Submitted",
                "value": (
                    listing.submitted_at
                    and timezone.localtime(listing.submitted_at).strftime("%d %b %Y %H:%M")
                )
                or "—",
            },
            {
                "label": "Published",
                "value": (
                    listing.published_at
                    and timezone.localtime(listing.published_at).strftime("%d %b %Y %H:%M")
                )
                or "—",
            },
        ]

    def _chat_context(self, listing):
        thread = governance_services.get_thread(listing)
        return {
            "thread": thread,
            "messages": thread.messages.select_related("sender")[:100],
            "messages_url": self._chat_messages_url(listing),
            "send_url": self._chat_send_url(listing),
        }


@admin.register(Auction)
class AuctionAdmin(CustomerChatAdminMixin, admin.ModelAdmin):
    """Moderation-first auction back office; lifecycle is written by services."""

    list_display = (
        "photo_preview",
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
        "chat_badge",
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
        "reviewed_by",
        "reviewed_at",
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
        (
            "Governance",
            {
                "fields": (
                    "admin_notes",
                    "reviewed_by",
                    "reviewed_at",
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
    actions = ("approve_selected", "request_changes_selected", "suspend_selected")

    def get_queryset(self, request):
        queryset = (
            super()
            .get_queryset(request)
            .annotate(
                # Voided bids must not vote for "highest" on the audit list.
                top_bid=Max("bids__amount", filter=Q(bids__voided_at__isnull=True)),
                unread_chat=governance_services.unread_staff_replies_subquery(
                    governance_services.content_type_for("auction")
                ),
            )
            .prefetch_related(
                Prefetch(
                    "images",
                    queryset=AuctionImage.objects.order_by(
                        "-is_cover", "display_order", "id"
                    ),
                )
            )
        )
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
                    When(
                        moderation_status=Auction.ModerationStatus.CHANGES_REQUESTED,
                        then=Value(1),
                    ),
                    default=Value(2),
                    output_field=IntegerField(),
                )
            ).order_by("_queue", "submitted_at", "id")
        return queryset

    @admin.display(description="Chat", ordering="unread_chat")
    def chat_badge(self, obj):
        if obj.unread_chat:
            return format_html(
                '<span class="governance-badge governance-badge-unread" title="'
                '{} unread customer message(s)">💬 {}</span>',
                obj.unread_chat,
                obj.unread_chat,
            )
        return "—"

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
        cells = [
            format_html(
                '<a href="{}" target="_blank"><img src="{}" '
                'style="height:70px;border-radius:6px;margin:2px"></a>',
                image.image.url,
                image.image.url,
            )
            for image in images
        ]
        return mark_safe(" ".join(cells))

    @admin.display(description="Preview")
    def photo_preview(self, obj):
        return _cover_thumbnail(obj.images.all())

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
                with transaction.atomic():
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

    @admin.action(description="Request changes for selected auctions")
    def request_changes_selected(self, request, queryset):
        return self._redirect_to_decide(request, queryset, "request_changes")

    @admin.action(description="Suspend selected auctions (delist immediately)")
    def suspend_selected(self, request, queryset):
        return self._redirect_to_decide(request, queryset, "suspend")

    def _redirect_to_decide(self, request, queryset, mode: str):
        ids = list(queryset.values_list("pk", flat=True))
        from urllib.parse import urlencode

        return redirect(
            reverse("admin:governance_auction_decide")
            + "?"
            + urlencode({"ids": ids, "mode": mode})
        )

    def decide_view(self, request):
        if not self.has_change_permission(request):
            raise PermissionDenied
        ids = [value for value in (request.GET or request.POST).get("ids", "").split(",") if value]
        mode = request.GET.get("mode") or request.POST.get("mode") or "request_changes"
        auctions = Auction.objects.filter(pk__in=[int(pk) for pk in ids]).select_related(
            "seller", "emirate", "service_area", "category"
        )

        if request.method == "POST":
            note = (request.POST.get("note") or "").strip()
            applied = skipped = 0
            for auction in auctions:
                try:
                    with transaction.atomic():
                        if mode == "suspend":
                            governance_services.suspend_item(
                                auction,
                                reviewer=request.user,
                                reason=note,
                                request=request,
                            )
                        else:
                            governance_services.request_changes(
                                auction,
                                reviewer=request.user,
                                note=note,
                                request=request,
                            )
                    applied += 1
                except (ValidationError, StateTransitionError) as exc:
                    skipped += 1
                    self.message_user(request, f"{auction.reference}: {exc}", messages.WARNING)
            self.message_user(
                request,
                f"{applied} auction(s) updated, {skipped} skipped.",
                messages.SUCCESS if applied else messages.WARNING,
            )
            return redirect(reverse("admin:auctions_auction_changelist"))

        context = {
            **self.admin_site.each_context(request),
            "title": (
                "Request changes from sellers"
                if mode == "request_changes"
                else "Suspend auctions"
            ),
            "mode": mode,
            "listings": auctions,
            "listing_count": auctions.count(),
            "is_auction": True,
            "opts": self.model._meta,
        }
        return render(request, "admin/governance/bulk_decide.html", context)

    # -- governance chat endpoint wiring ------------------------------------
    def _chat_messages_url(self, obj) -> str:
        return reverse("admin:governance_chat_messages_auction", args=[obj.pk])

    def _chat_send_url(self, obj) -> str:
        return reverse("admin:governance_chat_send_auction", args=[obj.pk])

    # -- custom review screen ---------------------------------------------
    def get_urls(self):
        custom = [
            path(
                "<int:auction_id>/review/",
                self.admin_site.admin_view(self.review_view),
                name="moderation_auction_review",
            ),
            path(
                "governance/decide/",
                self.admin_site.admin_view(self.decide_view),
                name="governance_auction_decide",
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
                elif decision == "request_changes":
                    with transaction.atomic():
                        governance_services.request_changes(
                            auction,
                            reviewer=request.user,
                            note=reason,
                            request=request,
                        )
                elif decision == "suspend":
                    with transaction.atomic():
                        governance_services.suspend_item(
                            auction,
                            reviewer=request.user,
                            reason=reason,
                            request=request,
                        )
                else:
                    with transaction.atomic():
                        governance_services.reject_item(
                            auction,
                            reviewer=request.user,
                            reason=reason,
                            request=request,
                        )
            except (ValidationError, StateTransitionError) as exc:
                messages.error(request, f"Could not update the auction: {exc}")
            else:
                labels = {
                    "approve": "approved",
                    "request_changes": "sent back for changes",
                    "suspend": "suspended",
                    "reject": "rejected",
                }
                messages.success(
                    request,
                    f"'{auction.title}' was {labels[decision]}.",
                )
                return redirect(reverse("admin:auctions_auction_changelist"))

        context = {
            **self.admin_site.each_context(request),
            "title": f"Review auction: {auction.title}",
            "object": auction,
            "object_kind": "auction",
            "object_label": auction.title,
            "seller": auction.seller,
            "seller_admin_url": reverse(
                "admin:accounts_user_change", args=[auction.seller.pk]
            ),
            "photos": list(auction.images.order_by("-is_cover", "display_order", "id")),
            "facts": self._auction_facts(auction),
            "description": auction.description,
            "reviews": auction.reviews.select_related("reviewer")[:10],
            "changelist_url": reverse("admin:auctions_auction_changelist"),
            "form": form,
            "opts": self.model._meta,
            "governance_chat": self._chat_context(auction),
        }
        return render(request, "admin/moderation/review.html", context)

    @staticmethod
    def _auction_facts(auction) -> list[dict]:
        from django.utils import timezone

        top_bidder_stats = auction.bids.filter(voided_at__isnull=False).aggregate(
            count=Count("id"),
            top=Max("amount"),
        )
        top_bid = (
            f"AED {top_bidder_stats['top']:,.2f} ({top_bidder_stats['count']} valid bid"
            f"{'s' if top_bidder_stats['count'] != 1 else ''})"
            if top_bidder_stats["top"] is not None
            else f"No valid bids yet (minimum AED {auction.reserve_price:,.2f})"
        )
        fmt = "%d %b %Y %H:%M"
        return [
            {
                "label": "Review status",
                "value": auction.get_moderation_status_display(),
                "pill": auction.moderation_status,
            },
            {"label": "Reference", "value": auction.reference},
            {"label": "Category", "value": auction.category.name},
            {"label": "Condition", "value": auction.get_condition_display()},
            {
                "label": "Bidding opens",
                "value": timezone.localtime(auction.start_at).strftime(fmt),
            },
            {
                "label": "Bidding ends",
                "value": timezone.localtime(auction.end_at).strftime(fmt),
            },
            {"label": "Minimum bid", "value": f"AED {auction.reserve_price:,.2f}"},
            {"label": "Bids so far", "value": top_bid},
            {
                "label": "Location",
                "value": f"{auction.service_area.name}, {auction.emirate.name}",
            },
            {
                "label": "Submitted",
                "value": (
                    auction.submitted_at
                    and timezone.localtime(auction.submitted_at).strftime(fmt)
                )
                or "—",
            },
        ]

    def _chat_context(self, auction):
        thread = governance_services.get_thread(auction)
        return {
            "thread": thread,
            "messages": thread.messages.select_related("sender")[:100],
            "messages_url": self._chat_messages_url(auction),
            "send_url": self._chat_send_url(auction),
        }
