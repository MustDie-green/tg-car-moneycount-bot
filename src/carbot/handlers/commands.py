"""Команды бота."""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .. import garage, timeutil
from ..avatars import fetch_avatars
from ..formatting import money, with_plural
from ..models import CATEGORY_EMOJI, CATEGORY_TITLE, MonthReport
from ..render import render_month, render_personal
from ..reporting import build_month_report, build_personal_report
from ..services import Context
from .expenses import display_name

log = logging.getLogger(__name__)
router = Router(name="commands")

HELP = """<b>Автобухгалтерия</b> — считаю, кто сколько спустил на машину.

<b>Как записать трату</b>
Пишите слово про машину и сумму:
· <code>бенз 2000</code>
· <code>мойка 500р</code>
· <code>колодки 6500 и развал 3000</code>
· <code>заправка 42л 2800 пробег 123456</code>
Я отвечу «На тачку?» — жмите «Да», и трата уедет в базу, а на сообщении появится 🫡

На голое число я не реагирую: в чате их слишком много не про машину.
Если слова я не знаю — начните с <code>на тачку</code>:
· <code>на тачку 3000 гидрач</code>
Тут я спрошу только категорию и заодно запомню слово «гидрач» —
в следующий раз пойму его сам.

Можно писать и просто <code>пробег 123456</code> — спрошу «Записать пробег?»,
и после «Да» отмечу ✍️.
Чем чаще пишете пробег, тем точнее я считаю цену километра и сроки замен.

<b>Категории</b>
⛽ Бензин · 🔧 Ремонт и ТО · 🚨 Штрафы · 🧽 Уход · 🏛 Государство
(парковки, платные дороги, налог, страховка, техосмотр)

<b>ТО</b>
Записал масло, фильтры, колодки — вопрос превратится в «Отметить в ТО?»:
пробег подставлю сам, поправить можно ответом на сообщение.

<b>Команды</b>
/top — картинка с итогами текущего месяца
/top 06.2026 — итоги конкретного месяца
/me — личная карточка со статистикой и расходом топлива
/undo — удалить последнюю свою трату
/car Camry — сказать, на какую машину идут траты
/cars — весь парк чата с пробегами
/to — настроить, что и когда менять (открою личку)
/lexicon — какие слова я выучил в этом чате
/detect — журнал: на что реагировал и на что молчал
/export — выгрузка всех трат в CSV

Первого числа каждого месяца сам присылаю итоги прошлого."""


@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


# --- Картинки ----------------------------------------------------------------


@router.message(Command("top", "month", "итоги"))
async def cmd_top(message: Message, command: CommandObject, ctx: Context) -> None:
    if not ctx.allows(message.chat.id):
        return
    year, month = _requested_month(command.args, ctx)
    notice = await message.answer("Рисую…")
    try:
        report, png = await build_month_image(ctx, message.chat.id, year, month)
        await message.answer_photo(
            BufferedInputFile(png, filename=f"top-{year}-{month:02d}.png"),
            caption=month_caption(report, ctx),
        )
    except Exception:
        log.exception("не смог нарисовать итоги %s-%02d для чата %s", year, month, message.chat.id)
        await message.reply("Картинка не нарисовалась. Загляни в логи.")
    finally:
        await _quiet_delete(ctx, message.chat.id, notice.message_id)


@router.message(Command("me", "my", "я"))
async def cmd_me(message: Message, command: CommandObject, ctx: Context) -> None:
    if not ctx.allows(message.chat.id) or message.from_user is None:
        return
    year, month = _requested_month(command.args, ctx)
    report = await build_personal_report(
        ctx.repo,
        message.chat.id,
        message.from_user.id,
        display_name(message.from_user),
        year,
        month,
        ctx.settings.timezone,
    )
    markup = render_personal(
        report, ctx.fonts, tz_name=ctx.settings.timezone, currency=ctx.settings.currency
    )
    png = await ctx.render_png(markup)
    await message.answer_photo(
        BufferedInputFile(png, filename="me.png"),
        caption=_personal_caption(report, ctx),
    )


async def build_month_image(
    ctx: Context, chat_id: int, year: int, month: int
) -> tuple[MonthReport, bytes]:
    report = await build_month_report(
        ctx.repo, chat_id, year, month, ctx.settings.timezone
    )
    if ctx.settings.avatars_enabled and report.people:
        avatars = await fetch_avatars(
            ctx.bot, [person.tg_id for person in report.people], ctx.settings.cache_dir
        )
        for person in report.people:
            person.avatar = avatars.get(person.tg_id)

    markup = render_month(
        report, ctx.fonts, tz_name=ctx.settings.timezone, currency=ctx.settings.currency
    )
    return report, await ctx.render_png(markup)


