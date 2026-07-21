# Хендаут: multilingual-dev-rag, план 003 — для ревью и приёмки

> Контекст для подхвата. Реализация готова в ветке `feat/process-guard-003`,
> локально (не запушена). Этот промпт — чтобы ревьюер/другая сессия могли
> проверить, доработать или принять.

---

## 0. Если ты — принимающая модель / ревьюер, прочти это первым

- Это **хендаут (handoff)** по **плану 003** (автоуборка процессов-дубликатов
  MCP). Реализация завершена, ожидает ревью и ручной приёмки на живой системе.
- Репозиторий: `C:\REPO\multilingual-dev-rag`, ветка **`feat/process-guard-003`**
  (от `main` @ `b472078`).
- **Не путать с планом 002** — номер 002 уже занят будущим планом по zvec 0.6.0
  (см. `pyproject.toml` «См. plan 002», `plans/001-*.md:5,35`). Мой план — 003.
- Пуш/PR/merge — **по явной команде автора**, пока ветка локальная.
- **Проверено на живой системе** после рестарта ZCode (см. раздел 6b): dry-run
  на 3 легитимных клиентах (ZCode + Claude Code + VSCode task) даёт **0 жертв**
  — multi-client сценарий plan 001 сохранён. Стресс-тест на синтетических
  дубликатах не выполнялся: `dev_rag.mcp_server` без клиента, держащего stdin,
  жить не может, поэтому реальная защита проверена только unit-тестами.
- **Баг 5 исправлен в этой сессии (2026-07-21)**: при стартовом dry-run
  выявлено, что `select_victims` убивал дубли в ЧУЖОМ клиенте при старте
  инстанса под своим. Зафиксирован регрессионным тестом и исправлен
  (коммит `767ff65`). Сюита теперь **87 passed** (16+1 в `test_process_guard`).
  См. раздел 2b (баг 5) и 6b (обновлённый dry-run).

---

## 1. Задача одной фразой

При старте нового инстанса `dev_rag.mcp_server` автоматически убирать свои
висящие копии — дубликаты от переподключений MCP-клиента (ZCode).

## 2. Корень проблемы (замерено, не гипотеза)

2026-07-20 на машине автора обнаружено **6 висящих инстансов** RAG + 6 k3
под одним ZCode. Анализ деревьев процессов (`Win32_Process`, PowerShell)
показал:

1. Все 6 инстансов RAG имели **живого** родителя — ZCode (PID 7812). Это НЕ
   «сироты с мёртвым родителем», а **дубликаты от переподключений**: ZCode
   поднимал новый MCP-инстанс, забывая погасить старые. Простая проверка
   «родитель мёртв → убить» их бы не поймала.
2. Каждый инстанс виден парой «стаб + настоящий python»: на Windows venv
   `Scripts\python.exe` (стаб) запускает настоящий интерпретатор
   (`C:\Python310-64\python.exe`) как дочерний процесс. ZCode убивает только
   стаб, kill на Windows не каскадируется → настоящий python остаётся.

Проверено: `indexer.py` / `zvec_searcher.py` **сами не порождают** дочерних
процессов (subprocess — только короткие `git` вызовы; sentence-transformers
вызывается без `num_workers`). Все «внуки» — артефакт venv-stab, не нашей код.

### 2a. Windows venv-топология (критично для дизайна)

Структура одного инстанса на Windows::

    ZCode (клиент, без маркера)
      └─ stub  `C:\VENV310-64\Scripts\python.exe -m dev_rag.mcp_server`  [маркер]
           └─ real `C:\Python310-64\python.exe -m dev_rag.mcp_server`    [маркер]

Из этого следуют два нетривиальных решения в `select_victims`:

- **Группировка по owner (владельцу-клиенту), а не по ppid.** У каждого real
  свой stub-родитель (ppid=stub), а не общий ZCode. Группировка по ppid
  разнесла бы настоящих дубликатов в разные группы по 1 и никто бы не убивался
  (это был **баг 2** первоначальной реализации — выявлен при анализе живых
  процессов после рестарта).
- **stub исключается из кандидатов** (`_stub_pids`): убив real, мы каскадно
  гасим и его stub (venv-stub ждёт дочерний процесс и выходит). Иначе в группе
  `[stubЧужой, realЧужой]` multi-client убил бы чужой real, убив его stub.

