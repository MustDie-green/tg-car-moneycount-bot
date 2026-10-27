"""Отметка замены в ТО: из траты в чате, ответом пробегом, из лички."""

from datetime import datetime, timedelta, timezone

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.methods import DeleteMessage, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from carbot import scheduler, service
from carbot.config import Settings
from carbot.db import Database, Repo
from carbot.db.migrations import split_category, split_other
from carbot.render import load_fonts
from carbot.services import Context
from carbot.models import Category

TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
GROUP = -1001234567890
VALERA = 222


class FakeBot(Bot):
    def __init__(self) -> None:
        super().__init__(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.calls: list = []
        self._next_id = 500

    async def __call__(self, method, request_timeout=None):  # noqa: ANN001
        self.calls.append(method)
        if isinstance(method, SendMessage):
            self._next_id += 1
            chat_type = "private" if method.chat_id > 0 else "supergroup"
            return Message(
                message_id=self._next_id,
                date=datetime.now(timezone.utc),
                chat=Chat(id=method.chat_id, type=chat_type),
                from_user=User(id=1, is_bot=True, first_name="bot"),
                text=method.text,
            )
        return True

    def of(self, method_type) -> list:
        return [call for call in self.calls if isinstance(call, method_type)]

    def last_edit(self) -> EditMessageText:
        return self.of(EditMessageText)[-1]


def group_message(text: str, *, message_id: int, reply_to: int | None = None) -> Message:
    reply = None
    if reply_to is not None:
        reply = Message(
            message_id=reply_to,
            date=datetime.now(timezone.utc),
            chat=Chat(id=GROUP, type="supergroup"),
            from_user=User(id=1, is_bot=True, first_name="bot"),
            text="вопрос",
        )
    return Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=GROUP, type="supergroup"),
        from_user=User(id=VALERA, is_bot=False, first_name="Валера"),
        text=text,
        reply_to_message=reply,
    )


def press(data: str, *, message_id: int, chat_id: int = GROUP, user_id: int = VALERA) -> CallbackQuery:
    chat_type = "private" if chat_id > 0 else "supergroup"
    return CallbackQuery(
        id="cb",
        from_user=User(id=user_id, is_bot=False, first_name="Валера"),
        chat_instance="ci",
        data=data,
        message=Message(
            message_id=message_id,
            date=datetime.now(timezone.utc),
            chat=Chat(id=chat_id, type=chat_type),
            from_user=User(id=1, is_bot=True, first_name="bot"),
            text="вопрос",
        ),
    )


@pytest.fixture
async def harness(dispatcher):
    db = Database("mem://", "test", "test")
    await db.connect()
    bot = FakeBot()
    ctx = Context(
        settings=Settings(bot_token=TOKEN),
        repo=Repo(db),
        fonts=load_fonts(str(Settings().fonts_dir)),
        bot=bot,
    )
    dispatcher["ctx"] = ctx

    async def feed(update_kwargs) -> None:
        await dispatcher.feed_update(bot, Update(update_id=1, **update_kwargs))

    await ctx.repo.upsert_person(GROUP, VALERA, "Валера")
    await ctx.repo.add_car(GROUP, VALERA, "Macan")
    car = await ctx.repo.active_car(GROUP, VALERA)
    now = datetime.now(timezone.utc)
    # Темп 50 км в день: 30 дней назад 186 000, вчера 187 450
    await ctx.repo.add_odometer(chat_id=GROUP, owner_id=VALERA, km=186_000, at=now - timedelta(days=30), car=car)
    await ctx.repo.add_odometer(chat_id=GROUP, owner_id=VALERA, km=187_450, at=now - timedelta(days=1), car=car)

    yield feed, bot, ctx, car
    await bot.session.close()
    await db.close()


async def oil_plan(ctx) -> dict:
    plans = {row["kind"]: row for row in await ctx.repo.service_plans(GROUP, VALERA)}
    return plans.get("oil_engine", {})


# --- Что поменяли: разбор текста ---------------------------------------------


@pytest.mark.parametrize(
    "text, sure, maybe",
    [
        ("ТО (масло + фильтры", ["oil_engine"], ["filter_air", "filter_cabin"]),
        ("замена масла", ["oil_engine"], []),
        ("масло акпп", ["oil_gearbox"], []),
        ("салонный фильтр", ["filter_cabin"], []),
        ("тормозуха", ["brake_fluid"], []),
        ("осаго", ["insurance"], []),
        ("развал-схождение", [], []),
        ("мойка", [], []),
    ],
)
def test_detect_kinds(text, sure, maybe):
    assert service.detect_kinds(text) == (sure, maybe)


