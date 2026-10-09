"""Application errors with a stable machine-readable code."""

from rest_framework import status


class AppError(Exception):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "error"
    message = "The request could not be completed."

    def __init__(self, message: str | None = None, *, code: str | None = None, fields=None):
        super().__init__(message or self.message)
        self.message = message or self.message
        if code:
            self.code = code
        self.fields = fields or {}


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class FieldValidationError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "validation_error"
    message = "Some fields are not valid."
