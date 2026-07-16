# -*- coding: utf-8 -*-
"""Базовые тесты конфигурации и импорта.

Запуск: pytest
"""
import os

import pytest


def test_import_search():
    """Пакет импортируется и экспортирует search()."""
    from dev_rag import search
    assert callable(search)


def test_config_defaults():
    """Config загружается с дефолтами."""
    from dev_rag.config import RAG_BACKEND, COLLECTIONS
    assert RAG_BACKEND in ('qdrant', 'zvec')
    assert 'docs' in COLLECTIONS
    assert 'code' in COLLECTIONS
    assert 'plans' in COLLECTIONS


def test_root_env_override(monkeypatch, tmp_path):
    """DEV_RAG_ROOT читается из переменной окружения."""
    import importlib
    import dev_rag.config
    monkeypatch.setenv('DEV_RAG_ROOT', str(tmp_path))
    importlib.reload(dev_rag.config)
    assert dev_rag.config.DEV_RAG_ROOT == str(tmp_path)
    assert dev_rag.config.require_root() == str(tmp_path)


def test_require_root_raises_when_unset(monkeypatch):
    """Корень не угадывается: без DEV_RAG_ROOT — внятное падение.

    Раньше корень по умолчанию считался как <пакет>/../.. и указывал в каталог
    без корпуса: globs не совпадали ни с чем, индексация печатала «0 chunks» и
    завершалась успешно. Тихий неверный ответ хуже падения.
    """
    import importlib
    import dev_rag.config
    monkeypatch.delenv('DEV_RAG_ROOT', raising=False)
    importlib.reload(dev_rag.config)
    with pytest.raises(RuntimeError, match='DEV_RAG_ROOT is not set'):
        dev_rag.config.require_root()


def test_require_root_raises_when_missing_dir(monkeypatch):
    """Путь есть, каталога нет — тоже падаем, а не индексируем пустоту."""
    import importlib
    import dev_rag.config
    monkeypatch.setenv('DEV_RAG_ROOT', '/definitely/not/a/real/dir')
    importlib.reload(dev_rag.config)
    with pytest.raises(RuntimeError, match='not a directory'):
        dev_rag.config.require_root()


def test_builtin_profiles_load():
    """Оба встроенных профиля читаются и имеют нужную форму."""
    from dev_rag.config import load_profile
    for name in ('generic', 'k3_mebel'):
        p = load_profile(name)
        assert p['name'] == name
        assert set(p['collections']) == {'docs', 'code', 'plans'}
        assert set(p['index_patterns']) == {'docs', 'code', 'plans'}


def test_unknown_profile_lists_available():
    """Опечатка в имени профиля — сообщение подсказывает, что доступно."""
    from dev_rag.config import load_profile
    with pytest.raises(RuntimeError, match='generic'):
        load_profile('no_such_profile')


def test_chunker_md():
    """Chunker разбивает markdown по заголовкам."""
    from dev_rag.chunker import chunk_text
    text = "# Title\n\nContent here.\n\n## Section\n\nMore content."
    chunks = list(chunk_text(text, 'test.md'))
    assert len(chunks) >= 1
    assert all('text' in c and 'path' in c for c in chunks)


def test_chunker_python():
    """Chunker разбивает Python по class/def."""
    from dev_rag.chunker import chunk_text
    text = "class Foo:\n    pass\n\ndef bar():\n    return 1\n"
    chunks = list(chunk_text(text, 'test.py'))
    assert len(chunks) >= 1


def test_reader_utf8(tmp_path):
    """Reader читает UTF-8 файл."""
    from dev_rag.reader import read_file_text, file_hash
    f = tmp_path / "test.txt"
    f.write_text("Привет, мир!", encoding='utf-8')
    assert read_file_text(str(f)) == "Привет, мир!"
    assert len(file_hash(str(f))) == 32  # md5 hex


def test_reader_cp1251(tmp_path):
    """Reader читает CP1251 файл."""
    from dev_rag.reader import read_file_text
    f = tmp_path / "test.txt"
    f.write_bytes("Привет".encode('cp1251'))
    assert read_file_text(str(f)) == "Привет"


def test_reader_bom(tmp_path):
    """Reader пропускает UTF-8 BOM."""
    from dev_rag.reader import read_file_text
    f = tmp_path / "test.txt"
    f.write_bytes(b'\xef\xbb\xbfHello')
    assert read_file_text(str(f)) == "Hello"
