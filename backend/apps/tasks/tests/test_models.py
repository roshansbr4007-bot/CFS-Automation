import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.tasks.models import Task, TaskAttachment, TaskComment, TaskVerification

pytestmark = pytest.mark.django_db


def test_received_time_and_source_go_together_in_the_database(ops, new_task):
    task = new_task(ops["manager"], ops["rahul_emp"])
    with pytest.raises(IntegrityError), transaction.atomic():
        Task.objects.filter(pk=task.pk).update(received_at_source="EMAIL")


def test_completed_status_needs_a_completion_time(ops, new_task):
    task = new_task(ops["manager"], ops["rahul_emp"])
    with pytest.raises(IntegrityError), transaction.atomic():
        Task.objects.filter(pk=task.pk).update(status="COMPLETED")


def test_rejection_row_needs_reason_and_remarks(ops, new_task):
    task = new_task(ops["manager"], ops["rahul_emp"])
    with pytest.raises(IntegrityError), transaction.atomic():
        TaskVerification.objects.create(
            task=task,
            cycle_no=1,
            submitted_at=timezone.now(),
            decision="REJECTED",
            decided_by=ops["manager"],
            decided_at=timezone.now(),
        )


def test_one_row_per_verification_cycle(ops, new_task):
    task = new_task(ops["manager"], ops["rahul_emp"])
    fields = {
        "task": task,
        "cycle_no": 1,
        "submitted_at": timezone.now(),
        "decision": "VERIFIED",
        "decided_by": ops["manager"],
        "decided_at": timezone.now(),
    }
    TaskVerification.objects.create(**fields)
    with pytest.raises(IntegrityError), transaction.atomic():
        TaskVerification.objects.create(**fields)


def test_string_representations(ops, new_task):
    task = new_task(ops["manager"], ops["rahul_emp"], title="Feed upload")
    assert str(task) == f"T-{task.pk:06d} Feed upload"
    assert Task(title="x").reference == "T-new"
    assignment = task.assignments.get()
    assert str(assignment) == f"{task.pk}: None -> {ops['rahul_emp'].pk}"
    comment = TaskComment.objects.create(task=task, author=ops["rahul"], body="hi")
    assert str(comment) == f"{task.pk} comment {comment.pk}"
    verification = TaskVerification.objects.create(
        task=task,
        cycle_no=1,
        submitted_at=timezone.now(),
        decision="VERIFIED",
        decided_by=ops["manager"],
        decided_at=timezone.now(),
    )
    assert str(verification) == f"{task.pk} cycle 1: VERIFIED"
    assert str(TaskAttachment(original_filename="folio.pdf")) == "folio.pdf"
