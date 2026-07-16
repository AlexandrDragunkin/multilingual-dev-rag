# -*- coding: utf-8 -*-
"""Интеграционные тесты: реальный движок zvec, реальная FTS-токенизация.

Юнит-тесты normalizer'а (test_fts_normalizer.py) проверяют чистую функцию
make_fts_unicode — но баг FTS живёт не в ней, а в токенайзере движка:
standard выбрасывает не-ASCII токены целиком, а фильтр lowercase в zvec
складывает регистр только для ASCII. Эти тесты ходят через настоящий zvec:
создают временную коллекцию, вставляют документы, ищут. Если завтра новая
версия zvec починит standard под кириллицу — test_standard_does_not_find_cyrillic
упадёт, и это сигнал, что обход (поле text_fts) можно убирать.

Запуск: python -m pytest tests/test_zvec_integration.py -v -m integration
"""
import os
import shutil
import tempfile

import pytest
import zvec

# zvec.init() падает при повторном вызове — guard.
if not getattr(zvec, "_mdr_inited", False):
    zvec.init()
    zvec._mdr_inited = True

from zvec import (  # noqa: E402
    CollectionOption,
    DataType,
    Doc,
    FieldSchema,
    FtsIndexParam,
    HnswIndexParam,
    VectorSchema,
)
from zvec.model.param.query import Fts, Query  # noqa: E402

from dev_rag.config import ZVEC_EMBED_DIM  # noqa: E402
from dev_rag.fts_normalizer import make_fts_unicode  # noqa: E402

# Фиктивный вектор: тестируем FTS, а не эмбеддинги.
_DUMMY_VEC = [0.1] * ZVEC_EMBED_DIM

# Счётчик для уникальных имён коллекций (regex-валидация zvec: однобуквенные
# имена вроде 't' отвергаются, 'probe_itX' проходит).
_COLL_SEQ = 0


def _build_schema(name: str) -> "zvec.CollectionSchema":
    """Schema идентична zvec_indexer._create_collection: два FTS-поля
    с разными токенайзерами (text=standard, text_fts=whitespace) + вектор."""
    return zvec.CollectionSchema(
        name=name,
        fields=[
            FieldSchema('path', DataType.STRING, nullable=False),
            FieldSchema('context', DataType.STRING, nullable=True),
            FieldSchema(
                'text',
                DataType.STRING,
                nullable=False,
                index_param=FtsIndexParam(
                    tokenizer_name='standard',
                    filters=['lowercase'],
                ),
            ),
            FieldSchema(
                'text_fts',
                DataType.STRING,
                nullable=False,
                index_param=FtsIndexParam(
                    tokenizer_name='whitespace',
                    filters=['lowercase'],
                ),
            ),
        ],
        vectors=[
            VectorSchema(
                'embedding',
                DataType.VECTOR_FP32,
                dimension=ZVEC_EMBED_DIM,
                index_param=HnswIndexParam(),
            ),
        ],
    )


@pytest.fixture
def coll():
    """Временная zvec-коллекция. Создаётся в tempfile.mkdtemp(), удаляется
    в finally. У Collection нет явного close() — освобождение = очистка каталога."""
    global _COLL_SEQ
    _COLL_SEQ += 1
    name = f"probe_it{_COLL_SEQ}"
    tmpdir = tempfile.mkdtemp(prefix="dev_rag_it_")
    coll_path = os.path.join(tmpdir, name)
    c = zvec.create_and_open(
        path=coll_path,
        schema=_build_schema(name),
        option=CollectionOption(read_only=False, enable_mmap=True),
    )
    try:
        yield c
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _insert(c, doc_id: str, text: str, text_fts: str, path: str = "x.py"):
    """Вставить один документ и построить индексы (без optimize FTS пуст)."""
    c.insert([
        Doc(
            id=doc_id,
            fields={
                'path': path,
                'context': '',
                'text': text,
                'text_fts': text_fts,
            },
            vectors={'embedding': list(_DUMMY_VEC)},
        )
    ])
    c.flush()
    c.optimize()


