"""Рисует картинки на выдуманных данных — чтобы крутить дизайн без телеграма.

Запуск: python scripts/preview.py [папка]
Кладёт month.png/month.svg и personal.png/personal.svg рядом друг с другом.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from carbot.config import settings  # noqa: E402
from carbot.cost import CostPerKm  # noqa: E402
from carbot.models import (  # noqa: E402
    Category, Expense, MonthReport, PersonMonth, ServiceHint,
)
from carbot.service import ServiceDue, ServicePlan  # noqa: E402
from carbot.render import dump_svg, load_fonts, render_month, render_personal, to_png  # noqa: E402
from carbot.reporting import FuelStats, PersonalReport  # noqa: E402
from carbot.titles import assign_titles  # noqa: E402

TZ = "Europe/Moscow"
NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def person(tg_id, name, fuel, service, other, count, prev_rank, car=None):
    by_category = {}
    if fuel:
        by_category[Category.FUEL] = Decimal(fuel)
    if service:
        by_category[Category.SERVICE] = Decimal(service)
    if other:
        by_category[Category.OTHER] = Decimal(other)
    car_title, odometer, month_km, per_km, low_data = car or (
        None, None, None, None, False
    )
    return PersonMonth(
        tg_id=tg_id,
        name=name,
        total=Decimal(fuel + service + other),
        by_category=by_category,
        count=count,
        prev_rank=prev_rank,
        car_title=car_title,
        odometer=odometer,
        month_km=month_km,
        rub_per_km=Decimal(per_km) if per_km else None,
        cost_low_data=low_data,
    )


def expense(author_id, name, amount, category, note, day):
    return Expense(
        chat_id=-100,
        author_id=author_id,
        author_name=name,
        message_id=day,
        amount=Decimal(amount),
        category=category,
        note=note,
        spent_at=NOW - timedelta(days=30 - day),
    )


def month_report() -> MonthReport:
    people = [
        person(1, "Антон", 12400, 48000, 3200, 11, 3,
               car=("Porsche Macan", "~84 300", 1420, "31.2", False)),
        person(2, "Валера", 18600, 4500, 6800, 14, 1,
               car=("Toyota RAV4", "212 480", 2180, "12.4", False)),
        person(3, "Сергей Кузнецов", 9800, 12000, 1500, 7, 2,
               car=("Audi Q3", "~96 100", 980, "18.7", False)),
        person(4, "Дима", 14200, 0, 2400, 6, None,
               car=("Toyota RAV4", "58 900", None, "9.1", True)),
        person(5, "Костя", 6100, 1800, 900, 4, 4,
               car=("Toyota RAV4", None, None, None, False)),
    ]
    people.sort(key=lambda p: -p.total)
    for position, item in enumerate(people, start=1):
        item.rank = position

    top = [
        expense(1, "Антон", 48000, Category.SERVICE, "замена сцепления и выжимного", 14),
        expense(3, "Сергей Кузнецов", 12000, Category.SERVICE, "новая резина на лето, четыре колеса", 6),
        expense(2, "Валера", 6800, Category.OTHER, "детейлинг и химчистка салона", 21),
    ]
    assign_titles(people, black_swan_id=1)

    total = sum((p.total for p in people), Decimal(0))
    by_category: dict[Category, Decimal] = {}
    for item in people:
        for category, amount in item.by_category.items():
            by_category[category] = by_category.get(category, Decimal(0)) + amount

    return MonthReport(
        chat_id=-100,
        year=2026,
        month=7,
        people=people,
        top_expenses=top,
        total=total,
        prev_total=total * Decimal("0.78"),
        by_category=by_category,
        # Самый длинный из фактов — на нём и проверяем, что подвал не разъезжается
        fun_fact=(
            "Этих денег хватило бы на 4 014 км пути — "
            "это как смотать из Москвы в Мурманск и обратно"
        ),
        service_soon=[
            ServiceHint("Антон", "Porsche Macan", "Масло двигателя",
                        "просрочено на 1 200 км", overdue=True),
            ServiceHint("Валера", "Toyota RAV4", "Тормозная жидкость", "через 12 дн."),
            ServiceHint("Сергей Кузнецов", "Audi Q3", "Салонный фильтр", "через ~380 км"),
            ServiceHint("Дима", "Toyota RAV4", "Страховка", "через 9 дн."),
        ],
        total_km=6_890,
    )


def personal_report() -> PersonalReport:
    return PersonalReport(
        tg_id=1,
        name="Антон",
        year=2026,
        month=7,
        total=Decimal(63600),
        by_category={
            Category.FUEL: Decimal(12400),
            Category.SERVICE: Decimal(48000),
            Category.OTHER: Decimal(3200),
        },
        count=11,
        rank=1,
        people_count=5,
        chat_average=Decimal(28440),
        forecast=Decimal(95400),
        history=[
            (2026, 2, Decimal(18400)),
            (2026, 3, Decimal(24100)),
            (2026, 4, Decimal(9800)),
            (2026, 5, Decimal(31200)),
            (2026, 6, Decimal(15600)),
            (2026, 7, Decimal(63600)),
        ],
        days_since_last=3,
        fuel=FuelStats(
            liters_per_100=Decimal("8.4"),
            rub_per_km=Decimal("5.12"),
            price_per_liter=Decimal("61.30"),
            distance=4200,
            fills=9,
        ),
        biggest=expense(1, "Антон", 48000, Category.SERVICE, "замена сцепления и выжимного", 14),
        all_time=Decimal(162700),
        car_title="Porsche Macan",
        odometer="~84 300",
        month_km=1420,
        cost=CostPerKm(
            total=Decimal("31.24"),
            fuel=Decimal("5.12"),
            without_outlier=Decimal("9.80"),
            distance=8400,
            days=124,
            readings=11,
            expense_count=23,
            low_data=False,
        ),
        due=[
            ServiceDue(
                plan=ServicePlan(
                    chat_id=-100, owner_id=1, car_slug="macan",
                    car_title="Porsche Macan", kind="oil_engine",
                    interval_km=10_000, last_km=75_500,
                ),
                km_left=-1_200, days_left=None, estimated=True,
            ),
            ServiceDue(
                plan=ServicePlan(
                    chat_id=-100, owner_id=1, car_slug="macan",
                    car_title="Porsche Macan", kind="brake_fluid",
                    interval_days=730,
                ),
                km_left=None, days_left=41, estimated=False,
            ),
            ServiceDue(
                plan=ServicePlan(
                    chat_id=-100, owner_id=1, car_slug="macan",
                    car_title="Porsche Macan", kind="filter_cabin",
                    interval_km=15_000, last_km=72_000,
                ),
                km_left=2_700, days_left=None, estimated=True,
            ),
        ],
    )


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "preview")
    out.mkdir(parents=True, exist_ok=True)
    fonts = load_fonts(str(settings.fonts_dir))

    pairs = (
        ("month", render_month(month_report(), fonts, tz_name=TZ)),
        ("personal", render_personal(personal_report(), fonts, tz_name=TZ)),
    )
    for name, markup in pairs:
        dump_svg(markup, out / f"{name}.svg")
        png = to_png(markup, fonts.files)
        (out / f"{name}.png").write_bytes(png)
        print(f"{name}: {len(png) // 1024} КБ → {out / (name + '.png')}")


if __name__ == "__main__":
    main()
