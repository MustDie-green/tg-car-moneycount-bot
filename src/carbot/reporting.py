"""Сборка отчётов из сырых трат: ранги, титулы, расход топлива, прогнозы."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from . import garage, mileage, timeutil
from .cost import CostPerKm
from .db import Repo
from .formatting import decimal_str, money, plural, with_plural
from .models import Category, Expense, MonthReport, PersonMonth, ServiceHint
from .service import ServiceDue
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


# Окно, по которому считается расход: свежее важнее, но короткий отрезок
# слишком шумный из-за неполных заправок
FUEL_WINDOW_DAYS = 180
MIN_FUEL_DISTANCE = 300


def fuel_stats(expenses: list[Expense]) -> FuelStats | None:
    """Расход по всем заправкам между первым и последним надёжным замером.

    Метод полного бака по соседним заправкам не годится: заливают по 20–40
    литров, а пробег пишут не всегда. Пропустил пробег на одной заправке — и
    её литры выпадали, а километры следующего отрезка оставались, отсюда
    5 л/100 у Макана.

    Поэтому берём две крайние точки с надёжным пробегом и делим на километры
    между ними все литры, залитые после первой из них, — с пробегом и без.
    Литры первой заправки не считаются: сколько было в баке до неё, неизвестно.
    Опечатки в одометре отсекает mileage.collect.
    """
    fills = sorted(
        (e for e in expenses if e.category == Category.FUEL and e.liters),
        key=lambda e: e.spent_at,
    )
    # Замер помним вместе с позицией заправки: границы окна — это позиции,
    # а не время, иначе две заправки с одной меткой времени теряются
    position: dict[int, int] = {}
    candidates: list[mileage.Reading] = []
    for index, fill in enumerate(fills):
        if fill.odometer:
            reading = mileage.Reading(at=fill.spent_at, km=fill.odometer)
            position[id(reading)] = index
            candidates.append(reading)
    readings = mileage.collect(candidates)
    if len(readings) < 2:
        return _price_only(fills)

    last = readings[-1]
    horizon = last.at - timedelta(days=FUEL_WINDOW_DAYS)
    recent = [r for r in readings if r.at >= horizon]
    first = recent[0] if len(recent) >= 2 else readings[0]

    distance = last.km - first.km
    inside = fills[position[id(first)] + 1 : position[id(last)] + 1]
    liters = sum((e.liters or ZERO for e in inside), ZERO)
    cost = sum((e.amount for e in inside), ZERO)
    if distance < MIN_FUEL_DISTANCE or liters <= 0:
        return _price_only(fills)

    per_100 = liters / Decimal(distance) * 100
    if not Decimal(2) <= per_100 <= Decimal(35):
        return _price_only(fills)

    return FuelStats(
        liters_per_100=per_100.quantize(Decimal("0.1")),
        rub_per_km=(cost / Decimal(distance)).quantize(Decimal("0.01")),
        price_per_liter=_avg_price(fills),
        distance=distance,
        fills=len(inside) + 1,
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

    people = _people_from_expenses(expenses, tz_name)
    _apply_ranks(people)
    _apply_previous_ranks(people, prev_rows)

    names = await repo.person_names(chat_id)
    for person in people:
        person.name = names.get(person.tg_id, person.name)
        if avatars:
            person.avatar = avatars.get(person.tg_id)

    top_expenses = sorted(expenses, key=lambda e: e.amount, reverse=True)[:3]

    by_category: dict[Category, Decimal] = defaultdict(lambda: ZERO)
    for expense in expenses:
        by_category[expense.category] += expense.amount

    total = sum((e.amount for e in expenses), ZERO)
    prev_total = sum((row["total"] for row in prev_rows), ZERO)

    garages = await garage.load_all(
        repo, chat_id, start=start, end=end, now=timeutil.now(tz_name)
    )
    for person in people:
        found = garages.get(person.tg_id)
        if found is None:
            continue
        person.car_title = found.title
        person.odometer = found.odometer_text
        person.month_km = found.month_km
        if found.cost:
            person.rub_per_km = found.cost.total
            person.cost_low_data = found.cost.low_data

    for person in people:
        fuel = fuel_stats(await repo.fuel_series(chat_id, person.tg_id))
        person.liters_per_100 = fuel.liters_per_100 if fuel else None

    # Звания раздаются последними: им нужны и пробег, и расход
    assign_titles(people, _black_swan(top_expenses, expenses))

    names_by_id = {p.tg_id: p.name for p in people}
    service_soon = [
        ServiceHint(
            person=names_by_id.get(item.owner_id, "—"),
            car=item.title,
            title=due.plan.title,
            reason=due.reason(),
            overdue=due.overdue,
        )
        for item, due in garage.soon(garages)
    ]
    driven = [g.month_km for g in garages.values() if g.month_km]

    return MonthReport(
        chat_id=chat_id,
        year=year,
        month=month,
        people=people,
        top_expenses=top_expenses,
        total=total,
        prev_total=prev_total,
        by_category=dict(by_category),
        fun_fact=_fun_fact(
            expenses,
            people,
            total,
            by_category,
            year,
            month,
            chat_id,
            timeutil.days_elapsed(year, month, tz_name),
            tz_name,
        ),
        service_soon=service_soon,
        total_km=sum(driven) if driven else None,
    )


# Траты с полуночи до этого часа по времени чата — для «Ночного гонщика»
NIGHT_UNTIL_HOUR = 5


def _people_from_expenses(
    expenses: list[Expense], tz_name: str | None = None
) -> list[PersonMonth]:
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
        if expense.category == Category.FUEL:
            person.fuel_count += 1
        if tz_name and spent_at_of(expense, tz_name).hour < NIGHT_UNTIL_HOUR:
            person.night_count += 1
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


# Ориентировочные цены для фактов в жанре «на эти деньги можно было бы».
# Точность тут не нужна — это подкол, а не смета, но и мимо порядка быть не
# должно. Цифры на июль 2026 и посчитаны под конкретный парк чата:
# пять RAV4, одна Audi Q3, один Porsche Macan — то есть средневзвешенно
# «пять народных кроссоверов и два дорогих». Поменяется парк — правьте здесь.

SHAWARMA_PRICE = Decimal(320)  # классическая, Москва

# RAV4 ~7 500, Q3 ~10 000, Macan ~17 000 (семь литров одобренного Porsche масла)
OIL_CHANGE_PRICE = Decimal(9_000)

# Комплект из четырёх: RAV4 на R17 ~37 000, на R19 ~59 000,
# Q3 ~50 000, Macan на 265/45 R20 ~98 000
WINTER_TIRES_PRICE = Decimal(55_000)

# Баки: RAV4 55 л, Q3 63 л, Macan 75 л
TANK_LITERS = Decimal(60)

# Запасные значения, пока в чате никто не писал литры и пробег.
# Все три модели ездят на 95-м, Macan просит 98-й.
FALLBACK_FUEL_PRICE = Decimal(78)  # ₽ за литр АИ-95, Росстат, июль 2026
FALLBACK_CONSUMPTION = Decimal("9.6")  # л на 100 км: RAV4 ~9.5, Q3 ~8.5, Macan ~11.5

# Ориентиры для «уехать N км»: расстояние, маршрут, маршрут туда-обратно
_ROUTES: tuple[tuple[int, str, str], ...] = (
    (40_075, "объехать Землю по экватору", "дважды объехать Землю по экватору"),
    (9_000, "доехать от Москвы до Владивостока", "смотать из Москвы во Владивосток и обратно"),
    (5_200, "доехать от Москвы до Байкала", "смотать из Москвы на Байкал и обратно"),
    (1_900, "доехать от Москвы до Мурманска", "смотать из Москвы в Мурманск и обратно"),
    (1_620, "доехать от Москвы до Сочи", "смотать из Москвы в Сочи и обратно"),
    (820, "доехать от Москвы до Казани", "смотать из Москвы в Казань и обратно"),
    (710, "доехать от Москвы до Питера", "смотать из Москвы в Питер и обратно"),
    (420, "доехать от Москвы до Нижнего", "смотать из Москвы в Нижний и обратно"),
)


@dataclass(slots=True)
class _FactContext:
    """Всё, что нужно фактам, посчитано один раз на всех."""

    expenses: list[Expense]
    people: list[PersonMonth]
    total: Decimal
    by_category: dict[Category, Decimal]
    count: int
    average: Decimal
    days: int  # сколько дней месяца уже прошло
    tz_name: str
    fuel: FuelStats | None

    def local_day(self, moment: datetime) -> int:
        """День месяца по часам чата: в базе время лежит в UTC."""
        return moment.astimezone(timeutil.tz(self.tz_name)).day


def _spaced(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _times(value: Decimal) -> str:
    """2 → «2 раза», 2.5 → «2,5 раза», 5 → «5 раз»."""
    rounded = value.quantize(Decimal("0.1"))
    if rounded == rounded.to_integral_value():
        count = int(rounded)
        return f"{count} {plural(count, 'раз', 'раза', 'раз')}"
    return f"{decimal_str(rounded)} раза"


def _fact_average(ctx: _FactContext) -> str | None:
    return (
        f"{with_plural(ctx.count, 'трата', 'траты', 'трат')} за месяц, "
        f"средний чек {money(ctx.average)}"
    )


def _fact_leader_share(ctx: _FactContext) -> str | None:
    if not ctx.people or ctx.total <= 0:
        return None
    leader = ctx.people[0]
    share = leader.total / ctx.total * 100
    if share < 40:
        return None
    return f"{leader.name} в одиночку сжёг {share:.0f}% бюджета чата"


def _fact_fuel_share(ctx: _FactContext) -> str | None:
    fuel_total = ctx.by_category.get(Category.FUEL, ZERO)
    if fuel_total <= 0 or ctx.total <= 0:
        return None
    share = fuel_total / ctx.total * 100
    return f"На бензин ушло {share:.0f}% всех денег — {money(fuel_total)}"


def _fact_washes(ctx: _FactContext) -> str | None:
    """Цену мойки берём из самого чата — у всех она своя."""
    washes = [
        e for e in ctx.expenses if e.category == Category.CARE and e.amount <= 2000
    ]
    if not washes:
        return None
    price = sum((e.amount for e in washes), ZERO) / len(washes)
    if price <= 0:
        return None
    times = int(ctx.total / price)
    if times < 2:
        return None
    return (
        f"На эти деньги можно было {times} "
        f"{plural(times, 'раз', 'раза', 'раз')} помыть машину"
    )


def _fact_liters(ctx: _FactContext) -> str | None:
    liters = sum((e.liters or ZERO for e in ctx.expenses if e.liters), ZERO)
    if liters <= 0:
        return None
    tanks = int(liters / TANK_LITERS)
    if tanks < 1:
        return None
    return (
        f"Залито {liters:.0f} литров — это примерно "
        f"{with_plural(tanks, 'бак', 'бака', 'баков')}"
    )


def _fact_worst_day(ctx: _FactContext) -> str | None:
    by_day: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for expense in ctx.expenses:
        by_day[ctx.local_day(expense.spent_at)] += expense.amount
    if not by_day:
        return None
    worst_day, worst_sum = max(by_day.items(), key=lambda item: item[1])
    if worst_sum < ctx.average * 3:
        return None
    return f"Самый дорогой день — {worst_day}-е, {money(worst_sum)} за сутки"


def _fact_distance(ctx: _FactContext) -> str | None:
    """Сколько бы проехали, спустив весь бюджет месяца на один бензин."""
    price = FALLBACK_FUEL_PRICE
    consumption = FALLBACK_CONSUMPTION
    if ctx.fuel:
        price = ctx.fuel.price_per_liter or price
        consumption = ctx.fuel.liters_per_100 or consumption
    if price <= 0 or consumption <= 0:
        return None
    km = int(ctx.total / price / consumption * 100)
    if km < 100:
        return None
    text = f"Этих денег хватило бы на {_spaced(km)} км пути"
    hint = _route_hint(km)
    return f"{text} — это как {hint}" if hint else text


def _route_hint(km: int) -> str | None:
    """Ориентир подставляем, только если он ложится ровно — иначе молчим.

    Берём не первый подходящий, а самый точный: 1650 км — это скорее Сочи
    (промах 2%), чем Мурманск (промах 13%), хотя формально подходят оба.
    Поездка в один конец всегда выигрывает у «туда и обратно»: те же 1650 км
    почти идеально ложатся и на Казань с возвратом, но до Сочи звучит живее.
    """
    for low, high, target, index in ((0.85, 1.2, 1.0, 1), (1.85, 2.2, 2.0, 2)):
        best_miss: float | None = None
        best_phrase: str | None = None

        for route in _ROUTES:
            ratio = km / route[0]
            if not low <= ratio <= high:
                continue
            miss = abs(ratio - target)
            if best_miss is None or miss < best_miss:
                best_miss, best_phrase = miss, route[index]

        if best_phrase:
            return best_phrase

    return None


def _fact_shawarma(ctx: _FactContext) -> str | None:
    count = int(ctx.total / SHAWARMA_PRICE)
    if count < 5:
        return None
    # «56 шаурмы» звучит криво в любом падеже, поэтому считаем порциями
    return (
        f"На эти деньги вышло бы "
        f"{with_plural(count, 'порция', 'порции', 'порций')} шаурмы"
    )


def _fact_oil_changes(ctx: _FactContext) -> str | None:
    count = int(ctx.total / OIL_CHANGE_PRICE)
    if count < 2:
        return None
    return f"Этого хватило бы на {with_plural(count, 'замену', 'замены', 'замен')} масла"


def _fact_tires(ctx: _FactContext) -> str | None:
    count = int(ctx.total / WINTER_TIRES_PRICE)
    if count < 1:
        return None
    return f"Это {with_plural(count, 'комплект', 'комплекта', 'комплектов')} зимней резины"


def _fact_per_day(ctx: _FactContext) -> str | None:
    if ctx.days <= 0 or ctx.total <= 0:
        return None
    return f"Машины обходятся чату в {money(ctx.total / ctx.days)} в день"


def _fact_year_pace(ctx: _FactContext) -> str | None:
    if ctx.days <= 0 or ctx.total <= 0:
        return None
    return f"Таким темпом за год набежит {money(ctx.total / ctx.days * 365)}"


def _fact_quiet_days(ctx: _FactContext) -> str | None:
    spent_days = {ctx.local_day(e.spent_at) for e in ctx.expenses}
    quiet = ctx.days - len(spent_days)
    if quiet < 3:
        return None
    return f"{with_plural(quiet, 'день', 'дня', 'дней')} в этом месяце — без единой траты"


def _fact_category_ratio(ctx: _FactContext) -> str | None:
    fuel = ctx.by_category.get(Category.FUEL, ZERO)
    service = ctx.by_category.get(Category.SERVICE, ZERO)
    if fuel <= 0 or service <= 0:
        return None
    if service >= fuel * 2:
        return (
            f"Ремонт дороже бензина в {_times(service / fuel)} — "
            f"{money(service)} против {money(fuel)}"
        )
    if fuel >= service * 2:
        return (
            f"Бензин дороже ремонта в {_times(fuel / service)} — "
            f"{money(fuel)} против {money(service)}"
        )
    return None


_FACT_BUILDERS = (
    _fact_average,
    _fact_leader_share,
    _fact_fuel_share,
    _fact_washes,
    _fact_liters,
    _fact_worst_day,
    _fact_distance,
    _fact_shawarma,
    _fact_oil_changes,
    _fact_tires,
    _fact_per_day,
    _fact_year_pace,
    _fact_quiet_days,
    _fact_category_ratio,
)


def _fun_fact(
    expenses: list[Expense],
    people: list[PersonMonth],
    total: Decimal,
    by_category: dict[Category, Decimal],
    year: int,
    month: int,
    chat_id: int,
    days: int,
    tz_name: str,
) -> str | None:
    if not expenses:
        return None

    context = _FactContext(
        expenses=expenses,
        people=people,
        total=total,
        by_category=by_category,
        count=len(expenses),
        average=total / len(expenses),
        days=days,
        tz_name=tz_name,
        fuel=fuel_stats(expenses),
    )
    facts = [text for builder in _FACT_BUILDERS if (text := builder(context))]
    if not facts:
        return None

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
    car_title: str | None = None
    odometer: str | None = None
    month_km: int | None = None
    cost: CostPerKm | None = None
    due: list[ServiceDue] = field(default_factory=list)


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

    mine_garage = await garage.load_one(
        repo, chat_id, tg_id, start=start, end=end, now=timeutil.now(tz_name)
    )

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
        car_title=mine_garage.title if mine_garage else None,
        odometer=mine_garage.odometer_text if mine_garage else None,
        month_km=mine_garage.month_km if mine_garage else None,
        cost=mine_garage.cost if mine_garage else None,
        due=mine_garage.due[:3] if mine_garage else [],
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
