"""The only write path into the audit log.

Call record() inside the same transaction.atomic() block as the change it describes, so the
change and its audit row commit or roll back together.
"""

from apps.core.request_context import current_request_context

from .models import AuditLog
from .scrub import scrub


def _request_user():
    request = current_request_context().request
    user = getattr(request, "user", None)
    return user if getattr(user, "is_authenticated", False) else None


def record(
    *,
    action: str,
    entity_type: str,
    entity_id,
    old=None,
    new=None,
    actor=None,
    use_request_user: bool = True,
    extra: dict | None = None,
) -> AuditLog:
    """Write one audit row.

    actor: the user who acted. If omitted, the logged-in request user is used, unless
    use_request_user is False (system actions, failed logins), in which case actor is null.
    """
    ctx = current_request_context()
    if actor is None and use_request_user:
        actor = _request_user()
    context = {"user_agent": ctx.user_agent} if ctx.user_agent else {}
    if actor is not None:
        context["actor_email"] = actor.email
    if extra:
        context.update(scrub(extra))
    return AuditLog.objects.create(
        actor_user=actor,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        old_value=scrub(old),
        new_value=scrub(new),
        ip=ctx.ip,
        request_id=ctx.request_id,
        context=context,
    )
