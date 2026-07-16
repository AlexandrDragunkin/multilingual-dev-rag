# multilingual-dev-rag

Local-first multilingual RAG for code and technical docs. In-process hybrid retrieval (vector + full-text) with FTS for Cyrillic and other non-ASCII scripts.

[Русская версия](README_RU.md) · Apache-2.0

---

## Why this exists

Most local RAG setups pair a multilingual embedding model with a full-text index and call it hybrid search. That works — until your corpus is not in English.

The embedded engine this is built on ([zvec](https://github.com/alibaba/zvec)) ships a `standard` tokenizer that **drops non-ASCII tokens entirely**, and a `lowercase` filter that **only folds ASCII**. The result is not an error. A query for `воркер` returns zero rows, a query for `RabbitMQ` returns rows, and the hybrid quietly degrades to vector-only for half your corpus. Nothing in the logs says so.

## How the fix works

Swapping the tokenizer does not save you. The engine ships exactly three, and each breaks differently:

| Tokenizer | Cyrillic | Identifiers | Punctuation |
| --- | --- | --- | --- |
| `standard` | **discards it** | splits correctly: `obj_k3_gab3` → `obj`, `k3`, `gab3` | separates correctly |
| `whitespace` | keeps it | **never splits** | **glues**: `RabbitMQ.` is one token |
| `jieba` | **shreds it** | splits | separates |

`jieba` looks like the answer right up until you measure it at scale: 7 of 9 correct on a five-document probe, pure noise on a real 837-chunk corpus — a query for the exact title word of one document returned three unrelated ones, a different wrong answer per letter case. Worse than `standard`, which at least stayed honestly silent.

Hence the design: **do not pick the best tokenizer — use two**, each for what it is good at. The index carries two fields, and every query goes only to the field that can answer it:

| Field | Content | Tokenizer | Answers |
| --- | --- | --- | --- |
| `text` | original, verbatim | `standard` | ASCII words and identifiers — `obj_k3_gab3` is findable as `obj`, `k3`, `gab3` |
| `text_fts` | non-ASCII tokens only, casefolded, punctuation stripped | `whitespace` | Cyrillic, Greek, accented Latin, Arabic — in any letter case |

`text` stays byte-for-byte the original: it is what search returns to you. `text_fts` is derived and internal.

Latin is deliberately **excluded** from `text_fts`. If both fields matched `RabbitMQ`, a document containing it would score twice in the Reciprocal Rank Fusion and outrank documents that match the query's Russian half. Keeping the two lanes disjoint keeps the fusion honest.

Case folding happens in Python, not in the engine: `casefold()` handles `ß`/`SS` and Greek final sigma, which the engine's ASCII-only `lowercase` filter does not.

## What you get

- **Hybrid retrieval** — vector + full-text, fused with Reciprocal Rank Fusion.
- **FTS that survives non-ASCII** — verified end-to-end on Russian, German, Greek, French, Arabic.
- **No servers** — zvec runs in your process. No Qdrant, no Ollama, no Docker.
- **Profiles** — describe your corpus in JSON; the package stays generic.
- **MCP server** — exposes `rag_search` to Claude Code and other MCP clients.

## Requirements

- **64-bit Python 3.10–3.14.** zvec ships wheels for Linux (`manylinux_2_28`, x86_64 and aarch64), macOS (arm64) and Windows (amd64) — 64-bit only. On a 32-bit interpreter `pip install` fails with `from versions: none`, which means *wrong platform*, not *missing package*.
- ~500 MB of disk for the embedding model on first run.

## Install

```bash
pip install multilingual-dev-rag
```

The default embedding model (`paraphrase-multilingual-MiniLM-L12-v2`, Apache-2.0) is downloaded from Hugging Face on first use and is not bundled.

## Configure

Two environment variables:

```bash
# Required: the repository you want to index.
export DEV_RAG_ROOT=/path/to/your/repo

# Optional: which files count. Built-in: generic (default), k3_mebel.
# Or a path to your own profile JSON.
export DEV_RAG_PROFILE=generic
```

`DEV_RAG_ROOT` is **not** guessed from the package location, deliberately. An earlier version defaulted to a path near the install directory; when that guess was wrong, globs matched nothing, indexing reported `0 chunks` and exited `0`.

Two guards replace that guess. A missing root, or one that is not a directory, raises before anything runs. A root that exists but matches no file under the profile's patterns makes `dev-rag-index` **exit 2** with both values printed — an empty index built "successfully" looks healthy right up until a search returns nothing and also says nothing.

## Use

```bash
# Index the corpus (first run also downloads the model)
dev-rag-index --category all --force

# Index one collection
dev-rag-index --category docs --force

# Search from the shell
dev-rag-search "ферма воркеров" --collection docs
```

```python
from dev_rag import search

for r in search('усталость воркера', collection='docs', n=5):
    print(f"{r['score']:.4f}  {r['path']}")
```

### As an MCP server

```json
{
  "mcpServers": {
    "dev-rag": {
      "command": "dev-rag-mcp",
      "env": {
        "DEV_RAG_ROOT": "/absolute/path/to/your/repo",
        "DEV_RAG_PROFILE": "generic"
      }
    }
  }
}
```

MCP servers are child processes of the client, not daemons: `env` is read once at startup. After editing the config, restart the client.

## Profiles

A profile says which files belong to which collection. `generic` indexes markdown and Python anywhere; `k3_mebel` is a worked example for a CAD codebase with `.mac` macros.

```json
{
  "name": "my_project",
  "collections": { "docs": "my_docs", "code": "my_code", "plans": "my_plans" },
  "index_patterns": {
    "docs":  ["docs/**/*.md"],
    "code":  ["src/**/*.py"],
    "plans": ["rfcs/*.md"]
  }
}
```

```bash
export DEV_RAG_PROFILE=/path/to/my_project.json
```

## Backends

| Backend | Model | Servers needed |
| --- | --- | --- |
| **zvec** (default) | `paraphrase-multilingual-MiniLM-L12-v2` (384d) | none — in-process |
| qdrant (legacy) | `nomic-embed-text` via Ollama (768d) | Qdrant + Ollama |

The Qdrant path predates zvec and is kept only as a fallback: `nomic-embed-text` is English-centric and recalls poorly on Russian. Install it with `pip install multilingual-dev-rag[qdrant]` and set `RAG_BACKEND=qdrant` if you need it.

## Known limits

- **CJK is not supported.** Chinese and Japanese have no whitespace between words, so the `whitespace` tokenizer swallows a sentence as one token. zvec ships a `jieba` tokenizer for exactly this — it would need a separate field. (Do not reach for `jieba` on Cyrillic: it indexes it, but shreds it into fragments that match everything. Measured on a real corpus, it ranked worse than returning nothing.)
- **Turkish `İ`/`ı` is not supported.** Caseless matching there is locale-dependent and neither `lower()` nor `casefold()` resolves it.
- **Indexing is manual.** Incremental indexing (`--changed-only`) is not implemented for the zvec backend; after changing your corpus, re-run a full index.
- **One writer.** zvec allows many readers but a single writer: do not index while another process holds the collection open for writing.

## Origins

Originally built for [K3-Мебель](https://k3-mebel.ru/) — a furniture-design CAD — to give semantic and full-text search over a Russian-language codebase and its documentation. That is where the non-ASCII FTS problem surfaced, and why the fix is measured against a real corpus rather than a toy example.

The `k3_mebel` profile is that original corpus, kept as a worked example of a hand-tuned profile: Python prototypes, `.mac` macros, API reference and plans.

## License

Apache-2.0. See [LICENSE](LICENSE).
