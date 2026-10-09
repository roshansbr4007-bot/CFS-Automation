"""User model helpers, the low-level manager, and the users list filter validation."""

import pytest

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.recurring.generator import SCHEDULER_EMAIL

USERS = "/api/v1/users/"
pytestmark = pytest.mark.django_db


def test_manager_create_user_normalises_email_and_hashes_password():
    user = User.objects.create_user("  Mixed.Case@Example.COM ", "Ledger-Manager-2026")
    assert user.email == "mixed.case@example.com"
    assert user.check_password("Ledger-Manager-2026")
    assert user.password != "Ledger-Manager-2026"
    assert user.is_superuser is False


def test_manager_create_user_requires_an_email():
    with pytest.raises(ValueError, match="email address is required"):
        User.objects.create_user("   ", "Ledger-Manager-2026")
    # Updated for Phase 5: the reserved, inactive CFS Scheduler user always exists (migration).
    assert User.objects.exclude(email=SCHEDULER_EMAIL).count() == 0


def test_natural_key_lookup_ignores_case():
    user = UserFactory(email="lookup@example.com")
    assert User.objects.get_by_natural_key("LOOKUP@Example.com") == user


def test_display_helpers_with_names():
    user = UserFactory(email="asha@example.com", first_name="Asha", last_name="Verma")
    assert str(user) == "asha@example.com"
    assert user.get_full_name() == "Asha Verma"
    assert user.get_short_name() == "Asha"


def test_display_helpers_fall_back_to_email_without_names():
    user = UserFactory(email="noname@example.com", first_name="", last_name="")
    assert user.full_name == ""
    assert user.get_full_name() == "noname@example.com"
    assert user.get_short_name() == "noname@example.com"


def test_users_is_active_filter_rejects_other_values(admin_client):
    response = admin_client.get(USERS, {"is_active": "maybe"})
    assert response.status_code == 400
    assert response.json()["fields"] == {"is_active": ["Use true or false."]}


def test_users_is_active_filter_accepts_1_and_0(admin_client, make_user):
    make_user(email="inactive@example.com", is_active=False)
    active = [u["email"] for u in admin_client.get(USERS, {"is_active": "1"}).json()["results"]]
    inactive = [u["email"] for u in admin_client.get(USERS, {"is_active": "0"}).json()["results"]]
    assert "inactive@example.com" not in active and "admin@example.com" in active
    # Updated for Phase 5: the reserved CFS Scheduler user is inactive by design, so it is
    # listed with the inactive users; the test's own users are checked without it.
    assert SCHEDULER_EMAIL in inactive and SCHEDULER_EMAIL not in active
    assert [e for e in inactive if e != SCHEDULER_EMAIL] == ["inactive@example.com"]
