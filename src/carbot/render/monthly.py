"""Картинка с итогами месяца."""

from __future__ import annotations

import base64
from decimal import Decimal

from .. import timeutil
from ..formatting import decimal_str, money, with_plural
from ..models import (
    Category,
    CATEGORY_TITLE,
    Expense,
    MonthReport,
    PersonMonth,
    ServiceHint,
)
from ..reporting import month_delta_text
from . import icons, svg, theme
from .text import FontBook

ZERO = Decimal(0)

HEADER_H = 208
PILL_H = 88
PILL_GAP = 16
# Строка участника выросла: под именем теперь машина, пробег и цена километра
ROW_H = 108
ROW_GAP = 12
SERVICE_ROW_H = 58
SERVICE_GAP = 10
TOP_CARD_H = 88
SECTION_H = 46
FOOTER_H = 74

BAR_MAX = 540
AVATAR_R = 27
NAME_X = theme.PAD + 140


def render_month(
    report: MonthReport,
    fonts: FontBook,
    *,
    tz_name: str = "Europe/Moscow",
    currency: str = "₽",
) -> str:
    defs: list[str] = [_glow_def()]
    body: list[str] = []
    width = theme.WIDTH

    y = _header(body, report, fonts, currency)
    y = _category_strip(body, report, fonts, y, currency)

    if report.people:
        y = _section(body, fonts, y, "КТО СКОЛЬКО ПОТРАТИЛ", "▲ выше в топе — значит потратил больше")
        y = _leaderboard(body, defs, report.people, fonts, y, currency)
    else:
        y = _empty_state(body, fonts, y)

    if report.top_expenses:
        y = _section(body, fonts, y, "САМОЕ ДОРОГОЕ ЗА МЕСЯЦ")
        y = _top_expenses(body, report.top_expenses, fonts, y, tz_name, currency)

    if report.service_soon:
        y = _section(body, fonts, y, "СКОРО НА СЕРВИС")
        y = _service_block(body, report.service_soon, fonts, y)

    if report.fun_fact:
        y = _footer(body, report.fun_fact, fonts, y)

    height = y + theme.PAD
    background = (
        svg.rect(0, 0, width, height, fill=theme.BG)
        + svg.rect(0, 0, width, min(height, 420), fill="url(#glow)")
    )
    return svg.document(width, height, "".join(defs), background + "".join(body))


# --- Блоки -------------------------------------------------------------------


def _glow_def() -> str:
    return (
        '<radialGradient id="glow" cx="0.5" cy="0" r="0.9">'
        f'<stop offset="0" stop-color="{theme.BG_TOP}" stop-opacity="0.85"/>'
        f'<stop offset="1" stop-color="{theme.BG}" stop-opacity="0"/>'
        "</radialGradient>"
    )


def _header(body: list[str], report: MonthReport, fonts: FontBook, currency: str) -> float:
    right = theme.WIDTH - theme.PAD

    body.append(
        svg.text(
            theme.PAD, 64, "АВТОБУХГАЛТЕРИЯ",
            size=15, weight=700, fill=theme.MUTED, tracking=5.5,
        )
    )
    body.append(
        svg.text(
            theme.PAD, 126, timeutil.month_title(report.year, report.month),
            size=52, weight=900, fill=theme.TEXT, tracking=-1,
        )
    )

    people_count = len(report.people)
    expense_count = sum(person.count for person in report.people)
    subtitle = (
        f"{with_plural(people_count, 'участник', 'участника', 'участников')} · "
        f"{with_plural(expense_count, 'трата', 'траты', 'трат')}"
    )
    if report.total_km:
        subtitle += f" · {report.total_km:,} км".replace(",", " ")
    body.append(svg.text(theme.PAD, 162, subtitle, size=17, weight=400, fill=theme.MUTED))

    body.append(
        svg.text(
            right, 122, money(report.total, currency),
            size=44, weight=900, fill=theme.GOLD, anchor="end", tracking=-0.5,
        )
    )
    delta = month_delta_text(report.total, report.prev_total)
    if delta:
        color = theme.DOWN if report.total > report.prev_total else theme.UP
        if delta.startswith("как"):
            color = theme.MUTED
        body.append(
            svg.text(right, 156, delta, size=17, weight=600, fill=color, anchor="end")
        )

    return HEADER_H


def _category_strip(
    body: list[str], report: MonthReport, fonts: FontBook, y: float, currency: str
) -> float:
    available = theme.WIDTH - theme.PAD * 2 - PILL_GAP * 2
    pill_w = available / 3

    for position, category in enumerate(Category):
        x = theme.PAD + position * (pill_w + PILL_GAP)
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

    return y + PILL_H + 28


