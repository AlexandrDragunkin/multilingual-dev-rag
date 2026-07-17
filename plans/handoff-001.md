# Хендаут: multilingual-dev-rag, план 001 — для продолжения в другой сессии/модели

> Контекст для подхвата. Работа по плану 001 уже выполнена и залита в PR #3.
> Этот промт — чтобы другая модель/сессия могла продолжить (ревью, доработки,
> приёмка на других платформах, дефекты, всплывшие после мерджа).

---

## 0. Если ты — принимающая модель, прочти это первым

- Это **хендаут (handoff)**, а не новая задача. Большая часть уже сделана.
  Не переделывай молча — сначала сверься с разделом «Что уже сделано».
- Репозиторий: `C:\REPO\multilingual-dev-rag`, ветка `fix/zvec-readonly`.
- Коммит решения: `48835ad`. PR: https://github.com/AlexandrDragunkin/multilingual-dev-rag/pull/3
  (mergeable: True, `Closes #2`). Issue: https://github.com/AlexandrDragunkin/multilingual-dev-rag/issues
- План-документ (живой, в репо): `plans/001-zvec-readonly-and-diagnostics.md`.
- Автор задачи явно просил: работать в ветке `fix/zvec-readonly`, **не в main**;
  **не трогать** `RrfReRanker(rank_constant=60)` (гипотеза о его виновности опровергнута).

---

## 1. Задача одной фразой

Починить 3 дефекта в `src/dev_rag/zvec_searcher.py` и `src/dev_rag/mcp_server.py`
по плану 001 — каждый по отдельности давал правдоподобный, но неверный ответ
(пустой результат вместо ошибки; плоские score; lock-конфликт между клиентами).

---

## 2. Окружение (важно — без него ничего не запустится)

- **OS:** Windows 11 (win32 10.0.26200), shell — Git Bash.
- **Python:** единственный рабочий интерпретатор —
  `C:\venv310-64\Scripts\python.exe` (Python 3.10.8, 64-bit).
  В нём установлены `zvec==0.5.1` и `multilingual-dev-rag` (editable, указывает
  на `C:\REPO\multilingual-dev-rag\src`). Другие интерпретаторы (Python 3.7-32,
  3.10-32, 3.12-32) **zvec не содержат** — не трать время.
- **Env (уже стоит, не нужно VPN):**
  `DEV_RAG_ROOT=C:\REPO\ARLINE`, `DEV_RAG_PROFILE=k3_mebel`,
  `HF_HUB_OFFLINE=1`, `RAG_BACKEND=zvec`.
- **Индекс уже построен** и живой:
  `C:\Users\aleksandr.HONOR117\AppData\Local\dev-rag\ARLINE-c896671a\zvec_data\{docs,code,plans}`.
  Переиндексация (`dev-rag-index --category docs --force`) нужна только если
  хочется свежий корпус — занимает время из-за embeddings.
- **Точка входа CLI:** в venv есть `dev-rag-search.exe` / `dev-rag-index.exe` /
  `dev-rag-mcp.exe`. ВАЖНО: `python -m dev_rag.cli` **ничего не делает** — в
  `cli.py` нет `if __name__=='__main__'`. Запускать только через `.exe` или
  `python -c "from dev_rag.cli import search_main; search_main()"`.
- **Тесты:** `python -m pytest tests/` (67 тестов, 10 из них `@pytest.mark.integration`
  — ходят в реальный zvec). Маркер `integration` уже зарегистрирован в `pyproject.toml`.
- **gh CLI нет.** GitHub-операции делаются через REST API + `git credential fill`
  (credential helper хранит токен пользователя). См. раздел «Если нужен GitHub».

Контрольный запрос для приёмки (из плана):
`dev-rag-search.exe "ферма воркеров" --collection docs --paths-only`
→ должен дать дифференцированные score `0.0159–0.0164`, и §8
«Бэкенд и брокер: ферма воркеров K3» должен быть в top-3.

