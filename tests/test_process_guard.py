# -*- coding: utf-8 -*-
"""Автоуборка дубликатов MCP: тесты чистой логики `select_victims`.

Без psutil и без обращения к ОС. Функция `select_victims` принимает готовый
снимок процессов (с предвычисленным `ancestors` — множеством pid всех
предков) и предикат «pid жив?». Тестируется детерминированными данными.
Приём из `test_diagnostics.py`: мок через аргументы функции, не через
monkeypatch модуля — тестируется логика, а не побочные эффекты psutil.

Модель группировки: два real-инстанса в одной группе, если их множества
`ancestors` пересекаются (есть общий живой предок). Это покрывает
многослойную топологию:

    GUI-клиент (VSCode)
      ├─ launcher1 (claude.exe) ── RAG real A    ┐ общий предок VSCode
      └─ launcher2 (claude.exe) ── RAG real B    ┘ → одна группа → дубль
      └─ stub (venv) ── RAG real C               ← общий предок stub → дубль

Real без общих предков с другими (multi-client: ZCode vs Claude) не трогаются.

Почему без @pytest.mark.integration: движок zvec и реальные процессы не
участвуют. Интеграция убийства живых pid — ручная приёмка после рестарта
(см. план 003, раздел 6 handoff-003.md).
"""
from dev_rag.process_guard import _ProcInfo, select_victims


def _proc(pid, ancestors, t, ppid=0, cmdline='-m dev_rag.mcp_server'):
    """Собрать _ProcInfo с маркером команды нашего MCP.

    ancestors — множество pid всех предков (включая промежуточные launchers
    и stub'ы). Группировка идёт по пересечению этих множеств.
    """
    return _ProcInfo(
        pid=pid, ppid=ppid, create_time=t,
        cmdline=cmdline, ancestors=set(ancestors),
    )


def _live_set(live_pids):
    """pid_alive, отвечающий True только для pid из live_pids."""
    return lambda pid: pid in live_pids


def _no_ancestors(pid):
    """is_ancestor_of_mine, ничего не считающий моим предком."""
    return False


# ---------------------------------------------------------------------------
# Базовые сценарии
# ---------------------------------------------------------------------------

def test_orphan_with_all_dead_ancestors_is_killed():
    # Все предки мертвы → сирота → убить.
    procs = [_proc(100, ancestors={7812}, t=10)]
    victims = select_victims(
        procs, my_pid=999, pid_alive=_live_set(set()),
        is_ancestor_of_mine=_no_ancestors,
    )
    assert [v.pid for v in victims] == [100]


def test_old_duplicate_with_common_ancestor_is_killed():
    # Два real под одним живым ZCode (общий предок 7812): t=10 (старый),
    # my_pid=200 (свежий). Старый должен умереть. Это базовый кейс
    # «дубликаты от переподключений».
    procs = [
        _proc(100, ancestors={7812}, t=10),
        _proc(200, ancestors={7812}, t=20),  # == my_pid
    ]
    victims = select_victims(
        procs, my_pid=200, pid_alive=_live_set({7812}),
        is_ancestor_of_mine=_no_ancestors,
    )
    assert [v.pid for v in victims] == [100]


def test_single_old_orphan_does_not_survive_beside_new():
    # Регрессия: одиночный старый дубль НЕ выживает рядом с новым. Self
    # участвует в группе и выигрывает как самый свежий.
    procs = [
        _proc(100, ancestors={7812}, t=10),
        _proc(999, ancestors={7812}, t=99),  # == my_pid
    ]
    victims = select_victims(
        procs, my_pid=999, pid_alive=_live_set({7812}),
        is_ancestor_of_mine=_no_ancestors,
    )
    assert [v.pid for v in victims] == [100]


def test_different_clients_no_common_ancestor_not_killed():
    # Multi-client сценарий plan 001: ZCode (предок 7812) и Claude (предок
    # 9999) — общих предков нет. Новый инстанс под ZCode не трогает инстанс
    # под Claude. Оба легитимно работают над одним индексом.
    procs = [
        _proc(100, ancestors={7812}, t=20),
        _proc(200, ancestors={9999}, t=10),
    ]
    victims = select_victims(
        procs, my_pid=999, pid_alive=_live_set({7812, 9999}),
        is_ancestor_of_mine=_no_ancestors,
    )
    assert victims == []


