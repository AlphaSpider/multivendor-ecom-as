"""
Auction tests: creation rules, bidding and tie-breaks, seller locking, confirmation,
fallback order, reserve prices, deadline processing and HTTP access control.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from auctions import services
from auctions.models import Auction, AuctionEvent, AuctionSelection, Bid
from catalog.models import Category, Listing
from core.exceptions import PermissionDeniedError, StateTransitionError
from deals.models import Deal
from locations.models import Emirate, ServiceArea
from notifications.models import Notification

User = get_user_model()


class AuctionTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emirate = Emirate.objects.get(slug="abu-dhabi")
        cls.other_emirate = Emirate.objects.get(slug="ras-al-khaimah")
        cls.area = ServiceArea.objects.filter(emirate=cls.emirate, is_active=True).first()
        cls.category = Category.objects.filter(is_active=True).first()

        cls.seller = User.objects.create_user(
            phone="+971508000001",
            display_name="Sam Seller",
            email="seller@example.test",
            emirate=cls.emirate,
            service_area=cls.area,
            phone_verified_at=timezone.now(),
        )
        cls.bidder_one = User.objects.create_user(
            phone="+971508000002",
            display_name="Bidder One",
            emirate=cls.emirate,
            service_area=cls.area,
            phone_verified_at=timezone.now(),
        )
        cls.bidder_two = User.objects.create_user(
            phone="+971508000003",
            display_name="Bidder Two",
            emirate=cls.emirate,
            service_area=cls.area,
            phone_verified_at=timezone.now(),
        )
        cls.stranger = User.objects.create_user(
            phone="+971508000004",
            display_name="Outsider",
            emirate=cls.emirate,
            service_area=cls.area,
            phone_verified_at=timezone.now(),
        )
        cls.unverified = User.objects.create_user(
            phone="+971508000005",
            display_name="Unverified",
            emirate=cls.emirate,
            service_area=cls.area,
        )
        cls.listing = Listing.objects.create(
            seller=cls.seller,
            title="Leather sofa",
            slug="auction-leather-sofa",
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

    # -- helpers -----------------------------------------------------------
    def make_listing(self, slug: str, *, seller=None, emirate=None, area=None):
        emirate = emirate or self.emirate
        area = area or self.area
        return Listing.objects.create(
            seller=seller or self.seller,
            title=f"Item {slug}",
            slug=slug,
            description="Auction test item.",
            category=self.category,
            condition=Listing.Condition.GOOD,
            asking_price=Decimal("500.00"),
            emirate=emirate,
            service_area=area,
            status=Listing.Status.APPROVED,
            submitted_at=timezone.now(),
            approved_at=timezone.now(),
            published_at=timezone.now(),
        )

    def make_auction(
        self,
        *,
        listing=None,
        seller=None,
        start_delta=timedelta(minutes=-5),
        end_delta=timedelta(hours=2),
        reserve=None,
    ) -> Auction:
        now = timezone.now()
        return services.create_auction(
            listing=listing or self.listing,
            seller=seller or self.seller,
            start_at=now + start_delta,
            end_at=now + end_delta,
            reserve_price=reserve,
            agree_fallback=True,
        )

    def place(self, auction, bidder, amount, client_bid_id=None):
        return services.place_bid(
            auction,
            bidder=bidder,
            amount=Decimal(str(amount)),
            client_bid_id=client_bid_id,
        )


class AuctionCreationTests(AuctionTestBase):
    def test_only_the_listing_owner_can_create_an_auction(self):
        with self.assertRaises(PermissionDeniedError):
            self.make_auction(seller=self.stranger)

    def test_unpublished_listing_cannot_be_auctioned(self):
        draft = self.make_listing("auction-draft")
        draft.status = Listing.Status.PENDING
        draft.published_at = None
        draft.save(update_fields=["status", "published_at"])

        with self.assertRaises(StateTransitionError):
            self.make_auction(listing=draft)

    def test_seller_must_agree_to_fallback_rules(self):
        now = timezone.now()
        with self.assertRaises(ValidationError):
            services.create_auction(
                listing=self.listing,
                seller=self.seller,
                start_at=now,
                end_at=now + timedelta(hours=2),
                agree_fallback=False,
            )

    def test_auction_duration_is_validated(self):
        now = timezone.now()
        with self.assertRaises(ValidationError):
            services.create_auction(
                listing=self.listing,
                seller=self.seller,
                start_at=now,
                end_at=now + timedelta(minutes=30),
                agree_fallback=True,
            )

        with self.assertRaises(ValidationError):
            services.create_auction(
                listing=self.listing,
                seller=self.seller,
                start_at=now,
                end_at=now + timedelta(days=15),
                agree_fallback=True,
            )

    def test_auction_starts_live_and_inherits_the_listing_emirate(self):
        auction = self.make_auction()

        self.assertEqual(auction.status, Auction.Status.LIVE)
        self.assertEqual(auction.emirate, self.emirate)
        self.assertEqual(auction.seller, self.seller)
        self.assertTrue(auction.seller_agreed_fallback)
        self.assertTrue(auction.events.filter(event_type=AuctionEvent.Type.CREATED).exists())

    def test_future_start_creates_a_scheduled_auction(self):
        auction = self.make_auction(
            start_delta=timedelta(hours=1), end_delta=timedelta(hours=3)
        )
        self.assertEqual(auction.status, Auction.Status.SCHEDULED)

    def test_a_listing_cannot_have_two_active_auctions(self):
        self.make_auction()
        with self.assertRaises(StateTransitionError):
            self.make_auction()


class BidTests(AuctionTestBase):
    def setUp(self):
        self.auction = self.make_auction()

    def test_bid_must_beat_the_highest_bid_by_the_increment(self):
        self.place(self.auction, self.bidder_one, "1000.00")

        with self.assertRaises(ValidationError):
            self.place(self.auction, self.bidder_two, "1000.00")

        bid, created = self.place(self.auction, self.bidder_two, "1001.00")
        self.assertTrue(created)
        self.assertEqual(bid.amount, Decimal("1001.00"))

    def test_unverified_member_cannot_bid(self):
        with self.assertRaises(PermissionDeniedError):
            self.place(self.auction, self.unverified, "1000.00")

    def test_anonymous_user_cannot_bid(self):
        with self.assertRaises(PermissionDeniedError):
            services.place_bid(self.auction, bidder=None, amount=Decimal("1000.00"))

    def test_seller_cannot_bid_on_their_own_auction(self):
        with self.assertRaises(PermissionDeniedError):
            self.place(self.auction, self.seller, "1000.00")

    def test_bids_are_rejected_after_the_scheduled_end(self):
        now = timezone.now()
        Auction.objects.filter(pk=self.auction.pk).update(
            start_at=now - timedelta(hours=2),
            end_at=now - timedelta(minutes=1),
        )
        self.auction.refresh_from_db()

        with self.assertRaises(StateTransitionError):
            self.place(self.auction, self.bidder_one, "1000.00")

    def test_bids_are_rejected_once_the_seller_locks(self):
        first, _ = self.place(self.auction, self.bidder_one, "1000.00")
        services.lock_bid(self.auction, seller=self.seller, bid_id=first.pk)

        with self.assertRaises(StateTransitionError):
            self.place(self.auction, self.bidder_two, "1200.00")

    def test_duplicate_client_bid_id_is_idempotent(self):
        client_id = uuid.uuid4()
        first, created = self.place(
            self.auction, self.bidder_one, "1000.00", client_bid_id=client_id
        )
        second, created_again = self.place(
            self.auction, self.bidder_one, "1000.00", client_bid_id=client_id
        )

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(self.auction.bids.count(), 1)

    def test_bids_below_reserve_are_stored_but_not_eligible(self):
        auction = self.make_auction(
            listing=self.make_listing("auction-reserve-item"), reserve="1500.00"
        )
        self.place(auction, self.bidder_one, "1000.00")

        self.assertEqual(auction.bids.count(), 1)
        self.assertEqual(services.eligible_bids(auction), [])
        self.assertIsNone(services.highest_eligible_bid(auction))


class TieBreakTests(AuctionTestBase):
    def test_earliest_bid_wins_a_tie(self):
        auction = self.make_auction()
        now = timezone.now()

        earlier = Bid.objects.create(
            auction=auction, bidder=self.bidder_one, amount=Decimal("1000.00")
        )
        later = Bid.objects.create(
            auction=auction, bidder=self.bidder_two, amount=Decimal("1000.00")
        )
        Bid.objects.filter(pk=earlier.pk).update(created_at=now - timedelta(minutes=10))
        Bid.objects.filter(pk=later.pk).update(created_at=now - timedelta(minutes=5))

        winner = services.highest_eligible_bid(auction)
        self.assertEqual(winner.pk, earlier.pk)
        self.assertEqual(winner.bidder, self.bidder_one)

        selection = services.lock_bid(auction, seller=self.seller, bid_id=winner.pk)
        self.assertEqual(selection.bidder, self.bidder_one)


class SellerLockTests(AuctionTestBase):
    def test_only_the_highest_eligible_bid_can_be_locked(self):
        low, _ = self.place(self.make_auction(), self.bidder_one, "1000.00")
        auction = Auction.objects.get(pk=low.auction_id)
        self.place(auction, self.bidder_two, "1200.00")

        with self.assertRaises(StateTransitionError):
            services.lock_bid(auction, seller=self.seller, bid_id=low.pk)

    def test_lock_freezes_bidding_and_starts_the_confirmation_window(self):
        auction = self.make_auction()
        bid, _ = self.place(auction, self.bidder_one, "1000.00")

        selection = services.lock_bid(auction, seller=self.seller, bid_id=bid.pk)
        auction.refresh_from_db()

        self.assertEqual(auction.status, Auction.Status.LOCKED)
        self.assertEqual(auction.selected_bid, bid)
        self.assertEqual(auction.selected_bidder, self.bidder_one)
        self.assertGreater(selection.confirmation_deadline, timezone.now())
        self.assertTrue(
            auction.events.filter(event_type=AuctionEvent.Type.BID_LOCKED).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                kind=Notification.Kind.AUCTION_SELECTION,
                entity_id=str(auction.pk),
            ).exists()
        )

    def test_reserve_price_prevents_locking_an_ineligible_bid(self):
        auction = self.make_auction(reserve="1500.00")
        self.place(auction, self.bidder_one, "1000.00")

        with self.assertRaises(StateTransitionError):
            services.lock_bid(auction, seller=self.seller, bid_id=auction.bids.get().pk)
        auction.refresh_from_db()
        self.assertEqual(auction.status, Auction.Status.LIVE)


class ConfirmationAndFallbackTests(AuctionTestBase):
    def _locked_auction(self):
        auction = self.make_auction()
        bid, _ = self.place(auction, self.bidder_one, "1000.00")
        selection = services.lock_bid(auction, seller=self.seller, bid_id=bid.pk)
        return auction, selection

    def test_confirm_creates_exactly_one_deal_at_the_winner_price(self):
        auction = self.make_auction()
        self.place(auction, self.bidder_one, "1000.00")
        top, _ = self.place(auction, self.bidder_two, "1200.00")
        selection = services.lock_bid(auction, seller=self.seller, bid_id=top.pk)

        deal = services.confirm_selection(selection, actor=self.bidder_two)
        auction.refresh_from_db()
        selection.refresh_from_db()

        self.assertEqual(deal.agreed_price, Decimal("1200.00"))
        self.assertEqual(auction.status, Auction.Status.AWARDED)
        self.assertEqual(auction.awarded_deal, deal)
        self.assertEqual(selection.status, AuctionSelection.Status.CONFIRMED)
        self.assertEqual(Deal.objects.count(), 1)
        self.assertEqual(deal.listing, self.listing)
        self.assertEqual(self.listing.__class__.objects.get(pk=self.listing.pk).status, Listing.Status.RESERVED)

        # A repeated confirmation (double click / retry) is idempotent.
        again = services.confirm_selection(selection, actor=self.bidder_two)
        self.assertEqual(again.pk, deal.pk)
        self.assertEqual(Deal.objects.count(), 1)

    def test_only_the_invited_bidder_can_confirm(self):
        auction, selection = self._locked_auction()
        with self.assertRaises(PermissionDeniedError):
            services.confirm_selection(selection, actor=self.bidder_two)

    def test_confirm_after_deadline_times_out_and_offers_the_next_bidder(self):
        auction = self.make_auction()
        self.place(auction, self.bidder_one, "1000.00")
        top, _ = self.place(auction, self.bidder_two, "1200.00")
        selection = services.lock_bid(auction, seller=self.seller, bid_id=top.pk)
        AuctionSelection.objects.filter(pk=selection.pk).update(
            confirmation_deadline=timezone.now() - timedelta(minutes=1)
        )
        selection.refresh_from_db()

        with self.assertRaises(StateTransitionError):
            services.confirm_selection(selection, actor=self.bidder_two)

        selection.refresh_from_db()
        self.assertEqual(selection.status, AuctionSelection.Status.TIMED_OUT)
        fallback = auction.selections.get(bidder=self.bidder_one)
        self.assertEqual(fallback.status, AuctionSelection.Status.PENDING)
        self.assertEqual(fallback.sequence, 2)
        self.assertGreater(fallback.confirmation_deadline, timezone.now())
        auction.refresh_from_db()
        self.assertEqual(auction.status, Auction.Status.LOCKED)
        self.assertEqual(auction.selected_bidder, self.bidder_one)

    def test_decline_offers_the_next_bidder_a_fresh_deadline(self):
        auction = self.make_auction()
        self.place(auction, self.bidder_one, "1000.00")
        top, _ = self.place(auction, self.bidder_two, "1200.00")
        selection = services.lock_bid(auction, seller=self.seller, bid_id=top.pk)

        services.decline_selection(selection, actor=self.bidder_two, reason="Changed my mind")

        selection.refresh_from_db()
        fallback = auction.selections.get(bidder=self.bidder_one)
        self.assertEqual(selection.status, AuctionSelection.Status.DECLINED)
        self.assertEqual(fallback.status, AuctionSelection.Status.PENDING)
        self.assertEqual(fallback.bid.amount, Decimal("1000.00"))
        self.assertGreater(fallback.confirmation_deadline, selection.confirmation_deadline)
        self.assertTrue(
            Notification.objects.filter(kind=Notification.Kind.AUCTION_FALLBACK).exists()
        )

    def test_each_bidder_is_offered_at_most_once_then_the_auction_is_unsold(self):
        auction = self.make_auction()
        self.place(auction, self.bidder_one, "1000.00")
        top, _ = self.place(auction, self.bidder_two, "1200.00")
        first = services.lock_bid(auction, seller=self.seller, bid_id=top.pk)

        services.decline_selection(first, actor=self.bidder_two)
        second = auction.selections.get(bidder=self.bidder_one)
        services.decline_selection(second, actor=self.bidder_one)

        auction.refresh_from_db()
        self.assertEqual(auction.status, Auction.Status.UNSOLD)
        self.assertEqual(auction.selections.count(), 2)
        self.assertFalse(
            auction.selections.filter(status=AuctionSelection.Status.PENDING).exists()
        )
        self.assertFalse(auction.awarded_deal_id)

    def test_fallback_uses_each_bidders_own_highest_bid_and_price(self):
        auction = self.make_auction()
        # Bidder one first bids low, then raises. Bidder two is in between.
        self.place(auction, self.bidder_one, "900.00")
        self.place(auction, self.bidder_two, "1200.00")
        top, _ = self.place(auction, self.bidder_one, "1300.00")

        selection = services.lock_bid(auction, seller=self.seller, bid_id=top.pk)
        self.assertEqual(selection.bidder, self.bidder_one)
        self.assertEqual(selection.bid.amount, Decimal("1300.00"))

        services.decline_selection(selection, actor=self.bidder_one)
        fallback = auction.selections.get(bidder=self.bidder_two)
        self.assertEqual(fallback.bid.amount, Decimal("1200.00"))

        deal = services.confirm_selection(fallback, actor=self.bidder_two)
        self.assertEqual(deal.agreed_price, Decimal("1200.00"))

    def test_expire_selection_is_idempotent(self):
        auction = self.make_auction()
        self.place(auction, self.bidder_one, "1000.00")
        top, _ = self.place(auction, self.bidder_two, "1200.00")
        selection = services.lock_bid(auction, seller=self.seller, bid_id=top.pk)
        AuctionSelection.objects.filter(pk=selection.pk).update(
            confirmation_deadline=timezone.now() - timedelta(minutes=1)
        )

        services.expire_selection(selection.pk)
        services.expire_selection(selection.pk)

        selection.refresh_from_db()
        self.assertEqual(selection.status, AuctionSelection.Status.TIMED_OUT)
        self.assertEqual(auction.selections.count(), 2)
        self.assertEqual(
            auction.selections.filter(status=AuctionSelection.Status.PENDING).count(), 1
        )

    def test_expiring_the_last_confirmation_marks_the_auction_unsold(self):
        auction, selection = self._locked_auction()
        AuctionSelection.objects.filter(pk=selection.pk).update(
            confirmation_deadline=timezone.now() - timedelta(minutes=1)
        )

        services.expire_selection(selection.pk)

        auction.refresh_from_db()
        selection.refresh_from_db()
        self.assertEqual(selection.status, AuctionSelection.Status.TIMED_OUT)
        self.assertEqual(auction.status, Auction.Status.UNSOLD)


class DeadlineTests(AuctionTestBase):
    def test_live_auction_closes_unsold_when_the_seller_never_locked(self):
        auction = self.make_auction()
        now = timezone.now()
        Auction.objects.filter(pk=auction.pk).update(
            start_at=now - timedelta(hours=2),
            end_at=now - timedelta(minutes=1),
        )

        services.close_expired_auction(auction.pk)

        auction.refresh_from_db()
        self.assertEqual(auction.status, Auction.Status.UNSOLD)
        self.assertIn("did not lock", auction.outcome_note)
        self.assertFalse(auction.awarded_deal_id)

    def test_locked_auction_is_not_closed_by_the_auction_end_worker(self):
        auction = self.make_auction()
        bid, _ = self.place(auction, self.bidder_one, "1000.00")
        services.lock_bid(auction, seller=self.seller, bid_id=bid.pk)
        now = timezone.now()
        Auction.objects.filter(pk=auction.pk).update(
            start_at=now - timedelta(hours=2),
            end_at=now - timedelta(minutes=1),
        )

        result = services.close_expired_auction(auction.pk)

        auction.refresh_from_db()
        self.assertIsNone(result)
        self.assertEqual(auction.status, Auction.Status.LOCKED)

    def test_scheduled_auction_opens_for_bids(self):
        auction = self.make_auction(
            start_delta=timedelta(minutes=10), end_delta=timedelta(hours=2)
        )
        opened = services.open_scheduled_auction(
            auction.pk, now=timezone.now() + timedelta(minutes=11)
        )

        self.assertIsNotNone(opened)
        auction.refresh_from_db()
        self.assertEqual(auction.status, Auction.Status.LIVE)
        self.assertTrue(auction.events.filter(event_type=AuctionEvent.Type.STARTED).exists())

    def test_command_processes_opening_closing_and_confirmation_deadlines(self):
        now = timezone.now()

        scheduled_listing = self.make_listing("auction-scheduled-item")
        scheduled = self.make_auction(
            listing=scheduled_listing,
            start_delta=timedelta(minutes=10),
            end_delta=timedelta(hours=2),
        )
        Auction.objects.filter(pk=scheduled.pk).update(start_at=now - timedelta(minutes=1))

        expired_listing = self.make_listing("auction-expired-item")
        expired = self.make_auction(listing=expired_listing)
        Auction.objects.filter(pk=expired.pk).update(
            start_at=now - timedelta(hours=2),
            end_at=now - timedelta(minutes=1),
        )

        locked_listing = self.make_listing("auction-locked-item")
        locked = self.make_auction(listing=locked_listing)
        bid, _ = self.place(locked, self.bidder_one, "800.00")
        selection = services.lock_bid(locked, seller=self.seller, bid_id=bid.pk)
        AuctionSelection.objects.filter(pk=selection.pk).update(
            confirmation_deadline=now - timedelta(minutes=1)
        )

        call_command("process_auction_deadlines", stdout=StringIO())

        scheduled.refresh_from_db()
        expired.refresh_from_db()
        locked.refresh_from_db()
        selection.refresh_from_db()
        self.assertEqual(scheduled.status, Auction.Status.LIVE)
        self.assertEqual(expired.status, Auction.Status.UNSOLD)
        self.assertEqual(selection.status, AuctionSelection.Status.TIMED_OUT)
        self.assertEqual(locked.status, Auction.Status.UNSOLD)


class AuctionHttpTests(AuctionTestBase):
    def setUp(self):
        self.auction = self.make_auction()

    def _selection_token(self, selection):
        return services.selection_token(selection)

    def test_auction_board_is_scoped_to_the_url_emirate(self):
        response = self.client.get(
            reverse("auction_marketplace:list", kwargs={"emirate_slug": "abu-dhabi"})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Leather sofa")

        response = self.client.get(
            reverse(
                "auction_marketplace:list", kwargs={"emirate_slug": "ras-al-khaimah"}
            )
        )
        self.assertNotContains(response, "Leather sofa")

    def test_bidder_identities_are_private_to_other_bidders(self):
        self.place(self.auction, self.bidder_one, "1000.00")
        self.place(self.auction, self.bidder_two, "1200.00")
        url = reverse(
            "auction_marketplace:detail",
            kwargs={"emirate_slug": "abu-dhabi", "pk": self.auction.pk},
        )

        self.client.force_login(self.seller)
        response = self.client.get(url)
        self.assertContains(response, "Bidder One")
        self.assertContains(response, "Bidder Two")

        self.client.force_login(self.bidder_two)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Bidder One")
        self.assertContains(response, "You")

    def test_seller_sees_the_lock_control(self):
        self.place(self.auction, self.bidder_one, "1000.00")
        url = reverse(
            "auction_marketplace:detail",
            kwargs={"emirate_slug": "abu-dhabi", "pk": self.auction.pk},
        )
        self.client.force_login(self.seller)
        response = self.client.get(url)
        self.assertContains(response, "Lock this bid")
        self.assertContains(response, "Bidder One")

    def test_bidder_can_place_a_bid_over_http(self):
        self.client.force_login(self.bidder_one)
        response = self.client.post(
            reverse(
                "auction_marketplace:bid",
                kwargs={"emirate_slug": "abu-dhabi", "pk": self.auction.pk},
            ),
            {"amount": "950.00", "client_bid_id": str(uuid.uuid4())},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.auction.bids.count(), 1)
        self.assertEqual(self.auction.bids.get().bidder, self.bidder_one)

    def test_bids_are_rejected_after_lock_over_http(self):
        bid, _ = self.place(self.auction, self.bidder_one, "1000.00")
        services.lock_bid(self.auction, seller=self.seller, bid_id=bid.pk)

        self.client.force_login(self.bidder_two)
        self.client.post(
            reverse(
                "auction_marketplace:bid",
                kwargs={"emirate_slug": "abu-dhabi", "pk": self.auction.pk},
            ),
            {"amount": "1500.00", "client_bid_id": str(uuid.uuid4())},
        )

        self.assertEqual(self.auction.bids.count(), 1)

    def test_confirmation_link_is_private_to_the_selected_bidder(self):
        bid, _ = self.place(self.auction, self.bidder_one, "1000.00")
        selection = services.lock_bid(self.auction, seller=self.seller, bid_id=bid.pk)
        url = reverse(
            "auctions:selection", kwargs={"token": self._selection_token(selection)}
        )

        self.client.force_login(self.bidder_two)
        self.assertEqual(self.client.get(url).status_code, 404)

        self.client.force_login(self.bidder_one)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm and create the deal")

    def test_selected_bidder_confirms_over_http_and_gets_a_deal(self):
        bid, _ = self.place(self.auction, self.bidder_one, "1000.00")
        selection = services.lock_bid(self.auction, seller=self.seller, bid_id=bid.pk)
        url = reverse(
            "auctions:selection", kwargs={"token": self._selection_token(selection)}
        )

        self.client.force_login(self.bidder_one)
        response = self.client.post(url, {"action": "confirm"})

        deal = Deal.objects.get()
        self.assertRedirects(response, reverse("deals:detail", kwargs={"pk": deal.pk}))
        self.assertEqual(deal.agreed_price, Decimal("1000.00"))

    def test_my_auctions_lists_the_members_activity(self):
        self.place(self.auction, self.bidder_one, "1000.00")
        self.client.force_login(self.bidder_one)
        response = self.client.get(reverse("auctions:mine"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Leather sofa")

    def test_only_the_owner_can_open_the_create_page(self):
        url = reverse(
            "auction_marketplace:create",
            kwargs={"emirate_slug": "abu-dhabi", "listing_slug": self.listing.slug},
        )
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(url).status_code, 404)

        self.client.force_login(self.seller)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_owner_can_create_an_auction_over_http(self):
        other = self.make_listing("auction-http-create")
        url = reverse(
            "auction_marketplace:create",
            kwargs={"emirate_slug": "abu-dhabi", "listing_slug": other.slug},
        )
        now = timezone.localtime(timezone.now()).replace(second=0, microsecond=0)
        self.client.force_login(self.seller)

        response = self.client.post(
            url,
            {
                "start_at": now.strftime("%Y-%m-%dT%H:%M"),
                "end_at": (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
                "reserve_price": "",
                "agree_fallback": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        auction = Auction.objects.get(listing=other)
        self.assertEqual(auction.status, Auction.Status.LIVE)
        self.assertEqual(auction.seller, self.seller)
