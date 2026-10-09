from datetime import datetime, time, timedelta

from django.utils.dateparse import parse_date, parse_datetime

from apps.core.errors import FieldValidationError
from apps.core.timeutils import ist_datetime

from .models import AuditLog


def _parse_bound(raw: str, field: str, *, end: bool) -> datetime:
    """Accepts an ISO datetime, or a date meaning that whole day in IST."""
    # Date first: on Python 3.11+ parse_datetime() also accepts a bare date (as a naive
    # midnight), which would wrongly trigger the "timezone offset" error below.
    try:
        day = parse_date(raw)
        value = None if day is not None else parse_datetime(raw)
    except ValueError:  # well-formed but impossible, e.g. 2026-13-01
        raise FieldValidationError(fields={field: ["Not a real date."]}) from None
    if day is not None:
        start = ist_datetime(day, time(0, 0))
        return start + timedelta(days=1) if end else start
    if value is None:
        raise FieldValidationError(fields={field: ["Use YYYY-MM-DD or an ISO datetime."]})
    if value.tzinfo is None:
        raise FieldValidationError(fields={field: ["Include a timezone offset."]})
    return value


def search(params):
    qs = AuditLog.objects.select_related("actor_user")
    if actor := params.get("actor"):
        if not str(actor).isdigit():
            raise FieldValidationError(fields={"actor": ["Must be a user id."]})
        qs = qs.filter(actor_user_id=int(actor))
    if action := params.get("action"):
        qs = qs.filter(action=action)
    if entity_type := params.get("entity_type"):
        qs = qs.filter(entity_type=entity_type)
    if entity_id := params.get("entity_id"):
        qs = qs.filter(entity_id=str(entity_id))
    if raw_from := params.get("from"):
        qs = qs.filter(occurred_at__gte=_parse_bound(raw_from, "from", end=False))
    if raw_to := params.get("to"):
        qs = qs.filter(occurred_at__lt=_parse_bound(raw_to, "to", end=True))
    return qs.order_by("-occurred_at", "-id")
