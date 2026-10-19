"""
Mobile number normalisation for the UAE (and future markets).

Rules
-----
* Numbers are stored and compared in E.164 form (``+971501234567``).
* Only UAE mobile numbers are accepted for self-registration, because the
  registration OTP is delivered over WhatsApp to a handset. UAE landlines are
  explicitly rejected. Additional regions can be enabled by extending
  ``ALLOWED_REGIONS``; emirate/service-area validation is a separate concern.
* ``phonenumbers`` (Google's libphonenumber port, pure Python) does the parsing so
  we do not hand-roll country rules.
"""

from __future__ import annotations

from dataclasses import dataclass

import phonenumbers
from django.conf import settings
from phonenumbers import NumberParseException

from core.exceptions import DomainError

# Region -> the phone-number types we accept for self-registration.
ALLOWED_REGIONS = {"AE": {"mobile"}}

REGION_TO_DIAL_CODE = {"AE": "+971"}

MESSAGES = {
    "empty": "Please enter your mobile number.",
    "unparseable": "That does not look like a valid mobile number.",
    "region": "Only UAE mobile numbers are supported at the moment.",
    "type": "Please enter a UAE mobile number (for example 050 123 4567).",
}


@dataclass(frozen=True)
class NormalisedPhone:
    """Outcome of normalising user supplied input."""

    e164: str
    region: str
    national: str


def normalise_phone(raw: str | None, default_region: str = "AE") -> NormalisedPhone:
    """
    Parse ``raw`` into E.164 form.

    Accepts local (``050 123 4567``), national (``971501234567``) and
    international (``+971501234567``) input.

    Raises:
        DomainError: with a user-safe message when the number cannot be used.
    """
    candidate = (raw or "").strip()
    if not candidate:
        raise DomainError(MESSAGES["empty"])

    # Common typo: 00971..., or a leading 0 that was kept alongside +971.
    cleaned = candidate.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if cleaned.startswith("00"):
        cleaned = f"+{cleaned[2:]}"

    try:
        parsed = phonenumbers.parse(cleaned, default_region)
    except NumberParseException as exc:
        raise DomainError(MESSAGES["unparseable"]) from exc

    if not phonenumbers.is_possible_number(parsed):
        raise DomainError(MESSAGES["unparseable"])

    region = phonenumbers.region_code_for_number(parsed) or default_region
    allowed_types = ALLOWED_REGIONS.get(region)
    if allowed_types is None:
        raise DomainError(MESSAGES["region"])
    if not phonenumbers.is_valid_number(parsed):
        raise DomainError(MESSAGES["unparseable"])

    number_type = phonenumbers.number_type(parsed)
    if "mobile" in allowed_types and number_type not in (
        phonenumbers.PhoneNumberType.MOBILE,
        phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE,
    ):
        raise DomainError(MESSAGES["type"])

    return NormalisedPhone(
        e164=phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164),
        region=region,
        national=phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.NATIONAL
        ),
    )


def is_valid_phone(raw: str | None) -> bool:
    """Non-raising convenience wrapper."""
    try:
        normalise_phone(raw)
    except DomainError:
        return False
    return True


def display_phone(e164: str | None) -> str:
    """Pretty-print a stored E.164 number for UI display."""
    if not e164:
        return ""
    try:
        parsed = phonenumbers.parse(e164, None)
    except NumberParseException:
        return e164
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)


def region_dial_code(region: str) -> str:
    return REGION_TO_DIAL_CODE.get(region, "")
