"""Блоки, общие для месячной и личной картинок."""

from __future__ import annotations

from decimal import Decimal

from ..formatting import money
from ..models import CATEGORY_TITLE, Category
from . import icons, svg, theme
from .text import FontBook

PILL_H = 88
PILL_GAP = 12


def category_strip(
    body: list[str],
    by_category: dict[Category, Decimal],
    fonts: FontBook,
    y: float,
    currency: str,
) -> float:
    """Плашка на каждую категорию: иконка, название, сумма.

    Категорий пять, поэтому раскладка компактная: иконка с подписью в верхней
    строке, сумма под ними во всю ширину плашки.
    """
    count = len(Category)
    pill_w = (theme.WIDTH - theme.PAD * 2 - PILL_GAP * (count - 1)) / count

    for position, category in enumerate(Category):
        x = theme.PAD + position * (pill_w + PILL_GAP)
        amount = by_category.get(category, Decimal(0))
        color = theme.CATEGORY_COLOR[category]

        body.append(svg.rect(x, y, pill_w, PILL_H, rx=18, fill=theme.CARD))
        body.append(svg.rect(x, y, 4, PILL_H, rx=2, fill=color, opacity=0.9))
        body.append(icons.category_icon(category, x + 18, y + 17, 20, color, width=2.1))

        label_x = x + 46
        label = fonts.fit(CATEGORY_TITLE[category].upper(), 11, x + pill_w - 10 - label_x, 700)
        body.append(
            svg.text(label_x, y + 32, label, size=11, weight=700, fill=theme.MUTED, tracking=1)
        )

        value = fonts.fit(money(amount, currency), 21, pill_w - 34, 700)
        body.append(
            svg.text(
                x + 18, y + 68, value,
                size=21, weight=700, fill=theme.TEXT if amount else theme.DIM,
            )
        )

    return y + PILL_H
