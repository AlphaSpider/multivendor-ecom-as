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
from auctions.models import (
    Auction,
    AuctionApplication,
    AuctionApplicationImage,
    AuctionEvent,
    AuctionImage,
    AuctionSelection,
)
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
        cls.admin_user = User.objects.create_user(
            phone="+971508000007",
            display_name="Ops Admin",
            emirate=cls.emirate,
            service_area=cls.area,
            phone_verified_at=timezone.now(),
            is_staff=True,
            is_superuser=True,
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

    def apply_url(self):
        return reverse(
            "auction_marketplace:apply", kwargs={"emirate_slug": self.emirate.slug}
        )

    def create_application(
        self,
        *,
        applicant=None,
        title="Linen sofa",
        proposed_starting_bid="250.00",
        submit=False,
        status=None,
    ) -> AuctionApplication:
        application = services.create_application(
            applicant=applicant or self.seller,
            emirate=self.emirate,
            title=title,
            description=(
                "A linen three-seater with a sturdy frame. Minor wear on the arms; "
                "collection from the lobby."
            ),
            category=self.category,
            condition=Auction.Condition.GOOD,
            service_area=self.area,
            proposed_starting_bid=proposed_starting_bid,
        )
        if submit:
            self.attach_application_images(application)
            application = services.submit_application(
                application, actor=application.applicant
            )
        if status is not None:
            AuctionApplication.objects.filter(pk=application.pk).update(status=status)
            application.refresh_from_db()
        return application

    def image_upload(self, name="photo.png") -> SimpleUploadedFile:
        buffer = BytesIO()
        PILImage.new("RGB", (320, 240), (180, 160, 140)).save(buffer, format="PNG")
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")

    def attach_application_images(self, application, count=None):
        count = count or settings.MIN_IMAGES_PER_LISTING
        for index in range(count):
            services.add_application_image(
                application,
                self.image_upload(f"application-{index}.png"),
                actor=application.applicant,
            )
        return application


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

    def test_legacy_create_url_redirects_to_the_application_form(self):
        response = self.client.get(self.create_url())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.apply_url())

    def test_apply_form_requires_a_minimum_starting_bid(self):
        self.client.force_login(self.seller)
        response = self.client.get(self.apply_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Proposed starting / minimum bid (AED)")
        field = response.context["form"].fields["proposed_starting_bid"]
        self.assertTrue(field.required)
        self.assertIn("lowest bid", field.help_text.lower())

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
        self.assertContains(response, self.apply_url())
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

    def test_logged_out_apply_redirects_to_login_with_next(self):
        response = self.client.get(self.apply_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)
        self.assertIn(self.apply_url(), response.url)

    def test_ineligible_seller_sees_the_same_message_as_listing_creation(self):
        self.client.force_login(self.unverified)
        auction_response = self.client.get(self.apply_url())
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
        self.assertContains(response, self.apply_url())

    def test_dashboard_offers_a_apply_auction_entry_point(self):
        self.client.force_login(self.seller)
        response = self.client.get(reverse("accounts:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.apply_url())
        self.assertContains(response, "Apply to auction")


class AuctionApplicationWorkflowTests(AuctionTestBase):
    """Member applications, staff decisions and conversion into an auction."""

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

    def auction_kwargs(self, application, **overrides):
        now = timezone.now()
        data = {
            "title": application.title,
            "description": application.description,
            "category": self.category,
            "condition": Auction.Condition.GOOD,
            "service_area": self.area,
            "start_at": now + timedelta(hours=1),
            "end_at": now + timedelta(days=3),
            "reserve_price": application.proposed_starting_bid,
        }
        data.update(overrides)
        return data

    def test_submit_requires_the_minimum_photos_and_notifies_the_applicant(self):
        application = self.create_application()
        self.attach_application_images(
            application, settings.MIN_IMAGES_PER_LISTING - 1
        )
        with self.assertRaises(ValidationError):
            services.submit_application(application, actor=self.seller)
        application.refresh_from_db()
        self.assertEqual(application.status, AuctionApplication.Status.DRAFT)

        self.attach_application_images(application, 1)
        submitted = services.submit_application(application, actor=self.seller)
        self.assertEqual(submitted.status, AuctionApplication.Status.SUBMITTED)
        self.assertIsNotNone(submitted.submitted_at)
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.AUCTION_APPLICATION_SUBMITTED,
                entity_type="auctions.auctionapplication",
                entity_id=str(application.pk),
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                kind=Notification.Kind.AUCTION_APPLICATION_UPDATE,
                user=self.seller,
                entity_id=str(application.pk),
            ).exists()
        )

    def test_changes_requested_can_be_resubmitted(self):
        application = self.create_application(submit=True)
        services.request_application_changes(
            application, actor=self.admin_user, note="Please add a photo of the base."
        )
        application.refresh_from_db()
        self.assertEqual(
            application.status, AuctionApplication.Status.CHANGES_REQUESTED
        )
        self.assertIn("photo of the base", application.applicant_message)
        self.assertTrue(application.is_editable_by_applicant)

        updated = services.update_application(
            application,
            actor=self.seller,
            title="Linen sofa with new base photo",
            description=application.description,
            category=self.category,
            condition=Auction.Condition.GOOD,
            service_area=self.area,
            proposed_starting_bid="275.00",
            request=None,
        )
        updated = services.submit_application(updated, actor=self.seller)
        self.assertEqual(updated.status, AuctionApplication.Status.SUBMITTED)
        self.assertEqual(updated.applicant_message, "")
        self.assertEqual(updated.proposed_starting_bid, Decimal("275.00"))

    def test_only_the_applicant_or_staff_can_change_an_application(self):
        application = self.create_application()
        with self.assertRaises(PermissionDeniedError):
            services.update_application(
                application,
                actor=self.stranger,
                title="Hijacked",
                description=application.description,
                category=self.category,
                condition=Auction.Condition.GOOD,
                service_area=self.area,
                proposed_starting_bid="300.00",
            )
        with self.assertRaises(PermissionDeniedError):
            services.submit_application(application, actor=self.stranger)
        with self.assertRaises(PermissionDeniedError):
            services.withdraw_application(application, actor=self.stranger)

    def test_submitted_application_cannot_be_withdrawn_after_listing(self):
        application = self.create_application(submit=True)
        auction = services.create_auction_from_application(
            application,
            actor=self.admin_user,
            **self.auction_kwargs(application),
        )
        application.refresh_from_db()
        self.assertEqual(application.status, AuctionApplication.Status.LISTED)
        self.assertEqual(application.auction, auction)
        with self.assertRaises(StateTransitionError):
            services.withdraw_application(application, actor=self.seller)

    def test_staff_publish_creates_an_approved_auction_and_copies_photos(self):
        application = self.create_application(submit=True)
        auction = services.create_auction_from_application(
            application,
            actor=self.admin_user,
            **self.auction_kwargs(application, reserve_price="315.00"),
        )

        auction.refresh_from_db()
        application.refresh_from_db()
        self.assertEqual(auction.seller, self.seller)
        self.assertEqual(auction.moderation_status, Auction.ModerationStatus.APPROVED)
        self.assertEqual(auction.approved_by, self.admin_user)
        self.assertEqual(auction.reserve_price, Decimal("315.00"))
        self.assertEqual(
            auction.images.count(), settings.MIN_IMAGES_PER_LISTING
        )
        self.assertEqual(application.status, AuctionApplication.Status.LISTED)
        self.assertEqual(application.listed_at is not None, True)
        self.assertEqual(application.auction_id, auction.pk)
        self.assertTrue(
            Notification.objects.filter(
                idempotency_key=f"application:{application.pk}:listed"
            ).exists()
        )

    def test_staff_can_save_a_private_draft_before_publishing(self):
        application = self.create_application(submit=True)
        auction = services.create_auction_from_application(
            application,
            actor=self.admin_user,
            publish=False,
            **self.auction_kwargs(application),
        )
        auction.refresh_from_db()
        application.refresh_from_db()
        self.assertEqual(auction.moderation_status, Auction.ModerationStatus.DRAFT)
        self.assertEqual(auction.status, Auction.Status.SCHEDULED)
        self.assertEqual(application.status, AuctionApplication.Status.IN_REVIEW)
        self.assertEqual(self.client.get(self.detail_url(auction)).status_code, 404)

    def test_non_staff_cannot_create_an_auction_from_an_application(self):
        application = self.create_application(submit=True)
        with self.assertRaises(PermissionDeniedError):
            services.create_auction_from_application(
                application,
                actor=self.seller,
                **self.auction_kwargs(application),
            )

    def test_staff_lock_is_staff_only_and_creates_a_confirmation_round(self):
        auction = self.make_live()
        bid, _created = self.place(auction, self.bidder_one, "450.00")
        with self.assertRaises(PermissionDeniedError):
            services.staff_lock_bid(
                auction, actor=self.seller, bid_id=bid.pk
            )
        selection = services.staff_lock_bid(
            auction, actor=self.admin_user, bid_id=bid.pk
        )
        auction.refresh_from_db()
        self.assertEqual(auction.status, Auction.Status.LOCKED)
        self.assertEqual(auction.selected_bid_id, bid.pk)
        self.assertEqual(selection.bidder, self.bidder_one)
        self.assertEqual(selection.status, AuctionSelection.Status.PENDING)

    def test_admin_queue_and_review_are_available_to_ops_staff(self):
        application = self.create_application(submit=True)
        self.client.force_login(self.admin_user)

        queue = self.client.get(reverse("admin:governance_auction_applications"))
        self.assertEqual(queue.status_code, 200)
        self.assertContains(queue, application.reference)

        review = self.client.get(
            reverse(
                "admin:governance_auction_application_review",
                args=[application.pk],
            )
        )
        self.assertEqual(review.status_code, 200)
        application.refresh_from_db()
        self.assertEqual(application.status, AuctionApplication.Status.IN_REVIEW)

    def test_admin_can_open_the_direct_staff_auction_form(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin:governance_auction_new"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create an auction")

    def test_admin_review_history_shows_the_submission_event(self):
        application = self.create_application(submit=True)
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse(
                "admin:governance_auction_application_review",
                args=[application.pk],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Auction application submitted")

    def test_admin_bid_monitor_renders_the_auction_and_bids(self):
        auction = self.make_live()
        bid, _created = self.place(auction, self.bidder_one, "450.00")
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse("admin:governance_bid_auction", args=[auction.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, auction.title)
        self.assertContains(response, "Lock bid")
        self.assertContains(response, f"AED {bid.amount:,.2f}")

    def test_admin_form_publishes_the_application_as_an_auction(self):
        application = self.create_application(submit=True)
        self.client.force_login(self.admin_user)
        form_url = reverse(
            "admin:governance_auction_application_list", args=[application.pk]
        )
        response = self.client.get(form_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Publish and list")

        now = timezone.localtime(timezone.now()).replace(second=0, microsecond=0)
        response = self.client.post(
            form_url,
            {
                "seller": str(self.seller.pk),
                "emirate": str(self.emirate.pk),
                "title": "Staff-reviewed linen sofa",
                "description": application.description,
                "category": str(self.category.pk),
                "condition": Auction.Condition.GOOD,
                "service_area": str(self.area.pk),
                "start_at": (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
                "end_at": (now + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M"),
                "reserve_price": "360.00",
                "cover_image": str(application.images.first().pk),
                "action": "publish",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        application.refresh_from_db()
        self.assertEqual(application.status, AuctionApplication.Status.LISTED)
        self.assertIsNotNone(application.auction_id)
        self.assertEqual(application.auction.reserve_price, Decimal("360.00"))
        self.assertEqual(
            application.auction.moderation_status, Auction.ModerationStatus.APPROVED
        )

    def test_staff_can_publish_their_own_application_after_changing_the_cover(self):
        self.admin_user.seller_status = User.SellerStatus.VERIFIED
        self.admin_user.save(update_fields=["seller_status"])
        application = self.create_application(
            applicant=self.admin_user, submit=True
        )
        new_cover = application.images.order_by("-display_order", "-id").first()
        self.client.force_login(self.admin_user)

        now = timezone.localtime(timezone.now()).replace(second=0, microsecond=0)
        response = self.client.post(
            reverse(
                "admin:governance_auction_application_list",
                args=[application.pk],
            ),
            {
                "seller": str(self.admin_user.pk),
                "emirate": str(self.emirate.pk),
                "title": "Staff-owned linen sofa",
                "description": application.description,
                "category": str(self.category.pk),
                "condition": Auction.Condition.GOOD,
                "service_area": str(self.area.pk),
                "start_at": (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
                "end_at": (now + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M"),
                "reserve_price": "360.00",
                "cover_image": str(new_cover.pk),
                "action": "publish",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        application.refresh_from_db()
        self.assertEqual(application.status, AuctionApplication.Status.LISTED)
        self.assertIsNotNone(application.auction_id)
        self.assertContains(
            response, f"Application {application.reference} is now listed"
        )

    def test_draft_auction_blocks_customer_withdrawal_and_late_rejection(self):
        application = self.create_application(submit=True)
        now = timezone.localtime(timezone.now()).replace(second=0, microsecond=0)
        auction = services.create_auction_from_application(
            application,
            actor=self.admin_user,
            title=application.title,
            description=application.description,
            category=self.category,
            condition=Auction.Condition.GOOD,
            service_area=self.area,
            start_at=now + timedelta(hours=1),
            end_at=now + timedelta(days=3),
            reserve_price=Decimal("300.00"),
            publish=False,
        )
        application.refresh_from_db()
        self.assertEqual(application.auction, auction)
        with self.assertRaises(StateTransitionError):
            services.withdraw_application(application, actor=self.seller)
        with self.assertRaises(StateTransitionError):
            services.reject_application(
                application, actor=self.admin_user, note="No longer suitable."
            )

    def test_admin_form_requires_a_reason_for_changes(self):
        application = self.create_application(submit=True)
        self.client.force_login(self.admin_user)
        review_url = reverse(
            "admin:governance_auction_application_review", args=[application.pk]
        )
        response = self.client.post(
            review_url, {"action": "request_changes", "decision_note": ""}
        )
        application.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(application.status, AuctionApplication.Status.SUBMITTED)


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
    """Application create/edit views enforce the minimum photo count."""

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
        return {
            "title": "Photo count sofa",
            "description": "A neat sofa, photographed from every important angle.",
            "category": str(self.category.pk),
            "condition": Auction.Condition.GOOD,
            "service_area": str(self.area.pk),
            "proposed_starting_bid": "100",
            "agree_review": "on",
            "agree_fallback": "on",
            "action": "submit",
            "images": [self._upload(name) for name in names],
        }

    def test_create_rejects_fewer_than_the_minimum_photos(self):
        self.client.force_login(self.seller)

        response = self.client.post(
            self.apply_url(),
            self._data(["front.png", "side.png"]),
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response, f"at least {settings.MIN_IMAGES_PER_LISTING} photos", status_code=400
        )
        self.assertFalse(AuctionApplication.objects.filter(title="Photo count sofa").exists())

    def test_create_accepts_exactly_the_minimum_photos(self):
        self.client.force_login(self.seller)

        response = self.client.post(
            self.apply_url(),
            self._data(["front.png", "side.png", "base.png"]),
            format="multipart",
        )

        self.assertEqual(response.status_code, 302)
        application = AuctionApplication.objects.get(title="Photo count sofa")
        self.assertEqual(application.status, AuctionApplication.Status.SUBMITTED)
        self.assertEqual(application.images.count(), settings.MIN_IMAGES_PER_LISTING)
        self.assertFalse(Auction.objects.filter(title="Photo count sofa").exists())

    def test_submitting_an_edit_with_too_few_photos_is_blocked(self):
        application = self.create_application(title="Draft sofa")
        AuctionApplicationImage.objects.create(
            application=application, image=self._upload("front.png")
        )
        self.client.force_login(self.seller)

        edit_url = reverse("auctions:application_edit", args=[application.pk])
        response = self.client.post(
            edit_url, self._data(["side.png"]), format="multipart"
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response, "at least 3 photos", status_code=400
        )
        application.refresh_from_db()
        self.assertEqual(application.images.count(), 1)
        self.assertEqual(application.status, AuctionApplication.Status.DRAFT)


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
