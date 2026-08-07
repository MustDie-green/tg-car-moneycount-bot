"""Личная карточка по команде /me."""

from __future__ import annotations

from decimal import Decimal

from .. import timeutil
from ..formatting import decimal_str, money, money_short, with_plural
from ..models import Category, CATEGORY_TITLE
from ..reporting import PersonalReport
from . import icons, svg, theme
from .text import FontBook

ZERO = Decimal(0)

HEADER_H = 186
SECTION_H = 46
CHART_H = 216
PILL_H = 88
STAT_H = 96
FOOTER_H = 78


def render_personal(
    report: PersonalReport,
    fonts: FontBook,
    *,
    tz_name: str = "Europe/Moscow",
    currency: str = "₽",
) -> str:
    defs = [
        '<radialGradient id="glow" cx="0.5" cy="0" r="0.9">'
        f'<stop offset="0" stop-color="{theme.BG_TOP}" stop-opacity="0.85"/>'
        f'<stop offset="1" stop-color="{theme.BG}" stop-opacity="0"/>'
        "</radialGradient>"
    ]
    body: list[str] = []

    y = _header(body, report, fonts, currency)
    y = _section(body, y, "ПОСЛЕДНИЕ ПОЛГОДА")
    y = _history_chart(body, report, fonts, y, currency)
    y = _category_strip(body, report, y, currency)
    y = _stats(body, report, fonts, y, currency)
    if report.cost is not None or report.month_km:
        y = _mileage(body, report, y, currency)
    if report.fuel is not None:
        y = _fuel(body, report, y, currency)
    if report.due:
        y = _section(body, y, "СКОРО МЕНЯТЬ")
        y = _service(body, report, fonts, y)
    if report.biggest is not None:
        y = _biggest(body, report, fonts, y, tz_name, currency)

    height = y + theme.PAD
    background = svg.rect(0, 0, theme.WIDTH, height, fill=theme.BG) + svg.rect(
        0, 0, theme.WIDTH, min(height, 380), fill="url(#glow)"
    )
    return svg.document(theme.WIDTH, height, "".join(defs), background + "".join(body))


def _header(body: list[str], report: PersonalReport, fonts: FontBook, currency: str) -> float:
    right = theme.WIDTH - theme.PAD

    body.append(
        svg.text(theme.PAD, 62, "ЛИЧНАЯ КАРТОЧКА", size=15, weight=700, fill=theme.MUTED, tracking=5.5)
    )
    body.append(
        svg.text(
            theme.PAD, 120, fonts.fit(report.name, 44, 560, 900),
            size=44, weight=900, fill=theme.TEXT, tracking=-0.8,
        )
    )
    subtitle = timeutil.month_title(report.year, report.month).title()
    if report.car_title:
        subtitle += f" · {report.car_title}"
    body.append(
        svg.text(
            theme.PAD, 154, fonts.fit(subtitle, 17, 560, 400),
            size=17, weight=400, fill=theme.MUTED,
        )
    )

    body.append(
        svg.text(
            right, 116, money(report.total, currency),
            size=40, weight=900, fill=theme.GOLD, anchor="end",
        )
    )
    place = (
        f"{report.rank}-е место из {report.people_count}"
        if report.people_count
        else "трат пока нет"
    )
    body.append(svg.text(right, 150, place, size=17, weight=600, fill=theme.MUTED, anchor="end"))
    return HEADER_H


def _section(body: list[str], y: float, title: str) -> float:
    body.append(svg.text(theme.PAD, y + 20, title, size=14, weight=700, fill=theme.DIM, tracking=3.5))
    return y + SECTION_H


