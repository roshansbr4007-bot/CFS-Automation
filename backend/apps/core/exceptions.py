"""Every error leaves the API as {"code", "message", "fields"}."""

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from .errors import AppError


def _body(code: str, message: str, fields=None) -> dict:
    return {"code": code, "message": message, "fields": fields or {}}


def exception_handler(exc, context):
    if isinstance(exc, AppError):
        return Response(_body(exc.code, exc.message, exc.fields), status=exc.status_code)

    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()

    response = drf_exception_handler(exc, context)
    if response is None:
        return None  # unexpected error: Django returns 500 and logs it

    if isinstance(exc, exceptions.ValidationError):
        detail = exc.detail if isinstance(exc.detail, dict) else {"non_field_errors": exc.detail}
        response.data = _body("validation_error", "Some fields are not valid.", detail)
    elif isinstance(exc, (exceptions.NotAuthenticated, exceptions.AuthenticationFailed)):
        response.status_code = status.HTTP_401_UNAUTHORIZED
        response.data = _body("not_authenticated", "Sign in to continue.")
    elif isinstance(exc, exceptions.PermissionDenied):
        message = str(exc.detail)
        if message.startswith("CSRF Failed"):
            response.data = _body(
                "csrf_failed", "Security token missing or invalid. Reload the page."
            )
        else:
            response.data = _body("permission_denied", "You do not have permission to do this.")
    elif isinstance(exc, exceptions.NotFound):
        response.data = _body("not_found", "Not found.")
    elif isinstance(exc, exceptions.MethodNotAllowed):
        response.data = _body("method_not_allowed", str(exc.detail))
    else:
        response.data = _body(getattr(exc, "default_code", "error"), str(exc.detail))
    return response