def _section(
    body: list[str], fonts: FontBook, y: float, title: str, hint: str | None = None
) -> float:
    body.append(
        svg.text(theme.PAD, y + 20, title, size=14, weight=700, fill=theme.DIM, tracking=3.5)
    )
    if hint:
        body.append(
            svg.text(
                theme.WIDTH - theme.PAD, y + 20, hint,
                size=13, weight=400, fill=theme.DIM, anchor="end",
            )
        )
    return y + SECTION_H


def _leaderboard(
    body: list[str],
    defs: list[str],
    people: list[PersonMonth],
    fonts: FontBook,
    y: float,
    currency: str,
) -> float:
    max_total = max((person.total for person in people), default=ZERO) or Decimal(1)

    for position, person in enumerate(people):
        top = y + position * (ROW_H + ROW_GAP)
        _leaderboard_row(body, defs, person, position, top, max_total, fonts, currency)

    return y + len(people) * (ROW_H + ROW_GAP)


def _leaderboard_row(
    body: list[str],
    defs: list[str],
    person: PersonMonth,
    index: int,
    top: float,
    max_total: Decimal,
    fonts: FontBook,
    currency: str,
) -> None:
    right = theme.WIDTH - theme.PAD
    center_y = top + ROW_H / 2

    body.append(svg.rect(theme.PAD, top, theme.WIDTH - theme.PAD * 2, ROW_H, rx=22, fill=theme.CARD))
    if person.rank <= 3:
        body.append(
            svg.rect(theme.PAD, top, 4, ROW_H, rx=2, fill=theme.rank_color(person.rank))
        )

    body.append(
        svg.text(
            theme.PAD + 40, center_y + 11, str(person.rank),
            size=31, weight=900, fill=theme.rank_color(person.rank), anchor="middle",
        )
    )

    body.append(_avatar(defs, person, theme.PAD + 96, center_y, AVATAR_R, fonts))

    amount_text = money(person.total, currency)
    amount_w = fonts.width(amount_text, 27, 900)
    body.append(
        svg.text(
            right - 26, top + 42, amount_text,
            size=27, weight=900, fill=theme.TEXT, anchor="end",
        )
    )

    badge = _rank_badge(person, fonts)
    if badge:
        markup, badge_w = badge
        body.append(
            svg.group(markup, transform=f"translate({svg.num(right - 26 - badge_w)},{svg.num(top + 54)})")
        )

    name_limit = (right - 26 - amount_w - 28) - NAME_X
    chip = _title_chip(person, fonts)
    if chip:
        chip_markup, chip_w = chip
        name_limit -= chip_w + 12
    name = fonts.fit(person.name, 25, max(name_limit, 60), 700)
    body.append(svg.text(NAME_X, top + 36, name, size=25, weight=700, fill=theme.TEXT))

    if chip:
        chip_x = NAME_X + fonts.width(name, 25, 700) + 12
        body.append(svg.group(chip_markup, transform=f"translate({svg.num(chip_x)},{svg.num(top + 16)})"))

    meta = _person_meta(person)
    if meta:
        body.append(
            svg.text(
                NAME_X, top + 60,
                fonts.fit(meta, 15, right - 26 - NAME_X - amount_w - 20, 400),
                size=15, weight=400, fill=theme.MUTED,
            )
        )

    _category_bar(body, defs, person, index, NAME_X, top + 76, max_total)


def _person_meta(person: PersonMonth) -> str:
    """«RAV4 · ~124 300 км · 1 240 км за месяц · 12,4 ₽/км»."""
    parts: list[str] = []
    if person.car_title:
        parts.append(person.car_title)
    if person.odometer:
        parts.append(f"{person.odometer} км")
    if person.month_km:
        parts.append(f"{person.month_km:,} км за месяц".replace(",", " "))
    if person.rub_per_km:
        price = decimal_str(person.rub_per_km, 1)
        # Пометка честности: цифра посчитана на слишком коротком окне
        parts.append(f"≈{price} ₽/км" if person.cost_low_data else f"{price} ₽/км")
    return " · ".join(parts)


