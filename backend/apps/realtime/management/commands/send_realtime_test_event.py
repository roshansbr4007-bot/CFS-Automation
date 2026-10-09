"""`python manage.py send_realtime_test_event --email <user>`

Phase 8 manual verification only: sends a harmless `system.test` event to that one user's
private group. Not reachable over REST or WebSocket.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.realtime.events import publish_to_user


class Command(BaseCommand):
    help = "Send a harmless system.test real-time event to one user (manual verification)."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True, help="The recipient's login email.")

    def handle(self, *args, email, **options):
        user = get_user_model().objects.filter(email__iexact=email.strip()).first()
        if user is None:
            raise CommandError(f"No user with email {email}.")
        if not user.is_active:
            raise CommandError(f"User {email} is inactive.")
        sent = publish_to_user(user.pk, "system.test", {"message": "Phase 8 test event"})
        if not sent:
            raise CommandError(
                "Event not sent: the channel layer is unavailable (is Redis running and "
                "CHANNEL_REDIS_URL correct?)."
            )
        self.stdout.write(f"system.test sent to {user.email} (group user.{user.pk}).")
