"""Разовые правки данных при переходе между версиями.

Каждая миграция идемпотентна: повторный запуск ничего не меняет. Поэтому
гоняются они просто на каждом старте, без таблицы версий.
"""

from __future__ import annotations

import logging

from ..models import Category
from ..parsing.morph import analyze, index
from .client import Database

log = logging.getLogger(__name__)

# Во что может превратиться старое «остальное»
_SPLIT = {Category.FINES, Category.STATE, Category.CARE}


def split_category(text: str) -> Category:
    """Куда раскладывать старое «остальное»: по словам, а иначе — в уход."""
    hit = index.match(analyze(text))
    if hit is not None and hit.category in _SPLIT:
        return hit.category
    return Category.CARE


async def split_other(db: Database) -> int:
    """«Остальное» → штрафы, государство или уход."""
    rows = await db.query("SELECT id, note FROM expense WHERE category = 'other'")
    for row in rows or []:
        category = split_category(row.get("note") or "")
        await db.query(
            "UPDATE $id SET category = $category",
            {"id": row["id"], "category": category.value},
        )

    words = await db.query("SELECT id, word FROM lexicon WHERE category = 'other'")
    for row in words or []:
        category = split_category(row["word"])
        await db.query(
            "UPDATE $id SET category = $category",
            {"id": row["id"], "category": category.value},
        )

    moved = len(rows or []) + len(words or [])
    if moved:
        log.info(
            "миграция категорий: трат %s, слов словаря %s", len(rows or []), len(words or [])
        )
    return moved


async def run_all(db: Database) -> None:
    await split_other(db)
