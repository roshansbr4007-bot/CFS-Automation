"""Phase 8: WebSocket foundation (Django Channels). Uses Channels' WebsocketCommunicator against
the same stack config/asgi.py serves, with the in-memory channel layer from test settings.

These tests are transaction-backed (the session lookup runs in a worker thread) and build their
own users, so they do not depend on migration-seeded data."""

import json
import os
from datetime import datetime
from io import StringIO

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from channels.layers import get_channel_layer
from channels.routing import ProtocolTypeRouter
from channels.security.websocket import OriginValidator
from channels.testing import HttpCommunicator, WebsocketCommunicator
from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import Client

from apps.accounts.tests.factories import UserFactory
from apps.realtime import events
from apps.realtime.asgi import websocket_application
from apps.realtime.events import InvalidEvent, apublish_to_user, build_event, publish_to_user
from apps.realtime.groups import user_group
from apps.realtime.routing import websocket_urlpatterns

pytestmark = pytest.mark.django_db(transaction=True)
APP = websocket_application(["testserver"])  # the production stack with a known origin
ORIGIN = (b"origin", b"http://testserver")
URL = "/ws/events/"


def run(scenario):
    return async_to_sync(scenario)()


def session_cookie(user):
    client = Client()
    client.force_login(user)
    value = client.cookies[settings.SESSION_COOKIE_NAME].value
    return (b"cookie", f"{settings.SESSION_COOKIE_NAME}={value}".encode())


def socket(*headers):
    return WebsocketCommunicator(APP, URL, headers=list(headers))


async def open_socket(user_cookie):
    communicator = socket(ORIGIN, user_cookie)
    connected, _ = await communicator.connect()
    assert connected
    hello = await communicator.receive_json_from()
    assert hello["event"] == "system.connected"
    return communicator


def members():
    """Non-empty groups of the in-memory layer."""
    return {g: set(channels) for g, channels in get_channel_layer().groups.items() if channels}


# --- ASGI configuration and routing -----------------------------------------------------------


def test_asgi_routes_http_and_websocket():
    from config.asgi import application

    assert isinstance(application, ProtocolTypeRouter)
    assert set(application.application_mapping) == {"http", "websocket"}
    assert isinstance(application.application_mapping["websocket"], OriginValidator)
    assert settings.ASGI_APPLICATION == "config.asgi.application"
    assert settings.INSTALLED_APPS[0] == "daphne"  # runserver serves HTTP and WebSockets


def test_channel_layer_configuration():
    from config.settings import base

    assert settings.CHANNEL_LAYERS["default"]["BACKEND"] == "channels.layers.InMemoryChannelLayer"
    redis = base.CHANNEL_LAYERS["default"]
    assert redis["BACKEND"] == "channels_redis.core.RedisChannelLayer"
    expected = os.environ.get("CHANNEL_REDIS_URL", "redis://localhost:6379/1")
    assert redis["CONFIG"]["hosts"] == [expected]
    assert expected != base.CELERY_BROKER_URL  # Celery's broker database stays separate


def test_only_the_events_route_exists():
    assert [str(p.pattern) for p in websocket_urlpatterns] == ["ws/events/"]


def test_http_through_asgi_is_unchanged():
    from config.asgi import application

    async def scenario():
        communicator = HttpCommunicator(application, "GET", "/api/v1/auth/csrf/",
                                        headers=[(b"host", b"testserver")])
        response = await communicator.get_response()
        assert response["status"] == 200

    run(scenario)


def test_rest_session_login_is_unchanged(client):
    user = UserFactory(email="rest.user@example.com")
    client.force_login(user)
    assert client.get("/api/v1/auth/me/").status_code == 200


# --- authentication and origin ----------------------------------------------------------------


