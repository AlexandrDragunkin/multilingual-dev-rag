# -*- coding: utf-8 -*-
"""Автоуборка дубликатов MCP: тесты чистой логики `select_victims`.

Без psutil и без обращения к ОС. Функция `select_victims` принимает готовый
снимок процессов (с предвычисленным `owner` — владельцем-клиентом) и два
предиката; мы подаём детерминированные данные и проверяем список жертв.
Это тот же приём, что в `test_diagnostics.py`: мок через аргументы функции,
а не через monkeypatch модуля — тестируется логика, а не побочные эффекты
psutil.

Топологическая модель (Windows venv):
    owner (клиент, без маркера)
      └─ stub  `...\\Scripts\\python.exe -m dev_rag.mcp_server`  [маркер, ppid=owner]
           └─ real `...\\Python310-64\\python.exe -m dev_rag.mcp_server` [маркер, ppid=stub]

stub исключается из кандидатов (`_stub_pids`): убив real, каскадно гасим и
его stub. Группировка идёт по owner (клиенту), а не по ppid — иначе каждый
real попадал бы в свою группу из одного и никто бы не убивался.

Почему без @pytest.mark.integration: движок zvec и реальные процессы здесь
не участвуют. Это проверка чистой функции отбора; интеграционная проверка
убийства живых pid — ручная приёмка после рестарта (см. план 003).
"""
from dev_rag.process_guard import _ProcInfo, select_victims


def _proc(pid, owner, t, ppid=None, cmdline='-m dev_rag.mcp_server'):
    """Собрать _ProcInfo с дефолтным маркером команды нашего MCP.

    ppid по умолчанию = owner (когда в топологии нет отдельного stub'а).
    Для Windows-топологии передавайте ppid=stub_id явно.
    """
    return _ProcInfo(
        pid=pid, ppid=(owner if ppid is None else ppid),
        create_time=t, cmdline=cmdline, owner=owner,
    )


def _live_set(live_owners):
    """owner_alive, отвечающий True только для pid из live_owners."""
    return lambda owner: owner in live_owners


def _no_ancestors(pid):
    """is_ancestor_of_mine, ничего не считающий моим предком."""
    return False


# ---------------------------------------------------------------------------
# Базовые сценарии
# ---------------------------------------------------------------------------

def test_orphan_with_dead_owner_is_killed():
    # Владелец 7812 мёртв → убить. Настоящий сирота.
    procs = [_proc(100, owner=7812, t=10)]
    victims = select_victims(
        procs, my_pid=999, owner_alive=_live_set(set()),
        is_ancestor_of_mine=_no_ancestors,
    )
    assert [v.pid for v in victims] == [100]


def test_old_duplicate_under_live_owner_is_killed():
    # Два real-инстанса под одним живым ZCode (owner=7812): t=10 (старый),
    # my_pid=200, t=20 (новый, только что стартовал). Старый должен умереть,
    # новый выживает. Это и есть вчерашний кейс «дубликаты от переподключений».
    procs = [
        _proc(100, owner=7812, t=10),
        _proc(200, owner=7812, t=20),  # == my_pid
    ]
    victims = select_victims(
        procs, my_pid=200, owner_alive=_live_set({7812}),
        is_ancestor_of_mine=_no_ancestors,
    )
    assert [v.pid for v in victims] == [100]


def test_single_old_orphan_does_not_survive_beside_new():
    # Регрессия на баг 1: одиночный старый дубль НЕ выживает рядом с новым.
    # Раньше self исключался из кандидатов → старый был «новейшим в группе
    # из одного» и выживал. Теперь self участвует в выборе max(create_time),
    # поэтому свежий self выигрывает, старый — жертва.
    procs = [
        _proc(100, owner=7812, t=10),  # одинокий старый дубль
        _proc(999, owner=7812, t=99),  # == my_pid, свежий
    ]
    victims = select_victims(
        procs, my_pid=999, owner_alive=_live_set({7812}),
        is_ancestor_of_mine=_no_ancestors,
    )
    assert [v.pid for v in victims] == [100]


def test_different_live_owners_not_killed():
    # Multi-client сценарий plan 001: ZCode (owner=7812) и Claude
    # (owner=9999) оба живы. Новый инстанс под ZCode НЕ должен трогать
    # инстанс под Claude — оба легитимно работают над одним индексом.
    procs = [
        _proc(100, owner=7812, t=20),   # RAG под ZCode
        _proc(200, owner=9999, t=10),   # RAG под Claude — чужой, но живой
    ]
    victims = select_victims(
        procs, my_pid=999, owner_alive=_live_set({7812, 9999}),
        is_ancestor_of_mine=_no_ancestors,
    )
    assert victims == []


