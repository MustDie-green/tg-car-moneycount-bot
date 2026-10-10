"""Прогноз трат на месяц: разовое — один раз, регулярное — по темпу."""

from datetime import datetime, timezone
from decimal import Decimal

from carbot.forecast import Forecast, is_lump, month_forecast, year_pace
from carbot.models import Category, Expense

TZ = "Europe/Moscow"


def spent(month: int, day: int, amount, category=Category.FUEL) -> Expense:
    return Expense(
        chat_id=-1, author_id=1, author_name="В", message_id=month * 100 + day,
        amount=Decimal(amount), category=category, note="",
        spent_at=datetime(2026, month, day, 12, tzinfo=timezone.utc),
    )


def regular_month(month: int, per_fill: int = 3000) -> list[Expense]:
    """Восемь заправок по 3000 — ровный месяц на 24 000."""
    return [spent(month, day, per_fill) for day in (2, 6, 10, 14, 18, 22, 26, 29)]


def test_big_one_off_is_counted_once():
    """Подрамник на десятый день не превращает месяц в 220 тысяч."""
    history = regular_month(8) + regular_month(9)
    october = [spent(10, 3, 3000), spent(10, 7, 3000), spent(10, 9, 61600, Category.SERVICE)]
    result = month_forecast(history + october, 2026, 10, TZ, elapsed=10)

    naive = sum(e.amount for e in october) / 10 * 31
    assert naive > 200_000
    assert result.lumps == Decimal(61600)
    # 61 600 один раз плюс обычный месячный темп ~24 000
    assert 80_000 < result.total < 95_000


def test_history_steadies_the_start_of_month():
    """Третьего числа одна заправка — это не 31 заправка за месяц."""
    history = regular_month(8) + regular_month(9)
    october = [spent(10, 1, 3000), spent(10, 2, 3000)]
    result = month_forecast(history + october, 2026, 10, TZ, elapsed=3)
    naive = Decimal(6000) / 3 * 31  # 62 000
    # Уже потраченные 6000 плюс почти исторический темп — около 31 000
    assert result.total < naive * Decimal("0.6")
    assert 25_000 < result.total < 35_000


def test_without_history_falls_back_to_current_pace():
    october = [spent(10, 1, 3000), spent(10, 5, 3000)]
    result = month_forecast(october, 2026, 10, TZ, elapsed=10)
    assert result.total == Decimal(6000) / 10 * 31


def test_partial_first_month_is_not_history():
    """Бот появился 26-го — июль не показатель, его темп занижен."""
    july = [spent(7, 27, 3000)]
    october = [spent(10, 1, 3000), spent(10, 5, 3000)]
    result = month_forecast(july + october, 2026, 8, TZ, elapsed=10)
    assert isinstance(result, Forecast)
    # Июль в истории не участвует: темп считается только по августу
    assert month_forecast(july, 2026, 8, TZ, elapsed=10).daily_rate == 0


def test_lump_rule():
    assert is_lump(Decimal(61600), typical=Decimal(3000))
    assert not is_lump(Decimal(4000), typical=Decimal(1000))  # дешевле 5000
    assert not is_lump(Decimal(6000), typical=Decimal(3000))  # всего вдвое


def test_year_pace_does_not_repeat_one_offs():
    month = regular_month(9) + [spent(9, 15, 61600, Category.SERVICE)]
    pace = year_pace(month, days=30)
    assert pace == Decimal(24000) / 30 * 365 + 61600
