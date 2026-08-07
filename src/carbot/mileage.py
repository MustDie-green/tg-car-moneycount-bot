"""Оценка текущего пробега по редким замерам одометра.

Замеры приходят когда придётся: кто-то пишет пробег на каждой заправке, кто-то
раз в квартал. Между замерами пробег приходится достраивать — иначе напоминать
о замене масла не по чему.

Считаем средний дневной пробег по отрезкам между соседними замерами, но свежие
отрезки весят больше: человек мог сменить работу, пересесть на метро или уехать
в отпуск, и прошлогодний темп к сегодняшнему дню отношения не имеет.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

# Через столько дней вес отрезка падает вдвое
HALF_LIFE_DAYS = 90
# Отрезки быстрее этого — опечатка в одометре, а не езда
MAX_KM_PER_DAY = 500
# После стольких дней без замеров оценка считается протухшей
STALE_AFTER_DAYS = 60
# Пока замеров меньше двух, свою оценку построить не из чего
MIN_READINGS = 2

ZERO = Decimal(0)


@dataclass(slots=True)
class MileageEstimate:
    """Что бот думает о текущем пробеге машины."""

    km: int
    # Последний фактический замер и когда он был
    known_km: int
    known_at: datetime
    km_per_day: Decimal
    # km получен экстраполяцией, а не замером
    is_estimate: bool
    # С последнего замера прошло слишком много — верить цифре опасно
    stale: bool
    readings: int
    # Темп взят со слов владельца, а не посчитан по замерам
    from_owner: bool = False

    @property
    def monthly_km(self) -> int:
        return int(self.km_per_day * 30)

    def format_km(self) -> str:
        prefix = "~" if self.is_estimate else ""
        return f"{prefix}{self.km:,}".replace(",", " ")


@dataclass(slots=True)
class Reading:
    at: datetime
    km: int


def collect(readings: list[Reading]) -> list[Reading]:
    """Сортирует по дате и выбрасывает нефизичное: скачки назад и дубли."""
    ordered = sorted(readings, key=lambda item: item.at)
    clean: list[Reading] = []
    for reading in ordered:
        if reading.km <= 0:
            continue
        # Одометр не крутится назад: меньший пробег позже — это опечатка
        if clean and reading.km < clean[-1].km:
            continue
        if clean and reading.km == clean[-1].km:
            continue
        clean.append(reading)
    return clean


def _pace(readings: list[Reading], now: datetime) -> Decimal | None:
    """Взвешенный километраж в день: свежие отрезки весят больше."""
    weighted_sum = ZERO
    weights = ZERO

    for previous, current in zip(readings, readings[1:]):
        days = Decimal((current.at - previous.at).total_seconds()) / 86400
        if days < 1:
            continue
        distance = current.km - previous.km
        if distance <= 0:
            continue
        speed = Decimal(distance) / days
        if speed > MAX_KM_PER_DAY:
            continue

        # Возраст меряем от середины отрезка: так длинный старый отрезок
        # не выглядит свежее, чем он есть
        middle = previous.at + (current.at - previous.at) / 2
        age_days = Decimal((now - middle).total_seconds()) / 86400
        weight = Decimal(0.5) ** (max(age_days, ZERO) / HALF_LIFE_DAYS)

        weighted_sum += speed * weight
        weights += weight

    if weights <= 0:
        return None
    return weighted_sum / weights


def estimate(
    readings: list[Reading],
    now: datetime,
    *,
    owner_monthly_km: int | None = None,
) -> MileageEstimate | None:
    """Текущий пробег: последний замер плюс достроенное с тех пор.

    Пока замеров меньше двух, темп взять неоткуда — тогда идёт в ход цифра,
    которую владелец назвал сам при настройке. Без неё оценки нет.
    """
    clean = collect(readings)
    if not clean:
        return None

    last = clean[-1]
    days_since = Decimal((now - last.at).total_seconds()) / 86400
    days_since = max(days_since, ZERO)

    pace = _pace(clean, now) if len(clean) >= MIN_READINGS else None
    from_owner = False
    if pace is None and owner_monthly_km:
        pace = Decimal(owner_monthly_km) / 30
        from_owner = True

    if pace is None:
        # Темпа нет — отдаём голый замер, без экстраполяции
        return MileageEstimate(
            km=last.km,
            known_km=last.km,
            known_at=last.at,
            km_per_day=ZERO,
            is_estimate=False,
            stale=days_since > STALE_AFTER_DAYS,
            readings=len(clean),
        )

    driven = int(pace * days_since)
    return MileageEstimate(
        km=last.km + driven,
        known_km=last.km,
        known_at=last.at,
        km_per_day=pace.quantize(Decimal("0.1")),
        is_estimate=driven > 0,
        stale=days_since > STALE_AFTER_DAYS,
        readings=len(clean),
        from_owner=from_owner,
    )


def distance_between(
    readings: list[Reading], start: datetime, end: datetime
) -> int | None:
    """Сколько накатано за период — по замерам, попавшим в его границы.

    Нужно для строки «проехал за месяц». Если замеров внутри периода меньше
    двух, честного ответа нет: одна точка не задаёт отрезок.
    """
    inside = [r for r in collect(readings) if start <= r.at < end]
    if len(inside) < 2:
        return None
    return inside[-1].km - inside[0].km


def window(readings: list[Reading]) -> tuple[Reading, Reading] | None:
    """Первый и последний замер — окно, внутри которого пробег известен."""
    clean = collect(readings)
    if len(clean) < 2:
        return None
    return clean[0], clean[-1]
