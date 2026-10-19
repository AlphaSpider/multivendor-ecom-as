"""
WebSocket URL routing for Django Channels.

Only the buyer/seller conversation sockets exist for now; every consumer enforces
participant authorisation itself (routing patterns are not a security boundary).
"""

from django.urls import path

from conversations.consumers import ConversationConsumer

websocket_urlpatterns = [
    path("ws/conversations/<int:conversation_id>/", ConversationConsumer.as_asgi()),
]
