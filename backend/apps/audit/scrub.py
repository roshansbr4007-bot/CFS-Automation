"""Keeps secrets out of audit values. Pure function, no Django imports."""

SENSITIVE_KEYS = frozenset(
    {"password", "password_hash", "new_password", "current_password", "session_key",
     "sessionid", "token", "csrftoken", "csrf_token", "secret", "api_key"}
)
# The only value a sensitive key may carry: the marker that it changed.
CHANGED_MARKER = "changed"
REDACTED = "[redacted]"


def scrub(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                out[key] = CHANGED_MARKER if item == CHANGED_MARKER else REDACTED
            else:
                out[key] = scrub(item)
        return out
    if isinstance(value, (list, tuple)):
        return [scrub(item) for item in value]
    return value
