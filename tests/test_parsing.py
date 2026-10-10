from decimal import Decimal

import pytest

from carbot.models import Category, Confidence
from carbot.parsing.amount import extract_liters, extract_odometer, find_amounts
from carbot.parsing.detector import parse_message


def amounts(text: str) -> list[Decimal]:
    return [m.value for m in find_amounts(text)]


# --- Суммы -------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("бенз 1000р", [1000]),
        ("мойка 500", [500]),
        ("запчасти 5000", [5000]),
        ("заправка 2 500 руб", [2500]),
        ("то 15.000", [15000]),
        ("масло 3к", [3000]),
        ("колодки 1.5к", [1500]),
        ("мойка 500₽", [500]),
        ("страховка 12 000 рублей", [12000]),
        ("шиномонтаж 2000 деревянных", [2000]),
        ("бенз 1000 мойка 500", [1000, 500]),
        ("отдал косарь за мойку", [1000]),
        ("развал 2 косаря", [2000]),
    ],
)
def test_amount_found(text, expected):
    assert amounts(text) == [Decimal(x) for x in expected]


@pytest.mark.parametrize(
    "text",
    [
        "залил 95",  # марка бензина
        "машина 2007 года",  # год выпуска
        "встречаемся в 18:30",  # время
        "сегодня 26.07 еду",  # дата
        "пробег 123456",  # одометр
        "проехал 300 км",  # расстояние
        "взял 40 л",  # литры
        "резина R16",  # размер диска
        "звони +7 999 123 45 67",  # телефон
        "скинь 5",  # ниже минимума
    ],
)
def test_amount_rejected(text):
    assert amounts(text) == []


# --- Категории ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text,category",
    [
        ("бенз 1000р", Category.FUEL),
        ("заправился на 2000", Category.FUEL),
        ("залил 92го на 3000р", Category.FUEL),
        ("мойка 500", Category.CARE),
        ("помыл тачку 700", Category.CARE),
        ("страховка 12000", Category.STATE),
        ("штраф 500", Category.FINES),
        ("парковка 300", Category.STATE),
        ("платные дороги 600", Category.STATE),
        ("абонемент 4775", Category.STATE),
        ("омывайка 399", Category.CARE),
        ("запчасти 5000", Category.SERVICE),
        ("замена масла 4500", Category.SERVICE),
        ("колодки поменял 6000", Category.SERVICE),
        ("шиномонтаж 2500", Category.SERVICE),
        ("развал схождение 3000", Category.SERVICE),
    ],
)
def test_category(text, category):
    result = parse_message(text)
    assert result, f"ничего не нашлось в {text!r}"
    assert result.detections[0].category == category


def test_slang_prefix_beats_typo():
    # «шином…» ловится префиксом, до нечёткого поиска дело не доходит
    result = parse_message("шиномантаж 2500")
    assert result.detections[0].category == Category.SERVICE
    assert result.detections[0].confidence == Confidence.HIGH


def test_typo_is_caught_fuzzily():
    result = parse_message("акумулятор 8000р")
    assert result
    assert result.detections[0].category == Category.SERVICE
    assert result.detections[0].confidence == Confidence.MEDIUM


def test_multiple_expenses_in_one_message():
    result = parse_message("бенз 1000 и мойка 500")
    assert len(result.detections) == 2
    assert result.detections[0].category == Category.FUEL
    assert result.detections[1].category == Category.CARE
    assert result.total == Decimal(1500)


def test_bare_amount_no_longer_triggers():
    """Одной суммы мало: в чате постоянно летают числа не про машину."""
    assert not parse_message("1500р")
    assert parse_message("1500р").reason == "no_keyword"


def test_short_message_with_unknown_word_stays_silent():
    """Раньше «гидрач 3000» считалось тратой по краткости — слишком грубо."""
    assert not parse_message("гидрач 3000")


def test_explicit_form_works_without_keyword():
    """«на тачку …» — запасной вход, когда слово боту незнакомо."""
    result = parse_message("на тачку 3000 гидрач")
    assert result.explicit
    assert len(result.detections) == 1
    assert result.detections[0].amount == Decimal(3000)
    assert result.reason == "explicit"


@pytest.mark.parametrize(
    "text",
    [
        "в машину 1500 незамерзайка",
        "на тачку: 2400 промывка форсунок",
        "за машину 800 парковка",
    ],
)
def test_explicit_variants(text):
    assert parse_message(text).explicit


@pytest.mark.parametrize(
    "text",
    [
        "500",  # голое число без маркера — молчим
        "их было 30 человек",
        "продал колеса за 5000",  # это доход
        "скинь 500 на подарок",  # это долг
        "сколько стоит такая 20000",  # прицениваются
        "/top",
        "",
    ],
)
def test_ignored(text):
    assert not parse_message(text)


def test_learned_word_from_chat_lexicon():
    # Пока слово незнакомо — молчим
    assert not parse_message("гидрач 3000р")
    # Выучив, узнаём его сами
    learned = {"гидрач": Category.SERVICE}
    result = parse_message("гидрач 3000р", learned=learned)
    assert result.detections[0].category == Category.SERVICE


def test_unknown_lemmas_collected_only_from_explicit_form():
    """Учимся там, где человек сам подтвердил, что речь про машину."""
    assert parse_message("гидрач 3000р").unknown_lemmas == []
    result = parse_message("на тачку 3000 гидрач")
    assert "гидрач" in result.unknown_lemmas


def test_no_learning_when_static_word_matched():
    result = parse_message("бенз 1000 на трассе")
    assert result.unknown_lemmas == []


# --- Топливо -----------------------------------------------------------------


def test_liters_and_odometer():
    assert extract_liters("залил 42л") == Decimal("42")
    assert extract_liters("взял 38.5 литров") == Decimal("38.5")
    assert extract_odometer("пробег 123456") == 123456
    assert extract_odometer("на одометре 98 000") is None  # с пробелом не берём


def test_fuel_data_attached_to_expense():
    result = parse_message("заправка 40л 2800р пробег 123456")
    detection = result.detections[0]
    assert detection.category == Category.FUEL
    assert detection.amount == Decimal(2800)
    assert detection.liters == Decimal(40)
    assert detection.odometer == 123456


def test_note_is_cleaned():
    result = parse_message("мойка 500")
    assert result.detections[0].note == "мойка"
