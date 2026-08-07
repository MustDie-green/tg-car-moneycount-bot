"""Во сколько обходится километр — по всем тратам, а не только по бензину.

Наивное «сумма трат / пройденный путь» врёт, когда числитель и знаменатель
живут в разных окнах: двадцать тысяч за ТО известны, а пробег померян на
отрезке в десять километров — и вот уже два рубля за метр.

Поэтому окно общее: от первого замера одометра до последнего. В числитель идут
только траты, попавшие внутрь, — под остальные просто нет километров.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .mileage import Reading, window
from .models import Category, Expense

ZERO = Decimal(0)

# Ниже этих порогов цифра ещё считается, но помечается как ненадёжная
MIN_READINGS = 3
MIN_DISTANCE_KM = 1000
MIN_DAYS = 30
# Трата, которая одна тянет больше половины суммы, показывается отдельно
OUTLIER_SHARE = Decimal("0.5")


@dataclass(slots=True)
class CostPerKm:
    """Разложение цены километра с базой, на которой она посчитана."""

    total: Decimal
    fuel: Decimal | None
    # Та же цифра без самой крупной траты — если она перекашивает картину
    without_outlier: Decimal | None
    distance: int
    days: int
    readings: int
    expense_count: int
    low_data: bool
    by_category: dict[Category, Decimal] = field(default_factory=dict)

    @property
    def basis(self) -> str:
        """«по 8 400 км за 4 месяца, 23 траты» — чтобы было видно, чему верить."""
        km = f"{self.distance:,}".replace(",", " ")
        months = self.days // 30
        period = f"{months} мес." if months >= 1 else f"{self.days} дн."
        return f"по {km} км за {period}, {self.expense_count} трат"


def per_km(readings: list[Reading], expenses: list[Expense]) -> CostPerKm | None:
    """Цена километра внутри окна наблюдения. None — если окна нет вовсе."""
    bounds = window(readings)
    if bounds is None:
        return None

    first, last = bounds
    distance = last.km - first.km
    days = (last.at - first.at).days
    if distance <= 0:
        return None

    # Ключевой момент: берём только траты внутри окна замеров
    inside = [e for e in expenses if first.at <= e.spent_at <= last.at]
    if not inside:
        return None

    total_spent = sum((e.amount for e in inside), ZERO)
    by_category: dict[Category, Decimal] = {}
    for expense in inside:
        previous = by_category.get(expense.category, ZERO)
        by_category[expense.category] = previous + expense.amount

    divisor = Decimal(distance)
    fuel_spent = by_category.get(Category.FUEL, ZERO)

    biggest = max((e.amount for e in inside), default=ZERO)
    without_outlier = None
    if total_spent > 0 and biggest / total_spent > OUTLIER_SHARE and len(inside) > 1:
        without_outlier = ((total_spent - biggest) / divisor).quantize(Decimal("0.01"))

    readings_count = len([r for r in readings if first.at <= r.at <= last.at])
    low_data = (
        readings_count < MIN_READINGS
        or distance < MIN_DISTANCE_KM
        or days < MIN_DAYS
    )

    return CostPerKm(
        total=(total_spent / divisor).quantize(Decimal("0.01")),
        fuel=(fuel_spent / divisor).quantize(Decimal("0.01")) if fuel_spent else None,
        without_outlier=without_outlier,
        distance=distance,
        days=days,
        readings=readings_count,
        expense_count=len(inside),
        low_data=low_data,
        by_category={
            category: (amount / divisor).quantize(Decimal("0.01"))
            for category, amount in by_category.items()
        },
    )