def test_authenticated_user_connects_and_gets_a_connected_event():
    cookie = session_cookie(UserFactory())

    async def scenario():
        communicator = socket(ORIGIN, cookie)
        connected, _ = await communicator.connect()
        assert connected
        hello = await communicator.receive_json_from()
        assert set(hello) == {"type", "event", "timestamp", "data"}
        assert (hello["type"], hello["event"], hello["data"]) == ("event", "system.connected", {})
        assert datetime.fromisoformat(hello["timestamp"]).tzinfo is not None
        await communicator.disconnect()

    run(scenario)


def test_anonymous_invalid_and_inactive_sessions_are_refused():
    inactive = UserFactory()
    inactive_cookie = session_cookie(inactive)
    inactive.is_active = False
    inactive.save(update_fields=["is_active"])
    bogus = (b"cookie", f"{settings.SESSION_COOKIE_NAME}=not-a-session".encode())

    async def scenario():
        for headers in ([ORIGIN], [ORIGIN, bogus], [ORIGIN, inactive_cookie]):
            connected, _ = await socket(*headers).connect()
            assert connected is False
        assert members() == {}

    run(scenario)


def test_foreign_or_missing_origin_is_refused():
    cookie = session_cookie(UserFactory())

    async def scenario():
        for headers in ([(b"origin", b"http://evil.example"), cookie], [cookie]):
            connected, _ = await socket(*headers).connect()
            assert connected is False

    run(scenario)


# --- groups, isolation, delivery --------------------------------------------------------------


def test_server_decides_the_groups():
    user = UserFactory()
    cookie = session_cookie(user)

    async def scenario():
        communicator = await open_socket(cookie)
        groups = members()
        assert set(groups) == {user_group(user.pk)} and len(groups[user_group(user.pk)]) == 1
        await communicator.disconnect()

    run(scenario)


def test_events_reach_only_their_user():
    alice, bob = UserFactory(), UserFactory()
    alice_cookie, bob_cookie = session_cookie(alice), session_cookie(bob)

    async def scenario():
        a, b = await open_socket(alice_cookie), await open_socket(bob_cookie)
        assert await apublish_to_user(alice.pk, "system.test", {"n": 1}) is True
        got = await a.receive_json_from()
        assert (got["event"], got["data"]) == ("system.test", {"n": 1})
        assert await b.receive_nothing()
        await apublish_to_user(bob.pk, "system.test", {"n": 2})
        assert (await b.receive_json_from())["data"] == {"n": 2}
        assert await a.receive_nothing()
        await a.disconnect()
        await b.disconnect()

    run(scenario)


def test_clients_cannot_subscribe_or_publish():
    alice, bob = UserFactory(), UserFactory()
    alice_cookie, bob_cookie = session_cookie(alice), session_cookie(bob)

    async def scenario():
        a, b = await open_socket(alice_cookie), await open_socket(bob_cookie)
        for attempt in ({"action": "subscribe", "group": user_group(bob.pk)},
                        {"type": "event", "event": "task.completed", "data": {"task": 1}},
                        {"action": "publish", "event": "system.test"}):
            await a.send_json_to(attempt)
            reply = await a.receive_json_from()
            assert (reply["event"], reply["data"]) == ("system.error",
                                                        {"code": "unsupported_action"})
        assert await b.receive_nothing()  # nothing Alice sent reached Bob
        await apublish_to_user(bob.pk, "system.test")
        assert (await b.receive_json_from())["event"] == "system.test"
        assert await a.receive_nothing()  # and Alice did not join Bob's group
        assert members()[user_group(alice.pk)] and len(members()) == 2
        await a.disconnect()
        await b.disconnect()

    run(scenario)


def test_malformed_client_messages_are_handled():
    cookie = session_cookie(UserFactory())

    async def scenario():
        communicator = await open_socket(cookie)
        for frame, code in (({"text_data": "not json"}, "invalid_json"),
                            ({"bytes_data": b"\x00\x01"}, "unsupported_message"),
                            ({"text_data": "x" * 2000}, "unsupported_message"),
                            ({"text_data": "[1, 2]"}, "unsupported_action"),
                            ({"text_data": json.dumps({"action": "shout"})}, "unsupported_action")):
            await communicator.send_to(**frame)
            reply = await communicator.receive_json_from()
            assert (reply["event"], reply["data"]) == ("system.error", {"code": code})
        await communicator.send_json_to({"action": "ping"})  # still connected and healthy
        assert (await communicator.receive_json_from())["event"] == "system.pong"
        await communicator.disconnect()

    run(scenario)


