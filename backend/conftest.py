import pytest
from django.contrib.auth.models import Group
from rest_framework.test import APIClient

from apps.accounts import roles
from apps.accounts.tests.factories import DEFAULT_PASSWORD, UserFactory


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def make_user(db):
    def _make(*role_names, **kwargs):
        user = UserFactory(**kwargs)
        if role_names:
            user.groups.set(Group.objects.filter(name__in=role_names))
        return user

    return _make


@pytest.fixture
def admin_user(make_user):
    return make_user(roles.ADMIN, email="admin@example.com")


@pytest.fixture
def client_for():
    def _client(user):
        client = APIClient()
        client.force_login(user)
        return client

    return _client


@pytest.fixture
def admin_client(client_for, admin_user):
    return client_for(admin_user)


@pytest.fixture
def password():
    return DEFAULT_PASSWORD


@pytest.fixture(scope="session", autouse=True)
def allow_audit_log_truncate_during_tests(
    django_db_setup,
    django_db_blocker,
):
    """
    Temporarily disables only the PostgreSQL TRUNCATE trigger on
    audit_log during the pytest session.

    This is required because transaction=True tests use Django's
    database flush during teardown.

    UPDATE and DELETE protection remains active.
    The TRUNCATE trigger is recreated after the test session.
    """

    from django.db import connections

    with django_db_blocker.unblock():
        connection = connections["default"]

        with connection.cursor() as cursor:
            cursor.execute(
                """
                DROP TRIGGER IF EXISTS audit_log_no_truncate
                ON audit_log;
                """
            )

    yield

    with django_db_blocker.unblock():
        connection = connections["default"]

        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TRIGGER audit_log_no_truncate
                BEFORE TRUNCATE ON audit_log
                FOR EACH STATEMENT
                EXECUTE FUNCTION audit_log_block_modification();
                """
            )