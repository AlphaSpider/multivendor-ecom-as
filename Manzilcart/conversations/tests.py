"""
Chat tests: participant authorisation, persistence, idempotency, pagination and the
WebSocket access boundary.

The WebSocket tests use Channels' in-memory layer and a session cookie, which is the
same authentication path a browser uses.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from asgiref.sync import async_to_sync
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import path, reverse
from django.utils import timezone

from catalog.models import Category, Listing
from auctions.models import Auction
from conversations.consumers import ConversationConsumer
from conversations.models import Conversation, Message
from conversations.services import send_message
from core.ws_auth import SessionAuthMiddlewareStack
from locations.models import Emirate, ServiceArea

User = get_user_model()


def make_member(*, phone: str, name: str, emirate, area):
    return User.objects.create_user(
        phone=phone,
        display_name=name,
        emirate=emirate,
        service_area=area,
        phone_verified_at=timezone.now(),
    )


def make_listing(*, seller, emirate, area, category, title="Oak dining table"):
    return Listing.objects.create(
        seller=seller,
        title=title,
        slug=f"oak-dining-table-{uuid.uuid4().hex[:8]}",
        description="Solid oak, seats six.",
        category=category,
        condition=Listing.Condition.GOOD,
        asking_price=Decimal("750.00"),
        emirate=emirate,
        service_area=area,
        status=Listing.Status.APPROVED,
        submitted_at=timezone.now(),
        approved_at=timezone.now(),
        published_at=timezone.now(),
    )


class ConversationHTTPTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emirate = Emirate.objects.get(slug="abu-dhabi")
        cls.area = ServiceArea.objects.filter(emirate=cls.emirate, is_active=True).first()
        cls.category = Category.objects.filter(is_active=True).first()
        cls.seller = make_member(
            phone="+971501000001", name="Seller", emirate=cls.emirate, area=cls.area
        )
        cls.buyer = make_member(
            phone="+971501000002", name="Buyer", emirate=cls.emirate, area=cls.area
        )
        cls.stranger = make_member(
            phone="+971501000003", name="Stranger", emirate=cls.emirate, area=cls.area
        )
        cls.listing = make_listing(
            seller=cls.seller,
            emirate=cls.emirate,
            area=cls.area,
            category=cls.category,
        )

    def test_start_requires_login(self):
        response = self.client.post(
            reverse("conversations:start", args=[self.listing.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])
        self.assertEqual(Conversation.objects.count(), 0)

    def test_seller_cannot_start_conversation_about_own_listing(self):
        self.client.force_login(self.seller)
        response = self.client.post(
            reverse("conversations:start", args=[self.listing.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Conversation.objects.count(), 0)

    def test_start_creates_once_and_reuses(self):
        self.client.force_login(self.buyer)
        url = reverse("conversations:start", args=[self.listing.pk])

        self.client.post(url)
        conversation = Conversation.objects.get()
        self.assertEqual(conversation.buyer, self.buyer)
        self.assertEqual(conversation.seller, self.seller)
        self.assertEqual(conversation.emirate, self.emirate)

        self.client.post(url)
        self.assertEqual(Conversation.objects.count(), 1)

    def test_cannot_start_for_unpublished_listing(self):
        self.listing.status = Listing.Status.PENDING
        self.listing.published_at = None
        self.listing.save(update_fields=["status", "published_at"])
        self.client.force_login(self.buyer)

        self.client.post(reverse("conversations:start", args=[self.listing.pk]))
        self.assertEqual(Conversation.objects.count(), 0)

    def test_participants_only_can_open_thread(self):
        conversation = Conversation.objects.create(
            listing=self.listing,
            buyer=self.buyer,
            seller=self.seller,
            emirate=self.emirate,
        )

        self.client.force_login(self.stranger)
        response = self.client.get(
            reverse("conversations:detail", args=[conversation.pk])
        )
        self.assertEqual(response.status_code, 404)

        self.client.force_login(self.buyer)
        response = self.client.get(
            reverse("conversations:detail", args=[conversation.pk])
        )
        self.assertEqual(response.status_code, 200)

    def test_http_fallback_persists_message_and_marks_it_read(self):
        conversation = Conversation.objects.create(
            listing=self.listing,
            buyer=self.buyer,
            seller=self.seller,
            emirate=self.emirate,
        )

        self.client.force_login(self.buyer)
        self.client.post(
            reverse("conversations:send", args=[conversation.pk]),
            {"body": "Is the table still available?"},
        )
        message = Message.objects.get()
        self.assertEqual(message.sender, self.buyer)
        self.assertEqual(message.body, "Is the table still available?")
        self.assertIsNone(message.read_at)

        self.client.force_login(self.seller)
        self.client.get(reverse("conversations:detail", args=[conversation.pk]))
        message.refresh_from_db()
        self.assertIsNotNone(message.read_at)

    def test_empty_message_is_not_persisted(self):
        conversation = Conversation.objects.create(
            listing=self.listing,
            buyer=self.buyer,
            seller=self.seller,
            emirate=self.emirate,
        )
        self.client.force_login(self.buyer)
        self.client.post(
            reverse("conversations:send", args=[conversation.pk]), {"body": "   "}
        )
        self.assertEqual(Message.objects.count(), 0)

    def test_stranger_cannot_send_into_thread(self):
        conversation = Conversation.objects.create(
            listing=self.listing,
            buyer=self.buyer,
            seller=self.seller,
            emirate=self.emirate,
        )
        self.client.force_login(self.stranger)
        response = self.client.post(
            reverse("conversations:send", args=[conversation.pk]), {"body": "Hello"}
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Message.objects.count(), 0)

    def test_client_message_id_is_idempotent(self):
        conversation = Conversation.objects.create(
            listing=self.listing,
            buyer=self.buyer,
            seller=self.seller,
            emirate=self.emirate,
        )
        client_id = uuid.uuid4()

        first, created_first = send_message(
            conversation,
            sender=self.buyer,
            body="First attempt",
            client_message_id=client_id,
        )
        second, created_second = send_message(
            conversation,
            sender=self.buyer,
            body="Retry after reconnect",
            client_message_id=client_id,
        )

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Message.objects.count(), 1)
        self.assertEqual(Message.objects.get().body, "First attempt")

    @override_settings(CHAT_MESSAGE_MAX_LENGTH=10)
    def test_message_length_is_enforced(self):
        conversation = Conversation.objects.create(
            listing=self.listing,
            buyer=self.buyer,
            seller=self.seller,
            emirate=self.emirate,
        )
        self.client.force_login(self.buyer)
        self.client.post(
            reverse("conversations:send", args=[conversation.pk]),
            {"body": "x" * 11},
        )
        self.assertEqual(Message.objects.count(), 0)

    def test_history_is_paginated(self):
        conversation = Conversation.objects.create(
            listing=self.listing,
            buyer=self.buyer,
            seller=self.seller,
            emirate=self.emirate,
        )
        for index in range(settings.MESSAGES_PER_PAGE + 5):
            Message.objects.create(
                conversation=conversation,
                sender=self.buyer,
                body=f"Message {index}",
            )

        self.client.force_login(self.buyer)
        first = self.client.get(reverse("conversations:detail", args=[conversation.pk]))
        self.assertEqual(len(first.context["messages"]), settings.MESSAGES_PER_PAGE)

        second = self.client.get(
            reverse("conversations:detail", args=[conversation.pk]) + "?page=2"
        )
        self.assertEqual(len(second.context["messages"]), 5)


class ConversationWebSocketTests(TransactionTestCase):
    reset_sequences = True
    # TransactionTestCase flushes every table between tests. Seed geography and
    # categories come from data migrations, so ask the runner to restore them.
    serialized_rollback = True

    def setUp(self):
        self.emirate = Emirate.objects.get(slug="abu-dhabi")
        self.area = ServiceArea.objects.filter(emirate=self.emirate, is_active=True).first()
        self.category = Category.objects.filter(is_active=True).first()
        self.seller = make_member(
            phone="+971502000001", name="Seller", emirate=self.emirate, area=self.area
        )
        self.buyer = make_member(
            phone="+971502000002", name="Buyer", emirate=self.emirate, area=self.area
        )
        self.stranger = make_member(
            phone="+971502000003", name="Stranger", emirate=self.emirate, area=self.area
        )
        self.listing = make_listing(
            seller=self.seller,
            emirate=self.emirate,
            area=self.area,
            category=self.category,
        )
        self.conversation = Conversation.objects.create(
            listing=self.listing,
            buyer=self.buyer,
            seller=self.seller,
            emirate=self.emirate,
        )
        self.application = SessionAuthMiddlewareStack(
            URLRouter(
                [
                    path(
                        "ws/conversations/<int:conversation_id>/",
                        ConversationConsumer.as_asgi(),
                    )
                ]
            )
        )

    def _cookie_header(self, user):
        client = Client()
        client.force_login(user)
        session_key = client.cookies[settings.SESSION_COOKIE_NAME].value
        return [(b"cookie", f"{settings.SESSION_COOKIE_NAME}={session_key}".encode())]

    async def _connect(self, headers):
        communicator = WebsocketCommunicator(
            self.application,
            f"/ws/conversations/{self.conversation.pk}/",
            headers=headers,
        )
        connected, _ = await communicator.connect()
        return communicator, connected

    def test_anonymous_socket_is_rejected(self):
        async def scenario():
            communicator, connected = await self._connect([])
            self.assertFalse(connected)
            await communicator.disconnect()

        async_to_sync(scenario)()

    def test_stranger_socket_is_rejected(self):
        headers = self._cookie_header(self.stranger)

        async def scenario():
            communicator, connected = await self._connect(headers)
            self.assertFalse(connected)
            await communicator.disconnect()

        async_to_sync(scenario)()

    def test_participant_can_send_and_message_persists(self):
        headers = self._cookie_header(self.buyer)

        async def scenario():
            communicator, connected = await self._connect(headers)
            self.assertTrue(connected)
            ready = await communicator.receive_json_from()
            self.assertEqual(ready["type"], "ready")

            client_id = str(uuid.uuid4())
            await communicator.send_json_to(
                {
                    "type": "message.send",
                    "body": "Hello from the socket",
                    "client_message_id": client_id,
                }
            )

            seen_ack = False
            seen_message = False
            for _ in range(6):
                event = await communicator.receive_json_from(timeout=2)
                if event["type"] == "message.ack":
                    seen_ack = True
                    self.assertEqual(event["client_message_id"], client_id)
                    self.assertTrue(event["created"])
                if event["type"] == "message":
                    seen_message = True
                    self.assertEqual(event["message"]["body"], "Hello from the socket")
                if seen_ack and seen_message:
                    break
            self.assertTrue(seen_ack)
            self.assertTrue(seen_message)

            await communicator.disconnect()

        async_to_sync(scenario)()

        message = Message.objects.get()
        self.assertEqual(message.sender, self.buyer)
        self.assertEqual(message.body, "Hello from the socket")

    def test_duplicate_socket_send_is_idempotent(self):
        headers = self._cookie_header(self.buyer)
        client_id = str(uuid.uuid4())

        async def scenario():
            communicator, connected = await self._connect(headers)
            self.assertTrue(connected)
            await communicator.receive_json_from()

            payload = {
                "type": "message.send",
                "body": "Only once",
                "client_message_id": client_id,
            }
            await communicator.send_json_to(payload)
            first_ack = await self._wait_for_ack(communicator)
            self.assertTrue(first_ack["created"])

            await communicator.send_json_to(payload)
            second_ack = await self._wait_for_ack(communicator)
            self.assertFalse(second_ack["created"])

            await communicator.disconnect()

        async_to_sync(scenario)()
        self.assertEqual(Message.objects.count(), 1)

    @staticmethod
    async def _wait_for_ack(communicator):
        for _ in range(6):
            event = await communicator.receive_json_from(timeout=2)
            if event["type"] == "message.ack":
                return event
        raise AssertionError("No acknowledgement received")

class AuctionConversationTests(TestCase):
    """Threads about standalone auctions use the auction foreign key, not a listing."""

    @classmethod
    def setUpTestData(cls):
        cls.emirate = Emirate.objects.get(slug="abu-dhabi")
        cls.area = ServiceArea.objects.filter(emirate=cls.emirate, is_active=True).first()
        cls.category = Category.objects.filter(is_active=True).first()
        cls.seller = make_member(
            phone="+971501000011", name="Auction seller", emirate=cls.emirate, area=cls.area
        )
        cls.buyer = make_member(
            phone="+971501000012", name="Auction buyer", emirate=cls.emirate, area=cls.area
        )
        now = timezone.now()
        cls.auction = Auction.objects.create(
            seller=cls.seller,
            title="Standalone auction sideboard",
            description="A teak sideboard offered through the auction marketplace.",
            category=cls.category,
            condition=Auction.Condition.GOOD,
            emirate=cls.emirate,
            service_area=cls.area,
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=2),
            status=Auction.Status.LIVE,
            moderation_status=Auction.ModerationStatus.APPROVED,
            seller_agreed_fallback=True,
            submitted_at=now,
            approved_at=now,
        )

    def test_starting_an_auction_thread_links_the_auction_and_not_a_listing(self):
        self.client.force_login(self.buyer)
        response = self.client.post(
            reverse("conversations:start_auction", args=[self.auction.pk])
        )
        self.assertEqual(response.status_code, 302)
        conversation = Conversation.objects.get()
        self.assertEqual(conversation.auction, self.auction)
        self.assertIsNone(conversation.listing_id)
        self.assertEqual(conversation.seller, self.seller)
        self.assertEqual(conversation.item_title, self.auction.title)
        self.assertEqual(conversation.item_kind, "auction")
        self.assertEqual(
            self.client.get(reverse("conversations:detail", args=[conversation.pk])).status_code,
            200,
        )

    def test_repeated_start_reuses_the_same_auction_thread(self):
        self.client.force_login(self.buyer)
        self.client.post(reverse("conversations:start_auction", args=[self.auction.pk]))
        self.client.post(reverse("conversations:start_auction", args=[self.auction.pk]))
        self.assertEqual(Conversation.objects.count(), 1)

    def test_pending_auction_cannot_start_a_thread(self):
        self.auction.moderation_status = Auction.ModerationStatus.PENDING
        self.auction.save(update_fields=["moderation_status", "updated_at"])
        self.client.force_login(self.buyer)
        response = self.client.post(
            reverse("conversations:start_auction", args=[self.auction.pk])
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Conversation.objects.count(), 0)

    def test_auction_thread_appears_in_the_conversation_list(self):
        self.client.force_login(self.buyer)
        self.client.post(reverse("conversations:start_auction", args=[self.auction.pk]))
        response = self.client.get(reverse("conversations:list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.auction.title)
