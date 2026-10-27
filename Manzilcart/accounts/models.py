"""
Individual member accounts.

Design decisions
----------------
* **Custom user model, created before the first migration** (non-negotiable - swapping
  ``AUTH_USER_MODEL`` later is extremely painful).
* **Mobile number is the identity.** ``USERNAME_FIELD = "phone"``; the number is stored
  in E.164 form and is unique. There are no usernames, no vendor/business accounts and
  no social logins.
* **No password for members.** Registration is completed by verifying a WhatsApp OTP,
  so member accounts are created with an unusable password; sessions are
  server-managed (``django.contrib.sessions``). Staff/superusers, created with
  ``createsuperuser``, keep a password so they can use Django Admin.
* **Profile data lives on the user.** A separate ``UserProfile`` table would be a
  1:1 row with no independent lifecycle, so it is deliberately omitted.
* **A pending registration is not a user row.** Verification state lives in
  ``OTPChallenge``, which means an unverified number can never squat on a phone
  number or appear in the member directory.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import TimeStampedModel
from core.phone import display_phone, normalise_phone


class UserManager(BaseUserManager):
    """
    Manager for the phone-identified user model.

    ``create_user`` expects an **already normalised** E.164 number (services normalise
    untrusted input first). ``create_superuser`` is used by ``createsuperuser`` and
    by the test suite.
    """

    use_in_migrations = True

    def _normalise(self, phone: str) -> str:
        if not phone:
            raise ValueError("A mobile number is required.")
        return normalise_phone(phone).e164

    def create_user(self, phone: str, password: str | None = None, **extra_fields):
        phone = self._normalise(phone)
        email = extra_fields.pop("email", "") or ""
        user = self.model(phone=phone, email=self.normalize_email(email), **extra_fields)
        if password:
            user.set_password(password)
        else:
            # Members authenticate with an OTP, never with a password.
            user.set_unusable_password()
        user.full_clean(exclude=["password", "last_login"])
        user.save(using=self._db)
        return user

    def create_superuser(self, phone: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("phone_verified_at", timezone.now())
        if not extra_fields.get("is_staff"):
            raise ValueError("Superuser must have is_staff=True.")
        if not extra_fields.get("is_superuser"):
            raise ValueError("Superuser must have is_superuser=True.")
        return self.create_user(phone, password=password, **extra_fields)

    def verified(self):
        """Members whose mobile number has been verified."""
        return self.get_queryset().filter(phone_verified_at__isnull=False)


class User(AbstractUser, TimeStampedModel):
    """
    An individual ManzilCart member (or staff user).

    ``date_joined`` is kept for Django compatibility; ``created_at``/``updated_at`` give
    us a consistent audit-friendly timestamp pair shared with every other model.
    """

    # Identity: mobile number instead of username.
    username = None
    phone = models.CharField(
        max_length=20,
        unique=True,
        help_text="E.164 format, e.g. +971501234567. Verified through WhatsApp OTP.",
    )
    email = models.EmailField(
        blank=True,
        help_text="Optional. Used for auction and deal notifications when provided.",
    )
    phone_verified_at = models.DateTimeField(null=True, blank=True)

    class SellerStatus(models.TextChoices):
        # Mobile verification is today's KYC gate, so existing members default
        # to verified; staff can downgrade or suspend from the vendor page.
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified"
        SUSPENDED = "suspended", "Suspended"

    seller_status = models.CharField(
        max_length=16,
        choices=SellerStatus.choices,
        default=SellerStatus.VERIFIED,
        db_index=True,
        help_text=(
            "Vendor governance state. A suspended seller's public items are "
            "delisted automatically."
        ),
    )
    display_name = models.CharField(
        max_length=64,
        blank=True,
        help_text="Name shown to other members on listings and in chat.",
    )

    # Marketplace membership. Staff accounts may leave these empty.
    emirate = models.ForeignKey(
        "locations.Emirate",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="members",
    )
    service_area = models.ForeignKey(
        "locations.ServiceArea",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="members",
    )

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()

    class Meta:
        verbose_name = "member"
        verbose_name_plural = "members"
        ordering = ["-date_joined"]
        constraints = [
            # Either both location fields are set, or neither (staff accounts). The
            # composite foreign key added in the migration then guarantees that a set
            # pair actually belongs together.
            models.CheckConstraint(
                condition=(
                    Q(emirate__isnull=True, service_area__isnull=True)
                    | Q(emirate__isnull=False, service_area__isnull=False)
                ),
                name="user_emirate_area_both_or_neither",
            ),
        ]
        indexes = [
            models.Index(fields=["emirate", "is_active"], name="user_emirate_active_idx"),
        ]

    def __str__(self) -> str:
        return self.display_name or display_phone(self.phone)

    # -- location integrity -------------------------------------------------
    def clean(self):
        super().clean()
        if bool(self.emirate_id) != bool(self.service_area_id):
            raise ValidationError(
                {
                    "service_area": (
                        "An emirate and a service area must be provided together."
                    )
                }
            )
        if self.service_area_id and self.emirate_id:
            # require_active=False on purpose: an existing member must keep working
            # when their area is deactivated. New registrations are validated against
            # active areas by the registration service.
            from locations.validation import validate_emirate_service_area

            validate_emirate_service_area(
                self.emirate, self.service_area, require_active=False
            )

    def save(self, *args, **kwargs):
        if self.phone:
            self.phone = self.phone.strip()
        super().save(*args, **kwargs)

    # -- helpers ------------------------------------------------------------
    @property
    def is_mobile_verified(self) -> bool:
        return self.phone_verified_at is not None

    @property
    def short_name(self) -> str:
        return self.display_name or self.masked_phone

    @property
    def masked_phone(self) -> str:
        """Phone number safe to show to other members before a deal is agreed."""
        digits = self.phone or ""
        if len(digits) <= 6:
            return "*" * len(digits)
        return f"{digits[:6]}{'*' * (len(digits) - 6)}"

    def mark_phone_verified(self, *, when=None) -> None:
        self.phone_verified_at = when or timezone.now()
        self.save(update_fields=["phone_verified_at", "updated_at"])

    def move_to(self, emirate, service_area) -> None:
        """Change marketplace membership after validating the pair."""
        from locations.validation import validate_emirate_service_area

        validate_emirate_service_area(emirate, service_area)
        self.emirate = emirate
        self.service_area = service_area
        self.save(update_fields=["emirate", "service_area", "updated_at"])


class OTPChallenge(TimeStampedModel):
    """
    A pending mobile-number verification (registration or sign-in).

    Security notes
    --------------
    * Only a **keyed hash** of the code is stored, bound to the phone number, so a
      database leak does not expose usable codes.
    * ``attempts`` is enforced server-side; exceeding ``max_attempts`` invalidates the
      challenge regardless of expiry.
    * Sending is rate limited per phone number (counted from this table) and per IP
      address (cache backed).
    * The model never stores or logs the plaintext code, and ``__str__`` does not
      include it.
    """

    class Purpose(models.TextChoices):
        REGISTER = "register", "Create account"
        LOGIN = "login", "Sign in"

    phone = models.CharField(max_length=20, db_index=True)
    code_hash = models.CharField(max_length=128)
    purpose = models.CharField(max_length=16, choices=Purpose.choices)

    # Location captured at registration time (re-validated before the account is made).
    emirate = models.ForeignKey(
        "locations.Emirate", null=True, blank=True, on_delete=models.SET_NULL
    )
    service_area = models.ForeignKey(
        "locations.ServiceArea", null=True, blank=True, on_delete=models.SET_NULL
    )
    # Set for sign-in challenges so the verified phone can be matched to an account.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="otp_challenges",
    )

    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    sends = models.PositiveSmallIntegerField(default=1)
    expires_at = models.DateTimeField()
    last_sent_at = models.DateTimeField(default=timezone.now, db_index=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    request_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "mobile verification"
        verbose_name_plural = "mobile verifications"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["phone", "purpose", "-created_at"], name="otp_phone_purpose_idx"),
            models.Index(fields=["phone", "last_sent_at"], name="otp_phone_sent_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_purpose_display()} for {self.phone} ({self.status_label})"

    # -- state --------------------------------------------------------------
    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_verified(self) -> bool:
        return self.verified_at is not None

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None

    @property
    def is_usable(self) -> bool:
        return not (self.is_expired or self.is_consumed or self.is_verified)

    @property
    def attempts_remaining(self) -> int:
        return max(0, self.max_attempts - self.attempts)

    @property
    def status_label(self) -> str:
        if self.is_consumed:
            return "used"
        if self.is_verified:
            return "verified"
        if self.is_expired:
            return "expired"
        return "pending"

    @property
    def expiry(self) -> timedelta:
        return self.expires_at - timezone.now()

    def mark_verified(self) -> None:
        self.verified_at = timezone.now()
        self.save(update_fields=["verified_at", "updated_at"])

    def mark_consumed(self) -> None:
        self.consumed_at = timezone.now()
        self.save(update_fields=["consumed_at", "updated_at"])

    def invalidate(self) -> None:
        """Stop this challenge from being usable again (superseded or abandoned)."""
        if self.consumed_at is None:
            self.consumed_at = timezone.now()
            self.save(update_fields=["consumed_at", "updated_at"])