### 2b. Баги реализации (выявлены и устранены в итерациях)

Первоначальная и две последующие реализации имели дефекты, выявленные
анализом живой системы и dry-run'ами. Все исправлены, каждый покрыт
регрессионным тестом:

- **Баг 1 (self-exclusion).** Self исключался из кандидатов → одиночный
  старый дубль выживал как «новейший в группе из одного». Исправлено: self
  участвует в выборе `max(create_time)` и никогда не выбирается жертвой.
- **Баг 2 (ppid grouping).** Группировка по ppid не работает на Windows
  (см. 2a): каждый real имеет родителем свой stub. Исправлено: группировка
  по предкам, а не по ppid.
- **Баг 3 (single owner — ближайший предок).** Стало ясно после анализа
  двух claude.exe под одним Code.exe: «ближайший предок без маркера» — это
  claude.exe (разный у двух инстансов), хотя фактически они под одним
  VSCode. Исправлено: учитываем ВСЕХ живых предков, а не ближайшего.
- **Баг 4 (корень в ancestors).** При переходе на «все предки» общие
  корневые процессы (explorer.exe, systemd) схлопнули в одну группу вообще
  все RAG на машине — multi-client план 001 сломался. Исправлено: обход
  предков обрывается на `_ROOT_PROCESS_NAMES`, сам корень в ancestors не
  попадает.

Регрессионные тесты: `test_single_old_orphan_does_not_survive_beside_new` (1),
`test_windows_stub_topology_groups_by_common_ancestor` (2),
`test_two_claude_sessions_in_one_vscode_are_duplicates` (3),
`test_root_process_not_in_ancestors_isolation` (4),
`test_self_does_not_kill_duplicates_in_other_clients_group` (5).

- **Баг 5 (глобальная уборка — чужой клиент).** Шаг newest-wins в
  `select_victims` применялся ко всем группам size>1 без проверки, состоит ли
  в группе self. Группировка корректно разносила разные клиенты по разным
  группам, но при старте инстанса под одним клиентом защита убивала дубли в
  совершенно чужом клиенте — нарушение multi-client plan 001. Не ловился
  прежними unit-тестами: там каждый чужой клиент был одиночкой (группа size=1).
  Выявлен dry-run на живой системе 2026-07-21: ZCode pid=34940 (свежий)
  убивал бы два VSCode-инстанса. Исправлен пропуском группы, где self не
  состоит (коммит `767ff65`). Фикс не вводит новой семантики — приводит код
  в соответствие с уже заявленным в docstring инвариантом.

### 2c. Решение о границе клиента (архитектурный выбор)

Dry-run на живой системе показал: VSCode task (через cmd.exe) и Claude
extension (через claude.exe) делят общего предка `Code.exe`. Автор не дал
явного ответа; решение принято исполнителем (см. комментарий в
`process_guard.py`, раздел «Граница один клиент»):

**Два consumer'а RAG под одним GUI-клиентом (VSCode, ZCode) схлопываются
как дубликаты.** Обоснование:
- На практике 2 RAG под одним VSCode — это артефакт переподключения
  (как сегодня: забытый VSCode task + новая Claude-сессия).
- RO-lock'и zvec разделяемые (plan 001), два параллельных поиска безопасны.
- Multi-client VSCode vs ZCode vs Claude-standalone сохранён (нет общего
  GUI-предка).
- Альтернатива (белый список клиентских процессов) неприменима — новые
  версии расширений будут ломать защиту.

## 3. Решение

Стратегия **newest-wins по владельцу-клиенту** (выбрана автором из 3 вариантов):

1. Найти процессы с маркером `-m dev_rag.mcp_server` в cmdline.
2. Исключить **stub'ы** (`_stub_pids` — marcer-процессы, чей pid является
   чьим-то ppid) и **своих предков** (собственный stub-цепочка).
3. Сгруппировать оставшихся по **owner** — ближайшему предку без маркера.
4. Для каждой группы:
   - owner **мёртв** → все в жертвы (сироты), кроме self;
   - owner **жив** → выживает `max(create_time)`; self всегда выживает.
5. **Разные живые владельцы не трогают друг друга** — сохраняет multi-client
   сценарий plan 001 (ZCode + Claude Code над одним индексом).

