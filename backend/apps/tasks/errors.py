from rest_framework import status

from apps.core.errors import AppError, ConflictError


class TaskVersionConflict(ConflictError):
    code = "version_conflict"
    message = "This task was changed by someone else. Reload and try again."


class InvalidTransition(ConflictError):
    code = "invalid_state_transition"
    message = "This action is not allowed in the task's current state."


class AcknowledgmentRequired(ConflictError):
    code = "acknowledgment_required"
    message = "Acknowledge the task before starting or completing it."


class TaskPermissionDenied(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"
    message = "You do not have permission to do this."


class AssignmentNotAllowed(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "assignment_not_allowed"
    message = "You may not assign tasks to this employee."
