"""Метрики текста.

resvg рисует SVG, но не говорит, сколько места займёт строка. Меряем сами по
тем же ttf-файлам, которые отдаём резвг: тогда обрезка длинных имён и
выравнивание по правому краю совпадают с реальным рендером до пикселя.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTFont

ELLIPSIS = "…"

WEIGHT_FILES = {
    400: "Inter-Regular.ttf",
    600: "Inter-SemiBold.ttf",
    700: "Inter-Bold.ttf",
    900: "Inter-Black.ttf",
}


class Font:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._tt = TTFont(str(path), lazy=True)
        self._upem = self._tt["head"].unitsPerEm
        self._cmap = self._tt.getBestCmap()
        self._hmtx = self._tt["hmtx"]
        self._cache: dict[str, int] = {}
        fallback = self._cmap.get(ord("?"))
        self._fallback = self._hmtx[fallback][0] if fallback else self._upem // 2

    def _advance(self, char: str) -> int:
        cached = self._cache.get(char)
        if cached is not None:
            return cached
        glyph = self._cmap.get(ord(char))
        advance = self._hmtx[glyph][0] if glyph else self._fallback
        self._cache[char] = advance
        return advance

    def has(self, char: str) -> bool:
        return ord(char) in self._cmap

    def width(self, text: str, size: float) -> float:
        units = sum(self._advance(char) for char in text)
        return units * size / self._upem

    def fit(self, text: str, size: float, max_width: float) -> str:
        """Обрезает строку до ширины, добавляя многоточие."""
        if self.width(text, size) <= max_width:
            return text
        ellipsis_width = self.width(ELLIPSIS, size)
        budget = max_width - ellipsis_width
        if budget <= 0:
            return ELLIPSIS
        out: list[str] = []
        used = 0.0
        for char in text:
            advance = self._advance(char) * size / self._upem
            if used + advance > budget:
                break
            out.append(char)
            used += advance
        return "".join(out).rstrip() + ELLIPSIS

    def wrap(self, text: str, size: float, max_width: float, max_lines: int = 2) -> list[str]:
        """Простой перенос по словам — resvg сам этого не умеет."""
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if self.width(candidate, size) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
                if len(lines) == max_lines:
                    break
        if current and len(lines) < max_lines:
            lines.append(current)
        if not lines:
            return []
        if len(lines) == max_lines:
            lines[-1] = self.fit(lines[-1], size, max_width)
        return lines


class FontBook:
    def __init__(self, fonts_dir: Path) -> None:
        self.dir = Path(fonts_dir)
        self._fonts: dict[int, Font] = {}

    @property
    def files(self) -> list[str]:
        """Пути для resvg — только те начертания, что реально есть."""
        return [
            str(self.dir / name)
            for name in WEIGHT_FILES.values()
            if (self.dir / name).exists()
        ]

    def font(self, weight: int = 400) -> Font:
        weight = min(WEIGHT_FILES, key=lambda w: abs(w - weight))
        if weight not in self._fonts:
            path = self.dir / WEIGHT_FILES[weight]
            if not path.exists():
                raise FileNotFoundError(
                    f"нет файла шрифта {path}. Запусти: python scripts/prepare_fonts.py"
                )
            self._fonts[weight] = Font(path)
        return self._fonts[weight]

    def width(self, text: str, size: float, weight: int = 400) -> float:
        return self.font(weight).width(text, size)

    def fit(self, text: str, size: float, max_width: float, weight: int = 400) -> str:
        return self.font(weight).fit(text, size, max_width)

    def wrap(
        self, text: str, size: float, max_width: float, weight: int = 400, max_lines: int = 2
    ) -> list[str]:
        return self.font(weight).wrap(text, size, max_width, max_lines)


@lru_cache(maxsize=4)
def load_fonts(fonts_dir: str) -> FontBook:
    return FontBook(Path(fonts_dir))
