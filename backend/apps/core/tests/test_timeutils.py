from datetime import UTC, date, datetime, time

import pytest

from apps.core.timeutils import IST, ist_datetime, now_ist, to_ist


def test_ist_datetime_and_utc_equivalent():
    value = ist_datetime(date(2026, 10, 5), time(23, 59))
    assert value.tzinfo == IST
    assert value.astimezone(UTC) == datetime(2026, 10, 5, 18, 29, tzinfo=UTC)


def test_to_ist_converts_and_rejects_naive():
    assert to_ist(datetime(2026, 10, 5, 18, 29, tzinfo=UTC)).strftime("%d %H:%M") == "05 23:59"
    with pytest.raises(ValueError):
        to_ist(datetime(2026, 10, 5, 18, 29))


def test_ist_datetime_rejects_aware_time():
    with pytest.raises(ValueError):
        ist_datetime(date(2026, 10, 5), time(10, 0, tzinfo=UTC))


def test_now_ist_is_aware():
    assert now_ist().utcoffset().total_seconds() == 5.5 * 3600