def test_root_process_not_in_ancestors_isolation():
    # Регрессия бага «корень в ancestors»: два РАЗНЫХ GUI-клиента (ZCode,
    # VSCode) разделяют в реальности корневой explorer.exe (40088), но он
    # ДОЛЖЕН быть отсечён _ROOT_PROCESS_NAMES в cleanup_orphan_siblings.
    # Тест подаёт ancestors УЖЕ без корня (как это сделает cleanup) и
    # проверяет: клиенты остаются в разных группах, multi-client сохранён.
    procs = [
        # RAG под ZCode: ancestors [ZCode=7812] (explorer 40088 отсечён)
        _proc(100, ancestors={7812}, t=20),
        # RAG под VSCode: ancestors [Code=16600] (explorer 40088 отсечён)
        _proc(200, ancestors={16600}, t=10),
    ]
    victims = select_victims(
        procs, my_pid=999, pid_alive=_live_set({7812, 16600}),
        is_ancestor_of_mine=_no_ancestors,
    )
    assert victims == []


# ---------------------------------------------------------------------------
# Многослойная топология: launcher / stub между клиентом и RAG
# ---------------------------------------------------------------------------

def test_two_claude_sessions_in_one_vscode_are_duplicates():
    # РЕГРЕССИЯ ключевого бага: VSCode (16600) породил два claude.exe
    # (29444 и 39784), каждый поднял свой RAG. Ближайшие предки разные
    # (claude.exe pid), но общий ЖИВОЙ предок VSCode есть → одна группа →
    # старый убивается. Это выявлено на живой системе 2026-07-21.
    procs = [
        # real A: ancestors = [claude1=29444, VSCode=16600, explorer=40088]
        _proc(48504, ancestors={29444, 16600, 40088}, t=100),
        # real B: ancestors = [claude2=39784, VSCode=16600, explorer=40088]
        _proc(40556, ancestors={39784, 16600, 40088}, t=200),  # my_pid
    ]
    victims = select_victims(
        procs, my_pid=40556, pid_alive=_live_set({29444, 39784, 16600, 40088}),
        is_ancestor_of_mine=_no_ancestors,
    )
    assert [v.pid for v in victims] == [48504]


def test_windows_stub_topology_groups_by_common_ancestor():
    # Windows venv: каждый real имеет РОДИТЕЛЕМ свой stub, но все три real
    # под одним ZCode имеют общий живой предок ZCode (7812) → одна группа.
    # Раньше (баг 2) группировка по ppid разнесла бы их в разные группы.
    # stub'ы 1000/2000/3000 — исключены из кандидатов.
    procs = [
        _proc(1000, ancestors={7812}, t=9,  ppid=7812),  # stub
        _proc(100,  ancestors={1000, 7812}, t=10, ppid=1000),
        _proc(2000, ancestors={7812}, t=19, ppid=7812),  # stub (self's)
        _proc(200,  ancestors={2000, 7812}, t=20, ppid=2000),  # == my_pid
        _proc(3000, ancestors={7812}, t=14, ppid=7812),  # stub
        _proc(300,  ancestors={3000, 7812}, t=15, ppid=3000),
    ]
    victims = select_victims(
        procs, my_pid=200, pid_alive=_live_set({7812, 1000, 2000, 3000}),
        is_ancestor_of_mine=_no_ancestors,
    )
    assert [v.pid for v in victims] == [100, 300]


def test_transitive_grouping_three_instances_one_client():
    # Транзитивность: A~B (общий X), B~C (общий Y), A и C общих предков не
    # имеют. Все трое должны попасть в одну группу через B. Свежий выживает,
    # два старых — жертвы. Без union-find эта цепочка разорвалась бы.
    procs = [
        _proc(100, ancestors={500}, t=10),            # A: общий с B через 500
        _proc(200, ancestors={500, 600}, t=15),       # B: мост
        _proc(300, ancestors={600}, t=20),            # == my_pid, C
    ]
    victims = select_victims(
        procs, my_pid=300, pid_alive=_live_set({500, 600}),
        is_ancestor_of_mine=_no_ancestors,
    )
    assert [v.pid for v in victims] == [100, 200]


def test_stub_itself_is_never_a_victim():
    # stub несёт маркер, но killing real-ребёнка каскадно гасит stub.
    # Поэтому stub исключается из кандидатов всегда — даже «старый».
    procs = [
        _proc(1000, ancestors={7812}, t=1,  ppid=7812),   # старый stub
        _proc(100,  ancestors={1000, 7812}, t=10, ppid=1000),
        _proc(999,  ancestors={7812}, t=20),              # self
    ]
    victims = select_victims(
        procs, my_pid=999, pid_alive=_live_set({7812, 1000}),
        is_ancestor_of_mine=_no_ancestors,
    )
    assert 1000 not in [v.pid for v in victims]
    assert [v.pid for v in victims] == [100]


