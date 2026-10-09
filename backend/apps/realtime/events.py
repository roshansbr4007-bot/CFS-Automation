"""The real-time event envelope and server-side publishing.

Envelope (JSON object, always these four keys):
    {"type": "event", "event": "<domain.name>", "timestamp": "<ISO-8601 with offset>",
     "data": {...}}

- `event` is a dotted lower-case name such as "system.test".
- `timestamp` is timezone-aware (UTC, ISO-8601).
- `data` is a small JSON object of identifiers/flags. Never put secrets, tokens, session data
  or whole database objects in it. Events are update SIGNALS: clients re-fetch authoritative
  data over REST.

Publishing is server-side only (services, management commands). It never raises into the
caller: a broken channel layer (e.g. Redis down) is logged and reported as False.
"""

import json
import logging
import re

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils import timezone

from .groups import user_group

logger = logging.getLogger(__name__)
EVENT_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
HANDLER = "realtime.event"  # -> EventsConsumer.realtime_event


class InvalidEvent(ValueError):
    pass


def build_event(event: str, data: dict | None = None) -> dict:
    if not isinstance(event, str) or not EVENT_NAME.match(event):
        raise InvalidEvent("Event names are dotted lower-case words, e.g. 'system.test'.")
    data = {} if data is None else data
    if not isinstance(data, dict):
        raise InvalidEvent("Event data must be a JSON object.")
    try:
        json.dumps(data)
    except (TypeError, ValueError):
        raise InvalidEvent("Event data must be JSON-serializable.") from None
    return {
        "type": "event",
        "event": event,
        "timestamp": timezone.now().isoformat(),
        "data": data,
    }


async def apublish_to_user(user_id: int, event: str, data: dict | None = None) -> bool:
    envelope = build_event(event, data)
    layer = get_channel_layer()
    if layer is None:
        logger.warning("Real-time event %s not sent: no channel layer configured.", event)
        return False
    try:
        await layer.group_send(user_group(user_id), {"type": HANDLER, "payload": envelope})
    except Exception:  # delivery is best effort; never break the caller
        logger.warning("Real-time event %s to user %s not sent.", event, user_id, exc_info=True)
        return False
    return True


def publish_to_user(user_id: int, event: str, data: dict | None = None) -> bool:
    """Synchronous wrapper for services and management commands."""
    return async_to_sync(apublish_to_user)(user_id, event, data)
