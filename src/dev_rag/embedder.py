# -*- coding: utf-8 -*-
"""Эмбеддинги через Ollama nomic-embed-text (legacy-бэкенд).

Используется только при RAG_BACKEND=qdrant.
Для zvec-бэкенда эмбеддинги генерируются локально (sentence-transformers)
внутри zvec_searcher.py.
"""
from __future__ import annotations

import httpx

from .config import OLLAMA_URL, EMBED_MODEL


def embed(text: str) -> list:
    """Получить вектор эмбеддинга через Ollama API."""
    r = httpx.post(
        f'{OLLAMA_URL}/api/embeddings',
        json={'model': EMBED_MODEL, 'prompt': text},
        timeout=120.0,
    )
    r.raise_for_status()
    return r.json()['embedding']


def embed_batch(texts: list, batch_size: int = 16) -> list:
    """Эмбеддинги для списка текстов."""
    vectors = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        for text in batch:
            vectors.append(embed(text))
    return vectors