**Зависимость:** `psutil>=5.9` как прямая. Обоснование: проект отвергает тихий
отказ (сквозная тема plan 001, `config.py:14-16`, `zvec_searcher.py`). Альтернативы:
PowerShell/wmic шелл-ауты — Windows-only и непроверяемы юнит-тестами;
optional-extra создал бы тот самый тихий отказ в чистой установке. psutil имеет
wheels на всех ОС из CI-матрицы.

## 4. Что сделано (10 коммитов, 5 файлов + scripts)

| Коммит | Тип | Содержание |
|---|---|---|
| `ef180f1` | `build:` | `pyproject.toml` (+9: psutil>=5.9) |
| `6b0ff6a` | `feat:` | `src/dev_rag/process_guard.py` (новый), `mcp_server.py` (+19: хук в main) |
| `ff9e5a7` | `test:` | `tests/test_process_guard.py` (новый, 10 кейсов) |
| `e56de03` | `docs:` | `plans/handoff-003.md` (новый) |
| `bfb77c4` | `docs:` | handoff актуализирован после dry-run |
| `7987723` | `feat:` | `DEV_RAG_PROCESS_GUARD_LOG` файловый лог (+2 теста) |
| `a3ab618` | `feat:` | **переработка**: группировка по общему предку, stop-list корней (+5 кейсов) |
| `59dd19c` | `chore:` | `scripts/rag_proc_check.ps1`, `scripts/rag_proc_watch.ps1`, `.gitignore` |
| `48853f5`, `65c217c` | `docs:` | актуализация handoff + pickup-003 (точка подхвата) |
| `767ff65` | `fix:` | **баг 5**: пропуск чужих групп в newest-wins (+1 регрессионный тест) |

Финальная ветка: 10 коммитов поверх `main` @ `b472078`.

### Архитектура process_guard.py

Логика разделена на 2 функции:

- **`select_victims(procs, my_pid, owner_alive, is_ancestor_of_mine)`** — ЧИСТАЯ
  функция, без обращения к ОС. Принимает снимок процессов (`_ProcInfo` с
  предвычисленным полем `owner`) и два колбэка-предиката. Тестируется без psutil
  — тот же приём, что в `test_diagnostics.py` (мок через аргументы, не через
  monkeypatch модуля).
- **`cleanup_orphan_siblings()`** — точка входа для `mcp_server.main()`: делает
  снимок процессов через psutil, разрешает owner для каждого real-процесса
  (обход `parents()` до первого предка без маркера), вызывает `select_victims()`,
  убивает выбранные. psutil импортируется **лениво**: при `ImportError` — явный
  `_log.warning` (не молчок), возвращает 0, не падает.

### Хук в mcp_server.main()

Вызов `cleanup_orphan_siblings()` в самом начале `main()`, **до** настройки
stdin/stdout и **до** загрузки модели — чтобы не множить потребление памяти.
Обёрнуто в `try/except Exception`: уборка best-effort, поиск важнее; любой сбой
не должен ронять MCP-сервер.

## 5. Тесты

`tests/test_process_guard.py` — **17 unit-тестов** (без `@pytest.mark.integration`,
не трогают ОС). Покрытие:

| Тест | Что проверяет |
|---|---|
| `test_orphan_with_all_dead_ancestors_is_killed` | все предки мертвы → сирота → убит |
| `test_old_duplicate_with_common_ancestor_is_killed` | два real, общий живой предок → старый убит |
| `test_single_old_orphan_does_not_survive_beside_new` | **регрессия бага 1**: одиночный старый дубль не выживает рядом с новым |
| `test_different_clients_no_common_ancestor_not_killed` | ZCode + Claude, общих предков нет → никого не трогаем (multi-client plan 001) |
| `test_root_process_not_in_ancestors_isolation` | **регрессия бага 3**: корневые процессы отсечены, разные GUI-клиенты не схлопываются |
| `test_two_claude_sessions_in_one_vscode_are_duplicates` | **регрессия бага 4**: 2 claude.exe под одним Code.exe → одна группа → старый убит |
| `test_windows_stub_topology_groups_by_common_ancestor` | Windows venv stub-топология: 3 real под одним ZCode |
| `test_transitive_grouping_three_instances_one_client` | транзитивность: A~B~C через мост, все трое в одной группе |
| `test_stub_itself_is_never_a_victim` | stub исключён из кандидатов всегда |
| `test_own_venv_stub_not_killed` | собственный stub-предок защищён |
| `test_orphan_with_one_dead_one_live_ancestor_not_orphan` | один живой предок достаточен, чтобы не быть сиротой |
| `test_non_dev_rag_python_not_touched` | k3-mcp-server и чужие python не трогаются (scope) |
| `test_empty_snapshot_returns_empty` | пустой снимок → пустой список жертв |
| `test_import_error_is_explicit_warning` | нет psutil → явный warning, 0, без падения |
| `test_file_log_written_when_env_set` | DEV_RAG_PROCESS_GUARD_LOG → файл с my_pid/killed/victims |
| `test_file_log_skipped_when_env_unset` | без env файл не создаётся |
| `test_self_does_not_kill_duplicates_in_other_clients_group` | **регрессия бага 5**: self под ZCode не убирает дубли в чужой группе Claude |

