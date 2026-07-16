# -*- coding: utf-8 -*-
"""Qdrant-индексатор (legacy) — индексация через Qdrant + Ollama.

Используется только при RAG_BACKEND=qdrant.
Для zvec используйте zvec_indexer.py.
"""
from __future__ import annotations

import os
import glob
import hashlib
import argparse

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    Filter, FieldCondition, MatchValue,
)

from .config import (
    QDRANT_URL, EMBED_DIM, DEV_RAG_ROOT,
    COLLECTIONS, INDEX_PATTERNS,
)
from .embedder import embed
from .chunker import chunk_text
from .reader import read_file_text, file_hash

client = QdrantClient(url=QDRANT_URL)


def _ensure_collections():
    existing = {c.name for c in client.get_collections().collections}
    for name in COLLECTIONS.values():
        if name not in existing:
            client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
            )
            print(f'Created collection: {name}')


def _collection_for(path: str) -> str:
    rel = os.path.relpath(path, DEV_RAG_ROOT).replace('\\', '/')
    if rel.startswith('plans/'):
        return COLLECTIONS['plans']
    if rel.endswith('.py') or rel.endswith('.mac'):
        return COLLECTIONS['code']
    return COLLECTIONS['docs']


def index_file(path: str, force: bool = False) -> int:
    abs_path = os.path.abspath(path)
    rel_path = os.path.relpath(abs_path, DEV_RAG_ROOT).replace('\\', '/')
    collection = _collection_for(abs_path)
    fhash = file_hash(abs_path)

    if not force:
        results = client.scroll(
            collection_name=collection,
            scroll_filter=Filter(must=[
                FieldCondition(key='path', match=MatchValue(value=rel_path)),
                FieldCondition(key='hash', match=MatchValue(value=fhash)),
            ]),
            limit=1,
        )
        if results[0]:
            return 0  # уже актуален

    client.delete(
        collection_name=collection,
        points_selector=Filter(must=[
            FieldCondition(key='path', match=MatchValue(value=rel_path))
        ]),
    )

    try:
        text = read_file_text(abs_path)
    except Exception as e:
        print(f'  Skip {rel_path}: {e}')
        return 0

    chunks = list(chunk_text(text, rel_path))
    if not chunks:
        return 0

    points = []
    for i, chunk in enumerate(chunks):
        chunk_id = int(hashlib.md5(f'{rel_path}:{i}'.encode()).hexdigest()[:15], 16)
        vector = embed(chunk['text'])
        points.append(PointStruct(
            id=chunk_id,
            vector=vector,
            payload={
                'path': rel_path,
                'context': chunk['context'],
                'text': chunk['text'],
                'hash': fhash,
                'chunk_index': i,
            },
        ))

    client.upsert(collection_name=collection, points=points)
    return len(chunks)


def index_all(force: bool = False) -> int:
    _ensure_collections()
    total = 0
    for group, patterns in INDEX_PATTERNS.items():
        print(f'\n[{group}]')
        for pattern in patterns:
            for path in glob.glob(os.path.join(DEV_RAG_ROOT, pattern), recursive=True):
                n = index_file(path, force=force)
                if n:
                    rel = os.path.relpath(path, DEV_RAG_ROOT)
                    print(f'  {rel}: {n} chunks')
                    total += n
    print(f'\nTotal: {total} chunks indexed')
    return total


def index_changed_files(changed_paths: list, force: bool = False) -> int:
    _ensure_collections()
    total = 0
    for path in changed_paths:
        abs_path = os.path.join(DEV_RAG_ROOT, path)
        if not os.path.exists(abs_path):
            continue
        if not (path.endswith('.py') or path.endswith('.md') or path.endswith('.mac')):
            continue
        n = index_file(abs_path, force=force)
        if n:
            print(f'  {path}: {n} chunks')
            total += n
    return total


def main():
    parser = argparse.ArgumentParser(description='Qdrant indexer for multilingual-dev-rag')
    parser.add_argument('--all', action='store_true', help='Index all configured files')
    parser.add_argument('--changed-only', action='store_true', help='Index files changed in last commit')
    parser.add_argument('--force', action='store_true', help='Force reindex')
    parser.add_argument('files', nargs='*', help='Specific files to index')
    args = parser.parse_args()

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
        print(f'Indexed {n} chunks from changed files')
    elif args.files:
        _ensure_collections()
        for f in args.files:
            n = index_file(f, force=args.force)
            print(f'{f}: {n} chunks')
    else:
        index_all(force=args.force)


if __name__ == '__main__':
    main()
