"""
Standalone auction tests: moderation, public visibility, seller edits, bidding,
locking, fallback, awarding and the HTTP entry points.

An auction is its own item; no test here creates or relies on a catalog listing.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from datetime import timedelta
from decimal import Decimal
from io import BytesIO, StringIO

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image as PILImage

from auctions import services
from auctions.models import Auction, AuctionEvent, AuctionImage, AuctionSelection
from catalog.models import Category, Listing
from core.exceptions import LocationError, PermissionDeniedError, StateTransitionError
from core.models import AuditEvent
from deals.models import Deal
from locations.models import Emirate, ServiceArea
from moderation.models import AuctionReview
from notifications.models import Notification

User = get_user_model()


class AuctionTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emirate = Emirate.objects.get(slug="abu-dhabi")
        cls.area = ServiceArea.objects.filter(emirate=cls.emirate, is_active=True).first()
        cls.category = (
            Category.objects.filter(is_active=True, parent__isnull=False).first()
            or Category.objects.filter(is_active=True).first()
        )

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
            email="bidder1@example.test",
            emirate=cls.emirate,
            service_area=cls.area,
            phone_verified_at=timezone.now(),
        )
        cls.bidder_two = User.objects.create_user(
            phone="+971508000003",
            display_name="Bidder Two",
            email="bidder2@example.test",
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
        cls.moderator = User.objects.create_user(
            phone="+971508000006",
            display_name="Moderator",
            emirate=cls.emirate,
            service_area=cls.area,
            phone_verified_at=timezone.now(),
            is_staff=True,
        )

    # -- helpers -----------------------------------------------------------
    def create_auction(
        self,
        *,
        seller=None,
        title="Leather sofa",
        start_delta=timedelta(minutes=-5),
        end_delta=timedelta(hours=3),
        reserve="100.00",
        submit_for_review=True,
        agree_fallback=True,
    ) -> Auction:
        now = timezone.now()
        return services.create_auction(
            seller=seller or self.seller,
            emirate=self.emirate,
            title=title,
            description=(
                "A well-kept three-seater sofa with minor marks on the base. "
                "Collection from the building lobby."
            ),
            category=self.category,
            condition=Auction.Condition.GOOD,
            service_area=self.area,
            start_at=now + start_delta,
            end_at=now + end_delta,
            reserve_price=reserve,
            agree_fallback=agree_fallback,
            submit_for_review=submit_for_review,
        )

    def approve(self, auction) -> Auction:
        return services.approve_auction(auction, reviewer=self.moderator)

    def make_approved(self, **kwargs) -> Auction:
        return self.approve(self.create_auction(**kwargs))

    def make_live(self, **kwargs) -> Auction:
        auction = self.make_approved(**kwargs)
        services.open_scheduled_auction(auction.pk)
        auction.refresh_from_db()
        self.assertEqual(auction.status, Auction.Status.LIVE)
        return auction

    def place(self, auction, bidder, amount, client_bid_id=None):
        return services.place_bid(
            auction,
            bidder=bidder,
            amount=Decimal(str(amount)),
            client_bid_id=client_bid_id,
        )

    def list_url(self):
        return reverse(
            "auction_marketplace:list", kwargs={"emirate_slug": self.emirate.slug}
        )

    def detail_url(self, auction):
        return reverse(
            "auction_marketplace:detail",
            kwargs={"emirate_slug": self.emirate.slug, "pk": auction.pk},
        )

    def create_url(self):
        return reverse(
            "auction_marketplace:create", kwargs={"emirate_slug": self.emirate.slug}
        )


class StandaloneCreationTests(AuctionTestBase):
    def test_condition_choices_exclude_new_items(self):
        values = {value for value, _label in Auction.Condition.choices}
        self.assertNotIn("new", values)
        self.assertIn(Auction.Condition.LIKE_NEW, values)

    def test_creation_never_creates_a_listing(self):
        before = Listing.objects.count()
        auction = self.create_auction()

        self.assertEqual(Listing.objects.count(), before)
        self.assertEqual(auction.title, "Leather sofa")
        self.assertEqual(auction.category, self.category)
        self.assertEqual(auction.condition, Auction.Condition.GOOD)
        self.assertEqual(auction.emirate, self.emirate)
        self.assertEqual(auction.service_area, self.area)
        self.assertTrue(auction.seller_agreed_fallback)
        self.assertEqual(auction.status, Auction.Status.SCHEDULED)
        self.assertEqual(auction.moderation_status, Auction.ModerationStatus.PENDING)
        self.assertIsNotNone(auction.submitted_at)
        self.assertTrue(
            auction.events.filter(event_type=AuctionEvent.Type.CREATED).exists()
        )
        self.assertTrue(
            auction.events.filter(event_type=AuctionEvent.Type.SUBMITTED).exists()
        )

    def test_save_as_draft_starts_draft_and_not_submitted(self):
        auction = self.create_auction(submit_for_review=False)
        self.assertEqual(auction.moderation_status, Auction.ModerationStatus.DRAFT)
        self.assertIsNone(auction.submitted_at)
        self.assertFalse(
            auction.events.filter(event_type=AuctionEvent.Type.SUBMITTED).exists()
        )

    def test_fallback_agreement_is_required(self):
        with self.assertRaises(ValidationError):
            self.create_auction(agree_fallback=False)

    def test_minimum_amount_is_required(self):
        with self.assertRaises(ValidationError) as ctx:
            self.create_auction(reserve=None)
        self.assertIn("reserve_price", ctx.exception.message_dict)

    def test_create_form_requires_a_minimum_starting_price(self):
        self.client.force_login(self.seller)
        response = self.client.get(self.create_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Minimum amount (AED)")
        field = response.context["form"].fields["reserve_price"]
        self.assertTrue(field.required)
        self.assertEqual(field.label, "Minimum amount (AED)")
        self.assertIn("starting price", field.help_text.lower())

    def test_schedule_duration_is_validated(self):
        now = timezone.now()
        with self.assertRaises(ValidationError):
            services.create_auction(
                seller=self.seller,
                emirate=self.emirate,
                title="Too short",
                description="This description is long enough for validation.",
                category=self.category,
                condition=Auction.Condition.GOOD,
                service_area=self.area,
                start_at=now,
                end_at=now + timedelta(minutes=30),
                agree_fallback=True,
            )
        with self.assertRaises(ValidationError):
            services.create_auction(
                seller=self.seller,
                emirate=self.emirate,
                title="Too long",
                description="This description is long enough for validation.",
                category=self.category,
                condition=Auction.Condition.GOOD,
                service_area=self.area,
                start_at=now,
                end_at=now + timedelta(days=30),
                agree_fallback=True,
            )

    def test_auction_in_wrong_emirate_is_rejected(self):
        other = Emirate.objects.get(slug="ras-al-khaimah")
        other_area = ServiceArea.objects.filter(emirate=other, is_active=True).first()
        with self.assertRaises(LocationError):
            services.create_auction(
                seller=self.seller,
                emirate=self.emirate,
                title="Wrong area",
                description="This description is long enough for validation.",
                category=self.category,
                condition=Auction.Condition.GOOD,
                service_area=other_area,
                start_at=timezone.now(),
                end_at=timezone.now() + timedelta(hours=3),
                agree_fallback=True,
            )


class ModerationTests(AuctionTestBase):
    def test_approval_is_separate_from_lifecycle_and_notifies_seller(self):
        auction = self.create_auction()
        approved = self.approve(auction)

        self.assertEqual(approved.moderation_status, Auction.ModerationStatus.APPROVED)
        self.assertEqual(approved.status, Auction.Status.SCHEDULED)
        self.assertEqual(approved.approved_by, self.moderator)
        self.assertIsNotNone(approved.approved_at)
        self.assertTrue(auction.reviews.filter(decision=AuctionReview.Decision.APPROVE).exists())
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.AUCTION_APPROVED, entity_id=auction.pk
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.seller, kind=Notification.Kind.AUCTION_OUTCOME
            ).exists()
        )

    def test_rejection_requires_a_reason_and_notifies_seller(self):
        auction = self.create_auction()
        with self.assertRaises(ValidationError):
            services.reject_auction(auction, reviewer=self.moderator, reason=" ")

        rejected = services.reject_auction(
            auction,
            reviewer=self.moderator,
            reason="Please add clearer photos of the marks.",
        )
        self.assertEqual(rejected.moderation_status, Auction.ModerationStatus.REJECTED)
        self.assertIn("clearer photos", rejected.moderation_note)
        review = auction.reviews.get()
        self.assertEqual(review.decision, AuctionReview.Decision.REJECT)
        self.assertIn("clearer photos", review.reason)
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.AUCTION_REJECTED, entity_id=auction.pk
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.seller, kind=Notification.Kind.AUCTION_OUTCOME
            ).exists()
        )

    def test_reviews_are_append_only(self):
        auction = self.create_auction()
        self.approve(auction)
        review = auction.reviews.get()
        review.reason = "changed"
        with self.assertRaises(ValueError):
            review.save()

    def test_only_pending_auctions_can_be_approved(self):
        auction = self.create_auction(submit_for_review=False)
        with self.assertRaises(StateTransitionError):
            self.approve(auction)

    def test_draft_and_pending_are_never_opened_by_the_worker(self):
        draft = self.create_auction(submit_for_review=False)
        pending = self.create_auction(title="Pending item")

        self.assertIsNone(services.open_scheduled_auction(draft.pk))
        self.assertIsNone(services.open_scheduled_auction(pending.pk))
        draft.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(draft.status, Auction.Status.SCHEDULED)
        self.assertEqual(pending.status, Auction.Status.SCHEDULED)

    def test_approved_auction_opens_when_start_passes(self):
        auction = self.make_live()
        self.assertEqual(auction.status, Auction.Status.LIVE)
        self.assertTrue(
            auction.events.filter(event_type=AuctionEvent.Type.STARTED).exists()
        )


class SellerEditTests(AuctionTestBase):
    def test_draft_and_rejected_can_be_edited_freely(self):
        draft = self.create_auction(submit_for_review=False)
        updated = services.update_auction(
            draft,
            actor=self.seller,
            title="Renamed sofa",
            description="A longer description for the renamed sofa item.",
            category=self.category,
            condition=Auction.Condition.LIKE_NEW,
            service_area=self.area,
            start_at=draft.start_at,
            end_at=draft.end_at,
            reserve_price=draft.reserve_price,
            agree_fallback=True,
            submit_for_review=True,
        )
        self.assertEqual(updated.title, "Renamed sofa")
        self.assertEqual(updated.moderation_status, Auction.ModerationStatus.PENDING)

        rejected = services.reject_auction(
            self.create_auction(title="Rejected item"),
            reviewer=self.moderator,
            reason="Needs a better description.",
        )
        resubmitted = services.update_auction(
            rejected,
            actor=self.seller,
            title="Rejected item",
            description="An improved description with dimensions and condition details.",
            category=self.category,
            condition=Auction.Condition.GOOD,
            service_area=self.area,
            start_at=rejected.start_at,
            end_at=rejected.end_at,
            reserve_price=rejected.reserve_price,
            agree_fallback=True,
            submit_for_review=True,
        )
        self.assertEqual(
            resubmitted.moderation_status, Auction.ModerationStatus.PENDING
        )
        self.assertEqual(resubmitted.moderation_note, "")

    def test_approved_auction_returns_to_review_when_edited_before_bids(self):
        auction = self.make_approved()
        updated = services.update_auction(
            auction,
            actor=self.seller,
            title="Updated before bidding",
            description="Updated description before the first bid was placed.",
            category=self.category,
            condition=Auction.Condition.GOOD,
            service_area=self.area,
            start_at=auction.start_at,
            end_at=auction.end_at,
            reserve_price=auction.reserve_price,
            agree_fallback=True,
            submit_for_review=True,
        )
        self.assertEqual(updated.moderation_status, Auction.ModerationStatus.PENDING)
        self.assertEqual(updated.status, Auction.Status.SCHEDULED)

    def test_content_is_frozen_after_the_first_bid(self):
        auction = self.make_live()
        self.place(auction, self.bidder_one, "1000.00")
        with self.assertRaises(StateTransitionError):
            services.update_auction(
                auction,
                actor=self.seller,
                title="Changed after bids",
                description="This edit must be rejected because bidding started.",
                category=self.category,
                condition=Auction.Condition.GOOD,
                service_area=self.area,
                start_at=auction.start_at,
                end_at=auction.end_at,
                agree_fallback=True,
            )

    def test_only_the_owner_may_edit(self):
        draft = self.create_auction(submit_for_review=False)
        with self.assertRaises(PermissionDeniedError):
            services.update_auction(
                draft,
                actor=self.stranger,
                title="Hijacked",
                description="This description is long enough for validation.",
                category=self.category,
                condition=Auction.Condition.GOOD,
                service_area=self.area,
                start_at=draft.start_at,
                end_at=draft.end_at,
                agree_fallback=True,
            )


class VisibilityTests(AuctionTestBase):
    def test_public_list_only_contains_approved_auctions(self):
        approved = self.make_approved(title="Approved visible item")
        self.create_auction(title="Pending hidden item")
        self.create_auction(title="Draft hidden item", submit_for_review=False)
        services.reject_auction(
            self.create_auction(title="Rejected hidden item"),
            reviewer=self.moderator,
            reason="Not suitable.",
        )

        response = self.client.get(self.list_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, approved.title)
        self.assertContains(response, self.create_url())
        self.assertNotContains(response, "Pending hidden item")
        self.assertNotContains(response, "Draft hidden item")
        self.assertNotContains(response, "Rejected hidden item")

    def test_unapproved_detail_is_404_for_visitors_but_visible_to_owner(self):
        pending = self.create_auction()
        self.assertEqual(self.client.get(self.detail_url(pending)).status_code, 404)

        self.client.force_login(self.seller)
        self.assertEqual(self.client.get(self.detail_url(pending)).status_code, 200)

    def test_detail_opens_a_due_scheduled_auction(self):
        auction = self.make_approved(title="Due auction")
        self.assertEqual(auction.status, Auction.Status.SCHEDULED)

        response = self.client.get(self.detail_url(auction))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bidding is open")
        auction.refresh_from_db()
        self.assertEqual(auction.status, Auction.Status.LIVE)

    def test_detail_leaves_a_future_scheduled_auction_closed(self):
        auction = self.make_approved(
            title="Future auction",
            start_delta=timedelta(hours=1),
            end_delta=timedelta(hours=3),
        )

        response = self.client.get(self.detail_url(auction))

        self.assertContains(response, "Bidding has not opened yet")
        auction.refresh_from_db()
        self.assertEqual(auction.status, Auction.Status.SCHEDULED)

    def test_detail_closes_an_expired_live_auction(self):
        auction = self.make_live(title="Expired auction")
        Auction.objects.filter(pk=auction.pk).update(
            start_at=timezone.now() - timedelta(hours=2),
            end_at=timezone.now() - timedelta(minutes=1),
        )

        response = self.client.get(self.detail_url(auction))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Auction closed unsold")
        auction.refresh_from_db()
        self.assertEqual(auction.status, Auction.Status.UNSOLD)

    def test_detail_shows_the_starting_price_as_the_first_bid_floor(self):
        auction = self.make_live(title="Starting price item", reserve="1500.00")
        response = self.client.get(self.detail_url(auction))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Starting price")
        self.assertContains(response, "1,500")
        self.assertEqual(response.context["starting_price"], Decimal("1500.00"))
        self.assertEqual(response.context["minimum_bid"], Decimal("1500.00"))

        self.place(auction, self.bidder_one, "1500.00")
        response = self.client.get(self.detail_url(auction))
        self.assertEqual(
            response.context["minimum_bid"],
            Decimal("1500.00") + Decimal(settings.AUCTION_MIN_BID_INCREMENT),
        )

    def test_logged_out_create_redirects_to_login_with_next(self):
        response = self.client.get(self.create_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)
        self.assertIn(self.create_url(), response.url)

    def test_ineligible_seller_sees_the_same_message_as_listing_creation(self):
        self.client.force_login(self.unverified)
        auction_response = self.client.get(self.create_url())
        listing_response = self.client.get(
            reverse(
                "catalog:listing_create",
                kwargs={"emirate_slug": self.emirate.slug},
            )
        )
        self.assertEqual(auction_response.status_code, 403)
        self.assertEqual(listing_response.status_code, 403)
        self.assertContains(
            auction_response,
            "Your account is not ready to sell yet.",
            status_code=403,
        )
        self.assertContains(
            listing_response,
            "Your account is not ready to sell yet.",
            status_code=403,
        )

    def test_my_auctions_lists_pending_and_rejected_with_reason(self):
        pending = self.create_auction(title="Waiting item")
        rejected = services.reject_auction(
            self.create_auction(title="Needs work item"),
            reviewer=self.moderator,
            reason="Please show the damaged corner.",
        )
        self.client.force_login(self.seller)
        response = self.client.get(reverse("auctions:mine"))

        self.assertContains(response, pending.title)
        self.assertContains(response, rejected.title)
        self.assertContains(response, "Please show the damaged corner.")
        self.assertContains(response, self.create_url())

    def test_dashboard_offers_a_create_auction_entry_point(self):
        self.client.force_login(self.seller)
        response = self.client.get(reverse("accounts:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.create_url())
        self.assertContains(response, "Create auction")


class BiddingTests(AuctionTestBase):
    def setUp(self):
        self.auction = self.make_live()

    def test_bid_must_beat_the_highest_bid_by_the_increment(self):
        self.place(self.auction, self.bidder_one, "1000.00")
        with self.assertRaises(ValidationError):
            self.place(self.auction, self.bidder_two, "1000.00")
        bid, created = self.place(self.auction, self.bidder_two, "1001.00")
        self.assertTrue(created)
        self.assertEqual(bid.amount, Decimal("1001.00"))

    def test_unverified_and_anonymous_cannot_bid(self):
        with self.assertRaises(PermissionDeniedError):
            self.place(self.auction, self.unverified, "1000.00")
        with self.assertRaises(PermissionDeniedError):
            services.place_bid(self.auction, bidder=None, amount=Decimal("1000.00"))

    def test_seller_cannot_bid_on_own_auction(self):
        with self.assertRaises(PermissionDeniedError):
            self.place(self.auction, self.seller, "1000.00")

    def test_unapproved_auction_cannot_receive_bids(self):
        pending = self.create_auction()
        services.open_scheduled_auction(pending.pk)
        with self.assertRaises(StateTransitionError):
            self.place(pending, self.bidder_one, "1000.00")

    def test_bids_are_rejected_after_the_end(self):
        auction = self.make_live()
        Auction.objects.filter(pk=auction.pk).update(
            start_at=timezone.now() - timedelta(hours=2),
            end_at=timezone.now() - timedelta(minutes=1),
        )
        auction.refresh_from_db()
        with self.assertRaises(StateTransitionError):
            self.place(auction, self.bidder_one, "1000.00")

    def test_client_bid_id_is_idempotent(self):
        client_id = uuid.uuid4()
        first, created = self.place(
            self.auction, self.bidder_one, "1000.00", client_bid_id=client_id
        )
        second, created_again = self.place(
            self.auction, self.bidder_one, "1200.00", client_bid_id=client_id
        )
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(second.amount, Decimal("1000.00"))

    def test_bid_endpoint_opens_a_due_scheduled_auction(self):
        auction = self.make_approved(title="Due auction")
        self.client.force_login(self.bidder_one)

        response = self.client.post(
            reverse(
                "auction_marketplace:bid",
                kwargs={"emirate_slug": self.emirate.slug, "pk": auction.pk},
            ),
            {"amount": "150.00"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        auction.refresh_from_db()
        self.assertEqual(auction.status, Auction.Status.LIVE)
        self.assertTrue(
            auction.bids.filter(
                bidder=self.bidder_one, amount=Decimal("150.00")
            ).exists()
        )

    def test_bids_below_the_starting_price_are_rejected(self):
        auction = self.make_live(title="Starting price item", reserve="1500.00")
        with self.assertRaises(ValidationError):
            self.place(auction, self.bidder_one, "1000.00")
        with self.assertRaises(ValidationError):
            self.place(auction, self.bidder_two, "1499.00")
        bid, created = self.place(auction, self.bidder_two, "1500.00")
        self.assertTrue(created)
        self.assertEqual(bid.amount, Decimal("1500.00"))
        highest = services.highest_eligible_bid(auction)
        self.assertEqual(highest.bidder, self.bidder_two)


class AwardTests(AuctionTestBase):
    def test_confirming_a_locked_bid_creates_an_auction_deal_without_a_listing(self):
        auction = self.make_live()
        self.place(auction, self.bidder_one, "1000.00")
        self.place(auction, self.bidder_two, "1100.00")
        highest = services.highest_eligible_bid(auction)

        selection = services.lock_bid(
            auction, seller=self.seller, bid_id=highest.pk
        )
        self.assertEqual(selection.status, AuctionSelection.Status.PENDING)
        auction.refresh_from_db()
        self.assertEqual(auction.status, Auction.Status.LOCKED)

        deal = services.confirm_selection(selection, actor=self.bidder_two)
        auction.refresh_from_db()

        self.assertEqual(auction.status, Auction.Status.AWARDED)
        self.assertEqual(auction.awarded_deal, deal)
        self.assertEqual(deal.auction, auction)
        self.assertIsNone(deal.listing_id)
        self.assertEqual(deal.buyer, self.bidder_two)
        self.assertEqual(deal.agreed_price, Decimal("1100.00"))
        self.assertEqual(deal.status, Deal.Status.ACCEPTED)
        self.assertEqual(Listing.objects.count(), 0)
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.AUCTION_AWARDED, entity_id=auction.pk
            ).exists()
        )

    def test_declining_offers_the_auction_to_the_next_bidder(self):
        auction = self.make_live()
        self.place(auction, self.bidder_two, "900.00")
        self.place(auction, self.bidder_one, "1000.00")
        highest = services.highest_eligible_bid(auction)
        first = services.lock_bid(auction, seller=self.seller, bid_id=highest.pk)

        services.decline_selection(first, actor=self.bidder_one, reason="Changed my mind")
        auction.refresh_from_db()
        self.assertEqual(auction.status, Auction.Status.LOCKED)

        second = (
            auction.selections.filter(status=AuctionSelection.Status.PENDING)
            .order_by("-sequence")
            .first()
        )
        self.assertIsNotNone(second)
        self.assertEqual(second.bidder, self.bidder_two)
        self.assertNotEqual(second.pk, first.pk)

    def test_seller_can_only_lock_the_highest_eligible_bid(self):
        auction = self.make_live()
        low, _ = self.place(auction, self.bidder_one, "1000.00")
        self.place(auction, self.bidder_two, "1100.00")
        with self.assertRaises(StateTransitionError):
            services.lock_bid(auction, seller=self.seller, bid_id=low.pk)

    def test_unsold_when_seller_does_not_lock_before_the_end(self):
        auction = self.make_live(end_delta=timedelta(hours=2))
        self.place(auction, self.bidder_one, "1000.00")
        result = services.close_expired_auction(
            auction.pk, now=timezone.now() + timedelta(hours=3)
        )
        self.assertEqual(result.status, Auction.Status.UNSOLD)
        self.assertFalse(Deal.objects.filter(auction=auction).exists())

    def test_cancelled_auction_is_not_public(self):
        auction = self.make_approved(title="Cancel me")
        services.cancel_auction(auction, actor=self.seller, reason="No longer available")
        auction.refresh_from_db()
        self.assertEqual(auction.status, Auction.Status.CANCELLED)
        self.client.force_login(self.seller)
        response = self.client.get(self.list_url())
        self.assertEqual(response.context["total_active"], 0)
        self.assertContains(response, "No live auctions yet")


class AuctionImageTests(AuctionTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp()
        cls._override = override_settings(MEDIA_ROOT=cls._media_root)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)
        super().tearDownClass()

    def _upload(self, name="sofa.png"):
        buffer = BytesIO()
        PILImage.new("RGB", (300, 300), (200, 120, 80)).save(buffer, format="PNG")
        return SimpleUploadedFile(
            name, buffer.getvalue(), content_type="image/png"
        )

    def test_first_photo_becomes_cover_and_is_stored_on_the_auction(self):
        auction = self.create_auction()
        image = services.add_auction_image(
            auction, self._upload(), actor=self.seller, caption="Front view"
        )
        self.assertTrue(image.is_cover)
        self.assertEqual(image.caption, "Front view")
        self.assertEqual(auction.images.count(), 1)
        self.assertEqual(auction.cover_image, image)
        self.assertGreater(image.width, 0)
        self.assertGreater(image.byte_size, 0)

    def test_detail_uses_the_same_gallery_as_a_fixed_price_listing(self):
        auction = self.create_auction()
        services.add_auction_image(
            auction, self._upload("front.png"), actor=self.seller, caption="Front view"
        )
        services.add_auction_image(
            auction, self._upload("side.png"), actor=self.seller, caption="Side view"
        )

        self.client.force_login(self.seller)
        response = self.client.get(self.detail_url(auction))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-product-gallery")
        self.assertContains(response, "gallery-main-img")
        self.assertContains(response, "data-thumb-btn", count=2)
        self.assertContains(response, "data-gallery-prev")
        self.assertContains(response, "data-gallery-next")
        self.assertContains(response, "auction-bidding-panel")
        self.assertContains(response, "auction-about-panel")
        self.assertLess(
            response.content.index(b"<h2>Bidding</h2>"),
            response.content.index(b"<h2>About this item</h2>"),
        )


class AuctionPhotoMinimumTests(AuctionTestBase):
    """Create/edit views must enforce the minimum photo count like fixed-price listings."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp()
        cls._override = override_settings(MEDIA_ROOT=cls._media_root)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)
        super().tearDownClass()

    def _upload(self, name):
        buffer = BytesIO()
        PILImage.new("RGB", (300, 300), (180, 160, 140)).save(buffer, format="PNG")
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")

    def _data(self, names):
        now = timezone.localtime(timezone.now())
        return {
            "title": "Photo count sofa",
            "description": "A neat sofa, photographed from every important angle.",
            "category": str(self.category.pk),
            "condition": Auction.Condition.GOOD,
            "service_area": str(self.area.pk),
            "start_at": (now + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M"),
            "end_at": (now + timedelta(days=3)).strftime("%Y-%m-%d %H:%M"),
            "reserve_price": "100",
            "agree_fallback": "on",
            "action": "submit",
            "images": [self._upload(name) for name in names],
        }

    def test_create_rejects_fewer_than_the_minimum_photos(self):
        self.client.force_login(self.seller)

        response = self.client.post(
            self.create_url(),
            self._data(["front.png", "side.png"]),
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response, f"at least {settings.MIN_IMAGES_PER_LISTING} photos", status_code=400
        )
        self.assertFalse(Auction.objects.filter(title="Photo count sofa").exists())

    def test_create_accepts_exactly_the_minimum_photos(self):
        self.client.force_login(self.seller)

        response = self.client.post(
            self.create_url(),
            self._data(["front.png", "side.png", "base.png"]),
            format="multipart",
        )

        self.assertEqual(response.status_code, 302)
        auction = Auction.objects.exclude(title="Leather sofa").get()
        self.assertEqual(
            auction.images.count(), settings.MIN_IMAGES_PER_LISTING
        )

    def test_submitting_an_edit_with_too_few_photos_is_blocked(self):
        # A draft with an old, swallowed-to-one-photo history from before the rule.
        auction = self.create_auction(
            start_delta=timedelta(hours=2), submit_for_review=False
        )
        AuctionImage.objects.create(auction=auction, image=self._upload("front.png"))
        self.client.force_login(self.seller)

        edit_url = reverse(
            "auction_marketplace:edit",
            kwargs={"emirate_slug": self.emirate.slug, "pk": auction.pk},
        )
        response = self.client.post(
            edit_url, self._data(["side.png"]), format="multipart"
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response, "before submitting for review", status_code=400
        )
        auction.refresh_from_db()
        self.assertEqual(auction.images.count(), 1)


class AuditCommandTests(AuctionTestBase):
    def test_dry_run_is_read_only_and_reports_schema(self):
        self.make_approved()
        before = (Auction.objects.count(), Listing.objects.count(), Deal.objects.count())
        output = StringIO()
        call_command("audit_auction_decoupling", stdout=output)
        text = output.getvalue()

        self.assertIn("Auction decoupling dry run", text)
        self.assertIn("Legacy Auction.listing column present: False", text)
        self.assertIn("Dry run complete; nothing was modified.", text)
        self.assertEqual(
            (Auction.objects.count(), Listing.objects.count(), Deal.objects.count()),
            before,
        )