def _history_chart(
    body: list[str], report: PersonalReport, fonts: FontBook, y: float, currency: str
) -> float:
    width = theme.WIDTH - theme.PAD * 2
    body.append(svg.rect(theme.PAD, y, width, CHART_H, rx=22, fill=theme.CARD))

    history = report.history or [(report.year, report.month, report.total)]
    columns = len(history)
    gap = 18
    inner = width - 48 * 2
    column_w = (inner - gap * (columns - 1)) / columns
    baseline = y + CHART_H - 46
    max_value = max((value for _, _, value in history), default=ZERO) or Decimal(1)

    for position, (hist_year, hist_month, value) in enumerate(history):
        x = theme.PAD + 48 + position * (column_w + gap)
        is_current = (hist_year, hist_month) == (report.year, report.month)
        bar_h = float(Decimal(118) * (value / max_value)) if max_value else 0
        bar_h = max(bar_h, 6 if value > 0 else 3)
        color = theme.GOLD if is_current else theme.CHART_BAR

        body.append(svg.rect(x, baseline - bar_h, column_w, bar_h, rx=10, fill=color))
        if value > 0:
            body.append(
                svg.text(
                    x + column_w / 2, baseline - bar_h - 14, money_short(value, currency),
                    size=15, weight=700,
                    fill=theme.TEXT if is_current else theme.MUTED, anchor="middle",
                )
            )
        body.append(
            svg.text(
                x + column_w / 2, baseline + 28,
                timeutil.MONTHS_NOMINATIVE[hist_month - 1][:3],
                size=14, weight=600,
                fill=theme.TEXT if is_current else theme.DIM, anchor="middle",
            )
        )

    return y + CHART_H + 24


def _category_strip(body: list[str], report: PersonalReport, y: float, currency: str) -> float:
    available = theme.WIDTH - theme.PAD * 2 - 16 * 2
    pill_w = available / 3

    for position, category in enumerate(Category):
        x = theme.PAD + position * (pill_w + 16)
        amount = report.by_category.get(category, ZERO)
        color = theme.CATEGORY_COLOR[category]
        body.append(svg.rect(x, y, pill_w, PILL_H, rx=20, fill=theme.CARD))
        body.append(svg.rect(x, y, 4, PILL_H, rx=2, fill=color, opacity=0.9))
        body.append(icons.category_icon(category, x + 22, y + 21, 26, color, width=2.1))
        body.append(
            svg.text(
                x + 62, y + 36, CATEGORY_TITLE[category].upper(),
                size=13, weight=700, fill=theme.MUTED, tracking=1.4,
            )
        )
        body.append(
            svg.text(
                x + 62, y + 66, money(amount, currency),
                size=23, weight=700, fill=theme.TEXT if amount else theme.DIM,
            )
        )
    return y + PILL_H + 20


def _stats(
    body: list[str], report: PersonalReport, fonts: FontBook, y: float, currency: str
) -> float:
    days = report.days_since_last
    streak = (
        with_plural(days, "день", "дня", "дней") if days is not None else "—"
    )
    cards = [
        ("ПРОГНОЗ НА МЕСЯЦ", money(report.forecast, currency), theme.TEXT),
        ("СРЕДНИЙ ПО ЧАТУ", money(report.chat_average, currency), theme.TEXT),
        ("БЕЗ ТРАТ", streak, theme.UP if (days or 0) >= 7 else theme.TEXT),
    ]
    return _card_row(body, fonts, y, cards)


def _mileage(
    body: list[str], report: PersonalReport, y: float, currency: str
) -> float:
    """Пробег и цена километра по всем тратам — не только по бензину."""
    cards: list[tuple[str, str, str]] = []

    if report.odometer:
        cards.append(("ПРОБЕГ", f"{report.odometer} км", theme.TEXT))
    if report.month_km:
        driven = f"{report.month_km:,} км".replace(",", " ")
        cards.append(("НАКАТАЛ ЗА МЕСЯЦ", driven, theme.TEXT))

    cost = report.cost
    if cost is not None:
        label = "ПОЛНАЯ ЦЕНА КМ" + ("  ·  МАЛО ДАННЫХ" if cost.low_data else "")
        color = theme.MUTED if cost.low_data else theme.GOLD
        cards.append((label, f"{decimal_str(cost.total, 2)} {currency}", color))

    if not cards:
        return y
    return _card_row(body, None, y, cards)


