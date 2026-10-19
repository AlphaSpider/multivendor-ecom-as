"""
One-time code (OTP) generation and hashing.

Why HMAC-SHA256 and not Django's password hasher?
-------------------------------------------------
``make_password`` uses PBKDF2 with 1,000,000+ iterations, i.e. roughly 0.3-1s of CPU
per call. An attacker could trigger that work per request with a six digit code,
which turns a cheap brute-force attempt into a denial-of-service lever against a
small VM. A six digit code is only 20 bits of entropy and is protected chiefly by a
short expiry plus strict attempt limits, so a keyed HMAC is the correct trade-off:
it is constant time to verify, cannot be brute-forced offline from a database dump
without the server secret, and is bound to the phone number.

Codes are compared with :func:`hmac.compare_digest` to avoid timing leaks.
"""

from __future__ import annotations

import hmac
import secrets
import string
from hashlib import sha256

from django.conf import settings

DIGITS = string.digits


def _secret() -> bytes:
    return settings.SECRET_KEY.encode("utf-8")


def generate_code(length: int | None = None) -> str:
    """
    Return a cryptographically secure numeric code.

    ``secrets.randbelow`` is used rather than ``random`` so codes are unpredictable.
    """
    code_length = length or settings.OTP_LENGTH
    return "".join(secrets.choice(DIGITS) for _ in range(code_length))


def hash_code(code: str, *, scope: str) -> str:
    """
    Keyed hash of ``code`` bound to ``scope`` (normally the normalised phone number).

    Binding the scope means a hash captured for one phone number cannot be replayed
    against another.
    """
    message = f"{scope}:{code}".encode("utf-8")
    return hmac.new(_secret(), message, sha256).hexdigest()


def verify_code(code: str, hashed: str, *, scope: str) -> bool:
    """Constant-time verification of a submitted code."""
    if not code or not hashed:
        return False
    return hmac.compare_digest(hash_code(code, scope=scope), hashed)


def generate_token(length: int = 32) -> str:
    """URL-safe token for confirmation links (auction awards, cancellations)."""
    return secrets.token_urlsafe(length)
