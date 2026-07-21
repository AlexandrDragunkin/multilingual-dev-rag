# Pickup — plan 003 (для новой сессии)

> Компактная точка подхвата. Полная летопись — в `handoff-003.md`.
> Эта сессия закрыта из-за контекста; новая начинает отсюда.

## Где мы сейчас (одной фразой)

План 003 (автоуборка процессов-дубликатов `dev_rag.mcp_server`) **реализован
и протестирован**, ветка `feat/process-guard-003` готова к ревью/пушу. Один
шаг остался непроверенным на живой системе — доказательный `guard.log` с
`killed>0`.

## Факты, которые надо знать сразу

- **Репо:** `C:\REPO\multilingual-dev-rag`, ветка `feat/process-guard-003`
  (от `main` @ `b472078`), **не запушена**.
- **Тесты:** `pytest tests/` → **86 passed** (было 70, +16 в `test_process_guard`).
- **Python:** `C:/venv310-64/Scripts/python.exe` (3.10.8, единственный с zvec).
- **9 коммитов** в ветке, conventional (`build/feat/test/docs/chore`).
- **Номер 003 — не rename!** «plan 002» уже занят будущим планом по zvec 0.6.0.

## Что делает plan 003

При старте нового `dev_rag.mcp_server` автоматически убирает висящие копии
(дубликаты от переподключений MCP-клиентов). Стратегия **newest-wins по
общему GUI-предку**:
- два real-инстанса в одной группе, если пересекаются их множества живых
  предков (кроме корневых explorer/systemd/...);
- в группе выживает самый свежий, остальные убиваются;
- сироты (все предки мертвы) — убиваются;
- разные GUI-клиенты (VSCode vs ZCode) друг друга не трогают (multi-client
  plan 001 сохранён).

Файлы: `src/dev_rag/process_guard.py`, `src/dev_rag/mcp_server.py` (хук в
`main`), `tests/test_process_guard.py` (16 кейсов), `pyproject.toml`
(`psutil>=5.9`).

## Что уже проверено

- **16 unit-тестов** — покрывают 4 найденных бага + базовые сценарии.
- **Dry-run на живой системе** (2026-07-21): 4 real-инстанса под 2 GUI-клиентами.
  VSCode group (3 consumer'а) корректно схлопывается; ZCode изолирован.

## Что осталось (опционально)

**Доказательный `guard.log` с `killed>0`.** Промежуточный лог был `killed=0
victims=[none]` (при живом дубле — это и показало баг 3,现已 исправлено).
Финального `killed>0` пока нет — для него нужен полный Quit ZCode из трея
(крестик уходит в трей, не перечитывает `.mcp.json`).

Инструкция (`handoff-003.md` раздел 6d):
1. В `C:\REPO\ARLINE\.mcp.json` уже прописано `DEV_RAG_PROCESS_GUARD_LOG`.
2. Полный Quit ZCode (правый клик в трее → Quit).
3. Открыть ZCode с workspace `C:\REPO\ARLINE`, сделать RAG-поиск.
4. Прочитать `C:/REPO/multilingual-dev-rag/scripts/guard.log`.

## Архитектурное решение (принято исполнителем, не подтверждено автором)

Граница клиента = общий GUI-предок. Два consumer'а RAG под одним VSCode
(Claude extension + забытый task) схлопываются как дубль. Обоснование в
`handoff-003.md` раздел 2c. **Если автор не согласен — это нужно менять.**

## Что НЕ делать

- **Не путать с plan 002** (zvec 0.6.0 tokenizer).
- **Не пушить без явной команды автора.**
- **Не трогать `k3-mcp-server`** (отдельный репо `C:\REPO\ARLINE\k3-mcp-server`).
- **Не переименовывать ветку.**

## Команды для быстрого старта

```bash
cd C:/REPO/multilingual-dev-rag
git branch --show-current          # feat/process-guard-003
git log --oneline main..HEAD      # 9 коммитов
C:/venv310-64/Scripts/python.exe -m pytest tests/    # 86 passed
C:/venv310-64/Scripts/python.exe -m pytest tests/test_process_guard.py -v
```

## Что разумно сделать дальше (на усмотрение автора)

1. **Принять как есть** → пуш + PR (минорный bump, тег `v0.2.0`).
2. **Доказательный тест** → цикл по 6d, затем пуш.
3. **Пересмотреть решение 2c** → если автор хочет различать Claude extension
   и VSCode task под одним VSCode.
