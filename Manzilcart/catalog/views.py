"""
Marketplace listing views.

Visibility rules are enforced here, server side:

* public browse/search only ever sees ``approved`` listings in the URL's emirate;
* a detail page is visible to anyone when the listing is public, and otherwise only to
  its owner and staff (a 404 is returned for everyone else so the existence of a draft
  is not disclosed);
* a listing is only reachable under its own emirate slug, which prevents a "wrong
  emirate" URL from leaking it into another marketplace context.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db.models import OuterRef, Q, Subquery
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import TemplateView

from accounts.eligibility import SellerEligibleMixin
from catalog import services
from catalog.forms import ListingForm, ListingSearchForm
from catalog.models import Category, Listing, ListingImage
from catalog.selectors import (
    active_categories,
    category_by_slug,
    category_listing_counts,
    is_wishlisted,
    listing_by_slug,
    search_listings,
    seller_listings,
    similar_listings,
    wishlist_items,
    wishlist_listing_ids,
)
from core.exceptions import PermissionDeniedError, StateTransitionError
from locations.mixins import EmirateRequiredMixin

logger = logging.getLogger("manzilcart.catalog.views")


def _apply_service_errors(form, exc: ValidationError) -> None:
    """Map a service-layer ValidationError onto the right form fields."""
    if hasattr(exc, "message_dict"):
        for field, errors in exc.message_dict.items():
            for error in errors:
                form.add_error(field if field in form.fields else None, error)
    else:
        form.add_error(None, " ".join(exc.messages))


def _attach_category_counts(categories, emirate):
    counts = category_listing_counts(emirate)
    for category in categories:
        category.listing_count = counts.get(category.pk, 0)
    return categories


def _sample_category_image_subquery(emirate=None):
    """Choose one public listing image to illustrate a category."""
    images = ListingImage.objects.filter(
        listing__category=OuterRef("pk"),
        listing__status=Listing.Status.APPROVED,
        listing__published_at__isnull=False,
    ).exclude(image="")
    if emirate is not None:
        images = images.filter(listing__emirate=emirate)
    return images.order_by(
        "-is_cover", "-listing__published_at", "display_order", "id"
    ).values("image")[:1]


class SearchSuggestionsView(EmirateRequiredMixin, View):
    """JSON category suggestions for the marketplace search boxes."""

    def get(self, request, *args, **kwargs):
        query = (request.GET.get("q") or "").strip()[:80]
        if not query:
            return JsonResponse({"suggestions": []})

        categories = Category.objects.filter(is_active=True)
        prefix_matches = categories.filter(name__istartswith=query)
        if not prefix_matches.exists():
            prefix_matches = categories.filter(name__icontains=query)
        categories = (
            prefix_matches.annotate(
                sample_image_local=Subquery(
                    _sample_category_image_subquery(self.emirate)
                ),
                sample_image_any=Subquery(_sample_category_image_subquery()),
            )
            .order_by("display_order", "name")[:8]
        )

        suggestions = []
        list_url = reverse(
            "catalog:listing_list",
            kwargs={"emirate_slug": self.emirate.slug},
        )
        for category in categories:
            sample_image = category.sample_image_local or category.sample_image_any
            suggestions.append(
                {
                    "name": category.name,
                    "url": f"{list_url}?category={category.slug}",
                    "image_url": (
                        default_storage.url(sample_image) if sample_image else ""
                    ),
                }
            )
        return JsonResponse({"suggestions": suggestions})


class ListingListView(EmirateRequiredMixin, TemplateView):
    """Emirate-scoped browse, search, filter and pagination."""

    template_name = "catalog/listing_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        request = self.request
        form = ListingSearchForm(request.GET or None)
        form.is_valid()  # invalid values simply fall back to "no filter"

        data = form.cleaned_data if form.is_valid() else {}
        category = category_by_slug(data.get("category"))

        queryset = search_listings(
            emirate=self.emirate,
            query=data.get("q", ""),
            category=category,
            condition=data.get("condition", ""),
            min_price=data.get("min_price"),
            max_price=data.get("max_price"),
            sort=data.get("sort") or "newest",
        )

        paginator = Paginator(queryset, settings.LISTINGS_PER_PAGE)
        page = paginator.get_page(request.GET.get("page"))

        categories = _attach_category_counts(list(active_categories()), self.emirate)

        modal_filter_count = (
            (1 if category else 0)
            + (1 if data.get("condition") else 0)
            + (1 if data.get("min_price") is not None or data.get("max_price") is not None else 0)
        )
        active_filter_count = modal_filter_count + (1 if data.get("q") else 0)

        context.update(
            {
                "emirate": self.emirate,
                "page_obj": page,
                "listings": page.object_list,
                "wishlist_ids": wishlist_listing_ids(
                    request.user, [listing.pk for listing in page.object_list]
                ),
                "total_results": paginator.count,
                "active_filter_count": active_filter_count,
                "modal_filter_count": modal_filter_count,
                "search_form": ListingSearchForm(
                    initial={
                        "q": data.get("q", ""),
                        "category": category.slug if category else "",
                        "condition": data.get("condition", ""),
                        "min_price": data.get("min_price"),
                        "max_price": data.get("max_price"),
                        "sort": data.get("sort") or "newest",
                    }
                ),
                "selected_category": category,
                "categories": categories,
                "conditions": Listing.Condition.choices,
                "sort_choices": ListingSearchForm().fields["sort"].choices,
                "query": data.get("q", ""),
            }
        )
        return context


class ListingDetailView(EmirateRequiredMixin, TemplateView):
    """Listing detail with ownership-aware visibility."""

    template_name = "catalog/listing_detail.html"

    def get(self, request, *args, **kwargs):
        listing = listing_by_slug(kwargs.get("slug"))
        if listing is None:
            raise Http404("Listing not found.")

        # The listing must belong to the emirate in the URL.
        if listing.emirate_id != self.emirate.pk:
            raise Http404("Listing not found.")

        if not listing.is_public:
            viewer = request.user
            is_owner = viewer.is_authenticated and viewer.pk == listing.seller_id
            if not (is_owner or viewer.is_staff):
                # Same response as a missing listing: do not disclose drafts.
                raise Http404("Listing not found.")

        # Owners get edit controls; auctions are standalone items and never appear here.
        similar = list(similar_listings(listing)) if listing.is_public else []
        return render(
            request,
            self.template_name,
            {
                "emirate": self.emirate,
                "listing": listing,
                "is_owner": request.user.is_authenticated
                and request.user.pk == listing.seller_id,
                "can_edit": request.user.is_authenticated
                and request.user.pk == listing.seller_id
                and listing.is_editable_by_owner,
                "is_wishlisted": is_wishlisted(request.user, listing),
                "similar_listings": similar,
                "wishlist_ids": wishlist_listing_ids(
                    request.user, [item.pk for item in similar]
                ),
            },
        )


class ListingCreateView(LoginRequiredMixin, SellerEligibleMixin, EmirateRequiredMixin, View):
    """Create a listing in the marketplace the member is browsing."""

    template_name = "catalog/listing_form.html"
    login_url = "accounts:login"

    def get(self, request, *args, **kwargs):
        form = ListingForm(emirate=self.emirate)
        return render(
            request,
            self.template_name,
            {"emirate": self.emirate, "form": form, "mode": "create"},
        )

    def post(self, request, *args, **kwargs):
        from core.images import validate_image_upload

        form = ListingForm(request.POST, request.FILES, emirate=self.emirate)
        images = request.FILES.getlist("images")

        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"emirate": self.emirate, "form": form, "mode": "create"},
                status=400,
            )

        # Validate uploads *before* creating the listing so a bad photo cannot leave an
        # orphan listing (and a consumed slug) behind.
        if len(images) < settings.MIN_IMAGES_PER_LISTING:
            form.add_error(
                "images",
                f"Please add at least {settings.MIN_IMAGES_PER_LISTING} photos of the "
                "item so buyers can judge its condition.",
            )
        else:
            for index, uploaded in enumerate(images, start=1):
                try:
                    validate_image_upload(uploaded)
                except ValidationError as exc:
                    form.add_error("images", f"Photo {index}: {' '.join(exc.messages)}")

        if form.errors:
            return render(
                request,
                self.template_name,
                {"emirate": self.emirate, "form": form, "mode": "create"},
                status=400,
            )

        try:
            listing = services.create_listing(
                seller=request.user,
                title=form.cleaned_data["title"],
                description=form.cleaned_data["description"],
                category=form.cleaned_data["category"],
                condition=form.cleaned_data["condition"],
                asking_price=form.cleaned_data["asking_price"],
                emirate=self.emirate,
                service_area=form.cleaned_data["service_area"],
                submit_for_review=True,
                request=request,
            )

            for uploaded in images:
                services.add_listing_image(listing, uploaded, actor=request.user)
        except ValidationError as exc:
            _apply_service_errors(form, exc)
            return render(
                request,
                self.template_name,
                {"emirate": self.emirate, "form": form, "mode": "create"},
                status=400,
            )
        except PermissionDeniedError:
            raise Http404

        messages.success(
            request,
            "Your listing has been submitted for review. It will appear once approved.",
        )
        return redirect("accounts:listings")


class ListingUpdateView(LoginRequiredMixin, EmirateRequiredMixin, View):
    """Edit a listing the member owns."""

    template_name = "catalog/listing_form.html"
    login_url = "accounts:login"

    def get_listing(self) -> Listing:
        listing = get_object_or_404(
            Listing, slug=self.kwargs["slug"], seller=self.request.user
        )
        if listing.emirate_id != self.emirate.pk:
            raise Http404("Listing not found.")
        return listing

    def get(self, request, *args, **kwargs):
        listing = self.get_listing()
        form = ListingForm(instance=listing, emirate=self.emirate)
        return render(
            request,
            self.template_name,
            {"emirate": self.emirate, "form": form, "listing": listing, "mode": "edit"},
        )

    def post(self, request, *args, **kwargs):
        listing = self.get_listing()
        form = ListingForm(request.POST, request.FILES, instance=listing, emirate=self.emirate)

        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"emirate": self.emirate, "form": form, "listing": listing, "mode": "edit"},
                status=400,
            )

        # An edited draft can keep fewer photos until it is submitted; anything that
        # goes (back) to moderation needs the full set.
        total_photos = listing.images.count() + len(request.FILES.getlist("images"))
        if (
            listing.status != Listing.Status.DRAFT
            and total_photos < settings.MIN_IMAGES_PER_LISTING
        ):
            form.add_error(
                "images",
                f"Please keep at least {settings.MIN_IMAGES_PER_LISTING} photos so "
                "buyers can judge the item's condition.",
            )
            return render(
                request,
                self.template_name,
                {"emirate": self.emirate, "form": form, "listing": listing, "mode": "edit"},
                status=400,
            )

        try:
            services.update_listing(
                listing,
                actor=request.user,
                title=form.cleaned_data["title"],
                description=form.cleaned_data["description"],
                category=form.cleaned_data["category"],
                condition=form.cleaned_data["condition"],
                asking_price=form.cleaned_data["asking_price"],
                service_area=form.cleaned_data["service_area"],
                request=request,
            )
            for uploaded in request.FILES.getlist("images"):
                services.add_listing_image(listing, uploaded, actor=request.user)
        except ValidationError as exc:
            _apply_service_errors(form, exc)
            return render(
                request,
                self.template_name,
                {"emirate": self.emirate, "form": form, "listing": listing, "mode": "edit"},
                status=400,
            )
        except StateTransitionError as exc:
            messages.error(request, exc.message)
            return redirect("accounts:listings")

        messages.success(request, "Your changes have been submitted for review.")
        return redirect("accounts:listings")


class ListingActionView(LoginRequiredMixin, EmirateRequiredMixin, View):
    """Owner actions that are not edits: submit, archive, mark sold."""

    def post(self, request, *args, **kwargs):
        listing = get_object_or_404(
            Listing, slug=self.kwargs["slug"], seller=request.user
        )
        action = request.POST.get("action")

        try:
            if action == "submit":
                services.submit_for_review(listing, actor=request.user, request=request)
                messages.success(request, "Listing submitted for review.")
            elif action == "archive":
                services.archive_listing(listing, actor=request.user, request=request)
                messages.success(request, "Listing archived.")
            elif action == "sold":
                services.mark_sold(listing, actor=request.user, request=request)
                messages.success(request, "Listing marked as sold.")
            else:
                messages.error(request, "Unknown action.")
        except (StateTransitionError, PermissionDeniedError) as exc:
            messages.error(request, exc.message)

        return redirect("accounts:listings")


class MyListingsView(LoginRequiredMixin, TemplateView):
    """All of a member's listings, across marketplaces."""

    template_name = "catalog/my_listings.html"
    login_url = "accounts:login"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        listings = seller_listings(self.request.user)
        context["listings"] = listings
        context["statuses"] = Listing.Status.choices
        return context


