"""Доменные модели, общие для парсинга, хранения и рендера."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class Category(StrEnum):
    FUEL = "fuel"
    SERVICE = "service"
    TUNING = "tuning"
    FINES = "fines"
    CARE = "care"
    STATE = "state"

    @classmethod
    def _missing_(cls, value: object) -> "Category | None":
        # «Остальное» из первой версии разнесено по трём категориям. Пока
        # миграция не прошла, старые записи читаются как «уход» — туда
        # попадала большая часть: мойки, омывайка, дворники.
        if value == "other":
            return cls.CARE
        return None


CATEGORY_TITLE: dict[Category, str] = {
    Category.FUEL: "Бензин",
    Category.SERVICE: "Ремонт и ТО",
    Category.TUNING: "Тюнинг",
    Category.FINES: "Штрафы",
    Category.CARE: "Уход",
    Category.STATE: "Государство",
}

CATEGORY_EMOJI: dict[Category, str] = {
    Category.FUEL: "⛽",
    Category.SERVICE: "🔧",
    Category.TUNING: "⚡",
    Category.FINES: "🚨",
    Category.CARE: "🧽",
    Category.STATE: "🏛",
}

CATEGORY_SHORT: dict[Category, str] = {
    Category.FUEL: "бенз",
    Category.SERVICE: "ремонт",
    Category.TUNING: "тюнинг",
    Category.FINES: "штраф",
    Category.CARE: "уход",
    Category.STATE: "гос",
}


class Confidence(StrEnum):
    """Насколько бот уверен, что это трата на машину."""

    HIGH = "high"  # нашлось слово из словаря
    MEDIUM = "medium"  # нашлось похожее слово (опечатка)
    LOW = "low"  # есть только сумма с валютой, категория неизвестна


@dataclass(slots=True)
class Detection:
    """Одна распознанная трата внутри сообщения."""

    amount: Decimal
    category: Category | None
    confidence: Confidence
    # Слово из текста, по которому определилась категория
    trigger: str | None = None
    # Кусок исходного текста, описывающий именно эту трату
    note: str = ""
    liters: Decimal | None = None
    odometer: int | None = None

    @property
    def needs_category(self) -> bool:
        return self.category is None


@dataclass(slots=True)
class ParseResult:
    """Итог разбора одного сообщения."""

    detections: list[Detection] = field(default_factory=list)
    # Леммы, которых нет в словаре — кандидаты на самообучение
    unknown_lemmas: list[str] = field(default_factory=list)
    # Человек сам написал «на тачку …» — спрашивать «на тачку?» уже незачем
    explicit: bool = False
    # Почему детектор решил именно так — уходит в журнал наблюдений
    reason: str = ""
    # Суммы, которые заметили в тексте, даже если тратой их не признали
    seen_amounts: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.detections)

    @property
    def total(self) -> Decimal:
        return sum((d.amount for d in self.detections), Decimal(0))


@dataclass(slots=True)
class Person:
    tg_id: int
    name: str
    username: str | None = None


@dataclass(slots=True)
class Expense:
    chat_id: int
    author_id: int
    author_name: str
    message_id: int
    amount: Decimal
    category: Category
    note: str
    spent_at: datetime
    liters: Decimal | None = None
    odometer: int | None = None
    record_id: str | None = None


@dataclass(slots=True)
class ServiceHint:
    """Строка «кому скоро на сервис» для общей картинки."""

    person: str
    car: str
    title: str
    reason: str
    overdue: bool = False


@dataclass(slots=True)
class PersonMonth:
    """Агрегат по человеку за месяц — основа для картинки."""

    tg_id: int
    name: str
    total: Decimal
    by_category: dict[Category, Decimal]
    count: int
    rank: int = 0
    prev_rank: int | None = None
    # Звания месяца, самое яркое первым. Не больше двух на человека
    titles: list[str] = field(default_factory=list)
    avatar: bytes | None = None
    # Заполняется из гаража: машина, пробег и цена километра
    car_title: str | None = None
    odometer: str | None = None
    month_km: int | None = None
    rub_per_km: Decimal | None = None
    cost_low_data: bool = False
    # Для званий: расход, число заправок и трат глубокой ночью
    liters_per_100: Decimal | None = None
    fuel_count: int = 0
    night_count: int = 0

    @property
    def title(self) -> str | None:
        """Главное звание — для мест, где помещается только одно."""
        return self.titles[0] if self.titles else None

    @property
    def rank_delta(self) -> int | None:
        """+N — поднялся на N мест, -N — опустился. None — новичок."""
        if self.prev_rank is None:
            return None
        return self.prev_rank - self.rank


@dataclass(slots=True)
class MonthReport:
    chat_id: int
    year: int
    month: int
    people: list[PersonMonth]
    top_expenses: list[Expense]
    total: Decimal
    prev_total: Decimal
    by_category: dict[Category, Decimal]
    fun_fact: str | None = None
    # Сводка по парку: кому скоро на сервис
    service_soon: list[ServiceHint] = field(default_factory=list)
    total_km: int | None = None
