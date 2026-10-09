from rest_framework.permissions import SAFE_METHODS, BasePermission

from .. import perms


def _has(request, perm) -> bool:
    user = request.user
    return bool(user and user.is_authenticated and user.has_perm(perm))


class DepartmentPermission(BasePermission):
    """Any signed-in user may read departments; writes need org.manage_departments."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        return request.method in SAFE_METHODS or _has(request, perms.MANAGE_DEPARTMENTS)


class EmployeePermission(BasePermission):
    """Action-level checks; record-level scope comes from selectors.visible_employees (404)."""

    ACTION_PERMS = {
        "list": (perms.VIEW_ALL_EMPLOYEES, perms.VIEW_TEAM_EMPLOYEES),
        "create": (perms.MANAGE_EMPLOYEES,),
        "partial_update": (perms.MANAGE_EMPLOYEES,),
        "link_login": (perms.LINK_EMPLOYEE_LOGIN,),
        "unlink_login": (perms.LINK_EMPLOYEE_LOGIN,),
    }

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        required = self.ACTION_PERMS.get(view.action)
        if required is None:  # retrieve, me, me_logins, logins: scoped by the queryset
            return True
        return any(_has(request, perm) for perm in required)
