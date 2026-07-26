"""Общий контейнер зависимостей, который хендлеры получают из диспетчера."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from aiogram import Bot

from .config import Settings
from .db import Repo
from .models import Category
from .render import FontBook

log = logging.getLogger(__name__)

LEXICON_TTL = 300.0


@dataclass
class Context:
    settings: Settings
    repo: Repo
    fonts: FontBook
    bot: Bot
    _lexicon_cache: dict[int, tuple[float, dict[str, Category]]] = field(
        default_factory=dict, repr=False
    )

    def allows(self, chat_id: int) -> bool:
        allowed = self.settings.allowed_chat_ids
        return not allowed or chat_id in allowed

    async def learned(self, chat_id: int) -> dict[str, Category]:
        """Выученные чатом слова, с коротким кэшем — читается на каждое сообщение."""
        cached = self._lexicon_cache.get(chat_id)
        if cached and time.monotonic() - cached[0] < LEXICON_TTL:
            return cached[1]
        words = await self.repo.learned_words(chat_id, self.settings.learn_threshold)
        self._lexicon_cache[chat_id] = (time.monotonic(), words)
        return words

    def forget_lexicon(self, chat_id: int) -> None:
        self._lexicon_cache.pop(chat_id, None)

    async def render_png(self, markup: str) -> bytes:
        """Растеризация — заметная CPU-работа, уводим её из цикла событий."""
        from .render import to_png

        return await asyncio.to_thread(to_png, markup, self.fonts.files)
