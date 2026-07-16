# -*- coding: utf-8 -*-
"""zvec-бэкенд — in-process векторная БД + локальная multilingual-модель.

Без внешних серверов. Гибридный поиск: FTS (точные совпадения) + vector (семантика)
с RRF reranker.
"""
from __future__ import annotations

import os
from typing import Optional

import zvec

# Движок нужно инициализировать перед любыми операциями, иначе FTS тихо
# возвращает 0 результатов. Guard от двойного init (indexer + searcher).
if not getattr(zvec, "_dev_rag_inited", False):
    zvec.init()
    zvec._dev_rag_inited = True

from zvec import (
    Collection,
    CollectionOption,
    DataType,
    Doc,
    FieldSchema,
    FtsIndexParam,
    HnswIndexParam,
    VectorSchema,
)
from zvec.extension.multi_vector_reranker import RrfReRanker
from zvec.model.param.query import Fts, Query

from .config import ZVEC_DB_PATH, ZVEC_MODEL, ZVEC_EMBED_DIM, COLLECTIONS
from .fts_normalizer import has_ascii_word, has_non_ascii, make_fts_unicode

# --- Lazy-loaded singletons ---
_embedder = None
_collections: dict = {}  # category -> Collection


def _get_embedder():
    """Lazy-load sentence-transformers (первый вызов ~50с, затем мгновенно)."""
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer(ZVEC_MODEL)
    return _embedder


def _embed(text: str) -> list:
    """Эмбеддинг через локальную multilingual-модель."""
    return _get_embedder().encode(text, normalize_embeddings=True).tolist()


def _open_or_create_collection(category: str) -> Collection:
    """Открыть существующую zvec-коллекцию или создать новую."""
    coll_path = os.path.join(ZVEC_DB_PATH, category)
    if os.path.exists(coll_path):
        return zvec.open(coll_path)

    schema = zvec.CollectionSchema(
        name=category,
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
            # Держать синхронно с zvec_indexer.py — см. fts_normalizer.py.
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
    return zvec.create_and_open(
        path=coll_path,
        schema=schema,
        option=CollectionOption(read_only=False, enable_mmap=True),
    )


def _get_collection(category: str) -> Optional[Collection]:
    """Получить (или lazy-open) zvec-коллекцию по категории."""
    global _collections
    if category not in _collections:
        try:
            _collections[category] = _open_or_create_collection(category)
        except Exception:
            return None
    return _collections[category]


def search(query: str, collection: str = 'all', n: int = 5) -> list:
    """Гибридный поиск (FTS + vector) через zvec.

    Args:
        query: Текст поискового запроса.
        collection: 'all', 'docs', 'code' или 'plans'.
        n: Количество результатов.

    Returns:
        Список словарей: {'score', 'path', 'context', 'text'}.
    """
    categories = list(COLLECTIONS.keys()) if collection == 'all' else [collection]
    query_vec = _embed(query)
    results = []
    # Берём n из каждой категории, затем сортируем и обрезаем.
    per = n
    reranker = RrfReRanker(rank_constant=60)

    for cat in categories:
        coll = _get_collection(cat)
        if coll is None:
            continue
        try:
            # Гибрид: vector + FTS с RRF reranker.
            # FTS-дороги подбираются по содержимому запроса, чтобы не запускать
            # заведомо пустую: text не видит не-ASCII, text_fts не содержит
            # чистой латиницы. Так дороги не пересекаются и документ не получает
            # двойной вес в RRF на смешанном запросе. См. fts_normalizer.py.
            queries = [Query(field_name='embedding', vector=query_vec)]
            if has_ascii_word(query):
                queries.append(
                    Query(field_name='text', fts=Fts(match_string=query))
                )
            if has_non_ascii(query):
                queries.append(
                    Query(field_name='text_fts',
                          fts=Fts(match_string=make_fts_unicode(query)))
                )
            resp = coll.query(
                queries=queries,
                topk=per,
                reranker=reranker,
            )
            for doc in resp:
                results.append({
                    'score': doc.score,
                    'path': doc.fields.get('path', ''),
                    'context': doc.fields.get('context', ''),
                    'text': doc.fields.get('text', ''),
                })
        except Exception:
            # Fallback на vector-only если FTS не сработал
            try:
                resp = coll.query(
                    Query(field_name='embedding', vector=query_vec),
                    topk=per,
                )
                for doc in resp:
                    results.append({
                        'score': doc.score,
                        'path': doc.fields.get('path', ''),
                        'context': doc.fields.get('context', ''),
                        'text': doc.fields.get('text', ''),
                    })
            except Exception:
                pass

    results.sort(key=lambda x: x['score'], reverse=True)
    return results[:n]
