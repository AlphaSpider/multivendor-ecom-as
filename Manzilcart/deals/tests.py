"""
Deal tests: negotiation transitions, atomic acceptance, duplicate prevention,
cancellation, optional services and operations notifications.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Category, Listing
from auctions.models import Auction
from core.exceptions import PermissionDeniedError, StateTransitionError
from deals import services
from deals.models import Deal, DealOffer, DealServiceSelection
from locations.models import (
    Emirate,
    ServiceArea,
    ServiceAreaCoverage,
    ServiceOption,
)
from notifications.models import Notification

User = get_user_model()


class DealTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emirate = Emirate.objects.get(slug="abu-dhabi")
        cls.area = ServiceArea.objects.filter(emirate=cls.emirate, is_active=True).first()
        cls.category = Category.objects.filter(is_active=True).first()
        cls.seller = User.objects.create_user(
            phone="+971505000001",
            display_name="Seller",
            emirate=cls.emirate,
            service_area=cls.area,
            phone_verified_at=timezone.now(),
            email="seller@example.test",
        )
        cls.buyer = User.objects.create_user(
            phone="+971505000002",
            display_name="Buyer",
            emirate=cls.emirate,
            service_area=cls.area,
            phone_verified_at=timezone.now(),
        )
        cls.buyer_two = User.objects.create_user(
            phone="+971505000003",
            display_name="Second buyer",
            emirate=cls.emirate,
            service_area=cls.area,
            phone_verified_at=timezone.now(),
        )
        cls.stranger = User.objects.create_user(
            phone="+971505000004",
            display_name="Outsider",
            emirate=cls.emirate,
            service_area=cls.area,
            phone_verified_at=timezone.now(),
        )
        cls.listing = Listing.objects.create(
            seller=cls.seller,
            title="Leather sofa",
            slug="leather-sofa",
            description="Three-seater, good condition.",
            category=cls.category,
            condition=Listing.Condition.GOOD,
            asking_price=Decimal("1200.00"),
            emirate=cls.emirate,
            service_area=cls.area,
            status=Listing.Status.APPROVED,
            submitted_at=timezone.now(),
            approved_at=timezone.now(),
            published_at=timezone.now(),
        )
        cls.covered = ServiceAreaCoverage.objects.filter(
            service_area=cls.area, is_available=True
        ).select_related("service_option").first()

    def make_deal(self, buyer=None, amount="1000.00"):
        deal = services.create_deal(
            listing=self.listing,
            buyer=buyer or self.buyer,
            amount=Decimal(amount),
        )
        return deal


class DealNegotiationTests(DealTestBase):
    def test_create_deal_records_offer_event_and_notifies_seller(self):
        deal = self.make_deal()

        self.assertEqual(deal.status, Deal.Status.NEGOTIATING)
        self.assertEqual(deal.buyer, self.buyer)
        self.assertEqual(deal.seller, self.seller)
        self.assertEqual(deal.emirate, self.emirate)
        offer = deal.offers.get()
        self.assertEqual(offer.amount, Decimal("1000.00"))
        self.assertEqual(offer.status, DealOffer.Status.PENDING)
        self.assertEqual(deal.events.count(), 1)
        self.assertTrue(
            Notification.objects.filter(
                kind=Notification.Kind.OFFER_RECEIVED, entity_id=str(deal.pk)
            ).exists()
        )

    def test_buyer_cannot_have_two_open_negotiations_for_the_same_listing(self):
        self.make_deal()
        with self.assertRaises(StateTransitionError):
            self.make_deal()

    def test_second_buyer_can_negotiate_in_parallel(self):
        first = self.make_deal()
        second = self.make_deal(self.buyer_two, amount="1100.00")
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(Deal.objects.count(), 2)

    def test_counteroffer_preserves_history_and_moves_pending_state(self):
        deal = self.make_deal()
        original = deal.offers.get()

        counter = services.counter_offer(
            deal, actor=self.seller, amount=Decimal("1150.00"), note="Firm price"
        )
        original.refresh_from_db()
        self.assertEqual(original.status, DealOffer.Status.COUNTERED)
        self.assertEqual(counter.sender, self.seller)
        self.assertEqual(counter.in_response_to, original)
        self.assertEqual(deal.offers.count(), 2)

        # The buyer may now accept the seller's counteroffer.
        services.accept_offer(deal, actor=self.buyer)
        deal.refresh_from_db()
        self.assertEqual(deal.status, Deal.Status.ACCEPTED)
        self.assertEqual(deal.agreed_price, Decimal("1150.00"))

    def test_sender_cannot_accept_their_own_offer(self):
        deal = self.make_deal()
        with self.assertRaises(PermissionDeniedError):
            services.accept_offer(deal, actor=self.buyer)

    def test_reject_then_new_offer(self):
        deal = self.make_deal()
        offer = services.reject_offer(deal, actor=self.seller, note="Too low")
        self.assertEqual(offer.status, DealOffer.Status.REJECTED)
        self.assertEqual(deal.status, Deal.Status.NEGOTIATING)

        replacement = services.make_offer(deal, actor=self.buyer, amount=Decimal("1050.00"))
        self.assertEqual(replacement.status, DealOffer.Status.PENDING)
        self.assertEqual(deal.offers.count(), 2)

    def test_stranger_cannot_act_on_deal(self):
        deal = self.make_deal()
        with self.assertRaises(PermissionDeniedError):
            services.counter_offer(deal, actor=self.stranger, amount=Decimal("900.00"))


class DealAcceptanceTests(DealTestBase):
    def test_acceptance_sets_price_and_reserves_listing(self):
        deal = self.make_deal()
        services.accept_offer(deal, actor=self.seller)

        deal.refresh_from_db()
        self.listing.refresh_from_db()
        self.assertEqual(deal.status, Deal.Status.ACCEPTED)
        self.assertEqual(deal.agreed_price, Decimal("1000.00"))
        self.assertIsNotNone(deal.accepted_at)
        self.assertEqual(deal.accepted_offer.status, DealOffer.Status.ACCEPTED)
        self.assertEqual(self.listing.status, Listing.Status.RESERVED)

        ops = Notification.objects.filter(
            kind=Notification.Kind.OPS_SERVICE_REQUEST, entity_id=str(deal.pk)
        )
        self.assertEqual(ops.count(), 1)
        self.assertIn(deal.reference, ops.get().body)

    def test_second_acceptance_is_rejected(self):
        deal = self.make_deal()
        services.accept_offer(deal, actor=self.seller)

        with self.assertRaises(StateTransitionError):
            services.accept_offer(deal, actor=self.seller)

        self.assertEqual(Deal.objects.filter(status=Deal.Status.ACCEPTED).count(), 1)
        self.assertEqual(
            DealOffer.objects.filter(status=DealOffer.Status.ACCEPTED).count(), 1
        )

    def test_competing_accepted_deals_are_impossible(self):
        first = self.make_deal()
        second = self.make_deal(self.buyer_two, amount="1300.00")

        services.accept_offer(first, actor=self.seller)
        with self.assertRaises(StateTransitionError):
            services.accept_offer(second, actor=self.seller)

        second.refresh_from_db()
        self.assertEqual(second.status, Deal.Status.NEGOTIATING)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Deal.objects.create(
                listing=self.listing,
                buyer=self.buyer_two,
                seller=self.seller,
                emirate=self.emirate,
                status=Deal.Status.ACCEPTED,
                agreed_price=Decimal("1300.00"),
                accepted_at=timezone.now(),
            )

    def test_acceptance_survives_notification_failure(self):
        deal = self.make_deal()
        with patch("deals.services.queue_notification", side_effect=RuntimeError("provider down")):
            services.accept_offer(deal, actor=self.seller)

        deal.refresh_from_db()
        self.listing.refresh_from_db()
        self.assertEqual(deal.status, Deal.Status.ACCEPTED)
        self.assertEqual(self.listing.status, Listing.Status.RESERVED)

    def test_listing_cannot_be_accepted_after_seller_archives_it(self):
        deal = self.make_deal()
        self.listing.status = Listing.Status.ARCHIVED
        self.listing.published_at = None
        self.listing.save(update_fields=["status", "published_at"])

        with self.assertRaises(StateTransitionError):
            services.accept_offer(deal, actor=self.seller)


class DealServiceSelectionTests(DealTestBase):
    def test_buyer_selects_covered_service(self):
        if self.covered is None:
            self.skipTest("No service coverage seeded for this area")
        deal = self.make_deal()
        services.set_service_selections(
            deal, actor=self.buyer, option_ids=[self.covered.service_option_id]
        )
        selections = DealServiceSelection.objects.filter(deal=deal)
        self.assertEqual(selections.count(), 1)
        self.assertEqual(selections.get().service_option_id, self.covered.service_option_id)

    def test_uncovered_service_is_rejected(self):
        option = ServiceOption.objects.create(
            code=f"packaging-{uuid.uuid4().hex[:6]}",
            name="Packaging",
        )
        deal = self.make_deal()
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            services.set_service_selections(deal, actor=self.buyer, option_ids=[option.pk])
        self.assertEqual(DealServiceSelection.objects.filter(deal=deal).count(), 0)

    def test_seller_cannot_choose_services(self):
        deal = self.make_deal()
        with self.assertRaises(PermissionDeniedError):
            services.set_service_selections(deal, actor=self.seller, option_ids=[])

    def test_services_are_locked_after_acceptance(self):
        deal = self.make_deal()
        services.accept_offer(deal, actor=self.seller)
        with self.assertRaises(StateTransitionError):
            services.set_service_selections(deal, actor=self.buyer, option_ids=[])


class DealCancellationTests(DealTestBase):
    def accepted_deal(self):
        deal = self.make_deal()
        services.accept_offer(deal, actor=self.seller)
        deal.refresh_from_db()
        return deal

    def test_request_does_not_cancel_immediately(self):
        deal = self.accepted_deal()
        cancellation = services.request_cancellation(
            deal, actor=self.buyer, reason="The sofa does not fit in my flat."
        )
        deal.refresh_from_db()
        self.listing.refresh_from_db()

        self.assertEqual(cancellation.status, "pending")
        self.assertEqual(deal.status, Deal.Status.CANCELLATION_REQUESTED)
        self.assertEqual(self.listing.status, Listing.Status.RESERVED)
        self.assertIsNone(deal.cancelled_at)

    def test_requester_cannot_respond_to_own_request(self):
        deal = self.accepted_deal()
        cancellation = services.request_cancellation(
            deal, actor=self.buyer, reason="Changed my mind about the item."
        )
        with self.assertRaises(PermissionDeniedError):
            services.respond_to_cancellation(
                cancellation, actor=self.buyer, accept=True
            )

    def test_peer_rejects_cancellation_and_deal_remains_accepted(self):
        deal = self.accepted_deal()
        cancellation = services.request_cancellation(
            deal, actor=self.buyer, reason="Changed my mind about the item."
        )
        services.respond_to_cancellation(
            cancellation, actor=self.seller, accept=False, note="I can still deliver."
        )
        deal.refresh_from_db()
        cancellation.refresh_from_db()
        self.listing.refresh_from_db()

        self.assertEqual(cancellation.status, "rejected")
        self.assertEqual(deal.status, Deal.Status.ACCEPTED)
        self.assertEqual(self.listing.status, Listing.Status.RESERVED)

    def test_peer_accepts_cancellation_and_listing_reopens(self):
        deal = self.accepted_deal()
        cancellation = services.request_cancellation(
            deal, actor=self.buyer, reason="My building does not allow this item."
        )
        services.respond_to_cancellation(cancellation, actor=self.seller, accept=True)

        deal.refresh_from_db()
        cancellation.refresh_from_db()
        self.listing.refresh_from_db()
        self.assertEqual(cancellation.status, "accepted")
        self.assertEqual(deal.status, Deal.Status.CANCELLED)
        self.assertIsNotNone(deal.cancelled_at)
        self.assertEqual(self.listing.status, Listing.Status.APPROVED)

    def test_staff_can_finalize_disputed_cancellation(self):
        staff = User.objects.create_superuser(
            phone="+971505000099", password="staff-pass"
        )
        deal = self.accepted_deal()
        cancellation = services.request_cancellation(
            deal, actor=self.seller, reason="Buyer stopped responding to messages."
        )
        services.escalate_cancellation(cancellation, actor=self.buyer, note="Please help.")
        services.finalize_cancellation(deal, actor=staff, note="Resolved by operations")

        deal.refresh_from_db()
        cancellation.refresh_from_db()
        self.listing.refresh_from_db()
        self.assertEqual(deal.status, Deal.Status.CANCELLED)
        self.assertEqual(cancellation.status, "finalized")
        self.assertEqual(self.listing.status, Listing.Status.APPROVED)

    def test_cancellation_only_after_acceptance(self):
        deal = self.make_deal()
        with self.assertRaises(StateTransitionError):
            services.request_cancellation(deal, actor=self.buyer, reason="No longer needed.")


class DealHTTPTests(DealTestBase):
    def test_listing_page_has_offer_form(self):
        self.client.force_login(self.buyer)
        response = self.client.get(self.listing.get_absolute_url())
        self.assertContains(response, reverse("deals:start", args=[self.listing.pk]))
        self.assertContains(response, "Make offer")

    def test_buyer_creates_deal_from_listing(self):
        self.client.force_login(self.buyer)
        response = self.client.post(
            reverse("deals:start", args=[self.listing.pk]),
            {"amount": "950", "note": "Can collect Saturday"},
        )
        deal = Deal.objects.get()
        self.assertRedirects(response, reverse("deals:detail", args=[deal.pk]))
        self.assertEqual(deal.offers.get().amount, Decimal("950.00"))

    def test_stranger_cannot_view_deal(self):
        deal = self.make_deal()
        self.client.force_login(self.stranger)
        response = self.client.get(reverse("deals:detail", args=[deal.pk]))
        self.assertEqual(response.status_code, 404)

    def test_seller_accepts_offer_through_view(self):
        deal = self.make_deal()
        self.client.force_login(self.seller)
        response = self.client.post(
            reverse("deals:action", args=[deal.pk]), {"action": "accept"}
        )
        self.assertRedirects(response, reverse("deals:detail", args=[deal.pk]))
        deal.refresh_from_db()
        self.assertEqual(deal.status, Deal.Status.ACCEPTED)

    def test_cancellation_flow_through_views(self):
        deal = self.make_deal()
        services.accept_offer(deal, actor=self.seller)

        self.client.force_login(self.buyer)
        self.client.post(
            reverse("deals:action", args=[deal.pk]),
            {"action": "request_cancellation", "reason": "The lift is too small."},
        )
        deal.refresh_from_db()
        self.assertEqual(deal.status, Deal.Status.CANCELLATION_REQUESTED)
        cancellation = deal.cancellations.get()

        self.client.force_login(self.seller)
        self.client.post(
            reverse("deals:action", args=[deal.pk]),
            {
                "action": "respond_cancellation",
                "cancellation_id": cancellation.pk,
                "decision": "accept",
            },
        )
        deal.refresh_from_db()
        self.listing.refresh_from_db()
        self.assertEqual(deal.status, Deal.Status.CANCELLED)
        self.assertEqual(self.listing.status, Listing.Status.APPROVED)

    def test_deals_list_requires_login_and_lists_participation(self):
        deal = self.make_deal()
        response = self.client.get(reverse("deals:list"))
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.buyer)
        response = self.client.get(reverse("deals:list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, deal.reference)

        self.client.force_login(self.stranger)
        response = self.client.get(reverse("deals:list"))
        self.assertNotContains(response, deal.reference)

class AuctionDealTests(DealTestBase):
    """Deals created from standalone auction awards, with no catalog listing."""

    def make_auction(self):
        now = timezone.now()
        return Auction.objects.create(
            seller=self.seller,
            title="Auctioned sideboard",
            description="Teak sideboard with two doors and a small water mark.",
            category=self.category,
            condition=Auction.Condition.GOOD,
            emirate=self.emirate,
            service_area=self.area,
            start_at=now - timedelta(hours=2),
            end_at=now + timedelta(hours=2),
            status=Auction.Status.LIVE,
            moderation_status=Auction.ModerationStatus.APPROVED,
            seller_agreed_fallback=True,
            submitted_at=now,
            approved_at=now,
        )

    def test_create_awarded_deal_links_the_auction_and_not_a_listing(self):
        auction = self.make_auction()
        before_listing_status = self.listing.status
        deal = services.create_awarded_deal(
            auction=auction,
            buyer=self.buyer,
            amount=Decimal("950.00"),
        )

        self.assertEqual(deal.auction, auction)
        self.assertIsNone(deal.listing_id)
        self.assertEqual(deal.status, Deal.Status.ACCEPTED)
        self.assertEqual(deal.seller, self.seller)
        self.assertEqual(deal.emirate, self.emirate)
        self.assertEqual(deal.agreed_price, Decimal("950.00"))
        self.assertEqual(deal.item_title, auction.title)
        self.assertEqual(deal.item_kind, "auction")
        self.assertTrue(deal.item_url.endswith(f"/auctions/{auction.pk}/"))
        self.assertTrue(deal.item_reference.startswith("MC-A"))

        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, before_listing_status)
        self.assertTrue(
            Notification.objects.filter(
                kind=Notification.Kind.DEAL_ACCEPTED,
                entity_id=str(deal.pk),
            ).exists()
        )

    def test_auction_award_is_unique_per_auction(self):
        auction = self.make_auction()
        services.create_awarded_deal(
            auction=auction, buyer=self.buyer, amount=Decimal("950.00")
        )
        with self.assertRaises(StateTransitionError):
            services.create_awarded_deal(
                auction=auction, buyer=self.buyer_two, amount=Decimal("980.00")
            )

    def test_deal_source_is_exactly_one_of_listing_or_auction(self):
        auction = self.make_auction()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Deal.objects.create(
                listing=self.listing,
                auction=auction,
                buyer=self.buyer,
                seller=self.seller,
                emirate=self.emirate,
                status=Deal.Status.ACCEPTED,
                agreed_price=Decimal("950.00"),
                accepted_at=timezone.now(),
            )

    def test_auction_deal_page_and_list_work_for_participants(self):
        auction = self.make_auction()
        deal = services.create_awarded_deal(
            auction=auction, buyer=self.buyer, amount=Decimal("950.00")
        )

        self.client.force_login(self.buyer)
        response = self.client.get(reverse("deals:detail", args=[deal.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, auction.title)
        self.assertContains(response, deal.reference)

        response = self.client.get(reverse("deals:list"))
        self.assertContains(response, deal.reference)
        self.assertNotContains(response, self.listing.title)

        self.client.force_login(self.stranger)
        response = self.client.get(reverse("deals:detail", args=[deal.pk]))
        self.assertEqual(response.status_code, 404)
