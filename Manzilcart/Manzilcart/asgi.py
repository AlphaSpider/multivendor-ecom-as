"""
ASGI config for ManzilCart.

Exposes the ASGI callable as a module-level variable named ``application``.
HTTP is served by Django's ASGI handler, WebSocket traffic is routed through
Django Channels (see ``Manzilcart/routing.py``).
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Manzilcart.settings")

# The HTTP application must be created before importing routing, so that the app
# registry is fully populated before consumers are imported.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from Manzilcart.routing import websocket_urlpatterns  # noqa: E402
from core.ws_auth import SessionAuthMiddlewareStack  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(
            SessionAuthMiddlewareStack(URLRouter(websocket_urlpatterns))
        ),
    }
)
