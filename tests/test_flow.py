"""Сквозной путь: сообщение → вопрос → подтверждение → база → картинка."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from carbot.config import settings
from carbot.db import Database, Repo, detection_from_json
from carbot.handlers.expenses import build_question
from carbot.models import Category
from carbot.parsing import parse_message
from carbot.render import load_fonts, render_month, render_personal, to_png
from carbot.reporting import build_month_report, build_personal_report, fuel_stats
from carbot.titles import assign_titles

CHAT = -1001234567890
ANTON, VALERA, DIMA = 111, 222, 333
TZ = "Europe/Moscow"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
async def repo():
    db = Database("mem://", "test", "test")
    await db.connect()
    yield Repo(db)
    await db.close()


@pytest.fixture(scope="module")
def fonts():
    return load_fonts(str(settings.fonts_dir))


async def confirm(repo, *, text, author_id, name, message_id, when, action="yes"):
    """Повторяет то, что делают хендлеры: разобрать, отложить, подтвердить."""
    learned = await repo.learned_words(CHAT)
    result = parse_message(text, learned=learned)
    if not result:
        return []

    build_question(result, message_id, "₽")  # проверяем, что клавиатура строится
    await repo.save_pending(
        chat_id=CHAT,
        message_id=message_id,
        question_id=message_id + 1000,
        author_id=author_id,
        author_name=name,
        username=None,
        detections=result.detections,
        unknown_lemmas=result.unknown_lemmas,
        spent_at=when,
        ttl_seconds=600,
    )

    pending = await repo.get_pending(CHAT, message_id)
    detections = [detection_from_json(item) for item in pending["payload"]["detections"]]
    unknown = pending["payload"]["unknown_lemmas"]

    if action == "no":
        await repo.vote_words(CHAT, unknown, None, positive=False)
        await repo.drop_pending(CHAT, message_id)
        return []

    chosen = None if action == "yes" else Category(action)
    for detection in detections:
        if detection.category is None:
            detection.category = chosen or Category.CARE
    if unknown and chosen is not None:
        await repo.vote_words(CHAT, unknown, chosen, positive=True)

    saved = await repo.save_expenses(
        chat_id=CHAT,
        author_id=author_id,
        author_name=name,
        message_id=message_id,
        detections=detections,
        spent_at=pending["spent_at"],
    )
    await repo.drop_pending(CHAT, message_id)
    return saved


async def test_full_confirmation_flow(repo):
    when = datetime.now(timezone.utc)
    saved = await confirm(
        repo, text="мойка 500", author_id=ANTON, name="Антон", message_id=1, when=when
    )
    assert len(saved) == 1
    assert saved[0].category == Category.CARE
    assert saved[0].amount == Decimal(500)
    assert await repo.get_pending(CHAT, 1) is None


async def test_declined_expense_is_not_saved(repo):
    when = datetime.now(timezone.utc)
    await confirm(
        repo, text="шаурма 500р", author_id=ANTON, name="Антон",
        message_id=2, when=when, action="no",
    )
    assert await repo.expenses_by_message(CHAT, 2) == []


async def test_chat_teaches_itself_a_new_word(repo):
    when = datetime.now(timezone.utc)
    # Слова «гидрач» бот не знает, поэтому траты заводят явной формой —
    # она же и учит: незнакомые слова рядом становятся кандидатами в словарь
    for message_id in (10, 11):
        await confirm(
            repo, text=f"на тачку {message_id * 100} гидрач", author_id=ANTON,
            name="Антон", message_id=message_id, when=when, action="service",
        )

    learned = await repo.learned_words(CHAT, threshold=2)
    assert learned.get("гидрач") == Category.SERVICE

    # Теперь то же слово распознаётся само, без кнопок выбора категории
    result = parse_message("гидрач 4000", learned=learned)
    assert result.detections[0].category == Category.SERVICE


async def test_month_report_ranks_titles_and_image(repo, fonts):
    now = datetime.now(timezone.utc)
    this_month = now.replace(day=15, hour=12)
    last_month = (this_month.replace(day=1) - timedelta(days=5)).replace(day=15)

    # Прошлый месяц: Валера впереди Антона
    await confirm(repo, text="бенз 20000р", author_id=VALERA, name="Валера", message_id=100, when=last_month)
    await confirm(repo, text="бенз 5000р", author_id=ANTON, name="Антон", message_id=101, when=last_month)

    # Этот месяц: Антон уходит в отрыв, добавляется новичок Дима
    await confirm(repo, text="замена сцепления 48000р", author_id=ANTON, name="Антон", message_id=200, when=this_month)
    await confirm(repo, text="бенз 3000р", author_id=ANTON, name="Антон", message_id=201, when=this_month)
    await confirm(repo, text="заправка 9000р", author_id=VALERA, name="Валера", message_id=202, when=this_month)
    await confirm(repo, text="мойка 700р", author_id=DIMA, name="Дима", message_id=203, when=this_month)

    report = await build_month_report(repo, CHAT, this_month.year, this_month.month, TZ)

    assert [person.name for person in report.people] == ["Антон", "Валера", "Дима"]
    assert report.people[0].total == Decimal(51000)
    assert report.total == Decimal(60700)

    anton, valera, dima = report.people
    assert anton.rank == 1 and anton.prev_rank == 2
    assert anton.rank_delta == 1  # поднялся с второго места на первое
    assert valera.rank == 2 and valera.prev_rank == 1
    assert dima.prev_rank is None  # новичок

    # Титулы раздаются по приоритету и не повторяются
    assert anton.title == "Чёрный лебедь"  # 48 000 сильно выбивается из ряда
    assert valera.title == "Бензоколонка"  # больше всех залил
    assert dima.title == "Мойдодыр"  # единственный, кто мыл машину

    assert [int(e.amount) for e in report.top_expenses] == [48000, 9000, 3000]
    assert report.by_category[Category.FUEL] == Decimal(12000)
    assert report.fun_fact

    png = to_png(render_month(report, fonts, tz_name=TZ), fonts.files)
    assert png.startswith(PNG_MAGIC)
    assert len(png) > 20_000


async def test_month_report_survives_empty_month(repo, fonts):
    report = await build_month_report(repo, CHAT, 2019, 3, TZ)
    assert report.people == []
    assert report.total == Decimal(0)
    png = to_png(render_month(report, fonts, tz_name=TZ), fonts.files)
    assert png.startswith(PNG_MAGIC)


async def test_personal_report_and_image(repo, fonts):
    now = datetime.now(timezone.utc).replace(day=15, hour=12)
    await confirm(repo, text="заправка 40л 2800р пробег 120000", author_id=ANTON, name="Антон", message_id=300, when=now)
    await confirm(repo, text="заправка 38л 2700р пробег 120500", author_id=ANTON, name="Антон", message_id=301, when=now)
    await confirm(repo, text="мойка 600р", author_id=ANTON, name="Антон", message_id=302, when=now)

    report = await build_personal_report(repo, CHAT, ANTON, "Антон", now.year, now.month, TZ)
    assert report.total == Decimal(6100)
    assert report.count == 3
    assert report.by_category[Category.FUEL] == Decimal(5500)
    assert len(report.history) == 6
    assert report.fuel is not None
    assert report.fuel.liters_per_100 == Decimal("7.6")  # 38 л на 500 км
    assert report.fuel.rub_per_km == Decimal("5.40")

    png = to_png(render_personal(report, fonts, tz_name=TZ), fonts.files)
    assert png.startswith(PNG_MAGIC)


async def test_undo_removes_the_last_expense(repo):
    now = datetime.now(timezone.utc)
    await confirm(repo, text="бенз 1000р", author_id=ANTON, name="Антон", message_id=400, when=now)
    await confirm(repo, text="мойка 500р", author_id=ANTON, name="Антон", message_id=401, when=now)

    last = await repo.last_expenses(CHAT, ANTON)
    assert [int(e.amount) for e in last] == [500]

    await repo.delete_expenses(CHAT, last[0].message_id)
    assert await repo.expenses_by_message(CHAT, 401) == []
    assert len(await repo.expenses_by_message(CHAT, 400)) == 1


# --- Чистая логика без базы --------------------------------------------------


def _fill(odometer, liters, amount, day):
    from carbot.models import Expense

    return Expense(
        chat_id=CHAT, author_id=ANTON, author_name="Антон", message_id=day,
        amount=Decimal(amount), category=Category.FUEL, note="",
        spent_at=datetime(2026, 7, day, tzinfo=timezone.utc),
        liters=Decimal(liters) if liters else None, odometer=odometer,
    )


def test_fuel_stats_counts_fills_without_odometer():
    """Пропустил пробег на заправке — её литры всё равно сожжены на этих км."""
    stats = fuel_stats(
        [
            _fill(100000, 40, 4000, 1),
            _fill(None, 30, 3000, 4),  # пробег не записали
            _fill(101000, 50, 5000, 9),
        ]
    )
    assert stats is not None
    # 30 + 50 литров на 1000 км. Старый метод видел только последние 50 — 5 л/100
    assert stats.liters_per_100 == Decimal("8.0")
    assert stats.rub_per_km == Decimal("8.00")


def test_fuel_stats_restores_liters_from_amount():
    """«Бенз 5994» без литров — тоже бензин в баке, литры по своей цене."""
    stats = fuel_stats(
        [
            _fill(100000, 40, 4000, 1),  # 100 ₽ за литр
            _fill(None, None, 6000, 4),  # записано одной суммой — это 60 л
            _fill(101000, 40, 4000, 9),
        ]
    )
    assert stats is not None
    assert stats.estimated_liters == Decimal("60.0")
    # 60 восстановленных + 40 литров на 1000 км. Без восстановления было бы 4
    assert stats.liters_per_100 == Decimal("10.0")


def test_fuel_stats_drops_odometer_typos():
    """«186567» вместо «187567» и лишняя цифра не ломают расход."""
    stats = fuel_stats(
        [
            _fill(100000, 40, 4000, 1),
            _fill(99500, 40, 4000, 3),  # опечатка назад
            _fill(1005000, 40, 4000, 5),  # лишняя цифра
            _fill(101000, 40, 4000, 9),
        ]
    )
    assert stats is not None
    # Опечатки выпали как замеры, но их литры честно залиты между крайними точками
    assert stats.liters_per_100 == Decimal("12.0")
    assert stats.distance == 1000


def test_titles_are_unique_per_person():
    from carbot.models import PersonMonth

    people = [
        PersonMonth(1, "А", Decimal(30000), {Category.FUEL: Decimal(30000)}, 5, rank=1, prev_rank=4),
        PersonMonth(2, "Б", Decimal(20000), {Category.SERVICE: Decimal(20000)}, 3, rank=2, prev_rank=1),
        PersonMonth(3, "В", Decimal(1000), {Category.CARE: Decimal(1000)}, 1, rank=3, prev_rank=2),
    ]
    assign_titles(people, black_swan_id=2)
    titles = [person.title for person in people]
    assert len(set(titles)) == len(titles)
    assert people[1].title == "Чёрный лебедь"
