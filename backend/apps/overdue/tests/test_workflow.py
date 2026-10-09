"""Phase 9: employee reason, reviewer cause, scope, self-review, immutability, audit,
notifications and realtime (published only after commit)."""

import pytest
from django.db import transaction

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.notifications.models import Notification
from apps.org.models import Department
from apps.overdue import services
from apps.overdue.models import OverdueCase, ReviewedCaseLocked

pytestmark = pytest.mark.django_db
URL = "/api/v1/overdue-cases/"


@pytest.fixture
def opened(ops, work):
    """Rahul's (OPS) task, raised by the OPS manager at 10:00, overdue at 11:00."""
    work.raise_task(ops["rahul_emp"], 2026, 10, 5, 10, 0)
    work.tick(2026, 10, 5, 11, 0)
    return OverdueCase.objects.get()


@pytest.fixture
def hr(staff):
    user, _ = staff(roles.HR, department="HR")
    return user


def _submit(client, case, **body):
    case.refresh_from_db()
    payload = {"version": case.version, "reason_category": "DEPENDENCY",
               "explanation": "Waiting for the RTA file", **body}
    return client.post(f"{URL}{case.pk}/submit/", payload, format="json")


def _review(client, case, **body):
    case.refresh_from_db()
    payload = {"version": case.version, "cause": "SYSTEM",
               "remark": "RTA portal was down that morning", **body}
    return client.post(f"{URL}{case.pk}/review/", payload, format="json")


# --- employee reason --------------------------------------------------------------------------


def test_employee_submits_the_reason_and_cannot_touch_the_facts(client_for, ops, opened, hr):
    rahul = client_for(ops["rahul"])
    detail = rahul.get(f"{URL}{opened.pk}/").json()
    assert (detail["can_submit"], detail["can_review"]) == (True, False)
    response = _submit(rahul, opened, task_title="Edited", sla_due_at="2030-01-01T00:00:00Z",
                       cause="EMPLOYEE", priority="LOW")
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["reason_category"]) == ("REASON_SUBMITTED", "DEPENDENCY")
    assert body["cause"] == ""  # the employee's reason is never the authoritative cause
    case = OverdueCase.objects.get()
    assert (case.task_title, case.priority) == ("Map RM codes", "HIGH")
    assert case.sla_due_at == opened.sla_due_at and case.submitted_by == ops["rahul"]
    assert _submit(rahul, opened).status_code == 409  # no second, silent overwrite


@pytest.mark.parametrize(
    ("body", "field"),
    [({"reason_category": "LAZY"}, "reason_category"), ({"explanation": "   "}, "explanation"),
     ({"explanation": ""}, "explanation")],
)
def test_reason_is_validated(client_for, ops, opened, body, field):
    response = _submit(client_for(ops["rahul"]), opened, **body)
    assert response.status_code == 400 and field in response.json()["fields"]


def test_only_the_case_employee_may_submit(client_for, ops, opened):
    assert _submit(client_for(ops["amit"]), opened).status_code == 404  # cannot even see it
    assert _submit(client_for(ops["manager"]), opened).status_code == 403  # sees, may not answer


def test_opening_notifies_the_employee_in_app_only(ops, opened):
    note = Notification.objects.get(kind="OVERDUE_REASON")
    assert note.recipient == ops["rahul"] and note.email_status == "NOT_REQUIRED"
    assert note.task_id == opened.task_id and note.clock_id == opened.clock_id
    assert services.open_case_for_clock(opened.clock, source="TICK") is None
    assert Notification.objects.filter(kind="OVERDUE_REASON").count() == 1


