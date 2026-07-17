# -*- coding: utf-8 -*-
"""multilingual-dev-rag — local-first RAG over source code and technical docs.

    from dev_rag import search
    results = search('ферма воркеров', collection='docs', n=5)

Set `DEV_RAG_ROOT` to the repository you want to index, and `DEV_RAG_PROFILE`
to a profile describing which files count. See `dev_rag.config`.
"""
from importlib.metadata import PackageNotFoundError, version as _version

from .config import DEV_RAG_PROFILE, DEV_RAG_ROOT, RAG_BACKEND
from .searcher import search

try:
    # Источник правды — git-тег: setuptools-scm кладёт номер в метаданные при
    # сборке, отсюда мы его только читаем. Своей копии номера в коде нет.
    __version__ = _version('multilingual-dev-rag')
except PackageNotFoundError:
    # Пакет не установлен — импорт прямо из исходников (src на PYTHONPATH).
    # Метаданных нет, и взять номер неоткуда: в дереве его никто не хранит.
    __version__ = 'unknown'

__all__ = [
    'search',
    'RAG_BACKEND',
    'DEV_RAG_ROOT',
    'DEV_RAG_PROFILE',
    '__version__',
]
