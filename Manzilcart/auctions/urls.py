"""Personal and token-based auction routes, mounted at the site root."""

from django.urls import path

from auctions import views

app_name = "auctions"

urlpatterns = [
    path("my/auctions/", views.MyAuctionsView.as_view(), name="mine"),
    path(
        "my/auctions/applications/",
        views.MyApplicationsView.as_view(),
        name="applications",
    ),
    path(
        "my/auctions/applications/<int:pk>/",
        views.AuctionApplicationDetailView.as_view(),
        name="application_detail",
    ),
    path(
        "my/auctions/applications/<int:pk>/edit/",
        views.AuctionApplicationUpdateView.as_view(),
        name="application_edit",
    ),
    path(
        "my/auctions/applications/<int:pk>/withdraw/",
        views.AuctionApplicationWithdrawView.as_view(),
        name="application_withdraw",
    ),
    path(
        "my/auctions/selection/<str:token>/",
        views.AuctionSelectionView.as_view(),
        name="selection",
    ),
]
