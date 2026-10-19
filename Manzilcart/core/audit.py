"""
Append-only audit trail helper.

Every sensitive state change should call :func:`record_audit` *inside the same
transaction* as the change it describes, so the audit row only exists if the
business change committed.
"""

from __future__ import annotations

from typing import Any, Iterable

from core.logging_utils import redact_metadata
from core.models import AuditEvent


def _client_ip(request) -> str | None:
    if request is None:
        return None
    # The proxy in front of the app is trusted (see SECURE_PROXY_SSL_HEADER) and
    # REMOTE_ADDR is preferred; X-Forwarded-For is only read as a fallback.
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45] or None
    return request.META.get("REMOTE_ADDR")


def record_audit(
    *,
    action: str,
    actor=None,
    entity: Any = None,
    entity_type: str | None = None,
    entity_id: Any = None,
    emirate=None,
    summary: str = "",
    metadata: dict[str, Any] | None = None,
    request=None,
) -> AuditEvent:
    """
    Write one audit row.

    ``entity`` may be any model instance; its ``_meta.label_lower`` and primary key
    are used unless ``entity_type`` / ``entity_id`` are given explicitly.
    """
    if entity is not None:
        entity_type = entity_type or entity._meta.label_lower
        entity_id = entity.pk if entity_id is None else entity_id

    if actor is None and request is not None:
        candidate = getattr(request, "user", None)
        if candidate is not None and getattr(candidate, "is_authenticated", False):
            actor = candidate

    if emirate is None and request is not None:
        emirate = getattr(request, "emirate", None)

    return AuditEvent.objects.create(
        actor=actor,
        action=action,
        entity_type=(entity_type or "")[:64],
        entity_id="" if entity_id is None else str(entity_id)[:64],
        emirate=emirate,
        summary=summary[:255],
        metadata=redact_metadata(metadata),
        ip_address=_client_ip(request),
        user_agent=(
            (request.META.get("HTTP_USER_AGENT", "") if request is not None else "")[:255]
        ),
    )


def record_audit_many(events: Iterable[dict[str, Any]]) -> list[AuditEvent]:
    """Convenience wrapper for writing several audit rows in one call."""
    return [record_audit(**event) for event in events]
