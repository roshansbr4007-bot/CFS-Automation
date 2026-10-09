from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.utils import timezone

from .services import record_daily_login


@receiver(user_logged_in, dispatch_uid="org.record_daily_login")
def on_user_logged_in(sender, request, user, **kwargs):
    record_daily_login(user=user, at=timezone.now())
