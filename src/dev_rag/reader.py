# -*- coding: utf-8 -*-
"""Чтение файлов с автоопределением кодировки (UTF-8 / CP1251)."""
from __future__ import annotations

import hashlib
from pathlib import Path


def read_file_text(path: str) -> str:
    """Прочитать файл, автоматически определив кодировку.

    Порядок: BOM UTF-8 → UTF-8 strict → CP1251 (с replace).
    """
    raw = Path(path).read_bytes()
    # BOM UTF-8
    if raw.startswith(b'\xef\xbb\xbf'):
        return raw[3:].decode('utf-8', errors='replace')
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('cp1251', errors='replace')


def file_hash(path: str) -> str:
    """MD5-хэш содержимого файла (для отслеживания изменений)."""
    with open(path, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()
