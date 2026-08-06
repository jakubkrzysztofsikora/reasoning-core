"""PreToolUse advisory: warn the agent when it writes a function the repo
already has.

Opt-in via ``RC_DUP_ORACLE=1`` (default off). On an Edit/Write/MultiEdit, it
extracts the function(s) being added, queries the repo's function+embedding
index (two-stage: cosine shortlist -> logic-token confirm -> distinctiveness
rank), and, if a behaviourally-equivalent function already exists elsewhere,
emits an ``additionalContext`` nudge so the agent can reuse instead of
re-inventing. **Never blocks** -- always exits 0, fails open on any error.

See ``docs/dup-oracle.md``. Modelled on ``post_assistant_diff_audit.py``.

Note: the repo index is built lazily on first use and cached per repo for the
process. Wiring it onto ``project_index``'s background-build lifecycle (so the
very first edit of a session doesn't pay the build) is a follow-up.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.dup_index import extract_functions, logic_tokens  # noqa: E402
from src.dup_oracle import CONFIRM_DEFAULT, RECALL_DEFAULT, find_near_duplicates  # noqa: E402
from src.dup_repo_index import DupOracleIndex, build_dup_index  # noqa: E402
from src.grammars import UnsupportedLanguageError  # noqa: E402

TOP_K = 3

_INDEX_CACHE: Dict[str, DupOracleIndex] = {}


def _added_source(tool_input: Dict[str, Any]) -> str:
    """The source the agent is adding, from a Write/Edit/MultiEdit tool_input.

    We look at the added text only (not the whole reconstructed file): a
    complete new function in that text is what we want to check.
    """
    if not isinstance(tool_input, dict):
        return ""
    content = tool_input.get("content")
    if isinstance(content, str):  # Write
        return content
    parts = []
    new_string = tool_input.get("new_string")
    if isinstance(new_string, str):  # Edit
        parts.append(new_string)
    for edit in tool_input.get("edits") or []:  # MultiEdit
        if isinstance(edit, dict) and isinstance(edit.get("new_string"), str):
            parts.append(edit["new_string"])
    return "\n".join(parts)


def _format_block(query_name: str, hits) -> str:
    lines = [f"  `{query_name}` looks equivalent to:"]
    for h in hits:
        why = ""
        if h.shared:
            names = ", ".join(t.split(":", 1)[-1] for t in h.shared[:4])
            why = f"  [shares {names}]"
        lines.append(f"    - {h.name}  {h.path}:{h.lineno}  (logic {h.logic_ratio:.2f}){why}")
    return "\n".join(lines)


def advise(
    added_src: str,
    file_path: str,
    index: Optional[DupOracleIndex],
    *,
    embed_fn,
    repo_root: Optional[str] = None,
    recall: float = RECALL_DEFAULT,
    confirm: float = CONFIRM_DEFAULT,
    top_k: int = TOP_K,
) -> Optional[str]:
    """Return the advisory text if any added function duplicates an indexed one,
    else ``None``. Pure given ``index`` + ``embed_fn`` -> offline-testable.

    ``repo_root`` lets a function's own index entry be skipped: index paths are
    repo-relative (from ``project_index``) while ``file_path`` from the tool
    payload is absolute, so we compare them in the same relative space -- else a
    lightly-edited existing function would be flagged as duplicating itself.
    """
    if not added_src or index is None or len(index) == 0:
        return None
    self_path = os.path.relpath(file_path, repo_root) if repo_root else file_path
    try:
        functions = extract_functions(file_path, added_src)
    except UnsupportedLanguageError:
        return None

    blocks = []
    for name, _line, fsrc in functions:
        tokens = logic_tokens(file_path, fsrc)
        if not tokens:
            continue
        hits = find_near_duplicates(
            embed_fn(fsrc), tokens, index.records, index.embeddings, index.token_df,
            recall=recall, confirm=confirm,
            skip=lambda i, rec, sp=self_path, nm=name: rec.path == sp and rec.name == nm,
        )
        if hits:
            blocks.append(_format_block(name, hits[:top_k]))

    if not blocks:
        return None
    return (
        "[reasoning-core] REUSE CHECK -- the repo already has functions that look "
        "equivalent to what you're adding:\n\n"
        + "\n".join(blocks)
        + "\n\n(advisory only -- reuse or extend if it fits, otherwise proceed.)"
    )


def _read_payload() -> Dict[str, Any]:
    try:
        return json.loads(sys.stdin.read() or "{}")
    except Exception:
        return {}


def _emit_additional_context(text: str, event_name: str) -> None:
    json.dump(
        {"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": text}},
        sys.stdout,
    )


def _get_index(repo_root: str) -> DupOracleIndex:
    index = _INDEX_CACHE.get(repo_root)
    if index is None:
        index = build_dup_index(repo_root)  # real embedder (lazy torch import)
        _INDEX_CACHE[repo_root] = index
    return index


def main() -> None:
    if os.environ.get("RC_DUP_ORACLE") != "1":  # opt-in
        sys.exit(0)
    try:
        payload = _read_payload()
        tool_input = payload.get("tool_input") or {}
        file_path = tool_input.get("file_path") or ""
        added = _added_source(tool_input)
        if not file_path or not added:
            sys.exit(0)
        repo_root = payload.get("cwd") or os.getcwd()
        from src.dup_embed import embed_function

        text = advise(
            added, file_path, _get_index(repo_root), embed_fn=embed_function, repo_root=repo_root
        )
        if text:
            _emit_additional_context(text, payload.get("hookEventName") or "PreToolUse")
    except Exception:
        pass  # fail open -- an advisory must never block an edit
    sys.exit(0)


if __name__ == "__main__":
    main()
