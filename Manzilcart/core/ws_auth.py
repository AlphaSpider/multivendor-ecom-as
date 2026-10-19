"""
Authentication for WebSocket connections.

Django Channels' ``AuthMiddlewareStack`` resolves the session from the session
cookie, which is exactly what the template-driven application needs: a logged-in
browser is authenticated on the socket too, and an anonymous visitor is not.

Consumers must still authorise per object (is this user a participant in *this*
conversation?) - the middleware only establishes *who*, never *what for*.
"""

from __future__ import annotations

from channels.auth import AuthMiddlewareStack

# Public alias so the ASGI entry point does not depend on a Channels internal name.
SessionAuthMiddlewareStack = AuthMiddlewareStack
