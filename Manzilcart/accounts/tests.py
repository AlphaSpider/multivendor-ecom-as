"""
Account tests: OTP verification, rate limits, registration, sign-in, profile updates
and access control.

Codes are deterministic in these tests (``accounts.services.generate_code`` is patched)
so the flow can be exercised end to end without ever reading a value out of a log.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import services
from accounts.admin import OTPChallengeAdmin
from accounts.models import OTPChallenge
from core.exceptions import DomainError, RateLimitedError
from core.models import AuditEvent
from core.security import verify_code
from locations.models import Emirate, ServiceArea
from notifications.backends.base import MockNotificationBackend
from notifications.models import Notification

User = get_user_model()


class AccountTestBase(TestCase):
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

    def setUp(self):
        # Throttle counters and the mock outbox are process-wide; isolate each test.
        cache.clear()
        MockNotificationBackend.outbox.clear()

    # -- helpers -----------------------------------------------------------
    def make_request(self, ip: str = "203.0.113.10"):
        return RequestFactory().post(
            "/accounts/start/",
            REMOTE_ADDR=ip,
            HTTP_USER_AGENT="ManzilCart test suite",
        )

    def start(self, raw_phone: str = "+971501234567", *, code: str = "123456", emirate=None):
        with patch("accounts.services.generate_code", return_value=code):
            return services.start_verification(
                raw_phone=raw_phone, request=self.make_request(), emirate=emirate
            )

    def verified_challenge(
        self, raw_phone: str = "+971501234567", *, code: str = "123456", emirate=None
    ) -> OTPChallenge:
        result = self.start(raw_phone, code=code, emirate=emirate)
        services.verify_challenge_code(
            challenge=result.challenge, submitted_code=code
        )
        result.challenge.refresh_from_db()
        return result.challenge

    def make_user(self, phone: str = "+971509876543", **overrides):
        fields = {
            "display_name": "Test Member",
            "emirate": self.emirate,
            "service_area": self.area,
            "phone_verified_at": timezone.now(),
        }
        fields.update(overrides)
        return User.objects.create_user(phone=phone, **fields)

    def latest_challenge(self, phone: str) -> OTPChallenge:
        return OTPChallenge.objects.filter(phone=phone).latest("created_at")


class VerificationStartTests(AccountTestBase):
    def test_start_normalises_phone_and_stores_only_a_keyed_hash(self):
        result = self.start("050 123 4567")

        challenge = result.challenge
        self.assertEqual(challenge.phone, "+971501234567")
        self.assertEqual(challenge.purpose, OTPChallenge.Purpose.REGISTER)
        self.assertFalse(result.is_returning_member)
        self.assertNotEqual(challenge.code_hash, "123456")
        self.assertTrue(
            verify_code("123456", challenge.code_hash, scope=challenge.phone)
        )

        notification = Notification.objects.get(kind=Notification.Kind.OTP)
        self.assertEqual(notification.recipient, "+971501234567")
        self.assertEqual(notification.status, Notification.Status.SENT)
        self.assertIn("123456", notification.body)
        self.assertNotIn("123456", challenge.code_hash)

    def test_returning_member_gets_a_login_challenge(self):
        self.make_user(phone="+971501234567")

        result = self.start("+971501234567")

        self.assertTrue(result.is_returning_member)
        self.assertEqual(result.challenge.purpose, OTPChallenge.Purpose.LOGIN)
        self.assertEqual(
            result.challenge.user_id,
            User.objects.get(phone="+971501234567").pk,
        )

    def test_invalid_phone_is_rejected_before_any_challenge_is_created(self):
        with self.assertRaises(DomainError):
            self.start("12345")

        self.assertFalse(OTPChallenge.objects.exists())

    def test_starting_again_consumes_the_previous_challenge(self):
        first = self.start(code="111111")
        with patch("accounts.services.generate_code", return_value="222222"):
            with override_settings(OTP_RESEND_COOLDOWN_SECONDS=0):
                second = services.start_verification(
                    raw_phone="+971501234567", request=self.make_request()
                )

        first.challenge.refresh_from_db()
        self.assertIsNotNone(first.challenge.consumed_at)
        self.assertNotEqual(first.challenge.pk, second.challenge.pk)
        self.assertEqual(
            OTPChallenge.objects.filter(
                phone="+971501234567", consumed_at__isnull=True
            ).count(),
            1,
        )


class VerificationCodeTests(AccountTestBase):
    def test_correct_code_marks_the_challenge_verified(self):
        result = self.start(code="246810")

        challenge = services.verify_challenge_code(
            challenge=result.challenge, submitted_code="246810"
        )

        challenge.refresh_from_db()
        self.assertTrue(challenge.is_verified)
        self.assertIsNotNone(challenge.verified_at)
        self.assertIsNone(challenge.consumed_at)
        self.assertEqual(challenge.attempts, 0)

    def test_wrong_code_counts_an_attempt_and_reports_the_remainder(self):
        result = self.start()

        with self.assertRaises(DomainError) as caught:
            services.verify_challenge_code(
                challenge=result.challenge, submitted_code="000000"
            )

        self.assertIn("4 attempt", caught.exception.message)
        result.challenge.refresh_from_db()
        self.assertEqual(result.challenge.attempts, 1)
        self.assertFalse(result.challenge.is_verified)

    def test_attempt_limit_invalidates_the_challenge(self):
        result = self.start()
        challenge = result.challenge

        # Every wrong attempt before the last reports the remaining budget.
        for _ in range(challenge.max_attempts - 1):
            with self.assertRaises(DomainError):
                services.verify_challenge_code(
                    challenge=challenge, submitted_code="000000"
                )

        with self.assertRaises(RateLimitedError):
            services.verify_challenge_code(
                challenge=challenge, submitted_code="000000"
            )

        challenge.refresh_from_db()
        self.assertEqual(challenge.attempts, challenge.max_attempts)
        self.assertIsNotNone(challenge.consumed_at)

        with self.assertRaises(DomainError):
            services.verify_challenge_code(
                challenge=challenge, submitted_code="123456"
            )

    def test_expired_challenge_cannot_be_verified(self):
        result = self.start(code="135790")
        OTPChallenge.objects.filter(pk=result.challenge.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        result.challenge.refresh_from_db()

        with self.assertRaises(DomainError) as caught:
            services.verify_challenge_code(
                challenge=result.challenge, submitted_code="135790"
            )

        self.assertIn("expired", caught.exception.message)
        result.challenge.refresh_from_db()
        self.assertEqual(result.challenge.attempts, 0)

    def test_consumed_challenge_cannot_be_replayed(self):
        challenge = self.verified_challenge(code="135790")
        challenge.mark_consumed()

        with self.assertRaises(DomainError):
            services.verify_challenge_code(
                challenge=challenge, submitted_code="135790"
            )

    def test_resend_replaces_the_code_and_resets_the_attempt_budget(self):
        result = self.start(code="111111")
        OTPChallenge.objects.filter(pk=result.challenge.pk).update(attempts=3)

        with override_settings(OTP_RESEND_COOLDOWN_SECONDS=0):
            with patch("accounts.services.generate_code", return_value="222222"):
                resent = services.resend_verification(
                    challenge=result.challenge, request=self.make_request()
                )

        resent.refresh_from_db()
        self.assertEqual(resent.sends, 2)
        self.assertEqual(resent.attempts, 0)

        with self.assertRaises(DomainError):
            services.verify_challenge_code(
                challenge=resent, submitted_code="111111"
            )

        services.verify_challenge_code(challenge=resent, submitted_code="222222")
        resent.refresh_from_db()
        self.assertTrue(resent.is_verified)
        self.assertEqual(
            Notification.objects.filter(kind=Notification.Kind.OTP).count(), 2
        )

    def test_resend_rejects_consumed_or_expired_challenges(self):
        consumed = self.start(code="111111").challenge
        consumed.mark_consumed()
        with self.assertRaises(DomainError):
            services.resend_verification(
                challenge=consumed, request=self.make_request()
            )

        expired = self.start("+971501234568", code="222222").challenge
        OTPChallenge.objects.filter(pk=expired.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        expired.refresh_from_db()
        with self.assertRaises(DomainError):
            services.resend_verification(
                challenge=expired, request=self.make_request()
            )


class SendRateLimitTests(AccountTestBase):
    def test_repeated_immediate_requests_hit_the_cooldown(self):
        self.start()

        with self.assertRaises(RateLimitedError) as caught:
            self.start()

        self.assertIn("wait", caught.exception.message.lower())

    @override_settings(OTP_RESEND_COOLDOWN_SECONDS=0, OTP_MAX_SENDS_PER_PHONE_PER_HOUR=2)
    def test_hourly_per_phone_limit_is_enforced(self):
        self.start()
        self.start()

        with self.assertRaises(RateLimitedError):
            self.start()

        self.assertEqual(
            OTPChallenge.objects.filter(phone="+971501234567").count(), 2
        )

    @override_settings(OTP_RESEND_COOLDOWN_SECONDS=0, OTP_MAX_SENDS_PER_IP_PER_HOUR=1)
    def test_per_ip_limit_blocks_a_second_number_from_the_same_connection(self):
        self.start("+971501234567")

        with self.assertRaises(RateLimitedError):
            self.start("+971501234568")


class RegistrationTests(AccountTestBase):
    def test_complete_registration_creates_a_verified_member(self):
        challenge = self.verified_challenge()

        user = services.complete_registration(
            challenge=challenge,
            emirate_slug="abu-dhabi",
            service_area_slug="al-reem-island",
            display_name="  Aisha Khan  ",
            email="aisha@example.test",
        )

        self.assertEqual(user.phone, "+971501234567")
        self.assertEqual(user.display_name, "Aisha Khan")
        self.assertEqual(user.email, "aisha@example.test")
        self.assertEqual(user.emirate, self.emirate)
        self.assertEqual(user.service_area, self.area)
        self.assertIsNotNone(user.phone_verified_at)
        self.assertFalse(user.has_usable_password())

        challenge.refresh_from_db()
        self.assertIsNotNone(challenge.consumed_at)
        self.assertEqual(challenge.user, user)
        self.assertEqual(challenge.service_area, self.area)

        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.ACCOUNT_CREATED, entity_id=str(user.pk)
            ).exists()
        )
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.PHONE_VERIFIED, entity_id=str(user.pk)
            ).exists()
        )

    def test_registration_requires_a_verified_challenge(self):
        challenge = self.start().challenge

        with self.assertRaises(DomainError):
            services.complete_registration(
                challenge=challenge,
                emirate_slug="abu-dhabi",
                service_area_slug="al-reem-island",
            )

        self.assertFalse(User.objects.filter(phone=challenge.phone).exists())

    def test_registration_rejects_an_area_from_another_emirate(self):
        challenge = self.verified_challenge()

        with self.assertRaises(DomainError):
            services.complete_registration(
                challenge=challenge,
                emirate_slug="abu-dhabi",
                service_area_slug="al-hamra-village",
            )

        self.assertFalse(User.objects.filter(phone=challenge.phone).exists())

    def test_registration_rejects_an_inactive_emirate(self):
        challenge = self.verified_challenge()
        Emirate.objects.filter(pk=self.emirate.pk).update(is_active=False)

        with self.assertRaises(DomainError):
            services.complete_registration(
                challenge=challenge,
                emirate_slug="abu-dhabi",
                service_area_slug="al-reem-island",
            )

        self.assertFalse(User.objects.filter(phone=challenge.phone).exists())

    def test_registration_rejects_an_inactive_area(self):
        challenge = self.verified_challenge()
        ServiceArea.objects.filter(pk=self.area.pk).update(is_active=False)

        with self.assertRaises(DomainError):
            services.complete_registration(
                challenge=challenge,
                emirate_slug="abu-dhabi",
                service_area_slug="al-reem-island",
            )

        self.assertFalse(User.objects.filter(phone=challenge.phone).exists())

    def test_duplicate_number_cannot_be_registered_twice(self):
        challenge = self.verified_challenge()
        self.make_user(phone=challenge.phone)

        with self.assertRaises(DomainError) as caught:
            services.complete_registration(
                challenge=challenge,
                emirate_slug="abu-dhabi",
                service_area_slug="al-reem-island",
            )

        self.assertIn("already registered", caught.exception.message)
        challenge.refresh_from_db()
        self.assertIsNotNone(challenge.consumed_at)
        self.assertEqual(User.objects.filter(phone=challenge.phone).count(), 1)


class HttpSignInTests(AccountTestBase):
    def _start_and_verify(self, phone: str, *, code: str = "123456", next_url: str = ""):
        with patch("accounts.services.generate_code", return_value=code):
            response = self.client.post(
                reverse("accounts:start"), {"phone": phone}
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("accounts:verify"))

        payload = {"code": code}
        if next_url:
            payload["next"] = next_url
        return self.client.post(reverse("accounts:verify"), payload)

    def test_returning_member_is_signed_in_and_challenge_is_consumed(self):
        user = self.make_user(phone="+971501234567")
        OTPChallenge.objects.all().delete()  # keep only the challenge created below

        response = self._start_and_verify("+971501234567")

        self.assertRedirects(response, reverse("accounts:dashboard"))
        challenge = self.latest_challenge("+971501234567")
        self.assertEqual(challenge.purpose, OTPChallenge.Purpose.LOGIN)
        self.assertIsNotNone(challenge.consumed_at)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.LOGIN_SUCCEEDED, entity_id=str(user.pk)
            ).exists()
        )

    def test_external_next_is_not_used_after_sign_in(self):
        self.make_user(phone="+971501234567")
        OTPChallenge.objects.all().delete()

        response = self._start_and_verify(
            "+971501234567", next_url="https://evil.example/steal"
        )

        self.assertRedirects(response, reverse("accounts:dashboard"))

    def test_local_next_is_honoured_after_sign_in(self):
        self.make_user(phone="+971501234567")
        OTPChallenge.objects.all().delete()

        response = self._start_and_verify(
            "+971501234567", next_url=reverse("accounts:profile")
        )

        self.assertRedirects(response, reverse("accounts:profile"))

    def test_inactive_member_cannot_sign_in(self):
        user = self.make_user(phone="+971501234567", is_active=False)
        OTPChallenge.objects.all().delete()

        response = self._start_and_verify("+971501234567")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not available")
        self.assertNotIn("_auth_user_id", self.client.session)
        challenge = self.latest_challenge("+971501234567")
        self.assertIsNotNone(challenge.consumed_at)
        self.assertFalse(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.LOGIN_SUCCEEDED, entity_id=str(user.pk)
            ).exists()
        )

    def test_registration_flow_creates_and_signs_in_the_member(self):
        session = self.client.session
        session[settings.EMIRATE_SESSION_KEY] = "abu-dhabi"
        session.save()

        phone = "+971501112223"
        with patch("accounts.services.generate_code", return_value="135790"):
            start = self.client.post(reverse("accounts:start"), {"phone": phone})
        self.assertEqual(start.status_code, 302)

        verify = self.client.post(
            reverse("accounts:verify"), {"code": "135790"}
        )
        self.assertRedirects(verify, reverse("accounts:details"))

        details = self.client.post(
            reverse("accounts:details"),
            {
                "display_name": "New Member",
                "email": "new@example.test",
                "service_area": self.area.pk,
            },
        )
        self.assertRedirects(details, reverse("accounts:dashboard"))

        user = User.objects.get(phone=phone)
        self.assertEqual(user.display_name, "New Member")
        self.assertEqual(user.emirate, self.emirate)
        self.assertEqual(user.service_area, self.area)
        self.assertIsNotNone(user.phone_verified_at)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_membership_is_not_disclosed_by_the_phone_entry_response(self):
        self.make_user(phone="+971501234567")

        with patch("accounts.services.generate_code", return_value="123456"):
            existing = self.client.post(
                reverse("accounts:start"), {"phone": "+971501234567"}
            )
            new = self.client.post(
                reverse("accounts:start"), {"phone": "+971501234568"}
            )

        self.assertEqual(existing.status_code, 302)
        self.assertEqual(new.status_code, 302)
        self.assertEqual(existing.url, new.url)
        self.assertEqual(existing.content, new.content)

        self.assertEqual(
            self.latest_challenge("+971501234567").purpose,
            OTPChallenge.Purpose.LOGIN,
        )
        self.assertEqual(
            self.latest_challenge("+971501234568").purpose,
            OTPChallenge.Purpose.REGISTER,
        )

    def test_verify_without_a_session_challenge_redirects_to_start(self):
        response = self.client.get(reverse("accounts:verify"))
        self.assertRedirects(response, reverse("accounts:start"))

    def test_details_without_a_verified_challenge_redirects_to_start(self):
        response = self.client.get(reverse("accounts:details"))
        self.assertRedirects(response, reverse("accounts:start"))


class ProfileAndAccessTests(AccountTestBase):
    def setUp(self):
        super().setUp()
        self.member = self.make_user(phone="+971501234567")

    def test_dashboard_requires_sign_in(self):
        response = self.client.get(reverse("accounts:dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_profile_update_moves_member_between_supported_areas(self):
        self.client.force_login(self.member)

        response = self.client.post(
            reverse("accounts:profile"),
            {
                "display_name": "Updated Name",
                "email": "updated@example.test",
                "service_area": self.other_area.pk,
            },
        )

        self.assertRedirects(response, reverse("accounts:profile"))
        self.member.refresh_from_db()
        self.assertEqual(self.member.display_name, "Updated Name")
        self.assertEqual(self.member.email, "updated@example.test")
        self.assertEqual(self.member.service_area, self.other_area)
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.PROFILE_UPDATED,
                entity_id=str(self.member.pk),
            ).exists()
        )

    def test_profile_rejects_an_area_from_another_marketplace(self):
        self.client.force_login(self.member)
        original_area = self.member.service_area

        response = self.client.post(
            reverse("accounts:profile"),
            {
                "display_name": "Updated Name",
                "email": "",
                "service_area": self.foreign_area.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("service_area", response.context["form"].errors)
        self.member.refresh_from_db()
        self.assertEqual(self.member.service_area, original_area)

    def test_update_profile_service_rejects_an_inactive_area(self):
        ServiceArea.objects.filter(pk=self.area.pk).update(is_active=False)

        with self.assertRaises(DomainError):
            services.update_profile(
                user=self.member,
                display_name="Nope",
                emirate_slug="abu-dhabi",
                service_area_slug="al-reem-island",
            )

        self.member.refresh_from_db()
        self.assertEqual(self.member.service_area, self.area)

    def test_logout_is_post_only_and_clears_the_session(self):
        self.client.force_login(self.member)

        get_response = self.client.get(reverse("accounts:logout"))
        self.assertRedirects(get_response, reverse("locations:landing"))
        self.assertIn("_auth_user_id", self.client.session)

        post_response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(post_response, reverse("locations:landing"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_settings_page_requires_sign_in(self):
        response = self.client.get(reverse("accounts:settings"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_settings_page_shows_preference_sections(self):
        self.client.force_login(self.member)

        response = self.client.get(reverse("accounts:settings"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Preferences")
        self.assertContains(response, "Language")
        self.assertContains(response, "Appearance")
        self.assertContains(response, "data-lang-choice")
        self.assertContains(response, "data-theme-choice")
        self.assertContains(response, str(self.member.phone))
        self.assertContains(response, "Sign out")

    def test_dashboard_shows_activity_counts_and_settings_link(self):
        self.client.force_login(self.member)

        response = self.client.get(reverse("accounts:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("accounts:settings"))
        self.assertContains(response, "Create auction")
        self.assertEqual(response.context["listings_count"], 0)
        self.assertEqual(response.context["deals_count"], 0)


class AdminHardeningTests(AccountTestBase):
    def test_otp_challenge_admin_is_read_only(self):
        request = RequestFactory().get("/admin/")
        request.user = self.make_user(is_staff=True)
        model_admin = OTPChallengeAdmin(OTPChallenge, AdminSite())

        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))
