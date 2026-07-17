# -*- coding: utf-8 -*-
"""Интеграционные тесты: реальный движок zvec, реальная FTS-токенизация.

Юнит-тесты normalizer'а (test_fts_normalizer.py) проверяют чистую функцию
make_fts_unicode — но баг FTS живёт не в ней, а в токенайзере движка. Эти тесты
ходят через настоящий zvec: создают временную коллекцию, вставляют документы,
ищут.

Платформенная особенность: токенайзер `standard` выбрасывает не-ASCII токены
на Windows и Linux, но **находит** их на macOS. Негативный тест
test_standard_does_not_find_cyrillic учитывает это и ожидает разный исход по
платформам. Если zvec изменит поведение на любой из них — тест упадёт, и это
сигнал перепроверить, нужен ли обход через text_fts на этой платформе.

Запуск: python -m pytest tests/test_zvec_integration.py -v -m integration
"""
import gc
import os
import platform
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
    в finally.

    У Collection в zvec 0.5.1 нет явного close()/release()/__exit__: сборщик
    мусора отпускает файловые дескрипторы RocksDB при удалении последней
    ссылки. Поэтому в finally — del + gc.collect() ДО rmtree. Без этого на
    macOS rmtree бежит, пока zvec ещё держит коллекцию открытой, и RocksDB
    печатает в лог «Failed to flush/close» на каждый тест. Тесты проходят,
    но шум маскирует настоящие ошибки.
    """
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
        del c
        gc.collect()
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
def test_french_accents_found(coll):
    """Французская диакритика находится, и регистр не важен: É и é сводятся
    к одному токену.

    README перечисляет французский среди проверенных сквозным зондом, но до
    этого теста обещание держалось на unit-тесте нормализатора — то есть на
    чистой функции, а не на движке. Ровно в движке и жили баги с не-ASCII.
    """
    _insert(coll, 'fr1', 'Poignée encastrée', make_fts_unicode('Poignée encastrée'))
    assert _hit_ids(coll, 'text_fts', make_fts_unicode('poignée')) == {'fr1'}
    assert _hit_ids(coll, 'text_fts', make_fts_unicode('POIGNÉE')) == {'fr1'}


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
# Read-only открытие коллекции (plan 001, дефект 1)
# =====================================================================
#
# Поисковый путь должен открывать существующую коллекцию как read_only=True,
# иначе клиенты поиска берут эксклюзивный RW-lock и второй процесс получает
# «Can't lock read-write collection». Эти тесты фиксируют два свойства, на
# которые опирается исправление в zvec_searcher._open_or_create_collection:
#
#   1. RO-хэндл отвечает на query() (FTS+вектор) — иначе поиск сломался бы.
#   2. Два RO-хэндла сосуществуют одновременно — это сценарий «два MCP-клиента
#      на одной машине» (plan 001, приёмка 4) и «клиент + CLI» (приёмка 3).
#
# ВАЖНО о границах zvec 0.5.1: RO-хэндл НЕ открывается рядом с удерживаемым
# RW-хэндлом (проверено вручную на этой версии: «Can't lock read-only
# collection»). То есть «искать, пока индексатор пишет» этим не покрывается —
# но приёмка plan 001 этого и не требует: все её шаги стартуют из «никто не
# держит коллекцию», а конфликтом был именно поиск-поиск, а не запись-поиск.
# Запись оставлена RW в zvec_indexer._create_collection (read_only=False).

@pytest.mark.integration
def test_readonly_handle_answers_query(tmp_path):
    """RO-хэндл отвечает на FTS-query так же, как RW: query() на read-only
    коллекции работает. Без этого перевод поиска на read_only=True ломает
    сам поиск (это и был риск, отмеченный в plan 001, таблица исполнения)."""
    import gc
    import shutil
    import zvec

    coll_path = str(tmp_path / "ro_probe")
    rw = zvec.create_and_open(
        path=coll_path,
        schema=_build_schema("ro_probe"),
        option=CollectionOption(read_only=False, enable_mmap=True),
    )
    try:
        _insert(rw, 'en1', 'воркер RabbitMQ диспетчер',
                make_fts_unicode('воркер RabbitMQ диспетчер'))
    finally:
        # RW-хэндл обязан уйти до RO-open (см. комментарий к блоку выше):
        # в zvec 0.5.1 RO не делит LOCK с удерживаемым RW.
        del rw
        gc.collect()

    try:
        ro = zvec.open(
            coll_path,
            option=CollectionOption(read_only=True, enable_mmap=True),
        )
        try:
            hits = _hit_ids(ro, 'text', 'RabbitMQ')
            assert hits == {'en1'}, (
                "read-only хэндл не ответил на FTS-query; план 001/дефект 1 "
                f"опирается на то, что query() работает на RO-хэндле. hits={hits}"
            )
        finally:
            del ro
            gc.collect()
    finally:
        shutil.rmtree(coll_path, ignore_errors=True)


@pytest.mark.integration
def test_two_readonly_handles_coexist(tmp_path):
    """Два RO-хэндла на одну коллекцию открыты одновременно и оба отвечают на
    query() — это сценарий «два MCP-клиента / клиент + CLI» (plan 001,
    приёмки 3-4). RO не берёт эксклюзивный LOCK, поэтому второй поиск не
    получает «Can't lock ... collection»."""
    import gc
    import shutil
    import zvec

    coll_path = str(tmp_path / "ro_pair")
    rw = zvec.create_and_open(
        path=coll_path,
        schema=_build_schema("ro_pair"),
        option=CollectionOption(read_only=False, enable_mmap=True),
    )
    try:
        _insert(rw, 'en1', 'воркер RabbitMQ диспетчер',
                make_fts_unicode('воркер RabbitMQ диспетчер'))
    finally:
        del rw
        gc.collect()

    ro1 = ro2 = None
    try:
        ro1 = zvec.open(coll_path, option=CollectionOption(read_only=True, enable_mmap=True))
        ro2 = zvec.open(coll_path, option=CollectionOption(read_only=True, enable_mmap=True))
        assert _hit_ids(ro1, 'text', 'RabbitMQ') == {'en1'}
        assert _hit_ids(ro2, 'text', 'RabbitMQ') == {'en1'}
    finally:
        del ro1, ro2
        gc.collect()
        shutil.rmtree(coll_path, ignore_errors=True)


