"""Who may do what to a task. Pure decisions; services enforce them and the API reports them.

Approved rules (Phase 3, as changed by the approved Phase 4 decisions):
- tasks.assign (every role from Phase 4) lets a creator assign to ANY active employee in any
  department; without it a creator may only assign to themselves. Department never blocks
  assignment.
- A creator may edit, reassign or cancel their own task only inside the "creator window":
  the task is still PENDING and has not been acknowledged.
- Operations Managers manage only tasks whose TASK department (chosen by the creator) is the
  department of their own ACTIVE employee record.
- HR (tasks.edit_all_tasks) may edit and reassign any task, and (tasks.delete_task) delete
  any task. HR does NOT cancel, block/unblock or verify: those stay with "manages".
- HR and Admin edit authority (fields, department, category, received time) is not limited by
  workflow status: Completed and Cancelled tasks stay editable for them. The Operations
  Manager and the creator keep their status-limited rules. Reassignment remains a workflow
  action for open tasks only, for everyone.
- Admin manages all tasks and may delete any task.
- Only HR, Admin and an Operations Manager (for tasks in their scope) change a task's
  department after creation; the creator cannot.
- Only the assignee acknowledges, starts and completes their task.
"""

from apps.org.models import Employee

from . import perms
from .models import Task, TaskStatus, VerificationStatus

OPEN_STATUSES = (TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED)


def own_employee(user) -> Employee | None:
    """The user's own employee record (cached on the user object for the request)."""
    if not getattr(user, "is_authenticated", False):
        return None
    if not hasattr(user, "_tasks_own_employee"):
        user._tasks_own_employee = Employee.objects.filter(user=user).first()
    return user._tasks_own_employee


def team_department_id(user) -> int | None:
    employee = own_employee(user)
    return employee.department_id if employee is not None and employee.is_active else None


def is_assignee(user, task: Task) -> bool:
    employee = own_employee(user)
    return employee is not None and task.assigned_to_id == employee.pk


def is_creator(user, task: Task) -> bool:
    return task.created_by_id == getattr(user, "pk", None)


def in_creator_window(task: Task) -> bool:
    return task.status == TaskStatus.PENDING and task.acknowledged_at is None


def manages(user, task: Task) -> bool:
    if user.has_perm(perms.MANAGE_ALL_TASKS):
        return True
    return user.has_perm(perms.MANAGE_TEAM_TASKS) and (
        team_department_id(user) == task.department_id
    )


def can_create(user) -> bool:
    return user.has_perm(perms.CREATE_TASK)


def can_assign_to(user, employee: Employee) -> bool:
    """May `user` make `employee` the assignee of a task (on create or reassignment)?"""
    if not employee.is_active:
        return False
    if user.has_perm(perms.MANAGE_ALL_TASKS) or user.has_perm(perms.ASSIGN):
        return True
    in_team = team_department_id(user) == employee.department_id
    if user.has_perm(perms.MANAGE_TEAM_TASKS) and in_team:
        return True
    own = own_employee(user)
    return own is not None and own.is_active and own.pk == employee.pk


def edits_any(user) -> bool:
    return user.has_perm(perms.EDIT_ALL_TASKS)


def has_task_authority(user) -> bool:
    """HR (tasks.edit_all_tasks) and Admin (tasks.manage_all_tasks): edit any task, any status."""
    return user.has_perm(perms.MANAGE_ALL_TASKS) or edits_any(user)


def can_edit(user, task: Task) -> bool:
    if has_task_authority(user):
        return True
    if task.status not in OPEN_STATUSES:
        return False
    if manages(user, task):  # Operations Manager, own task department, open tasks only
        return True
    return is_creator(user, task) and in_creator_window(task)


def can_change_department(user, task: Task) -> bool:
    """HR / Admin on any task in any status; an Operations Manager only for open tasks in their
    scope; never the creator merely because they created the task."""
    if has_task_authority(user):
        return True
    return task.status in OPEN_STATUSES and manages(user, task)


def can_delete(user) -> bool:
    """Physical delete (HR, Admin), in any status."""
    return user.has_perm(perms.DELETE_TASK)


def can_change_received_at(user, task: Task) -> bool:
    """R6: creator until acknowledgment; afterwards the Operations Manager, HR or Admin."""
    if has_task_authority(user):
        return True
    if task.status == TaskStatus.CANCELLED:
        return False
    if manages(user, task):
        return True
    return is_creator(user, task) and in_creator_window(task)


def can_reassign(user, task: Task) -> bool:
    """Reassignment hands the WORK to someone else, so it stays limited to open tasks for
    everyone (unchanged workflow rule; the service also refuses closed tasks with 409)."""
    return task.status in OPEN_STATUSES and can_edit(user, task)


def can_cancel(user, task: Task) -> bool:
    """Not part of HR's Phase 4 authority: managers, or the creator inside the window."""
    if task.status not in OPEN_STATUSES:
        return False
    return manages(user, task) or (is_creator(user, task) and in_creator_window(task))


def acknowledgment_outstanding(task: Task) -> bool:
    return task.acknowledgment_required and task.acknowledged_at is None


def can_acknowledge(user, task: Task) -> bool:
    return (
        task.status in OPEN_STATUSES
        and acknowledgment_outstanding(task)
        and is_assignee(user, task)
    )


def can_start(user, task: Task) -> bool:
    return task.status == TaskStatus.PENDING and is_assignee(user, task)


def can_complete(user, task: Task) -> bool:
    return task.status == TaskStatus.IN_PROGRESS and is_assignee(user, task)


def can_block(user, task: Task) -> bool:
    if task.status not in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS):
        return False
    return is_assignee(user, task) or manages(user, task)


def can_unblock(user, task: Task) -> bool:
    return task.status == TaskStatus.BLOCKED and (is_assignee(user, task) or manages(user, task))


def can_verify(user, task: Task) -> bool:
    return (
        task.status == TaskStatus.COMPLETED
        and task.verification_status == VerificationStatus.PENDING
        and manages(user, task)
    )


def can_comment(user, task: Task) -> bool:
    """HR (view-only) cannot comment; people working on or managing the task can."""
    return is_assignee(user, task) or is_creator(user, task) or manages(user, task)


def allowed_actions(user, task: Task) -> list[str]:
    """What the API tells the UI this user may do now. The services re-check everything."""
    checks = {
        "edit": can_edit,
        "change_department": can_change_department,
        "reassign": can_reassign,
        "cancel": can_cancel,
        "acknowledge": can_acknowledge,
        "start": lambda u, t: can_start(u, t) and not acknowledgment_outstanding(t),
        "complete": lambda u, t: can_complete(u, t) and not acknowledgment_outstanding(t),
        "block": can_block,
        "unblock": can_unblock,
        "verify": can_verify,
        "reject_verification": can_verify,
        "comment": can_comment,
        "attach": can_comment,
        "delete": lambda u, t: can_delete(u),
    }
    return [name for name, check in checks.items() if check(user, task)]
