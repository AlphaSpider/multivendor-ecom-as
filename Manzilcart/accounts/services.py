"""Mobile verification and account services."""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import login as auth_login
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import OTPChallenge, User
from core.audit import record_audit
from core.exceptions import DomainError, RateLimitedError
from core.logging_utils import mask_phone
from core.models import AuditEvent
from core.phone import normalise_phone
from core.ratelimit import client_ip, consume, reset
from core.security import generate_code, hash_code, verify_code
from locations.validation import validate_registration_location

logger = logging.getLogger("manzilcart.accounts")

# Session keys for the sign-up wizard. The challenge id never comes from the browser.
SESSION_CHALLENGE_ID = "manzilcart.otp_challenge_id"
SESSION_PHONE = "manzilcart.otp_phone"


class StartResult:
    """Outcome of starting a verification, safe to hand to a view."""

    def __init__(self, challenge: OTPChallenge, *, is_returning_member: bool):
        self.challenge = challenge
        #: True when the number already belongs to an account. Used internally to pick
        #: the post-verification step - it is never shown to the visitor.
        self.is_returning_member = is_returning_member


def _enforce_send_limits(*, phone: str, request) -> None:
    """
    Apply every OTP send limit.

    Per-phone limits are counted from the database (durable and auditable); the per-IP
    burst limit uses the cache.
    """
    now = timezone.now()
    window_start = now - timezone.timedelta(hours=1)

    recent_sends = OTPChallenge.objects.filter(
        phone=phone, last_sent_at__gte=window_start
    ).count()
    if recent_sends >= settings.OTP_MAX_SENDS_PER_PHONE_PER_HOUR:
        raise RateLimitedError(
            "We have already sent several codes to this number. "
            "Please wait an hour before trying again."
        )

    latest = (
        OTPChallenge.objects.filter(phone=phone).order_by("-last_sent_at").first()
    )
    if latest is not None:
        elapsed = (now - latest.last_sent_at).total_seconds()
        cooldown = settings.OTP_RESEND_COOLDOWN_SECONDS
        if elapsed < cooldown:
            wait = int(cooldown - elapsed) + 1
            raise RateLimitedError(f"Please wait {wait} second(s) before requesting a new code.")

    ip = client_ip(request)
    allowed, _ = consume(
        f"otp-ip:{ip}", settings.OTP_MAX_SENDS_PER_IP_PER_HOUR, window_seconds=3600
    )
    if not allowed:
        raise RateLimitedError(
            "Too many verification requests from this connection. Please try again later."
        )


def start_verification(*, raw_phone: str, request, emirate=None) -> StartResult:
    """
    Begin registration or sign-in for ``raw_phone``.

    The caller cannot choose between "register" and "sign in": the server decides based
    on whether the number already has an account, and the HTTP response is identical in
    both cases. That is what prevents the flow from becoming a membership oracle.
    """
    normalised = normalise_phone(raw_phone)
    phone = normalised.e164

    _enforce_send_limits(phone=phone, request=request)

    existing_user = User.objects.filter(phone=phone).first()
    purpose = (
        OTPChallenge.Purpose.LOGIN if existing_user else OTPChallenge.Purpose.REGISTER
    )

    code = generate_code()
    now = timezone.now()

    with transaction.atomic():
        # Only one live challenge per phone number at a time.
        OTPChallenge.objects.filter(
            phone=phone, consumed_at__isnull=True
        ).update(consumed_at=now)

        challenge = OTPChallenge.objects.create(
            phone=phone,
            code_hash=hash_code(code, scope=phone),
            purpose=purpose,
            emirate=emirate,
            user=existing_user,
            expires_at=now + timezone.timedelta(seconds=settings.OTP_TTL_SECONDS),
            max_attempts=settings.OTP_MAX_ATTEMPTS,
            last_sent_at=now,
            request_ip=client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT", "") if request else "")[:255],
        )

    # Outside the transaction's critical section: a messaging outage must not prevent
    # the challenge row from existing, and the visitor can ask for a resend.
    try:
        from notifications.services import send_otp

        send_otp(
            phone=phone,
            code=code,
            ttl_seconds=settings.OTP_TTL_SECONDS,
            idempotency_key=f"otp:{challenge.pk}",
        )
    except Exception:  # noqa: BLE001 - never leak provider detail to the visitor
        logger.exception(
            "Failed to dispatch OTP for challenge %s (phone %s)",
            challenge.pk,
            mask_phone(phone),
        )

    logger.info(
        "OTP challenge %s started for %s (purpose=%s)",
        challenge.pk,
        mask_phone(phone),
        purpose,
    )
    return StartResult(challenge, is_returning_member=existing_user is not None)


