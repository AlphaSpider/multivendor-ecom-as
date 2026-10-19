"""Personal conversation routes, mounted at the site root (not emirate-prefixed)."""

from django.urls import path

from conversations import views

app_name = "conversations"

urlpatterns = [
    path("my/conversations/", views.ConversationListView.as_view(), name="list"),
    path(
        "my/conversations/start/<int:listing_id>/",
        views.StartConversationView.as_view(),
        name="start",
    ),
    path(
        "my/conversations/<int:pk>/",
        views.ConversationDetailView.as_view(),
        name="detail",
    ),
    path(
        "my/conversations/<int:pk>/send/",
        views.SendMessageView.as_view(),
        name="send",
    ),
]
