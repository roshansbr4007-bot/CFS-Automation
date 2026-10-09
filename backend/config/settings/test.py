import os

# DATABASE_URL comes from .env locally and from the CI environment; it is never defaulted here,
# so tests always use the same credentials as the running app.
os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-key-not-for-production")

from .base import *  # noqa: E402, F403

DEBUG = False
# Fast hashing in tests only.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
# Phase 8: WebSocket tests use the in-memory channel layer, so the suite never needs Redis.
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
