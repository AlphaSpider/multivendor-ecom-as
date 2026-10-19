"""Catalog administration: categories and image inspection."""

from __future__ import annotations

from django.contrib import admin

from catalog.models import Category, ListingImage


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "parent", "slug", "is_active", "display_order", "listing_count")
    list_editable = ("is_active", "display_order")
    list_filter = ("is_active", "parent")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("parent",)

    @admin.display(description="Listings")
    def listing_count(self, obj):
        return obj.listings.count()

    def has_delete_permission(self, request, obj=None):
        # Listings reference categories with PROTECT; hide delete to avoid confusion.
        return False


@admin.register(ListingImage)
class ListingImageAdmin(admin.ModelAdmin):
    """Read-only inspection of uploaded images (moderation evidence)."""

    list_display = ("listing", "is_cover", "display_order", "width", "height", "byte_size", "created_at")
    list_filter = ("is_cover", "created_at")
    search_fields = ("listing__title", "listing__slug")
    list_select_related = ("listing",)
    readonly_fields = [field.name for field in ListingImage._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