# =====================================================================
# Негативный: фиксирует причину существования text_fts
# =====================================================================

# Поведение токенайзера standard зависит от платформы (проверено CI-матрицей
# на zvec 0.5.1): на Windows и Linux он выбрасывает не-ASCII токены целиком,
# на macOS — индексирует их. Это не баг zvec как таковой и не баг обхода; это
# факт о движке, от которого зависит, нужен ли text_fts на данной платформе.
# На macOS обход избыточен (standard и так нашёл бы), но безвреден: дороги
# дублирующих совпадений не создают, т.к. латиница в text_fts не попадает.
_STANDARD_DROPS_NON_ASCII = platform.system() != 'Darwin'


@pytest.mark.integration
def test_standard_cyrillic_behavior_matches_platform(coll):
    """ГЛАВНЫЙ негативный тест. Фиксирует причину существования поля text_fts:
    на Windows/Linux токенайзер `standard` выбрасывает кириллицу целиком, и без
    отдельного поля с `whitespace` поиск по ней невозможен.

    Поведение `standard` платформозависимо (zvec 0.5.1): на macOS он кириллицу
    находит. Поэтому ожидаемый исход зависит от platform.system():
      - Windows / Linux  → standard НЕ находит → text_fts необходим
      - macOS            → standard находит    → text_fts избыточен, но безвреден

    Тест обязан уметь падать на каждой платформе: если zvec изменит поведение
    (станет находить кириллицу на Win/Linux — обход можно убирать; перестанет
    находить на macOS — обход становится необходим), ассерт покраснеет.
    """
    _insert(coll, 'ru_neg', 'коробка двери сборка', 'коробка двери сборка')
    hits = _hit_ids(coll, 'text', 'коробка')
    if _STANDARD_DROPS_NON_ASCII:
        # Win/Linux: standard выбрасывает кириллицу — поиск пуст.
        assert hits == set(), (
            "standard unexpectedly matches Cyrillic on "
            f"{platform.system()}: {hits}. If zvec changed, the text_fts "
            "workaround may no longer be needed on this platform."
        )
    else:
        # macOS: standard кириллицу находит.
        assert hits == {'ru_neg'}, (
            "standard unexpectedly drops Cyrillic on "
            f"{platform.system()}: {hits}. If zvec changed, the text_fts "
            "workaround is now required on this platform."
        )