def test_parse_odometer():
    assert service.parse_odometer("187950") == 187_950
    assert service.parse_odometer("187 950 км") == 187_950
    assert service.parse_odometer("сто тысяч") is None


# --- Из траты в общем чате ---------------------------------------------------


async def test_oil_expense_turns_question_into_service(harness):
    """«Да» на масле — и тот же вопрос становится «Отметить в ТО?»."""
    feed, bot, ctx, car = harness
    # Старый план «следующая на 187 500» — тот самый, из-за которого масло
    # было «просрочено» сразу после замены
    await ctx.repo.upsert_service(
        chat_id=GROUP, owner_id=VALERA, car=car, car_slug="macan", car_title="Macan",
        kind="oil_engine", interval_km=7000, last_km=180_400, next_km=187_500,
    )

    await feed({"message": group_message("ТО масло и фильтр 22230", message_id=10)})
    question_id = bot._next_id
    await feed({"callback_query": press("e:10:yes", message_id=question_id)})

    edit = bot.last_edit()
    assert "Отметить замену в ТО" in edit.text
    # Прикидка по темпу: вчера 187 450, ездит по ~50 км в день
    assert "~187 4" in edit.text or "~187 5" in edit.text
    buttons = [b.text for row in edit.reply_markup.inline_keyboard for b in row]
    assert "☑ Масло двигателя" in buttons
    assert "☐ Воздушный фильтр" in buttons
    assert not bot.of(DeleteMessage), "вопрос не удаляется, а превращается"

    await feed({"callback_query": press("sa:ok", message_id=question_id)})
    plan = await oil_plan(ctx)
    assert plan["last_km"] > 187_450
    assert plan["next_km"] is None, "старый план сброшен"
    assert "Записал в ТО" in bot.last_edit().text


async def test_typed_odometer_wins_over_estimate(harness):
    feed, bot, ctx, _ = harness
    await feed({"message": group_message("замена масла 9000", message_id=11)})
    question_id = bot._next_id
    await feed({"callback_query": press("e:11:yes", message_id=question_id)})

    await feed({"callback_query": press("sa:km", message_id=question_id)})
    assert "Ответь на это сообщение пробегом" in bot.last_edit().text

    await feed({"message": group_message("187 950", message_id=12, reply_to=question_id)})
    assert "Пробег: 187 950 км" in bot.last_edit().text

    await feed({"callback_query": press("sa:ok", message_id=question_id)})
    assert (await oil_plan(ctx))["last_km"] == 187_950
    # Названный пробег — настоящий замер, он попадает в историю одометра
    assert 187_950 in [km for _, km in await ctx.repo.odometer_readings(GROUP, VALERA)]


async def test_toggles_and_refusal(harness):
    feed, bot, ctx, _ = harness
    await feed({"message": group_message("ТО 15000", message_id=13)})
    question_id = bot._next_id
    await feed({"callback_query": press("e:13:yes", message_id=question_id)})

    # Галочка на салонный фильтр (третья позиция), снятие с масла (первая)
    await feed({"callback_query": press("sa:t2", message_id=question_id)})
    await feed({"callback_query": press("sa:t0", message_id=question_id)})
    buttons = [b.text for row in bot.last_edit().reply_markup.inline_keyboard for b in row]
    assert "☐ Масло двигателя" in buttons and "☑ Салонный фильтр" in buttons

    await feed({"callback_query": press("sa:no", message_id=question_id)})
    assert bot.of(DeleteMessage)
    assert await ctx.repo.service_plans(GROUP, VALERA) == []


async def test_other_people_cannot_tick_your_car(harness):
    feed, bot, ctx, _ = harness
    await feed({"message": group_message("замена масла 9000", message_id=14)})
    question_id = bot._next_id
    await feed({"callback_query": press("e:14:yes", message_id=question_id)})
    await feed({"callback_query": press("sa:ok", message_id=question_id, user_id=999)})
    assert await ctx.repo.service_plans(GROUP, VALERA) == []


async def test_ordinary_expense_is_not_turned(harness):
    feed, bot, ctx, _ = harness
    await feed({"message": group_message("мойка 700", message_id=15)})
    question_id = bot._next_id
    await feed({"callback_query": press("e:15:yes", message_id=question_id)})
    assert [d.message_id for d in bot.of(DeleteMessage)] == [question_id]


