import uuid

from .request_context import RequestContext, reset_request_context, set_request_context


def _client_ip(request) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


class RequestContextMiddleware:
    """Gives every request an id and exposes IP, user agent and user to the audit recorder."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = uuid.uuid4()
        request.request_id = request_id
        ctx = RequestContext(
            request_id=request_id,
            ip=_client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:256],
            request=request,  # request.user is read lazily: it changes during login/logout
        )
        token = set_request_context(ctx)
        try:
            response = self.get_response(request)
        finally:
            reset_request_context(token)
        response["X-Request-ID"] = str(request_id)
        return response
