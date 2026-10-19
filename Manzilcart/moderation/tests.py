"""Moderation back-office tests: review screen, decisions and admin wiring."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Category, Listing
from locations.models import Emirate, ServiceArea
from moderation.models import ListingReview

User = get_user_model()


class ModerationAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emirate = Emirate.objects.get(slug="abu-dhabi")
        cls.area = ServiceArea.objects.filter(emirate=cls.emirate, is_active=True).first()
        cls.category = Category.objects.filter(is_active=True).first()
        cls.seller = User.objects.create_user(
            phone="+971504000001",
            display_name="Seller",
            emirate=cls.emirate,
            service_area=cls.area,
            phone_verified_at=timezone.now(),
        )
        cls.staff = User.objects.create_superuser(
            phone="+971504000099", password="staff-test-pass"
        )
        cls.listing = Listing.objects.create(
            seller=cls.seller,
            title="Pending armchair",
            slug="pending-armchair",
            description="Needs a moderator.",
            category=cls.category,
            condition=Listing.Condition.GOOD,
            asking_price=Decimal("300.00"),
            emirate=cls.emirate,
            service_area=cls.area,
            status=Listing.Status.PENDING,
            submitted_at=timezone.now(),
        )

    def test_changelist_loads(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("admin:catalog_listing_changelist"))
        self.assertEqual(response.status_code, 200)

    def test_review_screen_loads_and_links_to_catalog_changelist(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse("admin:moderation_review", args=[self.listing.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pending armchair")
        self.assertContains(response, reverse("admin:catalog_listing_changelist"))

    def test_approve_from_review_screen_publishes_listing(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("admin:moderation_review", args=[self.listing.pk]),
            {"decision": "approve", "reason": ""},
        )
        self.assertEqual(response.status_code, 302)

        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.APPROVED)
        self.assertIsNotNone(self.listing.published_at)
        review = ListingReview.objects.get()
        self.assertEqual(review.decision, ListingReview.Decision.APPROVE)
        self.assertEqual(review.reviewer, self.staff)

    def test_rejection_requires_a_reason(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("admin:moderation_review", args=[self.listing.pk]),
            {"decision": "reject", "reason": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.PENDING)
        self.assertEqual(ListingReview.objects.count(), 0)

    def test_non_staff_cannot_open_review_screen(self):
        buyer = User.objects.create_user(
            phone="+971504000002",
            display_name="Buyer",
            emirate=self.emirate,
            service_area=self.area,
            phone_verified_at=timezone.now(),
        )
        self.client.force_login(buyer)
        response = self.client.get(
            reverse("admin:moderation_review", args=[self.listing.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response["Location"])

    def test_view_only_staff_cannot_open_the_moderation_screen(self):
        viewer = User.objects.create_user(phone="+971504000003", is_staff=True)
        viewer.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="catalog", codename="view_listing"
            )
        )
        self.client.force_login(viewer)

        response = self.client.get(
            reverse("admin:moderation_review", args=[self.listing.pk])
        )

        self.assertEqual(response.status_code, 403)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.PENDING)
        self.assertEqual(ListingReview.objects.count(), 0)
