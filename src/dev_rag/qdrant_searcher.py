# -*- coding: utf-8 -*-
"""Qdrant-бэкенд (legacy) — Ollama nomic-embed-text (768d)."""
from __future__ import annotations

from qdrant_client import QdrantClient

from .config import QDRANT_URL, COLLECTIONS
from .embedder import embed as ollama_embed

_client = QdrantClient(url=QDRANT_URL)


def search(query: str, collection: str = 'all', n: int = 5) -> list:
    """Векторный поиск через Qdrant.

    Args:
        query: Текст поискового запроса.
        collection: 'all', 'docs', 'code' или 'plans'.
        n: Количество результатов.

    Returns:
        Список словарей: {'score', 'path', 'context', 'text'}.
    """
    vector = ollama_embed(query)
    colls = list(COLLECTIONS.values()) if collection == 'all' else [COLLECTIONS[collection]]
    results = []
    for coll in colls:
        try:
            resp = _client.query_points(
                collection_name=coll, query=vector, limit=n, with_payload=True
            )
            for h in resp.points:
                results.append({
                    'score': h.score,
                    'path': h.payload.get('path', ''),
                    'context': h.payload.get('context', ''),
                    'text': h.payload.get('text', ''),
                })
        except Exception:
            pass
    results.sort(key=lambda x: x['score'], reverse=True)
    return results[:n]
