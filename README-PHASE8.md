# Phase 8 — Real-time foundation (Django Channels / WebSockets)

Phase 8 adds **infrastructure only**: a secure WebSocket connection that the server can push events through. It adds no business events (task, SLA, notification, KPI). REST stays the source of truth; an event is only a signal to re-fetch.

## How it fits together

```
browser ──HTTP /api/…──► Vite proxy ─► Django (Daphne runserver) ─► ProtocolTypeRouter "http"      (unchanged)
browser ──WS   /ws/events/─► Vite proxy (ws: true) ─► same server  ─► ProtocolTypeRouter "websocket"
                                    Origin check → Django session auth → URLRouter → EventsConsumer
server code ─ publish_to_user(user_id, event, data) ─► channel layer (Redis DB 1) ─► that user's sockets
```

- **`config/asgi.py`:** an explicit `ProtocolTypeRouter`. HTTP goes to Django's own ASGI handler, exactly as before; WebSockets go to `apps/realtime/asgi.py`.
- **`apps/realtime`:** no models and no migrations.

  | File | Role |
  | --- | --- |
  | `routing.py` | `/ws/` routes |
  | `consumers.py` | thin consumer |
  | `groups.py` | group names |
  | `events.py` | envelope and publishing |
  | `management/commands/send_realtime_test_event.py` | manual test event |

## URL convention

All real-time endpoints live under `/ws/`. Phase 8 has exactly one: **`/ws/events/`**. A future endpoint is added to `apps/realtime/routing.py` and inherits the same origin and authentication checks.

## Authentication

- **The existing Django session cookie only.** There's no JWT and no second login; Channels' `AuthMiddlewareStack` reads the same session as the REST API.
- **Refused before the handshake completes** (HTTP 403):
  - anonymous visitors;
  - an invalid or expired session;
  - an inactive user;
  - a browser `Origin` whose host isn't in `DJANGO_ALLOWED_HOSTS` (or `localhost` / `127.0.0.1` in DEBUG with the list empty);
  - a missing `Origin`.
- **The WebSocket never changes business state.** Clients may only send `{"action": "ping"}` (answered with `system.pong`). Anything else gets `system.error` (`invalid_json`, `unsupported_message`, `unsupported_action`) and the connection stays open.

## Groups (server-controlled)

| Group | Members | Phase 8 |
| --- | --- | --- |
| `user.<user_id>` | every open socket of that signed-in user (several tabs are fine) | **implemented** |
| `department.<department_id>` | future, for example department-wide updates | extension point only |
| `org.admin` | future, for example Command Center live updates | extension point only |

The consumer derives the groups from the authenticated user (`groups.groups_for(user)`). The URL carries no user, employee or department ids, and the browser can't name or join a group. On disconnect, the socket leaves every group it joined.

## Event envelope

```json
{"type": "event", "event": "system.test", "timestamp": "2026-10-05T04:30:00.123456+00:00", "data": {"message": "Phase 8 test event"}}
```

- **Fields:** always exactly these four keys.
- **`event`:** dotted lower-case words.
- **`timestamp`:** timezone-aware ISO-8601.
- **`data`:** a small JSON object of identifiers or flags. Never secrets, tokens, session data or whole database objects.

| Event | When |
| --- | --- |
| `system.connected` | right after a successful connection |
| `system.pong` | reply to a client `ping` |
| `system.error` | a rejected client message (`data.code` says why) |
| `system.test` | sent by the manual-verification command only |

**Publishing** happens on the server only: `apps.realtime.events.publish_to_user(user_id, event, data)`, or the `apublish_to_user` async variant. It never raises into the caller; if the channel layer or Redis is unavailable it logs a warning and returns `False`.

## Frontend

`src/realtime/RealtimeProvider.tsx` wraps the protected layout (`app/routes.tsx`), so only signed-in users connect; signing out unmounts it and closes the socket.

- **One socket** per session.
- **Status:** `useRealtime().status` (`connecting`, `open`, `reconnecting`, `closed`).
- **Reconnect:** capped exponential backoff (1 s, 2 s, 4 s … 30 s), stopping after 10 failed attempts.
- **Malformed frames** are ignored.
- **`useRealtimeEvent(name, handler)`:** the hook future pages use to re-fetch REST data.

There's no visible UI change in Phase 8.

## Local development (Windows)

| Setting | Value |
| --- | --- |
| New `.env` variable (optional) | `CHANNEL_REDIS_URL=redis://localhost:6379/1` (the default). It's the **same Redis server** as Celery, on **database 1**; Celery keeps database 0. |
| Redis | `docker compose up -d db redis` (already part of the Phase 5 setup) |
| Backend | `python manage.py runserver 8000`. Daphne is the first installed app, so this one command now serves HTTP **and** WebSockets (the startup banner mentions ASGI/Daphne). |
| Frontend | `npm run dev`. Vite proxies `/api` (unchanged) and `/ws` (WebSocket) to `127.0.0.1:8000`. |
| `DJANGO_ALLOWED_HOSTS` | must include the host the browser uses (for example `localhost,127.0.0.1`), or the origin check refuses the socket |

## Production requirement (not changed in this phase)

Production still runs **gunicorn (WSGI)**, which can't serve WebSockets. To enable real-time in production:
- serve the ASGI app, for example `daphne -b 0.0.0.0 -p 8001 config.asgi:application`, either for `/ws/` only (with the reverse proxy forwarding `/ws/` with `Upgrade` and `Connection` headers) or for everything;
- set `CHANNEL_REDIS_URL` and `DJANGO_ALLOWED_HOSTS`.

## Tests

| Side | Coverage |
| --- | --- |
| Backend (`apps/realtime/tests/test_realtime.py`) | Uses Channels' `WebsocketCommunicator` with the **in-memory** channel layer (`config/settings/test.py`), so the suite never needs Redis. The tests are transaction-backed and create their own users. |
| Frontend (`src/realtime/realtime.test.tsx`) | A controllable fake socket. The shared `src/test/setup.ts` installs an inert `WebSocket` so no other test opens a network connection. |

## Known limitations

- **Delivery is best effort and not persisted:** events sent while a user is offline are lost. Pages must re-fetch over REST when the socket reconnects or the page loads.
- **A socket keeps the identity it connected with:** a sign-out in another tab, a deactivation or a session expiry takes effect on the next connection.
- **No business events yet:** existing polling (60 s) remains.
- **After 10 failed reconnect attempts** the client stops until the page is reloaded.
- **Production WebSockets** need the ASGI deployment above.

## Future extension points

- Publish business events from services after the transaction commits, for example `transaction.on_commit(lambda: publish_to_user(...))`.
- Add department or Admin groups in `groups_for()`.
- Add endpoints in `routing.py`.
- Subscribe in pages with `useRealtimeEvent("…", () => queryClient.invalidateQueries(…))`.
