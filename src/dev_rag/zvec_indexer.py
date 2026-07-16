# -*- coding: utf-8 -*-
"""zvec indexer — full-corpus indexing.

Использование через CLI: python -m dev_rag.zvec_indexer [--force] [--category all|docs|code|plans]
"""
from __future__ import annotations

import os
import sys
import time
import glob
import hashlib
import argparse

import zvec

# Движок нужно инициализировать перед любыми операциями, иначе FTS не работает.
zvec.init()

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

from .config import (
    require_root,
    ZVEC_DB_PATH,
    ZVEC_MODEL,
    ZVEC_EMBED_DIM,
    DEV_RAG_ROOT,
    DEV_RAG_PROFILE,
    COLLECTIONS,
    INDEX_PATTERNS,
)
from .chunker import chunk_text
from .fts_normalizer import make_fts_unicode
from .reader import read_file_text


# --- Embedding (lazy-loaded) ---
_embedder = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        print(f"[zvec-indexer] Loading model: {ZVEC_MODEL} ...", flush=True)
        _embedder = SentenceTransformer(ZVEC_MODEL)
        print(f"[zvec-indexer] Model loaded.", flush=True)
    return _embedder


def _embed(text: str) -> list:
    return _get_embedder().encode(text, normalize_embeddings=True).tolist()


# --- Collection management ---
def _create_collection(category: str, force: bool = False) -> Collection:
    """Создать новую zvec-коллекцию для категории."""
    coll_path = os.path.join(ZVEC_DB_PATH, category)

    if force and os.path.exists(coll_path):
        import shutil
        shutil.rmtree(coll_path)
        print(f"[zvec-indexer] Deleted existing: {coll_path}", flush=True)

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
            # Служебное поле: только кириллические токены, приведённые к нижнему
            # регистру. Не отдавать в выдачу — для этого есть text.
            # whitespace, а не standard: standard выбрасывает кириллицу целиком.
            # Подробности и отвергнутые альтернативы — в fts_normalizer.py.
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


def index_category(category: str, force: bool = False) -> int:
    """Проиндексировать все файлы категории. Возвращает кол-во чанков."""
    patterns = INDEX_PATTERNS.get(category, [])
    if not patterns:
        print(f"[zvec-indexer] No patterns for category: {category}", flush=True)
        return 0

    # Сбор файлов
    files = []
    for pattern in patterns:
        matched = glob.glob(os.path.join(DEV_RAG_ROOT, pattern), recursive=True)
        files.extend(matched)
    files = sorted(set(files))
    print(f"[zvec-indexer] [{category}] {len(files)} files to index", flush=True)

    # Создание/открытие коллекции
    coll = _create_collection(category, force=force)

    # Индексация
    total_chunks = 0
    t0 = time.time()

    for fi, fpath in enumerate(files):
        rel_path = os.path.relpath(fpath, DEV_RAG_ROOT).replace('\\', '/')
        try:
            content = read_file_text(fpath)
        except Exception as e:
            print(f"  Skip {rel_path}: {e}", flush=True)
            continue

        docs = []
        # Нумеруем чанки сквозным enumerate по всему файлу, а не берём
        # chunk['chunk_idx']: тот сбрасывается в 0 на каждой секции markdown
        # (_sliding_window вызывается отдельно на каждый заголовок). Из-за этого
        # doc_id совпадал у первых чанков всех секций, и они затирали друг друга
        # при insert — молча, потому что insert возвращает ok. Сводный
        # архитектурный документ давал 61 чанк, в индексе оставалось 3.
        # indexer.py (Qdrant) нумерует так же — через enumerate.
        for i, chunk in enumerate(chunk_text(content, rel_path)):
            vec = _embed(chunk['text'])
            doc_id = hashlib.md5(
                f"{rel_path}#{i}".encode()
            ).hexdigest()[:16]
            doc = Doc(
                id=doc_id,
                fields={
                    'path': rel_path,
                    'context': chunk['context'],
                    'text': chunk['text'],
                    'text_fts': make_fts_unicode(chunk['text']),
                },
                vectors={'embedding': vec},
            )
            docs.append(doc)

        if docs:
            results = coll.insert(docs)
            ok = sum(1 for r in results if r.ok())
            total_chunks += ok

        if (fi + 1) % 50 == 0 or fi + 1 == len(files):
            elapsed = time.time() - t0
            print(
                f"  [{category}] {fi+1}/{len(files)} files, "
                f"{total_chunks} chunks, {elapsed:.1f}s",
                flush=True,
            )

    elapsed = time.time() - t0
    print(
        f"[zvec-indexer] [{category}] Done: {total_chunks} chunks "
        f"in {elapsed:.1f}s ({total_chunks/max(elapsed,1):.0f} chunks/s)",
        flush=True,
    )

    # Построение FTS + HNSW индексов — без этого FTS возвращает 0 результатов.
    print(f"[zvec-indexer] [{category}] Flushing + optimizing (building indexes)...", flush=True)
    coll.flush()
    coll.optimize()
    print(
        f"[zvec-indexer] [{category}] index_completeness: "
        f"{coll.stats.index_completeness}, doc_count: {coll.stats.doc_count}",
        flush=True,
    )
    return total_chunks


