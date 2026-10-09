import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")
# Initialise Django (apps, settings) before importing anything that touches models.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter  # noqa: E402

from apps.realtime.asgi import websocket_application  # noqa: E402

# Phase 8: HTTP is served exactly as before; WebSockets are routed through Channels.
application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": websocket_application(),
    }
)
