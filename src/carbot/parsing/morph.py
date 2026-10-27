"""Морфология и нечёткое сопоставление слов.

Вместо матчинга по подстрокам приводим каждое слово к нормальной форме
(pymorphy3), а опечатки добираем rapidfuzz'ом. Это даёт устойчивость к
падежам, временам глаголов и кривому набору без всяких LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from rapidfuzz import process as fuzz_process
from rapidfuzz.distance import JaroWinkler

from ..models import Category, Confidence
from . import lexicon

_TOKEN_RE = re.compile(r"[а-яa-z]+", re.IGNORECASE)
_LEARNABLE_POS = {"NOUN", "INFN", "VERB"}


def normalize(text: str) -> str:
    return text.lower().replace("ё", "е").replace(" ", " ")


class Morph:
    """Ленивая обёртка над pymorphy3 — анализатор поднимается ~секунду."""

    def __init__(self) -> None:
        self._analyzer = None

    @property
    def analyzer(self):
        if self._analyzer is None:
            import pymorphy3

            self._analyzer = pymorphy3.MorphAnalyzer()
        return self._analyzer

    @lru_cache(maxsize=8192)
    def _parse_first(self, word: str):
        return self.analyzer.parse(word)[0]

    def lemma(self, word: str) -> str:
        return normalize(self._parse_first(word).normal_form)

    @lru_cache(maxsize=8192)
    def is_known(self, word: str) -> bool:
        """Есть ли слово в словаре русского языка — то есть не опечатка."""
        return bool(self.analyzer.word_is_known(word))

    def pos(self, word: str) -> str | None:
        tag = self._parse_first(word).tag.POS
        return str(tag) if tag else None


morph = Morph()


@dataclass(slots=True)
class Analysis:
    text: str
    tokens: list[str]
    lemmas: list[str]

    @property
    def lemma_set(self) -> set[str]:
        return set(self.lemmas)


def analyze(text: str) -> Analysis:
    normalized = normalize(text)
    tokens = [t for t in _TOKEN_RE.findall(normalized) if len(t) > 1]
    lemmas = [morph.lemma(t) for t in tokens]
    return Analysis(text=normalized, tokens=tokens, lemmas=lemmas)


@dataclass(slots=True, frozen=True)
class KeywordHit:
    category: Category
    trigger: str
    confidence: Confidence


class KeywordIndex:
    """Статические словари, разложенные по леммам, плюс нечёткий поиск."""

    def __init__(self, fuzzy_threshold: int = 90) -> None:
        self.fuzzy_threshold = fuzzy_threshold
        self._lemmas: dict[str, Category] = {}
        self._phrases: dict[tuple[str, str], Category] = {}
        self._prefixes: list[tuple[str, Category]] = []
        self._veto: set[str] = set()
        self._built = False

    def _build(self) -> None:
        if self._built:
            return
        for category, words in lexicon.WORDS.items():
            for word in words:
                self._lemmas[morph.lemma(normalize(word))] = category
        for category, phrases in lexicon.PHRASES.items():
            for first, second in phrases:
                key = (morph.lemma(normalize(first)), morph.lemma(normalize(second)))
                self._phrases[key] = category
        for category, prefixes in lexicon.PREFIXES.items():
            for prefix in prefixes:
                self._prefixes.append((normalize(prefix), category))
        # Длинные префиксы проверяем первыми — они точнее
        self._prefixes.sort(key=lambda item: -len(item[0]))
        self._veto = {morph.lemma(normalize(w)) for w in lexicon.VETO_WORDS}
        self._fuzzy_choices = [w for w in self._lemmas if len(w) >= 5]
        self._built = True

    # --- Публичное API -------------------------------------------------------

    def has_veto(self, analysis: Analysis) -> str | None:
        self._build()
        for lemma in analysis.lemmas:
            if lemma in self._veto:
                return lemma
        return None

    def tuning_trigger(self, analysis: Analysis) -> str | None:
        """Слово тюнинга в тексте, если оно есть, — для приоритета над ремонтом."""
        self._build()
        for lemma in analysis.lemmas:
            if self._lemmas.get(lemma) == Category.TUNING:
                return lemma
        for token in analysis.tokens:
            for prefix, category in self._prefixes:
                if category == Category.TUNING and token.startswith(prefix):
                    return token
        return None

    def match(
        self, analysis: Analysis, learned: dict[str, Category] | None = None
    ) -> KeywordHit | None:
        """Категория траты, с одним исключением из правила «первое слово».

        В тексте «установка вебасто и замена помпы» есть и ремонт, и тюнинг.
        Тюнинг тогда главнее: это и есть дорогая часть, ради которой платили,
        а ремонт попутный. Иначе апгрейды тонули в «Ремонте и ТО».
        """
        hit = self._first_match(analysis, learned)
        if hit is not None and hit.category == Category.SERVICE:
            trigger = self.tuning_trigger(analysis)
            if trigger:
                return KeywordHit(Category.TUNING, trigger, Confidence.HIGH)
        return hit

    def _first_match(
        self, analysis: Analysis, learned: dict[str, Category] | None = None
    ) -> KeywordHit | None:
        """Первое сработавшее правило и есть категория."""
        self._build()
        learned = learned or {}

        # 1. Фразы из двух слов — самые специфичные
        for first, second in zip(analysis.lemmas, analysis.lemmas[1:]):
            category = self._phrases.get((first, second))
            if category:
                return KeywordHit(category, f"{first} {second}", Confidence.HIGH)

        # 2. Точное совпадение леммы: сначала выученное чатом, потом базовое
        for lemma in analysis.lemmas:
            category = learned.get(lemma) or self._lemmas.get(lemma)
            if category:
                return KeywordHit(category, lemma, Confidence.HIGH)

        # 3. Сленг по началу слова
        for token in analysis.tokens:
            for prefix, category in self._prefixes:
                if token.startswith(prefix):
                    return KeywordHit(category, token, Confidence.HIGH)

        # 4. Опечатки. Только для слов, которых нет в словаре русского языка:
        # «акумулятор» — опечатка, а «делать», «капитан», «номер» — обычные
        # слова, и похожесть на «деталь» или «капиталку» ничего не значит
        for token, lemma in zip(analysis.tokens, analysis.lemmas):
            if len(lemma) < 5 or morph.is_known(token):
                continue
            best = fuzz_process.extractOne(
                lemma,
                self._fuzzy_choices,
                scorer=JaroWinkler.similarity,
                score_cutoff=self.fuzzy_threshold / 100,
            )
            if best:
                return KeywordHit(self._lemmas[best[0]], lemma, Confidence.MEDIUM)

        return None

    def learnable_lemmas(self, analysis: Analysis) -> list[str]:
        """Слова-кандидаты для самообучаемого словаря чата."""
        self._build()
        out: list[str] = []
        seen: set[str] = set()
        for token, lemma in zip(analysis.tokens, analysis.lemmas):
            if lemma in seen or len(lemma) < 4:
                continue
            if lemma in self._lemmas or lemma in lexicon.LEARN_STOPWORDS:
                continue
            if morph.pos(token) not in _LEARNABLE_POS:
                continue
            seen.add(lemma)
            out.append(lemma)
        return out


index = KeywordIndex()
