"""create_initial_admin paths not covered by test_seed_and_command.py."""

from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.recurring.generator import SCHEDULER_EMAIL

pytestmark = pytest.mark.django_db
STRONG = "Initial-Admin-Pass-2026"


def _answers(monkeypatch, *values):
    replies = iter(values)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(replies))


def test_email_is_required(monkeypatch):
    monkeypatch.delenv("INITIAL_ADMIN_EMAIL", raising=False)
    with pytest.raises(CommandError, match="INITIAL_ADMIN_EMAIL"):
        call_command("create_initial_admin", "--no-input")
    # Updated for Phase 5: the reserved, inactive CFS Scheduler user always exists (migration).
    assert User.objects.exclude(email=SCHEDULER_EMAIL).count() == 0


def test_prompts_for_password_and_uses_email_from_environment(monkeypatch):
    monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "Prompted.Admin@Example.com")
    monkeypatch.delenv("INITIAL_ADMIN_PASSWORD", raising=False)
    _answers(monkeypatch, STRONG, STRONG)
    out = StringIO()
    call_command("create_initial_admin", stdout=out)
    user = User.objects.get(email="prompted.admin@example.com")
    assert user.check_password(STRONG)
    assert user.role_names == ["Admin"]
    assert "Admin prompted.admin@example.com created." in out.getvalue()


def test_mismatched_passwords_create_nothing(monkeypatch):
    monkeypatch.delenv("INITIAL_ADMIN_PASSWORD", raising=False)
    _answers(monkeypatch, STRONG, STRONG + "-typo")
    with pytest.raises(CommandError, match="Passwords do not match"):
        call_command("create_initial_admin", "--email", "mismatch@example.com")
    assert not User.objects.filter(email="mismatch@example.com").exists()


def test_weak_password_is_reported_with_the_rule_and_nothing_is_created(monkeypatch):
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "12345")
    with pytest.raises(CommandError) as raised:
        call_command("create_initial_admin", "--email", "weak@example.com", "--no-input")
    message = str(raised.value)
    assert message.startswith("Some fields are not valid.")
    assert "password:" in message and "too short" in message
    assert not User.objects.filter(email="weak@example.com").exists()
    assert AuditLog.objects.count() == 0