def test_own_venv_stub_not_killed():
    # Мой собственный stub — мой предок. Даже если бы он не был отфильтрован
    # _stub_pids, is_ancestor_of_mine должен его защитить.
    procs = [
        _proc(50, ancestors={4}, t=0, ppid=4),    # мой stub-предок
        _proc(100, ancestors={50, 4}, t=10, ppid=50),  # my_pid
    ]
    victims = select_victims(
        procs, my_pid=100, pid_alive=_live_set({4, 50}),
        is_ancestor_of_mine=lambda pid: pid == 50,
    )
    assert victims == []


def test_orphan_with_one_dead_one_live_ancestor_not_orphan():
    # Предок частично мёртв: 7812 жив, 999 мёртв. Достаточно одного живого
    # предка, чтобы не считаться сиротой.
    procs = [_proc(100, ancestors={7812, 999}, t=10)]
    victims = select_victims(
        procs, my_pid=999, pid_alive=_live_set({7812}),
        is_ancestor_of_mine=_no_ancestors,
    )
    assert victims == []


# ---------------------------------------------------------------------------
# Прочее
# ---------------------------------------------------------------------------

def test_non_dev_rag_python_not_touched():
    # k3-mcp-server и чужие python-процессы — НЕ наш маркер, не трогаются.
    procs = [
        _proc(100, ancestors={7812}, t=10, cmdline='C:/REPO/ARLINE/k3-mcp-server/server.py'),
        _proc(200, ancestors={7812}, t=20, cmdline='some unrelated python'),
    ]
    victims = select_victims(
        procs, my_pid=999, pid_alive=_live_set({7812}),
        is_ancestor_of_mine=_no_ancestors,
    )
    assert victims == []


def test_empty_snapshot_returns_empty():
    victims = select_victims(
        [], my_pid=999, pid_alive=_live_set({7812}),
        is_ancestor_of_mine=_no_ancestors,
    )
    assert victims == []


def test_import_error_is_explicit_warning(monkeypatch, caplog):
    # psutil нет → cleanup_orphan_siblings логирует ЯВНЫЙ warning (не молчит),
    # возвращает 0, не падает. Принцип «тихий отказ хуже явной ошибки»
    # (config.py:14-16, plan 001 дефект 2).
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == 'psutil':
            raise ImportError('No module named psutil')
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', _fake_import)

    from dev_rag.process_guard import cleanup_orphan_siblings

    with caplog.at_level('WARNING'):
        killed = cleanup_orphan_siblings()

    assert killed == 0
    assert any('psutil' in rec.message and 'уборка' in rec.message.lower()
               for rec in caplog.records), [rec.message for rec in caplog.records]


def test_file_log_written_when_env_set(monkeypatch, tmp_path):
    # DEV_RAG_PROCESS_GUARD_LOG задан → cleanup пишет строку с my_pid/killed/
    # victims. MCP-клиенты не показывают stderr, поэтому без файла невозможно
    # отличить «уборка не понадобилась» от «уборка сломалась».
    log_file = tmp_path / 'guard.log'
    monkeypatch.setenv('DEV_RAG_PROCESS_GUARD_LOG', str(log_file))
    import dev_rag.process_guard as pg
    monkeypatch.setattr(pg, 'select_victims', lambda *a, **k: [])

    from dev_rag.process_guard import cleanup_orphan_siblings

    killed = cleanup_orphan_siblings()
    assert killed == 0
    assert log_file.exists()
    content = log_file.read_text(encoding='utf-8')
    assert 'my_pid=' in content
    assert 'killed=0' in content
    assert 'victims=[none]' in content


def test_file_log_skipped_when_env_unset(monkeypatch, tmp_path):
    # Без DEV_RAG_PROCESS_GUARD_LOG файл не создаётся — прод не замусоривается.
    monkeypatch.delenv('DEV_RAG_PROCESS_GUARD_LOG', raising=False)
    import dev_rag.process_guard as pg
    monkeypatch.setattr(pg, 'select_victims', lambda *a, **k: [])

    from dev_rag.process_guard import cleanup_orphan_siblings

    cleanup_orphan_siblings()
    assert list(tmp_path.iterdir()) == []
