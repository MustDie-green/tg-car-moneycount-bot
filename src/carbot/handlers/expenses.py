"""Детект трат в чате и флоу подтверждения.

Кто-то пишет «мойка 500» → бот отвечает «На тачку?» с кнопками → автор
сообщения жмёт «Да» → бот удаляет вопрос, вешает 🫡 на исходное сообщение и
кладёт трату в базу.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReactionTypeEmoji,
)

from ..db import detection_from_json
from ..formatting import money
from ..models import CATEGORY_EMOJI, CATEGORY_TITLE, Category, Detection, ParseResult
from ..parsing import parse_message
from ..parsing.amount import extract_odometer
from .. import mileage
from ..services import Context
from . import service_flow

log = logging.getLogger(__name__)
router = Router(name="expenses")

CALLBACK_PREFIX = "e"
ACTION_YES = "yes"
ACTION_NO = "no"
CATEGORY_ACTIONS = {category.value: category for category in Category}
# Кнопка из старых вопросов, заданных до разделения «остального»
CATEGORY_ACTIONS["other"] = Category.CARE

SALUTE = "🫡"
# Реакция на голый замер пробега — отличается от подтверждённой траты
WRITTEN = "✍️"
FIRE = "🔥"
SKULL = "💀"


# --- Сообщения ---------------------------------------------------------------


# Детект трат — история про общий чат. В личке свой интерфейс, и ловить там
# суммы значило бы перебивать мастер настройки ТО на полуслове.
GROUP_TYPES = {"group", "supergroup"}


@router.message(F.chat.type.in_(GROUP_TYPES), F.text | F.caption)
async def on_message(message: Message, ctx: Context) -> None:
    if message.from_user is None or message.from_user.is_bot:
        return
    if not ctx.allows(message.chat.id):
        return
    # Пересланное — чужие слова: уведомления АЗС, посты каналов, цитаты.
    # Трату человек пишет сам.
    if message.forward_origin is not None or message.is_automatic_forward:
        return

    text = message.text or message.caption or ""
    learned = await ctx.learned(message.chat.id)
    result = parse_message(
        text,
        learned=learned,
        min_amount=ctx.settings.min_amount,
        max_amount=ctx.settings.max_amount,
    )
    if not result:
        # Суммы нет, но мог быть голый замер: «пробег 123456».
        # Это главный корм для оценки пробега, терять его жалко.
        await _maybe_odometer(message, ctx, text)
        await _journal(message, ctx, text, result, asked=False)
        return

    if await ctx.repo.has_pending(message.chat.id, message.message_id):
        return
    if await ctx.repo.expenses_by_message(message.chat.id, message.message_id):
        return

    # «на тачку 3000 гидрач» — человек уже сказал, что это про машину.
    # Переспрашивать «На тачку?» тут глупо, сразу пишем.
    if result.explicit and not any(d.needs_category for d in result.detections):
        await _save_now(message, ctx, result)
        await _journal(message, ctx, text, result, asked=False)
        return

    question, keyboard = build_question(result, message.message_id, ctx.settings.currency)
    sent = await message.reply(question, reply_markup=keyboard)
    await _journal(message, ctx, text, result, asked=True)

    await ctx.repo.touch_chat(message.chat.id, message.chat.title or "")
    await ctx.repo.save_pending(
        chat_id=message.chat.id,
        message_id=message.message_id,
        question_id=sent.message_id,
        author_id=message.from_user.id,
        author_name=display_name(message.from_user),
        username=message.from_user.username,
        detections=result.detections,
        unknown_lemmas=result.unknown_lemmas,
        spent_at=message.date,
        ttl_seconds=ctx.settings.pending_ttl,
    )


async def _journal(
    message: Message, ctx: Context, text: str, result: ParseResult, *, asked: bool
) -> None:
    """Складывает в журнал каждое сообщение, где мелькнула похожая на деньги сумма.

    Пишем и то, что отбросили: без отказов не понять, где детектор перестраховался.
    """
    if not result.seen_amounts:
        return
    try:
        await ctx.repo.log_detection(
            chat_id=message.chat.id,
            message_id=message.message_id,
            author_id=message.from_user.id,
            author_name=display_name(message.from_user),
            text=text,
            reason=result.reason,
            amounts=result.seen_amounts,
            asked=asked,
        )
    except Exception:
        # Журнал — вспомогательная штука, ронять из-за него разбор нельзя
        log.debug("не смог записать наблюдение детектора", exc_info=True)


async def _save_now(message: Message, ctx: Context, result: ParseResult) -> None:
    """Явная форма: пишем сразу, без вопроса, и вешаем реакцию."""
    await ctx.repo.touch_chat(message.chat.id, message.chat.title or "")
    await ctx.repo.save_expenses(
        chat_id=message.chat.id,
        author_id=message.from_user.id,
        author_name=display_name(message.from_user),
        message_id=message.message_id,
        detections=result.detections,
        spent_at=message.date,
        car=await ctx.repo.active_car(message.chat.id, message.from_user.id),
    )
    if result.unknown_lemmas:
        category = result.detections[0].category
        await ctx.repo.vote_words(
            message.chat.id, result.unknown_lemmas, category, positive=True
        )
        ctx.forget_lexicon(message.chat.id)

    total = sum((d.amount for d in result.detections), Decimal(0))
    if not await _react(ctx, message.chat.id, message.message_id, total):
        await message.reply(f"{SALUTE} {_summary(result.detections, ctx.settings.currency)}")
    await service_flow.offer_as_reply(
        ctx,
        message,
        text=" ".join(d.note for d in result.detections),
        odometer=next((d.odometer for d in result.detections if d.odometer), None),
    )
    log.info(
        "трата записана явной формой: чат=%s автор=%s %s",
        message.chat.id, message.from_user.id,
        _summary(result.detections, ctx.settings.currency),
    )


async def _maybe_odometer(message: Message, ctx: Context, text: str) -> None:
    """Записывает замер пробега из сообщения без суммы и молча помечает галочкой."""
    km = extract_odometer(text)
    if km is None:
        return
    # Голый замер пишется молча, поэтому проверяем его строже, чем замер
    # внутри траты: он должен продолжать пробег машины, а не падать на 1250
    raw = await ctx.repo.odometer_readings(message.chat.id, message.from_user.id)
    chain = mileage.collect([mileage.Reading(at=at, km=value) for at, value in raw])
    if chain and not mileage.continues(chain[-1], mileage.Reading(at=message.date, km=km)):
        log.info(
            "замер %s км не продолжает пробег %s км — не записываю", km, chain[-1].km
        )
        return

    await ctx.repo.upsert_person(
        message.chat.id, message.from_user.id, display_name(message.from_user)
    )
    car = await ctx.repo.active_car(message.chat.id, message.from_user.id)
    await ctx.repo.add_odometer(
        chat_id=message.chat.id,
        owner_id=message.from_user.id,
        km=km,
        at=message.date,
        car=car,
    )
    await ctx.repo.touch_chat(message.chat.id, message.chat.title or "")
    try:
        await ctx.bot.set_message_reaction(
            message.chat.id,
            message.message_id,
            reaction=[ReactionTypeEmoji(emoji=WRITTEN)],
        )
    except Exception:
        log.debug("не смог поставить реакцию на замер пробега", exc_info=True)


def display_name(user) -> str:
    parts = [user.first_name, user.last_name]
    name = " ".join(part for part in parts if part).strip()
    return name or (user.username or f"id{user.id}")


def build_question(
    result: ParseResult, message_id: int, currency: str
) -> tuple[str, InlineKeyboardMarkup]:
    needs_category = any(detection.needs_category for detection in result.detections)

    if needs_category:
        # При явной форме «на тачку …» переспрашивать сам факт незачем —
        # остаётся только уточнить категорию
        lead = "Куда записать?" if result.explicit else "На тачку?"
        text = f"{lead} {money(result.total, currency)}"
        buttons = [
            _button(f"{CATEGORY_EMOJI[category]} {CATEGORY_TITLE[category]}", message_id, category.value)
            for category in Category
        ]
        buttons.append(_button("✖️ Не на тачку", message_id, ACTION_NO))
        # По две кнопки в ряд: шесть штук в одну строку не влезают на телефоне
        rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    parts = [
        f"{CATEGORY_EMOJI[detection.category]} {money(detection.amount, currency)}"
        for detection in result.detections
    ]
    body = " + ".join(parts)
    if len(parts) > 1:
        body += f" = {money(result.total, currency)}"
    text = f"На тачку? {body}"
    rows = [
        [
            _button("✅ Да", message_id, ACTION_YES),
            _button("✖️ Нет", message_id, ACTION_NO),
        ]
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _button(label: str, message_id: int, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=label, callback_data=f"{CALLBACK_PREFIX}:{message_id}:{action}"
    )


# --- Ответ на вопрос ---------------------------------------------------------


@router.callback_query(F.data.startswith(f"{CALLBACK_PREFIX}:"))
async def on_answer(callback: CallbackQuery, ctx: Context) -> None:
    if callback.message is None:
        return
    chat_id = callback.message.chat.id
    try:
        _, raw_message_id, action = callback.data.split(":", 2)
        message_id = int(raw_message_id)
    except ValueError:
        await callback.answer()
        return

    pending = await ctx.repo.get_pending(chat_id, message_id)
    if pending is None:
        await callback.answer("Этот вопрос уже протух")
        await _delete(ctx, chat_id, callback.message.message_id)
        return

    if callback.from_user.id != pending["author_id"]:
        await callback.answer("Это не твоя трата, руки убрал 👀", show_alert=True)
        return

    payload = pending["payload"]
    detections = [detection_from_json(item) for item in payload["detections"]]
    unknown = payload.get("unknown_lemmas", [])

    if action == ACTION_NO:
        await ctx.repo.vote_words(chat_id, unknown, None, positive=False)
        ctx.forget_lexicon(chat_id)
        await ctx.repo.drop_pending(chat_id, message_id)
        await _delete(ctx, chat_id, pending["question_id"])
        await ctx.repo.log_answer(chat_id, message_id, "no")
        await callback.answer("Ок, не считаем")
        return

    chosen = CATEGORY_ACTIONS.get(action)
    if chosen is None and action != ACTION_YES:
        await callback.answer()
        return

    for detection in detections:
        if detection.category is None:
            detection.category = chosen or Category.CARE

    saved = await ctx.repo.save_expenses(
        chat_id=chat_id,
        author_id=pending["author_id"],
        author_name=pending["author_name"],
        message_id=message_id,
        detections=detections,
        spent_at=pending["spent_at"],
        car=await ctx.repo.active_car(chat_id, pending["author_id"]),
    )
    if unknown and chosen is not None:
        await ctx.repo.vote_words(chat_id, unknown, chosen, positive=True)
        ctx.forget_lexicon(chat_id)

    await ctx.repo.drop_pending(chat_id, message_id)
    await ctx.repo.log_answer(chat_id, message_id, "yes")

    total = sum((detection.amount for detection in detections), Decimal(0))
    reacted = await _react(ctx, chat_id, message_id, total)
    summary = _summary(detections, ctx.settings.currency)

    # Масло, фильтры, колодки — вопрос не исчезает, а превращается в
    # «Отметить в ТО?», чтобы замена попала в обслуживание без похода в личку
    turned = await service_flow.offer_in_place(
        ctx,
        chat_id=chat_id,
        message_id=pending["question_id"],
        owner_id=pending["author_id"],
        text=" ".join(d.note for d in detections),
        odometer=next((d.odometer for d in detections if d.odometer), None),
        at=pending["spent_at"],
    )

    if turned:
        pass
    elif reacted:
        await _delete(ctx, chat_id, pending["question_id"])
    else:
        # Без права на реакции подтверждение должно быть хоть как-то видно
        await _replace_question(ctx, chat_id, pending["question_id"], f"{SALUTE} {summary}")

    await callback.answer(f"Записал: {summary}")
    log.info("трата записана: чат=%s автор=%s %s", chat_id, pending["author_id"], summary)
    if not saved:
        log.warning("база не вернула созданные записи для сообщения %s", message_id)


def _summary(detections: list[Detection], currency: str) -> str:
    return " + ".join(
        f"{CATEGORY_EMOJI[detection.category]} {money(detection.amount, currency)}"
        for detection in detections
    )


async def _react(ctx: Context, chat_id: int, message_id: int, total: Decimal) -> bool:
    """Эмодзи зависит от суммы: обычная трата, дорогая, совсем беда."""
    if total >= ctx.settings.huge_expense:
        emoji = SKULL
    elif total >= ctx.settings.big_expense:
        emoji = FIRE
    else:
        emoji = SALUTE
    try:
        await ctx.bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
        return True
    except Exception as error:  # noqa: BLE001
        log.warning("не удалось поставить реакцию в чате %s: %s", chat_id, error)
        return False


async def _delete(ctx: Context, chat_id: int, message_id: int) -> None:
    try:
        await ctx.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as error:  # noqa: BLE001
        log.debug("не удалось удалить сообщение %s: %s", message_id, error)


async def _replace_question(ctx: Context, chat_id: int, message_id: int, text: str) -> None:
    try:
        await ctx.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text, reply_markup=None
        )
    except Exception as error:  # noqa: BLE001
        log.debug("не удалось отредактировать вопрос %s: %s", message_id, error)
