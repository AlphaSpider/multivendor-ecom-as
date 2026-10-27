"""
Governance tests: moderation decisions, the admin ↔ seller chat, the bid
monitor controls, vendor suspension and the customer-facing support pages.
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from auctions import services as auction_services
from conversations.selectors import unread_total_for
from conversations.services import start_conversation
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image as PILImage

from accounts.models import User
from auctions.models import Auction, AuctionEvent, Bid
from catalog.models import Category, Listing, ListingImage
from core.exceptions import PermissionDeniedError, StateTransitionError
from governance import services
from governance.models import ListingChatMessage, ListingChatThread
from locations.models import Emirate, ServiceArea
from notifications.models import Notification

User = get_user_model()


class GovernanceBase(TestCase):
    media_root = tempfile.mkdtemp()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_override = override_settings(MEDIA_ROOT=cls.media_root)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        shutil.rmtree(cls.media_root, ignore_errors=True)
        super().tearDownClass()

    @classmethod
    def setUpTestData(cls):
        cls.emirate = Emirate.objects.get(slug="abu-dhabi")
        cls.area = ServiceArea.objects.filter(emirate=cls.emirate, is_active=True).first()
        cls.category = Category.objects.filter(is_active=True).first()
        cls.staff = User.objects.create_superuser(phone="+971500000001", display_name="Ops")
        cls.seller = User.objects.create_user(
            phone="+971500000002",
            display_name="Sam Seller",
            emirate=cls.emirate,
            service_area=cls.area,
            phone_verified_at=timezone.now(),
        )
        cls.buyer = User.objects.create_user(
            phone="+971500000003",
            display_name="Bilal Buyer",
            emirate=cls.emirate,
            service_area=cls.area,
            phone_verified_at=timezone.now(),
        )
        cls.listing = Listing.objects.create(
            seller=cls.seller,
            title="Walnut desk",
            slug="governance-walnut-desk",
            description="A tidy desk.",
            category=cls.category,
            condition=Listing.Condition.GOOD,
            asking_price=Decimal("199.00"),
            emirate=cls.emirate,
            service_area=cls.area,
            status=Listing.Status.PENDING,
            submitted_at=timezone.now(),
        )
        cls.auction = Auction.objects.create(
            seller=cls.seller,
            title="Vintage chair auction",
            description="Solid chair for the highest bidder.",
            category=cls.category,
            condition=Auction.Condition.GOOD,
            emirate=cls.emirate,
            service_area=cls.area,
            start_at=timezone.now() - timedelta(hours=1),
            end_at=timezone.now() + timedelta(hours=23),
            reserve_price=Decimal("100.00"),
            status=Auction.Status.LIVE,
            moderation_status=Auction.ModerationStatus.APPROVED,
        )


class ChatThreadTests(GovernanceBase):
    def test_one_thread_per_item_across_kinds(self):
        first = services.get_thread(self.listing)
        second = services.get_thread(self.listing)
        self.assertEqual(first.pk, second.pk)
        auction_thread = services.get_thread(self.auction)
        self.assertNotEqual(first.pk, auction_thread.pk)

    def test_customer_message_marks_unread_for_staff(self):
        thread = services.get_thread(self.listing)
        services.create_message(thread, sender=self.seller, message="Is the desk still available?")
        self.assertEqual(thread.unread_messages_for_staff(), 1)
        self.assertEqual(thread.unread_messages_for_customer(), 0)
        self.assertEqual(services.mark_customer_messages_read(thread), 1)

    def test_non_participant_cannot_post(self):
        thread = services.get_thread(self.listing)
        with self.assertRaises(PermissionDeniedError):
            services.create_message(thread, sender=self.buyer, message="hi")

    def test_seller_other_thread_not_created_automatically(self):
        # Threads materialise lazily, on first contact with the item.
        self.assertFalse(
            ListingChatThread.objects.filter(object_id=self.auction.pk).exists()
        )


class ModerationDecisionTests(GovernanceBase):
    def test_request_changes_listing_posts_system_message_and_notifies(self):
        thread = services.get_thread(self.listing)
        services.request_changes(
            self.listing, reviewer=self.staff, note="Please add photos of the legs.", request=None
        )
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.CHANGES_REQUESTED)
        self.assertEqual(self.listing.reviewed_by, self.staff)
        self.assertIsNotNone(self.listing.reviewed_at)
        self.assertIn("legs", self.listing.moderation_note)
        thread.refresh_from_db()
        self.assertEqual(thread.status, ListingChatThread.Status.NEEDS_SELLER_REPLY)
        self.assertTrue(
            thread.messages.filter(message__icontains="requested")
            .filter(message__icontains="legs")
            .exists()
        )
        # The seller was also told on WhatsApp (queued notification).
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.seller.phone, kind=Notification.Kind.MODERATION
            ).exists()
        )

    def test_request_changes_requires_a_note(self):
        with self.assertRaises(ValidationError):
            services.request_changes(self.listing, reviewer=self.staff, note="", request=None)

    def test_suspend_listing_delists_immediately(self):
        self.listing.status = Listing.Status.APPROVED
        self.listing.published_at = timezone.now()
        self.listing.save(update_fields=["status", "published_at"])
        services.suspend_item(
            self.listing, reviewer=self.staff, reason="Prohibited item", request=None
        )
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.SUSPENDED)

    def test_reject_listing_via_governance_service(self):
        services.reject_item(
            self.listing, reviewer=self.staff, reason="Not furniture.", request=None
        )
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.REJECTED)
        self.assertEqual(self.listing.reviewed_by, self.staff)

    def test_seller_can_resubmit_changes_requested(self):
        services.request_changes(self.listing, reviewer=self.staff, note="Add photos.", request=None)
        self.listing.refresh_from_db()
        from catalog.services import submit_for_review

        buffer = BytesIO()
        PILImage.new("RGB", (300, 300), (180, 140, 120)).save(buffer, format="PNG")
        for index in range(3):
            ListingImage.objects.create(
                listing=self.listing,
                image=SimpleUploadedFile(f"photo-{index}.png", buffer.getvalue(), content_type="image/png"),
            )
        submit_for_review(self.listing, actor=self.seller, request=None)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.PENDING)

    def test_request_changes_auction(self):
        services.request_changes(
            self.auction, reviewer=self.staff, note="Restart price too low", request=None
        )
        self.auction.refresh_from_db()
        self.assertEqual(
            self.auction.moderation_status, Auction.ModerationStatus.CHANGES_REQUESTED
        )
        self.assertEqual(self.auction.reviewed_by, self.staff)


class AdminEndpointTests(GovernanceBase):
    def setUp(self):
        self.client.force_login(self.staff)
        self.thread = services.get_thread(self.listing)

    def test_messages_view_marks_customer_messages_read(self):
        services.create_message(self.thread, sender=self.seller, message="Hello team")
        url = reverse("admin:governance_chat_messages_listing", args=[self.listing.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.unread_messages_for_staff(), 0)

    def test_send_view_appends_message(self):
        url = reverse("admin:governance_chat_send_listing", args=[self.listing.pk])
        response = self.client.post(url, {"message": "Adding better photos tomorrow."})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            ListingChatMessage.objects.filter(
                thread=self.thread, message="Adding better photos tomorrow."
            ).exists()
        )
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.status, ListingChatThread.Status.NEEDS_SELLER_REPLY)

    def test_send_rejects_empty_message(self):
        url = reverse("admin:governance_chat_send_listing", args=[self.listing.pk])
        response = self.client.post(url, {"message": "   "})
        self.assertEqual(response.status_code, 400)

    def test_customer_blocked_from_admin_endpoints(self):
        self.client.force_login(self.seller)
        url = reverse("admin:governance_chat_send_listing", args=[self.listing.pk])
        response = self.client.post(url, {"message": "hax"})
        self.assertIn(response.status_code, (302, 403))


class CustomerSupportPagesTests(GovernanceBase):
    def test_seller_can_view_and_reply(self):
        thread = services.get_thread(self.listing)
        services.create_message(
            thread, sender=self.staff, message="Add photos of the legs.", system=True
        )
        self.client.force_login(self.seller)

        response = self.client.get(reverse("governance:support_threads"))
        self.assertContains(response, "Walnut desk")

        response = self.client.get(reverse("governance:support_thread", args=[thread.pk]))
        self.assertContains(response, "Add photos of the legs")

        response = self.client.post(
            reverse("governance:support_thread", args=[thread.pk]),
            {"message": "Done, uploading now."},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            thread.messages.filter(sender=self.seller, message="Done, uploading now.").exists()
        )

    def test_other_member_cannot_open_the_thread(self):
        thread = services.get_thread(self.listing)
        self.client.force_login(self.buyer)
        response = self.client.get(reverse("governance:support_thread", args=[thread.pk]))
        self.assertEqual(response.status_code, 404)


class SupportThreadEditLinkTests(GovernanceBase):
    """When the team requests changes, the chat page links to the edit form."""

    def _open_thread_page(self):
        thread = services.get_thread(self.listing)
        services.create_message(
            thread, sender=self.staff, message="Please retake the last photo.", system=True
        )
        self.client.force_login(self.seller)
        return self.client.get(reverse("governance:support_thread", args=[thread.pk]))

    def test_changes_requested_listing_links_to_edit_page(self):
        self.listing.status = Listing.Status.CHANGES_REQUESTED
        self.listing.save(update_fields=["status"])
        response = self._open_thread_page()
        edit_url = reverse(
            "catalog:listing_edit",
            kwargs={"emirate_slug": self.emirate.slug, "slug": self.listing.slug},
        )
        self.assertContains(response, edit_url)
        self.assertContains(response, "Edit your listing")

    def test_pending_listing_has_no_edit_link(self):
        self.listing.status = Listing.Status.PENDING
        self.listing.save(update_fields=["status"])
        response = self._open_thread_page()
        self.assertNotContains(response, "Edit your listing")
        # The item itself is still browsable from the sidebar.
        self.assertContains(response, self.listing.get_absolute_url())

    def test_dashboard_no_longer_links_a_marketplace_messages_stat(self):
        self.client.force_login(self.seller)
        response = self.client.get(reverse("accounts:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Marketplace messages")
        self.assertNotContains(response, reverse("governance:support_threads"))


class SellerMessagesTabTests(GovernanceBase):
    """Marketplace-team threads surface inside the storefront Messages tab."""

    def test_support_thread_shows_in_messages_tab(self):
        thread = services.get_thread(self.listing)
        services.create_message(
            thread, sender=self.staff, message="Please add photos of the legs."
        )
        self.client.force_login(self.seller)
        response = self.client.get(reverse("conversations:list"))
        # The item title, the "Marketplace team" label and the thread link.
        self.assertContains(response, "Walnut desk")
        self.assertContains(response, "Marketplace team")
        self.assertContains(response, reverse("governance:support_thread", args=[thread.pk]))
        # One unread staff reply → the "new" badge.
        self.assertContains(response, 'class="badge"')

    def test_no_support_threads_for_non_sellers(self):
        # The thread belongs to the listing's seller; a stranger must not see it.
        thread = services.get_thread(self.listing)
        services.create_message(thread, sender=self.staff, message="Quick check.")
        self.client.force_login(self.buyer)
        response = self.client.get(reverse("conversations:list"))
        self.assertEqual(response.context["inbox"], [])

    def test_unread_nav_badge_counts_staff_replies_and_clears(self):
        thread = services.get_thread(self.listing)
        self.assertEqual(unread_total_for(self.seller), 0)
        services.create_message(thread, sender=self.staff, message="Add photos of the legs.")
        self.assertEqual(unread_total_for(self.seller), 1)
        # Opening the support thread marks the staff message read.
        self.client.force_login(self.seller)
        self.client.get(reverse("governance:support_thread", args=[thread.pk]))
        self.assertEqual(unread_total_for(self.seller), 0)

    def test_own_replies_do_not_count_as_unread(self):
        thread = services.get_thread(self.listing)
        services.create_message(thread, sender=self.seller, message="On it.")
        self.assertEqual(unread_total_for(self.seller), 0)

    def test_marketplace_team_row_joins_normal_conversations(self):
        # A regular buyer↔seller conversation (about the live auction)...
        start_conversation(buyer=self.buyer, auction=self.auction)
        # ...and a marketplace-team thread (about the listing) both in one tab.
        thread = services.get_thread(self.listing)
        services.create_message(thread, sender=self.staff, message="Quick check.")
        self.client.force_login(self.buyer)
        response = self.client.get(reverse("conversations:list"))
        self.assertContains(response, self.auction.title)
        self.assertNotIn(
            "support",
            [entry["kind"] for entry in response.context["inbox"]],
        )
        self.assertEqual(
            len(response.context["inbox"]),
            1,
            "A buyer should see exactly their own conversation.",
        )

        self.client.force_login(self.seller)
        response = self.client.get(reverse("conversations:list"))
        kinds = [entry["kind"] for entry in response.context["inbox"]]
        # The seller sees the support thread AND the buyer conversation
        # (they participate in both), each exactly once.
        self.assertCountEqual(kinds, ["support", "conversation"])
class BidMonitorTests(GovernanceBase):
    def _bid(self, bidder, amount="150.00"):
        return Bid.objects.create(auction=self.auction, bidder=bidder, amount=Decimal(amount))

    def test_highest_bid_excludes_voided_bids(self):
        top = self._bid(self.buyer, "500.00")
        self._bid(self.buyer, "200.00")
        auction_services.void_bid(self.auction, top, reviewer=self.staff)
        top.refresh_from_db()
        self.assertTrue(top.is_voided)
        highest = auction_services.highest_eligible_bid(self.auction)
        self.assertEqual(highest.amount, Decimal("200.00"))

    def test_void_bid_refused_for_locked_selection(self):
        top = self._bid(self.buyer)
        self.auction.selected_bid = top
        self.auction.selected_bidder = top.bidder
        self.auction.save(update_fields=["selected_bid", "selected_bidder"])
        with self.assertRaises(StateTransitionError):
            auction_services.void_bid(self.auction, top, reviewer=self.staff)

    def test_void_bid_records_event(self):
        bid = self._bid(self.buyer)
        auction_services.void_bid(self.auction, bid, reviewer=self.staff)
        self.assertTrue(
            AuctionEvent.objects.filter(auction=self.auction, event_type="bid_voided").exists()
        )

    def test_pause_and_resume_auction(self):
        auction_services.pause_auction(self.auction, actor=self.staff)
        self.auction.refresh_from_db()
        self.assertEqual(self.auction.status, Auction.Status.PAUSED)
        auction_services.resume_auction(self.auction, actor=self.staff)
        self.auction.refresh_from_db()
        self.assertEqual(self.auction.status, Auction.Status.LIVE)

    def test_extend_auction_moves_end_time(self):
        original_end = self.auction.end_at
        auction_services.extend_auction(self.auction, hours=24, actor=self.staff)
        self.auction.refresh_from_db()
        self.assertGreater(self.auction.end_at, original_end + timedelta(hours=23))

    def test_bid_monitor_pages_render_flags(self):
        self.client.force_login(self.staff)
        self._bid(self.buyer)
        response = self.client.get(reverse("admin:governance_bid_monitor"))
        self.assertContains(response, "Vintage chair auction")
        response = self.client.get(reverse("admin:governance_bid_auction", args=[self.auction.pk]))
        self.assertContains(response, "Void")


class VendorGovernanceTests(GovernanceBase):
    def test_suspend_vendor_freezes_assets(self):
        self.listing.status = Listing.Status.APPROVED
        self.listing.published_at = timezone.now()
        self.listing.save(update_fields=["status", "published_at"])
        services.suspend_vendor(self.seller, actor=self.staff)
        self.seller.refresh_from_db()
        self.assertEqual(self.seller.seller_status, "suspended")
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.SUSPENDED)

    def test_vendor_page_actions(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("admin:governance_vendors"))
        self.assertContains(response, "Sam Seller")
        response = self.client.post(
            reverse("admin:governance_vendors"),
            {"action": "suspend_vendor", "vendor_id": self.seller.pk},
        )
        self.seller.refresh_from_db()
        self.assertEqual(self.seller.seller_status, "suspended")


class AdminOtpLoginTests(GovernanceBase):
    """Staff log into the admin with a WhatsApp code: passwords never work."""

    def _latest_code(self) -> str:
        notification = (
            Notification.objects.filter(kind=Notification.Kind.OTP)
            .order_by("-id")
            .first()
        )
        self.assertIsNotNone(notification)
        # The message body starts with the 6-digit code.
        return notification.body.split()[0]

    def test_login_page_asks_for_a_mobile_number(self):
        response = self.client.get(reverse("admin:login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mobile number")
        self.assertContains(response, "one-time code")

    def test_staff_otp_cycle_signs_staff_in(self):
        response = self.client.post(
            reverse("admin:login"), {"step": "phone", "phone": self.staff.phone}
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            reverse("admin:login"), {"step": "code", "code": self._latest_code()}
        )
        self.assertRedirects(response, reverse("admin:index"))
        self.assertTrue(self.client.session.get("_auth_user_id"))

    def test_next_parameter_is_respected_after_login(self):
        target = reverse("admin:catalog_listing_changelist")
        self.client.post(
            reverse("admin:login") + f"?next={target}",
            {"step": "phone", "phone": self.staff.phone},
        )
        response = self.client.post(
            reverse("admin:login"), {"step": "code", "code": self._latest_code()}
        )
        self.assertRedirects(response, target)

    def test_non_staff_number_cannot_enter_the_admin(self):
        self.client.post(
            reverse("admin:login"), {"step": "phone", "phone": self.seller.phone}
        )
        code = self._latest_code()
        response = self.client.post(
            reverse("admin:login"), {"step": "code", "code": code}
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response["Location"].startswith(reverse("admin:login")),
            f"expected a bounce back to the login page, got {response['Location']}",
        )
        self.assertFalse(self.client.session.get("_auth_user_id"))

    def test_admin_pages_still_blocked_when_logged_out(self):
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])


class GovernancePagesTests(GovernanceBase):
    def test_staff_reaches_every_governance_page(self):
        self.client.force_login(self.staff)
        for name in (
            "admin:governance_dashboard",
            "admin:governance_moderation_inbox",
            "admin:governance_bid_monitor",
            "admin:governance_vendors",
            "admin:governance_financials",
        ):
            with self.subTest(url=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)

    def test_members_cannot_reach_governance_pages(self):
        self.client.force_login(self.seller)
        response = self.client.get(reverse("admin:governance_dashboard"))
        self.assertIn(response.status_code, (302, 403))

    def test_dashboard_counts_total_items_of_each_type(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("admin:governance_dashboard"))
        kpis = response.context["kpis"]
        self.assertEqual(kpis["total_listings"], Listing.objects.count())
        self.assertEqual(kpis["total_auctions"], Auction.objects.count())
        html = response.content.decode("utf-8")
        self.assertIn("Total fixed-price products", html)
        self.assertIn("Total auction products", html)
