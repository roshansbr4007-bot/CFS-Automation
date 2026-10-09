"""Append-only comments and the minimal, private attachment foundation."""

import io
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts import roles
from apps.audit.models import AuditLog
from apps.tasks import attachments as attachment_rules
from apps.tasks.models import TaskComment

TASKS = "/api/v1/tasks/"
pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
EML = b"From: client@example.com\r\nSubject: SIP\r\n\r\nPlease process.\r\n"


def _xlsx() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", "<workbook/>")
    return buffer.getvalue()


@pytest.fixture
def task(ops, new_task):
    return new_task(ops["manager"], ops["rahul_emp"])


def _upload(client, task, name, content):
    return client.post(
        f"{TASKS}{task.pk}/attachments/",
        {"file": SimpleUploadedFile(name, content)},
        format="multipart",
    )


# --- comments ---------------------------------------------------------------------------------


def test_assignee_manager_and_creator_comment(client_for, ops, task):
    for user in (ops["rahul"], ops["manager"]):
        response = client_for(user).post(f"{TASKS}{task.pk}/comments/", {"body": " Noted "})
        assert response.status_code == 201 and response.json()["body"] == "Noted"
    listed = client_for(ops["rahul"]).get(f"{TASKS}{task.pk}/comments/").json()
    assert [c["author"]["id"] for c in listed] == [ops["rahul"].pk, ops["manager"].pk]
    row = AuditLog.objects.filter(action="task.comment_added").order_by("id").first()
    assert row.new_value == {"comment_id": listed[0]["id"], "length": 5}


def test_hr_reads_comments_but_cannot_write(client_for, staff, task):
    hr, _ = staff(roles.HR, department="HR")
    client = client_for(hr)
    assert client.get(f"{TASKS}{task.pk}/comments/").status_code == 200
    assert client.post(f"{TASKS}{task.pk}/comments/", {"body": "hi"}).status_code == 403


def test_blank_comment_and_no_edit_path(client_for, ops, task):
    client = client_for(ops["rahul"])
    response = client.post(f"{TASKS}{task.pk}/comments/", {"body": "   "})
    assert response.status_code == 400 and "body" in response.json()["fields"]
    assert client.patch(f"{TASKS}{task.pk}/comments/", {"body": "x"}).status_code == 405
    assert client.delete(f"{TASKS}{task.pk}/comments/").status_code == 405
    assert not TaskComment.objects.exists()


def test_outsider_gets_404_for_comments(client_for, ops, task):
    assert client_for(ops["amit"]).get(f"{TASKS}{task.pk}/comments/").status_code == 404


# --- attachments ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("folio.pdf", PDF),
        ("scan.PNG", PNG),
        ("photo.jpg", JPG),
        ("mail.eml", EML),
        ("sheet.xlsx", None),
    ],
)
def test_allowed_types_upload_and_are_audited(client_for, ops, task, name, content):
    content = content if content is not None else _xlsx()
    response = _upload(client_for(ops["rahul"]), task, name, content)
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["original_filename"] == name and body["size_bytes"] == len(content)
    assert len(body["sha256"]) == 64
    row = AuditLog.objects.get(action="task.attachment_added")
    assert row.new_value["attachment_id"] == body["id"]


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("tool.exe", b"MZ" + b"\x00" * 10, "Allowed file types"),
        ("fake.pdf", b"not a pdf at all", "does not match"),
        ("fake.xlsx", b"PK not really a zip", "does not match"),
        ("fake.png", PDF, "does not match"),
        ("empty.pdf", b"", "empty"),
    ],
)
def test_rejected_uploads(client_for, ops, task, name, content, message):
    response = _upload(client_for(ops["rahul"]), task, name, content)
    assert response.status_code == 400
    assert message in " ".join(response.json()["fields"].get("file", [""]))


def test_size_limit(client_for, ops, task, monkeypatch):
    monkeypatch.setattr(attachment_rules, "MAX_BYTES", 10)
    response = _upload(client_for(ops["rahul"]), task, "big.pdf", PDF)
    assert response.status_code == 400 and "10 MB" in response.json()["fields"]["file"][0]


def test_eml_without_headers_is_rejected(client_for, ops, task):
    response = _upload(client_for(ops["rahul"]), task, "blank.eml", b"\r\n\r\njust a body")
    assert response.status_code == 400


def test_download_is_permission_checked(client_for, ops, staff, task):
    attachment_id = _upload(client_for(ops["rahul"]), task, "folio.pdf", PDF).json()["id"]
    url = f"{TASKS}{task.pk}/attachments/{attachment_id}/download/"
    response = client_for(ops["manager"]).get(url)
    assert response.status_code == 200
    assert response["Content-Type"] == "application/octet-stream"
    assert "folio.pdf" in response["Content-Disposition"]
    assert b"".join(response.streaming_content) == PDF
    assert client_for(ops["amit"]).get(url).status_code == 404
    missing = f"{TASKS}{task.pk}/attachments/999999/download/"
    assert client_for(ops["manager"]).get(missing).status_code == 404
    listed = client_for(ops["manager"]).get(f"{TASKS}{task.pk}/attachments/").json()
    assert [a["id"] for a in listed] == [attachment_id]


def test_hr_cannot_upload(client_for, staff, task):
    hr, _ = staff(roles.HR, department="HR")
    assert _upload(client_for(hr), task, "folio.pdf", PDF).status_code == 403


def test_upload_needs_a_file(client_for, ops, task):
    url = f"{TASKS}{task.pk}/attachments/"
    response = client_for(ops["rahul"]).post(url, {}, format="multipart")
    assert response.status_code == 400 and "file" in response.json()["fields"]
