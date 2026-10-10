"""Каталог позиций обслуживания и расчёт, что и когда пора менять.

Позиция срабатывает по двум осям сразу: по пробегу и по времени. Масло — это
в основном километры, тормозная жидкость и антифриз — в основном годы, а
страховка вообще только дата. Что наступит раньше, то и показываем.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from .mileage import MileageEstimate

# Напоминаем, когда осталось меньше этого запаса по пробегу...
REMIND_KM = 500
# ...или меньше этого запаса по времени
REMIND_DAYS = 14
# Порог по пробегу плавающий: тому, кто наматывает по 200 км в день, 500 км
# осталось бы всего на пару суток. Берём максимум из REMIND_KM и стольких дней
# езды по его собственному темпу.
REMIND_DAYS_OF_DRIVING = 14
# Повторное напоминание по одной и той же позиции не чаще раза в неделю
REMIND_COOLDOWN_DAYS = 7


@dataclass(frozen=True, slots=True)
class ServiceKind:
    """Позиция из каталога с типовыми интервалами."""

    key: str
    title: str
    default_km: int | None
    default_days: int | None
    icon: str

    @property
    def by_time_only(self) -> bool:
        return self.default_km is None


CATALOG: tuple[ServiceKind, ...] = (
    ServiceKind("oil_engine", "Масло двигателя", 10_000, 365, "oil"),
    ServiceKind("filter_air", "Воздушный фильтр", 20_000, None, "filter"),
    ServiceKind("filter_cabin", "Салонный фильтр", 15_000, 365, "filter"),
    ServiceKind("filter_fuel", "Топливный фильтр", 30_000, None, "filter"),
    ServiceKind("spark_plugs", "Свечи зажигания", 30_000, None, "spark"),
    ServiceKind("brake_pads", "Тормозные колодки", 40_000, None, "brake"),
    ServiceKind("brake_fluid", "Тормозная жидкость", 60_000, 730, "brake"),
    ServiceKind("coolant", "Антифриз", 60_000, 1095, "coolant"),
    ServiceKind("oil_gearbox", "Масло АКПП", 60_000, None, "oil"),
    ServiceKind("oil_transfer", "Масло раздатки", 60_000, None, "oil"),
    ServiceKind("timing", "Ремень или цепь ГРМ", 100_000, None, "timing"),
    ServiceKind("tires_seasonal", "Сезонная смена резины", None, 180, "tire"),
    ServiceKind("insurance", "Страховка", None, 365, "doc"),
    ServiceKind("inspection", "Техосмотр", None, 730, "doc"),
)

BY_KEY: dict[str, ServiceKind] = {kind.key: kind for kind in CATALOG}


@dataclass(slots=True)
class ServicePlan:
    """Настроенная позиция обслуживания конкретной машины."""

    chat_id: int
    owner_id: int
    car_slug: str
    car_title: str
    kind: str
    interval_km: int | None = None
    interval_days: int | None = None
    last_km: int | None = None
    last_at: datetime | None = None
    next_km: int | None = None
    next_at: datetime | None = None
    reminded_at: datetime | None = None

    @property
    def title(self) -> str:
        found = BY_KEY.get(self.kind)
        return found.title if found else self.kind

    @property
    def icon(self) -> str:
        found = BY_KEY.get(self.kind)
        return found.icon if found else "wrench"

    @property
    def configured(self) -> bool:
        """Позиция чего-то стоит, только если по ней можно посчитать срок."""
        return self.due_km is not None or self.due_at is not None

    @property
    def due_km(self) -> int | None:
        """Пробег, на котором пора менять."""
        if self.next_km is not None:
            return self.next_km
        if self.last_km is not None and self.interval_km:
            return self.last_km + self.interval_km
        return None

    @property
    def due_at(self) -> datetime | None:
        """Дата, к которой пора менять."""
        if self.next_at is not None:
            return self.next_at
        if self.last_at is not None and self.interval_days:
            return self.last_at + timedelta(days=self.interval_days)
        return None


@dataclass(slots=True)
class ServiceDue:
    """Насколько позиция близка к сроку — уже с учётом оценки пробега."""

    plan: ServicePlan
    km_left: int | None
    days_left: int | None
    # Пробег оценивался, а не измерялся: цифру показываем с тильдой
    estimated: bool

    @property
    def overdue(self) -> bool:
        return (self.km_left is not None and self.km_left < 0) or (
            self.days_left is not None and self.days_left < 0
        )

    @property
    def urgency(self) -> float:
        """Чем меньше, тем срочнее. Нужна только для сортировки."""
        parts: list[float] = []
        if self.km_left is not None:
            parts.append(self.km_left / max(REMIND_KM, 1))
        if self.days_left is not None:
            parts.append(self.days_left / REMIND_DAYS)
        return min(parts) if parts else float("inf")

    def reason(self) -> str:
        """Короткая формулировка «через сколько», по ближайшей из двух осей."""
        by_km = self.km_left is not None
        by_days = self.days_left is not None

        km_is_closer = by_km and (
            not by_days
            or self.km_left / max(REMIND_KM, 1) <= self.days_left / REMIND_DAYS
        )
        if km_is_closer:
            left = self.km_left or 0
            if left < 0:
                return f"просрочено на {abs(left):,} км".replace(",", " ")
            prefix = "~" if self.estimated else ""
            return f"через {prefix}{left:,} км".replace(",", " ")

        left = self.days_left or 0
        if left < 0:
            return f"просрочено на {abs(left)} дн."
        return f"через {left} дн."


def _threshold_km(mileage: MileageEstimate | None) -> int:
    """Плавающий порог: минимум 500 км, но не меньше двух недель езды."""
    if mileage is None or mileage.km_per_day <= 0:
        return REMIND_KM
    by_pace = int(mileage.km_per_day * REMIND_DAYS_OF_DRIVING)
    return max(REMIND_KM, by_pace)


def evaluate(
    plan: ServicePlan, mileage: MileageEstimate | None, now: datetime
) -> ServiceDue | None:
    """Сколько осталось до замены. None — если позиция не настроена."""
    if not plan.configured:
        return None

    km_left: int | None = None
    estimated = False
    due_km = plan.due_km
    if due_km is not None and mileage is not None:
        km_left = due_km - mileage.km
        estimated = mileage.is_estimate

    days_left: int | None = None
    due_at = plan.due_at
    if due_at is not None:
        days_left = (due_at - now).days

    if km_left is None and days_left is None:
        return None
    return ServiceDue(plan=plan, km_left=km_left, days_left=days_left, estimated=estimated)


def is_due(due: ServiceDue, mileage: MileageEstimate | None, now: datetime) -> bool:
    """Пора ли напоминать: близко по пробегу или по времени."""
    if due.plan.reminded_at is not None:
        quiet_until = due.plan.reminded_at + timedelta(days=REMIND_COOLDOWN_DAYS)
        if now < quiet_until:
            return False

    if due.km_left is not None and due.km_left <= _threshold_km(mileage):
        return True
    return due.days_left is not None and due.days_left <= REMIND_DAYS


def upcoming(
    plans: list[ServicePlan],
    mileage: MileageEstimate | None,
    now: datetime,
    *,
    limit: int | None = None,
) -> list[ServiceDue]:
    """Все настроенные позиции по срочности — для карточек."""
    items = [
        due for plan in plans if (due := evaluate(plan, mileage, now)) is not None
    ]
    items.sort(key=lambda due: due.urgency)
    return items[:limit] if limit else items


def defaults_for(kind_key: str) -> tuple[int | None, int | None]:
    kind = BY_KEY.get(kind_key)
    if kind is None:
        return None, None
    return kind.default_km, kind.default_days


def parse_interval_km(text: str) -> int | None:
    """«10000», «10 000», «10к», «10 тыс» → 10000."""
    raw = text.strip().lower().replace(" ", "").replace(" ", "")
    raw = raw.replace("км", "").replace("тыс.", "к").replace("тыс", "к")
    multiplier = 1
    if raw.endswith("к") or raw.endswith("k"):
        multiplier = 1000
        raw = raw[:-1]
    raw = raw.replace(",", ".")
    try:
        value = Decimal(raw) * multiplier
    except Exception:
        return None
    if not 100 <= value <= 500_000:
        return None
    return int(value)


# --- Что поменяли: позиции ТО из текста траты --------------------------------

# Однозначные слова. Проверяются по началу токена, а не по лемме: pymorphy
# путается в сленге вроде «салонник» и «тормозуха»
_KIND_PREFIXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("brake_fluid", ("тормозух", "тормозн жидк")),
    ("filter_cabin", ("салонник",)),
    ("filter_air", ("воздушник",)),
    ("spark_plugs", ("свеч",)),
    ("brake_pads", ("колодк",)),
    ("coolant", ("антифриз", "тосол")),
    ("timing", ("грм",)),
    ("tires_seasonal", ("переобу", "шиномонтаж")),
    ("insurance", ("осаго", "каско", "страховк")),
    ("inspection", ("техосмотр",)),
    ("oil_transfer", ("раздатк", "редуктор")),
)

# «фильтр» с уточнением перед ним
_FILTER_KINDS = {
    "воздушный": "filter_air",
    "салонный": "filter_cabin",
    "топливный": "filter_fuel",
}

_GEARBOX_WORDS = {"акпп", "мкпп", "вариатор", "коробка", "dsg", "робот"}

_UPPER_TO = re.compile(r"(?<![А-ЯЁа-яёA-Za-z])ТО(?![А-ЯЁа-яёA-Za-z])")


def detect_kinds(text: str) -> tuple[list[str], list[str]]:
    """Какие позиции ТО упомянуты в трате.

    Возвращает два списка: что наверняка поменяли (отмечается галочкой сразу)
    и что стоит предложить (без галочки). «ТО 15000» — это почти всегда масло,
    а фильтры могли поменять, а могли и нет: пусть человек отметит сам.
    """
    from .parsing.morph import analyze

    analysis = analyze(text)
    lemmas, tokens = analysis.lemmas, analysis.tokens
    sure: list[str] = []
    maybe: list[str] = []

    def add(target: list[str], kind: str) -> None:
        if kind not in sure and kind not in maybe:
            target.append(kind)

    for kind, prefixes in _KIND_PREFIXES:
        if any(token.startswith(prefix) for token in tokens for prefix in prefixes):
            add(sure, kind)

    filters = [
        _FILTER_KINDS[previous]
        for previous, current in zip(lemmas, lemmas[1:])
        if current == "фильтр" and previous in _FILTER_KINDS
    ]
    for kind in filters:
        add(sure, kind)

    if "масло" in lemmas:
        gearbox = any(word in _GEARBOX_WORDS for word in lemmas)
        if gearbox:
            add(sure, "oil_gearbox")
        elif "oil_transfer" not in sure:
            add(sure, "oil_engine")

    is_maintenance = bool(_UPPER_TO.search(text)) or "техобслуживание" in lemmas
    if is_maintenance:
        add(sure, "oil_engine")

    # «масло + фильтры» или «ТО»: какие именно фильтры — не сказано
    if (is_maintenance or "фильтр" in lemmas) and not filters:
        add(maybe, "filter_air")
        add(maybe, "filter_cabin")

    return sure, maybe


def parse_odometer(text: str) -> int | None:
    """Пробег, который человек прислал числом: «187950», «187 950 км»."""
    raw = re.sub(r"[\s  ]|км|km|\.", "", text.strip().lower())
    if not raw.isdigit():
        return None
    value = int(raw)
    return value if 100 <= value <= 3_000_000 else None