---

## 3. Три дефекта — что требовалось (краткая выжимка плана)

1. **Эксклюзивный read-write lock** (`zvec_searcher.py`).
   Существующая коллекция открывалась `zvec.open(coll_path)` (read-write по
   умолчанию). zvec берёт эксклюзивный `…/zvec_data/<cat>/LOCK`, второй процесс
   ловил `RuntimeError: Can't lock read-write collection` → тихо «No results found».
   Решение: открывать с `CollectionOption(read_only=True, enable_mmap=True)`.
   Путь **создания** (`zvec_indexer._create_collection`, `create_and_open`,
   `read_only=False`) НЕ трогать — это прерогатива индексатора.

2. **Тихий отказ** в `_get_collection` (`zvec_searcher.py`):
   голый `except Exception: return None` → любая ошибка открытия превращалась
   в пустой ответ. Решение: `_log.warning('zvec open failed for %r: %s', category, e)`.
   Bare `logging.warning` идёт в stderr через `logging.lastResort` — проверено.

3. **Обрезка score** в MCP (`mcp_server.py:58`): `:.3f` → `:.4f`.
   При спреде `0.0005` (типичный RRF-диапазон `0.0159–0.0164`) `:.3f` схлопывал
   все score в `0.016`. CLI уже печатает `:.4f` — привести MCP в соответствие.

---

## 4. Что уже сделано (коммит `48835ad`, ветка `fix/zvec-readonly`)

- Дефект 3: `mcp_server.py` — `:.3f` → `:.4f`, с поясняющим комментарием.
- Дефект 2: `zvec_searcher.py` — добавлен `import logging` + `_log = logging.getLogger(__name__)`;
  в `_get_collection` ловится `except Exception as e:` и пишется warning.
- Дефект 1: `_open_or_create_collection` теперь открывает существующую коллекцию
  с `read_only=True, enable_mmap=True` (`CollectionOption` уже импортирован в файле).
- **Сначала тесты, потом правка** (как просил план):
  `tests/test_zvec_integration.py` — добавлены
  `test_readonly_handle_answers_query` и `test_two_readonly_handles_coexist`.
- Все 67 тестов проходят.

---

## 5. КЛЮЧЕВАЯ НАХОДКА — обязательно учти, если будешь копать глубже

План предполагал, что `read_only=True` решит любой lock-конфликт. **На zvec 0.5.1
это не так.** Замеренная lock-модель:

| holder | второй open | результат |
|---|---|---|
| RW удерживается | open RO | **FAIL** `Can't lock read-only collection` |
| RW удерживается | open RW | **FAIL** `Can't lock read-write collection` |
| RO удерживается | open RO | **OK** (оба живут) |
| RO удерживается | open RW | FAIL |

Проверено и **межпроцессно** (на реальном индексе ARLINE), не только in-process.
**Следствие:** сценарий *«искать, пока индексатор пишет коллекцию»* этой версией
zvec **не поддерживается** в принципе — даже с read-only.

Почему это ок для plan 001: все приёмки стартуют из «никто не держит коллекцию»,
а сломанный ARLINE кейс был конфликтом **search↔search** (клиент + CLI / два
клиента). После правки все *search*-клиенты открывают RO → конфликт исчез.
Это ограничение зафиксировано в докстринге `_open_or_create_collection` и в
комментарии к тестам — **не пытайся «починить» его как баг, это факт о zvec 0.5.1**.

Если в будущем понадобится «искать во время индексации» — это отдельная задача:
либо обновление zvec (поведение могло поменяться), либо сторожить, чтобы индексатор
не держал LOCK во время поиска (отпустил хэндл после `flush()+optimize()`).

---

## 6. Что уже принято (не пересдавай без причины)

Приёмка гонялась на реальном индексе ARLINE, `"ферма воркеров" --collection docs`:

