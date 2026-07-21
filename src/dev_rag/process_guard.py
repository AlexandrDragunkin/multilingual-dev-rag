# -*- coding: utf-8 -*-
"""Автоуборка процессов-дубликатов MCP-сервера при старте нового инстанса.

Проблема (замерено 2026-07-20): MCP-клиент ZCode при переподключении
поднимает новый инстанс `dev_rag.mcp_server`, но не всегда гасит старый —
за день накопилось 6 висящих копий RAG под одним ZCode. Это дубликаты от
переподключений (родитель жив), а не «сироты с мёртвым родителем».

Усугубляет топология Windows venv. Каждый инстанс — это ПАРА процессов::

    ZCode (клиент, без маркера)
      └─ stub  `C:\\VENV310-64\\Scripts\\python.exe -m dev_rag.mcp_server`  [маркер]
           └─ real `C:\\Python310-64\\python.exe -m dev_rag.mcp_server`     [маркер]

`Scripts\\python.exe` — стабиль launcher, который запускает настоящий
интерпретатор как дочерний процесс. Оба несут маркер команды. Когда клиент
убивает стаб, настоящий python остаётся (kill на Windows не каскадируется).
Поэтому:

  * группировка по непосредственному родителю (ppid) НЕ РАБОТАЕТ — у каждого
    real свой stub-родитель, и дубликаты не попадают в одну группу;
  * stub и real одного инстанса — это ОДИН инстанс, их нельзя считать
    взаимными дублями (иначе multi-client убил бы чужой real, убив его stub).

Решение — стратегия newest-wins по ВЛАДЕЛЬЦУ-клиенту:

  * owner процесса — ближайший предок БЕЗ маркера `dev_rag.mcp_server`
    (стаб пропускается при обходе вверх; owner = ZCode / Claude / VSCode);
  * кандидатами на дедуп являются только «листья» — real-процессы (не stub).
    stub исключается как «родитель маркера»; убийство real каскадно гасит
    и его stub (venv-stub ждёт ребёнка и выходит);
  * кандидаты группируются по owner; в каждой группе:
      - owner мёртв      → убить всех (настоящие сироты);
      - owner жив         → выживает самый свежий (max create_time),
                            включая САМОГО СЕБЯ — это устраняет баг, когда
                            одиночный старый дубль выживал рядом с новым;
  * разные живые владельцы друг друга НЕ трогают → multi-client сценарий
    plan 001 (ZCode + Claude Code над одним индексом) сохраняется.

Архитектурно логика разделена:

  * `select_victims()` — ЧИСТАЯ функция отбора. Принимает снимок процессов
    (`_ProcInfo` с предвычисленным полем `owner`) и два колбэка-предиката
    («owner жив?», «этот pid — мой предок?»). Не обращается к ОС. Тестируется
    юнит-тестами без psutil — тот же приём, что в `test_diagnostics.py`
    (мок через аргументы, не через monkeypatch модуля).
  * `cleanup_orphan_siblings()` — точка входа для `mcp_server.main()`: делает
    снимок процессов через psutil, разрешает owner для каждого, применяет
    `select_victims()`. psutil импортируется ЛЕНИВО: при ImportError логирует
    явный warning (принцип проекта «тихий отказ хуже явной ошибки» —
    config.py:14-16), но не падает.

План 003. (Номер 002 уже занят будущим планом по zvec 0.6.0 — см.
`pyproject.toml` «См. plan 002» и `plans/001-*.md`. Не переназначать.)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable

_log = logging.getLogger(__name__)

# Строка-маркер команды нашего MCP-сервера. Иска́ть вхождение в cmdline, а не
# точное равенство: venv-стаб и настоящий интерпретатор дают разные пути
# (`C:\\VENV310-64\\Scripts\\python.exe` vs `C:\\Python310-64\\python.exe`),
# но оба содержат `-m dev_rag.mcp_server`. Совпадение по подстроке также
# надёжно отличает нас от k3-mcp-server и любых чужих python-процессов.
_MCP_MARKER = '-m dev_rag.mcp_server'


@dataclass
class _ProcInfo:
    """Снимок одного процесса для чистой логики.

    `owner` — pid ВЛАДЕЛЬЦА-клиента (ближайший предок без маркера), а НЕ
    непосредственный родитель. На Windows venv real-процесс имеет родителем
    stub; группировать нужно по клиенту (ZCode/Claude/...), поэтому owner
    предвычисляется вызывающим (cleanup_orphan_siblings через psutil). В
    тестах owner задаётся явно — так чистая логика не зависит от ОС.
    """

    pid: int
    ppid: int
    create_time: float
    cmdline: str
    owner: int = 0


def _is_dev_rag_mcp(proc: _ProcInfo) -> bool:
    """Принадлежит ли процесс нашему MCP-серверу (по маркеру команды)."""
    return _MCP_MARKER in (proc.cmdline or '')


def _stub_pids(marked: list[_ProcInfo]) -> set[int]:
    """pid процессов-стабов: маркер-процессов, чей pid является чьим-то ppid.

    stub и real одного инстанса оба несут маркер, но это ОДИН инстанс.
    stub отличаем как «родитель маркер-процесса». Кандидатами на дедуп такие
    stub не являются: убив real, мы каскадно гасим и его stub (venv-stub
    ждёт дочерний процесс и выходит вместе с ним). Исключение stub из
    кандидатов также защищает чужой легитимный инстанс в multi-client схеме:
    иначе в группе [stubЧужой, realЧужой] мы убили бы один из них.
    """
    parent_pids = {p.ppid for p in marked}
    return {p.pid for p in marked if p.pid in parent_pids}


def select_victims(
    procs: list[_ProcInfo],
    my_pid: int,
    owner_alive: Callable[[int], bool],
    is_ancestor_of_mine: Callable[[int], bool],
) -> list[_ProcInfo]:
    """Отобрать процессы к завершению. Чистая функция, без обращения к ОС.

    Аргументы-предикаты вынесены наружу для тестируемости без psutil (приём
    из `test_diagnostics.py`). Возвращает список `_ProcInfo` к убийству,
    отсортированный по pid (детерминированный порядок для assertions).

    Алгоритм:
      1. marked = процессы с маркером команды (`_is_dev_rag_mcp`).
      2. Исключить stub'ы (`_stub_pids`) и моих предков (venv-stab цепочка
         самого себя). СЕБЯ не исключаем — self должен попасть в свою группу,
         чтобы при выборе самого свежего именно он (только что стартовавший)
         выиграл у старых дублей. Без этого одиночный старый дубль выживал бы
         как «новейший в группе из одного» и сосуществовал с новым.
      3. Сгруппировать оставшихся по owner (клиенту).
      4. Для каждой группы:
           - owner мёртв → все в жертвы (сироты), кроме self;
           - owner жив   → выживает max(create_time), остальные — в жертвы,
                           кроме self (self никогда не убиваем).
      5. Разные живые owners не пересекаются → multi-client (ZCode + Claude)
         сохраняется.
    """
    marked = [p for p in procs if _is_dev_rag_mcp(p)]
    stubs = _stub_pids(marked)
    candidates = [
        p
        for p in marked
        if p.pid not in stubs and not is_ancestor_of_mine(p.pid)
    ]

    # Группировка по owner (владельцу-клиенту), не по ppid.
    groups: dict[int, list[_ProcInfo]] = {}
    for p in candidates:
        groups.setdefault(p.owner, []).append(p)

    victims: list[_ProcInfo] = []
    for owner, group in groups.items():
        if not owner_alive(owner):
            # Владелец мёртв — вся группа осиротела. Self сюда попасть не
            # может (у живого процесса owner жив), но проверка pid != my_pid
            # оставлена дляrobustness.
            victims.extend(p for p in group if p.pid != my_pid)
            continue
        newest = max(group, key=lambda p: p.create_time)
        for p in group:
            if p is newest or p.pid == my_pid:
                continue
            victims.append(p)

    victims.sort(key=lambda p: p.pid)
    return victims


def cleanup_orphan_siblings() -> int:
    """Точка входа: просканировать процессы, убить выбранные дубликаты.

    Вызывается из `mcp_server.main()` при старте нового инстанса. Возвращает
    количество завершённых процессов. psutil импортируется ЛЕНИВО: при
    ImportError логирует ЯВНЫЙ warning (не молчит) и возвращает 0. Стиль
    репо (config.py:14-16, plan 001 дефект 2): тихий отказ выглядит как
    рабочий ответ и хуже явной ошибки.
    """
    try:
        import psutil
    except ImportError:
        _log.warning(
            'process_guard: psutil не установлен — автоуборка дубликатов '
            'MCP пропущена. Поставьте psutil (`pip install psutil`), чтобы '
            'включить защиту от накапливающихся процессов dev_rag.mcp_server.'
        )
        return 0

    my_pid = os.getpid()
    me = psutil.Process(my_pid)

    # Снимок всех маркер-процессов (включая stub'ы) одним проходом process_iter.
    raw_snapshot = []
    for raw in psutil.process_iter(
        attrs=['pid', 'ppid', 'create_time', 'cmdline']
    ):
        try:
            info = raw.info
            cmdline = info.get('cmdline')
            cmdline_str = ' '.join(cmdline) if cmdline else ''
            if _MCP_MARKER not in cmdline_str:
                continue
            raw_snapshot.append(
                _ProcInfo(
                    pid=info['pid'],
                    ppid=info.get('ppid', 0),
                    create_time=info.get('create_time') or 0.0,
                    cmdline=cmdline_str,
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
            continue

    marker_pids = {p.pid for p in raw_snapshot}
    stubs = _stub_pids(raw_snapshot)

    # Кэш цепочек предков для разрешения owner. parents() возвращает список
    # от ближайшего предка к корню; первый pid вне marker_pids — это owner
    # (клиент). Зачем нужен обход через stub: real.ppid = stub (маркер), и
    # только выше — клиент. Кэшируем, т.к. stub и его real делят цепочку.
    parents_cache: dict[int, list[int]] = {}

    def _owner_of(pid: int) -> int:
        chain = parents_cache.get(pid)
        if chain is None:
            try:
                chain = [a.pid for a in psutil.Process(pid).parents()]
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                chain = []
            parents_cache[pid] = chain
        for ancestor in chain:
            if ancestor not in marker_pids:
                return ancestor
        return 0  # владелец неразрешим (все предки мертвы) → сирота

    # Сборка финального снимка: для real-процессов вычисляем owner; stub'ы
    # в снимок не включаем — они не кандидаты (см. _stub_pids). Их убийство
    # произойдёт каскадно при завершении их real-ребёнка.
    snapshot: list[_ProcInfo] = []
    for p in raw_snapshot:
        if p.pid in stubs:
            continue
        p.owner = _owner_of(p.pid)
        snapshot.append(p)

    def _owner_alive(owner_pid: int) -> bool:
        # 0/1 (неразрешимый / idle) трактуем как мёртвого владельца → сирота.
        if owner_pid in (0, 1):
            return False
        return psutil.pid_exists(owner_pid)

    def _is_ancestor_of_mine(pid: int) -> bool:
        # Защита от убийства собственного stub'а / цепочки spawn'а. stub
        # обычно уже исключён _stub_pids, но этот предикат закрывает и
        # необычные топологии (marker → shell → marker).
        try:
            return any(a.pid == pid for a in me.parents())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    victims = select_victims(
        snapshot,
        my_pid=my_pid,
        owner_alive=_owner_alive,
        is_ancestor_of_mine=_is_ancestor_of_mine,
    )

    killed = 0
    for v in victims:
        try:
            psutil.Process(v.pid).kill()
            killed += 1
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            _log.warning('process_guard: нет прав завершить pid=%d', v.pid)
        except Exception as e:  # noqa: BLE001 — best-effort, логируем и далее
            _log.warning('process_guard: не удалось завершить pid=%d: %s', v.pid, e)

    if killed:
        _log.info(
            'process_guard: убрано %d старых инстансов dev_rag.mcp_server', killed
        )
    # Файловый лог через DEV_RAG_PROCESS_GUARD_LOG: MCP-клиенты (ZCode, Claude
    # Code) не показывают stderr сервера, поэтому _log.* невидим. Без файла
    # невозможно отличить «уборка не понадобилась» от «уборка сломалась» —
    # тот же класс тихого отказа, с которым борется весь модуль. Env-gated,
    # чтобы не плодить файлы в проде без необходимости; в ARLINE включено для
    # диагностики plan 003.
    log_path = os.environ.get('DEV_RAG_PROCESS_GUARD_LOG')
    if log_path:
        try:
            import time
            with open(log_path, 'a', encoding='utf-8') as f:
                t = time.strftime('%Y-%m-%d %H:%M:%S')
                victims_str = ', '.join(f'pid={v.pid}(owner={v.owner})' for v in victims) or 'none'
                f.write(
                    f'{t} my_pid={my_pid} marked={len(raw_snapshot)} '
                    f'real_candidates={len(snapshot)} killed={killed} victims=[{victims_str}]\n'
                )
        except OSError:
            # Лог недоступен (нет прав, диск полный) — не ронять сервер.
            pass
    return killed
