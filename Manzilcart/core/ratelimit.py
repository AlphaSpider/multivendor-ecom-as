"""
Cache-backed throttling.

Used for burst protection that does not need a durable record: per-IP OTP requests,
login attempts and similar. Durable, auditable limits (how many OTPs a *phone
number* requested in the last hour) are counted from the database instead, because
those numbers matter for abuse investigation.

Deployment note
---------------
The cache is per-process unless ``REDIS_URL`` is configured. In production Redis is
required so all workers share the counters (``core.checks`` enforces this).
"""

from __future__ import annotations

import logging

from django.core.cache import cache

logger = logging.getLogger("manzilcart.core.ratelimit")

_PREFIX = "throttle"


def _key(key: str) -> str:
    return f"{_PREFIX}:{key}"


def consume(key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """
    Record one attempt against ``key``.

    Returns:
        ``(allowed, remaining)`` where ``remaining`` is the number of further
        attempts permitted inside the current window. ``allowed`` is False once the
        limit is exceeded.
    """
    if limit <= 0:
        return False, 0

    cache_key = _key(key)
    try:
        count = cache.incr(cache_key)
    except ValueError:
        # Key missing (or expired): start a fresh window.
        cache.add(cache_key, 1, timeout=window_seconds)
        count = cache.get(cache_key, 1)

    if count > limit:
        logger.info("Throttle limit reached for key=%s", key)
        return False, 0
    return True, max(0, limit - int(count))


def is_limited(key: str, limit: int, window_seconds: int) -> bool:
    """Check the counter without incrementing it."""
    if limit <= 0:
        return True
    count = cache.get(_key(key), 0) or 0
    return int(count) >= limit


def reset(key: str) -> None:
    """Clear a counter (used after a successful verification / login)."""
    cache.delete(_key(key))


def seconds_until_reset(key: str) -> int:
    """Best-effort remaining window; 0 when unknown.

    LocMemCache and Redis both expose ``ttl()`` in modern Django; any backend that
    does not returns ``None`` and we fall back to 0.
    """
    try:
        ttl = cache.ttl(_key(key))
    except (AttributeError, NotImplementedError):  # pragma: no cover - backend dependent
        return 0
    return int(ttl) if ttl and ttl > 0 else 0


def client_ip(request) -> str:
    """
    Best-effort client IP for throttling.

    ``REMOTE_ADDR`` is trusted; ``X-Forwarded-For`` is only used when present. This
    value is for abuse mitigation, never for authorisation.
    """
    if request is None:
        return "unknown"
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.META.get("REMOTE_ADDR") or "unknown")[:64]