def _category_bar(
    body: list[str],
    defs: list[str],
    person: PersonMonth,
    index: int,
    x: float,
    y: float,
    max_total: Decimal,
) -> None:
    height = 11
    width = float(BAR_MAX * (person.total / max_total)) if max_total else 0
    width = max(width, 24)

    body.append(svg.rect(x, y, BAR_MAX, height, rx=height / 2, fill=theme.CHART_BAR, opacity=0.5))

    clip_id = f"bar{index}"
    segments: list[str] = []
    offset = x
    for category in Category:
        amount = person.by_category.get(category, ZERO)
        if amount <= 0:
            continue
        segment_w = width * float(amount / person.total) if person.total else 0
        segments.append(
            svg.rect(offset, y, segment_w + 0.5, height, fill=theme.CATEGORY_COLOR[category])
        )
        offset += segment_w

    shape = svg.rect(x, y, width, height, rx=height / 2, fill="#fff")
    body.append(svg.clip(clip_id, shape, "".join(segments)))


def _rank_badge(person: PersonMonth, fonts: FontBook) -> tuple[str, float] | None:
    """Пилюля со смещением в топе: ▲2 / ▼1 / = / NEW."""
    delta = person.rank_delta
    height = 24

    if person.prev_rank is None:
        label, color, glyph = "NEW", theme.NEW, None
    elif delta == 0:
        label, color, glyph = "", theme.FLAT, "flat"
    elif delta > 0:
        label, color, glyph = str(abs(delta)), theme.DOWN, "up"
    else:
        label, color, glyph = str(abs(delta)), theme.UP, "down"

    text_w = fonts.width(label, 14, 700) if label else 0
    icon_w = 14 if glyph else 0
    width = 14 + icon_w + (6 if glyph and label else 0) + text_w + 14

    parts = [svg.rect(0, 0, width, height, rx=height / 2, fill=color, opacity=0.16)]
    cursor = 14
    if glyph == "up":
        parts.append(icons.triangle(cursor + 5, height / 2, 10, color, up=True))
        cursor += icon_w + 6
    elif glyph == "down":
        parts.append(icons.triangle(cursor + 5, height / 2, 10, color, up=False))
        cursor += icon_w + 6
    elif glyph == "flat":
        parts.append(icons.equals(cursor + 5, height / 2, 11, color))
        cursor += icon_w + 6
    if label:
        parts.append(
            svg.text(cursor, height / 2 + 5, label, size=14, weight=700, fill=color)
        )
    return "".join(parts), width


def _title_chip(person: PersonMonth, fonts: FontBook) -> tuple[str, float] | None:
    if not person.title:
        return None
    height = 26
    text_w = fonts.width(person.title, 13, 700)
    width = text_w + 26
    markup = svg.rect(0, 0, width, height, rx=height / 2, fill=theme.GOLD, opacity=0.14) + svg.text(
        13, height / 2 + 4.5, person.title, size=13, weight=700, fill=theme.GOLD
    )
    return markup, width


def _avatar(
    defs: list[str], person: PersonMonth, cx: float, cy: float, r: float, fonts: FontBook
) -> str:
    if person.avatar:
        clip_id = f"ava{person.tg_id}"
        defs.append(f'<clipPath id="{clip_id}"><circle cx="{svg.num(cx)}" cy="{svg.num(cy)}" r="{svg.num(r)}"/></clipPath>')
        encoded = base64.b64encode(person.avatar).decode("ascii")
        mime = "image/jpeg" if person.avatar[:2] == b"\xff\xd8" else "image/png"
        uri = f"data:{mime};base64,{encoded}"
        return (
            f'<g clip-path="url(#{clip_id})">'
            + svg.image(cx - r, cy - r, r * 2, uri)
            + "</g>"
        )

    color = theme.avatar_color(person.tg_id)
    initials = _initials(person.name)
    return (
        svg.circle(cx, cy, r, fill=color, opacity=0.9)
        + svg.text(
            cx, cy + 8, initials, size=22, weight=700, fill="#10141B", anchor="middle"
        )
    )


def _initials(name: str) -> str:
    parts = [part for part in name.replace("-", " ").split() if part]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][0].upper()
    return (parts[0][0] + parts[1][0]).upper()


