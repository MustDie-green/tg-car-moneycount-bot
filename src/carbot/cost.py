"""Во сколько обходится километр — по всем тратам, а не только по бензину.

Наивное «сумма трат / пройденный путь» врёт, когда числитель и знаменатель
живут в разных окнах: двадцать тысяч за ТО известны, а пробег померян на
отрезке в десять километров — и вот уже два рубля за метр.

Поэтому окно общее: от первого замера одометра до последнего. В числитель идут
только траты, попавшие внутрь, — под остальные просто нет километров.

Второе: крупная трата служит дольше окна. Масло за 22 тысячи меняют раз в
7000 км, и вешать его целиком на 1800 км, попавшие в окно, — завышать цену
километра вчетверо. Поэтому крупные траты растягиваются на срок службы: замена
по ТО — на свой интервал, ремонт без интервала (подрамник и прочее) — на
REPAIR_LIFE_KM. В окно попадает только доля, приходящаяся на его километры.

Тюнинг в цену километра не входит вовсе: это не стоимость езды, а желание
сделать машину лучше. Без вебасто машина ездит так же.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from . import service
from .forecast import is_lump, typical_check
from .mileage import Reading, window
from .models import Category, Expense

ZERO = Decimal(0)

# Ниже этих порогов цифра ещё считается, но помечается как ненадёжная
MIN_READINGS = 3
MIN_DISTANCE_KM = 1000
MIN_DAYS = 30
# На сколько километров растягивается крупный ремонт без своего интервала
REPAIR_LIFE_KM = 30_000


@dataclass(slots=True)
class CostPerKm:
    """Разложение цены километра с базой, на которой она посчитана."""

    total: Decimal
    fuel: Decimal | None
    distance: int
    days: int
    readings: int
    expense_count: int
    low_data: bool
    by_category: dict[Category, Decimal] = field(default_factory=dict)
    # Сколько крупных трат отнесено на будущие километры, за пределы окна
    deferred: Decimal = ZERO

    @property
    def basis(self) -> str:
        """«по 8 400 км за 4 месяца, 23 траты» — чтобы было видно, чему верить."""
        km = f"{self.distance:,}".replace(",", " ")
        months = self.days // 30
        period = f"{months} мес." if months >= 1 else f"{self.days} дн."
        return f"по {km} км за {period}, {self.expense_count} трат"


def life_km(
    expense: Expense, intervals: dict[str, int], km_per_day: Decimal
) -> int:
    """Срок службы крупной траты в километрах.

    Берётся самый короткий интервал из того, что точно поменяли: «ТО масло
    и фильтры» живёт до следующей замены масла, а не до замены фильтра.
    Позиции только с интервалом по времени (страховка, техосмотр) переводятся
    в километры по темпу езды.
    """
    sure, _ = service.detect_kinds(expense.note)
    lives: list[int] = []
    for kind in sure:
        default_km, default_days = service.defaults_for(kind)
        km = intervals.get(kind) or default_km
        if km:
            lives.append(km)
        elif default_days and km_per_day > 0:
            lives.append(int(km_per_day * default_days))
    return min(lives) if lives else REPAIR_LIFE_KM


def per_km(
    readings: list[Reading],
    expenses: list[Expense],
    intervals: dict[str, int] | None = None,
) -> CostPerKm | None:
    """Цена километра внутри окна наблюдения. None — если окна нет вовсе.

    intervals — интервалы ТО этой машины по позициям, kind → км.
    """
    bounds = window(readings)
    if bounds is None:
        return None

    first, last = bounds
    distance = last.km - first.km
    days = (last.at - first.at).days
    if distance <= 0:
        return None

    # Трата в момент первого замера — до окна: бензин той заправки сожжён
    # уже на его километрах, а до неё пробег неизвестен
    inside = [
        e for e in expenses
        if first.at < e.spent_at <= last.at and e.category != Category.TUNING
    ]
    if not inside:
        return None

    intervals = intervals or {}
    typical = typical_check(expenses)
    km_per_day = Decimal(distance) / Decimal(max(days, 1))
    divisor = Decimal(distance)

    by_category: dict[Category, Decimal] = {}
    deferred = ZERO
    for expense in inside:
        amount = expense.amount
        if is_lump(amount, typical):
            share = min(Decimal(1), divisor / Decimal(life_km(expense, intervals, km_per_day)))
            deferred += amount * (1 - share)
            amount *= share
        by_category[expense.category] = by_category.get(expense.category, ZERO) + amount

    total_spent = sum(by_category.values(), ZERO)
    fuel_spent = by_category.get(Category.FUEL, ZERO)
    readings_count = len([r for r in readings if first.at <= r.at <= last.at])
    low_data = (
        readings_count < MIN_READINGS
        or distance < MIN_DISTANCE_KM
        or days < MIN_DAYS
    )

    return CostPerKm(
        total=(total_spent / divisor).quantize(Decimal("0.01")),
        fuel=(fuel_spent / divisor).quantize(Decimal("0.01")) if fuel_spent else None,
        distance=distance,
        days=days,
        readings=readings_count,
        expense_count=len(inside),
        low_data=low_data,
        by_category={
            category: (amount / divisor).quantize(Decimal("0.01"))
            for category, amount in by_category.items()
        },
        deferred=deferred.quantize(Decimal("1")),
    )
