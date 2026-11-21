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
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import services
from accounts.admin import OTPChallengeAdmin
from accounts.forms import RegistrationForm
from accounts.models import EmailSender, MemberEmail, OTPChallenge
from accounts.otp_delivery import SMTP_PASSWORD_ENV, send_otp_email
from core.exceptions import DomainError, RateLimitedError
from core.models import AuditEvent
from core.security import generate_code, verify_code
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

    def add_email(self, user, email: str = "member@example.test") -> MemberEmail:
        return MemberEmail.objects.create(user=user, email=email)

    def latest_challenge(self, phone: str) -> OTPChallenge:
        return OTPChallenge.objects.filter(phone=phone).latest("created_at")


class VerificationStartTests(AccountTestBase):
    def test_generate_code_returns_a_six_digit_numeric_code(self):
        for _ in range(20):
            code = generate_code()

            self.assertEqual(len(code), settings.OTP_LENGTH)
            self.assertEqual(len(code), 6)
            self.assertTrue(code.isdigit())

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


class MemberRegistrationTests(AccountTestBase):
    def register(self, **overrides):
        values = {
            "phone": "050 123 4567",
            "email": "aisha@example.test",
            "password": "MemberPass123!",
            "display_name": "  Aisha Khan  ",
            "emirate_slug": "abu-dhabi",
            "service_area_slug": "al-reem-island",
        }
        values.update(overrides)
        return services.register_member(**values)

    def test_register_member_creates_an_immediate_unverified_account(self):
        user = self.register()

        self.assertEqual(user.phone, "+971501234567")
        self.assertEqual(user.display_name, "Aisha Khan")
        self.assertEqual(user.email, "aisha@example.test")
        self.assertEqual(user.primary_email, "aisha@example.test")
        self.assertEqual(user.emirate, self.emirate)
        self.assertEqual(user.service_area, self.area)
        self.assertIsNone(user.phone_verified_at)
        self.assertTrue(user.has_usable_password())
        self.assertTrue(user.check_password("MemberPass123!"))

        email = MemberEmail.objects.get(user=user)
        self.assertEqual(email.email, "aisha@example.test")
        self.assertIsNone(email.verified_at)

        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.ACCOUNT_CREATED, entity_id=str(user.pk)
            ).exists()
        )

    def test_register_member_without_email_still_has_password_login(self):
        user = self.register(email="")

        self.assertEqual(user.email, "")
        self.assertFalse(MemberEmail.objects.filter(user=user).exists())
        self.assertTrue(user.check_password("MemberPass123!"))

    def test_registration_form_rejects_weak_passwords(self):
        for password in ("short1!", "lowercase1!", "NoDigits!", "NoSpecial1"):
            with self.subTest(password=password):
                form = RegistrationForm(
                    {
                        "display_name": "Aisha Khan",
                        "phone": "050 123 4567",
                        "email": "aisha@example.test",
                        "service_area": self.area.pk,
                        "password1": password,
                        "password2": password,
                    },
                    emirate=self.emirate,
                )

                self.assertFalse(form.is_valid())
                self.assertIn("password1", form.errors)
        self.assertFalse(User.objects.exists())

    def test_register_member_normalises_and_rejects_duplicate_phone(self):
        self.register(phone="+971501234567")

        with self.assertRaises(DomainError) as caught:
            self.register(phone="050 123 4567")

        self.assertIn("already registered", caught.exception.message)
        self.assertEqual(User.objects.filter(phone="+971501234567").count(), 1)

    def test_register_member_rejects_duplicate_email(self):
        self.register(email="aisha@example.test")

        with self.assertRaises(DomainError) as caught:
            self.register(phone="+971501234568", email="AISHA@example.test")

        self.assertIn("email", caught.exception.message.lower())
        self.assertFalse(User.objects.filter(phone="+971501234568").exists())

    def test_register_member_rejects_an_area_from_another_emirate(self):
        with self.assertRaises(DomainError):
            self.register(service_area_slug="al-hamra-village")

        self.assertFalse(User.objects.filter(phone="+971501234567").exists())

    def test_register_member_rejects_an_inactive_emirate(self):
        Emirate.objects.filter(pk=self.emirate.pk).update(is_active=False)

        with self.assertRaises(DomainError):
            self.register()

        self.assertFalse(User.objects.filter(phone="+971501234567").exists())

    def test_register_member_rejects_an_inactive_area(self):
        ServiceArea.objects.filter(pk=self.area.pk).update(is_active=False)

        with self.assertRaises(DomainError):
            self.register()

        self.assertFalse(User.objects.filter(phone="+971501234567").exists())


