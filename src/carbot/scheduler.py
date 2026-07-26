"""Фоновые задачи: ежемесячный отчёт и уборка протухших вопросов."""

from __future__ import annotations

import logging

from aiogram.types import BufferedInputFile
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import timeutil
from .handlers.commands import build_month_image, month_caption
from .services import Context

log = logging.getLogger(__name__)


def setup_scheduler(ctx: Context) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=ctx.settings.timezone)

    scheduler.add_job(
        send_monthly_reports,
        CronTrigger(
            day=ctx.settings.report_day,
            hour=ctx.settings.report_hour,
            minute=ctx.settings.report_minute,
            timezone=ctx.settings.timezone,
        ),
        args=[ctx],
        id="monthly-report",
        misfire_grace_time=3600,
        coalesce=True,
    )

    scheduler.add_job(
        cleanup_pendings,
        IntervalTrigger(seconds=60),
        args=[ctx],
        id="cleanup-pendings",
        coalesce=True,
        max_instances=1,
    )
    return scheduler


async def send_monthly_reports(ctx: Context) -> None:
    """Первого числа рассылает итоги прошлого месяца по всем живым чатам."""
    now = timeutil.now(ctx.settings.timezone)
    year, month = timeutil.prev_month(now.year, now.month)

    for chat_id in await ctx.repo.chat_ids():
        if not ctx.allows(chat_id):
            continue
        try:
            report, png = await build_month_image(ctx, chat_id, year, month)
            if not report.people:
                log.info("чат %s: за %s-%02d трат нет, отчёт не шлём", chat_id, year, month)
                continue
            await ctx.bot.send_photo(
                chat_id=chat_id,
                photo=BufferedInputFile(png, filename=f"top-{year}-{month:02d}.png"),
                caption=month_caption(report, ctx),
            )
            log.info("отчёт за %s-%02d отправлен в чат %s", year, month, chat_id)
        except Exception as error:  # noqa: BLE001
            log.exception("не смог отправить отчёт в чат %s: %s", chat_id, error)


async def cleanup_pendings(ctx: Context) -> None:
    """Вопросы без ответа сами убираются из чата, чтобы не мозолили глаза."""
    now = timeutil.now(ctx.settings.timezone)
    for row in await ctx.repo.expired_pendings(now):
        chat_id, message_id = row["chat_id"], row["message_id"]
        try:
            await ctx.bot.delete_message(chat_id=chat_id, message_id=row["question_id"])
        except Exception as error:  # noqa: BLE001
            log.debug("протухший вопрос %s не удалился: %s", row["question_id"], error)
        await ctx.repo.drop_pending(chat_id, message_id)
