"""
Logging helpers.

ManzilCart must never write OTP codes, session tokens, passwords or full mobile
numbers to logs. Use these helpers at call sites instead of ad-hoc formatting.
"""

from __future__ import annotations

import re
from typing import Any

PHONE_RE = re.compile(r"(\+?\d{6,15})")

_KEEP_TAIL = 3
_KEEP_HEAD = 6


def mask_phone(phone: str | None) -> str:
    """
    Mask a phone number for logging: ``+971501234567`` -> ``+97150*****567``.

    Keeps the country/operator prefix (useful for support) and the last digits,
    hides the middle.
    """
    if not phone:
        return ""
    digits = re.sub(r"[^\d+]", "", str(phone))
    if len(digits) <= _KEEP_HEAD + _KEEP_TAIL:
        return "*" * len(digits)
    middle = len(digits) - _KEEP_HEAD - _KEEP_TAIL
    return f"{digits[:_KEEP_HEAD]}{'*' * middle}{digits[-_KEEP_TAIL:]}"


def mask_email(email: str | None) -> str:
    """Mask an email address for logging: ``aisha@example.com`` -> ``a***a@example.com``."""
    if not email or "@" not in email:
        return ""
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked = "*" * len(local)
    else:
        masked = f"{local[0]}{'*' * (len(local) - 2)}{local[-1]}"
    return f"{masked}@{domain}"


def redact_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """
    Defensively strip anything that looks like a secret from audit metadata.

    Audit metadata is written by our own code, but this guard means a future
    mistake cannot leak a one-time code or token into the audit trail.
    """
    if not metadata:
        return {}
    sensitive_fragments = ("otp", "code", "token", "password", "secret", "key")
    cleaned: dict[str, Any] = {}
    for key, value in metadata.items():
        lowered = str(key).lower()
        if any(fragment in lowered for fragment in sensitive_fragments):
            cleaned[key] = "[redacted]"
        elif isinstance(value, dict):
            cleaned[key] = redact_metadata(value)
        else:
            cleaned[key] = value
    return cleaned
