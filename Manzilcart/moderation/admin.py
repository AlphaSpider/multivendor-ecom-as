"""
Listing moderation in Django Admin.

The specification asks for a *small* back office, not a separate moderation product.
This module therefore adds:

* a moderation queue (pending listings first),
* bulk approval,
* a per-listing review screen where a rejection reason can be written,
* an append-only ``ListingReview`` row plus an ``AuditEvent`` for every decision.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html

from catalog import services
from catalog.models import Listing
from core.models import AuditEvent
from moderation.forms import ModerationDecisionForm
from moderation.models import ListingReview


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
        ("Publication", {
            "fields": (
                "status",
                "moderation_note",
                "submitted_at",
                "approved_at",
                "approved_by",
                "published_at",
                "reserved_at",
            )
        }),
        ("Seller", {"fields": ("seller", "slug")}),
        ("Media", {"fields": ("gallery",)}),
        ("Review", {"fields": ("review_link",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
    actions = ("approve_selected",)

    # -- permissions -------------------------------------------------------
    def has_add_permission(self, request):
        # Listings are created by members, not typed into the admin.
        return False

    def has_delete_permission(self, request, obj=None):
        # Deals and audit history reference listings; archive instead.
        return False

    def get_queryset(self, request):
        from django.db.models import Case, IntegerField, Value, When

        queryset = super().get_queryset(request)
        if "status__exact" not in request.GET:
            # Default view is the queue operators actually work from: pending first,
            # then rejected (to be re-reviewed), then everything else.
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
        from core.exceptions import StateTransitionError

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
        # ``admin_view`` only proves the user is staff. Moderation changes a listing,
        # so require the model change permission as well (a view-only staff account
        # must not be able to approve or reject listings through this custom URL).
        if not self.has_change_permission(request):
            raise PermissionDenied

        listing = get_object_or_404(
            Listing.objects.select_related("seller", "emirate", "service_area"),
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
                return redirect(
                    reverse("admin:catalog_listing_changelist")
                )

        context = {
            **self.admin_site.each_context(request),
            "title": f"Review: {listing.title}",
            "listing": listing,
            "form": form,
            "reviews": listing.reviews.select_related("reviewer")[:10],
            "opts": self.model._meta,
        }
        return render(request, "admin/moderation/review.html", context)
