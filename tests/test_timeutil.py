from datetime import datetime, timezone

import pytest

from carbot import timeutil

TZ = "Europe/Moscow"
YEAR = timeutil.now(TZ).year


@pytest.mark.parametrize(
    "text,expected",
    [
        ("06.2026", (2026, 6)),
        ("6/2026", (2026, 6)),
        ("12-2025", (2025, 12)),
        ("06.26", (2026, 6)),
        ("июнь", (YEAR, 6)),
        ("июн", (YEAR, 6)),
        ("фев", (YEAR, 2)),
        ("сентябрь 2024", (2024, 9)),
        ("7", (YEAR, 7)),
    ],
)
def test_parse_month(text, expected):
    assert timeutil.parse_month(text, TZ) == expected


@pytest.mark.parametrize("text", ["", "прошлый", "абракадабра", "99", "0"])
def test_parse_month_rejects_junk(text):
    assert timeutil.parse_month(text, TZ) is None


def test_month_range_is_utc_and_half_open():
    start, end = timeutil.month_range(2026, 7, TZ)
    assert start == datetime(2026, 6, 30, 21, tzinfo=timezone.utc)  # МСК = UTC+3
    assert end == datetime(2026, 7, 31, 21, tzinfo=timezone.utc)


def test_month_range_over_new_year():
    start, end = timeutil.month_range(2026, 12, TZ)
    assert (end.year, end.month) == (2026, 12)  # 31 декабря 21:00 UTC
    assert timeutil.prev_month(2026, 1) == (2025, 12)


@pytest.mark.parametrize(
    "delta,expected",
    [(-1, (2026, 6)), (-7, (2025, 12)), (1, (2026, 8)), (6, (2027, 1))],
)
def test_add_months(delta, expected):
    assert timeutil.add_months(2026, 7, delta) == expected


def test_days_in_month_handles_leap_year():
    assert timeutil.days_in_month(2024, 2) == 29
    assert timeutil.days_in_month(2026, 2) == 28
    assert timeutil.days_in_month(2026, 7) == 31


def test_month_title_and_date_format():
    assert timeutil.month_title(2026, 7) == "ИЮЛЬ 2026"
    moment = datetime(2026, 7, 14, 22, tzinfo=timezone.utc)
    assert timeutil.format_date(moment, TZ) == "15 июля"  # +3 часа переносят на завтра
