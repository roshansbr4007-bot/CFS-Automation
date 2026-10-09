from rest_framework import status

from apps.core.errors import AppError, ConflictError


class OverduePermissionDenied(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"
    message = "You do not have permission to do this."


class OverdueStateConflict(ConflictError):
    code = "overdue_case_state"
    message = "This overdue case cannot do that in its current state."


class OverdueVersionConflict(ConflictError):
    code = "version_conflict"
    message = "This case was changed by someone else. Reload and try again."
