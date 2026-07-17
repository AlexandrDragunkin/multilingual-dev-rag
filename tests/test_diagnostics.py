# -*- coding: utf-8 -*-
"""Дефект 2 («лучший» вариант плана 001): причина недоступности индекса
попадает в тело MCP-ответа, а не только в stderr.

Тесты юнитовые — реальный индекс не нужен. Подменяются `_open_or_create_collection`
(чтобы открытие падало как при эксклюзивном lock) и `_embed` (чтобы не грузить
модель). Проверяется диагностический канал `get_last_diagnostics()` и то, что
`mcp_server.rag_search` выносит причину в текст ответа.

Почему без @pytest.mark.integration: движок zvec здесь не работает с реальной
коллекцией — весь путь открытия замокан. Это проверка контракта диагностики,
а не поведения zvec.
"""
from dev_rag import mcp_server, zvec_searcher


def _force_unavailable(monkeypatch):
    """Сделать открытие любой категории падающим, эмбеддинг — фиктивным."""
    monkeypatch.setattr(zvec_searcher, '_embed', lambda text: [0.0] * 8)

    def _boom(category):
        # Дословно воспроизводит ошибку zvec при конфликте lock (дефект 1).
        raise RuntimeError("Can't lock read-write collection")

    monkeypatch.setattr(zvec_searcher, '_open_or_create_collection', _boom)
    # Сбросить кэш: для уже открытой категории _open_or_create_collection
    # не вызвался бы, и мок не сработал бы.
    zvec_searcher._collections.clear()


def test_search_publishes_diagnostic_on_open_failure(monkeypatch):
    _force_unavailable(monkeypatch)
    results = zvec_searcher.search('запрос', collection='docs')
    assert results == []
    diag = zvec_searcher.get_last_diagnostics()
    assert any('docs' in d for d in diag), diag


def test_diagnostics_cleared_between_searches(monkeypatch):
    _force_unavailable(monkeypatch)
    zvec_searcher.search('запрос', collection='docs')
    assert zvec_searcher.get_last_diagnostics()  # непусто после сбоя

    # Теперь открытие успешно, коллекция отдаёт пустую выдачу — прошлая
    # диагностика не должна «протечь» в следующий поиск.
    class _FakeColl:
        def query(self, *a, **k):
            return []

    monkeypatch.setattr(zvec_searcher, '_open_or_create_collection',
                        lambda category: _FakeColl())
    zvec_searcher._collections.clear()
    zvec_searcher.search('запрос', collection='docs')
    assert zvec_searcher.get_last_diagnostics() == []


def test_mcp_body_carries_diagnostic(monkeypatch):
    _force_unavailable(monkeypatch)
    body = mcp_server.rag_search(query='запрос', collection='docs', n_results=5)
    # Тело содержит и «пусто», и причину — тихий отказ стал явным.
    assert 'No results found' in body
    assert 'Диагностика индекса' in body
    assert 'docs' in body
