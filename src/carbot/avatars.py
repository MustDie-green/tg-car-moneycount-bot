"""Аватарки участников для картинки месяца.

Телеграм отдаёт фото профиля не всегда (настройки приватности, нет аватарки),
поэтому любая ошибка здесь — не ошибка: в картинке просто рисуется кружок с
инициалами.
"""

from __future__ import annotations

import logging
from pathlib import Path

from aiogram import Bot

log = logging.getLogger(__name__)


async def fetch_avatars(
    bot: Bot, tg_ids: list[int], cache_dir: Path
) -> dict[int, bytes]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    out: dict[int, bytes] = {}
    for tg_id in tg_ids:
        data = await _one(bot, tg_id, cache_dir)
        if data:
            out[tg_id] = data
    return out


async def _one(bot: Bot, tg_id: int, cache_dir: Path) -> bytes | None:
    try:
        photos = await bot.get_user_profile_photos(user_id=tg_id, limit=1)
        if not photos.photos:
            return None
        # Берём самый маленький размер: в кружке 54 пикселя большего не нужно
        photo = min(photos.photos[0], key=lambda size: size.width)
        cached = cache_dir / f"{photo.file_unique_id}.bin"
        if cached.exists():
            return cached.read_bytes()

        buffer = await bot.download(photo.file_id)
        if buffer is None:
            return None
        data = buffer.read()
        cached.write_bytes(data)
        return data
    except Exception as error:  # noqa: BLE001
        log.debug("аватарка %s недоступна: %s", tg_id, error)
        return None


def mime_of(data: bytes) -> str:
    return "image/jpeg" if data[:2] == b"\xff\xd8" else "image/png"
