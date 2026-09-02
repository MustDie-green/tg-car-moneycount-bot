"""Сборка всего вместе: текст сообщения → список распознанных трат.

Главное правило: одной суммы мало. В чате друзей числа летают постоянно —
кто-то скинулся на подарок, кто-то назвал зарплату, кто-то посчитал шаги.
Поэтому трата признаётся тратой, только если рядом есть слово из автомобильного
словаря. Если слова нет, но записать всё равно надо, есть явная форма:
«на тачку 3000 гидрач» — она же учит бота новым словам.
"""

from __future__ import annotations

import re
from decimal import Decimal

from ..models import Category, Confidence, Detection, ParseResult
from .amount import AmountMatch, extract_liters, extract_odometer, find_amounts
from .morph import analyze, index

_TRIM = " \t\n\r,.;:!?-—–+«»\"'()[]и"
_MAX_NOTE = 64

# Явная форма записи: «на тачку 3000 гидрач», «в тачку 500 мойка».
# Работает без ключевого слова и служит способом научить бота новому слову.
EXPLICIT_RE = re.compile(
    r"^\s*(?:на|в|за)?\s*"
    r"(?:тачку|тачка|тачке|машину|машина|машине|авто|автомобиль|бричку|ласточку)"
    r"\s*[:\-—]?\s*",
    re.IGNORECASE,
)


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

    Возвращает не только находки, но и причину решения — она уходит в журнал
    наблюдений, чтобы через пару недель было видно, где детектор промахивается.
    """
    if not text or text.lstrip().startswith("/"):
        return ParseResult(reason="command")

    explicit_match = EXPLICIT_RE.match(text)
    explicit = explicit_match is not None
    # Из явной формы вырезаем сам префикс: «на тачку 3000 мойка» → «3000 мойка»
    body = text[explicit_match.end() :] if explicit_match else text

    whole = analyze(body)

    amounts = find_amounts(body, min_amount=min_amount, max_amount=max_amount)
    if not amounts:
        return ParseResult(reason="no_amount", explicit=explicit)

    # Сумму ищем до проверки стоп-слов нарочно: тогда и у отброшенных сообщений
    # в журнале наблюдений видно, какое число там было
    seen = [str(a.value) for a in amounts]

    # Стоп-слова («продал», «должен», «подорожал») перебивают всё, кроме явной формы
    if not explicit:
        veto = index.has_veto(whole)
        if veto:
            return ParseResult(reason=f"veto:{veto}", seen_amounts=seen)
    segments = _segments(body, amounts)
    detections: list[Detection] = []
    any_static_hit = False

    for amount, segment in zip(amounts, segments):
        hit = index.match(analyze(segment), learned)
        if hit is None and len(amounts) == 1:
            hit = index.match(whole, learned)

        # Ключевое слово обязательно. Без него молчим — кроме явной формы,
        # где человек уже сказал, что это про машину.
        if hit is None and not explicit:
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
        return ParseResult(reason="no_keyword", seen_amounts=seen)

    _inherit_category(detections)
    _attach_fuel_data(body, detections)

    triggers = [d.trigger for d in detections if d.trigger]
    reason = "explicit" if explicit else f"keyword:{triggers[0] if triggers else '?'}"

    # Учим словарь только с явной формы: там человек сам подтвердил, что это
    # про машину, а значит незнакомые слова рядом — надёжные кандидаты.
    unknown = index.learnable_lemmas(whole) if explicit and not any_static_hit else []

    return ParseResult(
        detections=detections,
        unknown_lemmas=unknown,
        explicit=explicit,
        reason=reason,
        seen_amounts=seen,
    )


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
