import pytest
from django.core.management import call_command


@pytest.mark.django_db
def test_s1_openapi_schema_validates_without_warnings(tmp_path):
    call_command("spectacular", "--validate", "--fail-on-warn", "--file", str(tmp_path / "s.yml"))
    text = (tmp_path / "s.yml").read_text()
    for path in ("/api/v1/auth/login/", "/api/v1/users/", "/api/v1/audit-log/"):
        assert path in text


@pytest.mark.django_db
def test_m1_no_missing_migrations():
    call_command("makemigrations", "--check", "--dry-run", verbosity=0)
