"""Проверка проводки aiogram: апдейт → хендлер → вызовы Telegram API.

Настоящего бота тут нет: Bot.__call__ подменён, поэтому видно ровно то, что
хендлер попытался отправить в телеграм.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.methods import (
    DeleteMessage,
    EditMessageText,
    SendDocument,
    SendMessage,
    SetMessageReaction,
)
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from carbot.config import Settings
from carbot.db import Database, Repo
from carbot.handlers import build_router
from carbot.models import Category
from carbot.render import load_fonts
from carbot.services import Context

TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
CHAT = -1001234567890
ANTON = 111
VALERA = 222


class FakeBot(Bot):
    """Записывает вызовы API и отвечает правдоподобными заглушками."""

    def __init__(self) -> None:
        super().__init__(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.calls: list = []
        self._next_id = 500
        # Типы методов, на которых телеграм «отказывает» — для проверки запасных путей
        self.fail_on: tuple = ()

    async def __call__(self, method, request_timeout=None):  # noqa: ANN001
        self.calls.append(method)
        if self.fail_on and isinstance(method, self.fail_on):
            raise RuntimeError("not enough rights")
        if isinstance(method, SendMessage):
            self._next_id += 1
            return Message(
                message_id=self._next_id,
                date=datetime.now(timezone.utc),
                chat=Chat(id=CHAT, type="supergroup"),
                from_user=User(id=1, is_bot=True, first_name="bot"),
                text=method.text,
            )
        return True

    def of(self, method_type) -> list:
        return [call for call in self.calls if isinstance(call, method_type)]


def message(text: str, *, message_id: int, user_id: int = ANTON, name: str = "Антон") -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=CHAT, type="supergroup"),
        from_user=User(id=user_id, is_bot=False, first_name=name),
        text=text,
    )


def press(data: str, *, user_id: int = ANTON, question_id: int = 501) -> CallbackQuery:
    return CallbackQuery(
        id="cb1",
        from_user=User(id=user_id, is_bot=False, first_name="Антон"),
        chat_instance="ci",
        data=data,
        message=Message(
            message_id=question_id,
            date=datetime.now(timezone.utc),
            chat=Chat(id=CHAT, type="supergroup"),
            from_user=User(id=1, is_bot=True, first_name="bot"),
            text="На тачку?",
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

    yield feed, bot, ctx
    await bot.session.close()
    await db.close()


async def test_expense_message_gets_a_question(harness):
    feed, bot, ctx = harness
    await feed({"message": message("мойка 500", message_id=10)})

    sent = bot.of(SendMessage)
    assert len(sent) == 1
    assert sent[0].text == "На тачку? 🧽 500 ₽"
    assert sent[0].reply_parameters.message_id == 10

    pending = await ctx.repo.get_pending(CHAT, 10)
    assert pending is not None
    assert pending["author_id"] == ANTON
    assert pending["question_id"] == 501


async def test_ordinary_chatter_is_ignored(harness):
    feed, bot, _ = harness
    for text in ("да ладно", "их было 30 человек", "продал колеса за 5000"):
        await feed({"message": message(text, message_id=11)})
    assert bot.of(SendMessage) == []


async def test_yes_saves_expense_reacts_and_removes_question(harness):
    feed, bot, ctx = harness
    await feed({"message": message("бенз 1200р", message_id=20)})
    await feed({"callback_query": press("e:20:yes")})

    saved = await ctx.repo.expenses_by_message(CHAT, 20)
    assert len(saved) == 1
    assert saved[0].category == Category.FUEL
    assert saved[0].amount == Decimal(1200)
    assert saved[0].author_name == "Антон"

    reactions = bot.of(SetMessageReaction)
    assert len(reactions) == 1
    assert reactions[0].message_id == 20  # реакция на исходное сообщение
    assert reactions[0].reaction[0].emoji == "🫡"

    deleted = bot.of(DeleteMessage)
    assert [call.message_id for call in deleted] == [501]  # удалён вопрос бота
    assert await ctx.repo.get_pending(CHAT, 20) is None


async def test_big_expense_gets_a_louder_reaction(harness):
    feed, bot, _ = harness
    await feed({"message": message("замена сцепления 60000р", message_id=21)})
    await feed({"callback_query": press("e:21:yes")})
    assert bot.of(SetMessageReaction)[0].reaction[0].emoji == "💀"


async def test_no_keeps_the_chat_clean(harness):
    feed, bot, ctx = harness
    await feed({"message": message("шаурма 500р", message_id=22)})
    await feed({"callback_query": press("e:22:no")})

    assert await ctx.repo.expenses_by_message(CHAT, 22) == []
    assert bot.of(SetMessageReaction) == []
    assert [call.message_id for call in bot.of(DeleteMessage)] == [501]


async def test_only_the_author_may_answer(harness):
    feed, bot, ctx = harness
    await feed({"message": message("мойка 500", message_id=23)})
    await feed({"callback_query": press("e:23:yes", user_id=VALERA)})

    assert await ctx.repo.expenses_by_message(CHAT, 23) == []
    assert await ctx.repo.get_pending(CHAT, 23) is not None  # вопрос ещё висит


async def test_explicit_form_only_asks_for_the_category(harness):
    """«на тачку N гидрач»: сам факт уже понятен, остаётся категория."""
    feed, bot, ctx = harness
    for message_id in (30, 31):
        await feed({
            "message": message(f"на тачку {message_id * 100} гидрач", message_id=message_id)
        })
        await feed({"callback_query": press(f"e:{message_id}:service")})

    saved = await ctx.repo.expenses_by_message(CHAT, 31)
    assert saved[0].category == Category.SERVICE
    # Переспрашивать «На тачку?» незачем — человек уже это сказал
    asked = [c.text for c in bot.of(SendMessage) if c.text]
    assert not any("На тачку" in text for text in asked)
    assert any("Куда записать" in text for text in asked)
    assert await ctx.learned(CHAT) == {"гидрач": Category.SERVICE}


async def test_explicit_form_asks_before_saving(harness):
    """«на тачку 700 мойка» — без молчаливой записи, но и без «На тачку?»."""
    feed, bot, ctx = harness
    await feed({"message": message("на тачку 700 мойка", message_id=35)})

    assert await ctx.repo.expenses_by_message(CHAT, 35) == []
    question = bot.of(SendMessage)[-1].text
    assert question.startswith("Записать?") and "На тачку" not in question

    await feed({"callback_query": press("e:35:yes")})
    saved = await ctx.repo.expenses_by_message(CHAT, 35)
    assert saved and saved[0].amount == Decimal(700)


async def test_ordinary_number_no_longer_asks(harness):
    """Главная жалоба: бот дёргал вопросом на любое число в чате."""
    feed, bot, ctx = harness
    for text in ("прикольная штука", "давай 500 скинемся", "телефон за 90к взял"):
        await feed({"message": message(text, message_id=hash(text) % 1000)})

    assert not [c for c in bot.of(SendMessage) if "На тачку" in (c.text or "")]


async def test_detector_journal_records_the_verdict(harness):
    """В журнал попадает и то, на что бот среагировал, и то, что отбросил."""
    feed, bot, ctx = harness
    await feed({"message": message("мойка 500", message_id=60)})
    await feed({"message": message("телефон за 90к взял", message_id=61)})

    rows = {row["message_id"]: row for row in await ctx.repo.detect_log(CHAT)}
    assert rows[60]["reason"].startswith("keyword:")
    assert rows[60]["asked"] is True
    assert rows[61]["reason"] == "no_keyword"
    assert rows[61]["asked"] is False

    # Ответ человека тоже уходит в журнал — по нему потом видно точность
    await feed({"callback_query": press("e:60:yes")})
    rows = {row["message_id"]: row for row in await ctx.repo.detect_log(CHAT)}
    assert rows[60]["answer"] == "yes"


async def test_stale_question_is_answered_politely(harness):
    feed, bot, _ = harness
    await feed({"callback_query": press("e:999:yes")})
    assert [call.message_id for call in bot.of(DeleteMessage)] == [501]


async def test_fallback_when_reactions_are_forbidden(harness):
    feed, bot, ctx = harness
    bot.fail_on = (SetMessageReaction,)

    await feed({"message": message("мойка 800", message_id=40)})
    await feed({"callback_query": press("e:40:yes")})

    # Трата всё равно записана, а подтверждение видно вместо кнопок
    assert len(await ctx.repo.expenses_by_message(CHAT, 40)) == 1
    edits = bot.of(EditMessageText)
    assert len(edits) == 1
    assert edits[0].text == "🫡 🧽 800 ₽"
    assert bot.of(DeleteMessage) == []


async def test_undo_removes_expense_and_reaction(harness):
    feed, bot, ctx = harness
    await feed({"message": message("бенз 1000р", message_id=50)})
    await feed({"callback_query": press("e:50:yes")})
    await feed({"message": message("/undo", message_id=51)})

    assert await ctx.repo.expenses_by_message(CHAT, 50) == []
    cleared = [call for call in bot.of(SetMessageReaction) if call.reaction == []]
    assert [call.message_id for call in cleared] == [50]


async def test_help_command_answers(harness):
    feed, bot, _ = harness
    await feed({"message": message("/help", message_id=60)})
    assert "Автобухгалтерия" in bot.of(SendMessage)[0].text


async def test_detect_command_reports_and_exports(harness):
    """Сводка по журналу и выгрузка — то, чем детект правится по данным."""
    feed, bot, ctx = harness

    await feed({"message": message("/detect", message_id=70)})
    assert "Журнал пуст" in bot.of(SendMessage)[-1].text

    await feed({"message": message("мойка 500", message_id=71)})
    await feed({"callback_query": press("e:71:yes")})
    await feed({"message": message("телефон за 90к взял", message_id=72)})

    bot.calls.clear()
    await feed({"message": message("/detect", message_id=73)})

    documents = bot.of(SendDocument)
    assert documents, "журнал должен приезжать файлом"
    caption = documents[0].caption
    assert "подтвердили: 1" in caption
    assert "промолчал сам: 1" in caption
    assert "Попадание в цель: 100%" in caption

    body = documents[0].document.data.decode("utf-8-sig")
    assert "телефон за 90к взял" in body
    assert "no_keyword" in body
    assert "keyword:" in body