def month_caption(report: MonthReport, ctx: Context) -> str:
    title = timeutil.MONTHS_GENITIVE[report.month - 1]
    if not report.people:
        return f"Итоги {title}: тишина, никто ничего не потратил"
    leader = report.people[0]
    return (
        f"🏁 Итоги {title}: всего {money(report.total, ctx.settings.currency)}. "
        f"Больше всех — {leader.name}, {money(leader.total, ctx.settings.currency)}."
    )


def _personal_caption(report, ctx: Context) -> str:
    if report.count == 0:
        return "В этом месяце трат нет. Машина стоит в гараже и не просит есть."
    parts = [
        f"{with_plural(report.count, 'трата', 'траты', 'трат')} на "
        f"{money(report.total, ctx.settings.currency)}"
    ]
    if report.forecast > report.total:
        parts.append(f"к концу месяца выйдет ~{money(report.forecast, ctx.settings.currency)}")
    return " · ".join(parts)


def _requested_month(args: str | None, ctx: Context) -> tuple[int, int]:
    current = timeutil.now(ctx.settings.timezone)
    if not args:
        return current.year, current.month
    text = args.strip().lower()
    if text.startswith("прошл"):
        return timeutil.prev_month(current.year, current.month)
    parsed = timeutil.parse_month(text, ctx.settings.timezone)
    return parsed or (current.year, current.month)


# --- Правки и служебное ------------------------------------------------------


@router.message(Command("undo", "отмена"))
async def cmd_undo(message: Message, ctx: Context) -> None:
    if not ctx.allows(message.chat.id) or message.from_user is None:
        return

    target_id = None
    if message.reply_to_message is not None:
        target_id = message.reply_to_message.message_id
        expenses = await ctx.repo.expenses_by_message(message.chat.id, target_id)
        if expenses and expenses[0].author_id != message.from_user.id:
            await message.reply("Это не твоя трата 👀")
            return
    else:
        expenses = await ctx.repo.last_expenses(message.chat.id, message.from_user.id)
        target_id = expenses[0].message_id if expenses else None

    if not expenses or target_id is None:
        await message.reply("Нечего отменять")
        return

    total = sum(expense.amount for expense in expenses)
    await ctx.repo.delete_expenses(message.chat.id, target_id)
    try:
        await ctx.bot.set_message_reaction(
            chat_id=message.chat.id, message_id=target_id, reaction=[]
        )
    except Exception as error:  # noqa: BLE001
        log.debug("реакцию снять не вышло: %s", error)

    await message.reply(f"🗑 Убрал {money(total, ctx.settings.currency)}")


@router.message(Command("car", "тачка"))
async def cmd_car(message: Message, command: CommandObject, ctx: Context) -> None:
    if not ctx.allows(message.chat.id) or message.from_user is None:
        return
    title = (command.args or "").strip()
    if not title:
        await message.reply("Напиши название: <code>/car Camry 2007</code>")
        return
    await ctx.repo.upsert_person(
        message.chat.id, message.from_user.id, display_name(message.from_user)
    )
    await ctx.repo.add_car(message.chat.id, message.from_user.id, title[:60])
    await message.reply(f"🚗 Теперь траты пишу на «{title[:60]}»")


@router.message(Command("cars", "тачки"))
async def cmd_cars(message: Message, ctx: Context) -> None:
    """Весь парк чата: человек — машина — пробег."""
    if not ctx.allows(message.chat.id):
        return
    cars = await ctx.repo.all_cars(message.chat.id)
    if not cars:
        await message.reply("Машин пока нет. Добавь: <code>/car Camry 2007</code>")
        return

    names = await ctx.repo.person_names(message.chat.id)
    garages = await garage.load_all(
        ctx.repo, message.chat.id, now=timeutil.now(ctx.settings.timezone)
    )

    lines: list[str] = []
    for car in cars:
        owner = names.get(car["owner_id"], "кто-то")
        mark = "🚗" if car.get("active", True) else "🅿️"
        parts = [f"{mark} <b>{owner}</b> — {car['title']}"]

        found = garages.get(car["owner_id"])
        # Пробег есть только у активной машины: замеры пишутся на неё
        if found and found.slug == garage.slug_of(car["id"]) and found.odometer_text:
            parts.append(f"{found.odometer_text} км")
        lines.append(" — ".join(parts))

    hint = "\n\n<i>~ перед пробегом — это моя оценка между замерами</i>"
    await message.reply("\n".join(lines) + hint)


@router.message(Command("to", "то", "service"))
async def cmd_service(message: Message, ctx: Context) -> None:
    """Приглашение в личку: настраивать интервалы в общем чате — это спам."""
    if not ctx.allows(message.chat.id) or message.from_user is None:
        return
    await ctx.repo.bind_dm(message.from_user.id, message.chat.id)
    me = await ctx.bot.me()
    await message.reply(
        "Настройка ТО живёт в личке, чтобы не засорять чат.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔧 Открыть личный кабинет",
                        url=f"https://t.me/{me.username}?start=to",
                    )
                ]
            ]
        ),
    )


