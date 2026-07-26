"""Сборка отчётов из сырых трат: ранги, титулы, расход топлива, прогнозы."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from . import timeutil
from .db import Repo
from .formatting import money, plural, with_plural
from .models import Category, Expense, MonthReport, PersonMonth
from .titles import assign_titles

ZERO = Decimal(0)


# --- Расход топлива ----------------------------------------------------------


@dataclass(slots=True)
class FuelStats:
    liters_per_100: Decimal | None
    rub_per_km: Decimal | None
    price_per_liter: Decimal | None
    distance: int
    fills: int


def fuel_stats(expenses: list[Expense]) -> FuelStats | None:
    """Считает расход методом полного бака по последовательным заправкам.

    Между двумя заправками с известным пробегом машина проехала (odo2 - odo1)
    километров и залила liters2 литров — отсюда и л/100км, и рубли за километр.
    Отрезки с бредовыми числами выбрасываем.
    """
    with_odo = [e for e in expenses if e.odometer and e.liters]
    if len(with_odo) < 2:
        return _price_only(expenses)

    total_distance = 0
    total_liters = ZERO
    total_cost = ZERO
    segments = 0

    for previous, current in zip(with_odo, with_odo[1:]):
        distance = (current.odometer or 0) - (previous.odometer or 0)
        if not 30 <= distance <= 3000:
            continue
        consumption = (current.liters or ZERO) / Decimal(distance) * 100
        if not Decimal(2) <= consumption <= Decimal(35):
            continue
        total_distance += distance
        total_liters += current.liters or ZERO
        total_cost += current.amount
        segments += 1

    if not segments or not total_distance:
        return _price_only(expenses)

    return FuelStats(
        liters_per_100=(total_liters / Decimal(total_distance) * 100).quantize(Decimal("0.1")),
        rub_per_km=(total_cost / Decimal(total_distance)).quantize(Decimal("0.01")),
        price_per_liter=_avg_price(expenses),
        distance=total_distance,
        fills=segments + 1,
    )


def _price_only(expenses: list[Expense]) -> FuelStats | None:
    price = _avg_price(expenses)
    if price is None:
        return None
    return FuelStats(
        liters_per_100=None,
        rub_per_km=None,
        price_per_liter=price,
        distance=0,
        fills=len([e for e in expenses if e.liters]),
    )


def _avg_price(expenses: list[Expense]) -> Decimal | None:
    with_liters = [e for e in expenses if e.liters]
    if not with_liters:
        return None
    total_cost = sum((e.amount for e in with_liters), ZERO)
    total_liters = sum((e.liters or ZERO for e in with_liters), ZERO)
    if total_liters <= 0:
        return None
    return (total_cost / total_liters).quantize(Decimal("0.01"))


# --- Месячный отчёт ----------------------------------------------------------


async def build_month_report(
    repo: Repo,
    chat_id: int,
    year: int,
    month: int,
    tz_name: str,
    *,
    avatars: dict[int, bytes] | None = None,
) -> MonthReport:
    start, end = timeutil.month_range(year, month, tz_name)
    expenses = await repo.expenses_between(chat_id, start, end)

    prev_year, prev_month = timeutil.prev_month(year, month)
    prev_start, prev_end = timeutil.month_range(prev_year, prev_month, tz_name)
    prev_rows = await repo.totals_by_person(chat_id, prev_start, prev_end)

    people = _people_from_expenses(expenses)
    _apply_ranks(people)
    _apply_previous_ranks(people, prev_rows)

    names = await repo.person_names(chat_id)
    for person in people:
        person.name = names.get(person.tg_id, person.name)
        if avatars:
            person.avatar = avatars.get(person.tg_id)

    top_expenses = sorted(expenses, key=lambda e: e.amount, reverse=True)[:3]
    black_swan = _black_swan(top_expenses, expenses)
    assign_titles(people, black_swan)

    by_category: dict[Category, Decimal] = defaultdict(lambda: ZERO)
    for expense in expenses:
        by_category[expense.category] += expense.amount

    total = sum((e.amount for e in expenses), ZERO)
    prev_total = sum((row["total"] for row in prev_rows), ZERO)

    return MonthReport(
        chat_id=chat_id,
        year=year,
        month=month,
        people=people,
        top_expenses=top_expenses,
        total=total,
        prev_total=prev_total,
        by_category=dict(by_category),
        fun_fact=_fun_fact(expenses, people, total, by_category, year, month, chat_id),
    )


def _people_from_expenses(expenses: list[Expense]) -> list[PersonMonth]:
    grouped: dict[int, PersonMonth] = {}
    for expense in expenses:
        person = grouped.get(expense.author_id)
        if person is None:
            person = PersonMonth(
                tg_id=expense.author_id,
                name=expense.author_name,
                total=ZERO,
                by_category={},
                count=0,
            )
            grouped[expense.author_id] = person
        person.total += expense.amount
        person.count += 1
        person.by_category[expense.category] = (
            person.by_category.get(expense.category, ZERO) + expense.amount
        )
    return list(grouped.values())


def _apply_ranks(people: list[PersonMonth]) -> None:
    people.sort(key=lambda p: (-p.total, p.name.lower()))
    for position, person in enumerate(people, start=1):
        person.rank = position


def _apply_previous_ranks(people: list[PersonMonth], prev_rows: list[dict]) -> None:
    totals: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for row in prev_rows:
        totals[row["author_id"]] += row["total"]
    ordered = sorted(totals.items(), key=lambda item: -item[1])
    ranks = {tg_id: position for position, (tg_id, _) in enumerate(ordered, start=1)}
    for person in people:
        person.prev_rank = ranks.get(person.tg_id)


def _black_swan(top: list[Expense], expenses: list[Expense]) -> int | None:
    """Автор траты, которая сильно выбивается из общего ряда."""
    if not top or len(expenses) < 3:
        return None
    biggest = top[0]
    rest = [e.amount for e in expenses if e is not biggest]
    if not rest:
        return None
    average = sum(rest, ZERO) / len(rest)
    return biggest.author_id if biggest.amount >= average * 3 else None


# --- Прикольный факт под картинкой ------------------------------------------


def _fun_fact(
    expenses: list[Expense],
    people: list[PersonMonth],
    total: Decimal,
    by_category: dict[Category, Decimal],
    year: int,
    month: int,
    chat_id: int,
) -> str | None:
    if not expenses:
        return None

    facts: list[str] = []
    count = len(expenses)
    average = total / count

    facts.append(
        f"{with_plural(count, 'трата', 'траты', 'трат')} за месяц, "
        f"средний чек {money(average)}"
    )

    if people and total > 0:
        leader = people[0]
        share = leader.total / total * 100
        if share >= 40:
            facts.append(
                f"{leader.name} в одиночку сжёг {share:.0f}% бюджета чата"
            )

    fuel_total = by_category.get(Category.FUEL, ZERO)
    if fuel_total > 0 and total > 0:
        share = fuel_total / total * 100
        facts.append(f"На бензин ушло {share:.0f}% всех денег — {money(fuel_total)}")

    washes = [e for e in expenses if e.category == Category.OTHER and e.amount <= 2000]
    if washes and total > 0:
        wash_price = sum((e.amount for e in washes), ZERO) / len(washes)
        if wash_price > 0:
            times = int(total / wash_price)
            facts.append(
                f"На эти деньги можно было {times} "
                f"{plural(times, 'раз', 'раза', 'раз')} помыть машину"
            )

    liters = sum((e.liters or ZERO for e in expenses if e.liters), ZERO)
    if liters > 0:
        facts.append(
            f"Залито {liters:.0f} литров — это примерно "
            f"{int(liters / 50)} {plural(int(liters / 50), 'бак', 'бака', 'баков')}"
        )

    by_day: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for expense in expenses:
        by_day[expense.spent_at.day] += expense.amount
    if by_day:
        worst_day, worst_sum = max(by_day.items(), key=lambda item: item[1])
        if worst_sum >= average * 3:
            facts.append(
                f"Самый дорогой день — {worst_day}-е, {money(worst_sum)} за сутки"
            )

    # Выбор детерминирован: одинаковый месяц даёт одинаковый факт
    return facts[hash((chat_id, year, month)) % len(facts)]


# --- Личная статистика -------------------------------------------------------


@dataclass(slots=True)
class PersonalReport:
    tg_id: int
    name: str
    year: int
    month: int
    total: Decimal
    by_category: dict[Category, Decimal]
    count: int
    rank: int
    people_count: int
    chat_average: Decimal
    forecast: Decimal
    history: list[tuple[int, int, Decimal]] = field(default_factory=list)
    days_since_last: int | None = None
    fuel: FuelStats | None = None
    biggest: Expense | None = None
    all_time: Decimal = ZERO


async def build_personal_report(
    repo: Repo,
    chat_id: int,
    tg_id: int,
    name: str,
    year: int,
    month: int,
    tz_name: str,
    *,
    history_months: int = 6,
) -> PersonalReport:
    start, end = timeutil.month_range(year, month, tz_name)
    history_start_year, history_start_month = timeutil.add_months(
        year, month, -(history_months - 1)
    )
    history_start, _ = timeutil.month_range(history_start_year, history_start_month, tz_name)

    mine = await repo.expenses_between(chat_id, history_start, end, author_id=tg_id)
    current = [e for e in mine if start <= e.spent_at < end]

    all_month = await repo.expenses_between(chat_id, start, end)
    people = _people_from_expenses(all_month)
    _apply_ranks(people)
    rank = next((p.rank for p in people if p.tg_id == tg_id), len(people) + 1)
    chat_average = (
        sum((p.total for p in people), ZERO) / len(people) if people else ZERO
    )

    by_category: dict[Category, Decimal] = defaultdict(lambda: ZERO)
    for expense in current:
        by_category[expense.category] += expense.amount
    total = sum((e.amount for e in current), ZERO)

    history: list[tuple[int, int, Decimal]] = []
    for offset in range(-(history_months - 1), 1):
        hist_year, hist_month = timeutil.add_months(year, month, offset)
        hist_start, hist_end = timeutil.month_range(hist_year, hist_month, tz_name)
        bucket = sum(
            (e.amount for e in mine if hist_start <= e.spent_at < hist_end), ZERO
        )
        history.append((hist_year, hist_month, bucket))

    elapsed = timeutil.days_elapsed(year, month, tz_name)
    in_month = timeutil.days_in_month(year, month)
    forecast = (total / elapsed * in_month).quantize(Decimal("1")) if elapsed else total

    last_at = await repo.last_expense_at(chat_id, tg_id)
    days_since = None
    if last_at is not None:
        days_since = (timeutil.now(tz_name) - last_at.astimezone(timeutil.tz(tz_name))).days

    fuel_series = await repo.fuel_series(chat_id, tg_id)
    biggest = max(mine, key=lambda e: e.amount, default=None)

    return PersonalReport(
        tg_id=tg_id,
        name=name,
        year=year,
        month=month,
        total=total,
        by_category=dict(by_category),
        count=len(current),
        rank=rank,
        people_count=len(people),
        chat_average=chat_average,
        forecast=forecast,
        history=history,
        days_since_last=days_since,
        fuel=fuel_stats(fuel_series),
        biggest=biggest,
        all_time=sum((e.amount for e in mine), ZERO),
    )


def month_delta_text(total: Decimal, previous: Decimal) -> str | None:
    """«+18% к прошлому месяцу» — или None, если сравнивать не с чем."""
    if previous <= 0:
        return None
    change = (total - previous) / previous * 100
    if abs(change) < 1:
        return "как в прошлом месяце"
    sign = "+" if change > 0 else "−"
    return f"{sign}{abs(change):.0f}% к прошлому месяцу"


def spent_at_of(expense: Expense, tz_name: str) -> datetime:
    return expense.spent_at.astimezone(timeutil.tz(tz_name))