def _top_expenses(
    body: list[str],
    expenses: list[Expense],
    fonts: FontBook,
    y: float,
    tz_name: str,
    currency: str,
) -> float:
    right = theme.WIDTH - theme.PAD

    for position, expense in enumerate(expenses):
        top = y + position * (TOP_CARD_H + ROW_GAP)
        center_y = top + TOP_CARD_H / 2
        color = theme.CATEGORY_COLOR[expense.category]
        medal = theme.MEDAL_COLORS[min(position, 2)]

        body.append(
            svg.rect(theme.PAD, top, theme.WIDTH - theme.PAD * 2, TOP_CARD_H, rx=22, fill=theme.CARD)
        )
        # Кольцо вместо заливки: цифра внутри остаётся контрастной
        body.append(svg.circle(theme.PAD + 46, center_y, 21, fill=medal, opacity=0.85))
        body.append(svg.circle(theme.PAD + 46, center_y, 18.4, fill=theme.CARD))
        body.append(
            svg.text(
                theme.PAD + 46, center_y + 7, str(position + 1),
                size=20, weight=900, fill=medal, anchor="middle",
            )
        )

        body.append(icons.category_icon(expense.category, theme.PAD + 84, top + 20, 22, color, width=2.1))

        amount_text = money(expense.amount, currency)
        body.append(
            svg.text(theme.PAD + 116, top + 39, amount_text, size=25, weight=900, fill=theme.TEXT)
        )

        author = fonts.fit(expense.author_name, 19, 220, 700)
        author_w = fonts.width(author, 19, 700)
        body.append(
            svg.text(right - 24, top + 38, author, size=19, weight=700, fill=theme.TEXT, anchor="end")
        )
        body.append(
            svg.text(
                right - 24, top + 64, timeutil.format_date(expense.spent_at, tz_name),
                size=15, weight=400, fill=theme.DIM, anchor="end",
            )
        )

        note_limit = (right - 24 - author_w - 40) - (theme.PAD + 84)
        note = expense.note or CATEGORY_TITLE[expense.category].lower()
        body.append(
            svg.text(
                theme.PAD + 84, top + 66, fonts.fit(note, 17, note_limit, 400),
                size=17, weight=400, fill=theme.MUTED,
            )
        )

    return y + len(expenses) * (TOP_CARD_H + ROW_GAP)


def _service_block(
    body: list[str], hints: list[ServiceHint], fonts: FontBook, y: float
) -> float:
    """Кому по парку пора менять расходники: имя, машина, позиция и срок."""
    width = theme.WIDTH - theme.PAD * 2
    right = theme.WIDTH - theme.PAD

    for index, hint in enumerate(hints):
        top = y + index * (SERVICE_ROW_H + SERVICE_GAP)
        color = theme.DOWN if hint.overdue else theme.GOLD

        body.append(svg.rect(theme.PAD, top, width, SERVICE_ROW_H, rx=18, fill=theme.CARD))
        body.append(svg.rect(theme.PAD, top, 4, SERVICE_ROW_H, rx=2, fill=color))

        reason_w = fonts.width(hint.reason, 16, 700)
        body.append(
            svg.text(
                right - 22, top + 36, hint.reason,
                size=16, weight=700, fill=color, anchor="end",
            )
        )

        label = f"{hint.person} · {hint.car}"
        limit = right - 22 - reason_w - 24 - (theme.PAD + 26)
        body.append(
            svg.text(
                theme.PAD + 26, top + 26,
                fonts.fit(hint.title, 17, limit, 700),
                size=17, weight=700, fill=theme.TEXT,
            )
        )
        body.append(
            svg.text(
                theme.PAD + 26, top + 46,
                fonts.fit(label, 14, limit, 400),
                size=14, weight=400, fill=theme.MUTED,
            )
        )

    return y + len(hints) * (SERVICE_ROW_H + SERVICE_GAP)


def _footer(body: list[str], fact: str, fonts: FontBook, y: float) -> float:
    top = y + 8
    width = theme.WIDTH - theme.PAD * 2
    body.append(svg.rect(theme.PAD, top, width, FOOTER_H - 8, rx=20, fill=theme.CARD, opacity=0.75))
    body.append(
        svg.text(
            theme.WIDTH / 2, top + 41,
            fonts.fit(fact, 18, width - 60, 400),
            size=18, weight=400, fill=theme.MUTED, anchor="middle",
        )
    )
    return y + FOOTER_H


def _empty_state(body: list[str], fonts: FontBook, y: float) -> float:
    height = 120
    body.append(
        svg.rect(theme.PAD, y, theme.WIDTH - theme.PAD * 2, height, rx=22, fill=theme.CARD)
    )
    body.append(
        svg.text(
            theme.WIDTH / 2, y + 56, "В этом месяце никто ничего не потратил",
            size=22, weight=700, fill=theme.TEXT, anchor="middle",
        )
    )
    body.append(
        svg.text(
            theme.WIDTH / 2, y + 86, "Либо все ходят пешком, либо забыли написать в чат",
            size=17, weight=400, fill=theme.MUTED, anchor="middle",
        )
    )
    return y + height + 16