def _hit_ids(c, field: str, match: str) -> set:
    """Найти документы по FTS в поле field. Возвращает множество id."""
    resp = c.query(
        queries=[Query(field_name=field, fts=Fts(match_string=match))],
        topk=10,
    )
    return {d.id for d in resp}


# =====================================================================
# Положительные: не-ASCII письменности находятся через text_fts (whitespace)
# =====================================================================

@pytest.mark.integration
def test_cyrillic_found_casefold(coll):
    """Кириллица находится, и регистр не важен: 'коробка' и 'КОРОБКА'
    находят один документ. Регистр складывает Python casefold() —
    фильтр lowercase в zvec трогает только ASCII."""
    _insert(coll, 'ru1', 'Коробка двери', make_fts_unicode('Коробка двери'))
    assert _hit_ids(coll, 'text_fts', make_fts_unicode('коробка')) == {'ru1'}
    assert _hit_ids(coll, 'text_fts', make_fts_unicode('КОРОБКА')) == {'ru1'}


@pytest.mark.integration
def test_german_sharp_s_casefold(coll):
    """Регрессия на casefold (не lower): 'ß' и 'SS' должны сводиться к одному
    токену. lower() это не делал — 'TÜRGRÖSSE' не находил 'Türgröße'."""
    _insert(coll, 'de1', 'Türgröße', make_fts_unicode('Türgröße'))
    assert _hit_ids(coll, 'text_fts', make_fts_unicode('Türgröße')) == {'de1'}
    assert _hit_ids(coll, 'text_fts', make_fts_unicode('TÜRGRÖSSE')) == {'de1'}


@pytest.mark.integration
def test_greek_final_sigma(coll):
    """Греческая финальная сигма ς и обычная Σ складываются к одному токену."""
    _insert(coll, 'el1', 'πόρτας', make_fts_unicode('πόρτας'))
    assert _hit_ids(coll, 'text_fts', make_fts_unicode('πόρτας')) == {'el1'}
    assert _hit_ids(coll, 'text_fts', make_fts_unicode('ΠΌΡΤΑΣ')) == {'el1'}


@pytest.mark.integration
def test_arabic_found(coll):
    """Арабский (RTL, без пробелов внутри слова) находится."""
    _insert(coll, 'ar1', 'الباب', make_fts_unicode('الباب'))
    assert _hit_ids(coll, 'text_fts', make_fts_unicode('الباب')) == {'ar1'}


# =====================================================================
# Регресс: латиница и идентификаторы в поле text (standard) не сломаны
# =====================================================================

@pytest.mark.integration
def test_latin_not_broken(coll):
    """Латиница по-прежнему находится через text (standard)."""
    _insert(coll, 'en1', 'воркер RabbitMQ диспетчер',
            make_fts_unicode('воркер RabbitMQ диспетчер'))
    assert _hit_ids(coll, 'text', 'RabbitMQ') == {'en1'}


@pytest.mark.integration
def test_identifier_split_by_standard(coll):
    """Standard режет идентификаторы по подчёркиванию: 'obj_k3_gab3'
    находится по части 'gab3'. Это то, ради чего text остаётся на standard —
    терять такое нельзя."""
    _insert(coll, 'id1', 'def build(): obj_k3_gab3(obj)', '')
    assert _hit_ids(coll, 'text', 'gab3') == {'id1'}
    assert _hit_ids(coll, 'text', 'obj_k3_gab3') == {'id1'}


# =====================================================================
# Негативный: фиксирует причину существования text_fts
# =====================================================================

@pytest.mark.integration
def test_standard_does_not_find_cyrillic(coll):
    """ГЛАВНЫЙ негативный тест. Токенайзер standard НЕ находит кириллицу —
    выбрасывает не-ASCII токены целиком. Именно поэтому нужно отдельное поле
    text_fts с whitespace.

    Если этот тест упадёт (станет находить кириллицу) — значит zvec починил
    standard, и обход через text_fts можно убирать.
    """
    _insert(coll, 'ru_neg', 'коробка двери сборка', 'коробка двери сборка')
    assert _hit_ids(coll, 'text', 'коробка') == set()
    assert _hit_ids(coll, 'text', 'дверь') == set()
