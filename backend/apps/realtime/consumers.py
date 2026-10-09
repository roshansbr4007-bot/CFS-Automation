"""WebSocket consumer for /ws/events/. Thin by design:

- authentication: the Django session (AuthMiddlewareStack) decides who the user is;
  anonymous or inactive users are refused before the handshake completes;
- authorization / subscription: groups come only from groups.groups_for(user);
- delivery: server events arrive through the channel layer and are sent unchanged;
- client messages: only {"action": "ping"} is accepted (answered with system.pong). Clients
  cannot publish, subscribe to groups, or change any business state over this socket.
"""

import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer

from .events import build_event
from .groups import groups_for

logger = logging.getLogger(__name__)
MAX_CLIENT_MESSAGE = 1024  # bytes; client messages are tiny control frames


class EventsConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.joined: list[str] = []
        user = self.scope.get("user")
        if user is None or not user.is_authenticated or not user.is_active:
            await self.close()  # before accept(): the handshake is refused (HTTP 403)
            return
        for group in groups_for(user):
            await self.channel_layer.group_add(group, self.channel_name)
            self.joined.append(group)
        await self.accept()
        await self._send_event("system.connected")

    async def disconnect(self, code):
        for group in getattr(self, "joined", []):
            await self.channel_layer.group_discard(group, self.channel_name)
        self.joined = []

    async def receive(self, text_data=None, bytes_data=None):
        if text_data is None or len(text_data) > MAX_CLIENT_MESSAGE:
            await self._send_error("unsupported_message")
            return
        try:
            message = json.loads(text_data)
        except ValueError:
            await self._send_error("invalid_json")
            return
        if isinstance(message, dict) and message.get("action") == "ping":
            await self._send_event("system.pong")
            return
        await self._send_error("unsupported_action")

    async def realtime_event(self, message):
        """Channel-layer handler (type "realtime.event"): forward the prepared envelope."""
        await self.send(text_data=json.dumps(message["payload"]))

    async def _send_event(self, event: str, data: dict | None = None):
        await self.send(text_data=json.dumps(build_event(event, data)))

    async def _send_error(self, code: str):
        await self._send_event("system.error", {"code": code})
