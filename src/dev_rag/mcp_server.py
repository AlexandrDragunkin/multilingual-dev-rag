# -*- coding: utf-8 -*-
"""MCP stdio-сервер — инструмент rag_search для Claude/AI-ассистентов.

Использует роутер searcher, поэтому автоматически работает с любым бэкендом
(zvec или qdrant) в зависимости от RAG_BACKEND.
"""
from __future__ import annotations

import json
import sys
import traceback

from . import __version__
from .searcher import search

TOOLS = [
    {
        'name': 'rag_search',
        'description': (
            'Semantic search over the indexed codebase and documentation. '
            'Use for finding relevant code, API docs, patterns, or architecture decisions. '
            'Returns top matching chunks with file paths.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'query': {
                    'type': 'string',
                    'description': 'What to search for (in Russian or English)',
                },
                'collection': {
                    'type': 'string',
                    'enum': ['docs', 'code', 'plans', 'all'],
                    'default': 'all',
                    'description': 'docs=odocs/, code=.py files, plans=plans/, all=everywhere',
                },
                'n_results': {
                    'type': 'integer',
                    'default': 5,
                    'description': 'Number of results to return',
                },
            },
            'required': ['query'],
        },
    },
]


def _format_results(results: list, n_results: int) -> str:
    """Форматировать результаты поиска в текст для LLM."""
    if not results:
        return 'No results found.'

    parts = []
    for i, r in enumerate(results[:n_results], 1):
        ctx = f' [{r["context"]}]' if r.get('context') else ''
        parts.append(
            f'--- Result {i} (score={r["score"]:.3f}) ---\n'
            f'File: {r["path"]}{ctx}\n\n'
            f'{r["text"]}'
        )
    return '\n\n'.join(parts)


def rag_search(query: str, collection: str = 'all', n_results: int = 5) -> str:
    """Выполнить поиск через роутер (zvec или qdrant)."""
    results = search(query, collection=collection, n=n_results)
    return _format_results(results, n_results)


def handle(request: dict) -> dict | None:
    method = request.get('method', '')
    params = request.get('params', {})

    if method == 'initialize':
        return {
            'result': {
                'protocolVersion': '2024-11-05',
                'capabilities': {'tools': {}},
                'serverInfo': {'name': 'multilingual-dev-rag', 'version': __version__},
            }
        }

    if method == 'notifications/initialized':
        return None

    if method == 'tools/list':
        return {'result': {'tools': TOOLS}}

    if method == 'tools/call':
        tool_name = params.get('name', '')
        args = params.get('arguments', {})
        try:
            if tool_name == 'rag_search':
                text = rag_search(
                    query=args['query'],
                    collection=args.get('collection', 'all'),
                    n_results=int(args.get('n_results', 5)),
                )
                return {'result': {'content': [{'type': 'text', 'text': text}]}}
            return {'error': {'code': -32601, 'message': f'Unknown tool: {tool_name}'}}
        except Exception as e:
            return {'result': {'content': [{'type': 'text', 'text': f'Error: {e}\n{traceback.format_exc()}'}]}}

    return {'error': {'code': -32601, 'message': f'Method not found: {method}'}}


def main():
    """Точка входа MCP-сервера (stdio transport)."""
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stdin.reconfigure(encoding='utf-8')
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        response = handle(request)
        if response is not None and 'id' in request:
            response['id'] = request['id']
            response['jsonrpc'] = '2.0'
            print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
