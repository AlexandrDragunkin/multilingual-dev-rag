# -*- coding: utf-8 -*-
"""CLI entry points for multilingual-dev-rag.

    dev-rag-index   — build or rebuild the index
    dev-rag-search  — search from the shell
"""
from __future__ import annotations

import argparse
import sys

from .config import RAG_BACKEND

COLLECTION_CHOICES = ('all', 'docs', 'code', 'plans')


def main():
    """Entry point for dev-rag-index. Routes to the configured backend."""
    if RAG_BACKEND == 'zvec':
        from .zvec_indexer import main as _zvec_main
        _zvec_main()
    else:
        from .indexer import main as _qdrant_main
        _qdrant_main()


def search_main():
    """Entry point for dev-rag-search."""
    parser = argparse.ArgumentParser(
        prog='dev-rag-search',
        description='Search the indexed corpus (hybrid vector + full-text).',
    )
    parser.add_argument('query', help='what to search for')
    # Restricted to the known collections on purpose: an unknown name used to
    # be swallowed silently — the lookup failed, the exception was caught, and
    # the command printed "No results found" as if the corpus had no match.
    parser.add_argument(
        '-c', '--collection',
        choices=COLLECTION_CHOICES,
        default='all',
        help='which collection to search (default: all)',
    )
    parser.add_argument(
        '-n', '--num-results',
        type=int,
        default=5,
        metavar='N',
        help='how many results to return (default: 5)',
    )
    parser.add_argument(
        '--paths-only',
        action='store_true',
        help='print only scores and paths, without the matching text',
    )
    args = parser.parse_args()

    from .searcher import search

    results = search(args.query, collection=args.collection, n=args.num_results)
    if not results:
        print('No results found.')
        sys.exit(1)

    for i, r in enumerate(results, 1):
        ctx = f' [{r["context"]}]' if r.get('context') else ''
        if args.paths_only:
            print(f'{r["score"]:.4f}  {r["path"]}{ctx}')
            continue
        print(f'--- Result {i} (score={r["score"]:.4f}) ---')
        print(f'File: {r["path"]}{ctx}')
        print(r['text'][:300])
        print()