def test_disconnect_cleans_up_and_later_events_are_safe():
    user = UserFactory()
    cookie = session_cookie(user)

    async def scenario():
        communicator = await open_socket(cookie)
        await communicator.disconnect()
        assert members() == {}
        assert await apublish_to_user(user.pk, "system.test") is True  # nobody listening: fine

    run(scenario)


def test_duplicate_connections_and_reconnect():
    user = UserFactory()
    cookie = session_cookie(user)

    async def scenario():
        first, second = await open_socket(cookie), await open_socket(cookie)
        await apublish_to_user(user.pk, "system.test", {"n": 1})
        assert (await first.receive_json_from())["data"] == {"n": 1}
        assert (await second.receive_json_from())["data"] == {"n": 1}
        await first.disconnect()  # one tab closes; the other keeps receiving
        await apublish_to_user(user.pk, "system.test", {"n": 2})
        assert (await second.receive_json_from())["data"] == {"n": 2}
        third = await open_socket(cookie)  # reconnect
        await apublish_to_user(user.pk, "system.test", {"n": 3})
        assert (await third.receive_json_from())["data"] == {"n": 3}
        assert (await second.receive_json_from())["data"] == {"n": 3}
        await second.disconnect()
        await third.disconnect()
        assert members() == {}

    run(scenario)


# --- event envelope and publishing ------------------------------------------------------------


@pytest.mark.parametrize("name", ["system", "System.Test", "system.", ".test", "system test", 5])
def test_event_names_are_validated(name):
    with pytest.raises(InvalidEvent):
        build_event(name)


def test_event_data_must_be_a_json_object():
    for data in ([1, 2], "text", {"when": object()}):
        with pytest.raises(InvalidEvent):
            build_event("system.test", data)
    envelope = build_event("task.status_changed", {"task_id": 7})
    assert list(envelope) == ["type", "event", "timestamp", "data"]
    assert datetime.fromisoformat(envelope["timestamp"]).tzinfo is not None


def test_publishing_never_raises(monkeypatch):
    class Broken:
        async def group_send(self, group, message):
            raise ConnectionError("redis down")

    monkeypatch.setattr(events, "get_channel_layer", lambda: Broken())
    assert publish_to_user(1, "system.test") is False
    monkeypatch.setattr(events, "get_channel_layer", lambda: None)
    assert publish_to_user(1, "system.test") is False


# --- management command (manual verification aid) --------------------------------------------


def test_management_command_sends_a_test_event_to_one_user():
    alice, bob = UserFactory(email="alice.rt@example.com"), UserFactory()
    alice_cookie, bob_cookie = session_cookie(alice), session_cookie(bob)
    out = StringIO()

    async def scenario():
        a, b = await open_socket(alice_cookie), await open_socket(bob_cookie)
        await sync_to_async(call_command)("send_realtime_test_event", "--email",
                                          "ALICE.RT@example.com", stdout=out)
        got = await a.receive_json_from()
        assert (got["event"], got["data"]) == ("system.test", {"message": "Phase 8 test event"})
        assert await b.receive_nothing()
        await a.disconnect()
        await b.disconnect()

    run(scenario)
    assert f"group user.{alice.pk}" in out.getvalue()


def test_management_command_rejects_unknown_and_inactive_users():
    with pytest.raises(CommandError):
        call_command("send_realtime_test_event", "--email", "nobody@example.com")
    UserFactory(email="gone.rt@example.com", is_active=False)
    with pytest.raises(CommandError):
        call_command("send_realtime_test_event", "--email", "gone.rt@example.com")
