```python
"""Base settings shared by every environment. Secrets and connections come from the environment."""

from pathlib import Path

import environ
from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # backend/
REPO_ROOT = BASE_DIR.parent

env = environ.Env()
if (REPO_ROOT / ".env").exists():
    environ.Env.read_env(REPO_ROOT / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# CORS: allow the deployed frontend to call the backend.
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])

INSTALLED_APPS = [
    # Phase 8: Daphne first, so runserver serves HTTP and WebSockets (ASGI).
    "daphne",
    "corsheaders",
    # django.contrib.admin is intentionally NOT installed.
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "apps.core",
    "apps.accounts",
    "apps.audit",
    "apps.org",
    "apps.tasks",
    "apps.sla",
    "apps.notifications",
    "apps.calendars",
    "apps.recurring",
    "apps.command_center",
    "apps.performance",
    "apps.realtime",
    "apps.overdue",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.RequestContextMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        # Needed only by the Swagger UI page of drf-spectacular.
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": ["django.template.context_processors.request"]
        },
    }
]

DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = 60
# Services open their own transactions so each change and its audit row commit together.
DATABASES["default"]["ATOMIC_REQUESTS"] = False

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Time: stored in UTC, calculated and displayed in IST.
USE_TZ = True
TIME_ZONE = "Asia/Kolkata"
LANGUAGE_CODE = "en-in"
USE_I18N = False

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Private uploads (task attachments).
MEDIA_ROOT = env("MEDIA_ROOT", default=str(BASE_DIR / "media"))

# Email (SLA notifications).
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=25)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=False)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="cfs-operations@localhost")

# Celery (Phase 5).
CELERY_BROKER_URL = env(
    "CELERY_BROKER_URL",
    default="redis://localhost:6379/0",
)

# Phase 8 real-time delivery (Django Channels).
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [
                env("CHANNEL_REDIS_URL", default="redis://localhost:6379/1")
            ]
        },
    }
}
CELERY_TIMEZONE = "Asia/Kolkata"
CELERY_ENABLE_UTC = True
CELERY_TASK_IGNORE_RESULT = True
CELERY_BEAT_SCHEDULE = {
    "generate-recurring-tasks": {
        "task": "recurring.generate_recurring_tasks",
        "schedule": 60.0,
        "options": {"expires": 55},
    },
    "evaluate-sla-clocks": {
        "task": "sla.evaluate_sla_clocks",
        "schedule": 60.0,
        "options": {"expires": 55},
    },
    # Phase 7.3 (approved E17): KRA performance.
    "kra-daily-calculation": {
        "task": "performance.daily_kra_calculation",
        "schedule": crontab(minute=0, hour=1),
    },
    "kra-month-close": {
        "task": "performance.kra_month_close",
        "schedule": crontab(minute=0, hour=2, day_of_month=1),
    },
}

# Who receives an Overdue escalation.
SLA_BOSS_RESOLVER = env(
    "SLA_BOSS_RESOLVER",
    default="apps.notifications.services.reporting_manager_of_assignee",
)

# Sessions and CSRF.
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = False
CSRF_FAILURE_VIEW = "apps.core.views.csrf_failure"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.core.authentication.SessionAuthentication"
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated"
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer"
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser"
    ],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardPagination",
    "PAGE_SIZE": 25,
    "EXCEPTION_HANDLER": "apps.core.exceptions.exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "CFS Operations Platform API",
    "DESCRIPTION": "Phase 1: authentication, roles, user management and audit log.",
    "VERSION": "1.0.0",
    "SCHEMA_PATH_PREFIX": "/api/v1",
    "COMPONENT_SPLIT_REQUEST": True,
    "SERVE_INCLUDE_SCHEMA": False,
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"],
    "ENUM_NAME_OVERRIDES": {
        "SlaClockKindEnum": "apps.sla.models.ClockKind",
        "NotificationKindEnum": "apps.notifications.models.NotificationKind",
        "TaskStatusEnum": "apps.tasks.models.TaskStatus",
        "OccurrenceStatusEnum": "apps.recurring.models.OccurrenceStatus",
        "CalendarDayKindEnum": "apps.calendars.models.DayKind",
        "OverdueCauseEnum": "apps.overdue.models.OverdueCause",
    },
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
```
