"""Сборка всего, что бот знает про машину человека.

Один и тот же набор — машина, пробег, цена километра, ближайшее ТО — нужен и
месячной картинке, и личной карточке, и напоминаниям. Собираем в одном месте,
чтобы три потребителя не расходились в цифрах.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from . import cost, mileage, service
from .cost import CostPerKm
from .db import Repo
from .mileage import MileageEstimate, Reading
from .service import ServiceDue, ServicePlan


def slug_of(car_id: Any) -> str:
    """id машины — car:[chat_id, owner_id, slug]; достаём последний элемент."""
    raw = getattr(car_id, "id", car_id)
    if isinstance(raw, (list, tuple)) and raw:
        return str(raw[-1])
    return str(raw)


@dataclass(slots=True)
class Garage:
    """Машина владельца со всеми производными расчётами."""

    owner_id: int
    slug: str
    title: str
    mileage: MileageEstimate | None = None
    month_km: int | None = None
    cost: CostPerKm | None = None
    due: list[ServiceDue] = field(default_factory=list)

    @property
    def odometer_text(self) -> str | None:
        return self.mileage.format_km() if self.mileage else None


def plan_from_row(row: dict[str, Any]) -> ServicePlan:
    return ServicePlan(
        chat_id=row["chat_id"],
        owner_id=row["owner_id"],
        car_slug=row.get("car_slug", ""),
        car_title=row.get("car_title", ""),
        kind=row["kind"],
        interval_km=row.get("interval_km"),
        interval_days=row.get("interval_days"),
        last_km=row.get("last_km"),
        last_at=row.get("last_at"),
        next_km=row.get("next_km"),
        next_at=row.get("next_at"),
        reminded_at=row.get("reminded_at"),
    )


async def load_all(
    repo: Repo,
    chat_id: int,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    now: datetime,
) -> dict[int, Garage]:
    """Гараж по каждому владельцу чата. Ключ — tg_id."""
    cars = await repo.all_cars(chat_id)
    plans_by_owner: dict[int, list[ServicePlan]] = {}
    for row in await repo.all_service_plans(chat_id):
        plans_by_owner.setdefault(row["owner_id"], []).append(plan_from_row(row))

    garages: dict[int, Garage] = {}
    for row in cars:
        # Одна активная машина на владельца: на неё и вешаются траты
        if not row.get("active", True):
            continue
        owner_id = row["owner_id"]
        garage = Garage(
            owner_id=owner_id,
            slug=slug_of(row["id"]),
            title=row["title"],
        )

        raw = await repo.odometer_readings(chat_id, owner_id)
        readings = [Reading(at=at, km=km) for at, km in raw]
        garage.mileage = mileage.estimate(
            readings, now, owner_monthly_km=row.get("monthly_km")
        )
        if start is not None and end is not None:
            garage.month_km = mileage.distance_between(readings, start, end)

        expenses = await repo.expenses_of_person(chat_id, owner_id)
        garage.cost = cost.per_km(readings, expenses)

        plans = [p for p in plans_by_owner.get(owner_id, []) if p.car_slug == garage.slug]
        garage.due = service.upcoming(plans, garage.mileage, now)

        garages[owner_id] = garage

    return garages


async def load_one(
    repo: Repo,
    chat_id: int,
    owner_id: int,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    now: datetime,
) -> Garage | None:
    garages = await load_all(repo, chat_id, start=start, end=end, now=now)
    return garages.get(owner_id)


def soon(garages: dict[int, Garage], *, limit: int = 4) -> list[tuple[Garage, ServiceDue]]:
    """Кому по всему парку скорее всего пора на сервис — для общей картинки."""
    pairs: list[tuple[Garage, ServiceDue]] = []
    for garage in garages.values():
        for due in garage.due:
            if due.urgency <= 1:
                pairs.append((garage, due))
    pairs.sort(key=lambda item: item[1].urgency)
    return pairs[:limit]