def test_submission_notifies_department_managers_and_hr_but_not_admin(
    client_for, ops, opened, hr, admin_user, staff
):
    rm_manager, _ = staff(roles.OPERATIONS_MANAGER, department="RM")
    _submit(client_for(ops["rahul"]), opened)
    recipients = set(Notification.objects.filter(kind="OVERDUE_REVIEW")
                     .values_list("recipient_id", flat=True))
    assert recipients == {ops["manager"].pk, hr.pk}
    assert admin_user.pk not in recipients and rm_manager.pk not in recipients
    assert set(Notification.objects.filter(kind="OVERDUE_REVIEW")
               .values_list("email_status", flat=True)) == {"NOT_REQUIRED"}


# --- review -----------------------------------------------------------------------------------


@pytest.mark.parametrize("reviewer", ["manager", "hr", "admin"])
def test_eligible_reviewers_record_the_authoritative_cause(
    client_for, ops, opened, hr, admin_user, reviewer
):
    _submit(client_for(ops["rahul"]), opened)
    user = {"manager": ops["manager"], "hr": hr, "admin": admin_user}[reviewer]
    response = _review(client_for(user), opened, cause="EMPLOYEE", remark="File was available")
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["cause"], body["reason_category"]) == (
        "REVIEWED", "EMPLOYEE", "DEPENDENCY",  # both kept, side by side
    )
    assert body["reviewed_by"]["id"] == user.pk and body["can_review"] is False


def test_review_scope_and_who_may_not_review(client_for, ops, opened, staff):
    _submit(client_for(ops["rahul"]), opened)
    rm_manager, _ = staff(roles.OPERATIONS_MANAGER, department="RM")
    assert _review(client_for(rm_manager), opened).status_code == 404  # other department
    assert _review(client_for(ops["amit"]), opened).status_code == 404  # another employee
    assert _review(client_for(ops["rahul"]), opened).status_code == 403  # the employee


def test_self_review_is_forbidden_even_for_a_manager(client_for, ops, work, hr):
    work.raise_task(ops["manager_emp"], 2026, 10, 5, 10, 0)  # the manager's own task
    work.tick(2026, 10, 5, 11, 0)
    case = OverdueCase.objects.get()
    manager = client_for(ops["manager"])
    assert _submit(manager, case).status_code == 200
    assert manager.get(f"{URL}{case.pk}/").json()["can_review"] is False
    assert _review(manager, case).status_code == 403
    assert _review(client_for(hr), case).status_code == 200


def test_review_needs_a_reason_and_complete_input(client_for, ops, opened):
    manager = client_for(ops["manager"])
    assert _review(manager, opened).status_code == 409  # no reason submitted yet
    _submit(client_for(ops["rahul"]), opened)
    for body, field in (({"cause": "MOOD"}, "cause"), ({"remark": " "}, "remark"),
                        ({"remark": ""}, "remark")):
        response = _review(manager, opened, **body)
        assert response.status_code == 400 and field in response.json()["fields"]


def test_a_reviewed_case_is_immutable(client_for, ops, opened, hr):
    _submit(client_for(ops["rahul"]), opened)
    assert _review(client_for(ops["manager"]), opened).status_code == 200
    assert _review(client_for(hr), opened).status_code == 409  # no second review
    assert _submit(client_for(ops["rahul"]), opened).status_code == 409
    case = OverdueCase.objects.get()
    case.cause = "CLIENT"
    with pytest.raises(ReviewedCaseLocked), transaction.atomic():
        case.save()
    assert OverdueCase.objects.get().cause == "SYSTEM"


def test_scope_follows_the_task_department(client_for, ops, work, hr, staff):
    """Approved Q3: an HR-department task assigned to an OPS employee is reviewed by HR or
    Admin, not by the OPS manager."""
    hr_department = Department.objects.get(code="HR")
    work.raise_task(ops["rahul_emp"], 2026, 10, 5, 10, 0, actor=hr, department=hr_department)
    work.tick(2026, 10, 5, 11, 0)
    case = OverdueCase.objects.get()
    assert case.department == hr_department
    _submit(client_for(ops["rahul"]), case)
    assert client_for(ops["manager"]).get(f"{URL}{case.pk}/").status_code == 404
    assert _review(client_for(hr), case).status_code == 200


