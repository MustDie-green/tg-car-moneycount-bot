"""Форматирование чисел и слов по-русски."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

THIN_SPACE = " "


def money(value: Decimal | int | float, currency: str = "₽", *, sign: bool = False) -> str:
    """1234567 → «1 234 567 ₽»."""
    amount = Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    prefix = "+" if sign and amount > 0 else ""
    body = f"{abs(int(amount)):,}".replace(",", " ")
    minus = "−" if amount < 0 else ""
    return f"{prefix}{minus}{body} {currency}".strip()


def money_short(value: Decimal | int | float, currency: str = "₽") -> str:
    """Компактно для узких мест: 12 340 → «12,3к»."""
    amount = Decimal(value)
    if abs(amount) >= 1_000_000:
        return f"{amount / 1_000_000:.1f}".replace(".", ",") + f"м {currency}"
    if abs(amount) >= 10_000:
        return f"{amount / 1000:.0f}к {currency}"
    if abs(amount) >= 1000:
        return f"{amount / 1000:.1f}".replace(".", ",") + f"к {currency}"
    return money(amount, currency)


def plural(count: int, one: str, few: str, many: str) -> str:
    """1 трата / 2 траты / 5 трат."""
    n = abs(count) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def with_plural(count: int, one: str, few: str, many: str) -> str:
    return f"{count} {plural(count, one, few, many)}"


def percent(value: Decimal | float) -> str:
    return f"{abs(float(value)):.0f}%"


def decimal_str(value: Decimal, digits: int = 1) -> str:
    quant = Decimal(10) ** -digits
    return str(value.quantize(quant)).replace(".", ",")
