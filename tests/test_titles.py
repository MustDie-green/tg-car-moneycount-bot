"""Звания месяца: кто что получает и почему."""

from decimal import Decimal

from carbot.models import Category, PersonMonth
from carbot.titles import assign_titles

D = Decimal


def person(tg_id, *, total=0, count=1, **kwargs) -> PersonMonth:
    by_category = kwargs.pop("by_category", {})
    total = total or sum(by_category.values(), D(0))
    return PersonMonth(tg_id, f"P{tg_id}", D(total), by_category, count, **kwargs)


def test_strongest_title_wins_for_each_person():
    """Лидер с огромным отрывом получает именно это звание, а не случайное."""
    fines = person(1, by_category={Category.FINES: D(9000), Category.FUEL: D(5000)})
    fuel = person(2, by_category={Category.FUEL: D(4900), Category.FINES: D(500)})
    assign_titles([fines, fuel])
    assert fines.title == "Любимец камер"  # штрафов в 18 раз больше
    assert fuel.title != "Бензоколонка" or fuel.titles


def test_up_to_two_titles_per_person():
    star = person(
        1,
        by_category={Category.FINES: D(9000), Category.STATE: D(8000)},
        month_km=3000,
    )
    others = [person(2, by_category={Category.FUEL: D(3000)}, month_km=500)]
    assign_titles([star, *others])
    # Накатал вшестеро больше — это ярче, чем быть единственным со штрафами
    assert star.titles[0] == "Дальнобой"
    assert len(star.titles) == 2
    assert star.titles[1] in {"Любимец камер", "Меценат дорожного фонда"}


def test_each_title_goes_to_one_person_only():
    people = [
        person(i, by_category={Category.FUEL: D(1000 * i), Category.CARE: D(600 * i)})
        for i in range(1, 5)
    ]
    assign_titles(people)
    given = [title for p in people for title in p.titles]
    assert len(given) == len(set(given))
    assert all(len(p.titles) <= 2 for p in people)


def test_weak_second_title_is_not_given():
    """Второе звание — только за заметный отрыв, иначе плашками обрастут все."""
    leader = person(1, by_category={Category.FUEL: D(9000), Category.SERVICE: D(9000)})
    runner = person(2, by_category={Category.SERVICE: D(8999)})
    assign_titles([leader, runner])
    # «Бензоколонка» — единственный заправлявшийся. «Кузнец» — отрыв в один рубль
    assert leader.titles == ["Бензоколонка"]


def test_new_titles():
    night = person(1, by_category={Category.FUEL: D(1000)}, night_count=3)
    camel = person(2, by_category={Category.FUEL: D(900)}, liters_per_100=D("6.5"))
    tanker = person(3, by_category={Category.FUEL: D(800)}, liters_per_100=D("13.0"))
    orchestra = person(
        4, by_category={c: D(100) for c in Category},
    )
    assign_titles([night, camel, tanker, orchestra])
    assert "Ночной гонщик" in night.titles
    assert "Верблюд" in camel.titles
    assert "Танкер" in tanker.titles
    assert "Человек-оркестр" in orchestra.titles


def test_black_swan_beats_everything():
    swan = person(1, by_category={Category.SERVICE: D(60000), Category.FINES: D(9000)})
    assign_titles([swan, person(2, by_category={Category.FUEL: D(100)})], black_swan_id=1)
    assert swan.titles[0] == "Чёрный лебедь"


def test_tuning_titles():
    """Ксзибит — больше всех на тюнинг; Форсаж — тюнинг дороже всего остального."""
    builder = person(1, by_category={Category.TUNING: D(183000), Category.FUEL: D(20000)})
    other = person(2, by_category={Category.TUNING: D(5000), Category.FUEL: D(30000)})
    assign_titles([builder, other])
    assert "Ксзибит" in builder.titles
    assert "Форсаж" in builder.titles
    assert "Форсаж" not in other.titles


def test_tuning_black_swan_becomes_xzibit():
    """Самая выбивающаяся трата — апгрейд: это прокачка, а не беда."""
    builder = person(1, by_category={Category.TUNING: D(183000), Category.SERVICE: D(61600)})
    other = person(2, by_category={Category.FUEL: D(3000)})
    assign_titles([builder, other], black_swan_id=1, swan_is_tuning=True)
    assert builder.titles[0] == "Ксзибит"
    assert "Чёрный лебедь" not in builder.titles
