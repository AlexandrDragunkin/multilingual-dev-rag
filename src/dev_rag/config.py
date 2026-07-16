# -*- coding: utf-8 -*-
"""Configuration for multilingual-dev-rag.

Two things must be decided before indexing: **what to index** (the corpus root)
and **which files count** (the profile). Both come from the environment, so the
same installed package serves several projects.

    DEV_RAG_ROOT     absolute path to the repository being indexed. Required.
    DEV_RAG_PROFILE  built-in profile name, or a path to a profile JSON.
                     Default: 'generic'.

`DEV_RAG_ROOT` is deliberately **not** guessed from the package location. An
earlier version defaulted to `<package>/../..`, which quietly resolved to a
directory that held none of the corpus: globs matched nothing, indexing reported
"0 chunks" and exited successfully. A wrong answer that looks like a working one
is worse than a crash, so a missing root now raises — see `require_root()`.

Profiles keep project-specific knowledge out of the code. `generic` indexes
markdown and Python anywhere; `k3_mebel` is a hand-tuned example for a CAD
codebase with `.mac` macros. To describe your own corpus, copy a built-in
profile, edit the globs, and point `DEV_RAG_PROFILE` at the file.
"""
from __future__ import annotations

import hashlib
import json
import os

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROFILES_DIR = os.path.join(_PACKAGE_DIR, 'profiles')

# ==================== Corpus root ====================
# Empty (not a guess) when unset: see require_root().
DEV_RAG_ROOT = os.environ.get('DEV_RAG_ROOT', '')


def require_root() -> str:
    """Return the corpus root, or raise with an actionable message.

    Called by the indexer and searcher rather than at import time, so that
    `import dev_rag` stays cheap and testable without a corpus on disk.
    """
    if not DEV_RAG_ROOT:
        raise RuntimeError(
            'DEV_RAG_ROOT is not set. Point it at the repository you want to '
            'index, e.g. DEV_RAG_ROOT=/path/to/your/repo. It is not guessed '
            'from the package location on purpose: a wrong guess indexes '
            'nothing and reports success.'
        )
    if not os.path.isdir(DEV_RAG_ROOT):
        raise RuntimeError(
            f'DEV_RAG_ROOT points at {DEV_RAG_ROOT!r}, which is not a '
            f'directory. Nothing would be indexed.'
        )
    return DEV_RAG_ROOT


# ==================== Profile ====================
DEV_RAG_PROFILE = os.environ.get('DEV_RAG_PROFILE', 'generic')


def load_profile(name_or_path: str = None) -> dict:
    """Load a profile by built-in name or by path to a JSON file."""
    name_or_path = name_or_path or DEV_RAG_PROFILE
    path = name_or_path
    if not os.path.isfile(path):
        path = os.path.join(_PROFILES_DIR, f'{name_or_path}.json')
    if not os.path.isfile(path):
        available = sorted(
            f[:-5] for f in os.listdir(_PROFILES_DIR) if f.endswith('.json')
        )
        raise RuntimeError(
            f'Profile {name_or_path!r} not found. Built-in profiles: '
            f'{", ".join(available)}. Or pass a path to a profile JSON.'
        )
    with open(path, encoding='utf-8') as f:
        return json.load(f)


_profile = load_profile()

# Collection names and glob patterns come from the profile, not from this file.
COLLECTIONS = _profile['collections']
INDEX_PATTERNS = _profile['index_patterns']

# ==================== Backend selection ====================
# zvec   — in-process vector DB + local multilingual embeddings, no servers.
# qdrant — legacy: needs a Qdrant server and an Ollama embedder.
RAG_BACKEND = os.getenv('RAG_BACKEND', 'zvec')

# ==================== zvec backend ====================

def _default_db_path() -> str:
    """Where the index lives: a per-user, per-corpus directory.

    Not inside the package. The index is derived data — rebuilt by
    `dev-rag-index`, and holding chunks of whatever corpus you pointed at.
    Writing it into `site-packages/` works for an editable install and breaks
    for a normal one: on Linux that directory belongs to root.

    Keyed by the corpus root, so one installation can serve several
    repositories. With a single shared path, pointing DEV_RAG_ROOT at another
    repository and re-indexing would silently overwrite the previous index —
    and a search would then answer confidently from the wrong corpus.

    The directory name keeps the root's basename for readability and a short
    hash of its absolute path for uniqueness. Override wholesale with
    ZVEC_DB_PATH if you want the index somewhere specific.
    """
    if os.name == 'nt':
        base = os.environ.get('LOCALAPPDATA') or os.path.join(
            os.path.expanduser('~'), 'AppData', 'Local'
        )
    else:
        base = os.environ.get('XDG_DATA_HOME') or os.path.join(
            os.path.expanduser('~'), '.local', 'share'
        )
    root = os.path.abspath(DEV_RAG_ROOT) if DEV_RAG_ROOT else '_unset'
    slug = os.path.basename(root.rstrip(os.sep)) or 'root'
    digest = hashlib.md5(root.encode('utf-8')).hexdigest()[:8]
    return os.path.join(base, 'dev-rag', f'{slug}-{digest}', 'zvec_data')


ZVEC_DB_PATH = os.getenv('ZVEC_DB_PATH') or _default_db_path()
ZVEC_MODEL = os.getenv('ZVEC_MODEL', 'paraphrase-multilingual-MiniLM-L12-v2')
ZVEC_EMBED_DIM = 384  # paraphrase-multilingual-MiniLM-L12-v2 output dimension

# ==================== Qdrant + Ollama (legacy backend) ====================
QDRANT_URL = os.getenv('QDRANT_URL', 'http://localhost:6333')
OLLAMA_URL = os.getenv('OLLAMA_URL', 'http://localhost:11434')
EMBED_MODEL = os.getenv('EMBED_MODEL', 'nomic-embed-text')
EMBED_DIM = 768  # nomic-embed-text output dimension

# ==================== Chunking ====================
CHUNK_SIZE = 800     # characters per chunk
CHUNK_OVERLAP = 100  # overlap between consecutive chunks
