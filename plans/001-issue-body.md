# zvec: read-only collection open + explicit diagnostics (3 defects)

> **Скопируй это тело в [New issue](https://github.com/AlexandrDragunkin/multilingual-dev-rag/issues/new).**
> Title: `zvec: read-only collection open + explicit diagnostics`
> Labels: `bug`, `enhancement`

---

Three defects in the zvec search path, each independently producing a
plausible-but-wrong answer. All three confirmed by reproducible measurements
on 2026-07-17, not reasoning.

Detailed executable plan: [`plans/001-zvec-readonly-and-diagnostics.md`](../blob/main/plans/001-zvec-readonly-and-diagnostics.md).

## Defect 1 (critical): exclusive read-write lock on collection

**Symptom.** `dev-rag-search` from the CLI returns "No results found" while any
MCP client is running (each MCP client spawns its own `dev_rag.mcp_server`).
Closing all clients makes it work.

**Root cause.** Existing collection is opened as `zvec.open(coll_path)` with no
options (`src/dev_rag/zvec_searcher.py:59`) — read-write by default. zvec takes
an exclusive lock (`…/zvec_data/<cat>/LOCK`); a second process gets
`RuntimeError: Can't lock read-write collection`.

This blocks any setup where CLI + MCP, or two MCP clients, run on the same
machine against the same index.

**Fix.** Open existing collections read-only in the search path:

```python
# zvec_searcher.py:55-59
def _open_or_create_collection(category: str) -> Collection:
    coll_path = os.path.join(ZVEC_DB_PATH, category)
    if os.path.exists(coll_path):
        return zvec.open(
            coll_path,
            option=CollectionOption(read_only=True, enable_mmap=True),
        )
    # creation path below stays read_only=False (indexer's job)
```

**Acceptance.** With one MCP client open, run `dev-rag-search` from another
terminal → returns the same results as with all clients closed. Two MCP clients
open simultaneously → both return results.

⚠️ Verify that `Collection.query()` works on a read-only handle before merging.

## Defect 2: silent failure in `_get_collection`

**Symptom.** Any collection-open error (including the lock from defect 1)
becomes "No results found", as if the corpus were empty. The cause is invisible.

**Root cause.** `src/dev_rag/zvec_searcher.py:106-109`:

```python
try:
    _collections[category] = _open_or_create_collection(category)
except Exception:
    return None
```

`return None` → `search()` skips the category → empty result. Same class of
silent failure already documented in `config.py:14-16` ("0 chunks and exit 0").

**Fix.** Log the exception at WARNING/ERROR. For MCP, return the cause in the
text result (`f"Index unavailable: {e}"`); for CLI, print to stderr and exit
non-zero.

## Defect 3: MCP response truncates score to 3 decimal places

**Symptom.** The `rag_search` MCP tool prints `score=0.016` for every result,
even though real scores are `0.0159-0.0164`. Creates the illusion of flat scores
and broken ranking.

**Root cause.** `src/dev_rag/mcp_server.py:58`:

```python
f'--- Result {i} (score={r["score"]:.3f}) ---\n'
```

`:.3f` rounds 4-significant scores to 3 places; a 0.0005 spread between top-1
and top-5 vanishes.

**Note.** This is not cosmetic. In the project where it was found, the illusion
of flat scores drove a false "broken ranking" diagnosis and a multi-hour
investigation into `RrfReRanker(rank_constant=60)` — which turned out to work
correctly.

**Fix.**

```python
# mcp_server.py:58
f'--- Result {i} (score={r["score"]:.4f}) ---\n'
```

Bring MCP in line with CLI (`cli.py:67` already uses `:.4f`).

## Note on RRF

A hypothesis that `RrfReRanker(rank_constant=60)` causes flat scores was
**rejected**: ranking is correct, scores are differentiated. Do not touch
`rank_constant`.
