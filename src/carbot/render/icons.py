"""Векторные иконки.

Эмодзи в SVG не годятся — resvg рисует шрифтом, а в Inter цветных эмодзи нет.
Поэтому категории обозначаем нарисованными значками: полностью детерминированно
и выглядит опрятнее.
"""

from __future__ import annotations

from ..models import Category
from . import svg

# Все иконки нарисованы в квадрате 24×24 и масштабируются трансформацией.
_VIEWPORT = 24.0

_FUEL = (
    "M5 21V5.5A2.5 2.5 0 0 1 7.5 3h3.5A2.5 2.5 0 0 1 13.5 5.5V21",
    "M3 21h13",
    "M7.4 8.2h4.7",
    "M16.5 21v-8.2a1.8 1.8 0 0 1 1.8-1.8h1.2a1.5 1.5 0 0 0 1.5-1.5V7.5l-2.2-2.2",
)

# Ключ, в стилистике feather-icons
_SERVICE = (
    "M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77"
    "a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91"
    "a6 6 0 0 1 7.94-7.94l-3.76 3.76z",
)

# Молния
_TUNING = ("M13 2L3 14h9l-1 8 10-12h-9l1-8z",)

# Предупреждающий треугольник
_FINES = (
    "M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86"
    "a2 2 0 0 0-3.42 0z",
    "M12 9v4",
    "M12 17h.01",
)

# Капля
_CARE = ("M12 2.7l5.66 5.65a8 8 0 1 1-11.31 0z",)

# Здание с колоннами
_STATE = (
    "M3 21h18",
    "M4 9.5h16",
    "M4 8l8-5 8 5",
    "M6.5 12v6",
    "M10.5 12v6",
    "M13.5 12v6",
    "M17.5 12v6",
)

_PATHS: dict[Category, tuple[str, ...]] = {
    Category.FUEL: _FUEL,
    Category.SERVICE: _SERVICE,
    Category.TUNING: _TUNING,
    Category.FINES: _FINES,
    Category.CARE: _CARE,
    Category.STATE: _STATE,
}


def category_icon(
    category: Category, x: float, y: float, size: float, color: str, width: float = 2.0
) -> str:
    """Иконка категории с левым верхним углом в (x, y)."""
    scale = size / _VIEWPORT
    body = "".join(
        svg.path(d, stroke=color, width=width / scale) for d in _PATHS[category]
    )
    return svg.group(body, transform=f"translate({svg.num(x)},{svg.num(y)}) scale({scale:.4f})")


def triangle(cx: float, cy: float, size: float, color: str, *, up: bool = True) -> str:
    half = size / 2
    if up:
        d = f"M{svg.num(cx)} {svg.num(cy - half)}L{svg.num(cx + half)} {svg.num(cy + half)}H{svg.num(cx - half)}Z"
    else:
        d = f"M{svg.num(cx)} {svg.num(cy + half)}L{svg.num(cx + half)} {svg.num(cy - half)}H{svg.num(cx - half)}Z"
    return svg.path(d, fill=color)


def equals(cx: float, cy: float, size: float, color: str) -> str:
    half = size / 2
    thickness = max(1.6, size / 6)
    gap = thickness * 1.4
    return svg.rect(
        cx - half, cy - gap, size, thickness, rx=thickness / 2, fill=color
    ) + svg.rect(cx - half, cy + gap - thickness, size, thickness, rx=thickness / 2, fill=color)


def spark(cx: float, cy: float, size: float, color: str) -> str:
    """Звёздочка для новичков месяца."""
    r_outer, r_inner = size / 2, size / 5
    points: list[str] = []
    from math import cos, pi, sin

    for i in range(10):
        radius = r_outer if i % 2 == 0 else r_inner
        angle = -pi / 2 + i * pi / 5
        points.append(f"{svg.num(cx + radius * cos(angle))} {svg.num(cy + radius * sin(angle))}")
    return svg.path("M" + "L".join(points) + "Z", fill=color)