def _service(
    body: list[str], report: PersonalReport, fonts: FontBook, y: float
) -> float:
    """Ближайшие позиции обслуживания с оставшимся запасом."""
    width = theme.WIDTH - theme.PAD * 2
    right = theme.WIDTH - theme.PAD
    row_h, gap = 56, 10

    for index, due in enumerate(report.due):
        top = y + index * (row_h + gap)
        color = theme.DOWN if due.overdue else theme.GOLD
        body.append(svg.rect(theme.PAD, top, width, row_h, rx=18, fill=theme.CARD))
        body.append(svg.rect(theme.PAD, top, 4, row_h, rx=2, fill=color))

        reason = due.reason()
        body.append(
            svg.text(
                right - 22, top + 34, reason,
                size=16, weight=700, fill=color, anchor="end",
            )
        )
        limit = width - 48 - fonts.width(reason, 16, 700) - 24
        body.append(
            svg.text(
                theme.PAD + 26, top + 34,
                fonts.fit(due.plan.title, 18, limit, 600),
                size=18, weight=600, fill=theme.TEXT,
            )
        )

    return y + len(report.due) * (row_h + gap) + 8


def _fuel(body: list[str], report: PersonalReport, y: float, currency: str) -> float:
    fuel = report.fuel
    cards: list[tuple[str, str, str]] = []
    if fuel.liters_per_100 is not None:
        cards.append(
            (
                "РАСХОД",
                f"{decimal_str(fuel.liters_per_100)} л/100км",
                theme.CATEGORY_COLOR[Category.FUEL],
            )
        )
    if fuel.rub_per_km is not None:
        cards.append(("КМ ПО БЕНЗИНУ", f"{decimal_str(fuel.rub_per_km, 2)} {currency}", theme.TEXT))
    if fuel.price_per_liter is not None:
        cards.append(("ЦЕНА ЛИТРА", f"{decimal_str(fuel.price_per_liter, 2)} {currency}", theme.TEXT))
    if not cards:
        return y
    return _card_row(body, None, y, cards)


def _card_row(
    body: list[str], fonts: FontBook | None, y: float, cards: list[tuple[str, str, str]]
) -> float:
    count = len(cards)
    gap = 16
    width = (theme.WIDTH - theme.PAD * 2 - gap * (count - 1)) / count

    for position, (label, value, color) in enumerate(cards):
        x = theme.PAD + position * (width + gap)
        body.append(svg.rect(x, y, width, STAT_H, rx=20, fill=theme.CARD))
        body.append(
            svg.text(x + 24, y + 36, label, size=13, weight=700, fill=theme.MUTED, tracking=1.4)
        )
        body.append(svg.text(x + 24, y + 70, value, size=24, weight=900, fill=color))
    return y + STAT_H + 20


def _biggest(
    body: list[str],
    report: PersonalReport,
    fonts: FontBook,
    y: float,
    tz_name: str,
    currency: str,
) -> float:
    expense = report.biggest
    width = theme.WIDTH - theme.PAD * 2
    body.append(svg.rect(theme.PAD, y, width, FOOTER_H, rx=20, fill=theme.CARD, opacity=0.75))
    body.append(
        svg.text(theme.PAD + 24, y + 32, "САМАЯ КРУПНАЯ ТРАТА", size=13, weight=700, fill=theme.DIM, tracking=2)
    )
    note = expense.note or CATEGORY_TITLE[expense.category].lower()
    line = f"{money(expense.amount, currency)} · {note} · {timeutil.format_date(expense.spent_at, tz_name)}"
    body.append(
        svg.text(theme.PAD + 24, y + 60, fonts.fit(line, 19, width - 48, 600), size=19, weight=600, fill=theme.TEXT)
    )
    return y + FOOTER_H