@router.message(Command("detect", "детект"))
async def cmd_detect(message: Message, ctx: Context) -> None:
    """Журнал наблюдений детектора: сводка в чат, подробности файлом.

    Нужен, чтобы через пару недель разобрать на данных, где бот спрашивает зря,
    а где молчит там, где не надо.
    """
    if not ctx.allows(message.chat.id):
        return

    stats = await ctx.repo.detect_stats(message.chat.id)
    if not stats:
        await message.reply(
            "Журнал пуст. Я записываю сюда каждое сообщение, где встретилась "
            "похожая на деньги сумма — и то, на что среагировал, и то, что "
            "пропустил. Загляни через недельку."
        )
        return

    asked_yes = sum(r["cnt"] for r in stats if r.get("answer") == "yes")
    asked_no = sum(r["cnt"] for r in stats if r.get("answer") == "no")
    timeouts = sum(r["cnt"] for r in stats if r.get("answer") == "timeout")
    skipped = sum(
        r["cnt"] for r in stats
        if r["reason"].startswith(("no_keyword", "veto"))
    )
    total = sum(r["cnt"] for r in stats)

    lines = [
        f"<b>Журнал детектора</b> — {with_plural(total, 'сообщение', 'сообщения', 'сообщений')} с суммой",
        "",
        f"✅ подтвердили: {asked_yes}",
        f"✖️ отказались: {asked_no}",
        f"⏳ не ответили: {timeouts}",
        f"🤐 промолчал сам: {skipped}",
    ]
    if asked_yes + asked_no:
        precision = asked_yes / (asked_yes + asked_no) * 100
        lines.append(f"\nПопадание в цель: {precision:.0f}%")

    rows = await ctx.repo.detect_log(message.chat.id)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["дата", "автор", "текст", "вердикт", "суммы", "спросил", "ответ"])
    for row in rows:
        writer.writerow([
            row["at"].isoformat() if hasattr(row["at"], "isoformat") else row["at"],
            row.get("author_name", ""),
            row.get("text", ""),
            row.get("reason", ""),
            row.get("amounts", ""),
            "да" if row.get("asked") else "нет",
            row.get("answer") or "",
        ])

    await message.reply_document(
        BufferedInputFile(
            buffer.getvalue().encode("utf-8-sig"), filename="detect-log.csv"
        ),
        caption="\n".join(lines),
    )


@router.message(Command("lexicon", "словарь"))
async def cmd_lexicon(message: Message, ctx: Context) -> None:
    if not ctx.allows(message.chat.id):
        return
    rows = await ctx.repo.lexicon_stats(message.chat.id)
    active = await ctx.repo.learned_words(message.chat.id, ctx.settings.learn_threshold)
    if not rows:
        await message.reply(
            "Пока ничего не выучил. Я запоминаю слова, когда вы подтверждаете траты, "
            "которых нет в моём словаре."
        )
        return

    lines = ["<b>Что я выучил в этом чате</b>"]
    for row in rows:
        mark = "✅" if row["word"] in active else "🕓"
        category = row.get("category")
        label = CATEGORY_TITLE.get(category and _category(category), "?")
        lines.append(
            f"{mark} <code>{row['word']}</code> — {label} "
            f"(+{row['votes_yes']} / −{row['votes_no']})"
        )
    await message.reply("\n".join(lines))


def _category(value: str):
    from ..models import Category

    try:
        return Category(value)
    except ValueError:
        return None


@router.message(Command("export", "выгрузка"))
async def cmd_export(message: Message, ctx: Context) -> None:
    if not ctx.allows(message.chat.id):
        return
    start = datetime(2000, 1, 1, tzinfo=timezone.utc)
    end = datetime(2100, 1, 1, tzinfo=timezone.utc)
    expenses = await ctx.repo.expenses_between(message.chat.id, start, end)
    if not expenses:
        await message.reply("Пока нечего выгружать")
        return

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["дата", "кто", "категория", "сумма", "описание", "литры", "пробег"])
    for expense in expenses:
        local = expense.spent_at.astimezone(timeutil.tz(ctx.settings.timezone))
        writer.writerow(
            [
                local.strftime("%Y-%m-%d %H:%M"),
                expense.author_name,
                CATEGORY_TITLE[expense.category],
                f"{expense.amount:.2f}",
                expense.note,
                f"{expense.liters:.2f}" if expense.liters else "",
                expense.odometer or "",
            ]
        )

    data = buffer.getvalue().encode("utf-8-sig")
    await message.answer_document(
        BufferedInputFile(data, filename="car-expenses.csv"),
        caption=f"{with_plural(len(expenses), 'трата', 'траты', 'трат')} за всё время",
    )


@router.message(Command("chatid"))
async def cmd_chat_id(message: Message) -> None:
    await message.reply(f"<code>{message.chat.id}</code>")


async def _quiet_delete(ctx: Context, chat_id: int, message_id: int) -> None:
    try:
        await ctx.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:  # noqa: BLE001
        pass
