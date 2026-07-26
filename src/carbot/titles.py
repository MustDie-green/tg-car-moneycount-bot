"""Титулы месяца.

Чисто детерминированная логика: у каждого титула своё условие, приоритет
сверху вниз, один человек получает максимум один титул.
"""

from __future__ import annotations

from decimal import Decimal

from .models import Category, PersonMonth


def assign_titles(people: list[PersonMonth], black_swan_id: int | None = None) -> None:
    """Проставляет .title участникам. Изменяет объекты на месте."""
    if not people:
        return

    taken: set[int] = set()
    candidates: list[tuple[int, str]] = []

    if black_swan_id is not None:
        candidates.append((black_swan_id, "Чёрный лебедь"))

    for category, title in (
        (Category.FUEL, "Бензоколонка"),
        (Category.SERVICE, "Кузнец"),
        (Category.OTHER, "Мойдодыр"),
    ):
        leader = _max_by_category(people, category)
        if leader is not None:
            candidates.append((leader.tg_id, title))

    climber = max(
        (p for p in people if (p.rank_delta or 0) >= 2),
        key=lambda p: p.rank_delta or 0,
        default=None,
    )
    if climber is not None:
        candidates.append((climber.tg_id, "Ракета"))

    faller = min(
        (p for p in people if (p.rank_delta or 0) <= -2),
        key=lambda p: p.rank_delta or 0,
        default=None,
    )
    if faller is not None:
        candidates.append((faller.tg_id, "Свободное падение"))

    # Титул «новичок» не нужен: за него уже отвечает бейдж NEW рядом с суммой

    if len(people) >= 3:
        thrifty = min(people, key=lambda p: p.total)
        candidates.append((thrifty.tg_id, "Скупой рыцарь"))

    by_id = {person.tg_id: person for person in people}
    for tg_id, title in candidates:
        person = by_id.get(tg_id)
        if person is None or tg_id in taken:
            continue
        person.title = title
        taken.add(tg_id)


def _max_by_category(people: list[PersonMonth], category: Category) -> PersonMonth | None:
    """Лидер по категории — но только если он в ней заметно впереди."""
    spenders = [p for p in people if p.by_category.get(category, Decimal(0)) > 0]
    if not spenders:
        return None
    leader = max(spenders, key=lambda p: p.by_category[category])
    if leader.by_category[category] < Decimal(500):
        return None
    return leader