def test_list_scope_and_review_queue(client_for, ops, opened, hr, staff, work):
    rm_manager, _ = staff(roles.OPERATIONS_MANAGER, department="RM")
    ids = lambda client, **q: [c["id"] for c in client.get(URL, q).json()["results"]]  # noqa: E731
    assert ids(client_for(ops["rahul"])) == [opened.pk]
    assert ids(client_for(ops["amit"])) == []
    assert ids(client_for(rm_manager)) == []
    assert ids(client_for(ops["manager"])) == [opened.pk]
    assert ids(client_for(hr)) == [opened.pk]
    assert ids(client_for(ops["manager"]), reviewable="1") == []  # no reason yet
    _submit(client_for(ops["rahul"]), opened)
    assert ids(client_for(ops["manager"]), reviewable="1") == [opened.pk]
    assert ids(client_for(ops["rahul"]), reviewable="1") == []  # never one's own case
    assert client_for(ops["rahul"]).get(URL, {"status": "WRONG"}).status_code == 400


# --- audit and realtime -----------------------------------------------------------------------


def test_every_step_is_audited(client_for, ops, opened):
    _submit(client_for(ops["rahul"]), opened)
    _review(client_for(ops["manager"]), opened)
    rows = AuditLog.objects.filter(entity_type="overdue_case", entity_id=str(opened.pk))
    assert list(rows.order_by("id").values_list("action", flat=True)) == [
        "overdue_case.opened", "overdue_case.reason_submitted", "overdue_case.reviewed",
    ]
    opened_row = rows.get(action="overdue_case.opened")
    assert opened_row.actor_user is None and opened_row.new_value["opened_via"] == "TICK"
    submitted = rows.get(action="overdue_case.reason_submitted")
    assert submitted.actor_user == ops["rahul"]
    assert submitted.new_value["reason_category"] == "DEPENDENCY"
    reviewed = rows.get(action="overdue_case.reviewed")
    assert reviewed.actor_user == ops["manager"]
    assert (reviewed.new_value["cause"], reviewed.new_value["employee_reason"]) == (
        "SYSTEM", "DEPENDENCY",
    )
    assert reviewed.context["department_id"] == opened.department_id


@pytest.fixture
def published(monkeypatch):
    calls = []
    monkeypatch.setattr(services, "publish_to_user",
                        lambda user_id, event, data: calls.append((user_id, event, data)))
    return calls


def test_realtime_events_go_to_relevant_users_after_commit(
    client_for, ops, work, hr, published, django_capture_on_commit_callbacks
):
    work.raise_task(ops["rahul_emp"], 2026, 10, 5, 10, 0)
    with django_capture_on_commit_callbacks(execute=False) as pending:
        work.tick(2026, 10, 5, 11, 0)
    assert published == [] and len(pending) == 1  # nothing is sent before the commit
    pending[0]()
    case = OverdueCase.objects.get()
    assert published == [(ops["rahul"].pk, "overdue_case.opened",
                          {"case_id": case.pk, "task_id": case.task_id, "status": "OPEN"})]
    published.clear()
    with django_capture_on_commit_callbacks(execute=True):
        _submit(client_for(ops["rahul"]), case)
    assert {(u, e) for u, e, _ in published} == {
        (ops["rahul"].pk, "overdue_case.submitted"), (ops["manager"].pk, "overdue_case.submitted"),
        (hr.pk, "overdue_case.submitted"),
    }
    published.clear()
    with django_capture_on_commit_callbacks(execute=True):
        _review(client_for(ops["manager"]), case)
    assert {(u, e) for u, e, _ in published} == {
        (ops["manager"].pk, "overdue_case.reviewed"), (ops["rahul"].pk, "overdue_case.reviewed"),
    }
    assert ops["amit"].pk not in {u for u, _, _ in published}
