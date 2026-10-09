from rest_framework.permissions import BasePermission


def require_perm(perm: str) -> type[BasePermission]:
    """A DRF permission class that allows logged-in users holding `perm` (via a role)."""

    class _RequirePerm(BasePermission):
        required_perm = perm

        def has_permission(self, request, view):
            user = request.user
            return bool(user and user.is_authenticated and user.has_perm(perm))

    _RequirePerm.__name__ = f"RequirePerm[{perm}]"
    return _RequirePerm
