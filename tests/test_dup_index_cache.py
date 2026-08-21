"""Tests for the persisted dup-oracle index (TB-94).

Offline: a deterministic, call-counting stub embedder stands in for the model,
so the disk-cache reuse/invalidation/fallback logic is exercised with no torch
and no network. Every test passes its own tmp ``cache_dir`` so nothing is written
to the real ``~/.cache`` during the suite.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402

from src.dup_index import logic_tokens  # noqa: E402
from src.dup_oracle import FunctionRecord  # noqa: E402

_VOCAB = 64


class CountingEmbed:
    """Deterministic bag-of-logic-tokens embedder that counts how many function
    sources it embeds -- the probe for "did we re-embed or reuse?"."""

    def __init__(self, vocab: int = _VOCAB) -> None:
        self.vocab = vocab
        self.calls = 0

    def __call__(self, source: str) -> np.ndarray:
        self.calls += 1
        vec = np.zeros(self.vocab, dtype=np.float32)
        for tok in logic_tokens("x.ts", source):
            vec[sum(ord(c) for c in tok) % self.vocab] += 1.0
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm > 0 else vec


def _make_repo(root: Path) -> None:
    (root / "a.ts").write_text(
        'export function toSlug(s) {\n  return s.toLowerCase().replace(RE, "-").trim();\n}\n'
    )
    (root / "b.ts").write_text(
        'export function slugify(value) {\n  return value.toLowerCase().replace(RE, "-").trim();\n}\n'
    )
    (root / "c.ts").write_text(
        'export function fib(n) {\n  return n < 2 ? n : fib(n - 1) + fib(n - 2);\n}\n'
    )


# --------------------------------------------------------------------------
# Increment 1 -- the _index_file seam
# --------------------------------------------------------------------------

def test_index_file_returns_records_and_row_aligned_vectors(tmp_path):
    from src.dup_repo_index import _index_file

    _make_repo(tmp_path)
    embed = CountingEmbed()
    records, vecs = _index_file(str(tmp_path), "a.ts", embed)

    assert [r.name for r in records] == ["toSlug"]
    assert all(isinstance(r, FunctionRecord) for r in records)
    assert len(vecs) == len(records)  # row-aligned
    assert vecs[0].shape == (_VOCAB,)
    assert embed.calls == 1  # one function embedded


def test_index_file_skips_unsupported_language(tmp_path):
    from src.dup_repo_index import _index_file

    # An extension the grammar layer doesn't map at all raises
    # UnsupportedLanguageError -> skipped. (The real walk only feeds in-scope
    # code files; this is the defensive branch.)
    (tmp_path / "notes.txt").write_text("just some prose, not code\n")
    records, vecs = _index_file(str(tmp_path), "notes.txt", CountingEmbed())
    assert records == []
    assert vecs == []


def test_index_file_propagates_systemic_grammar_error(tmp_path, monkeypatch):
    import src.dup_repo_index as dri

    (tmp_path / "a.ts").write_text(
        'export function keep(s) {\n  return s.toLowerCase().trim();\n}\n'
    )

    def boom(rel, src):
        raise RuntimeError("tree-sitter ABI mismatch for 'typescript'")

    monkeypatch.setattr(dri, "extract_functions", boom)
    with pytest.raises(RuntimeError):
        dri._index_file(str(tmp_path), "a.ts", CountingEmbed())


# --------------------------------------------------------------------------
# Increment 2 -- B1: persist + reuse unchanged (AC1)
# --------------------------------------------------------------------------

def _same_index(a, b) -> None:
    """Assert two indexes are equivalent (records, embeddings, token_df)."""
    assert [(r.path, r.name, r.lineno, r.logic_tokens) for r in a.records] == \
           [(r.path, r.name, r.lineno, r.logic_tokens) for r in b.records]
    assert np.allclose(a.embeddings, b.embeddings)
    assert a.token_df == b.token_df


def test_second_build_reuses_cache_and_re_embeds_nothing(tmp_path):
    from src.dup_repo_index import load_or_build_dup_index

    repo = tmp_path / "repo"
    repo.mkdir()
    cache = tmp_path / "cache"
    _make_repo(repo)

    embed1 = CountingEmbed()
    idx1 = load_or_build_dup_index(str(repo), embed_fn=embed1, cache_dir=str(cache))
    assert len(idx1) == 3
    assert embed1.calls == 3  # first build embeds every function

    # A fresh embedder counter stands in for the next hook process. With nothing
    # changed, the persisted vectors must be reused -- zero repo re-embeds.
    embed2 = CountingEmbed()
    idx2 = load_or_build_dup_index(str(repo), embed_fn=embed2, cache_dir=str(cache))
    assert embed2.calls == 0
    assert len(idx2) == 3
    _same_index(idx1, idx2)


def test_cache_file_is_written_under_cache_dir(tmp_path):
    from src.dup_repo_index import load_or_build_dup_index

    repo = tmp_path / "repo"
    repo.mkdir()
    cache = tmp_path / "cache"
    _make_repo(repo)
    load_or_build_dup_index(str(repo), embed_fn=CountingEmbed(), cache_dir=str(cache))
    written = list(cache.glob("dup-index.*.npz"))
    assert len(written) == 1


def test_reused_index_still_finds_the_planted_duplicate(tmp_path):
    from src.dup_oracle import find_near_duplicates
    from src.dup_repo_index import load_or_build_dup_index

    repo = tmp_path / "repo"
    repo.mkdir()
    cache = tmp_path / "cache"
    _make_repo(repo)
    load_or_build_dup_index(str(repo), embed_fn=CountingEmbed(), cache_dir=str(cache))
    idx = load_or_build_dup_index(str(repo), embed_fn=CountingEmbed(), cache_dir=str(cache))

    i = next(k for k, r in enumerate(idx.records) if r.name == "toSlug")
    hits = find_near_duplicates(
        idx.embeddings[i], idx.records[i].logic_tokens, idx.records,
        idx.embeddings, idx.token_df, skip=lambda k, rec: k == i,
    )
    assert [h.name for h in hits] == ["slugify"]  # dup survives the cache round-trip


# --------------------------------------------------------------------------
# Increment 3 -- B2: selective recompute + prune + bounded cap (AC2)
# --------------------------------------------------------------------------

def test_only_changed_file_re_embedded_and_deleted_file_pruned(tmp_path):
    from src.dup_repo_index import load_or_build_dup_index

    repo = tmp_path / "repo"
    repo.mkdir()
    cache = tmp_path / "cache"
    _make_repo(repo)  # a.ts, b.ts, c.ts -- one function each

    load_or_build_dup_index(str(repo), embed_fn=CountingEmbed(), cache_dir=str(cache))

    # Change a.ts (new logic -> new hash) and delete c.ts.
    (repo / "a.ts").write_text(
        'export function toSlug(s) {\n  return s.toUpperCase().split(SEP).join("_");\n}\n'
    )
    (repo / "c.ts").unlink()

    embed = CountingEmbed()
    idx = load_or_build_dup_index(str(repo), embed_fn=embed, cache_dir=str(cache))

    assert embed.calls == 1  # only a.ts's single function re-embedded; b.ts reused
    names = {r.name for r in idx.records}
    assert names == {"toSlug", "slugify"}  # a.ts (changed) + b.ts; c.ts pruned
    assert "fib" not in names  # deleted file's record is gone


def _repo_of_single_fn_files(root: Path, names: list[str]) -> None:
    """One file per name (sorted by filename), each with exactly one function."""
    for nm in names:
        (root / f"{nm}.ts").write_text(
            f"export function {nm}(x) {{\n  return x.step().via_{nm}();\n}}\n"
        )


def test_first_build_embedding_is_bounded_by_max_funcs(tmp_path, capsys):
    from src.dup_repo_index import load_or_build_dup_index

    repo = tmp_path / "repo"
    repo.mkdir()
    cache = tmp_path / "cache"
    _repo_of_single_fn_files(repo, ["f0", "f1", "f2", "f3", "f4", "f5"])  # 6 fns

    embed = CountingEmbed()
    idx = load_or_build_dup_index(
        str(repo), embed_fn=embed, cache_dir=str(cache), max_funcs=3
    )
    assert embed.calls == 3      # bounded: never embeds all 6
    assert len(idx) == 3         # returned index capped
    err = capsys.readouterr().err
    assert "coverage partial" in err
    assert "first 3" in err


def test_over_cap_file_is_carried_forward_not_re_embedded(tmp_path):
    from src.dup_repo_index import load_or_build_dup_index

    repo = tmp_path / "repo"
    repo.mkdir()
    cache = tmp_path / "cache"
    # Sorted order: a, b, c -- all three fit and get cached.
    _repo_of_single_fn_files(repo, ["a", "b", "c"])
    load_or_build_dup_index(str(repo), embed_fn=CountingEmbed(), cache_dir=str(cache), max_funcs=3)

    # Add a file that sorts first, pushing c outside the cap of 3.
    (repo / "0.ts").write_text('export function z0(x) {\n  return x.step().via_z0();\n}\n')
    embed2 = CountingEmbed()
    load_or_build_dup_index(str(repo), embed_fn=embed2, cache_dir=str(cache), max_funcs=3)
    assert embed2.calls == 1  # only the new 0.ts embedded; c not re-embedded, just carried

    # Remove 0.ts again -> c re-enters the cap. If c had been carried forward
    # (not deleted), it is reused now with zero re-embeds.
    (repo / "0.ts").unlink()
    embed3 = CountingEmbed()
    idx3 = load_or_build_dup_index(str(repo), embed_fn=embed3, cache_dir=str(cache), max_funcs=3)
    assert embed3.calls == 0  # a, b, c all reused -- carry-forward saved c's vectors
    assert {r.name for r in idx3.records} == {"a", "b", "c"}


# --------------------------------------------------------------------------
# Increment 4 -- B3: contract-change invalidation (AC3)
# --------------------------------------------------------------------------

def _the_cache_file(cache: Path) -> Path:
    files = list(cache.glob("dup-index.*.npz"))
    assert len(files) == 1
    return files[0]


def _read_manifest(cache_path: Path) -> dict:
    with np.load(cache_path, allow_pickle=False) as npz:
        return json.loads(bytes(npz["manifest"]).decode("utf-8"))


def _rewrite_manifest(cache_path: Path, mutate) -> None:
    """Load the cache, mutate its manifest in place, resave (same .npz shape)."""
    with np.load(cache_path, allow_pickle=False) as npz:
        vectors = np.asarray(npz["vectors"], dtype=np.float32)
        manifest = json.loads(bytes(npz["manifest"]).decode("utf-8"))
    mutate(manifest)
    mbytes = np.frombuffer(json.dumps(manifest).encode("utf-8"), dtype=np.uint8)
    with open(cache_path, "wb") as fh:
        np.savez(fh, vectors=vectors, manifest=mbytes)


def test_changing_embedder_id_discards_the_cache(tmp_path):
    from src.dup_repo_index import load_or_build_dup_index

    repo = tmp_path / "repo"
    repo.mkdir()
    cache = tmp_path / "cache"
    _make_repo(repo)

    load_or_build_dup_index(str(repo), embed_fn=CountingEmbed(), cache_dir=str(cache), embedder_id="model-A")

    embed = CountingEmbed()
    load_or_build_dup_index(str(repo), embed_fn=embed, cache_dir=str(cache), embedder_id="model-B")
    assert embed.calls == 3  # stale vectors from model-A never reused
    assert _read_manifest(_the_cache_file(cache))["meta"]["embedder_id"] == "model-B"


def test_bumping_cache_format_version_discards_the_cache(tmp_path, monkeypatch):
    import src.dup_repo_index as dri

    repo = tmp_path / "repo"
    repo.mkdir()
    cache = tmp_path / "cache"
    _make_repo(repo)

    dri.load_or_build_dup_index(str(repo), embed_fn=CountingEmbed(), cache_dir=str(cache))

    monkeypatch.setattr(dri, "CACHE_FORMAT_VERSION", dri.CACHE_FORMAT_VERSION + 1)
    embed = CountingEmbed()
    dri.load_or_build_dup_index(str(repo), embed_fn=embed, cache_dir=str(cache))
    assert embed.calls == 3  # old-format cache discarded, everything re-embedded


def test_dim_mismatch_in_meta_discards_the_cache(tmp_path):
    from src.dup_repo_index import load_or_build_dup_index

    repo = tmp_path / "repo"
    repo.mkdir()
    cache = tmp_path / "cache"
    _make_repo(repo)

    load_or_build_dup_index(str(repo), embed_fn=CountingEmbed(), cache_dir=str(cache))
    # Corrupt the recorded dim so it no longer matches the vector matrix.
    _rewrite_manifest(_the_cache_file(cache), lambda m: m["meta"].__setitem__("dim", 999))

    embed = CountingEmbed()
    load_or_build_dup_index(str(repo), embed_fn=embed, cache_dir=str(cache))
    assert embed.calls == 3  # integrity check failed -> full rebuild
