"""
Location tests: the emirate picker, marketplace routing, switching, JSON area lookup,
active-only selectors and geographic validation.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.admin.sites import AdminSite
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase
from django.urls import reverse

from core.exceptions import LocationError
from core.models import AuditEvent
from locations import selectors
from locations.admin import AuditEventAdmin, ServiceAreaAdmin
from locations.models import (
    Emirate,
    ServiceArea,
    ServiceAreaCoverage,
    ServiceOption,
)
from locations.services import safe_redirect_target
from locations.validation import (
    validate_emirate_service_area,
    validate_registration_location,
)


class LocationTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.emirate = Emirate.objects.get(slug="abu-dhabi")
        cls.other_emirate = Emirate.objects.get(slug="ras-al-khaimah")
        cls.area = ServiceArea.objects.get(
            emirate=cls.emirate, slug="al-reem-island"
        )
        cls.other_area = ServiceArea.objects.get(
            emirate=cls.emirate, slug="yas-island"
        )
        cls.foreign_area = ServiceArea.objects.get(
            emirate=cls.other_emirate, slug="al-hamra-village"
        )

    def find_area(self, slug: str, emirate=None) -> ServiceArea:
        return ServiceArea.objects.get(
            emirate=emirate or self.emirate, slug=slug
        )

    def set_active(self, instance, value: bool):
        type(instance).objects.filter(pk=instance.pk).update(is_active=value)
        instance.refresh_from_db()
        return instance


class LandingTests(LocationTestBase):
    def test_landing_lists_only_active_emirates_in_display_order(self):
        self.set_active(self.other_emirate, False)

        response = self.client.get(reverse("locations:landing"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [emirate.slug for emirate in response.context["emirates"]],
            ["abu-dhabi"],
        )
        self.assertFalse(response.context["no_emirates_available"])

    def test_remembered_emirate_is_highlighted_not_auto_applied(self):
        session = self.client.session
        session[settings.EMIRATE_SESSION_KEY] = "ras-al-khaimah"
        session.save()

        response = self.client.get(reverse("locations:landing"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["remembered_emirate"].slug, "ras-al-khaimah"
        )
        self.assertEqual(len(response.context["emirates"]), 2)

    def test_stale_remembered_emirate_is_forgotten(self):
        self.set_active(self.other_emirate, False)
        session = self.client.session
        session[settings.EMIRATE_SESSION_KEY] = "ras-al-khaimah"
        session.save()

        response = self.client.get(reverse("locations:landing"))

        self.assertIsNone(response.context["remembered_emirate"])
        self.assertNotIn(settings.EMIRATE_SESSION_KEY, self.client.session)

    def test_landing_reports_when_no_marketplace_is_open(self):
        Emirate.objects.update(is_active=False)

        response = self.client.get(reverse("locations:landing"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["no_emirates_available"])
        self.assertEqual(list(response.context["emirates"]), [])


class MarketplaceHomeTests(LocationTestBase):
    def test_direct_emirate_url_renders_and_remembers_the_choice(self):
        response = self.client.get(
            reverse("marketplace:home", kwargs={"emirate_slug": "abu-dhabi"})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["emirate"], self.emirate)
        self.assertEqual(
            self.client.session[settings.EMIRATE_SESSION_KEY], "abu-dhabi"
        )

    def test_inactive_emirate_url_returns_404(self):
        self.set_active(self.other_emirate, False)

        response = self.client.get(
            reverse(
                "marketplace:home", kwargs={"emirate_slug": "ras-al-khaimah"}
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_unknown_emirate_url_returns_404(self):
        response = self.client.get("/sharjah/")

        self.assertEqual(response.status_code, 404)

    def test_marketplace_home_only_offers_active_areas(self):
        self.set_active(self.area, False)

        response = self.client.get(
            reverse("marketplace:home", kwargs={"emirate_slug": "abu-dhabi"})
        )

        self.assertEqual(response.status_code, 200)
        slugs = [area.slug for area in response.context["service_areas"]]
        self.assertNotIn("al-reem-island", slugs)
        self.assertIn("yas-island", slugs)
        for area in response.context["service_areas"]:
            self.assertTrue(area.is_active)
            self.assertEqual(area.emirate, self.emirate)


class ServiceAreaJsonTests(LocationTestBase):
    def test_returns_active_areas_for_the_requested_emirate(self):
        response = self.client.get(
            reverse(
                "marketplace:service_area_options",
                kwargs={"emirate_slug": "abu-dhabi"},
            )
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["emirate"]["slug"], "abu-dhabi")
        self.assertEqual(payload["emirate"]["name"], "Abu Dhabi")
        slugs = {item["slug"] for item in payload["areas"]}
        self.assertIn("al-reem-island", slugs)
        self.assertIn("yas-island", slugs)
        self.assertNotIn("al-hamra-village", slugs)
        self.assertEqual(
            len(payload["areas"]),
            ServiceArea.objects.filter(emirate=self.emirate, is_active=True).count(),
        )

    def test_inactive_area_is_omitted(self):
        self.set_active(self.area, False)

        response = self.client.get(
            reverse(
                "marketplace:service_area_options",
                kwargs={"emirate_slug": "abu-dhabi"},
            )
        )

        slugs = {item["slug"] for item in response.json()["areas"]}
        self.assertNotIn("al-reem-island", slugs)

    def test_inactive_or_unknown_emirate_returns_404(self):
        self.set_active(self.other_emirate, False)

        inactive = self.client.get(
            reverse(
                "marketplace:service_area_options",
                kwargs={"emirate_slug": "ras-al-khaimah"},
            )
        )
        unknown = self.client.get(
            reverse(
                "marketplace:service_area_options",
                kwargs={"emirate_slug": "sharjah"},
            )
        )

        self.assertEqual(inactive.status_code, 404)
        self.assertEqual(unknown.status_code, 404)

    def test_only_get_is_allowed(self):
        response = self.client.post(
            reverse(
                "marketplace:service_area_options",
                kwargs={"emirate_slug": "abu-dhabi"},
            )
        )

        self.assertEqual(response.status_code, 405)


class SwitchMarketplaceTests(LocationTestBase):
    def test_switch_persists_the_choice_and_redirects_to_that_marketplace(self):
        response = self.client.post(
            reverse("locations:switch_marketplace"),
            {"emirate": "ras-al-khaimah"},
        )

        self.assertRedirects(
            response,
            reverse(
                "marketplace:home", kwargs={"emirate_slug": "ras-al-khaimah"}
            ),
        )
        self.assertEqual(
            self.client.session[settings.EMIRATE_SESSION_KEY], "ras-al-khaimah"
        )

    def test_switch_honours_a_local_next_target(self):
        # Switch to RAK but return to the Abu Dhabi page: next must win over the
        # default destination for the newly selected marketplace.
        target = reverse(
            "marketplace:home", kwargs={"emirate_slug": "abu-dhabi"}
        )

        response = self.client.post(
            reverse("locations:switch_marketplace"),
            {"emirate": "ras-al-khaimah", "next": target},
        )

        self.assertRedirects(response, target)

    def test_switch_rejects_external_and_protocol_relative_next_targets(self):
        expected = reverse(
            "marketplace:home", kwargs={"emirate_slug": "ras-al-khaimah"}
        )

        for target in (
            "https://evil.example/steal",
            "//evil.example/steal",
        ):
            with self.subTest(target=target):
                response = self.client.post(
                    reverse("locations:switch_marketplace"),
                    {"emirate": "ras-al-khaimah", "next": target},
                )
                self.assertRedirects(response, expected)

    def test_switch_rejects_unknown_and_inactive_emirates(self):
        self.set_active(self.other_emirate, False)

        for slug in ("sharjah", "ras-al-khaimah"):
            with self.subTest(slug=slug):
                response = self.client.post(
                    reverse("locations:switch_marketplace"), {"emirate": slug}
                )
                self.assertRedirects(
                    response,
                    f"{reverse('locations:landing')}?invalid_emirate=1",
                )

    def test_switch_without_a_slug_returns_to_the_picker(self):
        response = self.client.post(
            reverse("locations:switch_marketplace"), {"emirate": ""}
        )

        self.assertRedirects(response, reverse("locations:landing"))

    def test_switch_is_post_only(self):
        response = self.client.get(reverse("locations:switch_marketplace"))

        self.assertEqual(response.status_code, 405)


class SelectorTests(LocationTestBase):
    def test_active_emirates_filter_and_order(self):
        self.set_active(self.other_emirate, False)

        slugs = [emirate.slug for emirate in selectors.active_emirates()]

        self.assertEqual(slugs, ["abu-dhabi"])
        # ``all_emirates`` deliberately keeps inactive rows for admin/reporting.
        self.assertIn(
            "ras-al-khaimah", [item.slug for item in selectors.all_emirates()]
        )

    def test_service_areas_can_include_inactive_rows_on_request(self):
        self.set_active(self.area, False)

        active_slugs = {
            area.slug for area in selectors.service_areas_for_emirate(self.emirate)
        }
        all_slugs = {
            area.slug
            for area in selectors.service_areas_for_emirate(
                self.emirate, active_only=False
            )
        }

        self.assertNotIn("al-reem-island", active_slugs)
        self.assertIn("al-reem-island", all_slugs)
        self.assertEqual(selectors.service_areas_for_emirate(None).count(), 0)

    def test_active_area_lookup_is_scoped_to_its_emirate(self):
        self.assertEqual(
            selectors.active_service_area_for_slug(self.emirate, "al-reem-island"),
            self.area,
        )
        self.assertIsNone(
            selectors.active_service_area_for_slug(
                self.other_emirate, "al-reem-island"
            )
        )
        self.assertIsNone(
            selectors.active_service_area_for_slug(self.emirate, "not-an-area")
        )

        self.set_active(self.area, False)
        self.assertIsNone(
            selectors.active_service_area_for_slug(self.emirate, "al-reem-island")
        )

    def test_available_service_options_are_opt_in(self):
        options = selectors.available_service_options(self.area)
        self.assertEqual(
            [option.code for option in options],
            ["delivery", "assembly", "disassembly"],
        )

        # Al Shamkha only has the delivery coverage row seeded.
        shamkha = self.find_area("al-shamkha")
        self.assertEqual(
            [option.code for option in selectors.available_service_options(shamkha)],
            ["delivery"],
        )
        self.assertEqual(
            selectors.available_service_options(None).count(), 0
        )

    def test_unavailable_coverage_hides_the_option(self):
        ServiceAreaCoverage.objects.filter(
            service_area=self.area, service_option__code="delivery"
        ).update(is_available=False)

        codes = [
            option.code
            for option in selectors.available_service_options(self.area)
        ]

        self.assertNotIn("delivery", codes)
        self.assertEqual(codes, ["assembly", "disassembly"])

    def test_inactive_service_option_is_hidden_everywhere(self):
        assembly = ServiceOption.objects.get(code="assembly")
        self.set_active(assembly, False)

        codes = [
            option.code
            for option in selectors.available_service_options(self.area)
        ]

        self.assertNotIn("assembly", codes)
        self.assertIsNone(selectors.service_option_by_id(assembly.pk))

    def test_emirate_from_session_clears_a_stale_slug(self):
        session = self.client.session
        session[settings.EMIRATE_SESSION_KEY] = "not-a-market"
        session.save()

        request = self.client.get(reverse("locations:landing")).wsgi_request

        self.assertIsNone(selectors.emirate_from_session(request))
        self.assertNotIn(settings.EMIRATE_SESSION_KEY, self.client.session)


class ValidationTests(LocationTestBase):
    def test_valid_pair_is_returned(self):
        emirate, area = validate_emirate_service_area(self.emirate, self.area)
        self.assertEqual(emirate, self.emirate)
        self.assertEqual(area, self.area)

    def test_missing_values_are_rejected(self):
        with self.assertRaises(LocationError):
            validate_emirate_service_area(None, self.area)
        with self.assertRaises(LocationError):
            validate_emirate_service_area(self.emirate, None)

    def test_area_from_another_emirate_is_rejected(self):
        with self.assertRaises(LocationError):
            validate_emirate_service_area(self.emirate, self.foreign_area)

    def test_inactive_records_are_rejected_when_active_is_required(self):
        self.set_active(self.emirate, False)
        with self.assertRaises(LocationError):
            validate_emirate_service_area(self.emirate, self.area)

        validate_emirate_service_area(
            self.emirate, self.area, require_active=False
        )

        self.set_active(self.emirate, True)
        self.set_active(self.area, False)
        with self.assertRaises(LocationError):
            validate_emirate_service_area(self.emirate, self.area)

        validate_emirate_service_area(
            self.emirate, self.area, require_active=False
        )

    def test_registration_location_resolves_and_requires_active_records(self):
        emirate, area = validate_registration_location(
            "abu-dhabi", "al-reem-island"
        )
        self.assertEqual(emirate, self.emirate)
        self.assertEqual(area, self.area)

        with self.assertRaises(LocationError):
            validate_registration_location("sharjah", "al-reem-island")
        with self.assertRaises(LocationError):
            validate_registration_location("abu-dhabi", "al-hamra-village")

        self.set_active(self.area, False)
        with self.assertRaises(LocationError):
            validate_registration_location("abu-dhabi", "al-reem-island")

    def test_safe_redirect_target_keeps_only_local_urls(self):
        class DummyRequest:
            def get_host(self):
                return "testserver"

            def is_secure(self):
                return False

        request = DummyRequest()
        self.assertEqual(
            safe_redirect_target("/abu-dhabi/", request, "/fallback/"),
            "/abu-dhabi/",
        )
        self.assertEqual(
            safe_redirect_target("https://evil.example/", request, "/fallback/"),
            "/fallback/",
        )
        self.assertEqual(
            safe_redirect_target("//evil.example/", request, "/fallback/"),
            "/fallback/",
        )
        self.assertEqual(
            safe_redirect_target(None, request, "/fallback/"), "/fallback/"
        )


class GeographyModelTests(TestCase):
    def test_slugs_are_normalised_to_lowercase(self):
        emirate = Emirate.objects.create(name="Test Emirate", slug="Test-Emirate")

        self.assertEqual(emirate.slug, "test-emirate")
        self.assertEqual(emirate.url_slug, "test-emirate")
        self.assertEqual(str(emirate), "Test Emirate")

    def test_database_rejects_an_uppercase_slug_update(self):
        emirate = Emirate.objects.get(slug="abu-dhabi")

        with self.assertRaises(IntegrityError), transaction.atomic():
            Emirate.objects.filter(pk=emirate.pk).update(slug="ABU-DHABI")

    def test_area_string_includes_the_emirate(self):
        area = ServiceArea.objects.get(
            emirate__slug="abu-dhabi", slug="al-reem-island"
        )

        self.assertEqual(str(area), "Al Reem Island (Abu Dhabi)")

    def test_coverage_string_reports_availability(self):
        coverage = ServiceAreaCoverage.objects.select_related(
            "service_option", "service_area"
        ).first()

        self.assertIn(str(coverage.service_option), str(coverage))
        self.assertIn("available", str(coverage))


class AdminConfigurationTests(LocationTestBase):
    def test_existing_area_cannot_be_moved_to_another_emirate(self):
        model_admin = ServiceAreaAdmin(ServiceArea, AdminSite())
        request = RequestFactory().get("/admin/")

        self.assertIn(
            "emirate", model_admin.get_readonly_fields(request, self.area)
        )
        self.assertNotIn("emirate", model_admin.get_readonly_fields(request))

    def test_audit_trail_admin_is_read_only(self):
        class StaffUser:
            is_active = True
            is_staff = True
            is_superuser = True

        request = RequestFactory().get("/admin/")
        request.user = StaffUser()
        model_admin = AuditEventAdmin(AuditEvent, AdminSite())

        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))
        self.assertTrue(model_admin.has_view_permission(request))
