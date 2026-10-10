"""Личный кабинет: настройка интервалов ТО и подробный просмотр трат.

Всё это в общий чат не помещается — там был бы спам. Поэтому в группе стоит
только команда-приглашение, а разговор идёт один на один.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .. import garage, service, timeutil
from ..formatting import money
from ..models import CATEGORY_EMOJI, CATEGORY_SHORT, Category
from ..services import Context
from . import service_flow

log = logging.getLogger(__name__)
router = Router(name="private")

PAGE_SIZE = 8
HISTORY_MONTHS = 6


class Wizard(StatesGroup):
    """Шаги, на которых бот ждёт от человека текст, а не нажатие кнопки."""

    interval_km = State()
    interval_days = State()
    value_km = State()
    value_date = State()
    monthly_km = State()


# --- Вход ---------------------------------------------------------------------


@router.message(CommandStart(), F.chat.type == "private")
async def dm_start(message: Message, ctx: Context, state: FSMContext) -> None:
    await state.clear()
    if message.from_user is None:
        return
    chat_id = await _resolve_chat(ctx, message.from_user.id)
    if chat_id is None:
        await message.answer(
            "Мы ещё не знакомы. Добавь меня в чат с друзьями, запиши там любую "
            "трату — и возвращайся, тут будет твой личный кабинет."
        )
        return
    await ctx.repo.bind_dm(message.from_user.id, chat_id)
    await message.answer(_menu_text(), reply_markup=_menu_keyboard())


@router.callback_query(F.data == "dm:menu")
async def back_to_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _swap(call, _menu_text(), _menu_keyboard())


def _menu_text() -> str:
    return (
        "<b>Личный кабинет</b>\n\n"
        "Тут можно настроить, что и когда менять на машине, и полистать свои "
        "траты подробнее, чем показывает картинка."
    )


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔧 Что и когда менять", callback_data="dm:cars")],
            [InlineKeyboardButton(text="📊 Мои траты", callback_data="dm:months")],
        ]
    )


async def _resolve_chat(ctx: Context, tg_id: int) -> int | None:
    """Какой чат обслуживаем: запомненный, а иначе единственный известный."""
    bound = await ctx.repo.dm_chat(tg_id)
    if bound is not None:
        return bound
    chats = await ctx.repo.chats_of_person(tg_id)
    return chats[0] if chats else None


# --- Выбор машины -------------------------------------------------------------


@router.callback_query(F.data == "dm:cars")
async def choose_car(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    chat_id = await _resolve_chat(ctx, call.from_user.id)
    if chat_id is None:
        await call.answer("Сначала запиши хоть одну трату в общем чате", show_alert=True)
        return

    cars = [c for c in await ctx.repo.cars(chat_id, call.from_user.id)]
    if not cars:
        await _swap(
            call,
            "Машин пока нет. Напиши в общем чате <code>/car Camry 2007</code> — "
            "и возвращайся.",
            _back_keyboard(),
        )
        return

    await state.update_data(chat_id=chat_id)
    rows = [
        [
            InlineKeyboardButton(
                text=f"🚗 {car['title']}", callback_data=f"dm:car:{index}"
            )
        ]
        for index, car in enumerate(cars)
    ]
    rows.append([InlineKeyboardButton(text="◀ Назад", callback_data="dm:menu")])
    await _swap(
        call, "Какая машина?", InlineKeyboardMarkup(inline_keyboard=rows)
    )


@router.callback_query(F.data.startswith("dm:car:"))
async def car_positions(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    data = await state.get_data()
    chat_id = data.get("chat_id") or await _resolve_chat(ctx, call.from_user.id)
    if chat_id is None:
        await call.answer("Не понял, про какой чат речь", show_alert=True)
        return

    index = int(call.data.rsplit(":", 1)[1])
    cars = await ctx.repo.cars(chat_id, call.from_user.id)
    if index >= len(cars):
        await call.answer("Машина потерялась, начни заново", show_alert=True)
        return

    car = cars[index]
    slug = garage.slug_of(car["id"])
    await state.update_data(
        chat_id=chat_id, car_slug=slug, car_title=car["title"], car_index=index
    )
    await _show_positions(call, ctx, state)


async def _show_positions(
    call: CallbackQuery, ctx: Context, state: FSMContext
) -> None:
    data = await state.get_data()
    chat_id, slug, title = data["chat_id"], data["car_slug"], data["car_title"]

    rows = await ctx.repo.service_plans(chat_id, call.from_user.id, slug)
    configured = {row["kind"] for row in rows}

    keyboard: list[list[InlineKeyboardButton]] = []
    for kind in service.CATALOG:
        mark = "✅" if kind.key in configured else "➖"
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {kind.title}", callback_data=f"dm:kind:{kind.key}"
                )
            ]
        )
    keyboard.append(
        [InlineKeyboardButton(text="🛣 Средний пробег в месяц", callback_data="dm:monthly")]
    )
    keyboard.append([InlineKeyboardButton(text="◀ Назад", callback_data="dm:cars")])

    text = (
        f"<b>{title}</b>\n\n"
        f"Настроено позиций: {len(configured)} из {len(service.CATALOG)}.\n"
        "Отмечай, что отслеживать — я предупрежу заранее."
    )
    await _swap(call, text, InlineKeyboardMarkup(inline_keyboard=keyboard))


# --- Одна позиция -------------------------------------------------------------


@router.callback_query(F.data.startswith("dm:kind:"))
async def open_position(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    key = call.data.rsplit(":", 1)[1]
    await state.update_data(kind=key)
    await _show_position(call, ctx, state)


async def _show_position(
    call: CallbackQuery, ctx: Context, state: FSMContext
) -> None:
    data = await state.get_data()
    kind = service.BY_KEY.get(data["kind"])
    if kind is None:
        await call.answer("Неизвестная позиция", show_alert=True)
        return

    plan = await _load_plan(ctx, state, call.from_user.id)
    lines = [f"<b>{kind.title}</b>", ""]

    if plan.interval_km or plan.interval_days:
        parts = []
        if plan.interval_km:
            parts.append(f"{plan.interval_km:,} км".replace(",", " "))
        if plan.interval_days:
            parts.append(f"{plan.interval_days} дн.")
        lines.append("Интервал: " + " или ".join(parts))
    else:
        lines.append("Интервал не задан")

    if plan.last_km is not None:
        lines.append(f"Меняли на {plan.last_km:,} км".replace(",", " "))
    if plan.last_at is not None:
        lines.append(f"Меняли {plan.last_at:%d.%m.%Y}")
    if plan.next_km is not None:
        lines.append(f"Следующая на {plan.next_km:,} км".replace(",", " "))
    if plan.next_at is not None:
        lines.append(f"Следующая {plan.next_at:%d.%m.%Y}")

    if not plan.configured:
        lines += [
            "",
            "Чтобы я начал следить, нужен интервал и одна точка отсчёта — "
            "либо когда меняли в прошлый раз, либо когда планируешь в следующий.",
        ]

    keyboard = [
        [InlineKeyboardButton(text="⏱ Интервал", callback_data="dm:iv")],
        [
            InlineKeyboardButton(text="◀ Меняли", callback_data="dm:last"),
            InlineKeyboardButton(text="Следующая ▶", callback_data="dm:next"),
        ],
    ]
    keyboard.append([InlineKeyboardButton(text="✅ Заменил", callback_data="dm:done")])
    if plan.configured:
        keyboard.append(
            [InlineKeyboardButton(text="🗑 Не следить", callback_data="dm:drop")]
        )
    keyboard.append([InlineKeyboardButton(text="◀ Назад", callback_data="dm:back")])

    await _swap(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard))


@router.callback_query(F.data == "dm:back")
async def back_to_positions(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    await state.set_state(None)
    await _show_positions(call, ctx, state)


@router.callback_query(F.data == "dm:iv")
async def ask_interval(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    data = await state.get_data()
    kind = service.BY_KEY[data["kind"]]
    rows: list[list[InlineKeyboardButton]] = []

    if kind.default_km:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Обычно — {kind.default_km:,} км".replace(",", " "),
                    callback_data="dm:ivdef",
                )
            ]
        )
        rows.append(
            [InlineKeyboardButton(text="Свой пробег", callback_data="dm:ivkm")]
        )
    if kind.default_days:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Обычно — раз в {_years(kind.default_days)}",
                    callback_data="dm:ivdefd",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Свой срок в месяцах", callback_data="dm:ivd")])
    rows.append([InlineKeyboardButton(text="◀ Назад", callback_data="dm:kindback")])

    await _swap(
        call,
        f"<b>{kind.title}</b>\n\nКак часто менять?",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data == "dm:kindback")
async def back_to_position(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    await state.set_state(None)
    await _show_position(call, ctx, state)


@router.callback_query(F.data.in_({"dm:ivdef", "dm:ivdefd"}))
async def set_default_interval(
    call: CallbackQuery, ctx: Context, state: FSMContext
) -> None:
    data = await state.get_data()
    kind = service.BY_KEY[data["kind"]]
    if call.data == "dm:ivdef":
        await _save(ctx, state, call.from_user.id, interval_km=kind.default_km)
    else:
        await _save(ctx, state, call.from_user.id, interval_days=kind.default_days)
    await _show_position(call, ctx, state)


@router.callback_query(F.data == "dm:ivkm")
async def ask_interval_km(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Wizard.interval_km)
    await _swap(
        call,
        "Через сколько километров менять? Напиши числом — например "
        "<code>10000</code> или <code>10к</code>.",
        _cancel_keyboard(),
    )


@router.callback_query(F.data == "dm:ivd")
async def ask_interval_days(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Wizard.interval_days)
    await _swap(
        call,
        "Раз во сколько месяцев менять? Напиши числом — например <code>12</code>.",
        _cancel_keyboard(),
    )


@router.callback_query(F.data.in_({"dm:last", "dm:next"}))
async def ask_point(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(point="last" if call.data == "dm:last" else "next")
    word = "меняли в прошлый раз" if call.data == "dm:last" else "менять в следующий"
    await _swap(
        call,
        f"Как задать, когда {word}?",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🛣 По пробегу", callback_data="dm:pkm")],
                [InlineKeyboardButton(text="📅 По дате", callback_data="dm:pdate")],
                [InlineKeyboardButton(text="◀ Назад", callback_data="dm:kindback")],
            ]
        ),
    )


@router.callback_query(F.data == "dm:pkm")
async def ask_value_km(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Wizard.value_km)
    await _swap(call, "Напиши пробег числом, например <code>123456</code>.", _cancel_keyboard())


@router.callback_query(F.data == "dm:pdate")
async def ask_value_date(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Wizard.value_date)
    await _swap(call, "Напиши дату в виде <code>12.05.2026</code>.", _cancel_keyboard())


@router.callback_query(F.data == "dm:monthly")
async def ask_monthly(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Wizard.monthly_km)
    await _swap(
        call,
        "Сколько примерно километров в месяц наезжаешь?\n\n"
        "Это нужно, только пока я не набрал своих замеров пробега — "
        "потом буду считать по ним.",
        _cancel_keyboard(),
    )


@router.callback_query(F.data == "dm:done")
async def mark_done(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    """Заменил — тот же вопрос, что и в чате: пробег прикинут, можно поправить."""
    data = await state.get_data()
    sent = await service_flow.offer_in_dm(
        ctx,
        group_id=data["chat_id"],
        owner_id=call.from_user.id,
        offered=[data["kind"]],
        selected=[data["kind"]],
    )
    await call.answer() if sent else await call.answer("Сначала заведи машину", show_alert=True)


@router.callback_query(F.data == "dm:drop")
async def drop_position(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    data = await state.get_data()
    await ctx.repo.drop_service(
        data["chat_id"], call.from_user.id, data["car_slug"], data["kind"]
    )
    await _show_positions(call, ctx, state)


# --- Ввод текстом -------------------------------------------------------------


@router.message(Wizard.interval_km, F.chat.type == "private")
async def got_interval_km(message: Message, ctx: Context, state: FSMContext) -> None:
    value = service.parse_interval_km(message.text or "")
    if value is None:
        await message.answer("Не понял число. Попробуй ещё раз, например <code>10000</code>.")
        return
    await _save(ctx, state, message.from_user.id, interval_km=value)
    await state.set_state(None)
    await _reply_position(message, ctx, state, f"Интервал: {value:,} км".replace(",", " "))


@router.message(Wizard.interval_days, F.chat.type == "private")
async def got_interval_days(message: Message, ctx: Context, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= 120:
        await message.answer("Напиши число месяцев, например <code>12</code>.")
        return
    await _save(ctx, state, message.from_user.id, interval_days=int(raw) * 30)
    await state.set_state(None)
    await _reply_position(message, ctx, state, f"Интервал: раз в {raw} мес.")


@router.message(Wizard.value_km, F.chat.type == "private")
async def got_value_km(message: Message, ctx: Context, state: FSMContext) -> None:
    raw = (message.text or "").replace(" ", "").replace(" ", "")
    if not raw.isdigit() or not 1 <= int(raw) <= 3_000_000:
        await message.answer("Напиши пробег числом, например <code>123456</code>.")
        return
    data = await state.get_data()
    field = "last_km" if data.get("point") == "last" else "next_km"
    await _save(ctx, state, message.from_user.id, **{field: int(raw)})
    await state.set_state(None)
    await _reply_position(message, ctx, state, "Записал")


@router.message(Wizard.value_date, F.chat.type == "private")
async def got_value_date(message: Message, ctx: Context, state: FSMContext) -> None:
    parsed = _parse_date(message.text or "")
    if parsed is None:
        await message.answer("Не разобрал дату. Формат такой: <code>12.05.2026</code>.")
        return
    data = await state.get_data()
    field = "last_at" if data.get("point") == "last" else "next_at"
    await _save(ctx, state, message.from_user.id, **{field: parsed})
    await state.set_state(None)
    await _reply_position(message, ctx, state, "Записал")


@router.message(Wizard.monthly_km, F.chat.type == "private")
async def got_monthly(message: Message, ctx: Context, state: FSMContext) -> None:
    raw = (message.text or "").replace(" ", "").replace(" ", "")
    if not raw.isdigit() or not 50 <= int(raw) <= 20_000:
        await message.answer("Напиши число километров в месяц, например <code>1500</code>.")
        return
    data = await state.get_data()
    cars = await ctx.repo.cars(data["chat_id"], message.from_user.id)
    index = data.get("car_index", 0)
    if index < len(cars):
        await ctx.repo.set_monthly_km(cars[index]["id"], int(raw))
    await state.set_state(None)
    await message.answer(
        f"Записал: около {int(raw):,} км в месяц.".replace(",", " "),
        reply_markup=_back_keyboard("dm:cars"),
    )


# --- Просмотр трат ------------------------------------------------------------


@router.callback_query(F.data == "dm:months")
async def choose_month(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    chat_id = await _resolve_chat(ctx, call.from_user.id)
    if chat_id is None:
        await call.answer("Сначала запиши трату в общем чате", show_alert=True)
        return
    await state.update_data(chat_id=chat_id)

    now = timeutil.now(ctx.settings.timezone)
    rows: list[list[InlineKeyboardButton]] = []
    for offset in range(0, HISTORY_MONTHS):
        year, month = timeutil.add_months(now.year, now.month, -offset)
        rows.append(
            [
                InlineKeyboardButton(
                    text=timeutil.month_title(year, month).title(),
                    callback_data=f"dm:mon:{year}:{month}:all:0",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="◀ Назад", callback_data="dm:menu")])
    await _swap(call, "Какой месяц показать?", InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("dm:mon:"))
async def show_expenses(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    _, _, year, month, category, page = call.data.split(":")
    year, month, page = int(year), int(month), int(page)

    data = await state.get_data()
    chat_id = data.get("chat_id") or await _resolve_chat(ctx, call.from_user.id)
    if chat_id is None:
        await call.answer("Не понял, про какой чат речь", show_alert=True)
        return

    start, end = timeutil.month_range(year, month, ctx.settings.timezone)
    items = await ctx.repo.expenses_between(
        chat_id, start, end, author_id=call.from_user.id
    )
    if category != "all":
        items = [e for e in items if e.category == Category(category)]
    items.sort(key=lambda e: e.spent_at, reverse=True)

    total = sum((e.amount for e in items), Decimal(0))
    header = (
        f"<b>{timeutil.month_title(year, month).title()}</b>\n"
        f"{len(items)} трат на {money(total)}"
    )

    if not items:
        await _swap(
            call,
            header + "\n\nЗа этот месяц ничего не записано.",
            _expense_keyboard(year, month, category, page, has_more=False),
        )
        return

    chunk = items[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    lines = [header, ""]
    for expense in chunk:
        when = timeutil.format_date(expense.spent_at, ctx.settings.timezone)
        note = expense.note or CATEGORY_SHORT[expense.category]
        emoji = CATEGORY_EMOJI[expense.category]
        lines.append(f"{emoji} <b>{money(expense.amount)}</b> · {note} · {when}")

    has_more = (page + 1) * PAGE_SIZE < len(items)
    await _swap(
        call,
        "\n".join(lines),
        _expense_keyboard(year, month, category, page, has_more),
    )


def _expense_keyboard(
    year: int, month: int, category: str, page: int, has_more: bool
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    filters = [
        InlineKeyboardButton(
            text=("· Все ·" if category == "all" else "Все"),
            callback_data=f"dm:mon:{year}:{month}:all:0",
        )
    ]
    for item in Category:
        mark = "·" if category == item.value else ""
        filters.append(
            InlineKeyboardButton(
                text=f"{mark}{CATEGORY_EMOJI[item]}{mark}",
                callback_data=f"dm:mon:{year}:{month}:{item.value}:0",
            )
        )
    rows.append(filters)

    pager: list[InlineKeyboardButton] = []
    if page > 0:
        pager.append(
            InlineKeyboardButton(
                text="◀", callback_data=f"dm:mon:{year}:{month}:{category}:{page - 1}"
            )
        )
    if has_more:
        pager.append(
            InlineKeyboardButton(
                text="Ещё ▶", callback_data=f"dm:mon:{year}:{month}:{category}:{page + 1}"
            )
        )
    if pager:
        rows.append(pager)

    rows.append([InlineKeyboardButton(text="◀ К месяцам", callback_data="dm:months")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# --- Мелочи -------------------------------------------------------------------


def _years(days: int) -> str:
    if days >= 365 and days % 365 == 0:
        count = days // 365
        return f"{count} год" if count == 1 else f"{count} года"
    return f"{days // 30} мес."


def _parse_date(text: str) -> datetime | None:
    raw = text.strip().replace("/", ".").replace("-", ".")
    for pattern in ("%d.%m.%Y", "%d.%m.%y", "%m.%Y"):
        try:
            parsed = datetime.strptime(raw, pattern)
        except ValueError:
            continue
        return parsed.replace(tzinfo=timezone.utc)
    return None


async def _load_plan(
    ctx: Context, state: FSMContext, owner_id: int
) -> service.ServicePlan:
    data = await state.get_data()
    rows = await ctx.repo.service_plans(data["chat_id"], owner_id, data["car_slug"])
    for row in rows:
        if row["kind"] == data["kind"]:
            return garage.plan_from_row(row)
    return service.ServicePlan(
        chat_id=data["chat_id"],
        owner_id=owner_id,
        car_slug=data["car_slug"],
        car_title=data["car_title"],
        kind=data["kind"],
    )


async def _save(ctx: Context, state: FSMContext, owner_id: int, **fields) -> None:
    """Досохраняет одно поле позиции, не затирая остальные."""
    data = await state.get_data()
    plan = await _load_plan(ctx, state, owner_id)
    cars = await ctx.repo.cars(data["chat_id"], owner_id)
    index = data.get("car_index", 0)
    if index >= len(cars):
        return

    merged = {
        "interval_km": plan.interval_km,
        "interval_days": plan.interval_days,
        "last_km": plan.last_km,
        "last_at": plan.last_at,
        "next_km": plan.next_km,
        "next_at": plan.next_at,
    }
    merged.update(fields)
    # Новая «последняя замена» отменяет старый план «следующая на …»: иначе
    # план, назначенный до замены, считался важнее и замена выглядела
    # просроченной сразу после того, как её сделали
    if "last_km" in fields:
        merged["next_km"] = None
    if "last_at" in fields:
        merged["next_at"] = None

    # Интервал по умолчанию подставляем, чтобы одной точки хватило для расчёта
    kind = service.BY_KEY.get(data["kind"])
    if kind and merged["interval_km"] is None and merged["interval_days"] is None:
        merged["interval_km"], merged["interval_days"] = kind.default_km, kind.default_days

    await ctx.repo.upsert_service(
        chat_id=data["chat_id"],
        owner_id=owner_id,
        car=cars[index]["id"],
        car_slug=data["car_slug"],
        car_title=data["car_title"],
        kind=data["kind"],
        **merged,
    )
    if "last_km" in fields or "last_at" in fields:
        await ctx.repo.clear_reminder(data["chat_id"], owner_id, data["car_slug"], data["kind"])


async def _reply_position(
    message: Message, ctx: Context, state: FSMContext, note: str
) -> None:
    data = await state.get_data()
    kind = service.BY_KEY.get(data.get("kind", ""))
    title = kind.title if kind else "Позиция"
    await message.answer(
        f"{note}.\n\n<b>{title}</b> — что дальше?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⏱ Интервал", callback_data="dm:iv")],
                [
                    InlineKeyboardButton(text="◀ Меняли", callback_data="dm:last"),
                    InlineKeyboardButton(text="Следующая ▶", callback_data="dm:next"),
                ],
                [InlineKeyboardButton(text="◀ К списку", callback_data="dm:back")],
            ]
        ),
    )


def _back_keyboard(target: str = "dm:menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀ Назад", callback_data=target)]]
    )


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀ Отмена", callback_data="dm:kindback")]]
    )


async def _swap(call: CallbackQuery, text: str, keyboard: InlineKeyboardMarkup) -> None:
    """Перерисовывает то же сообщение — чтобы не плодить простыню в личке."""
    await call.answer()
    if call.message is None:
        return
    try:
        await call.message.edit_text(text, reply_markup=keyboard)
    except Exception:
        # Телеграм ругается, если текст и клавиатура не изменились — это не беда
        log.debug("не смог обновить сообщение в личке", exc_info=True)
