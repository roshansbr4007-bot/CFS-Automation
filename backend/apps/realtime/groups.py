"""Server-controlled group names. A browser never names a group: the consumer derives the
groups from the authenticated Django user.

Convention (Channels group names allow ASCII letters, digits, hyphens, underscores, periods):
    user.<user_id>          one user's private stream (the only group in Phase 8)

Future extension points (NOT created yet): department.<department_id>, org.admin.
"""


def user_group(user_id: int) -> str:
    return f"user.{int(user_id)}"


def groups_for(user) -> list[str]:
    """Every group `user` may receive events from. Phase 8: only their own user group."""
    return [user_group(user.pk)]
