"""R2: every API view states its permission explicitly; none relies on the default."""

from django.urls import URLPattern, URLResolver, get_resolver


def _walk(patterns, prefix=""):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _walk(pattern.url_patterns, prefix + str(pattern.pattern))
        elif isinstance(pattern, URLPattern):
            yield prefix + str(pattern.pattern), pattern.callback


def test_r2_every_api_view_declares_permission_classes():
    missing = []
    for route, callback in _walk(get_resolver().url_patterns):
        if not route.startswith("api/v1/"):
            continue
        view_class = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
        assert view_class is not None, route
        if "permission_classes" not in view_class.__dict__:
            missing.append(route)
    assert not missing, f"Views without explicit permission_classes: {missing}"
