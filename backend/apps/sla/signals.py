from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.utils import timezone

from apps.org.models import Employee

from .services import start_login_clocks


@receiver(user_logged_in, dispatch_uid="sla.start_login_clocks")
def on_user_logged_in(sender, request, user, **kwargs):
    employee = Employee.objects.filter(user=user, is_active=True).first()
    if employee is not None:
        start_login_clocks(employee.pk, timezone.now())
