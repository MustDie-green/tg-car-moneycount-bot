"""Фоновые задачи: ежемесячный отчёт и уборка протухших вопросов."""

from __future__ import annotations

import logging

from aiogram.types import BufferedInputFile
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import garage, service, timeutil
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

    # Раз в сутки утром: проверяем, кому пора менять расходники
    scheduler.add_job(
        send_service_reminders,
        CronTrigger(hour=11, minute=0, timezone=ctx.settings.timezone),
        args=[ctx],
        id="service-reminders",
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )
    return scheduler


async def send_service_reminders(ctx: Context) -> None:
    """Личное напоминание владельцу, когда до замены осталось немного.

    Пишем только в личку и только тому, чья машина: в общем чате это было бы
    ежедневным спамом. Личку бот открыть не может, поэтому тем, кто ни разу не
    писал боту напрямую, сообщение просто не доставится — это нормально.
    """
    now = timeutil.now(ctx.settings.timezone)

    for chat_id in await ctx.repo.chat_ids():
        if not ctx.allows(chat_id):
            continue
        try:
            garages = await garage.load_all(ctx.repo, chat_id, now=now)
        except Exception:  # noqa: BLE001
            log.exception("не смог собрать гараж чата %s", chat_id)
            continue

        for owner_id, item in garages.items():
            ready = [
                due
                for due in item.due
                if service.is_due(due, item.mileage, now)
            ]
            if not ready:
                continue
            await _notify(ctx, chat_id, owner_id, item, ready, now)


async def _notify(ctx: Context, chat_id: int, owner_id: int, item, due_list, now) -> None:
    lines = [f"🔧 <b>{item.title}</b> — скоро на сервис", ""]
    for due in due_list:
        mark = "❗️" if due.overdue else "·"
        lines.append(f"{mark} {due.plan.title} — {due.reason()}")

    if item.mileage and item.mileage.is_estimate:
        lines += [
            "",
            f"<i>Пробег прикинут по темпу езды — точный был "
            f"{item.mileage.known_km:,} км. Напиши «пробег 123456», "
            f"и я пересчитаю.</i>".replace(",", " "),
        ]

    try:
        await ctx.bot.send_message(owner_id, "\n".join(lines))
    except Exception as error:  # noqa: BLE001
        # Личка не открыта — ждём, пока человек сам напишет боту
        log.debug("не смог написать %s в личку: %s", owner_id, error)
        return

    for due in due_list:
        await ctx.repo.mark_reminded(
            chat_id, owner_id, due.plan.car_slug, due.plan.kind, now
        )


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
        # Вопрос без ответа — тоже сигнал: скорее всего бот спросил зря
        await ctx.repo.log_answer(chat_id, message_id, "timeout")
        await ctx.repo.drop_pending(chat_id, message_id)
