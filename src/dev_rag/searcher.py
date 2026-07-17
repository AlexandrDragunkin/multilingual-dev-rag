# -*- coding: utf-8 -*-
"""Search router — picks the backend from RAG_BACKEND.

    RAG_BACKEND=zvec   (default) — in-process zvec + local multilingual model
    RAG_BACKEND=qdrant (legacy)  — Qdrant server + Ollama embedder

The qdrant path predates zvec and is kept only as a fallback; its embedder
(nomic-embed-text) is English-centric and recalls poorly on Russian.
"""
from __future__ import annotations

from .config import RAG_BACKEND

if RAG_BACKEND == 'zvec':
    from .zvec_searcher import search, get_last_diagnostics
else:
    from .qdrant_searcher import search

    def get_last_diagnostics() -> list:
        """qdrant-бэкенд не публикует диагностику открытия индекса —
        возвращаем пусто, чтобы mcp_server оставался backend-agnostic."""
        return []

__all__ = ['search', 'get_last_diagnostics']
