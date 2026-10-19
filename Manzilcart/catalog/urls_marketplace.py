"""Emirate-scoped catalog routes, mounted under ``/<emirate_slug>/listings/``."""

from django.urls import path

from catalog import views

app_name = "catalog"

urlpatterns = [
    path("", views.ListingListView.as_view(), name="listing_list"),
    path("new/", views.ListingCreateView.as_view(), name="listing_create"),
    path("<slug:slug>/", views.ListingDetailView.as_view(), name="listing_detail"),
    path("<slug:slug>/edit/", views.ListingUpdateView.as_view(), name="listing_edit"),
    path("<slug:slug>/action/", views.ListingActionView.as_view(), name="listing_action"),
    path(
        "images/<int:image_id>/action/",
        views.ListingImageView.as_view(),
        name="listing_image_action",
    ),
]
