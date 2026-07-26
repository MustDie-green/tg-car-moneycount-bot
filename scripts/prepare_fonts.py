"""Нарезает статические начертания Inter из вариативного файла.

resvg умеет вариативные оси, но статические файлы надёжнее: fontTools меряет
ширину текста ровно по тому же файлу, который потом рендерит resvg, так что
обрезка длинных имён и выравнивание по правому краю получаются точными.

Запуск: python scripts/prepare_fonts.py
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parents[1]
FONTS = ROOT / "assets" / "fonts"
SOURCE = ROOT / ".fontcache" / "Inter-Variable.ttf"
SOURCE_URL = (
    "https://raw.githubusercontent.com/google/fonts/main/ofl/inter/"
    "Inter%5Bopsz,wght%5D.ttf"
)

# Оптический размер фиксируем крупным — картинку смотрят с телефона
OPSZ = 28.0
WEIGHTS = {
    "Regular": 400,
    "SemiBold": 600,
    "Bold": 700,
    "Black": 900,
}


def download_source() -> None:
    if SOURCE.exists():
        return
    SOURCE.parent.mkdir(parents=True, exist_ok=True)
    print(f"скачиваю Inter → {SOURCE}")
    with urllib.request.urlopen(SOURCE_URL, timeout=60) as response:
        SOURCE.write_bytes(response.read())


def rename(font: TTFont, weight_name: str) -> None:
    """Все начертания складываем в одно семейство «Inter».

    По умолчанию инстансер разносит SemiBold и Black в отдельные семейства
    (так требует старая модель именования). Нам же нужно, чтобы resvg выбирал
    начертание по одному family + font-weight.
    """
    subfamily = "Bold" if weight_name == "Bold" else "Regular"
    records = {
        1: "Inter",
        2: subfamily,
        4: f"Inter {weight_name}",
        6: f"Inter-{weight_name}",
        16: "Inter",
        17: weight_name,
    }
    table = font["name"]
    for name_id, value in records.items():
        table.setName(value, name_id, 3, 1, 0x409)
        table.setName(value, name_id, 1, 0, 0)


def build() -> None:
    download_source()
    FONTS.mkdir(parents=True, exist_ok=True)
    for name, weight in WEIGHTS.items():
        target = FONTS / f"Inter-{name}.ttf"
        font = TTFont(SOURCE)
        static = instancer.instantiateVariableFont(
            font, {"wght": weight, "opsz": OPSZ}, updateFontNames=True, inplace=True
        )
        rename(static, name)
        static["OS/2"].usWeightClass = weight
        static.save(target)
        size = target.stat().st_size // 1024
        print(f"  {target.name}: {size} КБ, вес {weight}")


if __name__ == "__main__":
    try:
        build()
    except Exception as error:  # noqa: BLE001
        print(f"не получилось: {error}", file=sys.stderr)
        raise SystemExit(1)
