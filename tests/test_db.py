from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from carbot.db import Database, Repo
from carbot.models import Category, Confidence, Detection


@pytest.fixture
async def repo():
    db = Database("mem://", "test", "test")
    await db.connect()
    yield Repo(db)
    await db.close()


def detection(amount, category, note="", **kwargs):
    return Detection(
        amount=Decimal(amount),
        category=category,
        confidence=Confidence.HIGH,
        note=note,
        **kwargs,
    )


CHAT = -1001234567890
ANTON = 111
VALERA = 222


async def test_save_and_read_expenses(repo):
    now = datetime.now(timezone.utc)
    saved = await repo.save_expenses(
        chat_id=CHAT,
        author_id=ANTON,
        author_name="Антон",
        message_id=42,
        detections=[detection(1000, Category.FUEL, "бенз")],
        spent_at=now,
    )
    assert len(saved) == 1
    assert saved[0].amount == Decimal(1000)
    assert saved[0].category == Category.FUEL

    again = await repo.expenses_by_message(CHAT, 42)
    assert again[0].note == "бенз"
    assert again[0].author_name == "Антон"


async def test_multiple_detections_from_one_message(repo):
    now = datetime.now(timezone.utc)
    await repo.save_expenses(
        chat_id=CHAT,
        author_id=ANTON,
        author_name="Антон",
        message_id=43,
        detections=[
            detection(1000, Category.FUEL, "бенз"),
            detection(500, Category.OTHER, "мойка"),
        ],
        spent_at=now,
    )
    rows = await repo.expenses_by_message(CHAT, 43)
    assert {r.category for r in rows} == {Category.FUEL, Category.OTHER}


async def test_totals_by_person(repo):
    now = datetime.now(timezone.utc)
    for author_id, name, amount, category, msg in (
        (ANTON, "Антон", 1000, Category.FUEL, 1),
        (ANTON, "Антон", 2000, Category.SERVICE, 2),
        (ANTON, "Антон", 500, Category.FUEL, 3),
        (VALERA, "Валера", 700, Category.OTHER, 4),
    ):
        await repo.save_expenses(
            chat_id=CHAT,
            author_id=author_id,
            author_name=name,
            message_id=msg,
            detections=[detection(amount, category)],
            spent_at=now,
        )

    rows = await repo.totals_by_person(CHAT, now - timedelta(days=1), now + timedelta(days=1))
    by_key = {(r["author_id"], r["category"]): r for r in rows}
    assert by_key[(ANTON, Category.FUEL)]["total"] == Decimal(1500)
    assert by_key[(ANTON, Category.FUEL)]["cnt"] == 2
    assert by_key[(ANTON, Category.SERVICE)]["total"] == Decimal(2000)
    assert by_key[(VALERA, Category.OTHER)]["total"] == Decimal(700)


async def test_period_filter_excludes_other_months(repo):
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=60)
    await repo.save_expenses(
        chat_id=CHAT, author_id=ANTON, author_name="Антон", message_id=1,
        detections=[detection(999, Category.FUEL)], spent_at=old,
    )
    rows = await repo.totals_by_person(CHAT, now - timedelta(days=1), now + timedelta(days=1))
    assert rows == []


async def test_top_expenses_sorted(repo):
    now = datetime.now(timezone.utc)
    for msg, amount in enumerate([500, 12000, 3000, 40000], start=1):
        await repo.save_expenses(
            chat_id=CHAT, author_id=ANTON, author_name="Антон", message_id=msg,
            detections=[detection(amount, Category.SERVICE)], spent_at=now,
        )
    top = await repo.top_expenses(CHAT, now - timedelta(days=1), now + timedelta(days=1), limit=3)
    assert [int(e.amount) for e in top] == [40000, 12000, 3000]


async def test_undo_removes_whole_message(repo):
    now = datetime.now(timezone.utc)
    await repo.save_expenses(
        chat_id=CHAT, author_id=ANTON, author_name="Антон", message_id=7,
        detections=[detection(1000, Category.FUEL), detection(500, Category.OTHER)],
        spent_at=now,
    )
    last = await repo.last_expenses(CHAT, ANTON)
    assert len(last) == 2
    removed = await repo.delete_expenses(CHAT, 7)
    assert removed == 2
    assert await repo.expenses_by_message(CHAT, 7) == []


async def test_lexicon_learns_after_threshold(repo):
    assert await repo.learned_words(CHAT) == {}

    await repo.vote_words(CHAT, ["гидрач"], Category.SERVICE, positive=True)
    assert await repo.learned_words(CHAT, threshold=2) == {}

    await repo.vote_words(CHAT, ["гидрач"], Category.SERVICE, positive=True)
    assert await repo.learned_words(CHAT, threshold=2) == {"гидрач": Category.SERVICE}


async def test_lexicon_negative_votes_kill_the_word(repo):
    for _ in range(2):
        await repo.vote_words(CHAT, ["шаурма"], Category.OTHER, positive=True)
    for _ in range(3):
        await repo.vote_words(CHAT, ["шаурма"], None, positive=False)
    assert await repo.learned_words(CHAT, threshold=2) == {}


