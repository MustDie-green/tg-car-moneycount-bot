"""Оценка пробега и цена километра — самые нетривиальные расчёты в боте."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from carbot import cost, mileage, service
from carbot.mileage import Reading
from carbot.models import Category, Expense

NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def days_ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


def reading(days: int, km: int) -> Reading:
    return Reading(at=days_ago(days), km=km)


def expense(days: int, amount, category=Category.SERVICE, odometer=None):
    return Expense(
        chat_id=-100,
        author_id=1,
        author_name="Валера",
        message_id=1,
        amount=Decimal(amount),
        category=category,
        note="",
        spent_at=days_ago(days),
        odometer=odometer,
    )


# --- Чистка замеров ----------------------------------------------------------


def test_collect_drops_backward_and_duplicate_readings():
    readings = [
        reading(90, 100_000),
        reading(60, 101_000),
        reading(45, 99_000),  # одометр назад не крутится — опечатка
        reading(30, 101_000),  # дубль предыдущего значения
        reading(10, 102_000),
    ]
    clean = mileage.collect(readings)
    assert [r.km for r in clean] == [100_000, 101_000, 102_000]


# --- Темп и экстраполяция ----------------------------------------------------


def test_estimate_extrapolates_from_pace():
    """1000 км за 30 дней = 33,3 км/день, плюс 10 дней с последнего замера."""
    readings = [reading(40, 100_000), reading(10, 101_000)]
    result = mileage.estimate(readings, NOW)
    assert result is not None
    assert result.known_km == 101_000
    assert result.is_estimate
    # 101 000 + 33,3 * 10 ≈ 101 333
    assert 101_320 <= result.km <= 101_340


def test_recent_segments_weigh_more():
    """Человек стал ездить втрое больше — оценка должна тянуться к новому темпу."""
    readings = [
        reading(400, 100_000),
        reading(200, 102_000),  # 10 км/день, но это было давно
        reading(10, 120_000),  # 94 км/день совсем недавно
    ]
    result = mileage.estimate(readings, NOW)
    assert result is not None
    # Простое среднее по отрезкам дало бы ~52 км/день, взвешенное — заметно выше
    assert result.km_per_day > 70


def test_absurd_segments_are_ignored():
    readings = [
        reading(60, 100_000),
        reading(59, 400_000),  # 300 000 км за сутки — мимо
        reading(30, 101_000),
    ]
    result = mileage.estimate(readings, NOW)
    assert result is not None
    assert result.km_per_day < 100


def test_owner_pace_used_when_readings_too_few():
    """Один замер — темпа нет, берём заявленный владельцем месячный пробег."""
    result = mileage.estimate([reading(30, 50_000)], NOW, owner_monthly_km=1_500)
    assert result is not None
    assert result.from_owner
    assert result.km == 50_000 + 1_500  # ровно месяц по 50 км/день


def test_single_reading_without_owner_pace_is_not_extrapolated():
    result = mileage.estimate([reading(30, 50_000)], NOW)
    assert result is not None
    assert result.km == 50_000
    assert not result.is_estimate


def test_estimate_goes_stale():
    fresh = mileage.estimate([reading(40, 100_000), reading(10, 101_000)], NOW)
    old = mileage.estimate([reading(400, 100_000), reading(200, 101_000)], NOW)
    assert fresh is not None and not fresh.stale
    assert old is not None and old.stale


def test_distance_between_needs_two_readings_inside():
    readings = [reading(50, 100_000), reading(20, 101_000), reading(5, 101_400)]
    start, end = days_ago(30), NOW
    assert mileage.distance_between(readings, start, end) == 400
    # В окно попадает только один замер — отрезка нет
    assert mileage.distance_between(readings, days_ago(25), days_ago(10)) is None


# --- Цена километра ----------------------------------------------------------


def test_per_km_ignores_expenses_outside_window():
    """Трата до первого замера не попадает в числитель: под неё нет километров."""
    readings = [reading(60, 100_000), reading(0, 105_000)]
    expenses = [
        expense(90, 50_000),  # задолго до окна — не считается
        expense(30, 25_000),
        expense(10, 25_000),
    ]
    result = cost.per_km(readings, expenses)
    assert result is not None
    assert result.expense_count == 2
    assert result.total == Decimal(10)  # 50 000 / 5000 км


def test_per_km_flags_thin_data():
    """Тот самый случай: 20к на ТО и 10 км пробега."""
    readings = [reading(5, 100_000), reading(0, 100_010)]
    result = cost.per_km(readings, [expense(2, 20_000)])
    assert result is not None
    assert result.low_data  # 2 замера, 10 км, 5 дней — все пороги мимо


def test_per_km_is_confident_on_real_data():
    readings = [reading(180, 100_000), reading(90, 105_000), reading(0, 110_000)]
    expenses = [expense(day, 5_000, Category.FUEL) for day in (150, 120, 60, 30)]
    result = cost.per_km(readings, expenses)
    assert result is not None
    assert not result.low_data
    assert result.total == Decimal(2)  # 20 000 / 10 000 км
    assert result.fuel == Decimal(2)


def test_per_km_separates_one_off_repair():
    readings = [reading(180, 100_000), reading(90, 105_000), reading(0, 110_000)]
    expenses = [expense(150, 5_000, Category.FUEL), expense(60, 95_000)]
    result = cost.per_km(readings, expenses)
    assert result is not None
    assert result.total == Decimal(10)
    assert result.without_outlier == Decimal("0.5")


def test_per_km_without_readings():
    assert cost.per_km([], [expense(1, 1000)]) is None
    assert cost.per_km([reading(1, 100)], [expense(1, 1000)]) is None


# --- Сроки обслуживания ------------------------------------------------------


def plan(**kwargs) -> service.ServicePlan:
    base = dict(
        chat_id=-100, owner_id=1, car_slug="rav4", car_title="RAV4", kind="oil_engine"
    )
    return service.ServicePlan(**{**base, **kwargs})


def test_next_change_from_last_plus_interval():
    item = plan(interval_km=10_000, last_km=100_000)
    assert item.due_km == 110_000


def test_next_change_can_be_set_directly():
    """Прошлая замена неизвестна, но есть план — этого достаточно."""
    item = plan(interval_km=10_000, next_km=115_000)
    assert item.due_km == 115_000
    assert item.configured


def test_time_only_position():
    item = plan(kind="insurance", interval_days=365, last_at=days_ago(300))
    assert item.due_km is None
    assert item.due_at is not None
    due = service.evaluate(item, None, NOW)
    assert due is not None and due.days_left == 65


def test_due_picks_the_closer_axis():
    """По километрам ещё далеко, а по времени уже почти срок."""
    item = plan(interval_km=10_000, interval_days=365, last_km=100_000, last_at=days_ago(360))
    estimate = mileage.estimate([reading(60, 101_000), reading(0, 102_000)], NOW)
    due = service.evaluate(item, estimate, NOW)
    assert due is not None
    assert due.km_left is not None and due.km_left > 7_000
    assert due.days_left == 5
    assert "дн." in due.reason()


def test_reminder_threshold_scales_with_pace():
    """Тому, кто наматывает по 100 км в день, 500 км — это меньше недели."""
    fast = mileage.estimate([reading(30, 100_000), reading(0, 103_000)], NOW)
    item = plan(interval_km=10_000, last_km=94_000)  # до замены 104 000
    due = service.evaluate(item, fast, NOW)
    assert due is not None
    # Осталось около 1000 км — при 100 км/день это порог в 1400 км
    assert service.is_due(due, fast, NOW)


def test_cooldown_blocks_repeat_reminders():
    item = plan(interval_km=10_000, last_km=100_000, reminded_at=days_ago(2))
    estimate = mileage.estimate([reading(30, 109_800), reading(0, 109_900)], NOW)
    due = service.evaluate(item, estimate, NOW)
    assert due is not None
    assert not service.is_due(due, estimate, NOW)


def test_overdue_wording():
    item = plan(interval_km=10_000, last_km=100_000)
    estimate = mileage.estimate([reading(30, 110_000), reading(0, 111_000)], NOW)
    due = service.evaluate(item, estimate, NOW)
    assert due is not None and due.overdue
    assert "просрочено" in due.reason()


def test_parse_interval_km():
    assert service.parse_interval_km("10000") == 10_000
    assert service.parse_interval_km("10 000 км") == 10_000
    assert service.parse_interval_km("10к") == 10_000
    assert service.parse_interval_km("7.5к") == 7_500
    assert service.parse_interval_km("абв") is None
    assert service.parse_interval_km("5") is None  # слишком мало для интервала
