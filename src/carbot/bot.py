"""Сборка и запуск бота."""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from .config import Settings, settings as default_settings
from .db import Database, Repo
from .handlers import build_router
from .render import load_fonts
from .scheduler import setup_scheduler
from .services import Context

log = logging.getLogger(__name__)


async def run(settings: Settings | None = None) -> None:
    settings = settings or default_settings
    if not settings.bot_token:
        raise SystemExit(
            "Не задан токен. Скопируй .env.example в .env и впиши CARBOT_BOT_TOKEN."
        )

    database = Database(
        url=settings.db_url,
        namespace=settings.db_namespace,
        database=settings.db_database,
        user=settings.db_user,
        password=settings.db_password,
    )
    await database.connect()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    context = Context(
        settings=settings,
        repo=Repo(database),
        fonts=load_fonts(str(settings.fonts_dir)),
        bot=bot,
    )

    dispatcher = Dispatcher()
    dispatcher["ctx"] = context
    dispatcher.include_router(build_router())

    scheduler = setup_scheduler(context)
    scheduler.start()

    me = await bot.get_me()
    log.info("бот @%s запущен", me.username)
    if not me.can_read_all_group_messages:
        log.warning(
            "У бота включён privacy mode — в группе он видит только команды и "
            "ответы себе, а значит траты ловить не сможет. Выключи: "
            "@BotFather → /setprivacy → выбрать бота → Disable, потом убрать "
            "бота из чата и добавить заново."
        )

    try:
        await dispatcher.start_polling(
            bot,
            allowed_updates=["message", "edited_message", "callback_query"],
        )
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await database.close()
