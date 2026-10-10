"""Отметка замены в ТО: из траты, из напоминания и из личного кабинета.

Один и тот же вопрос с галочками и пробегом живёт в трёх местах:

- в общем чате — вопрос «На тачку?» после «Да» превращается в «Отметить
  в ТО?», если в трате нашлось масло, фильтры, колодки и прочее;
- в личке — под напоминанием «пора менять»;
- в личке — кнопкой «Заменил» в карточке позиции.

Пробег подставляется из сообщения, а если его там нет — прикидывается по
темпу езды. Не совпал — человек отвечает на сообщение своим числом.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .. import garage, mileage, service
from ..services import Context

log = logging.getLogger(__name__)
router = Router(name="service_flow")

PREFIX = "sa"


# --- Как спросить ------------------------------------------------------------


def render(ask: dict[str, Any]) -> tuple[str, InlineKeyboardMarkup]:
    """Текст и кнопки вопроса по его текущему состоянию."""
    km = ask.get("km")
    tilde = "~" if ask.get("km_estimated") else ""
    lines = [f"🔧 Отметить замену в ТО · {ask['car_title']}?"]
    if km:
        hint = " (прикинул по темпу езды)" if ask.get("km_estimated") else ""
        lines.append(f"Пробег: {tilde}{_km(km)}{hint}")
    else:
        lines.append("Пробег не знаю — укажи его или запишу только дату")
    if ask.get("awaiting_km"):
        lines += ["", "✏️ Ответь на это сообщение пробегом, например <code>187950</code>"]

    selected = set(ask.get("selected") or [])
    toggles = [
        InlineKeyboardButton(
            text=f"{'☑' if kind in selected else '☐'} {_title(kind)}",
            callback_data=f"{PREFIX}:t{index}",
        )
        for index, kind in enumerate(ask.get("offered") or [])
    ]
    rows = [toggles[i : i + 2] for i in range(0, len(toggles), 2)]

    save = f"✅ Записать на {tilde}{_km(km)}" if km else "✅ Записать по дате"
    rows.append([InlineKeyboardButton(text=save, callback_data=f"{PREFIX}:ok")])
    rows.append(
        [
            InlineKeyboardButton(text="✏️ Другой пробег", callback_data=f"{PREFIX}:km"),
            InlineKeyboardButton(text="✖️ Не надо", callback_data=f"{PREFIX}:no"),
        ]
    )
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def offer_in_place(
    ctx: Context,
    *,
    chat_id: int,
    message_id: int,
    owner_id: int,
    text: str,
    odometer: int | None,
    at: datetime,
) -> bool:
    """Превращает вопрос «На тачку?» в «Отметить в ТО?». False — если не о чем."""
    ask = await _draft(ctx, chat_id, owner_id, text, odometer, at)
    if ask is None:
        return False
    body, keyboard = render(ask)
    try:
        await ctx.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=body, reply_markup=keyboard
        )
    except Exception as error:  # noqa: BLE001
        log.warning("не смог превратить вопрос %s в ТО: %s", message_id, error)
        return False
    await _store(ctx, chat_id, message_id, ask)
    return True


async def offer_as_reply(
    ctx: Context,
    message: Message,
    *,
    text: str,
    odometer: int | None,
) -> None:
    """Для явной формы «на тачку …»: вопроса не было, задаём отдельным ответом."""
    ask = await _draft(
        ctx, message.chat.id, message.from_user.id, text, odometer, message.date
    )
    if ask is None:
        return
    body, keyboard = render(ask)
    sent = await message.reply(body, reply_markup=keyboard)
    await _store(ctx, message.chat.id, sent.message_id, ask)


async def offer_in_dm(
    ctx: Context,
    *,
    group_id: int,
    owner_id: int,
    offered: list[str],
    selected: list[str],
    lead: str | None = None,
) -> bool:
    """Вопрос в личке: под напоминанием или по кнопке «Заменил»."""
    now = datetime.now().astimezone()
    car = await _car(ctx, group_id, owner_id)
    if car is None:
        return False
    km, estimated = await _known_km(ctx, group_id, owner_id, None, now)
    ask = {
        "group_id": group_id,
        "owner_id": owner_id,
        **car,
        "offered": offered,
        "selected": selected,
        "km": km,
        "km_estimated": estimated,
        "awaiting_km": False,
        "at": now,
    }
    body, keyboard = render(ask)
    if lead:
        body = f"{lead}\n\n{body}"
    try:
        sent = await ctx.bot.send_message(owner_id, body, reply_markup=keyboard)
    except Exception as error:  # noqa: BLE001
        # Личка не открыта — бот не может написать первым
        log.debug("не смог написать %s в личку: %s", owner_id, error)
        return False
    await _store(ctx, owner_id, sent.message_id, ask)
    return True


# --- Ответы ------------------------------------------------------------------


@router.callback_query(F.data.startswith(f"{PREFIX}:"))
async def on_button(call: CallbackQuery, ctx: Context) -> None:
    if call.message is None:
        return
    chat_id, message_id = call.message.chat.id, call.message.message_id
    ask = await ctx.repo.get_service_ask(chat_id, message_id)
    if ask is None:
        await call.answer("Вопрос протух. Отметить замену можно в личке: /to", show_alert=True)
        await _forget_keyboard(ctx, chat_id, message_id)
        return
    if call.from_user.id != ask["owner_id"]:
        await call.answer("Это не твоя машина 👀", show_alert=True)
        return

    action = call.data.split(":", 1)[1]

    if action.startswith("t") and action[1:].isdigit():
        offered = ask.get("offered") or []
        index = int(action[1:])
        if index < len(offered):
            selected = list(ask.get("selected") or [])
            kind = offered[index]
            selected.remove(kind) if kind in selected else selected.append(kind)
            ask["selected"] = selected
            await ctx.repo.update_service_ask(chat_id, message_id, selected=selected)
        await call.answer()
        await _redraw(ctx, chat_id, message_id, ask)
        return

    if action == "km":
        ask["awaiting_km"] = True
        await ctx.repo.update_service_ask(chat_id, message_id, awaiting_km=True)
        await call.answer("Ответь на сообщение числом")
        await _redraw(ctx, chat_id, message_id, ask)
        return

    if action == "no":
        await ctx.repo.drop_service_ask(chat_id, message_id)
        await call.answer("Ок, в ТО не пишу")
        if call.message.chat.type == "private":
            await _replace(ctx, chat_id, message_id, "Ок, ничего не отмечаю.")
        else:
            await _delete(ctx, chat_id, message_id)
        return

    if action == "ok":
        if not ask.get("selected"):
            await call.answer("Отметь галочкой, что именно поменяли", show_alert=True)
            return
        summary = await _record(ctx, ask)
        await ctx.repo.drop_service_ask(chat_id, message_id)
        await call.answer("Записал в ТО")
        await _replace(ctx, chat_id, message_id, summary)
        return

    await call.answer()


async def _is_service_reply(message: Message, ctx: Context) -> dict[str, Any] | bool:
    """Ответ числом на вопрос «Отметить в ТО?» — от хозяина машины."""
    if message.reply_to_message is None or not message.text or message.from_user is None:
        return False
    ask = await ctx.repo.get_service_ask(message.chat.id, message.reply_to_message.message_id)
    if ask is None or message.from_user.id != ask["owner_id"]:
        return False
    return {"ask": ask}


@router.message(_is_service_reply)
async def on_km_reply(message: Message, ctx: Context, ask: dict[str, Any]) -> None:
    km = service.parse_odometer(message.text or "")
    if km is None:
        if ask.get("awaiting_km"):
            await message.reply("Нужно число — пробег в километрах, например 187950")
        return

    question_id = message.reply_to_message.message_id
    at = await _moment_of(ctx, ask, km)
    ask.update(km=km, km_estimated=False, awaiting_km=False, at=at)
    await ctx.repo.update_service_ask(
        message.chat.id, question_id, km=km, km_estimated=False, awaiting_km=False, at=at
    )
    await _redraw(ctx, message.chat.id, question_id, ask)


async def _moment_of(ctx: Context, ask: dict[str, Any], km: int) -> datetime:
    """Когда была замена на этом пробеге — и не замер ли это заодно.

    Под тратой в чате пробег назван на момент траты — это настоящий замер,
    он уточнит и оценку темпа. В личке «заменил на 187 500» часто говорят
    задним числом, уже на 188 400: тогда это не текущий одометр, а прошлое,
    и дату замены восстанавливаем по замерам вокруг этого пробега.
    """
    group_id, owner_id = ask["group_id"], ask["owner_id"]
    raw = await ctx.repo.odometer_readings(group_id, owner_id)
    readings = [mileage.Reading(at=moment, km=value) for moment, value in raw]
    latest = mileage.collect(readings)[-1].km if readings else 0
    in_chat = ask.get("msg_chat", group_id) == group_id

    if in_chat or km >= latest:
        await ctx.repo.add_odometer(
            chat_id=group_id, owner_id=owner_id, km=km, at=ask["at"], car=ask["car"]
        )
        return ask["at"]
    return mileage.when(readings, km) or ask["at"]


# --- Кухня -------------------------------------------------------------------


async def _draft(
    ctx: Context,
    chat_id: int,
    owner_id: int,
    text: str,
    odometer: int | None,
    at: datetime,
) -> dict[str, Any] | None:
    sure, maybe = service.detect_kinds(text)
    if not sure and not maybe:
        return None
    car = await _car(ctx, chat_id, owner_id)
    if car is None:
        return None
    km, estimated = await _known_km(ctx, chat_id, owner_id, odometer, at)
    return {
        "group_id": chat_id,
        "owner_id": owner_id,
        **car,
        "offered": sure + maybe,
        "selected": list(sure),
        "km": km,
        "km_estimated": estimated,
        "awaiting_km": False,
        "at": at,
    }


async def _store(ctx: Context, msg_chat: int, message_id: int, ask: dict[str, Any]) -> None:
    await ctx.repo.save_service_ask(
        msg_chat=msg_chat,
        message_id=message_id,
        group_id=ask["group_id"],
        owner_id=ask["owner_id"],
        car=ask["car"],
        car_slug=ask["car_slug"],
        car_title=ask["car_title"],
        offered=ask["offered"],
        selected=ask["selected"],
        km=ask["km"],
        km_estimated=ask["km_estimated"],
        at=ask["at"],
    )


async def _car(ctx: Context, chat_id: int, owner_id: int) -> dict[str, Any] | None:
    for row in await ctx.repo.cars(chat_id, owner_id):
        if row.get("active", True):
            return {
                "car": row["id"],
                "car_slug": garage.slug_of(row["id"]),
                "car_title": row["title"],
            }
    return None


async def _known_km(
    ctx: Context, chat_id: int, owner_id: int, odometer: int | None, at: datetime
) -> tuple[int | None, bool]:
    """Пробег на момент замены: из сообщения, а иначе — прикидка по темпу."""
    if odometer:
        return odometer, False
    found = await garage.load_one(ctx.repo, chat_id, owner_id, now=at)
    if found is None or found.mileage is None:
        return None, False
    estimate = found.mileage
    # Голый замер не того же дня — тоже прикидка: машина с тех пор ездила
    fresh = abs((at - estimate.known_at).total_seconds()) < 86400
    return estimate.km, estimate.is_estimate or not fresh


async def _record(ctx: Context, ask: dict[str, Any]) -> str:
    """Пишет замены и возвращает итог: что отмечено и когда следующая."""
    group_id, owner_id = ask["group_id"], ask["owner_id"]
    plans = {
        row["kind"]: row
        for row in await ctx.repo.service_plans(group_id, owner_id, ask["car_slug"])
    }
    km = ask.get("km")
    at = ask["at"]
    tilde = "~" if ask.get("km_estimated") else ""

    lines = [f"🔧 Записал в ТО · {ask['car_title']}"]
    for kind in ask["selected"]:
        existing = plans.get(kind) or {}
        default_km, default_days = service.defaults_for(kind)
        interval_km = existing.get("interval_km") or default_km
        interval_days = existing.get("interval_days") or default_days
        await ctx.repo.mark_serviced(
            chat_id=group_id,
            owner_id=owner_id,
            car=ask["car"],
            car_slug=ask["car_slug"],
            car_title=ask["car_title"],
            kind=kind,
            km=km,
            at=at,
            interval_km=interval_km,
            interval_days=interval_days,
        )
        lines.append(f"· {_title(kind)} — {_next_hint(km, at, interval_km, interval_days, tilde)}")
    log.info(
        "замена отмечена: чат=%s владелец=%s %s на %s км",
        group_id, owner_id, ask["selected"], km,
    )
    return "\n".join(lines)


def _next_hint(
    km: int | None,
    at: datetime,
    interval_km: int | None,
    interval_days: int | None,
    tilde: str,
) -> str:
    parts: list[str] = []
    if km and interval_km:
        parts.append(f"{tilde}{_km(km + interval_km)}")
    if interval_days:
        parts.append(f"{(at + timedelta(days=interval_days)):%d.%m.%Y}")
    if not parts:
        return "отмечено"
    return "следующая " + " или ".join(parts)


def _title(kind: str) -> str:
    found = service.BY_KEY.get(kind)
    return found.title if found else kind


def _km(value: int | None) -> str:
    return f"{value:,} км".replace(",", " ") if value else "—"


async def _redraw(ctx: Context, chat_id: int, message_id: int, ask: dict[str, Any]) -> None:
    body, keyboard = render(ask)
    try:
        await ctx.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=body, reply_markup=keyboard
        )
    except Exception as error:  # noqa: BLE001
        # Телеграм ругается, если ничего не поменялось, — не беда
        log.debug("не смог перерисовать вопрос ТО %s: %s", message_id, error)


async def _replace(ctx: Context, chat_id: int, message_id: int, text: str) -> None:
    try:
        await ctx.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text, reply_markup=None
        )
    except Exception as error:  # noqa: BLE001
        log.debug("не смог обновить вопрос ТО %s: %s", message_id, error)


async def _forget_keyboard(ctx: Context, chat_id: int, message_id: int) -> None:
    try:
        await ctx.bot.edit_message_reply_markup(
            chat_id=chat_id, message_id=message_id, reply_markup=None
        )
    except Exception as error:  # noqa: BLE001
        log.debug("не смог убрать кнопки %s: %s", message_id, error)


async def _delete(ctx: Context, chat_id: int, message_id: int) -> None:
    try:
        await ctx.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as error:  # noqa: BLE001
        log.debug("не смог удалить вопрос ТО %s: %s", message_id, error)
