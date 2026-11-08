"""Catalog tests: public visibility, emirate scoping, owner flows and page wiring."""

from __future__ import annotations

import shutil
import tempfile
import uuid
from decimal import Decimal
from io import BytesIO

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image as PILImage

from catalog.models import Category, Listing, ListingImage, WishlistItem
from locations.models import Emirate, ServiceArea

User = get_user_model()


def make_image_file(name="photo.png"):
    """A small valid upload, as Pillow-encoded bytes wrapped for the test client."""
    buffer = BytesIO()
    PILImage.new("RGB", (300, 300), (200, 120, 80)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


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

    def test_condition_choices_exclude_new_items(self):
        values = {value for value, _label in Listing.Condition.choices}
        self.assertNotIn("new", values)
        self.assertIn(Listing.Condition.LIKE_NEW, values)

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

    def test_detail_shows_similar_products_from_same_category_and_emirate(self):
        now = timezone.now()
        similar = Listing.objects.create(
            seller=self.seller,
            title="Matching side table",
            slug="matching-side-table",
            description="A matching piece for the same room.",
            category=self.category,
            condition=Listing.Condition.GOOD,
            asking_price=Decimal("250.00"),
            emirate=self.abu_dhabi,
            service_area=self.ad_area,
            status=Listing.Status.APPROVED,
            submitted_at=now,
            approved_at=now,
            published_at=now,
        )
        other_category = (
            Category.objects.filter(is_active=True)
            .exclude(pk=self.category.pk)
            .first()
        )
        unrelated = Listing.objects.create(
            seller=self.seller,
            title="Unrelated bed frame",
            slug="unrelated-bed-frame",
            description="A different category.",
            category=other_category,
            condition=Listing.Condition.GOOD,
            asking_price=Decimal("500.00"),
            emirate=self.abu_dhabi,
            service_area=self.ad_area,
            status=Listing.Status.APPROVED,
            submitted_at=now,
            approved_at=now,
            published_at=now,
        )
        other_emirate = Listing.objects.create(
            seller=self.seller,
            title="Similar item in Ras Al Khaimah",
            slug="similar-item-in-ras-al-khaimah",
            description="A matching category in another emirate.",
            category=self.category,
            condition=Listing.Condition.GOOD,
            asking_price=Decimal("300.00"),
            emirate=self.rak,
            service_area=self.rak_area,
            status=Listing.Status.APPROVED,
            submitted_at=now,
            approved_at=now,
            published_at=now,
        )

        response = self.client.get(self.listing.get_absolute_url())

        self.assertContains(response, "Similar products")
        self.assertContains(response, similar.title)
        self.assertContains(response, f"?category={self.category.slug}")
        self.assertNotContains(response, unrelated.title)
        self.assertNotContains(response, other_emirate.title)
        self.assertEqual(response.context["similar_listings"], [similar])

    def test_detail_omits_similar_products_when_category_has_no_other_items(self):
        response = self.client.get(self.listing.get_absolute_url())
        self.assertNotContains(response, "Similar products")

    def test_buyer_can_save_and_remove_a_product_from_the_wishlist(self):
        self.client.force_login(self.buyer)
        toggle_url = reverse(
            "catalog:wishlist_toggle",
            kwargs={"emirate_slug": "abu-dhabi", "slug": self.listing.slug},
        )

        response = self.client.post(
            toggle_url, {"next": self.listing.get_absolute_url()}
        )
        self.assertRedirects(response, self.listing.get_absolute_url())
        self.assertTrue(
            WishlistItem.objects.filter(
                user=self.buyer, listing=self.listing
            ).exists()
        )

        detail = self.client.get(self.listing.get_absolute_url())
        self.assertContains(detail, "Saved to wishlist")

        wishlist_page = self.client.get(reverse("accounts:wishlist"))
        self.assertEqual(wishlist_page.status_code, 200)
        self.assertContains(wishlist_page, "Walnut coffee table")
        self.assertEqual(wishlist_page.context["wishlist_count"], 1)

        response = self.client.post(
            toggle_url, {"next": reverse("accounts:wishlist")}
        )
        self.assertRedirects(response, reverse("accounts:wishlist"))
        self.assertFalse(
            WishlistItem.objects.filter(
                user=self.buyer, listing=self.listing
            ).exists()
        )
        self.assertContains(
            self.client.get(reverse("accounts:wishlist")),
            "No saved products yet",
        )

    def test_wishlist_requires_sign_in(self):
        response = self.client.post(
            reverse(
                "catalog:wishlist_toggle",
                kwargs={"emirate_slug": "abu-dhabi", "slug": self.listing.slug},
            )
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])
        self.assertFalse(WishlistItem.objects.exists())

    def test_only_public_products_can_be_added_to_a_wishlist(self):
        self.listing.status = Listing.Status.PENDING
        self.listing.published_at = None
        self.listing.save(update_fields=["status", "published_at"])
        self.client.force_login(self.buyer)

        response = self.client.post(
            reverse(
                "catalog:wishlist_toggle",
                kwargs={"emirate_slug": "abu-dhabi", "slug": self.listing.slug},
            )
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(WishlistItem.objects.exists())

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
        # Moderation requires the minimum photo set before a draft can go live.
        for index in range(settings.MIN_IMAGES_PER_LISTING):
            ListingImage.objects.create(
                listing=draft, image=make_image_file(f"photo-{index}.png")
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

    def test_submission_requires_the_minimum_photo_count(self):
        draft = Listing.objects.create(
            seller=self.seller,
            title="Under-photographed shelf",
            slug=f"under-photographed-{uuid.uuid4().hex[:6]}",
            description="Needs a review.",
            category=self.category,
            condition=Listing.Condition.FAIR,
            asking_price=Decimal("90.00"),
            emirate=self.abu_dhabi,
            service_area=self.ad_area,
            status=Listing.Status.DRAFT,
        )
        for index in range(settings.MIN_IMAGES_PER_LISTING - 1):
            ListingImage.objects.create(
                listing=draft, image=make_image_file(f"photo-{index}.png")
            )
        self.client.force_login(self.seller)

        response = self.client.post(
            reverse(
                "catalog:listing_action",
                kwargs={"emirate_slug": "abu-dhabi", "slug": draft.slug},
            ),
            {"action": "submit"},
            follow=True,
        )
        draft.refresh_from_db()
        self.assertEqual(draft.status, Listing.Status.DRAFT)
        self.assertContains(
            response,
            f"Add at least {settings.MIN_IMAGES_PER_LISTING} photos",
        )

    def test_marketplace_home_renders_with_catalog_links(self):
        response = self.client.get(
            reverse("marketplace:home", kwargs={"emirate_slug": "abu-dhabi"})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"/abu-dhabi/listings/")
        self.assertContains(response, "data-search-suggestions")

    def test_search_suggestions_return_matching_categories_with_images(self):
        ListingImage.objects.create(
            listing=self.listing,
            image="listings/demo/category-sample.jpg",
            is_cover=True,
        )

        response = self.client.get(
            reverse(
                "catalog:search_suggestions",
                kwargs={"emirate_slug": "abu-dhabi"},
            ),
            {"q": self.category.name[0]},
        )

        self.assertEqual(response.status_code, 200)
        suggestions = response.json()["suggestions"]
        self.assertTrue(suggestions)
        self.assertEqual(suggestions[0]["name"], self.category.name)
        self.assertTrue(suggestions[0]["image_url"])
        self.assertIn("category=", suggestions[0]["url"])
        for suggestion in suggestions:
            self.assertTrue(
                suggestion["name"].lower().startswith(
                    self.category.name[0].lower()
                )
            )

    def test_search_suggestions_are_empty_for_unknown_categories(self):
        response = self.client.get(
            reverse(
                "catalog:search_suggestions",
                kwargs={"emirate_slug": "abu-dhabi"},
            ),
            {"q": "zzzz-no-category"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["suggestions"], [])


class ListingPhotoMinimumTests(CatalogTestCase):
    """The create form requires ``MIN_IMAGES_PER_LISTING`` photos, like auctions."""

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

    def _create_url(self):
        return reverse("catalog:listing_create", kwargs={"emirate_slug": "abu-dhabi"})

    def _data(self, file_names):
        return {
            "title": "Photo count chair",
            "description": "A solid, comfortable chair in daily use.",
            "category": str(self.category.pk),
            "condition": Listing.Condition.GOOD,
            "asking_price": "100",
            "service_area": str(self.ad_area.pk),
            "images": [make_image_file(name) for name in file_names],
        }

    def test_create_rejects_fewer_than_the_minimum_photos(self):
        self.client.force_login(self.seller)

        response = self.client.post(
            self._create_url(),
            self._data(["front.png", "side.png"]),
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            f"at least {settings.MIN_IMAGES_PER_LISTING} photos",
            status_code=400,
        )
        self.assertFalse(Listing.objects.filter(title="Photo count chair").exists())

    def test_create_accepts_exactly_the_minimum_photos(self):
        self.client.force_login(self.seller)

        response = self.client.post(
            self._create_url(),
            self._data(["front.png", "side.png", "leg.png"]),
            format="multipart",
        )

        self.assertEqual(response.status_code, 302)
        listing = Listing.objects.get(title="Photo count chair")
        self.assertEqual(listing.images.count(), settings.MIN_IMAGES_PER_LISTING)
        self.assertEqual(listing.status, Listing.Status.PENDING)
