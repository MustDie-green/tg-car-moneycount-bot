"""SVG → PNG через resvg."""

from __future__ import annotations

import logging
from pathlib import Path

import resvg_py

log = logging.getLogger(__name__)


def to_png(svg_markup: str, font_files: list[str], *, scale: float = 1.0) -> bytes:
    """Растеризует разметку. Системные шрифты отключены — рендер одинаков везде."""
    raw = resvg_py.svg_to_bytes(
        svg_string=svg_markup,
        skip_system_fonts=bool(font_files),
        font_files=font_files or None,
        zoom=scale if scale != 1.0 else None,
        text_rendering="optimize_legibility",
        shape_rendering="geometric_precision",
    )
    return bytes(raw)


def dump_svg(svg_markup: str, path: str | Path) -> Path:
    """Сохраняет исходный SVG — удобно открыть в браузере при отладке вёрстки."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(svg_markup, encoding="utf-8")
    return target
