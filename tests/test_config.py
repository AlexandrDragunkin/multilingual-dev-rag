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


def _installed_version():
    """Версия из метаданных, или None если пакет не установлен."""
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version('multilingual-dev-rag')
    except PackageNotFoundError:
        return None


def test_version_resolved_from_metadata():
    """__version__ разрешается в настоящий номер, а не в 'unknown'.

    Номер приходит из git-тега через setuptools-scm и живёт только в
    метаданных. Своей копии в коде больше нет, так что разъезжаться нечему —
    но появился новый способ соврать тихо: если имя в
    importlib.metadata.version() разойдётся с name в pyproject (переименование,
    опечатка), сработает fallback и __version__ молча станет 'unknown'.
    Исключения при этом не будет — только неверный ответ.
    """
    import dev_rag

    if _installed_version() is None:
        pytest.skip('пакет не установлен — версию брать неоткуда')

    assert dev_rag.__version__, '__version__ пустой'
    assert dev_rag.__version__ != 'unknown', (
        'пакет установлен, а __version__ свалился в fallback — вероятно, '
        'имя в importlib.metadata.version() разошлось с name в pyproject'
    )


def test_mcp_serverinfo_reports_package_version():
    """MCP-хендшейк отдаёт версию пакета, а не зашитую строку.

    До перехода на setuptools-scm здесь лежал литерал '0.1.0' — третья копия
    номера, которую не стерёг ни тест, ни CI. Клиент MCP видел бы её как
    версию сервера ещё долго после релиза 0.2.0.
    """
    import dev_rag
    from dev_rag.mcp_server import handle

    resp = handle({'method': 'initialize', 'params': {}})
    assert resp['result']['serverInfo']['version'] == dev_rag.__version__


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


class TestIndexLocation:
    """Индекс — производные данные, и живёт он не в каталоге кода."""

    def test_index_not_inside_package(self, monkeypatch, tmp_path):
        """Писать в site-packages нельзя: на Linux этот каталог принадлежит root."""
        import importlib
        import dev_rag.config
        monkeypatch.setenv('DEV_RAG_ROOT', str(tmp_path))
        monkeypatch.delenv('ZVEC_DB_PATH', raising=False)
        importlib.reload(dev_rag.config)
        pkg_dir = os.path.dirname(os.path.abspath(dev_rag.config.__file__))
        assert not dev_rag.config.ZVEC_DB_PATH.startswith(pkg_dir)

    def test_different_roots_get_different_indexes(self, monkeypatch, tmp_path):
        """Иначе смена DEV_RAG_ROOT молча затирает индекс прошлого корпуса,
        и поиск начинает уверенно отвечать не из того репозитория."""
        import importlib
        import dev_rag.config
        monkeypatch.delenv('ZVEC_DB_PATH', raising=False)
        paths = []
        for name in ('repo_one', 'repo_two'):
            root = tmp_path / name
            root.mkdir()
            monkeypatch.setenv('DEV_RAG_ROOT', str(root))
            importlib.reload(dev_rag.config)
            paths.append(dev_rag.config.ZVEC_DB_PATH)
        assert paths[0] != paths[1]

    def test_same_root_is_stable(self, monkeypatch, tmp_path):
        """Один корень — один и тот же путь между запусками."""
        import importlib
        import dev_rag.config
        monkeypatch.delenv('ZVEC_DB_PATH', raising=False)
        monkeypatch.setenv('DEV_RAG_ROOT', str(tmp_path))
        importlib.reload(dev_rag.config)
        first = dev_rag.config.ZVEC_DB_PATH
        importlib.reload(dev_rag.config)
        assert dev_rag.config.ZVEC_DB_PATH == first

    def test_path_keeps_readable_slug(self, monkeypatch, tmp_path):
        """В пути должно быть видно, какой это корпус, а не только хэш."""
        import importlib
        import dev_rag.config
        root = tmp_path / 'my_project'
        root.mkdir()
        monkeypatch.delenv('ZVEC_DB_PATH', raising=False)
        monkeypatch.setenv('DEV_RAG_ROOT', str(root))
        importlib.reload(dev_rag.config)
        assert 'my_project-' in dev_rag.config.ZVEC_DB_PATH

    def test_env_override_wins(self, monkeypatch, tmp_path):
        """ZVEC_DB_PATH перекрывает вычисленный путь целиком."""
        import importlib
        import dev_rag.config
        monkeypatch.setenv('DEV_RAG_ROOT', str(tmp_path))
        monkeypatch.setenv('ZVEC_DB_PATH', str(tmp_path / 'custom'))
        importlib.reload(dev_rag.config)
        assert dev_rag.config.ZVEC_DB_PATH == str(tmp_path / 'custom')


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
