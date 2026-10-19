"""
WebSocket consumer for a single conversation.

Authorisation is checked here, not in the URL router: the socket only opens when the
session user is one of the conversation's two participants. Reads and writes both go
through the ``conversations`` service layer.

The protocol is intentionally small:

client -> server
    {"type": "message.send", "body": "...", "client_message_id": "<uuid>"}

server -> client
    {"type": "ready", "conversation_id": 1, "user_id": 7}
    {"type": "message", "message": {...}}            # broadcast, including the sender
    {"type": "message.ack", "id": 12, "client_message_id": "...", "created": true}
    {"type": "error", "error": "human readable message"}
"""

from __future__ import annotations

import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.core.exceptions import ValidationError

from conversations import services
from conversations.models import Conversation
from conversations.selectors import conversation_for_participant
from core.exceptions import DomainError

logger = logging.getLogger("manzilcart.conversations.consumer")


class ConversationConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.user = self.scope.get("user")
        self.conversation_id = int(self.scope["url_route"]["kwargs"]["conversation_id"])

        if self.user is None or not self.user.is_authenticated:
            await self.close(code=4401)
            return

        self.conversation = await self._load_conversation(self.conversation_id, self.user)
        if self.conversation is None:
            # Same close code for "does not exist" and "not yours": do not confirm the
            # existence of another user's conversation.
            await self.close(code=4403)
            return

        self.group = services.group_name(self.conversation)
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        await self.send_json(
            {
                "type": "ready",
                "conversation_id": self.conversation.pk,
                "user_id": self.user.pk,
            }
        )

    async def disconnect(self, close_code):
        group = getattr(self, "group", None)
        if group:
            await self.channel_layer.group_discard(group, self.channel_name)

    async def receive_json(self, content, **kwargs):
        if not isinstance(content, dict):
            await self._error("Malformed message.")
            return

        if content.get("type") != "message.send":
            await self._error("Unsupported message type.")
            return

        try:
            message, created = await database_sync_to_async(services.send_message)(
                self.conversation,
                sender=self.user,
                body=content.get("body", ""),
                client_message_id=content.get("client_message_id"),
            )
        except DomainError as exc:
            await self._error(exc.message, content.get("client_message_id"))
            return
        except ValidationError as exc:
            await self._error(" ".join(exc.messages), content.get("client_message_id"))
            return

        await self.send_json(
            {
                "type": "message.ack",
                "id": message.pk,
                "client_message_id": (
                    str(message.client_message_id) if message.client_message_id else None
                ),
                "created": created,
            }
        )

    async def chat_message(self, event):
        """Group event: a committed message was broadcast."""
        await self.send_json({"type": "message", "message": event["message"]})

    async def _error(self, message: str, client_message_id=None):
        payload = {"type": "error", "error": message}
        if client_message_id:
            payload["client_message_id"] = str(client_message_id)
        await self.send_json(payload)

    @staticmethod
    @database_sync_to_async
    def _load_conversation(conversation_id: int, user):
        return conversation_for_participant(conversation_id, user)