**Вся сюита: `pytest tests/` → 87 passed** (было 70, +16 в исходной работе,
+1 регрессия бага 5 в этой сессии).

Проверка импортов: `from dev_rag import mcp_server, process_guard` — чисто,
entry-point'ы (`main`, `cleanup_orphan_siblings`, `select_victims`) callable.

## 6. Приёмка

### Автоматическая (выполнено)
- `pytest tests/test_process_guard.py -v` — 17/17 passed.
- `pytest tests/` — 87/87 passed.
- `pyproject.toml` парсится, psutil в dependencies.

### Автоматическая end-to-end (выполнено 2026-07-21)
- **Реальный kill на живых процессах** — см. 6c-3: синтетический дубль
  под ZCode-деревом убит `process_guard`, `guard.log` зафиксировал
  `killed=1 victims=[pid=5220]`.

### 6b. Dry-run на живой системе (выполнено, несколько итераций)

2026-07-21, после нескольких циклов открытия/закрытия ZCode + параллельно
работающие VSCode и Claude Code. Финальное состояние: 4 real-инстанса RAG
под 2 GUI-клиентами (ZCode, VSCode), внутри VSCode — 3 consumer'а (task,
2 сессии Claude).

**Dry-run ДО фикса бага 5** (select_victims от лица свежего ZCode pid=34940):
- ZCode убивал бы VSCode-инстансы 7580, 48504 — к которым не имеет никаких
  общих предков. Это и был баг 5: newest-wins применялся к чужой группе.

**Dry-run ПОСЛЕ фикса** (коммит `767ff65`, тот же снимок):
- ZCode pid=34940 (свежий) → **0 жертв**. Корректно: VSCode-дубли —
  дело свежего VSCode-инстанса при его старте, не ZCode.
- Поведение внутри своего клиента не изменилось: прежние тесты
  (`test_two_claude_sessions_in_one_vscode_are_duplicates`,
  `test_windows_stub_topology_groups_by_common_ancestor`,
  `test_transitive_grouping_three_instances_one_client`) — зелёные.

**Доказательный guard.log с killed>0 всё ещё не получен** (см. 6c/6d).

### 6c. Прямой лог (частично получен)

`guard.log` (через `DEV_RAG_PROCESS_GUARD_LOG` в `.mcp.json` ARLINE):
```
2026-07-21 08:14:51 my_pid=40556 marked=11 real_candidates=5 killed=0 victims=[none]
```
Эта запись — от **промежуточной версии** кода (до исправления бага 3):
защита видела 5 real, но не убирала дубль (Claude под VSCode). После
переработки логика бы убрала старого Claude. Финальный лог с killed>0
пока не получен.

#### 6c-1. Почему env-лог не пишется после полного Quit ZCode (найдено 2026-07-21)

После полного Quit ZCode и рестарта (2026-07-21 10:10) запустился новый
MCP-инстанс под workspace ARLINE (pid 29208 → stub 36644 → real 49104).
Лог `guard.log` **не обновился**. Причина — **ZCode не прокидывает
`DEV_RAG_PROCESS_GUARD_LOG` в env запускаемого MCP-сервера**, несмотря на
корректную запись в `.mcp.json` (валидный JSON, ASCII, без скрытых
символов — проверено побайтово). Прямая проверка env процесса 49104:

