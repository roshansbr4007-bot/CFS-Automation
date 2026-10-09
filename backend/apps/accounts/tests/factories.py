import factory

from apps.accounts.models import User

DEFAULT_PASSWORD = "Ledger-Sandstone-2026"


class UserFactory(factory.django.DjangoModelFactory):
    """Test data only. Bypasses services, so it writes no audit rows."""

    class Meta:
        model = User
        django_get_or_create = ("email",)

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    first_name = "Test"
    last_name = factory.Sequence(lambda n: f"User{n}")
    password = factory.django.Password(DEFAULT_PASSWORD)
    is_active = True
