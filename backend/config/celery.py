"""Celery application (Phase 5). Tasks are thin wrappers around service functions; all business
rules live in the services so they run the same from Beat, a worker or a management command.

Local (Windows):  celery -A config worker -l info --pool=solo
                  celery -A config beat -l info
Production: run workers as needed but EXACTLY ONE beat process.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("cfs_operations")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
