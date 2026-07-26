"""Подключение к SurrealDB.

Поддерживаются два режима:
  surrealkv://./data/carbot.db — встроенный, база живёт в файле внутри процесса
  ws://localhost:8000/rpc      — отдельно поднятый сервер
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from surrealdb import AsyncSurreal

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.surql")
_REMOTE_SCHEMES = ("ws://", "wss://", "http://", "https://")


class Database:
    def __init__(
        self,
        url: str,
        namespace: str,
        database: str,
        user: str = "root",
        password: str = "root",
    ) -> None:
        self.url = url
        self.namespace = namespace
        self.database = database
        self.user = user
        self.password = password
        self._db: Any = None
        self._lock = asyncio.Lock()

    @property
    def is_remote(self) -> bool:
        return self.url.startswith(_REMOTE_SCHEMES)

    async def connect(self) -> None:
        async with self._lock:
            if self._db is not None:
                return
            self._ensure_storage_dir()
            db = AsyncSurreal(self.url)
            await db.connect()
            if self.is_remote:
                await db.signin({"username": self.user, "password": self.password})
            await db.use(self.namespace, self.database)
            self._db = db
            log.info("подключились к SurrealDB: %s", self.url)
        await self.apply_schema()

    def _ensure_storage_dir(self) -> None:
        if self.is_remote or "://" not in self.url:
            return
        path = Path(self.url.split("://", 1)[1])
        if path.name:
            path.parent.mkdir(parents=True, exist_ok=True)

    async def apply_schema(self) -> None:
        statements = SCHEMA_PATH.read_text(encoding="utf-8")
        await self.query(statements)

    async def close(self) -> None:
        async with self._lock:
            if self._db is None:
                return
            try:
                await self._db.close()
            finally:
                self._db = None

    async def query(self, sql: str, variables: dict[str, Any] | None = None) -> Any:
        if self._db is None:
            await self.connect()
        return await self._db.query(sql, variables or {})

    async def first(
        self, sql: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        rows = await self.query(sql, variables)
        if isinstance(rows, list) and rows:
            return rows[0]
        return None