| Переменная | Declared в .mcp.json | Actual в pid=49104 |
|---|---|---|
| `RAG_BACKEND` | `zvec` | `zvec` ✓ |
| `DEV_RAG_ROOT` | `C:/REPO/ARLINE` | `C:/REPO/ARLINE` ✓ |
| `DEV_RAG_PROFILE` | `k3_mebel` | `k3_mebel` ✓ |
| `HF_HUB_OFFLINE` | `1` | `1` ✓ |
| **`DEV_RAG_PROCESS_GUARD_LOG`** | `C:/REPO/multilingual-dev-rag/scripts/guard.log` | **MISSING** |

4 из 5 переменных дошли, пятая — нет. Отличие потерянной переменной: самое
длинное значение (47 символов vs 3–17) и путь к файлу. Подозрение — ZCode
квотирует/обрезает длинные env-значения или фильтрует по нераскрытому
правилу. **Это внешний по отношению к нашему коду дефект** (handoff 7:
«Чинить ZCode — не контролируем»). Сам `process_guard` отработал корректно
— просто тихо, как и задумано без env.

**Дополнительный нюанс:** после Quit ZCode поднял новый MCP-инстанс от
workspace ARLINE, не от `multilingual-dev-rag` (хотя открыт второй). Снимок
процессов в этот момент: 4 real-инстанса под 2 GUI-клиентами (ZCode,
VSCode). `select_victims` от лица свежего ZCode корректно даёт **0 жертв**
— это multi-client сценарий plan 001: VSCode-дубли — дело свежего
VSCode-инстанса, не ZCode (см. фикс бага 5, раздел 2b).

#### 6c-2. Обходные пути (если доказательный лог всё же нужен)

1. **Положить путь в `DEV_RAG_ROOT`-подобную переменную**, которая точно
   прокидывается (но загрязняет семантику существующих переменных).
2. **Захардкодить в `process_guard.py`** путь относительно `__file__`
   (модуль знает свой репо). Минус — лог всегда пишется, не env-gated.
3. **Ждать фикса ZCode** по корректному прокидыванию env из `.mcp.json`.
   Зарепортить в трекер ZCode (вне scope этого репо).
4. **Запускать MCP-сервер вручную из терминала** с явным env для разовой
   проверочной сессии — обходит ZCode-стек полностью.

#### 6c-3. Доказательный killed>0 получен вручную (2026-07-21 10:41)

Через путь 4 (обход ZCode-стека): скрипт породил два синтетических
инстанса `dev_rag.mcp_server` под ZCode-деревом (A, затем B с
`DEV_RAG_PROCESS_GUARD_LOG=...`). B при старте вызвал
`cleanup_orphan_siblings`, увидел A как дубль (общий живой предок —
bash.exe → ZCode.exe) и убил его.

Финальный `scripts/guard.log` (3 строки — три эпохи):
```
2026-07-21 08:14:51 my_pid=40556 marked=11 real_candidates=5 killed=0 victims=[none]
2026-07-21 10:38:51 my_pid=40672 marked=4 real_candidates=2 killed=1 victims=[pid=24004]
2026-07-21 10:41:42 my_pid=21216 marked=4 real_candidates=2 killed=1 victims=[pid=5220]
```

Последняя запись — **чистый воспроизводимый тест** без побочных жертв:
4 marker-процесса = A(stub+real) + B(stub+real), 2 real-кандидата (A и B),
`max(create_time)` = B (свежий), A убит. End-to-end проверка kill-цикла
`cleanup_orphan_siblings` на реальном psutil-снимке без моков. Задача
приёмки 6c выполнена.

**Побочный эффект теста:** рабочий pid=49104 (RAG под ZCode) исчез во
время первого прогона (10:38) — он попал в ту же группу что и A, и был
самым старым (`create_time` 10:10:34), но не попал в victims (видимо,
к моменту снимка уже отсутствовал — ZCode оборвал stdin и процесс вышел
сам). После теста рабочий MCP под ZCode не поднялся автоматически —
нужен новый RAG-вызов или рестарт сессии ZCode для его восстановления.

### 6d. Ручная (на авторе, при желании)

> ⚠️ **Обновлено 2026-07-21:** шаг ниже был выполнен (полный Quit ZCode),
> но `guard.log` не обновился, потому что ZCode не прокидывает
> `DEV_RAG_PROCESS_GUARD_LOG` в env MCP-сервера (см. 6c-1). Инструкция
> сохранена для будущего, когда/если ZCode починят.

