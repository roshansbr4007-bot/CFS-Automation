"""All writes to users go through here. Each one is audited in the same transaction."""

from django.contrib.auth import authenticate, password_validation, update_session_auth_hash
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.contrib.auth.models import Group
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit import actions
from apps.audit.services import record
from apps.core.errors import AppError, ConflictError, FieldValidationError

from . import roles as role_defs
from .managers import normalize_email
from .models import User

PASSWORD_MARKER = {"password": "changed"}


class InvalidCredentials(AppError):
    code = "invalid_credentials"
    message = "Email or password is incorrect."


def _email_taken() -> ConflictError:
    return ConflictError("A user with this email already exists.", code="email_taken")


def _validate_roles(roles) -> list[str]:
    roles = list(roles or [])
    unknown = sorted(set(roles) - set(role_defs.ROLE_NAMES))
    if unknown:
        raise FieldValidationError(fields={"roles": [f"Unknown role: {name}" for name in unknown]})
    return sorted(set(roles))


def _validate_password(password: str, user: User, field: str) -> None:
    try:
        password_validation.validate_password(password, user)
    except DjangoValidationError as exc:
        raise FieldValidationError(fields={field: list(exc.messages)}) from exc


def _set_roles(user: User, role_names: list[str]) -> None:
    user.groups.set(Group.objects.filter(name__in=role_names))


def _user_snapshot(user: User, role_names: list[str]) -> dict:
    return {
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "roles": role_names,
        "is_active": user.is_active,
    }


def _end_sessions(user: User) -> None:
    for session in Session.objects.filter(expire_date__gt=timezone.now()).iterator():
        if session.get_decoded().get("_auth_user_id") == str(user.pk):
            session.delete()


# --- Authentication -------------------------------------------------------------------------


def login_user(request, *, email: str, password: str) -> User:
    email = normalize_email(email)
    user = authenticate(request, username=email, password=password)
    if user is None:
        matched = User.objects.filter(email=email).first()
        record(
            action=actions.AUTH_LOGIN_FAILED,
            entity_type="user",
            entity_id=matched.pk if matched else "unknown",
            actor=matched,
            use_request_user=False,
            extra={"attempted_email": email},
        )
        raise InvalidCredentials()
    with transaction.atomic():
        django_login(request, user)
        record(action=actions.AUTH_LOGIN, entity_type="user", entity_id=user.pk, actor=user)
    return user


def logout_user(request) -> None:
    user = request.user
    with transaction.atomic():
        record(action=actions.AUTH_LOGOUT, entity_type="user", entity_id=user.pk, actor=user)
        django_logout(request)


def change_own_password(request, *, current_password: str, new_password: str) -> None:
    user = request.user
    if not user.check_password(current_password):
        raise FieldValidationError(fields={"current_password": ["Current password is incorrect."]})
    _validate_password(new_password, user, "new_password")
    with transaction.atomic():
        user.set_password(new_password)
        user.save(update_fields=["password", "updated_at"])
        record(
            action=actions.USER_PASSWORD_CHANGED,
            entity_type="user",
            entity_id=user.pk,
            actor=user,
            new=PASSWORD_MARKER,
        )
    update_session_auth_hash(request, user)  # keep this session signed in


# --- User management (Admin) ----------------------------------------------------------------


def _create(*, email, first_name, last_name, roles, password) -> tuple[User, list[str]]:
    email = normalize_email(email)
    if not email:
        raise FieldValidationError(fields={"email": ["This field is required."]})
    if User.objects.filter(email=email).exists():
        raise _email_taken()
    role_names = _validate_roles(roles)
    user = User(email=email, first_name=first_name or "", last_name=last_name or "")
    _validate_password(password, user, "password")
    user.set_password(password)
    try:
        with transaction.atomic():
            user.save()
    except IntegrityError as exc:  # two requests racing on the same email
        raise _email_taken() from exc
    _set_roles(user, role_names)
    return user, role_names


def create_user(*, actor: User, email: str, first_name: str = "", last_name: str = "",
                roles=(), password: str) -> User:
    with transaction.atomic():
        user, role_names = _create(
            email=email, first_name=first_name, last_name=last_name, roles=roles,
            password=password,
        )
        record(
            action=actions.USER_CREATED,
            entity_type="user",
            entity_id=user.pk,
            actor=actor,
            new=_user_snapshot(user, role_names),
        )
    return user


def create_initial_admin(*, email: str, password: str, first_name: str = "",
                         last_name: str = "") -> User:
    """Used once by the create_initial_admin command. Audited as a system action."""
    with transaction.atomic():
        user, role_names = _create(
            email=email, first_name=first_name, last_name=last_name,
            roles=[role_defs.ADMIN], password=password,
        )
        record(
            action=actions.SYSTEM_INITIAL_ADMIN_CREATED,
            entity_type="user",
            entity_id=user.pk,
            actor=None,
            use_request_user=False,
            new=_user_snapshot(user, role_names),
        )
    return user


_UNSET = object()


def update_user(*, actor: User, user: User, first_name=_UNSET, last_name=_UNSET, roles=None,
                is_active=None) -> User:
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)

        old, new = {}, {}
        for field, value in (("first_name", first_name), ("last_name", last_name)):
            if value is not _UNSET and value != getattr(user, field):
                old[field], new[field] = getattr(user, field), value
                setattr(user, field, value)
        if new:
            user.save(update_fields=[*new.keys(), "updated_at"])
            record(action=actions.USER_UPDATED, entity_type="user", entity_id=user.pk,
                   actor=actor, old=old, new=new)

        if roles is not None:
            new_roles = _validate_roles(roles)
            old_roles = user.role_names
            if new_roles != old_roles:
                _set_roles(user, new_roles)
                record(action=actions.USER_ROLE_CHANGED, entity_type="user", entity_id=user.pk,
                       actor=actor, old={"roles": old_roles}, new={"roles": new_roles})

        if is_active is not None and is_active != user.is_active:
            user.is_active = is_active
            user.save(update_fields=["is_active", "updated_at"])
            record(
                action=actions.USER_ACTIVATED if is_active else actions.USER_DEACTIVATED,
                entity_type="user",
                entity_id=user.pk,
                actor=actor,
                old={"is_active": not is_active},
                new={"is_active": is_active},
            )
            if not is_active:
                _end_sessions(user)
    return user


def set_password_by_admin(*, actor: User, user: User, password: str) -> None:
    _validate_password(password, user, "password")
    with transaction.atomic():
        user.set_password(password)
        user.save(update_fields=["password", "updated_at"])
        record(action=actions.USER_PASSWORD_SET_BY_ADMIN, entity_type="user", entity_id=user.pk,
               actor=actor, new=PASSWORD_MARKER)
