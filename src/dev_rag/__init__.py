# -*- coding: utf-8 -*-
"""multilingual-dev-rag — local-first RAG over source code and technical docs.

    from dev_rag import search
    results = search('ферма воркеров', collection='docs', n=5)

Set `DEV_RAG_ROOT` to the repository you want to index, and `DEV_RAG_PROFILE`
to a profile describing which files count. See `dev_rag.config`.
"""
from .config import DEV_RAG_PROFILE, DEV_RAG_ROOT, RAG_BACKEND
from .searcher import search

__version__ = '0.1.0rc1'

__all__ = [
    'search',
    'RAG_BACKEND',
    'DEV_RAG_ROOT',
    'DEV_RAG_PROFILE',
    '__version__',
]
