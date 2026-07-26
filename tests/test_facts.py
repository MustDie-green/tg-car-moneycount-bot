"""Факты под картинкой: арифметика, склонения и детерминированность выбора."""

from datetime import datetime, timezone
from decimal import Decimal

from carbot import reporting
from carbot.models import Category, Expense, PersonMonth

CHAT = -1001234567890
TZ = "Europe/Moscow"


def expense(day, amount, category, note="", *, liters=None, odometer=None, hour=12):
    return Expense(
        chat_id=CHAT,
        author_id=111,
        author_name="Валера",
        message_id=day,
        amount=Decimal(amount),
        category=category,
        note=note,
        spent_at=datetime(2026, 6, day, hour, 0, tzinfo=timezone.utc),
        liters=Decimal(liters) if liters else None,
        odometer=odometer,
    )


def context(expenses, *, days=30, people=None):
    by_category: dict[Category, Decimal] = {}
    for item in expenses:
        by_category[item.category] = (
            by_category.get(item.category, Decimal(0)) + item.amount
        )
    total = sum((item.amount for item in expenses), Decimal(0))
    return reporting._FactContext(
        expenses=expenses,
        people=people or [],
        total=total,
        by_category=by_category,
        count=len(expenses),
        average=total / len(expenses) if expenses else Decimal(0),
        days=days,
        tz_name=TZ,
        fuel=reporting.fuel_stats(expenses),
    )


# --- «На эти деньги можно было бы...» ----------------------------------------


def test_distance_uses_real_consumption():
    """Два залива с пробегом дают реальные л/100км, а не запасные 10."""
    expenses = [
        expense(2, 2400, Category.FUEL, liters=40, odometer=120_000),
        expense(9, 2400, Category.FUEL, liters=40, odometer=120_500),
    ]
    # 40 л на 500 км = 8 л/100км, цена 60 ₽/л, бюджет 4800 ₽
    # 4800 / 60 = 80 л, 80 л при 8 л/100км = 1000 км
    assert "1 000 км" in reporting._fact_distance(context(expenses))


def test_distance_falls_back_without_fuel_data():
    """Без литров берём запасные 60 ₽/л и 10 л/100км: 12000 ₽ → 2000 км."""
    expenses = [expense(3, 12_000, Category.FUEL)]
    assert "2 000 км" in reporting._fact_distance(context(expenses))


def test_distance_stays_quiet_on_small_sums():
    assert reporting._fact_distance(context([expense(3, 300, Category.OTHER)])) is None


def test_route_hint_matches_only_when_it_lands_well():
    assert reporting._route_hint(1650) == "доехать от Москвы до Сочи"
    assert reporting._route_hint(3300) == "смотать из Москвы в Сочи и обратно"
    assert reporting._route_hint(40_100) == "объехать Землю по экватору"
    # Между ориентирами ничего не выдумываем
    assert reporting._route_hint(2600) is None


def test_shawarma_counts_portions():
    fact = reporting._fact_shawarma(context([expense(3, 19_600, Category.FUEL)]))
    assert fact == "На эти деньги вышло бы 56 порций шаурмы"


def test_oil_changes_and_tires_have_thresholds():
    cheap = context([expense(3, 5_000, Category.SERVICE)])
    assert reporting._fact_oil_changes(cheap) is None  # меньше двух замен
    assert reporting._fact_tires(cheap) is None

    rich = context([expense(3, 92_000, Category.SERVICE)])
    assert reporting._fact_oil_changes(rich) == "Этого хватило бы на 23 замены масла"
    assert reporting._fact_tires(rich) == "Это 3 комплекта зимней резины"


# --- Ритм трат ---------------------------------------------------------------


def test_per_day_and_year_pace():
    expenses = [expense(day, 1_000, Category.FUEL) for day in (1, 2, 3)]
    ctx = context(expenses, days=30)
    assert reporting._fact_per_day(ctx) == "Машины обходятся чату в 100 ₽ в день"
    assert reporting._fact_year_pace(ctx) == "Таким темпом за год набежит 36 500 ₽"


def test_quiet_days_counts_only_elapsed_days():
    """Считаем от прошедших дней, а не от длины месяца."""
    expenses = [expense(day, 1_000, Category.FUEL) for day in (1, 2)]
    assert reporting._fact_quiet_days(context(expenses, days=10)) == (
        "8 дней в этом месяце — без единой траты"
    )
    # Тратили каждый день из прошедших — тихих нет
    assert reporting._fact_quiet_days(context(expenses, days=2)) is None


def test_day_facts_use_chat_timezone():
    """22:00 UTC — это уже следующий день по Москве."""
    late = expense(4, 5_000, Category.SERVICE, hour=22)
    quiet = [expense(day, 100, Category.OTHER) for day in (1, 2, 3)]
    ctx = context([late, *quiet])
    assert ctx.local_day(late.spent_at) == 5
    assert "5-е" in reporting._fact_worst_day(ctx)


# --- Соотношение категорий ---------------------------------------------------


def test_category_ratio_both_directions():
    service_heavy = context(
        [expense(3, 30_000, Category.SERVICE), expense(4, 5_000, Category.FUEL)]
    )
    assert reporting._fact_category_ratio(service_heavy) == (
        "Ремонт дороже бензина в 6 раз — 30 000 ₽ против 5 000 ₽"
    )

    fuel_heavy = context(
        [expense(3, 2_000, Category.SERVICE), expense(4, 5_000, Category.FUEL)]
    )
    assert reporting._fact_category_ratio(fuel_heavy) == (
        "Бензин дороже ремонта в 2,5 раза — 5 000 ₽ против 2 000 ₽"
    )


def test_category_ratio_quiet_when_close():
    balanced = context(
        [expense(3, 5_000, Category.SERVICE), expense(4, 4_000, Category.FUEL)]
    )
    assert reporting._fact_category_ratio(balanced) is None


def test_times_wording():
    assert reporting._times(Decimal(2)) == "2 раза"
    assert reporting._times(Decimal(5)) == "5 раз"
    assert reporting._times(Decimal("2.53")) == "2,5 раза"


# --- Выбор факта -------------------------------------------------------------


def test_fun_fact_is_deterministic_and_varies_by_month():
    expenses = [
        expense(2, 2_500, Category.FUEL, liters=42, odometer=120_000),
        expense(9, 8_000, Category.SERVICE),
        expense(16, 500, Category.OTHER),
    ]
    people = [
        PersonMonth(tg_id=111, name="Валера", total=Decimal(11_000),
                    by_category={}, count=3)
    ]
    ctx_args = (expenses, people, Decimal(11_000),
                {Category.FUEL: Decimal(2_500), Category.SERVICE: Decimal(8_000)})

    first = reporting._fun_fact(*ctx_args, 2026, 6, CHAT, 30, TZ)
    again = reporting._fun_fact(*ctx_args, 2026, 6, CHAT, 30, TZ)
    assert first and first == again

    months = {
        reporting._fun_fact(*ctx_args, 2026, month, CHAT, 30, TZ)
        for month in range(1, 13)
    }
    assert len(months) > 1  # разные месяцы не показывают один и тот же факт


def test_fun_fact_empty_month():
    assert reporting._fun_fact([], [], Decimal(0), {}, 2026, 6, CHAT, 30, TZ) is None
