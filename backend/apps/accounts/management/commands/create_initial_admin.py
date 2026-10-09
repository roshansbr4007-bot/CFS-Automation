"""Creates the first Admin user. Audited as a system action.

Email: --email or INITIAL_ADMIN_EMAIL. Password: INITIAL_ADMIN_PASSWORD if set (CI / e2e),
otherwise typed in twice. Refuses to run if a user with that email already exists.
"""

import getpass
import os

from django.core.management.base import BaseCommand, CommandError

from apps.accounts import services
from apps.accounts.managers import normalize_email
from apps.accounts.models import User
from apps.core.errors import AppError


class Command(BaseCommand):
    help = "Create the first Admin user (email login)."

    def add_arguments(self, parser):
        parser.add_argument("--email", default=None)
        parser.add_argument("--first-name", default="")
        parser.add_argument("--last-name", default="")
        parser.add_argument("--no-input", action="store_true", help="Never prompt.")

    def handle(self, *args, **options):
        email = normalize_email(options["email"] or os.environ.get("INITIAL_ADMIN_EMAIL"))
        if not email:
            raise CommandError("Give --email or set INITIAL_ADMIN_EMAIL in .env.")
        if User.objects.filter(email=email).exists():
            raise CommandError(f"A user with email {email} already exists.")

        password = os.environ.get("INITIAL_ADMIN_PASSWORD") or ""
        if not password:
            if options["no_input"]:
                raise CommandError("Set INITIAL_ADMIN_PASSWORD or run without --no-input.")
            password = getpass.getpass("Password for the initial Admin: ")
            if password != getpass.getpass("Password again: "):
                raise CommandError("Passwords do not match.")

        try:
            user = services.create_initial_admin(
                email=email,
                password=password,
                first_name=options["first_name"],
                last_name=options["last_name"],
            )
        except AppError as exc:
            details = "; ".join(f"{k}: {' '.join(v)}" for k, v in exc.fields.items())
            raise CommandError(f"{exc.message} {details}".strip()) from exc
        self.stdout.write(self.style.SUCCESS(f"Admin {user.email} created."))
