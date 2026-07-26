"""Работа с месяцами в часовом поясе чата."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MONTHS_NOMINATIVE = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)

MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def tz(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def now(tz_name: str) -> datetime:
    return datetime.now(tz(tz_name))


def month_range(year: int, month: int, tz_name: str) -> tuple[datetime, datetime]:
    """Границы месяца в UTC — так их понимает SurrealDB."""
    zone = tz(tz_name)
    start = datetime(year, month, 1, tzinfo=zone)
    end_year, end_month = (year + 1, 1) if month == 12 else (year, month + 1)
    end = datetime(end_year, end_month, 1, tzinfo=zone)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    index = (year * 12 + month - 1) + delta
    return index // 12, index % 12 + 1


def month_title(year: int, month: int) -> str:
    return f"{MONTHS_NOMINATIVE[month - 1].upper()} {year}"


def format_date(value: datetime, tz_name: str) -> str:
    local = value.astimezone(tz(tz_name))
    return f"{local.day} {MONTHS_GENITIVE[local.month - 1]}"


def days_in_month(year: int, month: int) -> int:
    end_year, end_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return (datetime(end_year, end_month, 1) - datetime(year, month, 1)).days


def days_elapsed(year: int, month: int, tz_name: str) -> int:
    """Сколько дней месяца уже прошло — для прогноза до конца месяца."""
    current = now(tz_name)
    if (current.year, current.month) != (year, month):
        return days_in_month(year, month)
    return max(1, current.day)


def parse_month(text: str, tz_name: str) -> tuple[int, int] | None:
    """«07.2026», «7/2026», «июль», «июль 2025» → (year, month)."""
    raw = text.strip().lower().replace("ё", "е")
    if not raw:
        return None
    current = now(tz_name)

    for separator in (".", "/", "-", " "):
        if separator in raw:
            left, _, right = raw.partition(separator)
            if left.isdigit() and right.isdigit():
                month, year = int(left), int(right)
                if year < 100:
                    year += 2000
                if 1 <= month <= 12:
                    return year, month

    words = [word for word in raw.split() if len(word) >= 3]
    for position, name in enumerate(MONTHS_NOMINATIVE, start=1):
        stem = name[:4]
        # Ловим и «июнь», и «июн», и сокращение «фев»
        if any(word.startswith(stem) or stem.startswith(word) for word in words):
            year = next(
                (int(w) for w in raw.split() if w.isdigit() and len(w) == 4),
                current.year,
            )
            return year, position

    if raw.isdigit() and 1 <= int(raw) <= 12:
        return current.year, int(raw)
    return None


def shift_to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "tz", "now", "month_range", "prev_month", "add_months", "month_title",
    "format_date", "days_in_month", "days_elapsed", "parse_month",
    "shift_to_utc", "timedelta",
]
