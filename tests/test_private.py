"""Личный кабинет: мастер интервалов, просмотр трат, напоминания о ТО."""

from datetime import datetime, timedelta, timezone

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from carbot import scheduler
from carbot.config import Settings
from carbot.db import Database, Repo
from carbot.handlers import build_router
from carbot.models import Category, Confidence, Detection
from carbot.render import load_fonts
from carbot.services import Context

TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
GROUP = -1001234567890
ANTON = 111


class FakeBot(Bot):
    def __init__(self) -> None:
        super().__init__(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.calls: list = []
        self._next_id = 900

    async def __call__(self, method, request_timeout=None):  # noqa: ANN001
        self.calls.append(method)
        if isinstance(method, SendMessage):
            self._next_id += 1
            return Message(
                message_id=self._next_id,
                date=datetime.now(timezone.utc),
                chat=Chat(id=ANTON, type="private"),
                from_user=User(id=1, is_bot=True, first_name="bot"),
                text=method.text,
            )
        return True

    def of(self, method_type) -> list:
        return [call for call in self.calls if isinstance(call, method_type)]

    @property
    def last_text(self) -> str:
        for call in reversed(self.calls):
            if isinstance(call, (SendMessage, EditMessageText)):
                return call.text
        return ""

    @property
    def last_buttons(self) -> list[str]:
        for call in reversed(self.calls):
            if isinstance(call, (SendMessage, EditMessageText)) and call.reply_markup:
                return [
                    button.text
                    for row in call.reply_markup.inline_keyboard
                    for button in row
                ]
        return []


def dm(text: str, *, message_id: int = 10) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=ANTON, type="private"),
        from_user=User(id=ANTON, is_bot=False, first_name="Антон"),
        text=text,
    )


