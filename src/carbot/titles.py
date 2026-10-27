"""Звания месяца.

Чисто детерминированная логика. У каждого звания своё условие и «сила» —
насколько человек выделяется: лидер, который вдвое обогнал второго, сильнее
лидера, обошедшего второго на пару процентов. Звания раздаются от сильных к
слабым, одному человеку достаётся одно — самое яркое из заслуженных.

Без силы лидеры пяти категорий разбирали бы всех участников ещё до того,
как очередь дойдёт до Дальнобоя или Ночного гонщика.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from .models import Category, PersonMonth

ZERO = Decimal(0)
# Единственный участник в номинации — тоже заметно, но не бесконечно
SOLE_LEADER = 3.0
MAX_STRENGTH = 5.0

CATEGORY_TITLES: dict[Category, str] = {
    Category.FUEL: "Бензоколонка",
    Category.SERVICE: "Кузнец",
    Category.TUNING: "Ксзибит",
    Category.FINES: "Любимец камер",
    Category.CARE: "Мойдодыр",
    Category.STATE: "Меценат дорожного фонда",
}

Candidate = tuple[float, int, str]


def assign_titles(
    people: list[PersonMonth],
    black_swan_id: int | None = None,
    *,
    swan_is_tuning: bool = False,
) -> None:
    """Проставляет .titles участникам. Изменяет объекты на месте."""
    if not people:
        return

    candidates: list[Candidate] = []
    if black_swan_id is not None:
        # Одна трата, на голову выше всех остальных, — главное событие месяца.
        # Если это апгрейд, а не поломка, — это не лебедь, это прокачка тачки
        title = "Ксзибит" if swan_is_tuning else "Чёрный лебедь"
        candidates.append((10.0, black_swan_id, title))

    for category, title in CATEGORY_TITLES.items():
        _add(candidates, title, _top(
            people, lambda p, c=category: _money(p.by_category.get(c, ZERO)), floor=500
        ))

    _add(candidates, "Дальнобой", _top(people, lambda p: p.month_km or 0, floor=1000))
    _add(candidates, "Домосед", _bottom(people, lambda p: p.month_km or 0))
    _add(candidates, "Танкер", _top(people, lambda p: _money(p.liters_per_100), need=2))
    _add(candidates, "Верблюд", _bottom(people, lambda p: _money(p.liters_per_100)))
    _add(candidates, "Завсегдатай АЗС", _top(people, lambda p: p.fuel_count, floor=5))
    _add(candidates, "Олигарх", _top(
        people, lambda p: _money(p.total) / p.count if p.count >= 2 else 0, floor=3000, need=2
    ))
    _add(candidates, "Ночной гонщик", _top(people, lambda p: p.night_count, floor=2))

    for person in people:
        tuning = person.by_category.get(Category.TUNING, ZERO)
        rest = person.total - tuning
        # Апгрейды дороже всего остального вместе взятого — машина-проект
        if tuning >= 10_000 and tuning > rest:
            strength = min(float(tuning / rest) if rest > 0 else SOLE_LEADER, MAX_STRENGTH)
            candidates.append((strength, person.tg_id, "Форсаж"))
        # Траты чуть ли не во всех категориях: тюнинг в норме бывает не у всех
        if sum(person.by_category.get(c, ZERO) > 0 for c in Category) >= len(Category) - 1:
            candidates.append((2.5, person.tg_id, "Человек-оркестр"))

    climber = max(people, key=lambda p: p.rank_delta or 0)
    if (climber.rank_delta or 0) >= 2:
        candidates.append((1.5 + 0.3 * climber.rank_delta, climber.tg_id, "Ракета"))
    faller = min(people, key=lambda p: p.rank_delta or 0)
    if (faller.rank_delta or 0) <= -2:
        candidates.append((1.5 + 0.3 * -faller.rank_delta, faller.tg_id, "Свободное падение"))

    if len(people) >= 3:
        # Утешительное звание: достаётся, только если ничего ярче не нашлось
        thrifty = _bottom(people, lambda p: _money(p.total))
        if thrifty is not None:
            _add(candidates, "Скупой рыцарь", (min(thrifty[0], 1.2), thrifty[1]))

    _distribute(people, candidates)


# Сколько званий может достаться одному человеку
MAX_TITLES = 2
# Второе звание — только за заметное отличие, иначе плашками обрастут все
SECOND_TITLE_STRENGTH = 1.3


def _distribute(people: list[PersonMonth], candidates: list[Candidate]) -> None:
    """Раздаёт звания в два круга, самые сильные — первыми.

    Первый круг — каждому по одному: так звание не достанется дважды одному
    человеку, пока у кого-то нет ни одного. Второй круг — вторые звания из
    оставшихся, но только сильные. Каждое звание — одному человеку.
    """
    by_id = {person.tg_id: person for person in people}
    for person in people:
        person.titles = []
    taken_titles: set[str] = set()
    # При равной силе порядок объявления: он и задаёт приоритет
    ordered = [
        candidate
        for _, candidate in sorted(enumerate(candidates), key=lambda item: (-item[1][0], item[0]))
    ]

    for round_number in range(MAX_TITLES):
        for strength, tg_id, title in ordered:
            person = by_id.get(tg_id)
            if person is None or title in taken_titles or len(person.titles) != round_number:
                continue
            if round_number > 0 and strength < SECOND_TITLE_STRENGTH:
                continue
            person.titles.append(title)
            taken_titles.add(title)


def _money(value: Decimal | None) -> float:
    return float(value) if value is not None else 0.0


def _add(candidates: list[Candidate], title: str, found: tuple[float, int] | None) -> None:
    if found is not None:
        strength, tg_id = found
        candidates.append((strength, tg_id, title))


def _top(
    people: list[PersonMonth],
    key: Callable[[PersonMonth], float],
    *,
    floor: float = 0,
    need: int = 1,
) -> tuple[float, int] | None:
    """Лидер и насколько он оторвался от второго места."""
    ranked = sorted((p for p in people if key(p) > 0), key=key, reverse=True)
    if len(ranked) < need or not ranked or key(ranked[0]) < floor:
        return None
    if len(ranked) == 1:
        return SOLE_LEADER, ranked[0].tg_id
    first, second = key(ranked[0]), key(ranked[1])
    if first == second:
        return None
    return min(first / second, MAX_STRENGTH), ranked[0].tg_id


def _bottom(
    people: list[PersonMonth], key: Callable[[PersonMonth], float]
) -> tuple[float, int] | None:
    """Самый скромный — только среди тех, у кого вообще есть что сравнивать."""
    ranked = sorted((p for p in people if key(p) > 0), key=key)
    if len(ranked) < 2:
        return None
    first, second = key(ranked[0]), key(ranked[1])
    if first == second:
        return None
    return min(second / first, MAX_STRENGTH), ranked[0].tg_id