def resend_verification(*, challenge: OTPChallenge, request) -> OTPChallenge:
    """Resend a code for an existing, still-usable challenge."""
    if challenge is None or challenge.is_consumed or challenge.is_verified:
        raise DomainError("That verification is no longer valid. Please start again.")
    if challenge.is_expired:
        raise DomainError("That code has expired. Please request a new one.")

    _enforce_send_limits(phone=challenge.phone, request=request)

    code = generate_code()
    with transaction.atomic():
        locked = OTPChallenge.objects.select_for_update().get(pk=challenge.pk)
        locked.code_hash = hash_code(code, scope=locked.phone)
        locked.sends += 1
        locked.attempts = 0  # a fresh code gets a fresh attempt budget
        locked.last_sent_at = timezone.now()
        locked.expires_at = timezone.now() + timezone.timedelta(
            seconds=settings.OTP_TTL_SECONDS
        )
        locked.save(
            update_fields=[
                "code_hash",
                "sends",
                "attempts",
                "last_sent_at",
                "expires_at",
                "updated_at",
            ]
        )

    try:
        from notifications.services import send_otp

        send_otp(
            phone=locked.phone,
            code=code,
            ttl_seconds=settings.OTP_TTL_SECONDS,
            idempotency_key=f"otp:{locked.pk}:resend:{locked.sends}",
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to resend OTP for challenge %s", locked.pk)

    # The pre-verification IP counter should not block a legitimate resend, but the
    # per-phone limit above still applies.
    reset(f"otp-ip:{client_ip(request)}")
    return locked


def verify_challenge_code(*, challenge: OTPChallenge, submitted_code: str) -> OTPChallenge:
    """
    Check a submitted code, enforcing expiry, single-use and the attempt limit.

    The row is locked for the duration so parallel submissions cannot each consume an
    attempt and race past the limit. The failure is raised *after* the transaction
    commits: raising inside it would roll back the attempt counter and defeat the
    limit (a domain error is a normal outcome, not a database failure).
    """
    if challenge is None:
        raise DomainError("Your verification session has expired. Please start again.")

    failure: DomainError | None = None

    with transaction.atomic():
        locked = OTPChallenge.objects.select_for_update().get(pk=challenge.pk)

        if locked.is_consumed:
            failure = DomainError(
                "That verification has already been used. Please start again."
            )
        elif locked.is_expired:
            failure = DomainError("That code has expired. Please request a new one.")
        elif locked.attempts >= locked.max_attempts:
            locked.invalidate()
            failure = RateLimitedError(
                "Too many incorrect attempts. Please request a new code."
            )
        elif not verify_code(submitted_code, locked.code_hash, scope=locked.phone):
            locked.attempts += 1
            locked.save(update_fields=["attempts", "updated_at"])
            remaining = locked.attempts_remaining
            if remaining <= 0:
                locked.invalidate()
                failure = RateLimitedError(
                    "Too many incorrect attempts. Please request a new code."
                )
            else:
                failure = DomainError(
                    f"That code is not correct. {remaining} attempt(s) remaining."
                )
        elif not locked.is_verified:
            locked.mark_verified()

    if failure is not None:
        raise failure

    return locked


def complete_registration(
    *,
    challenge: OTPChallenge,
    emirate_slug: str,
    service_area_slug: str,
    display_name: str = "",
    email: str = "",
    request=None,
) -> User:
    """
    Create the account once the phone is verified and a valid area has been chosen.

    The emirate/area values come from the browser and are therefore re-validated here;
    an inactive emirate or an area belonging to a different emirate is rejected.

    A duplicate-number outcome still consumes the challenge, so the "already registered"
    error is raised after the transaction commits (raising inside would roll that write
    back).
    """
    user = None
    duplicate = False

    with transaction.atomic():
        locked = OTPChallenge.objects.select_for_update().get(pk=challenge.pk)

        if not locked.is_verified:
            raise DomainError("Please verify your mobile number first.")
        if locked.is_consumed:
            raise DomainError("This registration has already been completed.")
        if locked.purpose != OTPChallenge.Purpose.REGISTER:
            raise DomainError("This verification is not a registration.")

        emirate, service_area = validate_registration_location(
            emirate_slug, service_area_slug
        )

        if User.objects.filter(phone=locked.phone).exists():
            # The number was claimed between challenge and completion (or a race).
            locked.emirate = emirate
            locked.service_area = service_area
            locked.mark_consumed()
            locked.save(
                update_fields=["emirate", "service_area", "updated_at"]
            )
            duplicate = True
        else:
            try:
                user = User.objects.create_user(
                    phone=locked.phone,
                    display_name=display_name.strip()[:64],
                    email=email.strip()[:254],
                    emirate=emirate,
                    service_area=service_area,
                )
            except IntegrityError as exc:  # pragma: no cover - defence in depth
                raise DomainError(
                    "This mobile number is already registered. Please sign in instead."
                ) from exc

            user.phone_verified_at = timezone.now()
            user.save(update_fields=["phone_verified_at", "updated_at"])

            locked.emirate = emirate
            locked.service_area = service_area
            locked.user = user
            locked.mark_consumed()
            # ``mark_consumed`` only writes the timestamp; persist the captured context.
            locked.save(
                update_fields=["emirate", "service_area", "user", "updated_at"]
            )

            record_audit(
                action=AuditEvent.Action.ACCOUNT_CREATED,
                actor=user,
                entity=user,
                emirate=emirate,
                summary="Member account created",
                metadata={"service_area": service_area.slug},
                request=request,
            )
            record_audit(
                action=AuditEvent.Action.PHONE_VERIFIED,
                actor=user,
                entity=user,
                emirate=emirate,
                summary="Mobile number verified",
                request=request,
            )

    if duplicate:
        raise DomainError(
            "This mobile number is already registered. Please sign in instead."
        )

    logger.info("Account created for %s", mask_phone(user.phone))
    return user


def sign_in_with_verified_challenge(*, challenge: OTPChallenge, request) -> User:
    """
    Sign an existing member in after a successful OTP verification.

    Any challenge row for this phone is consumed so a code cannot be replayed.
    """
    user = User.objects.filter(phone=challenge.phone).first()
    if user is None:
        # Do not disclose which step failed; the operator can see the audit trail.
        challenge.mark_consumed()
        raise DomainError("We could not sign you in. Please start again.")

    if not user.is_active:
        challenge.mark_consumed()
        raise DomainError("This account is not available. Please contact support.")

    challenge.mark_consumed()

    if user.phone_verified_at is None:
        user.phone_verified_at = timezone.now()
        user.save(update_fields=["phone_verified_at", "updated_at"])

    # Rotate the session identifier on privilege change.
    auth_login(request, user)
    reset(f"otp-ip:{client_ip(request)}")

    record_audit(
        action=AuditEvent.Action.LOGIN_SUCCEEDED,
        actor=user,
        entity=user,
        emirate=user.emirate,
        summary="Signed in with mobile OTP",
        request=request,
    )
    return user


def update_profile(*, user: User, display_name: str, emirate_slug: str, service_area_slug: str, email: str = "", request=None) -> User:
    """
    Update a member's profile.

    Location changes go through the same validation as registration, so a member cannot
    move themselves into an unsupported area by posting a crafted form.
    """
    emirate, service_area = validate_registration_location(emirate_slug, service_area_slug)

    user.display_name = display_name.strip()[:64]
    user.email = email.strip()[:254]
    user.emirate = emirate
    user.service_area = service_area

    user.full_clean(exclude=["password", "last_login"])
    user.save(
        update_fields=[
            "display_name",
            "email",
            "emirate",
            "service_area",
            "updated_at",
        ]
    )

    record_audit(
        action=AuditEvent.Action.PROFILE_UPDATED,
        actor=user,
        entity=user,
        emirate=emirate,
        summary="Profile updated",
        metadata={"service_area": service_area.slug},
        request=request,
    )
    return user