def tap(data: str) -> CallbackQuery:
    return CallbackQuery(
        id="cb",
        from_user=User(id=ANTON, is_bot=False, first_name="Антон"),
        chat_instance="ci",
        data=data,
        message=Message(
            message_id=901,
            date=datetime.now(timezone.utc),
            chat=Chat(id=ANTON, type="private"),
            from_user=User(id=1, is_bot=True, first_name="bot"),
            text="меню",
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


async def seed(repo, *, car: str = "RAV4") -> None:
    await repo.upsert_person(GROUP, ANTON, "Антон")
    await repo.add_car(GROUP, ANTON, car)


# --- Вход ---------------------------------------------------------------------


async def test_start_without_history_explains_itself(harness):
    feed, bot, ctx = harness
    await feed({"message": dm("/start")})
    assert "Мы ещё не знакомы" in bot.last_text


async def test_start_binds_the_only_known_chat(harness):
    feed, bot, ctx = harness
    await seed(ctx.repo)

    await feed({"message": dm("/start")})
    assert "Личный кабинет" in bot.last_text
    assert await ctx.repo.dm_chat(ANTON) == GROUP


async def test_expense_detector_stays_out_of_dm(harness):
    """В личке «мойка 500» не должна превращаться в вопрос «На тачку?»."""
    feed, bot, ctx = harness
    await seed(ctx.repo)
    await feed({"message": dm("мойка 500")})
    assert "На тачку" not in bot.last_text
    assert await ctx.repo.get_pending(ANTON, 10) is None


# --- Мастер интервалов --------------------------------------------------------


async def test_configure_position_with_defaults(harness):
    feed, bot, ctx = harness
    await seed(ctx.repo)
    await feed({"message": dm("/start")})

    await feed({"callback_query": tap("dm:cars")})
    assert "Какая машина?" in bot.last_text

    await feed({"callback_query": tap("dm:car:0")})
    assert "Настроено позиций: 0" in bot.last_text

    await feed({"callback_query": tap("dm:kind:oil_engine")})
    assert "Масло двигателя" in bot.last_text
    assert "Интервал не задан" in bot.last_text

    await feed({"callback_query": tap("dm:iv")})
    assert any("10 000 км" in text for text in bot.last_buttons)

    await feed({"callback_query": tap("dm:ivdef")})
    assert "Интервал: 10 000 км" in bot.last_text

    plans = await ctx.repo.service_plans(GROUP, ANTON)
    assert len(plans) == 1 and plans[0]["interval_km"] == 10_000


async def test_last_change_by_odometer(harness):
    feed, bot, ctx = harness
    await seed(ctx.repo)
    await feed({"message": dm("/start")})
    await feed({"callback_query": tap("dm:cars")})
    await feed({"callback_query": tap("dm:car:0")})
    await feed({"callback_query": tap("dm:kind:oil_engine")})
    await feed({"callback_query": tap("dm:last")})
    await feed({"callback_query": tap("dm:pkm")})
    await feed({"message": dm("100000", message_id=11)})

    plans = await ctx.repo.service_plans(GROUP, ANTON)
    assert plans[0]["last_km"] == 100_000
    # Интервал не задавали руками — подставился типовой, иначе считать нечем
    assert plans[0]["interval_km"] == 10_000


async def test_next_change_without_knowing_the_last(harness):
    """Прошлая замена неизвестна, но есть план — этого достаточно."""
    feed, bot, ctx = harness
    await seed(ctx.repo)
    await feed({"message": dm("/start")})
    await feed({"callback_query": tap("dm:cars")})
    await feed({"callback_query": tap("dm:car:0")})
    await feed({"callback_query": tap("dm:kind:brake_fluid")})
    await feed({"callback_query": tap("dm:next")})
    await feed({"callback_query": tap("dm:pdate")})
    await feed({"message": dm("15.09.2026", message_id=12)})

    plans = await ctx.repo.service_plans(GROUP, ANTON)
    assert plans[0]["next_at"] is not None
    assert plans[0]["last_at"] is None


async def test_bad_input_is_rejected_without_losing_the_step(harness):
    feed, bot, ctx = harness
    await seed(ctx.repo)
    await feed({"message": dm("/start")})
    await feed({"callback_query": tap("dm:cars")})
    await feed({"callback_query": tap("dm:car:0")})
    await feed({"callback_query": tap("dm:kind:oil_engine")})
    await feed({"callback_query": tap("dm:iv")})
    await feed({"callback_query": tap("dm:ivkm")})

    await feed({"message": dm("абракадабра", message_id=13)})
    assert "Не понял число" in bot.last_text
    assert await ctx.repo.service_plans(GROUP, ANTON) == []

    await feed({"message": dm("15к", message_id=14)})
    plans = await ctx.repo.service_plans(GROUP, ANTON)
    assert plans[0]["interval_km"] == 15_000


async def test_monthly_km_is_saved_on_the_car(harness):
    feed, bot, ctx = harness
    await seed(ctx.repo)
    await feed({"message": dm("/start")})
    await feed({"callback_query": tap("dm:cars")})
    await feed({"callback_query": tap("dm:car:0")})
    await feed({"callback_query": tap("dm:monthly")})
    await feed({"message": dm("1500", message_id=15)})

    cars = await ctx.repo.cars(GROUP, ANTON)
    assert cars[0]["monthly_km"] == 1500


# --- Просмотр трат ------------------------------------------------------------


async def test_expense_browser_lists_and_filters(harness):
    feed, bot, ctx = harness
    await seed(ctx.repo)
    now = datetime.now(timezone.utc)

    for index, (amount, category, note) in enumerate(
        [
            (2500, Category.FUEL, "бенз"),
            (600, Category.CARE, "мойка"),
            (8000, Category.SERVICE, "колодки"),
        ]
    ):
        await ctx.repo.save_expenses(
            chat_id=GROUP,
            author_id=ANTON,
            author_name="Антон",
            message_id=100 + index,
            detections=[
                Detection(
                    amount=__import__("decimal").Decimal(amount),
                    category=category,
                    confidence=Confidence.HIGH,
                    note=note,
                )
            ],
            spent_at=now,
        )

    await feed({"message": dm("/start")})
    await feed({"callback_query": tap("dm:months")})
    assert "Какой месяц показать?" in bot.last_text

    await feed({"callback_query": tap(f"dm:mon:{now.year}:{now.month}:all:0")})
    assert "3 трат на 11 100 ₽" in bot.last_text
    assert "колодки" in bot.last_text

    await feed({"callback_query": tap(f"dm:mon:{now.year}:{now.month}:fuel:0")})
    assert "бенз" in bot.last_text
    assert "колодки" not in bot.last_text


# --- Напоминания --------------------------------------------------------------


async def test_reminder_lands_in_dm_and_marks_itself(harness):
    feed, bot, ctx = harness
    await seed(ctx.repo)
    now = datetime.now(timezone.utc)
    car = await ctx.repo.active_car(GROUP, ANTON)

    # Два замера: 30 дней назад и сейчас, темп 100 км/день
    await ctx.repo.add_odometer(
        chat_id=GROUP, owner_id=ANTON, km=106_000, at=now - timedelta(days=30), car=car
    )
    await ctx.repo.add_odometer(
        chat_id=GROUP, owner_id=ANTON, km=109_000, at=now, car=car
    )
    # Менять на 110 000 — осталась тысяча километров
    await ctx.repo.upsert_service(
        chat_id=GROUP,
        owner_id=ANTON,
        car=car,
        car_slug="rav4",
        car_title="RAV4",
        kind="oil_engine",
        interval_km=10_000,
        last_km=100_000,
    )
    await ctx.repo.touch_chat(GROUP, "гараж")

    await scheduler.send_service_reminders(ctx)

    sent = [call for call in bot.of(SendMessage) if call.chat_id == ANTON]
    assert sent, "напоминание должно уйти владельцу в личку"
    assert "Масло двигателя" in sent[-1].text

    plans = await ctx.repo.service_plans(GROUP, ANTON)
    assert plans[0]["reminded_at"] is not None

    # Повторный прогон в тот же день молчит
    bot.calls.clear()
    await scheduler.send_service_reminders(ctx)
    assert not [call for call in bot.of(SendMessage) if call.chat_id == ANTON]


async def test_no_reminder_when_change_is_far_away(harness):
    feed, bot, ctx = harness
    await seed(ctx.repo)
    now = datetime.now(timezone.utc)
    car = await ctx.repo.active_car(GROUP, ANTON)

    await ctx.repo.add_odometer(
        chat_id=GROUP, owner_id=ANTON, km=100_000, at=now - timedelta(days=30), car=car
    )
    await ctx.repo.add_odometer(
        chat_id=GROUP, owner_id=ANTON, km=100_500, at=now, car=car
    )
    await ctx.repo.upsert_service(
        chat_id=GROUP,
        owner_id=ANTON,
        car=car,
        car_slug="rav4",
        car_title="RAV4",
        kind="oil_engine",
        interval_km=10_000,
        last_km=100_000,
    )
    await ctx.repo.touch_chat(GROUP, "гараж")

    await scheduler.send_service_reminders(ctx)
    assert not [call for call in bot.of(SendMessage) if call.chat_id == ANTON]


# --- Замеры пробега из чата ---------------------------------------------------


def group_text(text: str, message_id: int) -> Message:
    return Message(
        message_id=message_id, date=datetime.now(timezone.utc),
        chat=Chat(id=GROUP, type="supergroup"),
        from_user=User(id=ANTON, is_bot=False, first_name="Антон"), text=text,
    )


def group_tap(data: str, user_id: int = ANTON) -> CallbackQuery:
    return CallbackQuery(
        id="cb", from_user=User(id=user_id, is_bot=False, first_name="Антон"),
        chat_instance="ci", data=data,
        message=Message(
            message_id=901, date=datetime.now(timezone.utc),
            chat=Chat(id=GROUP, type="supergroup"),
            from_user=User(id=1, is_bot=True, first_name="bot"), text="вопрос",
        ),
    )


async def test_bare_odometer_asks_before_recording(harness):
    """Голый пробег больше не пишется молча — сначала вопрос."""
    feed, bot, ctx = harness
    await seed(ctx.repo)
    await feed({"message": group_text("пробег 123456", 20)})

    assert await ctx.repo.odometer_readings(GROUP, ANTON) == []
    assert "Записать пробег 123 456 км · RAV4?" in bot.last_text
    assert "На тачку" not in bot.last_text

    # Чужой палец не считается
    await feed({"callback_query": group_tap("e:20:yes", user_id=999)})
    assert await ctx.repo.odometer_readings(GROUP, ANTON) == []

    await feed({"callback_query": group_tap("e:20:yes")})
    assert [km for _, km in await ctx.repo.odometer_readings(GROUP, ANTON)] == [123_456]


async def test_bare_odometer_refused(harness):
    feed, bot, ctx = harness
    await seed(ctx.repo)
    await feed({"message": group_text("пробег 123456", 21)})
    await feed({"callback_query": group_tap("e:21:no")})
    assert await ctx.repo.odometer_readings(GROUP, ANTON) == []
    assert await ctx.repo.get_pending(GROUP, 21) is None


async def test_implausible_odometer_is_not_even_asked(harness):
    """Ниже текущего пробега — это поездка или опечатка: даже не спрашиваем."""
    feed, bot, ctx = harness
    await seed(ctx.repo)
    car = await ctx.repo.active_car(GROUP, ANTON)
    await ctx.repo.add_odometer(
        chat_id=GROUP, owner_id=ANTON, km=189_000,
        at=datetime.now(timezone.utc) - timedelta(days=3), car=car,
    )
    bot.calls.clear()
    await feed({"message": group_text("пробег 1250", 30)})
    assert not bot.of(SendMessage)
    await feed({"message": group_text("пробег 189400", 31)})
    assert "Записать пробег 189 400 км" in bot.last_text