async def test_pending_roundtrip(repo):
    now = datetime.now(timezone.utc)
    await repo.save_pending(
        chat_id=CHAT, message_id=99, question_id=100, author_id=ANTON,
        author_name="Антон", username="anton",
        detections=[detection(1500, None, "гидрач")],
        unknown_lemmas=["гидрач"], spent_at=now, ttl_seconds=600,
    )
    pending = await repo.get_pending(CHAT, 99)
    assert pending["author_id"] == ANTON
    assert pending["payload"]["unknown_lemmas"] == ["гидрач"]
    assert pending["payload"]["detections"][0]["amount"] == "1500"

    await repo.drop_pending(CHAT, 99)
    assert await repo.get_pending(CHAT, 99) is None


async def test_expired_pendings(repo):
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    await repo.save_pending(
        chat_id=CHAT, message_id=5, question_id=6, author_id=ANTON,
        author_name="Антон", username=None,
        detections=[detection(100, Category.FUEL)], unknown_lemmas=[],
        spent_at=past, ttl_seconds=60,
    )
    expired = await repo.expired_pendings(datetime.now(timezone.utc))
    assert [row["message_id"] for row in expired] == [5]


async def test_cars(repo):
    await repo.add_car(CHAT, ANTON, "Camry 2007")
    await repo.add_car(CHAT, ANTON, "Ларгус")
    cars = await repo.cars(CHAT, ANTON)
    assert [c["title"] for c in cars] == ["Camry 2007", "Ларгус"]
    assert [c["active"] for c in cars] == [False, True]
    assert await repo.active_car(CHAT, ANTON) is not None


async def test_fuel_series_ordered_by_odometer(repo):
    now = datetime.now(timezone.utc)
    for msg, (odo, liters, amount) in enumerate(
        [(120000, 40, 2800), (120500, 38, 2700), (121000, 42, 2900)], start=1
    ):
        await repo.save_expenses(
            chat_id=CHAT, author_id=ANTON, author_name="Антон", message_id=msg,
            detections=[
                detection(amount, Category.FUEL, liters=Decimal(liters), odometer=odo)
            ],
            spent_at=now,
        )
    series = await repo.fuel_series(CHAT, ANTON)
    assert [e.odometer for e in series] == [120000, 120500, 121000]
    assert series[0].liters == Decimal(40)


# --- Пробег, машины и обслуживание -------------------------------------------


async def test_odometer_readings_merge_standalone_and_expenses(repo):
    now = datetime.now(timezone.utc)
    await repo.add_car(CHAT, ANTON, "RAV4")
    car = await repo.active_car(CHAT, ANTON)

    await repo.add_odometer(
        chat_id=CHAT, owner_id=ANTON, km=100_000, at=now - timedelta(days=30), car=car
    )
    await repo.save_expenses(
        chat_id=CHAT,
        author_id=ANTON,
        author_name="Антон",
        message_id=70,
        detections=[detection(3000, Category.FUEL, "бенз", odometer=101_000)],
        spent_at=now,
        car=car,
    )

    readings = await repo.odometer_readings(CHAT, ANTON)
    assert [km for _, km in readings] == [100_000, 101_000]


async def test_all_cars_covers_every_participant(repo):
    await repo.add_car(CHAT, ANTON, "Porsche Macan")
    await repo.add_car(CHAT, VALERA, "RAV4")
    cars = await repo.all_cars(CHAT)
    assert {car["title"] for car in cars} == {"Porsche Macan", "RAV4"}
    assert {car["owner_id"] for car in cars} == {ANTON, VALERA}


async def test_service_plan_roundtrip(repo):
    now = datetime.now(timezone.utc)
    await repo.add_car(CHAT, ANTON, "RAV4")
    car = await repo.active_car(CHAT, ANTON)

    await repo.upsert_service(
        chat_id=CHAT,
        owner_id=ANTON,
        car=car,
        car_slug="rav4",
        car_title="RAV4",
        kind="oil_engine",
        interval_km=10_000,
        last_km=100_000,
    )
    plans = await repo.service_plans(CHAT, ANTON)
    assert len(plans) == 1
    assert plans[0]["kind"] == "oil_engine"
    assert plans[0]["last_km"] == 100_000
    assert plans[0]["reminded_at"] is None

    # Повторный апсерт обновляет, а не плодит дубли
    await repo.upsert_service(
        chat_id=CHAT,
        owner_id=ANTON,
        car=car,
        car_slug="rav4",
        car_title="RAV4",
        kind="oil_engine",
        interval_km=15_000,
        last_km=100_000,
    )
    plans = await repo.service_plans(CHAT, ANTON)
    assert len(plans) == 1 and plans[0]["interval_km"] == 15_000

    await repo.mark_reminded(CHAT, ANTON, "rav4", "oil_engine", now)
    plans = await repo.service_plans(CHAT, ANTON)
    assert plans[0]["reminded_at"] is not None

    await repo.drop_service(CHAT, ANTON, "rav4", "oil_engine")
    assert await repo.service_plans(CHAT, ANTON) == []


async def test_dm_binding(repo):
    assert await repo.dm_chat(ANTON) is None
    await repo.bind_dm(ANTON, CHAT)
    assert await repo.dm_chat(ANTON) == CHAT


async def test_chats_of_person(repo):
    other_chat = -1009999999999
    await repo.upsert_person(CHAT, ANTON, "Антон")
    await repo.upsert_person(other_chat, ANTON, "Антон")
    assert sorted(await repo.chats_of_person(ANTON)) == sorted([CHAT, other_chat])
