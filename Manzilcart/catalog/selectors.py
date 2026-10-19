"""
Read queries for the public catalog.

Every public query starts from ``Listing.objects.public()``, so an unapproved or
archived listing can never leak into browse, search, category counts or the sitemap.
"""

from __future__ import annotations

from django.db.models import Count, Q

from catalog.models import Category, Listing


def public_listings(emirate):
    """Approved, published listings in ``emirate``."""
    return (
        Listing.objects.public()
        .filter(emirate=emirate)
        .select_related("category", "service_area", "emirate")
        .prefetch_related("images")
    )


def featured_listings(emirate, *, limit: int = 8):
    """Newest approved listings, used on the marketplace home."""
    return public_listings(emirate).order_by("-published_at")[:limit]


def marketplace_listing_stats(emirate) -> dict:
    """Small headline numbers for the marketplace home."""
    base = Listing.objects.public().filter(emirate=emirate)
    return {
        "total": base.count(),
        "categories": base.values("category_id").distinct().count(),
    }


def active_categories():
    """Top-level active categories with their active children."""
    return (
        Category.objects.filter(is_active=True, parent__isnull=True)
        .prefetch_related("children")
        .order_by("display_order", "name")
    )


def category_by_slug(slug: str | None) -> Category | None:
    if not slug:
        return None
    return Category.objects.filter(slug=slug, is_active=True).first()


def search_listings(
    *,
    emirate,
    query: str = "",
    category: Category | None = None,
    condition: str = "",
    min_price=None,
    max_price=None,
    sort: str = "newest",
):
    """
    Emirate-scoped search.

    The emirate filter is applied first and unconditionally; filters and sorting are
    refinements on top of it, never a way around it.
    """
    queryset = public_listings(emirate)

    if query:
        queryset = queryset.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(category__name__icontains=query)
        )

    if category is not None:
        # Selecting a parent category includes its children.
        queryset = queryset.filter(
            Q(category=category) | Q(category__parent=category)
        )

    if condition:
        queryset = queryset.filter(condition=condition)

    if min_price is not None:
        queryset = queryset.filter(asking_price__gte=min_price)
    if max_price is not None:
        queryset = queryset.filter(asking_price__lte=max_price)

    ordering = {
        "newest": ["-published_at", "-id"],
        "oldest": ["published_at", "id"],
        "price_low": ["asking_price", "-published_at"],
        "price_high": ["-asking_price", "-published_at"],
    }.get(sort, ["-published_at", "-id"])

    return queryset.order_by(*ordering)


def listing_by_slug(slug: str):
    """Fetch a listing by slug; visibility is decided by the caller."""
    return (
        Listing.objects.filter(slug=slug)
        .select_related("seller", "category", "emirate", "service_area")
        .prefetch_related("images")
        .first()
    )


def seller_listings(user):
    """Everything the member has listed, including drafts and rejected items."""
    return (
        Listing.objects.filter(seller=user)
        .select_related("category", "emirate", "service_area")
        .prefetch_related("images")
        .order_by("-created_at")
    )


def category_listing_counts(emirate):
    """Approved listing counts per top-level category, for navigation."""
    rows = (
        Listing.objects.public()
        .filter(emirate=emirate)
        .values("category_id", "category__parent_id")
        .annotate(total=Count("id"))
    )
    counts: dict[int, int] = {}
    for row in rows:
        key = row["category__parent_id"] or row["category_id"]
        counts[key] = counts.get(key, 0) + row["total"]
    return counts