- **AC3** — CLI-поиск **при живом RO-holder** (имитация открытого MCP-клиента)
  возвращает полный top-3, §8 в top-3, holder остаётся жив, lock-ворнингов нет.
- **AC4** — **три RO-процесса одновременно** (holder + два параллельных поиска)
  — все возвращают результаты.
- **Дефект 2** — RW-holder + CLI-поиск пишет в stderr
  `zvec open failed for 'docs': Can't lock read-only collection`.
- **Дефект 3** — MCP `rag_search` отдаёт **3 разных score** в top-5
  (`0.0164 / 0.0161 / 0.0159`).

Как гонять concurrency-приёмку заново (holder держит коллекцию через фикса):
написать скрипт, который делает `from dev_rag.zvec_searcher import _get_collection;
_get_collection("docs")` и ждёт на sentinel-файле (НЕ на `sys.stdin.readline()` —
в фоне stdin сразу EOF, и процесс умрёт; это первая ошибка, которую я допустил).

---

## 7. Что разумно сделать дальше (если задача — продолжить, а не ревью)

- **Повторить приёмку на Linux/macOS.** Поведение zvec-токенайзера `standard`
  платформозависимо (см. `test_standard_cyrillic_behavior_matches_platform`).
  Lock-модель тоже стоит перепроверить на другой ОС — я мерил только на Windows.
- **Подумать, не вынести ли RO-параметр в конфиг** (`ZVEC_SEARCH_READ_ONLY`),
  если появится сценарий, где нужен RW-поиск. Сейчас захардкожено `True`.
- **Дефект 2 — «лучший» вариант из плана:** сейчас warning идёт только в stderr/log.
  План упоминал, что для MCP хорошо бы вернуть осмысленный текст в теле ответа
  (`Index unavailable: …`). Я сознательно не стал менять контракт `search() -> list`
  (он общий с qdrant-бэкендом, CLI, MCP, тестами) — но если потребуется, это
  отдельное архитектурное решение (tuple результатов + diagnostics, или
  módul-level буфер последних ошибок).
- **Проверить, не осталось ли holder-процессов** зомби после моих запусков
  (я их аккуратно гасил, но на всякий случай: `tasklist | grep python`).

---

## 8. Если нужен GitHub (gh CLI нет)

Создание PR / операций через REST API с токеном из git credential helper:

```bash
printf 'url=https://github.com\n\n' | git credential fill 2>/dev/null > /tmp/cred.txt
USER=$(grep '^username=' /tmp/cred.txt | cut -d= -f2)
TOKEN=$(grep '^password=' /tmp/cred.txt | cut -d= -f2-)
# дальше curl -u "$USER:$TOKEN" https://api.github.com/repos/AlexandrDragunkin/multilingual-dev-rag/...
rm -f /tmp/cred.txt   # НЕ печатай TOKEN и не коммить /tmp/cred.txt
```

Токен — PAT длиной 40 (не OAuth `gho_`), Basic-auth работает.

---

## 9. Стиль репо (чтобы правки не выбивались)

- Комментарии в коде — русские, развёрнутые, объясняют «почему», а не «что».
  Этот стиль **нужно поддерживать** — см. существующие комменты в `config.py`,
  `zvec_indexer.py`, тестах. Молчаливый баг всегда снабжается комментарием,
  почему именно так.
- Commits — conventional (`fix:`, `test:`, `docs:`, `build:`, `ci:`),
  subj на английском, в теле можно русский.
- Тесты для движка — `@pytest.mark.integration`, в `tests/test_zvec_integration.py`,
  с фикстурой `coll` (временная коллекция, cleanup через `del + gc.collect()` ДО
  `rmtree`, иначе RocksDB шумит — см. докстрингу фикстуры).
- Имя коллекции должно проходить regex zvec: однобуквенные (`'t'`) отвергаются,
  `'probe_itX'` / `'lockprobeX'` проходят. Схема без полей тоже отвергается.
