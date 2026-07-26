"""Извлечение денежных сумм из текста сообщения.

Задача не «найти число», а «найти число, которое действительно является ценой».
Поэтому кроме основной регулярки здесь живёт набор анти-паттернов: марки бензина,
пробег, литры, годы выпуска, время, даты, телефоны, размеры резины.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# --- Базовые куски регулярки -------------------------------------------------

# 1 500 / 1.500 / 12 000 / 1500 / 1500,50
_NUM = r"(?:\d{1,3}(?:[  .,]\d{3})+|\d+(?:[.,]\d{1,2})?)"
# 2к / 1.5k / 3 тыс / пара косарей
_MULT = r"(?:к|k|тыс\.?|тысяч[а-я]*|косар[а-я]*|косых)"
# 1000р / 1000 руб / 1000₽ / 1000 деревянных
_CUR = r"(?:₽|rub|руб[а-я]*\.?|р\.|р|деревянн[а-я]*|дерев)"

AMOUNT_RE = re.compile(
    rf"(?<![\d.,])(?P<num>{_NUM})\s*(?P<mult>{_MULT})?\s*(?P<cur>{_CUR})?(?![\w])",
    re.IGNORECASE,
)

# Единицы измерения сразу после числа — значит это не деньги
_UNIT_AFTER = re.compile(
    r"^\s*(?:"
    r"л(?![а-яё])|литр[а-я]*|"
    r"км|килом[а-я]*|тыс\.?\s*км|"
    r"%|шт[а-я.]*|раз[а-я]*|"
    r"мин[а-я.]*|час[а-я]*|ч(?![а-яё])|сек[а-я.]*|"
    r"мм|см|кг|гр(?![а-яё])|грамм[а-я]*|"
    r"год[а-я]*|лет(?![а-яё])|г\.в|г\.г|"
    r"мес[а-я.]*|нед[а-я.]*|дн[ея][йм]?|"
    r"лс(?![а-яё])|л\.с|об/мин|бар|атм"
    r")",
    re.IGNORECASE,
)

# Префиксы перед числом, после которых это точно не цена
_UNIT_BEFORE = re.compile(r"(?:аи[\s\-]*|№|#|[rр](?=\d)|\+|x|х(?=\d))$", re.IGNORECASE)

# Куски текста, внутри которых числа игнорируются целиком
_MASKS = (
    re.compile(r"\b\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?\b"),  # даты
    re.compile(r"\b\d{1,2}:\d{2}\b"),  # время
    re.compile(r"(?:\+7|\b8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}"),  # телефон
    re.compile(r"https?://\S+|\S+\.(?:ru|com|org|net|рф)/\S*"),  # ссылки
    re.compile(r"\br\s?\d{2}\b", re.IGNORECASE),  # R16 — размер диска
)

# Марки топлива — голое число без валюты
_FUEL_GRADES = {80, 92, 95, 98, 100, 102}

# Числа рядом с этими словами — пробег, а не деньги
_ODOMETER_NEAR = re.compile(r"пробег|одометр|намотал|накатал", re.IGNORECASE)

# Денежный сленг без цифр
SLANG_AMOUNTS: dict[str, int] = {
    "полтинник": 50,
    "стольник": 100,
    "двушка": 200,
    "пятихатка": 500,
    "пятихат": 500,
    "косарь": 1000,
    "косаря": 1000,
    "косарей": 1000,
    "штука": 1000,
    "штуку": 1000,
    "штуки": 1000,
}
_SLANG_RE = re.compile(
    r"\b(?P<count>\d{1,2}\s+)?(?P<word>" + "|".join(SLANG_AMOUNTS) + r")\b",
    re.IGNORECASE,
)


@dataclass(slots=True, frozen=True)
class AmountMatch:
    value: Decimal
    start: int
    end: int
    # Была явная валюта или множитель — значит это почти наверняка деньги
    strong: bool


def _to_decimal(raw: str) -> Decimal | None:
    s = raw.replace(" ", " ")
    try:
        if re.fullmatch(r"\d{1,3}(?:[ .,]\d{3})+", s):
            return Decimal(re.sub(r"[ .,]", "", s))
        return Decimal(s.replace(",", "."))
    except InvalidOperation:
        return None


def _multiplier(token: str | None) -> int:
    return 1000 if token else 1


def _masked_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for pattern in _MASKS:
        spans.extend(m.span() for m in pattern.finditer(text))
    return spans


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(span[0] < e and s < span[1] for s, e in spans)


def find_amounts(
    text: str, *, min_amount: int = 10, max_amount: int = 5_000_000
) -> list[AmountMatch]:
    """Возвращает суммы, найденные в тексте, в порядке появления."""
    masked = _masked_spans(text)
    found: list[AmountMatch] = []

    for m in AMOUNT_RE.finditer(text):
        span = m.span()
        if _overlaps(span, masked):
            continue

        head = text[: m.start("num")]
        if _UNIT_BEFORE.search(head):
            continue

        tail = text[m.end() :]
        # Единица измерения после числа — но только если валюту мы не увидели
        if not m.group("cur") and _UNIT_AFTER.match(tail):
            continue
        if tail[:1] == ":":
            continue

        value = _to_decimal(m.group("num"))
        if value is None:
            continue
        value *= _multiplier(m.group("mult"))

        strong = bool(m.group("cur") or m.group("mult"))

        if not strong:
            # Голое «95» — это марка бензина, а не 95 рублей
            if value == value.to_integral_value() and int(value) in _FUEL_GRADES:
                continue
            # Голое пятизначное рядом со словом «пробег» — это одометр
            window = text[max(0, m.start() - 25) : m.end() + 25]
            if len(m.group("num").replace(" ", "")) >= 5 and _ODOMETER_NEAR.search(window):
                continue

        if not (min_amount <= value <= max_amount):
            continue

        found.append(AmountMatch(value=value, start=span[0], end=span[1], strong=strong))

    if found:
        return found
    return _find_slang(text, min_amount=min_amount, max_amount=max_amount)


def _find_slang(text: str, *, min_amount: int, max_amount: int) -> list[AmountMatch]:
    out: list[AmountMatch] = []
    for m in _SLANG_RE.finditer(text):
        base = SLANG_AMOUNTS[m.group("word").lower()]
        count = int(m.group("count").strip()) if m.group("count") else 1
        value = Decimal(base * count)
        if min_amount <= value <= max_amount:
            out.append(AmountMatch(value=value, start=m.start(), end=m.end(), strong=True))
    return out


def extract_liters(text: str) -> Decimal | None:
    """«залил 42л» / «40 литров» → 42."""
    m = re.search(r"(\d{1,3}(?:[.,]\d{1,2})?)\s*(?:л(?![а-яё])|литр[а-я]*)", text, re.I)
    if not m:
        return None
    try:
        value = Decimal(m.group(1).replace(",", "."))
    except InvalidOperation:
        return None
    return value if Decimal(1) <= value <= Decimal(200) else None


def extract_odometer(text: str) -> int | None:
    """«пробег 123456» / «123456 км» → 123456."""
    patterns = (
        r"(?:пробег|одометр)\D{0,12}(\d{4,7})",
        r"(\d{4,7})\s*км",
    )
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            value = int(m.group(1))
            if 100 <= value <= 3_000_000:
                return value
    return None
