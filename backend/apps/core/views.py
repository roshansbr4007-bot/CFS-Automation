from django.http import JsonResponse


def csrf_failure(request, reason=""):
    """Django's CSRF failure page, as JSON in the API's error shape."""
    return JsonResponse(
        {
            "code": "csrf_failed",
            "message": "Security token missing or invalid. Reload the page.",
            "fields": {},
        },
        status=403,
    )
