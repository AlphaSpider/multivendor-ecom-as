"""
Request-independent "current emirate" context.

The emirate a visitor is browsing is request state, but some code paths (service
functions, model defaults, background workers) need to reach it without threading
``request`` through every call. A :mod:`contextvars` based holder solves that and,
unlike ``threading.local``, behaves correctly if we later adopt async views.

The middleware is the only writer; everything else is read-only.
"""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from locations.models import Emirate

_current_emirate: contextvars.ContextVar[Optional["Emirate"]] = contextvars.ContextVar(
    "manzilcart_current_emirate", default=None
)


def set_current_emirate(emirate: Optional["Emirate"]):
    """Bind the current emirate and return the token needed to reset it."""
    return _current_emirate.set(emirate)


def reset_current_emirate(token) -> None:
    """Undo a previous :func:`set_current_emirate`."""
    _current_emirate.reset(token)


def get_current_emirate() -> Optional["Emirate"]:
    """Return the emirate bound to the current execution context, if any."""
    return _current_emirate.get()
