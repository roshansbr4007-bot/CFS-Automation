from apps.audit.scrub import REDACTED, scrub


def test_scrub_redacts_sensitive_keys_recursively():
    value = {
        "email": "a@example.com",
        "password": "hunter2",
        "nested": {"token": "abc", "ok": 1},
        "items": [{"new_password": "x"}, "plain"],
    }
    assert scrub(value) == {
        "email": "a@example.com",
        "password": REDACTED,
        "nested": {"token": REDACTED, "ok": 1},
        "items": [{"new_password": REDACTED}, "plain"],
    }


def test_scrub_keeps_changed_marker_and_plain_values():
    assert scrub({"password": "changed"}) == {"password": "changed"}
    assert scrub(None) is None
    assert scrub(["a", 1]) == ["a", 1]