# ---------------------------------------------------------------------------
# Windows venv stub-топология (критично — см. _stub_pids)
# ---------------------------------------------------------------------------

def test_windows_stub_topology_groups_by_owner_not_ppid():
    # Регрессия на баг 2: настоящие дубликаты под одним ZCode, но каждый
    # real имеет РОДИТЕЛЕМ свой stub (Windows venv), а не общий ZCode.
    # Группировка по ppid разнесла бы их в разные группы и никто бы не
    # убивался. Группировка по owner — схлопывает в одну.
    #
    # Топология:
    #   ZCode 7812
    #     ├─ stub 1000 ── real 100 (t=10, СТАРЫЙ)   [my_pid = 200]
    #     ├─ stub 2000 ── real 200 (t=20, НОВЫЙ=self)
    #     └─ stub 3000 ── real 300 (t=15, СТАРЫЙ)
    # Жертвы: real 100, real 300. Выживает: self (real 200).
    # stub'ы 1000/2000/3000 исключены из кандидатов (_stub_pids).
    procs = [
        _proc(1000, owner=7812, t=9,  ppid=7812),   # stub
        _proc(100,  owner=7812, t=10, ppid=1000),   # real старый
        _proc(2000, owner=7812, t=19, ppid=7812),   # stub (self's)
        _proc(200,  owner=7812, t=20, ppid=2000),   # real == my_pid
        _proc(3000, owner=7812, t=14, ppid=7812),   # stub
        _proc(300,  owner=7812, t=15, ppid=3000),   # real старый
    ]
    victims = select_victims(
        procs, my_pid=200, owner_alive=_live_set({7812}),
        is_ancestor_of_mine=_no_ancestors,
    )
    assert [v.pid for v in victims] == [100, 300]


def test_stub_itself_is_never_a_victim():
    # stub несёт маркер, но killing real-ребёнка каскадно гасит stub.
    # Поэтому stub исключается из кандидатов всегда — даже если он «старый».
    procs = [
        _proc(1000, owner=7812, t=1,  ppid=7812),   # старый stub
        _proc(100,  owner=7812, t=10, ppid=1000),   # его real
        _proc(999,  owner=7812, t=20),              # self (без stub в снимке)
    ]
    victims = select_victims(
        procs, my_pid=999, owner_alive=_live_set({7812}),
        is_ancestor_of_mine=_no_ancestors,
    )
    assert 1000 not in [v.pid for v in victims]
    assert [v.pid for v in victims] == [100]


def test_own_venv_stub_not_killed():
    # Мой собственный stub — мой предок. Даже если бы он не был отфильтрован
    # _stub_pids, is_ancestor_of_mine должен его защитить (защита от
    # нестандартных топологий marker→shell→marker).
    procs = [
        _proc(50, owner=4, t=0, ppid=4),    # мой stub-предок
        _proc(100, owner=4, t=10, ppid=50), # my_pid
    ]
    victims = select_victims(
        procs, my_pid=100, owner_alive=_live_set({4}),
        is_ancestor_of_mine=lambda pid: pid == 50,
    )
    assert victims == []


# ---------------------------------------------------------------------------
# Прочее
# ---------------------------------------------------------------------------

def test_non_dev_rag_python_not_touched():
    # k3-mcp-server и прочие python-процессы — НЕ наш маркер, не трогаются.
    # Подтверждает scope плана: только RAG, даже при общем владельце.
    procs = [
        _proc(100, owner=7812, t=10, cmdline='C:/REPO/ARLINE/k3-mcp-server/server.py'),
        _proc(200, owner=7812, t=20, cmdline='some unrelated python'),
    ]
    victims = select_victims(
        procs, my_pid=999, owner_alive=_live_set({7812}),
        is_ancestor_of_mine=_no_ancestors,
    )
    assert victims == []


def test_empty_snapshot_returns_empty():
    victims = select_victims(
        [], my_pid=999, owner_alive=_live_set({7812}),
        is_ancestor_of_mine=_no_ancestors,
    )
    assert victims == []


def test_import_error_is_explicit_warning(monkeypatch, caplog):
    # psutil нет → cleanup_orphan_siblings логирует ЯВНЫЙ warning (не молчит),
    # возвращает 0, не падает. Принцип «тихий отказ хуже явной ошибки»
    # (config.py:14-16, plan 001 дефект 2): защита должна громко сказать,
    # что она пропущена, а не создавать иллюзию работающей уборки.
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

    # Подменяем select_victims пустым списком, чтобы не зависеть от ОС.
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
    # Файл не должен создаваться. Проверяем по отсутствию новых файлов в tmp.
    assert list(tmp_path.iterdir()) == []
