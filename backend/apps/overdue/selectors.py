"""Who may see and act on which overdue cases (Phase 9). Read-only.

- Everyone: their OWN cases (they are the employee recorded on the case).
- Operations Manager (overdue.review_team_cases): cases of tasks in their own department
  (the TASK's department, approved Q3).
- HR, Admin (overdue.view_all_cases): every case.
Review additionally requires a submitted reason and is never allowed on one's own case.
"""

from django.db.models import Q

from apps.tasks.policy import team_department_id

from . import perms
from .models import OverdueCase, OverdueStatus


def is_own(user, case: OverdueCase) -> bool:
    return case.employee.user_id == user.pk


def visible_cases(user):
    qs = OverdueCase.objects.select_related(
        "task", "employee", "department", "task_creator", "submitted_by", "reviewed_by",
        "clock",
    )
    if user.has_perm(perms.VIEW_ALL_CASES):
        return qs
    scope = Q(employee__user=user)
    department = team_department_id(user)
    if user.has_perm(perms.REVIEW_TEAM_CASES) and department is not None:
        scope |= Q(department_id=department)
    return qs.filter(scope)


def in_review_scope(user, case: OverdueCase) -> bool:
    if user.has_perm(perms.REVIEW_ALL_CASES):
        return True
    return user.has_perm(perms.REVIEW_TEAM_CASES) and (
        team_department_id(user) == case.department_id
    )


def can_submit(user, case: OverdueCase) -> bool:
    return case.status == OverdueStatus.OPEN and is_own(user, case)


def can_review(user, case: OverdueCase) -> bool:
    return (
        case.status == OverdueStatus.REASON_SUBMITTED
        and not is_own(user, case)  # self-review is forbidden for every role
        and in_review_scope(user, case)
    )


def reviewable_cases(user):
    """Cases waiting for THIS user's review (the review queue)."""
    qs = visible_cases(user).filter(status=OverdueStatus.REASON_SUBMITTED)
    qs = qs.exclude(employee__user=user)  # never one's own case
    if user.has_perm(perms.REVIEW_ALL_CASES):
        return qs
    department = team_department_id(user)
    if user.has_perm(perms.REVIEW_TEAM_CASES) and department is not None:
        return qs.filter(department_id=department)
    return qs.none()
