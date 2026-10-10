"""Прогноз трат на месяц, который не улетает от одной крупной покупки.

Наивный прогноз — «потратил за 10 дней, умножь на 3» — честен только для
ровных трат вроде бензина. Подрамник за 61 600 на десятый день превращал
прогноз в 220 тысяч, хотя второго подрамника в этом месяце не будет.

Поэтому траты делятся на две кучки:

- разовые — крупные и заметно выше обычного чека человека. Они случились,
  в прогноз входят ровно один раз и на остаток месяца не размножаются;
- регулярные — всё остальное. Их темп и тянется до конца месяца.

Темп регулярных трат смешивается с прошлыми месяцами: в начале месяца
у текущего темпа мало данных, и история весит больше, к концу — наоборот.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from statistics import median

from . import timeutil
from .models import Expense

ZERO = Decimal(0)

# Разовая трата: не дешевле этого...
LUMP_MIN = Decimal(5000)
# ...и во столько раз дороже обычного чека человека
LUMP_FACTOR = 3
# Сколько прошлых месяцев берём в историю
HISTORY_MONTHS = 3
# Месяц, в котором человек начал писать траты позже этого числа, неполный
PARTIAL_START_DAY = 4


@dataclass(slots=True)
class Forecast:
    total: Decimal
    # Разовые траты этого месяца — посчитаны один раз
    lumps: Decimal
    # Регулярные траты в день, которые тянутся до конца месяца
    daily_rate: Decimal


def typical_check(expenses: list[Expense]) -> Decimal:
    amounts = [e.amount for e in expenses]
    return Decimal(median(amounts)) if amounts else ZERO


def is_lump(amount: Decimal, typical: Decimal) -> bool:
    return amount >= LUMP_MIN and amount >= typical * LUMP_FACTOR


def split(expenses: list[Expense], typical: Decimal) -> tuple[Decimal, Decimal]:
    """(регулярные, разовые) — суммы двух кучек."""
    regular = lumps = ZERO
    for expense in expenses:
        if is_lump(expense.amount, typical):
            lumps += expense.amount
        else:
            regular += expense.amount
    return regular, lumps


def month_forecast(
    history: list[Expense],
    year: int,
    month: int,
    tz_name: str,
    *,
    elapsed: int | None = None,
) -> Forecast:
    """Сколько выйдет к концу месяца по всей истории трат человека.

    elapsed — сколько дней месяца прошло; по умолчанию по часам, задаётся
    руками для бэктеста «что бы бот сказал десятого числа».
    """
    typical = typical_check(history)
    start, end = timeutil.month_range(year, month, tz_name)
    current = [e for e in history if start <= e.spent_at < end]
    total = sum((e.amount for e in current), ZERO)
    regular_now, lumps = split(current, typical)

    if elapsed is None:
        elapsed = timeutil.days_elapsed(year, month, tz_name)
    in_month = timeutil.days_in_month(year, month)
    rate_now = regular_now / elapsed if elapsed else ZERO

    rate_past = _past_rate(history, year, month, tz_name, typical)
    if rate_past is None:
        rate = rate_now
    else:
        weight = Decimal(elapsed) / Decimal(in_month)
        rate = weight * rate_now + (1 - weight) * rate_past

    remaining = max(in_month - elapsed, 0)
    return Forecast(
        total=(total + rate * remaining).quantize(Decimal("1")),
        lumps=lumps,
        daily_rate=rate.quantize(Decimal("1")),
    )


def _past_rate(
    history: list[Expense], year: int, month: int, tz_name: str, typical: Decimal
) -> Decimal | None:
    """Медиана дневного темпа регулярных трат за прошлые полные месяцы."""
    if not history:
        return None
    first = min(e.spent_at for e in history).astimezone(timeutil.tz(tz_name))

    rates: list[Decimal] = []
    for offset in range(1, HISTORY_MONTHS + 1):
        past_year, past_month = timeutil.add_months(year, month, -offset)
        if (past_year, past_month) < (first.year, first.month):
            break
        # Месяц, когда человек только начал писать траты, неполный: его
        # дневной темп занижен днями, когда трат в боте ещё не было
        if (past_year, past_month) == (first.year, first.month) and first.day >= PARTIAL_START_DAY:
            break
        start, end = timeutil.month_range(past_year, past_month, tz_name)
        regular, _ = split([e for e in history if start <= e.spent_at < end], typical)
        rates.append(regular / timeutil.days_in_month(past_year, past_month))

    return Decimal(median(rates)) if rates else None


def year_pace(expenses: list[Expense], days: int) -> Decimal:
    """«Таким темпом за год»: регулярное — по темпу, разовое — один раз."""
    if days <= 0:
        return ZERO
    regular, lumps = split(expenses, typical_check(expenses))
    return regular / days * 365 + lumps
