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
from carbot.models import Category, Expense, MonthReport, PersonMonth  # noqa: E402
from carbot.render import dump_svg, load_fonts, render_month, render_personal, to_png  # noqa: E402
from carbot.reporting import FuelStats, PersonalReport  # noqa: E402
from carbot.titles import assign_titles  # noqa: E402

TZ = "Europe/Moscow"
NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def person(tg_id, name, fuel, service, other, count, prev_rank):
    by_category = {}
    if fuel:
        by_category[Category.FUEL] = Decimal(fuel)
    if service:
        by_category[Category.SERVICE] = Decimal(service)
    if other:
        by_category[Category.OTHER] = Decimal(other)
    return PersonMonth(
        tg_id=tg_id,
        name=name,
        total=Decimal(fuel + service + other),
        by_category=by_category,
        count=count,
        prev_rank=prev_rank,
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
        person(1, "Антон", 12400, 48000, 3200, 11, 3),
        person(2, "Валера", 18600, 4500, 6800, 14, 1),
        person(3, "Сергей Кузнецов", 9800, 12000, 1500, 7, 2),
        person(4, "Дима", 14200, 0, 2400, 6, None),
        person(5, "Костя", 6100, 1800, 900, 4, 4),
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
