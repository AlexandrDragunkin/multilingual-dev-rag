# -*- coding: utf-8 -*-
"""Разбиение текста на чанки с учётом типа файла."""
from __future__ import annotations

import re
from typing import Generator

from .config import CHUNK_SIZE, CHUNK_OVERLAP


def chunk_text(text: str, path: str) -> Generator[dict, None, None]:
    """Разбить текст на перекрывающиеся чанки с метаданными.

    Стратегия зависит от расширения:
      .md  — по секциям заголовков (##/###)
      .mac — по блокам (двойной перевод строки)
      .py  — по границам class/def
    """
    if path.endswith('.md'):
        sections = re.split(r'\n(?=#{1,3} )', text)
        for section in sections:
            if not section.strip():
                continue
            title_match = re.match(r'#{1,3} (.+)', section)
            section_title = title_match.group(1) if title_match else ''
            yield from _sliding_window(section, path, section_title)
    elif path.endswith('.mac') or path.endswith('.MAC'):
        blocks = re.split(r'\n{2,}', text)
        for block in blocks:
            if not block.strip():
                continue
            name_match = re.match(r'(\w+)\s*\(', block.lstrip())
            block_name = name_match.group(1) if name_match else ''
            yield from _sliding_window(block, path, block_name)
    else:
        # Python: split by class/def boundaries
        blocks = re.split(r'\n(?=(?:class |def |\Z))', text)
        for block in blocks:
            if not block.strip():
                continue
            name_match = re.match(r'(?:class|def)\s+(\w+)', block.lstrip())
            block_name = name_match.group(1) if name_match else ''
            yield from _sliding_window(block, path, block_name)


def _sliding_window(text: str, path: str, context: str) -> Generator[dict, None, None]:
    """Скользящее окно по тексту с перекрытием."""
    start = 0
    idx = 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        chunk = text[start:end]
        if chunk.strip():
            yield {
                'text': chunk,
                'path': path,
                'context': context or '',
                'chunk_idx': idx,
            }
            idx += 1
        if end >= len(text):
            break
        start += CHUNK_SIZE - CHUNK_OVERLAP
