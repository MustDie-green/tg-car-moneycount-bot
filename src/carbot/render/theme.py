"""Палитра и размеры картинок."""

from __future__ import annotations

from ..models import Category

WIDTH = 1000
PAD = 44

# --- Цвета -------------------------------------------------------------------

BG = "#0B0E13"
BG_TOP = "#1B2739"
CARD = "#141922"
CARD_TOP = "#1A212C"
STROKE = "#232B38"
# Столбики графика и «пустая» часть полосы — заметно светлее карточки
CHART_BAR = "#2E3949"

TEXT = "#F2F5F9"
MUTED = "#78849A"
DIM = "#4C5769"

GOLD = "#F5C451"
SILVER = "#C3CEDD"
BRONZE = "#D08B4F"

CATEGORY_COLOR: dict[Category, str] = {
    Category.FUEL: "#4C9AFF",
    Category.SERVICE: "#FF7A45",
    Category.OTHER: "#35D0A5",
}

UP = "#35D07F"
DOWN = "#FF5C6C"
FLAT = "#66738A"
NEW = "#A98BFF"

# Цвета аватарок-заглушек: выбираются детерминированно по telegram id
AVATAR_COLORS = (
    "#E8615A", "#F0913C", "#E9C445", "#5CC26B", "#3FBFB0",
    "#4C9AFF", "#7B76F1", "#B96BE0", "#EE6BA8", "#8A94A6",
)

MEDAL_COLORS = (GOLD, SILVER, BRONZE)


def rank_color(rank: int) -> str:
    if 1 <= rank <= 3:
        return MEDAL_COLORS[rank - 1]
    return DIM


def avatar_color(tg_id: int) -> str:
    return AVATAR_COLORS[tg_id % len(AVATAR_COLORS)]
