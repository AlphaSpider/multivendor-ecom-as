"""Emirate-scoped auction routes, mounted under ``/<emirate_slug>/auctions/``."""

from django.urls import path

from auctions import views

app_name = "auction_marketplace"

urlpatterns = [
    path("", views.AuctionListView.as_view(), name="list"),
    path("new/", views.AuctionCreateView.as_view(), name="create"),
    path("<int:pk>/", views.AuctionDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.AuctionUpdateView.as_view(), name="edit"),
    path("<int:pk>/bid/", views.AuctionBidView.as_view(), name="bid"),
    path("<int:pk>/lock/", views.AuctionLockView.as_view(), name="lock"),
    path("<int:pk>/cancel/", views.AuctionCancelView.as_view(), name="cancel"),
]
