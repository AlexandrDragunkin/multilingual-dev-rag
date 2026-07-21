# -*- coding: utf-8 -*-
r"""Автоуборка процессов-дубликатов MCP-сервера при старте нового инстанса.

Проблема (замерено 2026-07-20): MCP-клиенты при переподключении / открытии
новой сессии поднимают новый инстанс `dev_rag.mcp_server`, не гася старый —
за день накопилось 6 висящих копий RAG. Это дубликаты (родитель жив), а не
«сироты с мёртвым родителем»: простая проверка «родитель мёртв → убить» их
не поймает.

Топология инстансов многослойная, и каждый слой может плодить дубликаты::

    GUI-клиент (VSCode / ZCode)
      └─ launcher 1 (claude.exe)        ← новая сессия Claude в VSCode
      │     └─ RAG real  (маркер)
      └─ launcher 2 (claude.exe)        ← другая сессия Claude в том же VSCode
      │     └─ RAG real  (маркер)       ← ДУБЛЬ под тем же VSCode
      └─ stub (venv Scripts\python.exe) ← Windows venv launcher
            └─ RAG real  (маркер)

На Windows venv `Scripts\\python.exe` (стаб) запускает настоящий интерпретатор
как дочерний процесс; kill на Windows не каскадируется → при закрытии клиента
остаётся real. Аналогично VSCode порождает новый `claude.exe` на каждую
сессию Claude Code, и каждый поднимает свой RAG. Поэтому «владелец» — не
ближайший предок (claude.exe / venv-stub — промежуточные звенья), а ЛЮБОЙ
общий живой предок в цепочке.

Решение — стратегия newest-wins по общему предку:

  * для каждого real-кандидата строим множество всех живых предков (pid),
    обрывая обход на корневых процессах (explorer/systemd/...);
  * два real попадают в одну группу, если их множества ПЕРЕСЕКАЮТСЯ — это
    доказывает «порождены одним клиентом» (каким именно — не важно);
  * в группе: owner-предок(и) мертвы → все в жертвы (сироты); иначе выживает
    самый свежий (max create_time), остальные — в жертвы (дубликаты);
  * real без общих предков с другими (multi-client: ZCode vs Claude Code)
    НЕ трогаются → сохраняет сценарий plan 001.

Граница «один клиент» = общий GUI-предок (VSCode, ZCode, отдельный
терминал). Два consumer'а RAG под одним VSCode (напр. Claude extension +
забытый task в терминале) схлопываются как дубликаты — на практике это
всегда артефакт переподключения, а не два нужных одновременно consumer'а.
RO-lock'и zvec разделяемые (plan 001), поэтому два параллельных поиска в
один индекс безопасны, и для пользователя разница незаметна.

Архитектурно логика разделена:

  * `select_victims()` — ЧИСТАЯ функция. Принимает снимок процессов
    (`_ProcInfo` с предвычисленным `ancestors`) и предикат «pid жив?».
    Не обращается к ОС. Тестируется без psutil — тот же приём, что в
    `test_diagnostics.py`.
  * `cleanup_orphan_siblings()` — точка входа для `mcp_server.main()`: снимок
    процессов через psutil, сбор ancestors, применение `select_victims()`,
    kill выбранных. psutil импортируется ЛЕНИВО; при ImportError — явный
    warning (принцип проекта «тихий отказ хуже явной ошибки», config.py:14-16),
    но не падает. Опциональный файловый лог через DEV_RAG_PROCESS_GUARD_LOG
    (MCP-клиенты не показывают stderr сервера).

План 003. (Номер 002 уже занят будущим планом по zvec 0.6.0 — см.
`pyproject.toml` «См. plan 002» и `plans/001-*.md`. Не переназначать.)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Callable

_log = logging.getLogger(__name__)

# Строка-маркер команды нашего MCP-сервера. Иска́ть вхождение в cmdline, а не
# точное равенство: venv-стаб и настоящий интерпретатор дают разные пути
# (`C:\\VENV310-64\\Scripts\\python.exe` vs `C:\\Python310-64\\python.exe`),
# но оба содержат `-m dev_rag.mcp_server`. Совпадение по подстроке также
# надёжно отличает нас от k3-mcp-server и любых чужих python-процессов.
_MCP_MARKER = '-m dev_rag.mcp_server'

# Корневые процессы дерева — их предки общие для ВСЕХ процессов на машине
# (explorer.exe на Windows — родитель любого GUI-приложения, запущенного с
# рабочего стола; systemd/init/launchd — на Unix). Если включать их в
# `ancestors`, union-find схлопнет в одну группу вообще все RAG-инстансы,
# включая чужие клиенты (VSCode + ZCode), и защита убьёт легитимный
# multi-client сценарий plan 001. Поэтому обход предков обрываем ПРИ
# встрече с таким процессом: сам корень в ancestors НЕ попадает (он —
# граница, а не маркер родства), но его прямые потомки (Code.exe, ZCode.exe)
# попадают и служат общим предком для действительно родственных инстансов.
# Баг «корень в ancestors» был выявлен dry-run 2026-07-21: 4 RAG-инстанса
# под разными клиентами схлопывались в одну группу через explorer.exe.
# Стоп-список маленький и стабильный; имена — в нижнем регистре.
_ROOT_PROCESS_NAMES = frozenset({
    # Windows
    'explorer.exe', 'services.exe', 'svchost.exe', 'lsass.exe', 'wininit.exe',
    'smss.exe', 'csrss.exe',
    # Unix
    'init', 'systemd', 'launchd', 'kthreadd',
    # macOS GUI root
    'finder',
})


@dataclass
class _ProcInfo:
    """Снимок одного процесса для чистой логики.

    `ancestors` — pid ВСЕХ живых предков (полный путь от родителя до корня),
    НЕ ближайший. Множество, не одно значение: группировка по пересечению
    множеств покрывает многослойную топологию (venv-stab + launcher-клиент),
    где у двух дублей нет одного общего «ближайшего» предка, но есть общий
    предок где-то выше. Предвычисляется вызывающим; в тестах задаётся явно,
    так что чистая логика не зависит от ОС.
    """

    pid: int
    ppid: int
    create_time: float
    cmdline: str
    ancestors: set = field(default_factory=set)


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


def _is_orphan(proc: _ProcInfo, pid_alive: Callable[[int], bool]) -> bool:
    """Сирота: ни один предок не жив. Владелец неразрешим → подлежит убийству."""
    return not any(pid_alive(a) for a in proc.ancestors)


def select_victims(
    procs: list[_ProcInfo],
    my_pid: int,
    pid_alive: Callable[[int], bool],
    is_ancestor_of_mine: Callable[[int], bool],
) -> list[_ProcInfo]:
    """Отобрать процессы к завершению. Чистая функция, без обращения к ОС.

    Аргументы-предикаты вынесены наружу для тестируемости без psutil (приём
    из `test_diagnostics.py`). Возвращает список `_ProcInfo` к убийству,
    отсортированный по pid (детерминированный порядок для assertions).

    Алгоритм:
      1. marked = процессы с маркером команды.
      2. Исключить stub'ы (`_stub_pids`) и моих предков (защита от убийства
         собственного stub'а / цепочки spawn'а). СЕБЯ НЕ исключаем: self
         должен попасть в свою группу, чтобы при выборе самого свежего именно
         он (только что стартовавший) выиграл у старых дублей. Без этого
         одиночный старый дубль выживал бы как «новейший в группе из одного».
      3. Сироты (нет живых предков) → в жертвы (кроме self).
      4. Из оставшихся построить группы связностью: два real в одной группе,
         если их множества ancestors пересекаются (общий живой предок).
         Транзитивное замыкание: если A~B и B~C, то все трое в одной группе.
      5. В каждой группе выживает max(create_time); self всегда выживает;
         остальные — в жертвы.
      6. Real без общих предков с другими (multi-client) не трогаются.
    """
    marked = [p for p in procs if _is_dev_rag_mcp(p)]
    stubs = _stub_pids(marked)
    candidates = [
        p
        for p in marked
        if p.pid not in stubs and not is_ancestor_of_mine(p.pid)
    ]

    victims: list[_ProcInfo] = []

    # 3. Сироты — отдельным проходом: им группировка не нужна.
    non_orphans: list[_ProcInfo] = []
    for p in candidates:
        if p.pid == my_pid:
            non_orphans.append(p)  # self никогда не жертва, но участвует в группе
            continue
        if _is_orphan(p, pid_alive):
            victims.append(p)
        else:
            non_orphans.append(p)

    # 4. Группы по связности ancestors (транзитивное замыкание через union).
    #    O(n²) при малом n (число RAG-инстансов ≪ 100) — достаточно.
    n = len(non_orphans)
    parent = list(range(n))  # union-find: индекс в non_orphans

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(n):
        for j in range(i + 1, n):
            if non_orphans[i].ancestors & non_orphans[j].ancestors:
                union(i, j)

    groups: dict[int, list[_ProcInfo]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(non_orphans[i])

    # 5. В каждой группе выживает самый свежий, self всегда выживает.
    for group in groups.values():
        if len(group) <= 1:
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

    # Снимок всех маркер-процессов одним проходом process_iter.
    raw_snapshot: list[_ProcInfo] = []
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

    stubs = _stub_pids(raw_snapshot)

    # Кэш живых предков для real-кандидатов. parents() возвращает список от
    # ближайшего к корню. Обход обрываем ПРИ встрече с процессом из
    # _ROOT_PROCESS_NAMES (explorer/systemd/...): сам корень в ancestors
    # попадает (общий для родственных инстансов под одним GUI-клиентом), но
    # выше не поднимаемся — иначе все RAG на машине схлопнутся в одну
    # группу через глобальные корни (баг, выявленный dry-run 2026-07-21).
    # Фильтровать живых будем позже, через pid_alive, чтобы не дёргать ОС
    # повторно (процесс мог умереть между snapshots).
    def _ancestors_of(pid: int) -> set[int]:
        try:
            result: set[int] = set()
            for a in psutil.Process(pid).parents():
                # Корневой процесс (explorer/systemd/...) обрывает обход и
                # САМ В ancestors НЕ ПОПАДАЕТ. Иначе он становится общим для
                # вообще всех процессов на машине, и union-find схлопывает в
                # одну группу даже чужие клиенты (VSCode + ZCode) — убивая
                # multi-client сценарий plan 001. Баг выявлен dry-run
                # 2026-07-21: при включении корня в ancestors все 4 RAG
                # оказывались в одной группе.
                try:
                    if a.name().lower() in _ROOT_PROCESS_NAMES:
                        break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    break
                result.add(a.pid)
            return result
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return set()

    # Собираем финальный снимок: для real-процессов (не stub) вычисляем
    # ancestors. stub'ы исключаем — они не кандидаты (см. _stub_pids).
    snapshot: list[_ProcInfo] = []
    for p in raw_snapshot:
        if p.pid in stubs:
            continue
        p.ancestors = _ancestors_of(p.pid)
        snapshot.append(p)

    def _pid_alive(pid: int) -> bool:
        if pid in (0, 1):
            return False
        return psutil.pid_exists(pid)

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
        pid_alive=_pid_alive,
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
    # чтобы не плодить файлы в проде без необходимости.
    log_path = os.environ.get('DEV_RAG_PROCESS_GUARD_LOG')
    if log_path:
        try:
            import time
            with open(log_path, 'a', encoding='utf-8') as f:
                t = time.strftime('%Y-%m-%d %H:%M:%S')
                victims_str = (
                    ', '.join(f'pid={v.pid}' for v in victims) or 'none'
                )
                f.write(
                    f'{t} my_pid={my_pid} marked={len(raw_snapshot)} '
                    f'real_candidates={len(snapshot)} killed={killed} '
                    f'victims=[{victims_str}]\n'
                )
        except OSError:
            # Лог недоступен (нет прав, диск полный) — не ронять сервер.
            pass
    return killed