Чтобы получить доказательный `guard.log` с killed>0:
1. Удостовериться, что `DEV_RAG_PROCESS_GUARD_LOG` прописан в
   `C:\REPO\ARLINE\.mcp.json` (вне этого репо).
2. Полный Quit ZCode из трея (не крестик — в трей).
3. Открыть ZCode с workspace `C:\REPO\ARLINE`, вызвать RAG-поиск.
4. Прочитать `scripts/guard.log` — будет `killed=N victims=[pid=...]`.

**Альтернатива без зависимости от ZCode:** запустить MCP-сервер вручную
из терминала с явным env:
```bash
DEV_RAG_PROCESS_GUARD_LOG=C:/REPO/multilingual-dev-rag/scripts/guard.log \
DEV_RAG_ROOT=C:/REPO/ARLINE RAG_BACKEND=zvec \
  C:/venv310-64/Scripts/python.exe -m dev_rag.mcp_server < /dev/null
```
Это обходит ZCode-стек и точно проставит env. Лог запишется. Но для
получения killed>0 нужно, чтобы к моменту запуска в живых был дубль того
же клиента (multi-client не считается).

Если killed=0 victims=[none] при живых дублях — баг в ОС-части
(`cleanup_orphan_siblings`: сбор ancestors через `parents()`,
фильтр `_ROOT_PROCESS_NAMES`), не в чистой `select_victims`.

## 7. Что НЕ в scope

- **`k3-mcp-server`** (отдельный репо `C:\REPO\ARLINE\k3-mcp-server`) — тоже
  affected, отдельный PR туда. Здесь не трогается.
- **Чинить ZCode** — мы не контролируем; mitigation на стороне MCP-сервера.
- **RO/RW lock-модель zvec** (ограничение plan 001) — не связано с дубликатами
  процессов.

## 8. Релиз

После мерджа PR — **minor bump**, тег `v0.2.0` (semver: новая фича + новая
зависимость = minor). Версия в `pyproject.toml` остаётся `dynamic = ["version"]`
(источник правды — git-тег, по договорённости репо). Тегирование — отдельный
шаг после мерджа, по команде автора.

## 9. Стиль репо (соблюдено)

- Комментарии в коде — русские, развёрнутые, объясняют «почему» (см.
  `process_guard.py` docstring про venv-stab, про ленивый import psutil).
- Commits — conventional (`build:`, `feat:`, `test:`), subj на английском,
  тело по-русски.
- Тесты — unit, без `@pytest.mark.integration` (не трогают движок zvec / ОС).
- Мок через аргументы чистой функции, не через monkeypatch модуля (как в
  `test_diagnostics.py`).

## 10. Команды для проверки

```bash
# Текущая ветка и состояние
git -C C:\REPO\multilingual-dev-rag branch --show-current   # feat/process-guard-003
git -C C:\REPO\multilingual-dev-rag log --oneline main..HEAD

# Прогон тестов
C:/venv310-64/Scripts/python.exe -m pytest tests/test_process_guard.py -v
C:/venv310-64/Scripts/python.exe -m pytest tests/    # вся сюита

# Проверка диффа
git -C C:\REPO\multilingual-dev-rag diff main...HEAD
```

## 11. Если нужно править

- **Логика отбора** — только в `select_victims()` (`process_guard.py`). Менять
  алгоритм → сначала обновить тесты, потом функцию (как plan 001 требовал).
- **Стратегия агрессивности** — если понадобится «глобально убивать всех кроме
  себя» (вариант 3 из обсуждения), убрать группировку по owner, оставить
  только исключение себя и своих предков. **Внимание:** ломает multi-client
  сценарий plan 001.
- **Маркер команды** — `_MCP_MARKER` в `process_guard.py`. Если запуск
  изменится (не `-m dev_rag.mcp_server`), обновить маркер.
- **Разрешение owner** — в `cleanup_orphan_siblings` через `psutil.Process(
  pid).parents()` до первого предка без маркера. Если владелец неразрешим
  (все предки мертвы) → owner=0 → трактуется как мёртвый → процесс-сирота.
- **Граница воздействия** — шаг 5 в `select_victims`: группу обрабатываем,
  только если в ней есть self. Снятие этой проверки вернёт «глобальную
  уборку» (баг 5): свежий инстанс одного клиента будет убирать дубли чужих.
