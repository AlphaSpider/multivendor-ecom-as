"""Personal and token-based auction routes, mounted at the site root."""

from django.urls import path

from auctions import views

app_name = "auctions"

urlpatterns = [
    path("my/auctions/", views.MyAuctionsView.as_view(), name="mine"),
    path(
        "my/auctions/selection/<str:token>/",
        views.AuctionSelectionView.as_view(),
        name="selection",
    ),
]
