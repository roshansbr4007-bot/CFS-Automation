from django.db.models import Q

from apps.core.errors import FieldValidationError

from . import roles as role_defs
from .models import User

_BOOL = {"true": True, "1": True, "false": False, "0": False}


def list_users(params):
    qs = User.objects.prefetch_related("groups").order_by("email")
    if search := (params.get("search") or "").strip():
        qs = qs.filter(
            Q(email__icontains=search) | Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
        )
    if role := params.get("role"):
        if role not in role_defs.ROLE_NAMES:
            raise FieldValidationError(fields={"role": [f"Unknown role: {role}"]})
        qs = qs.filter(groups__name=role)
    if (raw := params.get("is_active")) is not None and raw != "":
        if raw.lower() not in _BOOL:
            raise FieldValidationError(fields={"is_active": ["Use true or false."]})
        qs = qs.filter(is_active=_BOOL[raw.lower()])
    return qs.distinct()
