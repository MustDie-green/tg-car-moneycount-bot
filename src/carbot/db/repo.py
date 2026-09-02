"""Все запросы к базе. Выше этого слоя SurrealQL не встречается."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from surrealdb import RecordID

from ..models import Category, Detection, Expense
from .client import Database


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value or 0))


def _row_to_expense(row: dict[str, Any]) -> Expense:
    return Expense(
        chat_id=row["chat_id"],
        author_id=row["author_id"],
        author_name=row.get("author_name", ""),
        message_id=row["message_id"],
        amount=_decimal(row["amount"]),
        category=Category(row["category"]),
        note=row.get("note", ""),
        spent_at=row["spent_at"],
        liters=_decimal(row["liters"]) if row.get("liters") is not None else None,
        odometer=row.get("odometer"),
        record_id=str(row["id"]) if row.get("id") else None,
    )


class Repo:
    def __init__(self, db: Database) -> None:
        self.db = db

    # --- Участники и чаты ----------------------------------------------------

    async def touch_chat(self, chat_id: int, title: str = "") -> None:
        await self.db.query(
            "UPSERT chat:[$chat] SET chat_id = $chat, title = $title, "
            "last_seen = time::now()",
            {"chat": chat_id, "title": title},
        )

    async def chat_ids(self) -> list[int]:
        rows = await self.db.query("SELECT chat_id FROM chat")
        return [row["chat_id"] for row in rows or []]

    async def upsert_person(
        self, chat_id: int, tg_id: int, name: str, username: str | None = None
    ) -> RecordID:
        await self.db.query(
            "UPSERT person:[$chat, $tg] SET chat_id = $chat, tg_id = $tg, "
            "name = $name, username = $username",
            {"chat": chat_id, "tg": tg_id, "name": name, "username": username},
        )
        return RecordID("person", [chat_id, tg_id])

    async def person_names(self, chat_id: int) -> dict[int, str]:
        rows = await self.db.query(
            "SELECT tg_id, name FROM person WHERE chat_id = $chat", {"chat": chat_id}
        )
        return {row["tg_id"]: row["name"] for row in rows or []}

    # --- Траты ---------------------------------------------------------------

    async def save_expenses(
        self,
        *,
        chat_id: int,
        author_id: int,
        author_name: str,
        message_id: int,
        detections: list[Detection],
        spent_at: datetime,
        car: RecordID | None = None,
    ) -> list[Expense]:
        author = await self.upsert_person(chat_id, author_id, author_name)
        rows = [
            {
                "chat_id": chat_id,
                "author": author,
                "author_id": author_id,
                "author_name": author_name,
                "message_id": message_id,
                "amount": detection.amount,
                "category": str(detection.category or Category.OTHER),
                "note": detection.note,
                "liters": detection.liters,
                "odometer": detection.odometer,
                "car": car,
                "spent_at": spent_at,
            }
            for detection in detections
        ]
        created = await self.db.query("INSERT INTO expense $rows", {"rows": rows})
        return [_row_to_expense(row) for row in created or []]

    async def expenses_by_message(self, chat_id: int, message_id: int) -> list[Expense]:
        rows = await self.db.query(
            "SELECT * FROM expense WHERE chat_id = $chat AND message_id = $msg",
            {"chat": chat_id, "msg": message_id},
        )
        return [_row_to_expense(row) for row in rows or []]

    async def last_expenses(self, chat_id: int, author_id: int) -> list[Expense]:
        """Последняя записанная пачка трат автора — для /undo."""
        rows = await self.db.query(
            "SELECT * FROM expense WHERE chat_id = $chat AND author_id = $author "
            "ORDER BY created_at DESC LIMIT 1",
            {"chat": chat_id, "author": author_id},
        )
        if not rows:
            return []
        return await self.expenses_by_message(chat_id, rows[0]["message_id"])

    async def delete_expenses(self, chat_id: int, message_id: int) -> int:
        existing = await self.expenses_by_message(chat_id, message_id)
        if existing:
            await self.db.query(
                "DELETE expense WHERE chat_id = $chat AND message_id = $msg",
                {"chat": chat_id, "msg": message_id},
            )
        return len(existing)

    # --- Агрегаты ------------------------------------------------------------

    async def totals_by_person(
        self, chat_id: int, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """[{author_id, category, total, cnt}] за период."""
        rows = await self.db.query(
            "SELECT author_id, category, math::sum(amount) AS total, "
            "count() AS cnt FROM expense "
            "WHERE chat_id = $chat AND spent_at >= $start AND spent_at < $end "
            "GROUP BY author_id, category",
            {"chat": chat_id, "start": start, "end": end},
        )
        return [
            {
                "author_id": row["author_id"],
                "category": Category(row["category"]),
                "total": _decimal(row["total"]),
                "cnt": row["cnt"],
            }
            for row in rows or []
        ]

    async def top_expenses(
        self, chat_id: int, start: datetime, end: datetime, limit: int = 3
    ) -> list[Expense]:
        rows = await self.db.query(
            "SELECT * FROM expense "
            "WHERE chat_id = $chat AND spent_at >= $start AND spent_at < $end "
            f"ORDER BY amount DESC LIMIT {int(limit)}",
            {"chat": chat_id, "start": start, "end": end},
        )
        return [_row_to_expense(row) for row in rows or []]

    async def expenses_between(
        self, chat_id: int, start: datetime, end: datetime, author_id: int | None = None
    ) -> list[Expense]:
        clause = " AND author_id = $author" if author_id is not None else ""
        rows = await self.db.query(
            "SELECT * FROM expense "
            f"WHERE chat_id = $chat AND spent_at >= $start AND spent_at < $end{clause} "
            "ORDER BY spent_at ASC",
            {"chat": chat_id, "start": start, "end": end, "author": author_id},
        )
        return [_row_to_expense(row) for row in rows or []]

    async def expenses_of_person(self, chat_id: int, author_id: int) -> list[Expense]:
        """Вся история трат человека — цена километра считает по своему окну."""
        rows = await self.db.query(
            "SELECT * FROM expense WHERE chat_id = $chat AND author_id = $author "
            "ORDER BY spent_at ASC",
            {"chat": chat_id, "author": author_id},
        )
        return [_row_to_expense(row) for row in rows or []]

    async def last_expense_at(self, chat_id: int, author_id: int) -> datetime | None:
        rows = await self.db.query(
            "SELECT spent_at FROM expense WHERE chat_id = $chat AND author_id = $author "
            "ORDER BY spent_at DESC LIMIT 1",
            {"chat": chat_id, "author": author_id},
        )
        return rows[0]["spent_at"] if rows else None

    async def fuel_series(self, chat_id: int, author_id: int) -> list[Expense]:
        """Заправки с указанным пробегом — из них считается расход."""
        rows = await self.db.query(
            "SELECT * FROM expense WHERE chat_id = $chat AND author_id = $author "
            "AND category = 'fuel' AND odometer != NONE ORDER BY odometer ASC",
            {"chat": chat_id, "author": author_id},
        )
        return [_row_to_expense(row) for row in rows or []]

    # --- Самообучаемый словарь ----------------------------------------------

    async def learned_words(
        self, chat_id: int, threshold: int = 2
    ) -> dict[str, Category]:
        rows = await self.db.query(
            "SELECT word, category FROM lexicon "
            "WHERE chat_id = $chat AND category != NONE "
            "AND votes_yes >= $threshold AND votes_yes > votes_no * 2",
            {"chat": chat_id, "threshold": threshold},
        )
        return {row["word"]: Category(row["category"]) for row in rows or []}

    async def vote_words(
        self,
        chat_id: int,
        words: list[str],
        category: Category | None,
        *,
        positive: bool,
    ) -> None:
        if not words:
            return
        field = "votes_yes" if positive else "votes_no"
        for word in words:
            await self.db.query(
                f"UPSERT lexicon:[$chat, $word] SET chat_id = $chat, word = $word, "
                f"category = $category, {field} += 1, updated_at = time::now()",
                {
                    "chat": chat_id,
                    "word": word,
                    "category": str(category) if category else None,
                },
            )

    async def lexicon_stats(self, chat_id: int) -> list[dict[str, Any]]:
        rows = await self.db.query(
            "SELECT word, category, votes_yes, votes_no FROM lexicon "
            "WHERE chat_id = $chat ORDER BY votes_yes DESC LIMIT 20",
            {"chat": chat_id},
        )
        return list(rows or [])

    # --- Ожидающие подтверждения --------------------------------------------

    async def save_pending(
        self,
        *,
        chat_id: int,
        message_id: int,
        question_id: int,
        author_id: int,
        author_name: str,
        username: str | None,
        detections: list[Detection],
        unknown_lemmas: list[str],
        spent_at: datetime,
        ttl_seconds: int,
    ) -> None:
        payload = json.dumps(
            {
                "detections": [_detection_to_json(d) for d in detections],
                "unknown_lemmas": unknown_lemmas,
            },
            ensure_ascii=False,
        )
        await self.db.query(
            "UPSERT pending:[$chat, $msg] SET chat_id = $chat, message_id = $msg, "
            "question_id = $question, author_id = $author, author_name = $name, "
            "username = $username, payload = $payload, spent_at = $spent_at, "
            "expires_at = $expires",
            {
                "chat": chat_id,
                "msg": message_id,
                "question": question_id,
                "author": author_id,
                "name": author_name,
                "username": username,
                "payload": payload,
                "spent_at": spent_at,
                "expires": spent_at + timedelta(seconds=ttl_seconds),
            },
        )

    async def get_pending(self, chat_id: int, message_id: int) -> dict[str, Any] | None:
        rows = await self.db.query(
            "SELECT * FROM pending:[$chat, $msg]", {"chat": chat_id, "msg": message_id}
        )
        if not rows:
            return None
        row = dict(rows[0])
        row["payload"] = json.loads(row["payload"])
        return row

    async def drop_pending(self, chat_id: int, message_id: int) -> None:
        await self.db.query(
            "DELETE pending:[$chat, $msg]", {"chat": chat_id, "msg": message_id}
        )

    async def expired_pendings(self, now: datetime) -> list[dict[str, Any]]:
        rows = await self.db.query(
            "SELECT chat_id, message_id, question_id FROM pending WHERE expires_at < $now",
            {"now": now},
        )
        return list(rows or [])

    async def has_pending(self, chat_id: int, message_id: int) -> bool:
        return await self.get_pending(chat_id, message_id) is not None

    # --- Машины --------------------------------------------------------------

    async def add_car(self, chat_id: int, owner_id: int, title: str) -> None:
        slug = title.lower().strip()[:40]
        await self.db.query(
            "UPDATE car SET active = false WHERE chat_id = $chat AND owner_id = $owner",
            {"chat": chat_id, "owner": owner_id},
        )
        await self.db.query(
            "UPSERT car:[$chat, $owner, $slug] SET chat_id = $chat, "
            "owner_id = $owner, title = $title, active = true",
            {"chat": chat_id, "owner": owner_id, "slug": slug, "title": title},
        )

    async def active_car(self, chat_id: int, owner_id: int) -> RecordID | None:
        rows = await self.db.query(
            "SELECT id FROM car WHERE chat_id = $chat AND owner_id = $owner "
            "AND active = true LIMIT 1",
            {"chat": chat_id, "owner": owner_id},
        )
        return rows[0]["id"] if rows else None

    async def cars(self, chat_id: int, owner_id: int) -> list[dict[str, Any]]:
        rows = await self.db.query(
            # SurrealDB требует, чтобы поле сортировки было в проекции
            "SELECT id, title, active, monthly_km, created_at FROM car "
            "WHERE chat_id = $chat AND owner_id = $owner ORDER BY created_at ASC",
            {"chat": chat_id, "owner": owner_id},
        )
        return list(rows or [])

    async def all_cars(self, chat_id: int) -> list[dict[str, Any]]:
        """Весь парк чата — для /cars и общей карточки."""
        rows = await self.db.query(
            "SELECT id, owner_id, title, active, monthly_km, created_at FROM car "
            "WHERE chat_id = $chat ORDER BY owner_id ASC, created_at ASC",
            {"chat": chat_id},
        )
        return list(rows or [])

    async def set_monthly_km(self, car: RecordID, monthly_km: int) -> None:
        await self.db.query(
            "UPDATE $car SET monthly_km = $km", {"car": car, "km": monthly_km}
        )

    # --- Пробег --------------------------------------------------------------

    async def add_odometer(
        self,
        *,
        chat_id: int,
        owner_id: int,
        km: int,
        at: datetime,
        car: RecordID | None = None,
    ) -> None:
        await self.db.query(
            "CREATE odometer SET chat_id = $chat, owner_id = $owner, "
            "car = $car, km = $km, at = $at",
            {"chat": chat_id, "owner": owner_id, "car": car, "km": km, "at": at},
        )

    async def odometer_readings(
        self, chat_id: int, owner_id: int
    ) -> list[tuple[datetime, int]]:
        """Все замеры пробега человека: и отдельные, и вписанные в траты."""
        standalone = await self.db.query(
            "SELECT km, at FROM odometer WHERE chat_id = $chat AND owner_id = $owner",
            {"chat": chat_id, "owner": owner_id},
        )
        from_expenses = await self.db.query(
            "SELECT odometer, spent_at FROM expense "
            "WHERE chat_id = $chat AND author_id = $owner AND odometer != NONE",
            {"chat": chat_id, "owner": owner_id},
        )
        readings = [(row["at"], row["km"]) for row in standalone or []]
        readings += [
            (row["spent_at"], row["odometer"]) for row in from_expenses or []
        ]
        return sorted(readings, key=lambda item: item[0])

    # --- Обслуживание --------------------------------------------------------

    async def service_plans(
        self, chat_id: int, owner_id: int, car_slug: str | None = None
    ) -> list[dict[str, Any]]:
        clause = " AND car_slug = $slug" if car_slug else ""
        rows = await self.db.query(
            "SELECT chat_id, owner_id, car_slug, car_title, kind, interval_km, "
            "interval_days, last_km, last_at, next_km, next_at, reminded_at "
            f"FROM service WHERE chat_id = $chat AND owner_id = $owner{clause}",
            {"chat": chat_id, "owner": owner_id, "slug": car_slug},
        )
        return list(rows or [])

    async def all_service_plans(self, chat_id: int) -> list[dict[str, Any]]:
        rows = await self.db.query(
            "SELECT chat_id, owner_id, car_slug, car_title, kind, interval_km, "
            "interval_days, last_km, last_at, next_km, next_at, reminded_at "
            "FROM service WHERE chat_id = $chat",
            {"chat": chat_id},
        )
        return list(rows or [])

    async def upsert_service(
        self,
        *,
        chat_id: int,
        owner_id: int,
        car: RecordID,
        car_slug: str,
        car_title: str,
        kind: str,
        interval_km: int | None = None,
        interval_days: int | None = None,
        last_km: int | None = None,
        last_at: datetime | None = None,
        next_km: int | None = None,
        next_at: datetime | None = None,
    ) -> None:
        await self.db.query(
            "UPSERT service:[$chat, $owner, $slug, $kind] SET chat_id = $chat, "
            "owner_id = $owner, car = $car, car_slug = $slug, car_title = $title, "
            "kind = $kind, interval_km = $ikm, interval_days = $idays, "
            "last_km = $lkm, last_at = $lat, next_km = $nkm, next_at = $nat, "
            "updated_at = time::now()",
            {
                "chat": chat_id,
                "owner": owner_id,
                "car": car,
                "slug": car_slug,
                "title": car_title,
                "kind": kind,
                "ikm": interval_km,
                "idays": interval_days,
                "lkm": last_km,
                "lat": last_at,
                "nkm": next_km,
                "nat": next_at,
            },
        )

    async def drop_service(
        self, chat_id: int, owner_id: int, car_slug: str, kind: str
    ) -> None:
        await self.db.query(
            "DELETE service:[$chat, $owner, $slug, $kind]",
            {"chat": chat_id, "owner": owner_id, "slug": car_slug, "kind": kind},
        )

    async def mark_reminded(
        self, chat_id: int, owner_id: int, car_slug: str, kind: str, at: datetime
    ) -> None:
        await self.db.query(
            "UPDATE service:[$chat, $owner, $slug, $kind] SET reminded_at = $at",
            {
                "chat": chat_id,
                "owner": owner_id,
                "slug": car_slug,
                "kind": kind,
                "at": at,
            },
        )

    # --- Журнал наблюдений детектора -----------------------------------------

    async def log_detection(
        self,
        *,
        chat_id: int,
        message_id: int,
        author_id: int,
        author_name: str,
        text: str,
        reason: str,
        amounts: list[str],
        asked: bool,
    ) -> None:
        """Пишем каждое сообщение, где мелькнула похожая на деньги сумма."""
        await self.db.query(
            "UPSERT detect_log:[$chat, $msg] SET chat_id = $chat, message_id = $msg, "
            "author_id = $author, author_name = $name, text = $text, "
            "reason = $reason, amounts = $amounts, asked = $asked, at = time::now()",
            {
                "chat": chat_id,
                "msg": message_id,
                "author": author_id,
                "name": author_name,
                "text": text[:300],
                "reason": reason,
                "amounts": ",".join(amounts),
                "asked": asked,
            },
        )

    async def log_answer(self, chat_id: int, message_id: int, answer: str) -> None:
        """Чем кончился заданный вопрос: yes, no или timeout."""
        await self.db.query(
            "UPDATE detect_log:[$chat, $msg] SET answer = $answer",
            {"chat": chat_id, "msg": message_id, "answer": answer},
        )

    async def detect_log(self, chat_id: int, limit: int = 2000) -> list[dict[str, Any]]:
        rows = await self.db.query(
            "SELECT message_id, author_name, text, reason, amounts, asked, answer, at "
            f"FROM detect_log WHERE chat_id = $chat ORDER BY at DESC LIMIT {int(limit)}",
            {"chat": chat_id},
        )
        return list(rows or [])

    async def detect_stats(self, chat_id: int) -> list[dict[str, Any]]:
        """Сколько раз каждый вердикт встречался и чем кончился."""
        rows = await self.db.query(
            "SELECT reason, answer, count() AS cnt FROM detect_log "
            "WHERE chat_id = $chat GROUP BY reason, answer",
            {"chat": chat_id},
        )
        return list(rows or [])

    # --- Личка ---------------------------------------------------------------

    async def bind_dm(self, tg_id: int, chat_id: int) -> None:
        """Какой чат обслуживаем, когда человек пишет боту напрямую."""
        await self.db.query(
            "UPSERT dm:[$tg] SET tg_id = $tg, chat_id = $chat, "
            "updated_at = time::now()",
            {"tg": tg_id, "chat": chat_id},
        )

    async def dm_chat(self, tg_id: int) -> int | None:
        rows = await self.db.query("SELECT chat_id FROM dm:[$tg]", {"tg": tg_id})
        return rows[0]["chat_id"] if rows else None

    async def chats_of_person(self, tg_id: int) -> list[int]:
        rows = await self.db.query(
            "SELECT chat_id FROM person WHERE tg_id = $tg", {"tg": tg_id}
        )
        return [row["chat_id"] for row in rows or []]


def _detection_to_json(detection: Detection) -> dict[str, Any]:
    data = asdict(detection)
    data["amount"] = str(detection.amount)
    data["category"] = str(detection.category) if detection.category else None
    data["confidence"] = str(detection.confidence)
    data["liters"] = str(detection.liters) if detection.liters is not None else None
    return data


def detection_from_json(data: dict[str, Any]) -> Detection:
    from ..models import Confidence

    return Detection(
        amount=Decimal(data["amount"]),
        category=Category(data["category"]) if data.get("category") else None,
        confidence=Confidence(data["confidence"]),
        trigger=data.get("trigger"),
        note=data.get("note", ""),
        liters=Decimal(data["liters"]) if data.get("liters") else None,
        odometer=data.get("odometer"),
    )
