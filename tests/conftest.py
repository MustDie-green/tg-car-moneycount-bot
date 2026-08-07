"""Общие фикстуры для тестов, которые гоняют апдейты через aiogram."""

import pytest
from aiogram import Dispatcher

from carbot.handlers import build_router


@pytest.fixture(scope="session")
def dispatcher():
    """Один диспетчер на весь прогон.

    Роутеры хендлеров — модульные синглтоны, и привязать их можно ровно к
    одному диспетчеру. Поэтому он общий, а состояние мастера чистится между
    тестами: иначе шаг, на котором бот ждёт текст, протёк бы в следующий тест.
    """
    instance = Dispatcher()
    instance.include_router(build_router())
    return instance


@pytest.fixture(autouse=True)
def _clean_fsm(request):
    storage = getattr(request.getfixturevalue("dispatcher"), "storage", None) if (
        "dispatcher" in request.fixturenames
    ) else None
    if storage is not None:
        storage.storage.clear()
    yield
