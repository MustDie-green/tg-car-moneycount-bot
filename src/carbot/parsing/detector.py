"""Сборка всего вместе: текст сообщения → список распознанных трат."""

from __future__ import annotations

import re
from decimal import Decimal

from ..models import Category, Confidence, Detection, ParseResult
from .amount import AmountMatch, extract_liters, extract_odometer, find_amounts
from .morph import analyze, index

_TRIM = " \t\n\r,.;:!?-—–+«»\"'()[]и"
_MAX_NOTE = 64


def _segments(text: str, amounts: list[AmountMatch]) -> list[str]:
    """Кусок текста, относящийся к каждой сумме, без самой суммы.

    «бенз 1000 мойка 500» → ['бенз', 'мойка']. Для одной суммы — весь текст,
    из которого вырезано число: оно и так лежит в отдельном поле.
    """
    if len(amounts) <= 1:
        amount = amounts[0]
        return [f"{text[: amount.start]} {text[amount.end :]}"]
    out: list[str] = []
    prev_end = 0
    for amount in amounts:
        chunk = text[prev_end : amount.start]
        out.append(chunk if chunk.strip(_TRIM) else text[amount.end : amount.end + 40])
        prev_end = amount.end
    return out


def _make_note(segment: str) -> str:
    note = re.sub(r"\s+", " ", segment).strip(_TRIM)
    if len(note) > _MAX_NOTE:
        note = note[: _MAX_NOTE - 1].rstrip() + "…"
    return note


def parse_message(
    text: str,
    *,
    learned: dict[str, Category] | None = None,
    min_amount: int = 10,
    max_amount: int = 5_000_000,
) -> ParseResult:
    """Главная точка входа детектора.

    Бот спрашивает «На тачку?» только если сработало ключевое слово либо
    у суммы был явный денежный маркер (₽/руб/к). Голое число в чате
    игнорируется — иначе бот задолбает всех вопросами.
    """
    empty = ParseResult()
    if not text or text.lstrip().startswith("/"):
        return empty

    whole = analyze(text)
    if index.has_veto(whole):
        return empty

    amounts = find_amounts(text, min_amount=min_amount, max_amount=max_amount)
    if not amounts:
        return empty

    # «гидрач 3000» — короткая реплика из пары слов с одним числом выглядит
    # как трата, даже если валюту не написали и слово боту незнакомо.
    shorthand = (
        1 <= len(whole.tokens) <= 2
        and len(amounts) == 1
        and amounts[0].value >= 100
    )

    segments = _segments(text, amounts)
    detections: list[Detection] = []
    any_static_hit = False

    for amount, segment in zip(amounts, segments):
        hit = index.match(analyze(segment), learned)
        if hit is None and len(amounts) == 1:
            hit = index.match(whole, learned)
        if hit is None and not amount.strong and not shorthand:
            continue
        if hit is not None:
            any_static_hit = True
        detections.append(
            Detection(
                amount=amount.value,
                category=hit.category if hit else None,
                confidence=hit.confidence if hit else Confidence.LOW,
                trigger=hit.trigger if hit else None,
                note=_make_note(segment),
            )
        )

    if not detections:
        return empty

    _inherit_category(detections)
    _attach_fuel_data(text, detections)

    unknown = [] if any_static_hit else index.learnable_lemmas(whole)
    return ParseResult(detections=detections, unknown_lemmas=unknown)


def _inherit_category(detections: list[Detection]) -> None:
    """«заправка 2000 и ещё 500р» — вторая трата наследует категорию первой."""
    known = {d.category for d in detections if d.category is not None}
    if len(known) != 1:
        return
    category = known.pop()
    for d in detections:
        if d.category is None:
            d.category = category
            d.confidence = Confidence.MEDIUM


def _attach_fuel_data(text: str, detections: list[Detection]) -> None:
    """Литры и пробег вешаем на бензиновую трату — из них считается расход."""
    liters = extract_liters(text)
    odometer = extract_odometer(text)
    if liters is None and odometer is None:
        return
    target = next((d for d in detections if d.category == Category.FUEL), None)
    if target is None and len(detections) == 1:
        target = detections[0]
    if target is None:
        return
    target.liters = liters
    target.odometer = odometer


def price_per_liter(amount: Decimal, liters: Decimal | None) -> Decimal | None:
    if not liters:
        return None
    return (amount / liters).quantize(Decimal("0.01"))