def index_changed_files(changed_paths: list, force: bool = False) -> int:
    """Индексировать только изменённые файлы (для git hook).

    Упрощённая версия: помечает файлы для переиндексации.
    При zvec-бэкенде полная переиндексация надёжнее.
    """
    # TODO: инкрементальная индексация для zvec (пока только полная)
    print(f"[zvec-indexer] Incremental indexing not yet supported for zvec. Use --force for full reindex.", flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description='zvec indexer for multilingual-dev-rag')
    parser.add_argument(
        '--category', default='all',
        choices=['all', 'docs', 'code', 'plans'],
        help='Category to index (default: all)',
    )
    parser.add_argument(
        '--force', action='store_true',
        help='Force re-create collections (delete existing data)',
    )
    parser.add_argument(
        '--changed-only', action='store_true',
        help='Index files changed in last commit (git hook mode)',
    )
    args = parser.parse_args()

    print("=" * 60, flush=True)
    print("zvec indexer for multilingual-dev-rag", flush=True)
    print(f"  Backend: zvec + {ZVEC_MODEL} ({ZVEC_EMBED_DIM}d)", flush=True)
    print(f"  DEV_RAG_ROOT: {DEV_RAG_ROOT}", flush=True)
    print(f"  DB path: {ZVEC_DB_PATH}", flush=True)
    print(f"  Category: {args.category}, Force: {args.force}", flush=True)
    print("=" * 60, flush=True)

    if args.changed_only:
        import subprocess
        try:
            out = subprocess.check_output(
                ['git', 'diff', 'HEAD~1', 'HEAD', '--name-only'],
                cwd=DEV_RAG_ROOT, stderr=subprocess.DEVNULL,
            ).decode().splitlines()
        except Exception:
            out = []
        n = index_changed_files(out, force=args.force)
        print(f"Indexed {n} chunks from changed files", flush=True)
        return

    if args.category == 'all':
        categories = list(COLLECTIONS.keys())
    else:
        categories = [args.category]

    grand_total = 0
    grand_t0 = time.time()
    for cat in categories:
        print(flush=True)
        grand_total += index_category(cat, force=args.force)

    grand_elapsed = time.time() - grand_t0
    print(flush=True)
    print("=" * 60, flush=True)
    print(
        f"[zvec-indexer] Grand total: {grand_total} chunks "
        f"in {grand_elapsed:.1f}s",
        flush=True,
    )

    # Ноль чанков — это отказ, а не результат. require_root() ловит только
    # «каталога нет»; корень может существовать и при этом не содержать ничего
    # из того, что описывает профиль — тогда globs молча не совпадают ни с чем,
    # и раньше команда сообщала «Grand total: 0 chunks» с кодом возврата 0.
    # Пустой индекс, полученный «успешно», выглядит как рабочий до первого
    # поиска, который ничего не находит и тоже не жалуется.
    if grand_total == 0:
        print("=" * 60, flush=True)
        print(
            f"[zvec-indexer] ERROR: nothing was indexed.\n"
            f"  DEV_RAG_ROOT:    {DEV_RAG_ROOT}\n"
            f"  DEV_RAG_PROFILE: {DEV_RAG_PROFILE}\n"
            f"  Categories:      {', '.join(categories)}\n"
            f"The root exists, but no file under it matched the profile's glob "
            f"patterns. Either the root points at the wrong repository, or the "
            f"profile describes a different layout. Check the patterns with:\n"
            f"  python -c \"from dev_rag.config import INDEX_PATTERNS; "
            f"print(INDEX_PATTERNS)\"",
            file=sys.stderr, flush=True,
        )
        sys.exit(2)
    print("=" * 60, flush=True)


if __name__ == '__main__':
    main()
