"""Creating SLA notifications and delivering their emails.

Recipients (locked decision, resolved at the moment the threshold fires):
- 50%  Warning : assigned employee, in-app.
- 75%  Critical: assigned employee, in-app + email.
- 100% Overdue : assigned employee + every ACTIVE HR user + the designated Boss recipient,
                 in-app + email. Applies to the ACK and the RESOLUTION clock alike, except the
                 one case the SLA checker passes escalate=False for (approved C1: a scheduled
                 task's ACK clock already overdue when it was created): the employee only.

"Boss" is NOT a role and is never "every Admin". It is resolved by the function named in
settings.SLA_BOSS_RESOLVER (default: the assignee's reporting manager, the Phase 2 reporting
relationship). If no Boss can be resolved, nobody is guessed: the gap is returned so the SLA
checker can record it for configuration.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.module_loading import import_string

from apps.accounts import roles
from apps.accounts.models import User
from apps.core.timeutils import to_ist

from .models import EmailStatus, Notification, NotificationKind

logger = logging.getLogger(__name__)

MAX_EMAIL_ATTEMPTS = 5
LEVEL_KIND = {
    "WARNING": NotificationKind.SLA_WARNING,
    "CRITICAL": NotificationKind.SLA_CRITICAL,
    "OVERDUE": NotificationKind.SLA_OVERDUE,
}
LEVEL_WORD = {"WARNING": "Warning (50%)", "CRITICAL": "Critical (75%)", "OVERDUE": "Overdue"}
EMAIL_LEVELS = {"CRITICAL", "OVERDUE"}


def reporting_manager_of_assignee(clock) -> tuple[User | None, str | None]:
    """Default Boss resolver: the assignee's reporting manager (existing Phase 2 relationship).

    Returns (user, None) or (None, reason). Swap it via settings.SLA_BOSS_RESOLVER once a
    dedicated escalation-recipient mapping is approved.
    """
    manager = clock.task.assigned_to.reporting_manager
    if manager is None:
        return None, "The assignee has no reporting manager set."
    if not manager.is_active:
        return None, "The assignee's reporting manager is inactive."
    user = manager.user
    if user is None or not user.is_active:
        return None, "The assignee's reporting manager has no active login."
    return user, None


def boss_recipient(clock) -> tuple[User | None, str | None]:
    return import_string(settings.SLA_BOSS_RESOLVER)(clock)


def recipients_for(clock, level: str, *, escalate: bool = True) -> tuple[list[User], str | None]:
    """(recipients, reason the Boss could not be resolved or None). escalate=False keeps the
    employee and leaves HR and the Boss out (the Boss is then not even looked up)."""
    people: dict[int, User] = {}
    employee_user = clock.task.assigned_to.user
    if employee_user is not None and employee_user.is_active:
        people[employee_user.pk] = employee_user
    else:
        logger.info("SLA %s on task %s: assignee has no active login", level, clock.task_id)
    boss_gap = None
    if level == "OVERDUE" and escalate:
        for user in User.objects.filter(is_active=True, groups__name=roles.HR).distinct():
            people[user.pk] = user
        boss, boss_gap = boss_recipient(clock)
        if boss is not None:
            people[boss.pk] = boss
        else:
            logger.warning(
                "SLA overdue on task %s: no Boss recipient (%s)", clock.task_id, boss_gap
            )
    return list(people.values()), boss_gap


def _message(clock, level: str) -> tuple[str, str]:
    task = clock.task
    which = "Acknowledgment" if clock.kind == "ACK" else "Resolution"
    title = f"{task.reference} {which} SLA {LEVEL_WORD[level]}: {task.title}"
    due = to_ist(clock.due_at).strftime("%d %b %Y, %H:%M IST") if clock.due_at else "-"
    body = (
        f"Task {task.reference} \"{task.title}\" assigned to {task.assigned_to.full_name}.\n"
        f"{which} SLA ({clock.rule_snapshot['name']}) is {LEVEL_WORD[level]}.\n"
        f"Due: {due}"
    )
    return title, body


def notify_threshold(clock, level: str, *, escalate: bool = True) -> str | None:
    """Create the notifications for one threshold of one clock. Safe to call repeatedly.

    Returns why no Boss recipient could be resolved (Overdue only), else None."""
    title, body = _message(clock, level)
    recipients, boss_gap = recipients_for(clock, level, escalate=escalate)
    email = EmailStatus.PENDING if level in EMAIL_LEVELS else EmailStatus.NOT_REQUIRED
    rows = [
        Notification(
            recipient=user,
            task=clock.task,
            clock=clock,
            kind=LEVEL_KIND[level],
            title=title[:200],
            body=body,
            dedup_key=f"sla:{clock.pk}:{level}:{user.pk}",
            email_status=email,
        )
        for user in recipients
    ]
    # ignore_conflicts + the unique dedup_key make this idempotent.
    Notification.objects.bulk_create(rows, ignore_conflicts=True)
    return boss_gap


def send_pending_emails(limit: int = 200) -> int:
    """Send queued emails. Each row is locked while it is sent, so two checkers never send
    the same email; failures are recorded and retried on the next run."""
    sent = 0
    ids = list(
        Notification.objects.filter(
            email_status__in=[EmailStatus.PENDING, EmailStatus.FAILED],
            email_attempts__lt=MAX_EMAIL_ATTEMPTS,
        )
        .order_by("id")
        .values_list("id", flat=True)[:limit]
    )
    for pk in ids:
        with transaction.atomic():
            row = (
                Notification.objects.select_for_update(skip_locked=True)
                .filter(pk=pk, email_status__in=[EmailStatus.PENDING, EmailStatus.FAILED])
                .first()
            )
            if row is None:
                continue
            recipient = User.objects.get(pk=row.recipient_id)
            row.email_attempts += 1
            try:
                send_mail(
                    subject=f"[CFS Operations] {row.title}",
                    message=row.body,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[recipient.email],
                )
            except Exception as exc:  # delivery problems are recorded, never raised
                row.email_status = EmailStatus.FAILED
                row.email_error = str(exc)[:1000]
                logger.warning("Email for notification %s failed: %s", row.pk, exc)
            else:
                row.email_status = EmailStatus.SENT
                row.email_sent_at = timezone.now()
                row.email_error = ""
                sent += 1
            row.save(
                update_fields=["email_status", "email_attempts", "email_sent_at", "email_error"]
            )
    return sent


def mark_read(*, user, notification_id: int) -> Notification:
    row = Notification.objects.get(pk=notification_id, recipient=user)
    if row.read_at is None:
        row.read_at = timezone.now()
        row.save(update_fields=["read_at"])
    return row


def mark_all_read(*, user) -> int:
    return Notification.objects.filter(recipient=user, read_at__isnull=True).update(
        read_at=timezone.now()
    )


def schedule_warning_recipients(occurrence) -> list[User]:
    """Active Admins, plus active Operations Managers whose own active employee record is in the
    responsibility's department."""
    department_id = occurrence.schedule.responsibility.department_id
    return list(
        User.objects.filter(is_active=True)
        .filter(
            Q(groups__name=roles.ADMIN)
            | Q(
                groups__name=roles.OPERATIONS_MANAGER,
                employee__department_id=department_id,
                employee__is_active=True,
            )
        )
        .distinct()
    )


def notify_schedule_warning(occurrence, reason: str) -> None:
    """In-app warning that a scheduled responsibility occurrence was not generated."""
    responsibility = occurrence.schedule.responsibility
    title = (
        f"{responsibility.name} for {occurrence.occurrence_date:%d %b %Y} was not generated"
    )[:200]
    rows = [
        Notification(
            recipient=user,
            kind=NotificationKind.SCHEDULE_WARNING,
            title=title,
            body=f"{reason}\nStatus: {occurrence.get_status_display()}.",
            dedup_key=f"schedule:{occurrence.pk}:{user.pk}",
        )
        for user in schedule_warning_recipients(occurrence)
    ]
    Notification.objects.bulk_create(rows, ignore_conflicts=True)
