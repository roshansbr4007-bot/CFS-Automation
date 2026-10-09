from rest_framework.permissions import SAFE_METHODS, BasePermission

from .. import perms


class TaskPermission(BasePermission):
    """Action-level gate. Record scope comes from selectors.visible_tasks (404) and every
    write is re-checked in the service layer against the approved policy."""

    CREATE_ACTIONS = {"create", "assignees", "sla_preview"}

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if view.action in self.CREATE_ACTIONS:
            return user.has_perm(perms.CREATE_TASK)
        if view.action == "destroy":
            return user.has_perm(perms.DELETE_TASK)
        return True


class TaskCategoryPermission(BasePermission):
    """Any signed-in user may read categories; changes need tasks.manage_task_categories."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return request.method in SAFE_METHODS or user.has_perm(perms.MANAGE_TASK_CATEGORIES)
