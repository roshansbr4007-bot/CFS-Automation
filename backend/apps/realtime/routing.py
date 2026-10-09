"""WebSocket URL routes. All real-time endpoints live under /ws/; Phase 8 has exactly one.
Future endpoints are added to this list (they inherit authentication and origin checks)."""

from django.urls import path

from .consumers import EventsConsumer

websocket_urlpatterns = [
    path("ws/events/", EventsConsumer.as_asgi()),
]
