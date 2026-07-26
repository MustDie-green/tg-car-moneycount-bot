"""Конфигурация бота. Все значения читаются из .env с префиксом CARBOT_."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="CARBOT_", extra="ignore"
    )

    # --- Telegram ---
    bot_token: str = ""
    # Чаты, где бот работает. Пусто = все чаты, куда его добавили.
    allowed_chats: str = ""

    # --- SurrealDB ---
    # surrealkv://path — встроенный режим (файл, без внешнего сервиса)
    # ws://localhost:8000/rpc — отдельно поднятый сервер
    db_url: str = "surrealkv://./data/carbot.db"
    db_namespace: str = "carbot"
    db_database: str = "carbot"
    db_user: str = "root"
    db_password: str = "root"

    # --- Логика трат ---
    timezone: str = "Europe/Moscow"
    currency: str = "₽"
    min_amount: int = 10
    max_amount: int = 5_000_000
    # Сколько живёт вопрос «На тачку?» до автоудаления, секунд
    pending_ttl: int = 600
    # Пороги для «уважительных» реакций
    big_expense: int = 10_000
    huge_expense: int = 50_000

    # --- Детект ---
    fuzzy_threshold: int = 90
    # Сколько подтверждений нужно, чтобы новое слово попало в словарь чата
    learn_threshold: int = 2

    # --- Ежемесячный отчёт ---
    report_day: int = 1
    report_hour: int = 12
    report_minute: int = 0

    # --- Рендер ---
    fonts_dir: Path = ROOT / "assets" / "fonts"
    avatars_enabled: bool = True
    cache_dir: Path = ROOT / "data" / "cache"

    @property
    def allowed_chat_ids(self) -> set[int]:
        raw = self.allowed_chats.strip()
        if not raw:
            return set()
        return {int(x) for x in raw.replace(";", ",").split(",") if x.strip()}


settings = Settings()