class MyWishlistView(LoginRequiredMixin, TemplateView):
    """Products a member has saved from any marketplace."""

    template_name = "catalog/my_wishlist.html"
    login_url = "accounts:login"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        items = list(wishlist_items(self.request.user))
        context["wishlist_items"] = items
        context["wishlist_ids"] = {item.listing_id for item in items}
        context["wishlist_count"] = len(items)
        return context


class WishlistToggleView(LoginRequiredMixin, EmirateRequiredMixin, View):
    """POST-only save/remove action used by product cards and the detail page."""

    login_url = "accounts:login"

    def post(self, request, *args, **kwargs):
        listing = get_object_or_404(
            Listing.objects.select_related("emirate"),
            slug=kwargs["slug"],
            emirate=self.emirate,
        )
        already_saved = is_wishlisted(request.user, listing)
        if not listing.is_public and not already_saved:
            raise Http404("Listing not found.")

        try:
            saved = services.toggle_wishlist(listing, user=request.user)
        except StateTransitionError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(
                request,
                "Saved to your wishlist."
                if saved
                else "Removed from your wishlist.",
            )

        next_url = request.POST.get("next", "")
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            {request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(next_url)
        if listing.is_public:
            return redirect(listing.get_absolute_url())
        return redirect("accounts:wishlist")


class ListingImageView(LoginRequiredMixin, View):
    """Owner-only actions on a single image (cover selection / deletion)."""

    def post(self, request, *args, **kwargs):
        from catalog.models import ListingImage

        image = get_object_or_404(
            ListingImage.objects.select_related("listing"),
            pk=kwargs["image_id"],
            listing__seller=request.user,
        )
        action = request.POST.get("action")

        try:
            if action == "cover":
                services.set_cover_image(image, actor=request.user)
                messages.success(request, "Cover photo updated.")
            elif action == "delete":
                services.delete_listing_image(image, actor=request.user)
                messages.success(request, "Photo removed.")
            else:
                messages.error(request, "Unknown action.")
        except PermissionDeniedError:
            raise Http404

        return redirect(
            "catalog:listing_edit",
            emirate_slug=image.listing.emirate.slug,
            slug=image.listing.slug,
        )
