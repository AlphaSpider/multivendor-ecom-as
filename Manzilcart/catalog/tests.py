"""Catalog tests: public visibility, emirate scoping, owner flows and page wiring."""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Category, Listing
from locations.models import Emirate, ServiceArea

User = get_user_model()


class CatalogTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.abu_dhabi = Emirate.objects.get(slug="abu-dhabi")
        cls.rak = Emirate.objects.get(slug="ras-al-khaimah")
        cls.ad_area = ServiceArea.objects.filter(emirate=cls.abu_dhabi, is_active=True).first()
        cls.rak_area = ServiceArea.objects.filter(emirate=cls.rak, is_active=True).first()
        cls.category = Category.objects.filter(is_active=True).first()

        cls.seller = User.objects.create_user(
            phone="+971503000001",
            display_name="Seller",
            emirate=cls.abu_dhabi,
            service_area=cls.ad_area,
            phone_verified_at=timezone.now(),
        )
        cls.buyer = User.objects.create_user(
            phone="+971503000002",
            display_name="Buyer",
            emirate=cls.abu_dhabi,
            service_area=cls.ad_area,
            phone_verified_at=timezone.now(),
        )
        cls.listing = Listing.objects.create(
            seller=cls.seller,
            title="Walnut coffee table",
            slug="walnut-coffee-table",
            description="A sturdy walnut table.",
            category=cls.category,
            condition=Listing.Condition.GOOD,
            asking_price=Decimal("400.00"),
            emirate=cls.abu_dhabi,
            service_area=cls.ad_area,
            status=Listing.Status.APPROVED,
            submitted_at=timezone.now(),
            approved_at=timezone.now(),
            published_at=timezone.now(),
        )

    def test_public_list_is_scoped_to_the_url_emirate(self):
        response = self.client.get(
            reverse("catalog:listing_list", kwargs={"emirate_slug": "abu-dhabi"})
        )
        self.assertContains(response, "Walnut coffee table")

        response = self.client.get(
            reverse("catalog:listing_list", kwargs={"emirate_slug": "ras-al-khaimah"})
        )
        self.assertNotContains(response, "Walnut coffee table")

    def test_unapproved_listing_is_hidden_from_public_browse(self):
        self.listing.status = Listing.Status.PENDING
        self.listing.published_at = None
        self.listing.save(update_fields=["status", "published_at"])

        response = self.client.get(
            reverse("catalog:listing_list", kwargs={"emirate_slug": "abu-dhabi"})
        )
        self.assertNotContains(response, "Walnut coffee table")

    def test_buyer_sees_message_seller_button(self):
        self.client.force_login(self.buyer)
        response = self.client.get(self.listing.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Message the seller")

    def test_owner_sees_edit_link(self):
        self.client.force_login(self.seller)
        response = self.client.get(self.listing.get_absolute_url())
        self.assertContains(response, "Edit listing")

    def test_my_listings_page_lists_owner_items(self):
        self.client.force_login(self.seller)
        response = self.client.get(reverse("accounts:listings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Walnut coffee table")
        self.assertContains(response, "Approved / published")

    def test_draft_can_be_submitted_from_my_listings(self):
        draft = Listing.objects.create(
            seller=self.seller,
            title="Draft bookshelf",
            slug=f"draft-bookshelf-{uuid.uuid4().hex[:6]}",
            description="Needs a review.",
            category=self.category,
            condition=Listing.Condition.FAIR,
            asking_price=Decimal("90.00"),
            emirate=self.abu_dhabi,
            service_area=self.ad_area,
            status=Listing.Status.DRAFT,
        )
        self.client.force_login(self.seller)

        response = self.client.post(
            reverse(
                "catalog:listing_action",
                kwargs={"emirate_slug": "abu-dhabi", "slug": draft.slug},
            ),
            {"action": "submit"},
        )
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(draft.status, Listing.Status.PENDING)
        self.assertIsNotNone(draft.submitted_at)

    def test_marketplace_home_renders_with_catalog_links(self):
        response = self.client.get(
            reverse("marketplace:home", kwargs={"emirate_slug": "abu-dhabi"})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"/abu-dhabi/listings/")
