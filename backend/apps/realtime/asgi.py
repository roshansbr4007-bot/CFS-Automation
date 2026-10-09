"""The WebSocket half of the ASGI application (used by config/asgi.py and by tests)."""

from channels.auth import AuthMiddlewareStack
from channels.routing import URLRouter
from channels.security.websocket import AllowedHostsOriginValidator, OriginValidator

from .routing import websocket_urlpatterns


def websocket_application(allowed_origins=None):
    """Origin check -> Django session authentication -> /ws/ routes.

    With no argument the browser Origin must match settings.ALLOWED_HOSTS (production and
    development). Tests pass explicit origins to exercise the identical stack."""
    inner = AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
    if allowed_origins is None:
        return AllowedHostsOriginValidator(inner)
    return OriginValidator(inner, allowed_origins)