class HttpSignInTests(AccountTestBase):
    def _start_and_verify(
        self, identifier: str, *, code: str = "123456", next_url: str = ""
    ):
        payload = {"identifier": identifier}
        if next_url:
            payload["next"] = next_url
        with patch("accounts.services.generate_code", return_value=code):
            response = self.client.post(reverse("accounts:login"), payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("accounts:verify"))
        return self.client.post(reverse("accounts:verify"), {"code": code})

    def test_returning_member_is_signed_in_and_challenge_is_consumed(self):
        user = self.make_user(phone="+971501234567")
        OTPChallenge.objects.all().delete()  # keep only the challenge created below

        response = self._start_and_verify("+971501234567")

        expected_url = reverse(
            "marketplace:home", kwargs={"emirate_slug": user.emirate.slug}
        )
        self.assertRedirects(response, expected_url)
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
        user = self.make_user(phone="+971501234567")
        OTPChallenge.objects.all().delete()

        response = self._start_and_verify(
            "+971501234567", next_url="https://evil.example/steal"
        )

        expected_url = reverse(
            "marketplace:home", kwargs={"emirate_slug": user.emirate.slug}
        )
        self.assertRedirects(response, expected_url)

    def test_local_next_is_honoured_after_sign_in(self):
        self.make_user(phone="+971501234567")
        OTPChallenge.objects.all().delete()

        response = self._start_and_verify(
            "+971501234567", next_url=reverse("accounts:profile")
        )

        self.assertRedirects(response, reverse("accounts:profile"))

    def test_inactive_member_cannot_start_otp_sign_in(self):
        user = self.make_user(phone="+971501234567", is_active=False)
        OTPChallenge.objects.all().delete()

        response = self.client.post(
            reverse("accounts:login"), {"identifier": "+971501234567"}
        )

        # Inactive identifiers take the same decoy path as unknown ones.
        self.assertRedirects(response, reverse("accounts:verify"))
        self.assertNotIn("_auth_user_id", self.client.session)
        challenge = OTPChallenge.objects.get()
        self.assertEqual(challenge.phone, "+000000000000")
        self.assertIsNone(challenge.user)
        self.assertFalse(Notification.objects.filter(kind=Notification.Kind.OTP).exists())
        self.assertFalse(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.LOGIN_SUCCEEDED, entity_id=str(user.pk)
            ).exists()
        )

    def test_verify_page_exposes_resend_cooldown_and_password_fallback(self):
        with patch("accounts.services.generate_code", return_value="123456"):
            self.client.post(
                reverse("accounts:login"), {"identifier": "+971501234567"}
            )

        response = self.client.get(reverse("accounts:verify"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, f'data-cooldown="{settings.OTP_RESEND_COOLDOWN_SECONDS}"'
        )
        self.assertContains(response, "Use a password instead")

    def test_signup_creates_and_signs_in_the_member(self):
        session = self.client.session
        session[settings.EMIRATE_SESSION_KEY] = "abu-dhabi"
        session.save()

        phone = "+971501112223"
        response = self.client.post(
            reverse("accounts:register"),
            {
                "display_name": "New Member",
                "phone": phone,
                "email": "new@example.test",
                "service_area": self.area.pk,
                "password1": "MemberPass123!",
                "password2": "MemberPass123!",
            },
        )
        expected_home = reverse(
            "marketplace:home", kwargs={"emirate_slug": self.emirate.slug}
        )
        self.assertRedirects(response, expected_home)

        user = User.objects.get(phone=phone)
        self.assertEqual(user.display_name, "New Member")
        self.assertEqual(user.primary_email, "new@example.test")
        self.assertEqual(user.emirate, self.emirate)
        self.assertEqual(user.service_area, self.area)
        self.assertIsNone(user.phone_verified_at)
        self.assertTrue(user.check_password("MemberPass123!"))
        self.assertTrue(MemberEmail.objects.filter(user=user).exists())
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

        self.client.post(reverse("accounts:logout"))
        sign_in = self.client.post(
            reverse("accounts:password_login"),
            {"username": phone, "password": "MemberPass123!"},
        )
        self.assertRedirects(sign_in, expected_home)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_signup_rejects_weak_password(self):
        session = self.client.session
        session[settings.EMIRATE_SESSION_KEY] = "abu-dhabi"
        session.save()

        response = self.client.post(
            reverse("accounts:register"),
            {
                "display_name": "Weak Member",
                "phone": "+971501112224",
                "email": "",
                "service_area": self.area.pk,
                "password1": "user@123",
                "password2": "user@123",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "uppercase")
        self.assertFalse(User.objects.filter(phone="+971501112224").exists())

    def test_signup_rejects_mismatched_passwords(self):
        session = self.client.session
        session[settings.EMIRATE_SESSION_KEY] = "abu-dhabi"
        session.save()

        response = self.client.post(
            reverse("accounts:register"),
            {
                "display_name": "Password Member",
                "phone": "+971501112225",
                "email": "",
                "service_area": self.area.pk,
                "password1": "MemberPass123!",
                "password2": "DifferentPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "did not match")
        self.assertFalse(User.objects.filter(phone="+971501112225").exists())

    def test_signup_page_exposes_optional_email_and_required_password(self):
        session = self.client.session
        session[settings.EMIRATE_SESSION_KEY] = "abu-dhabi"
        session.save()

        response = self.client.get(reverse("accounts:register"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create your account")
        self.assertContains(response, "Email (optional)")
        self.assertContains(response, 'name="password1"')
        self.assertContains(response, 'name="password2"')
        self.assertContains(response, reverse("accounts:login"))

    def test_login_page_links_to_password_and_signup(self):
        response = self.client.get(reverse("accounts:login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sign in with password instead")
        self.assertContains(response, reverse("accounts:password_login"))
        self.assertContains(response, reverse("accounts:register"))

    def test_unknown_identifier_gets_the_same_step_without_disclosing_membership(self):
        response = self.client.post(
            reverse("accounts:login"), {"identifier": "+971501234568"}
        )

        self.assertRedirects(response, reverse("accounts:verify"))
        challenge = OTPChallenge.objects.get()
        self.assertEqual(challenge.phone, "+000000000000")
        self.assertIsNone(challenge.user)
        self.assertFalse(Notification.objects.filter(kind=Notification.Kind.OTP).exists())

        # Any submitted code fails with the same generic message.
        verify = self.client.post(reverse("accounts:verify"), {"code": "123456"})
        self.assertEqual(verify.status_code, 200)
        self.assertContains(verify, "Invalid credentials.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_verify_without_a_session_challenge_redirects_to_login(self):
        response = self.client.get(reverse("accounts:verify"))
        self.assertRedirects(response, reverse("accounts:login"))

    def test_authenticated_user_accessing_start_redirects_to_home(self):
        user = self.make_user(phone="+971501234567")
        self.client.force_login(user)
        response = self.client.get(reverse("accounts:start"))
        expected_url = reverse(
            "marketplace:home", kwargs={"emirate_slug": user.emirate.slug}
        )
        self.assertRedirects(response, expected_url)


class EmailSignInTests(AccountTestBase):
    def test_login_with_email_sends_code_to_whatsapp_and_email(self):
        user = self.make_user(phone="+971501234567")
        self.add_email(user, "aisha@example.test")

        with patch("accounts.services.generate_code", return_value="123456"):
            with patch("accounts.otp_delivery.send_otp_email") as email_sender:
                response = self.client.post(
                    reverse("accounts:login"), {"identifier": "AISHA@example.test"}
                )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("accounts:verify"))

        challenge = self.latest_challenge("+971501234567")
        self.assertEqual(challenge.user, user)

        # WhatsApp remains a queued stub; email goes through the configured sender.
        notification = Notification.objects.get(kind=Notification.Kind.OTP)
        self.assertEqual(notification.channel, Notification.Channel.WHATSAPP)
        self.assertIn("123456", notification.body)
        email_sender.assert_called_once_with(
            email="aisha@example.test", otp="123456"
        )

    def test_otp_without_email_sends_only_whatsapp(self):
        self.make_user(phone="+971501234567")

        with patch("accounts.services.generate_code", return_value="123456"):
            self.client.post(
                reverse("accounts:login"), {"identifier": "+971501234567"}
            )

        notifications = Notification.objects.filter(kind=Notification.Kind.OTP)
        self.assertEqual(notifications.count(), 1)
        self.assertEqual(notifications.get().channel, Notification.Channel.WHATSAPP)

    def test_email_otp_verifies_phone_and_email(self):
        user = self.make_user(
            phone="+971501234567", phone_verified_at=None
        )
        email = self.add_email(user, "aisha@example.test")

        with patch("accounts.services.generate_code", return_value="246810"):
            self.client.post(
                reverse("accounts:login"), {"identifier": "aisha@example.test"}
            )
        response = self.client.post(
            reverse("accounts:verify"), {"code": "246810"}
        )

        expected_url = reverse(
            "marketplace:home", kwargs={"emirate_slug": user.emirate.slug}
        )
        self.assertRedirects(response, expected_url)

        user.refresh_from_db()
        email.refresh_from_db()
        self.assertIsNotNone(user.phone_verified_at)
        self.assertIsNotNone(email.verified_at)
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.PHONE_VERIFIED, entity_id=str(user.pk)
            ).exists()
        )
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.EMAIL_VERIFIED, entity_id=str(user.pk)
            ).exists()
        )

    def test_password_login_accepts_an_email(self):
        member = self.make_user(
            phone="+971501234567", password="MemberPass123!"
        )
        self.add_email(member, "aisha@example.test")

        response = self.client.post(
            reverse("accounts:password_login"),
            {"username": "AISHA@example.test", "password": "MemberPass123!"},
        )

        expected_url = reverse(
            "marketplace:home", kwargs={"emirate_slug": member.emirate.slug}
        )
        self.assertRedirects(response, expected_url)
        self.assertEqual(int(self.client.session["_auth_user_id"]), member.pk)


class PasswordPolicyTests(AccountTestBase):
    def test_new_passwords_need_length_upper_digit_and_special(self):
        with self.assertRaises(ValidationError):
            validate_password("user@123")
        with self.assertRaises(ValidationError):
            validate_password("lowercase1!")
        with self.assertRaises(ValidationError):
            validate_password("NoDigits!")
        with self.assertRaises(ValidationError):
            validate_password("NoSpecial1")

        validate_password("MemberPass123!")

    def test_existing_weak_password_still_signs_in_until_changed(self):
        member = self.make_user(phone="+971501234567", password="user@123")

        response = self.client.post(
            reverse("accounts:password_login"),
            {"username": "+971501234567", "password": "user@123"},
        )

        expected_url = reverse(
            "marketplace:home", kwargs={"emirate_slug": member.emirate.slug}
        )
        self.assertRedirects(response, expected_url)

    def test_settings_rejects_a_weak_new_password(self):
        member = self.make_user(phone="+971501234567")
        self.client.force_login(member)

        response = self.client.post(
            reverse("accounts:settings"),
            {
                "new_password1": "user@123",
                "new_password2": "user@123",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "uppercase")
        member.refresh_from_db()
        self.assertFalse(member.has_usable_password())


class PasswordSignInTests(AccountTestBase):
    def setUp(self):
        super().setUp()
        self.member = self.make_user(
            phone="+971501234567", password="MemberPass123!"
        )

    def test_member_can_sign_in_with_phone_and_password(self):
        response = self.client.post(
            reverse("accounts:password_login"),
            {"username": "050 123 4567", "password": "MemberPass123!"},
        )

        expected_url = reverse(
            "marketplace:home", kwargs={"emirate_slug": self.member.emirate.slug}
        )
        self.assertRedirects(response, expected_url)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.member.pk)
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.LOGIN_SUCCEEDED,
                entity_id=str(self.member.pk),
                summary="Signed in with password",
            ).exists()
        )

    def test_wrong_password_does_not_sign_the_member_in(self):
        response = self.client.post(
            reverse("accounts:password_login"),
            {"username": "+971501234567", "password": "wrong-password"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid credentials.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_inactive_member_cannot_sign_in_with_a_password(self):
        self.member.is_active = False
        self.member.save(update_fields=["is_active"])

        response = self.client.post(
            reverse("accounts:password_login"),
            {"username": "+971501234567", "password": "MemberPass123!"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid credentials.")
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(PASSWORD_LOGIN_MAX_ATTEMPTS=2)
    def test_password_login_is_rate_limited(self):
        for _ in range(2):
            response = self.client.post(
                reverse("accounts:password_login"),
                {"username": "+971501234567", "password": "wrong-password"},
            )
            self.assertEqual(response.status_code, 200)

        response = self.client.post(
            reverse("accounts:password_login"),
            {"username": "+971501234567", "password": "MemberPass123!"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Too many sign-in attempts")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_password_login_honours_a_safe_next_url(self):
        response = self.client.post(
            reverse("accounts:password_login") + "?next=" + reverse("accounts:profile"),
            {"username": "+971501234567", "password": "MemberPass123!"},
        )

        self.assertRedirects(response, reverse("accounts:profile"))

    def test_phone_entry_links_to_password_sign_in(self):
        response = self.client.get(reverse("accounts:start"))

        self.assertContains(response, reverse("accounts:password_login"))


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
        self.assertEqual(self.member.primary_email, "updated@example.test")
        self.assertEqual(self.member.service_area, self.other_area)
        self.assertTrue(
            MemberEmail.objects.filter(
                user=self.member, email="updated@example.test"
            ).exists()
        )
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.PROFILE_UPDATED,
                entity_id=str(self.member.pk),
            ).exists()
        )

    def test_profile_rejects_an_email_used_by_another_member(self):
        other = self.make_user(phone="+971509876544")
        self.add_email(other, "taken@example.test")
        self.client.force_login(self.member)

        response = self.client.post(
            reverse("accounts:profile"),
            {
                "display_name": "Updated Name",
                "email": "TAKEN@example.test",
                "service_area": self.area.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)
        self.member.refresh_from_db()
        self.assertEqual(self.member.primary_email, "")
        self.assertFalse(MemberEmail.objects.filter(user=self.member).exists())

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

    def test_settings_page_renders_at_my_settings(self):
        self.assertEqual(reverse("accounts:settings"), "/my/settings/")
        self.client.force_login(self.member)

        response = self.client.get("/my/settings/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-password-rules")
        self.assertNotContains(response, "data-resend-button")

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
        self.assertContains(response, "Set password")
        self.assertContains(response, "Sign out")

    def test_settings_can_set_a_password_for_an_account_without_one(self):
        self.assertFalse(self.member.has_usable_password())
        self.client.force_login(self.member)

        response = self.client.post(
            reverse("accounts:settings"),
            {
                "new_password1": "MemberPass123!",
                "new_password2": "MemberPass123!",
            },
        )

        self.assertRedirects(response, reverse("accounts:settings"))
        self.member.refresh_from_db()
        self.assertTrue(self.member.check_password("MemberPass123!"))
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.PASSWORD_CHANGED,
                entity_id=str(self.member.pk),
            ).exists()
        )

    def test_settings_changes_the_password_with_the_current_password(self):
        self.member.set_password("OldMemberPass123!")
        self.member.save(update_fields=["password"])
        self.client.force_login(self.member)

        response = self.client.post(
            reverse("accounts:settings"),
            {
                "current_password": "OldMemberPass123!",
                "new_password1": "NewMemberPass123!",
                "new_password2": "NewMemberPass123!",
            },
        )

        self.assertRedirects(response, reverse("accounts:settings"))
        self.member.refresh_from_db()
        self.assertTrue(self.member.check_password("NewMemberPass123!"))

    def test_settings_requires_the_current_password_when_changing_it(self):
        self.member.set_password("OldMemberPass123!")
        self.member.save(update_fields=["password"])
        self.client.force_login(self.member)

        response = self.client.post(
            reverse("accounts:settings"),
            {
                "new_password1": "NewMemberPass123!",
                "new_password2": "NewMemberPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.member.refresh_from_db()
        self.assertTrue(self.member.check_password("OldMemberPass123!"))

    def test_settings_rejects_a_wrong_current_password(self):
        self.member.set_password("OldMemberPass123!")
        self.member.save(update_fields=["password"])
        self.client.force_login(self.member)

        response = self.client.post(
            reverse("accounts:settings"),
            {
                "current_password": "WrongPass123!",
                "new_password1": "NewMemberPass123!",
                "new_password2": "NewMemberPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not correct")
        self.member.refresh_from_db()
        self.assertTrue(self.member.check_password("OldMemberPass123!"))

    def test_dashboard_shows_activity_counts_and_settings_link(self):
        self.client.force_login(self.member)

        response = self.client.get(reverse("accounts:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("accounts:settings"))
        self.assertContains(response, "Apply to auction")
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


class EmailSenderTests(AccountTestBase):
    def make_sender(self, **overrides):
        values = {
            "sender_email": "otp@example.test",
            "smtp_host": "smtp.example.test",
            "smtp_port": 587,
            "use_tls": True,
            "smtp_username": "otp-account",
            "is_active": True,
        }
        values.update(overrides)
        return EmailSender.objects.create(**values)

    def test_activating_a_sender_deactivates_the_previous_one(self):
        first = self.make_sender()
        second = self.make_sender(sender_email="new@example.test")

        first.refresh_from_db()
        self.assertTrue(second.is_active)
        self.assertFalse(first.is_active)

    @patch("accounts.otp_delivery.EmailMessage")
    @patch("accounts.otp_delivery.EmailBackend")
    def test_send_otp_email_uses_the_active_sender(self, backend_cls, message_cls):
        sender = self.make_sender()
        message = message_cls.return_value
        message.send.return_value = 1

        with patch.dict("os.environ", {SMTP_PASSWORD_ENV: "smtp-secret"}):
            result = send_otp_email("member@example.test", "123456")

        self.assertTrue(result)
        backend_cls.assert_called_once_with(
            alias="otp",
            host="smtp.example.test",
            port=587,
            username="otp-account",
            password="smtp-secret",
            use_tls=True,
            use_ssl=False,
            timeout=10,
            fail_silently=False,
        )
        kwargs = message_cls.call_args.kwargs
        self.assertIs(kwargs["connection"], backend_cls.return_value)
        self.assertEqual(kwargs["from_email"], "otp@example.test")
        self.assertEqual(kwargs["to"], ["member@example.test"])
        self.assertIn("123456", kwargs["body"])
        message.send.assert_called_once_with(fail_silently=False)

    @patch("accounts.otp_delivery.EmailMessage")
    @patch("accounts.otp_delivery.EmailBackend")
    def test_email_failure_does_not_raise(self, backend_cls, message_cls):
        self.make_sender()
        message_cls.return_value.send.side_effect = RuntimeError("SMTP down")

        with patch.dict("os.environ", {SMTP_PASSWORD_ENV: "smtp-secret"}):
            result = send_otp_email("member@example.test", "123456")

        self.assertFalse(result)

    @patch("accounts.otp_delivery.EmailBackend")
    def test_missing_configuration_does_not_raise(self, backend_cls):
        self.assertFalse(send_otp_email("member@example.test", "123456"))

        self.make_sender()
        with patch.dict("os.environ", {SMTP_PASSWORD_ENV: ""}):
            self.assertFalse(send_otp_email("member@example.test", "123456"))

        backend_cls.assert_not_called()
