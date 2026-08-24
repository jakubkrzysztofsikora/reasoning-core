"""Build the repo-wide function + embedding index the near-duplicate oracle
queries.

Walks the repo's source files, extracts named functions, computes their logic
tokens + a repo token-df, and embeds each. The embedder is **injectable**
(default: ``dup_embed.embed_function``) so the build / cap logic is unit-testable
offline with a stub -- the real model is only reached when no ``embed_fn`` is
supplied.

File discovery reuses ``project_index._iter_repo_files`` with the dup oracle's
own extension set: that helper walks only ``.py/.js/.ts/.tsx`` by default (for
its call-graph feature), which would silently miss duplicates in ``.mjs/.cjs``
(both JavaScript).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .dup_index import build_token_df, extract_functions, logic_tokens
from .dup_oracle import FunctionRecord
from .grammars import EXTENSION_MAP, UnsupportedLanguageError
from .project_index import _iter_repo_files

# The oracle dedups *functions*, so index only languages the extractor AND the
# normalizer fully handle -- currently Python / JavaScript / TypeScript. Being
# grammar-parseable is NOT sufficient: the real gates are dup_index._FUNC_TYPES
# (which function nodes are extracted) and normalize()'s bound-name handling
# (which params/locals are canonicalized so renamed duplicates collapse). C# and
# SQL parse but are deliberately OUT OF SCOPE -- enabling them needs their
# function node types in _FUNC_TYPES and their param containers in the
# normalizer, not just a wider walk. Explicit allowlist by design; yields
# {.py, .js, .mjs, .cjs, .ts, .tsx}.
_CODE_LANGUAGES = frozenset({"python", "javascript", "typescript", "tsx"})
_DUP_SRC_EXTS = frozenset(
    ext for ext, lang in EXTENSION_MAP.items() if lang in _CODE_LANGUAGES
)


def _max_funcs_from_env() -> int:
    """Cap embedding cost / memory on large monorepos (768-dim f32 ~= 3 KB/fn).

    A malformed env value falls back to the default rather than raising at import
    time (which would break the hook's fail-open contract).
    """
    try:
        return int(os.environ.get("RC_DUP_ORACLE_MAX_FUNCS", "5000"))
    except ValueError:
        return 5000


DEFAULT_MAX_FUNCS = _max_funcs_from_env()


@dataclass
class DupOracleIndex:
    """The repo functions the oracle checks a new function against."""

    records: list[FunctionRecord]
    embeddings: np.ndarray  # (n, d) float32, L2-normalised, row-aligned to records
    token_df: Counter

    def __len__(self) -> int:
        return len(self.records)


def _default_embed() -> Callable[[str], "np.ndarray"]:
    # Imported lazily so this module (and its offline tests) never pull torch
    # unless the real embedder is actually used.
    from .dup_embed import embed_function

    return embed_function


def _index_file(
    repo_root: str,
    rel: str,
    embed_fn: Callable[[str], "np.ndarray"],
) -> tuple[list[FunctionRecord], list["np.ndarray"]]:
    """Extract, tokenise and embed every named function in one repo file.

    Returns ``(records, vecs)`` row-aligned (``vecs[i]`` is the embedding of
    ``records[i]``). The single per-file extract+embed code path shared by the
    plain builder and the persistent cache, so both honour the same contracts:

    * an unreadable file (``OSError``) or a non-code extension
      (``UnsupportedLanguageError``) yields ``([], [])`` -- skip it;
    * a grammar-load/ABI ``RuntimeError`` is deliberately **not** caught. Every
      in-scope language is a required dependency, so such a failure is systemic
      (a broken install), not a per-file quirk -- letting it surface is correct
      rather than masking it as a silently-empty index. This is also why callers
      must assemble fully *before* persisting: a mid-build raise here must never
      leave a truncated cache behind.
    """
    try:
        with open(os.path.join(repo_root, rel), encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    except OSError:
        return [], []
    try:
        funcs = extract_functions(rel, src)
    except UnsupportedLanguageError:
        return [], []
    records: list[FunctionRecord] = []
    vecs: list["np.ndarray"] = []
    for name, line, fsrc in funcs:
        records.append(FunctionRecord(rel, name, line, logic_tokens(rel, fsrc)))
        vecs.append(embed_fn(fsrc))
    return records, vecs


def _assemble_index(
    records: list[FunctionRecord], vecs: list["np.ndarray"]
) -> DupOracleIndex:
    """Pack row-aligned records + vectors into a ``DupOracleIndex`` (token_df is
    derived cheaply from the records)."""
    if records:
        embeddings = np.stack(vecs).astype(np.float32)
    else:
        embeddings = np.empty((0, 0), dtype=np.float32)
    token_df, _ = build_token_df([r.logic_tokens for r in records])
    return DupOracleIndex(records=records, embeddings=embeddings, token_df=token_df)


def build_dup_index(
    repo_root: str,
    *,
    embed_fn: Optional[Callable[[str], "np.ndarray"]] = None,
    max_funcs: int = DEFAULT_MAX_FUNCS,
) -> DupOracleIndex:
    """Build the function + embedding index for ``repo_root`` (no persistence).

    ``embed_fn`` maps a function's source to an L2-normalised vector; when
    omitted the real code embedder is used. ``max_funcs`` bounds how many
    functions land in the index, so a monorepo can't blow the budget. Stays
    persistence-free -- the disk cache lives in :func:`load_or_build_dup_index`.
    """
    if embed_fn is None:
        embed_fn = _default_embed()

    records: list[FunctionRecord] = []
    vecs: list["np.ndarray"] = []
    for rel in _iter_repo_files(repo_root, _DUP_SRC_EXTS):
        if len(records) >= max_funcs:
            break
        frecords, fvecs = _index_file(repo_root, rel, embed_fn)
        for rec, vec in zip(frecords, fvecs):
            records.append(rec)
            vecs.append(vec)
            if len(records) >= max_funcs:
                break
    return _assemble_index(records, vecs)


# ---------------------------------------------------------------------------
# Persistent disk cache
# ---------------------------------------------------------------------------
#
# The advisory hook runs as a fresh process per edit, so an in-process cache
# cannot survive between edits. This cache persists the function+embedding index
# to disk per repo, keyed per file by content hash: unchanged files reuse their
# cached vectors and only changed/new files are embedded, so an edit's advisory
# is a cached lookup (~seconds) rather than a whole-repo re-embed. Self-contained
# (no sidecar, no network) and offline-testable via the injectable stub embedder.
# See docs/dup-oracle.md.

# BUMP THIS on ANY change to the embedding pipeline -- the model id, the model
# weights, OR dup_embed's normalisation. Model weights can't be fingerprinted
# offline, so this manual bump is the enforced contract that guarantees
# incompatible vectors can never be reused across a pipeline change. The
# embedder_id + dim fields in meta are automatic belt-and-braces on top.
CACHE_FORMAT_VERSION = 1


def _cache_dir_default() -> Path:
    """Parent cache directory, mirroring the repo convention (_patch_tracker /
    _prm_promotion): ``$RC_CACHE_DIR`` override else ``~/.cache/reasoning-core``."""
    override = os.environ.get("RC_CACHE_DIR")
    return Path(override) if override else Path.home() / ".cache" / "reasoning-core"


def _default_embedder_id() -> str:
    """Identity of the real embedder, so swapping ``RC_EMBEDDER`` invalidates the
    cache automatically (dup_embed defaults this to ``unixcoder-base``)."""
    return os.environ.get("RC_EMBEDDER", "unixcoder-base")


def _cache_path(repo_root: str, cache_dir: Optional[str]) -> Path:
    base = Path(cache_dir) if cache_dir else _cache_dir_default()
    key = hashlib.sha256(os.path.abspath(repo_root).encode("utf-8")).hexdigest()[:16]
    return base / f"dup-index.{key}.npz"


def _file_hash(repo_root: str, rel: str) -> Optional[str]:
    """SHA-256 of a file's raw bytes, or ``None`` if unreadable (skip it)."""
    try:
        with open(os.path.join(repo_root, rel), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def _load_cache(
    path: Path, embedder_id: str
) -> Optional[tuple[dict, "np.ndarray"]]:
    """Load + validate the cache, returning ``(manifest, vectors)`` or ``None``.

    ``None`` on ANY of: missing/corrupt/unreadable file, an embedding-contract
    mismatch (format version, embedder id, or dim), or a manifest/matrix-shape
    inconsistency. A ``None`` result means "rebuild from scratch" -- the cache is
    never allowed to crash the caller (AC4, read side).
    """
    try:
        with np.load(path, allow_pickle=False) as npz:
            vectors = np.asarray(npz["vectors"], dtype=np.float32)
            manifest = json.loads(bytes(npz["manifest"]).decode("utf-8"))
    except Exception:  # noqa: BLE001 - corrupt/missing cache must never raise
        return None
    if not isinstance(manifest, dict):
        return None
    meta = manifest.get("meta", {})
    files = manifest.get("files")
    dim = meta.get("dim")
    if (
        meta.get("format_version") != CACHE_FORMAT_VERSION
        or meta.get("embedder_id") != embedder_id
        or not isinstance(dim, int)
        or not isinstance(files, dict)
    ):
        return None
    # Integrity: the matrix shape must match what the manifest claims, else a
    # reuse would read the wrong rows.
    total = 0
    for entry in files.values():
        fns = entry.get("functions") if isinstance(entry, dict) else None
        if not isinstance(fns, list) or not isinstance(entry.get("row_start"), int):
            return None
        total += len(fns)
    if vectors.ndim != 2 or vectors.shape[0] != total or vectors.shape[1] != dim:
        return None
    return manifest, vectors


def _reuse_entry(
    rel: str, entry: dict, vectors: "np.ndarray"
) -> tuple[list[FunctionRecord], list["np.ndarray"]]:
    """Reconstruct a file's records + vectors from its cached manifest entry."""
    rs = entry["row_start"]
    records: list[FunctionRecord] = []
    vecs: list["np.ndarray"] = []
    for j, fn in enumerate(entry["functions"]):
        records.append(FunctionRecord(rel, fn["name"], fn["line"], list(fn["logic_tokens"])))
        vecs.append(np.asarray(vectors[rs + j], dtype=np.float32))
    return records, vecs


def _fns_meta(records: list[FunctionRecord]) -> list[dict]:
    return [
        {"name": r.name, "line": r.lineno, "logic_tokens": r.logic_tokens}
        for r in records
    ]


def _save_cache(
    path: Path,
    out_files: dict,
    out_vecs: list["np.ndarray"],
    embedder_id: str,
) -> None:
    """Atomically write the cache as a single ``.npz`` (binary vectors + a uint8
    JSON manifest). A write failure is swallowed -- an advisory must never block
    an edit (AC4, write side). Callers assemble the whole index *before* calling
    this, so a mid-build error never reaches here with a truncated result.
    """
    if out_vecs:
        matrix = np.stack(out_vecs).astype(np.float32)
        dim = int(matrix.shape[1])
    else:
        matrix = np.zeros((0, 0), dtype=np.float32)
        dim = 0
    manifest = {
        "meta": {
            "format_version": CACHE_FORMAT_VERSION,
            "embedder_id": embedder_id,
            "dim": dim,
        },
        "files": out_files,
    }
    manifest_bytes = np.frombuffer(json.dumps(manifest).encode("utf-8"), dtype=np.uint8)
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".npz.tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                np.savez(fh, vectors=matrix, manifest=manifest_bytes)
            os.replace(tmp, path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except OSError:
        pass  # fail open -- persistence is best-effort


def load_or_build_dup_index(
    repo_root: str,
    *,
    embed_fn: Optional[Callable[[str], "np.ndarray"]] = None,
    max_funcs: int = DEFAULT_MAX_FUNCS,
    cache_dir: Optional[str] = None,
    embedder_id: Optional[str] = None,
) -> DupOracleIndex:
    """Build the index, reusing a persisted per-file cache where possible.

    Walks ``repo_root`` in stable sorted order and embeds up to ``max_funcs``
    functions (a bounded ceiling, so the first build always terminates -- a huge
    repo indexes its first N and reports partial coverage on stderr). Files whose
    content hash is unchanged reuse their cached vectors; changed/new files are
    re-embedded. Over-cap cache entries are carried forward (never deleted); only
    files deleted from disk are pruned. The cache is saved once, at the end, on
    the success path -- a systemic error from :func:`_index_file` propagates with
    the previous cache left intact. Set ``RC_DUP_ORACLE_CACHE=0`` to disable
    persistence entirely.
    """
    if embed_fn is None:
        embed_fn = _default_embed()
    if embedder_id is None:
        embedder_id = _default_embedder_id()
    cache_enabled = os.environ.get("RC_DUP_ORACLE_CACHE", "1") != "0"
    cache_path = _cache_path(repo_root, cache_dir)

    loaded = _load_cache(cache_path, embedder_id) if cache_enabled else None
    if cache_enabled and loaded is None and cache_path.exists():
        # A cache file was present but unusable -- corrupt, or an incompatible
        # embedding contract (embedder id / format version / dim). We fall back to
        # a full rebuild; surface it on stderr so an unexpected, repeated full
        # re-embed isn't invisible. Fail-open breadcrumb, not an error -- a strict
        # raise-instead mode is the RC_DUP_ORACLE_STRICT follow-up.
        print(
            "dup-oracle: cache discarded (corrupt or incompatible) — rebuilding index",
            file=sys.stderr,
        )
    old_manifest, old_vectors = loaded if loaded else (None, None)
    old_files: dict = old_manifest["files"] if old_manifest else {}

    files_on_disk = _iter_repo_files(repo_root, _DUP_SRC_EXTS)  # sorted, stable
    disk_set = set(files_on_disk)

    walked: list[str] = []  # files processed this run, in sorted order
    walked_records: dict[str, list[FunctionRecord]] = {}
    walked_vecs: dict[str, list["np.ndarray"]] = {}
    walked_hash: dict[str, str] = {}
    embedded_funcs = 0
    stopped_early = False

    for rel in files_on_disk:
        if embedded_funcs >= max_funcs:
            stopped_early = True
            break
        fhash = _file_hash(repo_root, rel)
        if fhash is None:
            continue  # unreadable -- skip, don't cache
        entry = old_files.get(rel)
        if entry is not None and entry.get("hash") == fhash and old_vectors is not None:
            recs, vecs = _reuse_entry(rel, entry, old_vectors)
        else:
            # May raise a systemic RuntimeError -- deliberately propagates BEFORE
            # any cache write, so the previous cache stays intact (AC4).
            recs, vecs = _index_file(repo_root, rel, embed_fn)
        walked.append(rel)
        walked_records[rel] = recs
        walked_vecs[rel] = vecs
        walked_hash[rel] = fhash
        embedded_funcs += len(recs)

    partial = stopped_early or embedded_funcs > max_funcs
    if partial:
        print(
            f"dup-oracle: repo exceeds {max_funcs} functions, indexing first "
            f"{max_funcs} — coverage partial",
            file=sys.stderr,
        )

    # Build the new cache: freshly-walked files, plus carry-forward of over-cap
    # cached files that still exist on disk (never delete an entry for a file
    # that is merely outside the cap this run). Prune only disk-deleted files.
    carried = [
        rel for rel in old_files
        if rel not in walked_records and rel in disk_set and old_vectors is not None
    ]
    out_files: dict = {}
    out_vecs: list["np.ndarray"] = []
    row = 0
    for rel in sorted(set(walked) | set(carried)):
        if rel in walked_records:
            recs, vecs, h = walked_records[rel], walked_vecs[rel], walked_hash[rel]
        else:
            recs, vecs = _reuse_entry(rel, old_files[rel], old_vectors)
            h = old_files[rel]["hash"]
        out_files[rel] = {"hash": h, "row_start": row, "functions": _fns_meta(recs)}
        out_vecs.extend(vecs)
        row += len(vecs)

    if cache_enabled:
        _save_cache(cache_path, out_files, out_vecs, embedder_id)

    # Returned index: the within-cap walked functions, sorted-walk order, capped.
    ret_records: list[FunctionRecord] = []
    ret_vecs: list["np.ndarray"] = []
    for rel in walked:
        ret_records.extend(walked_records[rel])
        ret_vecs.extend(walked_vecs[rel])
    return _assemble_index(ret_records[:max_funcs], ret_vecs[:max_funcs])