# --- Из напоминания в личке ---------------------------------------------------


async def test_reminder_comes_with_replaced_buttons(harness):
    feed, bot, ctx, car = harness
    await ctx.repo.upsert_service(
        chat_id=GROUP, owner_id=VALERA, car=car, car_slug="macan", car_title="Macan",
        kind="oil_engine", interval_km=7000, last_km=180_700,
    )
    await ctx.repo.touch_chat(GROUP, "гараж")
    await scheduler.send_service_reminders(ctx)

    reminder = [call for call in bot.of(SendMessage) if call.chat_id == VALERA][-1]
    assert "скоро на сервис" in reminder.text
    buttons = [b.text for row in reminder.reply_markup.inline_keyboard for b in row]
    assert "☐ Масло двигателя" in buttons

    await feed({"callback_query": press("sa:t0", message_id=bot._next_id, chat_id=VALERA)})
    await feed({"callback_query": press("sa:ok", message_id=bot._next_id, chat_id=VALERA)})
    plan = await oil_plan(ctx)
    assert plan["last_km"] > 187_000 and plan["reminded_at"] is None


async def test_dm_past_replacement_is_not_an_odometer_reading(harness):
    """«Заменил на 186 500» в личке — это прошлое, а не текущий одометр."""
    feed, bot, ctx, car = harness
    from carbot.handlers import service_flow

    await service_flow.offer_in_dm(
        ctx, group_id=GROUP, owner_id=VALERA, offered=["oil_engine"], selected=["oil_engine"]
    )
    ask_id = bot._next_id
    reply = Message(
        message_id=900,
        date=datetime.now(timezone.utc),
        chat=Chat(id=VALERA, type="private"),
        from_user=User(id=VALERA, is_bot=False, first_name="Валера"),
        text="186500",
        reply_to_message=Message(
            message_id=ask_id, date=datetime.now(timezone.utc),
            chat=Chat(id=VALERA, type="private"),
            from_user=User(id=1, is_bot=True, first_name="bot"), text="вопрос",
        ),
    )
    await feed({"message": reply})
    await feed({"callback_query": press("sa:ok", message_id=ask_id, chat_id=VALERA)})

    readings = [km for _, km in await ctx.repo.odometer_readings(GROUP, VALERA)]
    assert 186_500 not in readings, "прошлый пробег не должен стать текущим замером"
    plan = await oil_plan(ctx)
    assert plan["last_km"] == 186_500
    # Дата восстановлена по замерам: 186 500 было примерно 20 дней назад
    days = (datetime.now(timezone.utc) - plan["last_at"]).days
    assert 15 <= days <= 25


# --- Миграция «остального» ---------------------------------------------------


@pytest.mark.parametrize(
    "note, expected",
    [
        ("штраф", Category.FINES),
        ("Платные парковк", Category.STATE),
        ("абонемент М11", Category.STATE),
        ("техосмотр", Category.STATE),
        ("омывайка зимняя", Category.CARE),
        ("Лакокрасочное", Category.CARE),  # незнакомое — в уход
    ],
)
def test_split_category(note, expected):
    assert split_category(note) == expected


async def test_split_other_migration_is_idempotent():
    db = Database("mem://", "test", "test")
    await db.connect()
    await db.query(
        "CREATE expense SET chat_id = 1, author = person:[1, 1], author_id = 1, "
        "author_name = 'А', message_id = 1, amount = 500, category = 'other', "
        "note = 'штраф', spent_at = time::now()"
    )
    assert await split_other(db) == 1
    assert await split_other(db) == 0
    rows = await db.query("SELECT category FROM expense")
    assert rows[0]["category"] == "fines"
    await db.close()


async def test_split_tuning_runs_once():
    from carbot.db.migrations import split_tuning

    db = Database("mem://", "test", "test")
    await db.connect()
    for note in ("установка предпускового подогревателя", "замена помпы"):
        await db.query(
            "CREATE expense SET chat_id = 1, author = person:[1, 1], author_id = 1, "
            "author_name = 'А', message_id = 1, amount = 500, category = 'service', "
            "note = $note, spent_at = time::now()",
            {"note": note},
        )
    assert await split_tuning(db) == 1
    # Человек вернул трату в ремонт — повторный старт его решение не трогает
    await db.query("UPDATE expense SET category = 'service'")
    assert await split_tuning(db) == 0
    rows = await db.query("SELECT category FROM expense WHERE category = 'tuning'")
    assert rows == []
    await db.close()
