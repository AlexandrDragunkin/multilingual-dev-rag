# -*- coding: utf-8 -*-
"""Тесты нормализации текста для FTS по не-ASCII письменностям.

Не требуют ни zvec, ни модели эмбеддингов — чистые функции.
"""
import pytest

from dev_rag.fts_normalizer import (
    FTS_NORM_VERSION,
    has_ascii_word,
    has_non_ascii,
    make_fts_unicode,
)


class TestHasNonAscii:
    @pytest.mark.parametrize('text', [
        'дверь',           # кириллица
        'Дверь',
        'коробка KSM12',   # смешанный
        'Größe',           # латиница с диакритикой
        'Ελλάδα',          # греческий
        'مرحبا',           # арабский
        '日本語',            # CJK (детектится, но см. границу применимости)
    ])
    def test_finds_non_ascii(self, text):
        assert has_non_ascii(text)

    @pytest.mark.parametrize('text', ['RabbitMQ', 'obj_k3_gab3', '123', '', 'a-b_c.d'])
    def test_no_false_positives(self, text):
        assert not has_non_ascii(text)


class TestHasAsciiWord:
    @pytest.mark.parametrize('text', ['assembly', 'KSM12', 'коробка KSM12', '9000'])
    def test_finds_ascii_word(self, text):
        assert has_ascii_word(text)

    @pytest.mark.parametrize('text', ['дверь', 'ферма воркеров', '', '—'])
    def test_no_false_positives(self, text):
        assert not has_ascii_word(text)


class TestMakeFtsUnicode:
    def test_deterministic(self):
        assert make_fts_unicode('дверь коробка') == make_fts_unicode('дверь коробка')

    def test_lowercases_non_ascii(self):
        # Именно ради этого нужен Python-lower: фильтр lowercase в zvec ASCII-only
        assert make_fts_unicode('ДВЕРЬ') == make_fts_unicode('дверь') == 'дверь'
        assert make_fts_unicode('Микрорепозитории') == 'микрорепозитории'

    def test_yo_folded_to_ye(self):
        # «ёлка» и «елка» должны искаться одинаково
        assert make_fts_unicode('Ёлка') == make_fts_unicode('елка') == 'елка'

    def test_ascii_dropped(self):
        # Латиница живёт в поле text; дублирование дало бы двойной вес в RRF
        assert make_fts_unicode('RabbitMQ assembly script') == ''
        assert make_fts_unicode('def build_door(leaf_spec): pass') == ''

    def test_mixed_keeps_only_non_ascii(self):
        assert make_fts_unicode('Коробка двери KSM12.') == 'коробка двери'

    def test_punctuation_becomes_separator(self):
        # whitespace-токенайзер сам пунктуацию не режет — это делаем мы
        assert make_fts_unicode('дверь-коробка') == 'дверь коробка'
        assert make_fts_unicode('сборка, профиль; вставка.') == 'сборка профиль вставка'

    def test_no_translit_collisions(self):
        # Схема с транслитом склеивала бы ел/ель -> el. Здесь токены различимы.
        assert make_fts_unicode('ел') != make_fts_unicode('ель')
        assert make_fts_unicode('подъезд') != make_fts_unicode('подезд')

    def test_whitespace_collapsed(self):
        assert make_fts_unicode('дверь   \n\t коробка') == 'дверь коробка'

    def test_empty_input(self):
        assert make_fts_unicode('') == ''

    def test_token_with_mixed_scripts_kept_whole(self):
        # Токен содержит не-ASCII — значит в text по нему не найтись, берём целиком
        assert make_fts_unicode('проверка_obj') == 'проверка_obj'


class TestOtherScripts:
    """Обход не кириллице-специфичен: standard слеп ко всему вне ASCII."""

    def test_german_umlaut(self):
        # ß → ss из-за casefold; 'der' — чистый ASCII, в text_fts не идёт
        assert make_fts_unicode('Größe der Tür') == 'grösse tür'

    def test_german_sharp_s_case_folding(self):
        # Дефект, найденный сквозным зондом: lower() оставляет 'ß' как есть,
        # а 'SS' делает 'ss' — TÜRGRÖSSE не находил Türgröße. casefold() лечит.
        assert make_fts_unicode('Türgröße') == make_fts_unicode('TÜRGRÖSSE')

    def test_greek(self):
        assert make_fts_unicode('Ελλάδα') == 'ελλάδα'

    def test_greek_final_sigma(self):
        # ς и Σ должны складываться к одному токену
        assert make_fts_unicode('πόρτας') == make_fts_unicode('ΠΌΡΤΑΣ')

    def test_french_accents(self):
        assert make_fts_unicode('Café') == make_fts_unicode('CAFÉ') == 'café'

    def test_arabic(self):
        assert make_fts_unicode('الباب') == 'الباب'

    def test_mixed_scripts_in_one_text(self):
        assert make_fts_unicode('дверь Größe Ελλάδα code') == 'дверь grösse ελλάδα'


class TestVersion:
    def test_version_constant_present(self):
        assert FTS_NORM_VERSION
