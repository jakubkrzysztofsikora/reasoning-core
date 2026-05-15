"""Architectural rule engine for reasoning-core.

Evaluates source edits against declarative rules in .reasoning-core/rules.yaml.
Supports two rule types:
  - forbid_import:  forbid specific import patterns (Python + JS/TS)
  - forbid_pattern: forbid regex patterns in source

Hard constraints:
  - ≤50 rules total
  - ≤5ms per rule (warn if exceeded)
  - fail-closed by default; RC_RULE_ENGINE_LENIENT=1 → warn-only
  - schema error → exit(2) unless lenient

Usage:
    rules = load_rules("/path/to/project")
    hits = evaluate_edit("src/hooks/foo.py", before_src, after_src, "python", rules)
"""
from __future__ import annotations

import ast
import fnmatch
import logging
import math
import os
import re
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REQUIRED_CORPUS_VERSION = "v1"
_MAX_RULES = 50
_MAX_MS_PER_RULE = 5.0

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RuleEngineError(RuntimeError):
    """Raised on schema mismatch or fatal rule engine error."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleHit:
    """A single rule violation."""

    rule_id: str
    rule_type: str
    severity: str
    message: str
    line: int
    column: int
    matched_text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_type": self.rule_type,
            "severity": self.severity,
            "message": self.message,
            "line": self.line,
            "column": self.column,
            "matched_text": self.matched_text,
        }


# ---------------------------------------------------------------------------
# Mtime-cached rule loader
# ---------------------------------------------------------------------------

# Cache key carries (mtime_ns, size, inode) rather than mtime alone. ext3,
# FAT, and some NFS mounts only carry second-granularity mtimes; a same-second
# rewrite of rules.yaml would share the cached mtime and serve stale rules.
_RULES_CACHE: dict[str, tuple[tuple[int, int, int], list[dict], str]] = {}
_WARNED_ONCE: set[str] = set()


def _warn_once(key: str, msg: str) -> None:
    if key in _WARNED_ONCE:
        return
    _WARNED_ONCE.add(key)
    logger.warning(msg)


def _find_rules_file(project_root: str) -> Path | None:
    """Locate .reasoning-core/rules.yaml under project_root."""
    p = Path(project_root) / ".reasoning-core" / "rules.yaml"
    if p.is_file():
        return p
    return None


def _parse_yaml(path: Path) -> dict:
    """Parse rules.yaml. Requires PyYAML in production.

    The rule engine is a security gate; relying on the hand-rolled minimal
    parser as a silent fallback creates a divergence surface (a rules file
    that parses one way under PyYAML and another way under the fallback
    could ship rules that *appear* enforced but never evaluate). Fail
    closed when PyYAML is missing; operators can install it with
    ``pip install PyYAML>=6.0`` or, for tests/dev only, opt into the
    fallback via ``RC_RULE_ENGINE_ALLOW_BASIC_YAML=1``.
    """
    src = path.read_text(encoding="utf-8")
    try:
        import yaml
    except ImportError:
        if os.environ.get("RC_RULE_ENGINE_ALLOW_BASIC_YAML") == "1":
            logger.warning(
                "PyYAML missing; falling back to _basic_yaml_parse because "
                "RC_RULE_ENGINE_ALLOW_BASIC_YAML=1. Do not use in production."
            )
            return _basic_yaml_parse(src)
        raise RuleEngineError(
            "PyYAML is required to load rules.yaml. "
            "Install with `pip install PyYAML>=6.0`, "
            "or set RC_RULE_ENGINE_ALLOW_BASIC_YAML=1 to opt into the "
            "limited fallback parser (tests/dev only)."
        )
    return yaml.safe_load(src)


def _basic_yaml_parse(src: str) -> dict:
    """Parse a minimal YAML subset sufficient for rules.yaml and rules.schema.yaml.

    Handles arbitrary indent-based nesting via an explicit context stack:
    top-level scalars, nested dicts of any depth, lists of scalars, and lists
    of dicts whose items themselves contain nested structures. The previous
    flat implementation dropped nested keys (e.g. ``schema.metadata`` and
    ``schema.rule_types.forbid_import.fields``).

    Not supported (intentional): flow style (``{}``/``[]``), anchors and
    aliases, block scalars (``|``/``>``), merge keys. Use PyYAML when those
    features are needed -- this parser is only the fallback for environments
    where PyYAML is missing.
    """
    lines = src.splitlines()
    root: dict[str, Any] = {}
    # Stack frames are (parent_indent, container, kind). The root frame uses
    # parent_indent = -1 so any indent-0 line is treated as a child.
    stack: list[tuple[int, Any, str]] = [(-1, root, "dict")]
    pending_key: str | None = None
    pending_indent: int = -1

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        # Resolve a `key:` left dangling by the previous line: the child's
        # indent and shape decide whether key maps to a nested dict or list.
        if pending_key is not None:
            if indent > pending_indent:
                if stripped.startswith("- "):
                    new_list: list[Any] = []
                    stack[-1][1][pending_key] = new_list
                    stack.append((pending_indent, new_list, "list"))
                else:
                    new_dict: dict[str, Any] = {}
                    stack[-1][1][pending_key] = new_dict
                    stack.append((pending_indent, new_dict, "dict"))
            else:
                stack[-1][1][pending_key] = {}
            pending_key = None

        # Drop frames whose parent_indent is at or beyond the current indent
        # -- we've exited their scope.
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if not stack:
            stack.append((-1, root, "dict"))

        _top_indent, top_container, top_kind = stack[-1]

        if stripped.startswith("- "):
            if top_kind != "list":
                i += 1
                continue
            item_text = stripped[2:].rstrip()
            if ":" in item_text:
                key, val = item_text.split(":", 1)
                key = key.strip()
                val = val.strip()
                new_item: dict[str, Any] = {}
                top_container.append(new_item)
                stack.append((indent, new_item, "dict"))
                if val == "":
                    pending_key = key
                    pending_indent = indent
                else:
                    new_item[key] = _yaml_val(val)
            else:
                top_container.append(_yaml_val(item_text))
            i += 1
            continue

        if ":" in stripped:
            key, val = stripped.split(":", 1)
            key = key.strip()
            val = val.strip()
            if top_kind != "dict":
                i += 1
                continue
            if val == "":
                pending_key = key
                pending_indent = indent
            else:
                top_container[key] = _yaml_val(val)

        i += 1

    if pending_key is not None and stack:
        stack[-1][1][pending_key] = {}

    return root


def _yaml_val(val: str) -> Any:
    """Parse a YAML scalar value."""
    val = val.strip()
    if val.startswith('"') and val.endswith('"'):
        return val[1:-1]
    if val.startswith("'") and val.endswith("'"):
        return val[1:-1]
    if val.lower() in ("true", "yes"):
        return True
    if val.lower() in ("false", "no"):
        return False
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val


def load_rules(project_root: str) -> list[dict]:
    """Load rules from .reasoning-core/rules.yaml with mtime caching.

    Returns empty list if file missing.
    Raises RuleEngineError if schema invalid (unless lenient).
    """
    path = _find_rules_file(project_root)
    if path is None:
        return []

    path_str = str(path)
    try:
        st = path.stat()
        cache_key = (st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        return []

    cached = _RULES_CACHE.get(path_str)
    if cached is not None:
        cached_key, rules, _ = cached
        if cached_key == cache_key:
            return rules

    try:
        data = _parse_yaml(path)
    except Exception as exc:
        if _is_lenient():
            _warn_once(f"parse:{path_str}", f"rules.yaml parse failed: {exc}")
            return []
        raise RuleEngineError(f"rules.yaml parse failed: {exc}")

    # Validate corpus_version
    corpus_version = data.get("corpus_version", "")
    if corpus_version != _REQUIRED_CORPUS_VERSION:
        msg = (
            f"rules.yaml corpus_version={corpus_version!r} != "
            f"required {_REQUIRED_CORPUS_VERSION!r}"
        )
        if _is_lenient():
            _warn_once(f"version:{path_str}", msg)
            return []
        raise RuleEngineError(msg)

    rules = data.get("rules", [])
    if not isinstance(rules, list):
        msg = "rules.yaml 'rules' must be a list"
        if _is_lenient():
            _warn_once(f"rules_type:{path_str}", msg)
            return []
        raise RuleEngineError(msg)

    if len(rules) > _MAX_RULES:
        msg = f"rules.yaml has {len(rules)} rules; max is {_MAX_RULES}"
        if _is_lenient():
            _warn_once(f"max_rules:{path_str}", msg)
            rules = rules[:_MAX_RULES]
        else:
            raise RuleEngineError(msg)

    # Validate each rule has required fields
    required_fields = {"id", "type", "severity", "language"}
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        missing = required_fields - set(rule.keys())
        if missing:
            msg = f"rule {rule.get('id', '?')} missing fields: {missing}"
            if _is_lenient():
                _warn_once(f"rule:{rule.get('id')}", msg)
            else:
                raise RuleEngineError(msg)

    _RULES_CACHE[path_str] = (cache_key, rules, path_str)
    logger.info("Loaded %d rules from %s", len(rules), path_str)
    return rules


def _is_lenient() -> bool:
    return os.environ.get("RC_RULE_ENGINE_LENIENT") == "1"


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate_edit(
    file_path: str,
    before_src: str,
    after_src: str,
    lang: str,
    rules: list[dict],
) -> list[RuleHit]:
    """Evaluate a single edit against all applicable rules.

    Only checks the AFTER state. ``before_src`` is used for the bypass
    provenance check (# rc:skip-rule:<id>).

    Returns list of RuleHit (empty = clean).
    """
    hits: list[RuleHit] = []

    # Collect bypass comments from before_src
    bypass_rules = _extract_bypass_comments(before_src)

    for rule in rules:
        t0 = time.monotonic()
        try:
            hit = _eval_rule(file_path, after_src, lang, rule, bypass_rules)
            if hit is not None:
                hits.append(hit)
        except Exception as exc:
            # Fail-closed: an evaluator that crashes on a malformed source or
            # an unexpected language construct must not silently let the edit
            # through. Synthesize a deny hit so the dispatcher sees an
            # explicit violation and the caller can decide policy.
            logger.warning("Rule %s evaluation failed: %s", rule.get("id", "?"), exc)
            hits.append(RuleHit(
                rule_id=rule.get("id", "?"),
                rule_type=rule.get("type", "unknown"),
                severity="deny",
                message=f"Rule evaluator raised {type(exc).__name__}: {exc}",
                line=0,
                column=0,
                matched_text="",
            ))

        elapsed_ms = (time.monotonic() - t0) * 1000.0
        if elapsed_ms > _MAX_MS_PER_RULE:
            logger.warning(
                "Rule %s took %.1fms (budget: %.1fms)",
                rule.get("id", "?"), elapsed_ms, _MAX_MS_PER_RULE,
            )

    return hits


def _extract_bypass_comments(src: str) -> set[str]:
    """Extract ``# rc:skip-rule:<id>`` or ``// rc:skip-rule:<id>`` markers.

    Both Python (``#``) and C-family/JS/TS (``//``) line-comment forms are
    accepted so the rule engine's bypass UX works in every language it can
    evaluate.
    """
    bypassed: set[str] = set()
    for line in src.splitlines():
        m = re.search(r'(?:#|//)\s*rc:skip-rule:([A-Za-z0-9_-]+)', line)
        if m:
            bypassed.add(m.group(1))
    return bypassed


def _eval_rule(
    file_path: str,
    after_src: str,
    lang: str,
    rule: dict,
    bypass_rules: set[str],
) -> RuleHit | None:
    """Evaluate a single rule. Returns RuleHit if violated, None if clean."""
    rule_id = rule.get("id", "")
    rule_type = rule.get("type", "")
    severity = rule.get("severity", "deny")
    rule_lang = rule.get("language", "")
    scope = rule.get("scope", "")
    message = rule.get("message", f"Rule {rule_id} violated")

    # Check language match
    if not _language_matches(lang, rule_lang):
        return None

    # Check scope match. Hook payloads typically carry absolute paths while
    # ``scope`` globs in rules.yaml are repo-relative (e.g. ``src/hooks/**``).
    # Match against both forms so the rule lands either way.
    if scope:
        normalized = file_path.replace(os.sep, "/")
        repo_root = os.environ.get("CLAUDE_PROJECT_DIR", "").replace(os.sep, "/")
        rel_form = normalized
        if repo_root and normalized.startswith(repo_root.rstrip("/") + "/"):
            rel_form = normalized[len(repo_root.rstrip("/")) + 1:]
        if not (fnmatch.fnmatch(normalized, scope) or fnmatch.fnmatch(rel_form, scope)):
            return None

    # Check bypass
    if rule_id in bypass_rules:
        return None

    if rule_type == "forbid_import":
        return _eval_forbid_import(file_path, after_src, lang, rule)
    elif rule_type == "forbid_pattern":
        return _eval_forbid_pattern(file_path, after_src, rule)
    else:
        logger.debug("Unknown rule type: %s", rule_type)
        return None


# ---------------------------------------------------------------------------
# Language matching
# ---------------------------------------------------------------------------


def _language_matches(file_lang: str, rule_lang: str) -> bool:
    """Check if a file's language matches the rule's language scope.

    javascript rules also apply to typescript and tsx files (JS superset).
    """
    file_lang = file_lang.lower().strip()
    rule_lang = rule_lang.lower().strip()
    if file_lang == rule_lang:
        return True
    # javascript rules cover ts and tsx
    if rule_lang == "javascript" and file_lang in ("typescript", "tsx"):
        return True
    return False


# ---------------------------------------------------------------------------
# forbid_import evaluator
# ---------------------------------------------------------------------------


def _eval_forbid_import(
    file_path: str,
    after_src: str,
    lang: str,
    rule: dict,
) -> RuleHit | None:
    """Evaluate a forbid_import rule."""
    target = rule.get("target", "")
    if not target:
        return None

    if lang == "python" or file_path.endswith(".py"):
        return _eval_python_import(file_path, after_src, rule, target)
    elif lang in ("javascript", "typescript", "tsx") or any(
        file_path.endswith(ext) for ext in (".js", ".ts", ".tsx")
    ):
        return _eval_ts_js_import(file_path, after_src, rule, target)
    return None


def _eval_python_import(
    file_path: str,
    after_src: str,
    rule: dict,
    target: str,
) -> RuleHit | None:
    """Check if Python source imports the forbidden target."""
    try:
        tree = ast.parse(after_src)
    except SyntaxError:
        return None

    target_parts = target.split(".")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _import_matches(alias.name, target_parts):
                    return RuleHit(
                        rule_id=rule.get("id", ""),
                        rule_type="forbid_import",
                        severity=rule.get("severity", "deny"),
                        message=rule.get("message", f"Import of {target} forbidden"),
                        line=getattr(node, "lineno", 1),
                        column=getattr(node, "col_offset", 0),
                        matched_text=alias.name,
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            module_parts = module.split(".")
            # Check if the module itself matches
            if _import_matches(module, target_parts):
                return RuleHit(
                    rule_id=rule.get("id", ""),
                    rule_type="forbid_import",
                    severity=rule.get("severity", "deny"),
                    message=rule.get("message", f"Import from {target} forbidden"),
                    line=getattr(node, "lineno", 1),
                    column=getattr(node, "col_offset", 0),
                    matched_text=module,
                )
            # Check imported names
            for alias in node.names:
                full_name = f"{module}.{alias.name}" if module else alias.name
                if _import_matches(full_name, target_parts):
                    return RuleHit(
                        rule_id=rule.get("id", ""),
                        rule_type="forbid_import",
                        severity=rule.get("severity", "deny"),
                        message=rule.get("message", f"Import of {target} forbidden"),
                        line=getattr(node, "lineno", 1),
                        column=getattr(node, "col_offset", 0),
                        matched_text=full_name,
                    )
    return None


def _import_matches(import_name: str, target_parts: list[str]) -> bool:
    """Check if an import name matches the target pattern.

    Matches are prefix-based: target "src.sidecar_supervisor" matches
    "src.sidecar_supervisor" and "src.sidecar_supervisor.foo".
    """
    import_parts = import_name.split(".")
    if len(import_parts) < len(target_parts):
        return False
    for i, part in enumerate(target_parts):
        if import_parts[i] != part:
            return False
    return True


def _eval_ts_js_import(
    file_path: str,
    after_src: str,
    rule: dict,
    target: str,
) -> RuleHit | None:
    """Check if JS/TS source imports the forbidden target."""
    patterns = [
        re.compile(
            r'''import\s+(?:(?:\{[^}]*\}|\*\s+as\s+\w+|\w+)\s+from\s+)?["']([^"']+)["']''',
            re.MULTILINE,
        ),
        re.compile(r'''require\s*\(\s*["']([^"']+)["']\s*\)''', re.MULTILINE),
    ]

    for pat in patterns:
        for m in pat.finditer(after_src):
            imported = m.group(1)
            # Check exact match or relative path match
            if imported == target:
                line = after_src[:m.start()].count("\n") + 1
                return RuleHit(
                    rule_id=rule.get("id", ""),
                    rule_type="forbid_import",
                    severity=rule.get("severity", "deny"),
                    message=rule.get("message", f"Import of {target} forbidden"),
                    line=line,
                    column=m.start() - after_src.rfind("\n", 0, m.start()),
                    matched_text=imported,
                )
            # Relative import check. ``lstrip("./")`` strips characters, not
            # the prefix, so a naive ``imported.endswith(stripped)`` would
            # match ``custom_supervisor`` for target ``../supervisor``. Anchor
            # the match at a ``/`` boundary so module-name suffix collisions
            # don't false-positive.
            if target.startswith("."):
                tail = target.lstrip("./")
                if imported == target or imported.endswith("/" + tail):
                    line = after_src[:m.start()].count("\n") + 1
                    return RuleHit(
                        rule_id=rule.get("id", ""),
                        rule_type="forbid_import",
                        severity=rule.get("severity", "deny"),
                        message=rule.get("message", f"Import of {target} forbidden"),
                        line=line,
                        column=m.start() - after_src.rfind("\n", 0, m.start()),
                        matched_text=imported,
                    )
    return None


# ---------------------------------------------------------------------------
# forbid_pattern evaluator
# ---------------------------------------------------------------------------


class _RuleTimeout(Exception):
    """Raised by the SIGALRM watchdog when a regex match exceeds the budget."""


def _alarm_handler(signum, frame):  # noqa: ARG001 -- signal API
    raise _RuleTimeout()


# Hard caps to keep a malicious or accidental rules.yaml entry from hanging
# the gate. The 5ms-per-rule soft budget in evaluate_edit() is for the
# happy path; this is the catastrophic-backtracking backstop.
_MAX_PATTERN_LEN = 512
_MAX_SOURCE_BYTES_FOR_REGEX = 2 * 1024 * 1024  # 2 MiB
_REGEX_TIMEOUT_SECONDS = 0.25


def _eval_forbid_pattern(
    file_path: str,
    after_src: str,
    rule: dict,
) -> RuleHit | None:
    """Evaluate a ``forbid_pattern`` rule under ReDoS protection.

    Rules in ``.reasoning-core/rules.yaml`` are operator-authored, but a
    malicious or accidental catastrophic-backtracking regex (``(a+)+$``,
    nested unbounded groups) can hang the gate process and stall every
    subsequent edit. Three layers of defense:

    1. ``len(pattern) <= _MAX_PATTERN_LEN`` -- bounded compilation cost.
    2. ``len(after_src) <= _MAX_SOURCE_BYTES_FOR_REGEX`` -- truncate inputs
       that would amplify worst-case match time.
    3. ``signal.SIGALRM`` watchdog with ``_REGEX_TIMEOUT_SECONDS`` hard
       cap. Unix-only; on platforms without SIGALRM the size limits alone
       carry the protection.

    A timeout is surfaced as a synthesized deny hit so the caller can act
    on it -- silently swallowing the failure would let a malformed rule
    become a permanent gate-disable.
    """
    pattern = rule.get("pattern", "")
    rule_id = rule.get("id", "?")
    if not pattern:
        return None
    if len(pattern) > _MAX_PATTERN_LEN:
        logger.warning(
            "Rule %s pattern is %d chars (cap %d); refusing to compile",
            rule_id, len(pattern), _MAX_PATTERN_LEN,
        )
        return RuleHit(
            rule_id=rule_id,
            rule_type="forbid_pattern",
            severity="deny",
            message=f"Pattern exceeds {_MAX_PATTERN_LEN} chars; rejected",
            line=0,
            column=0,
            matched_text="",
        )

    try:
        regex = re.compile(pattern)
    except re.error as exc:
        logger.debug("Invalid regex in rule %s: %s", rule_id, exc)
        return None

    bounded_src = after_src
    if len(after_src) > _MAX_SOURCE_BYTES_FOR_REGEX:
        bounded_src = after_src[:_MAX_SOURCE_BYTES_FOR_REGEX]

    have_alarm = hasattr(signal, "SIGALRM") and hasattr(signal, "setitimer")
    prev_handler = None
    if have_alarm:
        try:
            prev_handler = signal.signal(signal.SIGALRM, _alarm_handler)
            signal.setitimer(signal.ITIMER_REAL, _REGEX_TIMEOUT_SECONDS)
        except ValueError:
            # signal.signal raises ValueError outside the main thread.
            have_alarm = False
            prev_handler = None

    try:
        m = regex.search(bounded_src)
        if m is None:
            return None
        line = bounded_src[:m.start()].count("\n") + 1
        col = m.start() - bounded_src.rfind("\n", 0, m.start())
        return RuleHit(
            rule_id=rule.get("id", ""),
            rule_type="forbid_pattern",
            severity=rule.get("severity", "deny"),
            message=rule.get("message", f"Pattern {pattern} forbidden"),
            line=line,
            column=col,
            matched_text=m.group(0),
        )
    except _RuleTimeout:
        logger.warning(
            "Rule %s exceeded %.0fms regex timeout (likely ReDoS); denying edit",
            rule_id, _REGEX_TIMEOUT_SECONDS * 1000,
        )
        return RuleHit(
            rule_id=rule_id,
            rule_type="forbid_pattern",
            severity="deny",
            message=(
                f"Pattern evaluation exceeded "
                f"{int(_REGEX_TIMEOUT_SECONDS * 1000)}ms (likely ReDoS)"
            ),
            line=0,
            column=0,
            matched_text="",
        )
    finally:
        if have_alarm:
            signal.setitimer(signal.ITIMER_REAL, 0)
            if prev_handler is not None:
                signal.signal(signal.SIGALRM, prev_handler)


# ---------------------------------------------------------------------------
# Public API helpers
# ---------------------------------------------------------------------------


def format_hits(hits: list[RuleHit]) -> str:
    """Format rule hits for human-readable display."""
    if not hits:
        return "No rule violations."
    lines = [f"Rule violations ({len(hits)}):"]
    for hit in hits:
        lines.append(
            f"  [{hit.severity}] {hit.rule_id} (L{hit.line}): {hit.message}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# lenient-mode exit handler
# ---------------------------------------------------------------------------


def _exit_on_schema_error(msg: str) -> None:
    """Fail-closed: exit(2) on schema error unless lenient."""
    if _is_lenient():
        _warn_once("lenient", f"Schema error (lenient mode): {msg}")
        return
    sys.stderr.write(f"[rule_engine] {msg}\n")
    sys.exit(2)


__all__ = [
    "RuleEngineError",
    "RuleHit",
    "evaluate_edit",
    "format_hits",
    "load_rules",
]
