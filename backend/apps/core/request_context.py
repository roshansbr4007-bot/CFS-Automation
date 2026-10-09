"""Per-request context (request id, IP, user agent, request).

Services read it without having it passed in; the audit recorder uses it.
"""

import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RequestContext:
    request_id: uuid.UUID | None = None
    ip: str | None = None
    user_agent: str = ""
    request: object | None = field(default=None, compare=False)


_current: ContextVar[RequestContext] = ContextVar("request_context", default=RequestContext())


def current_request_context() -> RequestContext:
    return _current.get()


def set_request_context(ctx: RequestContext):
    return _current.set(ctx)


def reset_request_context(token) -> None:
    _current.reset(token)
