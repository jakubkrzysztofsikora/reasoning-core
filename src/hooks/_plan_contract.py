"""Plan-to-contract compiler for reasoning-core.

Turns a PLAN.md (and optional explicit ``.reasoning-core/contract.yaml``) into a
machine-readable contract and evaluates edits against it. The compiler is
designed to be cheap (< 5 ms for path checks) so it can run on every PreToolUse
event.

Two contract sources, in precedence order:

  1. ``.reasoning-core/contract.yaml`` — explicit, operator-authored contract.
  2. ``PLAN.md`` — derived contract: allowed paths are the files mentioned in
     the plan; forbidden paths are files declared under "Files I will
     explicitly NOT touch" or similar negative sections.

The enforcement semantics intentionally degrade gracefully: a missing or
unparseable contract results in ``allowed`` so the gate never blocks users
because of a tooling bug.
"""
from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Violation:
    """A single contract violation."""

    kind: str  # "path", "import", "invariant", "phase"
    rule_id: str
    severity: str  # "deny" or "warn"
    message: str
    line: int = 0
    column: int = 0
    matched_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "rule_id": self.rule_id,
            "severity": self.severity,
            "message": self.message,
            "line": self.line,
            "column": self.column,
            "matched_text": self.matched_text,
        }


@dataclass
class Phase:
    """One implementation phase in a contract."""

    name: str = ""
    active: bool = False
    allowed_paths: List[str] = field(default_factory=list)
    forbidden_paths: List[str] = field(default_factory=list)
    required_tests: List[str] = field(default_factory=list)


@dataclass
class ImportRule:
    """Import constraint."""

    id: str = ""
    severity: str = "deny"
    scope: str = "**"  # glob of files this rule applies to
    forbidden_imports: List[str] = field(default_factory=list)
    message: str = ""


@dataclass
class Invariant:
    """Free-form invariant that can be checked by pattern or description."""

    id: str = ""
    severity: str = "warn"
    description: str = ""
    pattern: str = ""  # regex; advisory-only if empty


@dataclass
class Contract:
    """Compiled contract."""

    source: str = ""
    version: str = "1.0"
    plan_derived: bool = False
    allowed_paths: List[str] = field(default_factory=list)
    forbidden_paths: List[str] = field(default_factory=list)
    required_tests: List[str] = field(default_factory=list)
    import_rules: List[ImportRule] = field(default_factory=list)
    invariants: List[Invariant] = field(default_factory=list)
    phases: List[Phase] = field(default_factory=list)

    # -----------------------------------------------------------------------
    # Loading
    # -----------------------------------------------------------------------

    @classmethod
    def load(
        cls,
        project_root: str,
        plan_text: Optional[str] = None,
        plan_path: Optional[Path] = None,
    ) -> "Contract":
        """Load the best available contract for ``project_root``.

        Precedence:
          1. ``.reasoning-core/contract.yaml``
          2. derived from ``plan_text`` / ``plan_path``
          3. empty contract (allows everything) if neither is available
        """
        root = Path(project_root)

        contract_file = root / ".reasoning-core" / "contract.yaml"
        if contract_file.is_file():
            try:
                data = _parse_yaml(contract_file)
                contract = cls.from_dict(data)
                contract.source = str(contract_file)
                return contract
            except Exception:  # noqa: BLE001
                # Corrupt explicit contract -> degrade to plan-derived or empty.
                pass

        if plan_text is None and plan_path is not None and plan_path.is_file():
            try:
                plan_text = plan_path.read_text(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                plan_text = None

        if plan_text:
            contract = cls.from_plan(plan_text)
            contract.source = str(plan_path) if plan_path else "plan-derived"
            return contract

        return cls(source="empty")

    @classmethod
    def from_dict(cls, data: Dict[str, Any], *, plan_derived: bool = False) -> "Contract":
        """Build a Contract from a parsed YAML/JSON dict."""
        phases = []
        for p in data.get("phases", []) or []:
            if not isinstance(p, dict):
                continue
            phases.append(
                Phase(
                    name=str(p.get("name", "")),
                    active=bool(p.get("active", False)),
                    allowed_paths=_to_str_list(p.get("allowed_paths")),
                    forbidden_paths=_to_str_list(p.get("forbidden_paths")),
                    required_tests=_to_str_list(p.get("required_tests")),
                )
            )

        import_rules = []
        for r in data.get("import_rules", []) or []:
            if not isinstance(r, dict):
                continue
            import_rules.append(
                ImportRule(
                    id=str(r.get("id", "")),
                    severity=_severity(r.get("severity", "deny")),
                    scope=str(r.get("scope", "**")),
                    forbidden_imports=_to_str_list(r.get("forbidden_imports")),
                    message=str(r.get("message", "")),
                )
            )

        invariants = []
        for inv in data.get("invariants", []) or []:
            if not isinstance(inv, dict):
                continue
            invariants.append(
                Invariant(
                    id=str(inv.get("id", "")),
                    severity=_severity(inv.get("severity", "warn")),
                    description=str(inv.get("description", "")),
                    pattern=str(inv.get("pattern", "")),
                )
            )

        return cls(
            version=str(data.get("version", "1.0")),
            plan_derived=plan_derived,
            allowed_paths=_to_str_list(data.get("allowed_paths")),
            forbidden_paths=_to_str_list(data.get("forbidden_paths")),
            required_tests=_to_str_list(data.get("required_tests")),
            import_rules=import_rules,
            invariants=invariants,
            phases=phases,
        )

    @classmethod
    def from_plan(cls, plan_text: str) -> "Contract":
        """Derive a minimal contract from a PLAN.md body.

        - allowed_paths  : file paths mentioned in the plan.
        - forbidden_paths: files listed in explicit negative sections.
        - phases         : Stage/Phase sections, with the first stage active.
        """
        # Prefer the existing extractor so extraction semantics stay identical
        # to the Phase-0 gate and the corpus precision/recall tests.
        try:
            from _plan_paths import distinct_file_paths  # type: ignore

            allowed = distinct_file_paths(plan_text)
        except ImportError:
            allowed = _extract_file_paths(plan_text)

        forbidden = _extract_forbidden_paths(plan_text)
        phases = _extract_phases(plan_text)

        return cls(
            plan_derived=True,
            allowed_paths=sorted(allowed),
            forbidden_paths=sorted(forbidden),
            phases=phases,
        )

    # -----------------------------------------------------------------------
    # Enforcement
    # -----------------------------------------------------------------------

    def active_phase(self) -> Optional[Phase]:
        """Return the first active phase, or None if no phases are active."""
        for phase in self.phases:
            if phase.active:
                return phase
        return None

    def check_path(self, file_path: str) -> Optional[Violation]:
        """Check whether ``file_path`` is allowed by the contract.

        Returns a Violation if the path is forbidden or outside all allowed
        scopes. Returns None if allowed or if the contract is empty.
        """
        if not self.has_any_path_rules():
            return None

        norm = _normalize_path(file_path)

        # Forbidden paths are enforced for explicit contracts only. Plan-derived
        # contracts preserve the original Phase-0 semantics (allowed-path check
        # only) so existing corpus labels and user expectations stay stable.
        if not self.plan_derived:
            for fp in self.forbidden_paths:
                if _path_matches(norm, fp):
                    return Violation(
                        kind="path",
                        rule_id="forbidden_path",
                        severity="deny",
                        message=f"{file_path} is in the contract's forbidden_paths",
                    )

        active = self.active_phase()
        if active:
            if not self.plan_derived:
                for fp in active.forbidden_paths:
                    if _path_matches(norm, fp):
                        return Violation(
                            kind="path",
                            rule_id="phase_forbidden_path",
                            severity="deny",
                            message=(
                                f"{file_path} is forbidden in phase "
                                f"'{active.name}'"
                            ),
                        )
            # Active phase has its own allow-list if non-empty.
            if active.allowed_paths:
                if any(_path_matches(norm, ap) for ap in active.allowed_paths):
                    return None
                return Violation(
                    kind="phase",
                    rule_id="phase_scope",
                    severity="deny",
                    message=(
                        f"{file_path} is outside the allowed scope of phase "
                        f"'{active.name}'"
                    ),
                )

        if self.allowed_paths:
            if any(_path_matches(norm, ap) for ap in self.allowed_paths):
                return None
            return Violation(
                kind="path",
                rule_id="allowed_paths",
                severity="deny",
                message=f"{file_path} is not in the contract's allowed_paths",
            )

        # Plan-derived contract with zero allowed paths: every file is a drift.
        if self.plan_derived:
            return Violation(
                kind="path",
                rule_id="empty_plan",
                severity="deny",
                message=f"{file_path} is not in the empty plan's allowed_paths",
            )

        return None

    def check_imports(
        self, file_path: str, after_src: str
    ) -> List[Violation]:
        """Check whether ``after_src`` introduces forbidden imports."""
        if not after_src or not self.import_rules:
            return []

        norm = _normalize_path(file_path)
        hits: List[Violation] = []

        for rule in self.import_rules:
            if not _path_matches(norm, rule.scope):
                continue
            for forbidden in rule.forbidden_imports:
                hit = _check_python_import(
                    file_path, after_src, rule, forbidden
                ) or _check_js_import(file_path, after_src, rule, forbidden)
                if hit:
                    hits.append(hit)

        return hits

    def check_invariants(
        self, file_path: str, after_src: str
    ) -> List[Violation]:
        """Check pattern-based invariants in ``after_src``."""
        if not after_src or not self.invariants:
            return []

        hits: List[Violation] = []
        for inv in self.invariants:
            if not inv.pattern:
                continue
            try:
                regex = re.compile(inv.pattern)
            except re.error:
                continue
            for m in regex.finditer(after_src):
                line = after_src[: m.start()].count("\n") + 1
                col = m.start() - (after_src.rfind("\n", 0, m.start()) + 1)
                hits.append(
                    Violation(
                        kind="invariant",
                        rule_id=inv.id or "invariant",
                        severity=inv.severity,
                        message=inv.description or f"matches {inv.pattern}",
                        line=line,
                        column=col,
                        matched_text=m.group(0),
                    )
                )
        return hits

    def has_any_path_rules(self) -> bool:
        """True if this contract has any path-based constraints.

        Plan-derived contracts are always considered to have path rules, even
        when the plan is empty, so that an empty PLAN.md correctly warns on
        every edit rather than silently allowing everything.
        """
        if self.plan_derived:
            return True
        if self.allowed_paths or self.forbidden_paths:
            return True
        return any(
            p.allowed_paths or p.forbidden_paths for p in self.phases
        )

    def first_deny(self, violations: List[Violation]) -> Optional[Violation]:
        """Return the first deny-level violation, or None."""
        for v in violations:
            if v.severity == "deny":
                return v
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_path(path: str) -> str:
    """Normalize a path for matching: collapse separators, drop leading ./, collapse .."""
    p = os.path.normpath(path)
    p = p.replace(os.sep, "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def _path_matches(file_path: str, pattern: str) -> bool:
    """Match a normalized file path against a glob pattern.

    Supports both absolute-path matching and repo-relative matching, plus
    ``**`` recursive directory globs which Python's ``fnmatch`` does not
    handle.
    """
    pattern = _normalize_path(pattern)
    norm = _normalize_path(file_path)

    # Direct equality.
    if norm == pattern:
        return True

    # Plain glob (no **) -> fnmatch is sufficient.
    if "**" not in pattern:
        if fnmatch.fnmatch(norm, pattern):
            return True
        # Repo-relative suffix match for absolute file paths.
        if norm.startswith("/") and not pattern.startswith("/"):
            parts = norm.split("/")
            for i in range(1, len(parts)):
                suffix = "/".join(parts[i:])
                if fnmatch.fnmatch(suffix, pattern):
                    return True
                if suffix == pattern:
                    return True
        return False

    # Recursive glob handling.
    # Convert pattern to a regex. ``**`` matches zero or more path components.
    # We handle three forms:
    #   - ``**/foo``  at start: any number of leading directories.
    #   - ``foo/**``  at end:   any depth under foo.
    #   - ``a/**/b``  in middle: zero or more intermediate directories.
    regex_pattern = ""
    i = 0
    while i < len(pattern):
        if pattern[i : i + 3] == "/**":
            # foo/**  or  /** at start.
            regex_pattern += "(?:/.*)?"
            i += 3
        elif pattern[i : i + 3] == "**/":
            regex_pattern += "(?:.*/)*"
            i += 3
        elif pattern[i : i + 2] == "**":
            regex_pattern += ".*"
            i += 2
        elif pattern[i] == "*":
            regex_pattern += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            regex_pattern += "[^/]"
            i += 1
        else:
            regex_pattern += re.escape(pattern[i])
            i += 1

    # Anchor at end; optionally anchor at start or allow repo-relative suffix.
    regex = re.compile("(?:^|/)" + regex_pattern + "$")
    if regex.search(norm):
        return True
    if norm.startswith("/") and not pattern.startswith("/"):
        parts = norm.split("/")
        for i in range(1, len(parts)):
            suffix = "/".join(parts[i:])
            if regex.search(suffix):
                return True
    return False


def _to_str_list(value: Any) -> List[str]:
    """Coerce a value to a list of strings, ignoring empty entries."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(v).strip() for v in value if str(v).strip()]


def _severity(value: Any) -> str:
    """Normalize severity to deny/warn."""
    s = str(value).strip().lower()
    return "deny" if s in ("deny", "denied", "block", "error") else "warn"


# ---------------------------------------------------------------------------
# PLAN.md extraction
# ---------------------------------------------------------------------------

_FILE_PATH_RE = re.compile(
    r"`([A-Za-z0-9_./\-~]+\.[A-Za-z0-9]{1,8})`"
)

_BULLET_PATH_RE = re.compile(
    r"^[\-\*\+]\s*`?([A-Za-z0-9_./\-~]+\.[A-Za-z0-9]{1,8})`?",
    re.MULTILINE,
)

# Match "Files I will explicitly NOT touch" or "Out of scope" sections.
_NEGATIVE_SECTION_RE = re.compile(
    r"^(?:#{1,4}\s*)?(?:Files?\s+I\s+will\s+explicitly\s+NOT\s+touch|"
    r"Files?\s+not\s+in\s+scope|Out\s+of\s+scope|Not\s+touched)[\s:]*\n",
    re.IGNORECASE | re.MULTILINE,
)


def _extract_file_paths(plan_text: str) -> set[str]:
    """Return file paths mentioned in the plan."""
    paths: set[str] = set()
    for m in _FILE_PATH_RE.finditer(plan_text):
        paths.add(m.group(1))
    for m in _BULLET_PATH_RE.finditer(plan_text):
        paths.add(m.group(1))
    return paths


def _extract_forbidden_paths(plan_text: str) -> set[str]:
    """Return paths declared as explicitly not touched / out of scope."""
    forbidden: set[str] = set()
    for m in _NEGATIVE_SECTION_RE.finditer(plan_text):
        section_start = m.end()
        # Read until next heading of same or higher level, or end of doc.
        rest = plan_text[section_start:]
        lines: List[str] = []
        for line in rest.splitlines():
            if line.startswith("#"):
                break
            lines.append(line)
        section = "\n".join(lines)
        for pm in _FILE_PATH_RE.finditer(section):
            forbidden.add(pm.group(1))
        for pm in _BULLET_PATH_RE.finditer(section):
            forbidden.add(pm.group(1))
    return forbidden


_PHASE_RE = re.compile(
    r"^(?:#{1,4}\s*)?(?:Stage|Phase)\s+(\d+)[\s:\-–]*(.*)$",
    re.IGNORECASE | re.MULTILINE,
)


def _extract_phases(plan_text: str) -> List[Phase]:
    """Extract Stage/Phase sections; the first one is marked active."""
    phases: List[Phase] = []
    for i, m in enumerate(_PHASE_RE.finditer(plan_text)):
        name = f"Stage {m.group(1)}"
        subtitle = m.group(2).strip()
        if subtitle:
            name = f"{name}: {subtitle}"
        phases.append(
            Phase(
                name=name,
                active=(i == 0),
                allowed_paths=[],
                forbidden_paths=[],
            )
        )
    return phases


# ---------------------------------------------------------------------------
# Import checking
# ---------------------------------------------------------------------------


def _check_python_import(
    file_path: str, after_src: str, rule: ImportRule, target: str
) -> Optional[Violation]:
    """Check if Python source imports the forbidden target."""
    if not file_path.endswith(".py"):
        return None

    try:
        import ast

        tree = ast.parse(after_src)
    except SyntaxError:
        return _check_python_import_regex(file_path, after_src, rule, target)

    target_parts = target.split(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _import_matches(alias.name, target_parts):
                    return _import_violation(rule, target, node, alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            full = module
            if _import_matches(module, target_parts):
                return _import_violation(rule, target, node, module)
            for alias in node.names:
                full = f"{module}.{alias.name}" if module else alias.name
                if _import_matches(full, target_parts):
                    return _import_violation(rule, target, node, full)
    return None


def _check_python_import_regex(
    file_path: str, after_src: str, rule: ImportRule, target: str
) -> Optional[Violation]:
    """Regex fallback when the file does not parse."""
    if not file_path.endswith(".py"):
        return None
    escaped = re.escape(target)
    pat = re.compile(
        rf"^\s*(?:import\s+{escaped}(?:\.\w+)*|from\s+{escaped}(?:\.\w+)*\s+import)\b",
        re.MULTILINE,
    )
    m = pat.search(after_src)
    if m is None:
        return None
    line = after_src[: m.start()].count("\n") + 1
    return Violation(
        kind="import",
        rule_id=rule.id or "forbid_import",
        severity=rule.severity,
        message=rule.message or f"Import of {target} forbidden",
        line=line,
        column=0,
        matched_text=m.group(0).strip(),
    )


def _check_js_import(
    file_path: str, after_src: str, rule: ImportRule, target: str
) -> Optional[Violation]:
    """Check JS/TS source imports the forbidden target."""
    if not any(file_path.endswith(ext) for ext in (".js", ".ts", ".tsx", ".jsx")):
        return None

    patterns = [
        re.compile(
            r"""import\s+(?:(?:\{[^}]*\}|\*\s+as\s+\w+|\w+)\s+from\s+)?["']([^"']+)["']""",
            re.MULTILINE,
        ),
        re.compile(r"""require\s*\(\s*["']([^"']+)["']\s*\)""", re.MULTILINE),
    ]

    for pat in patterns:
        for m in pat.finditer(after_src):
            imported = m.group(1)
            if imported == target or imported.endswith("/" + target):
                line = after_src[: m.start()].count("\n") + 1
                col = m.start() - (after_src.rfind("\n", 0, m.start()) + 1)
                return Violation(
                    kind="import",
                    rule_id=rule.id or "forbid_import",
                    severity=rule.severity,
                    message=rule.message or f"Import of {target} forbidden",
                    line=line,
                    column=col,
                    matched_text=imported,
                )
    return None


def _import_matches(import_name: str, target_parts: List[str]) -> bool:
    """Prefix-based import match."""
    import_parts = import_name.split(".")
    if len(import_parts) < len(target_parts):
        return False
    for i, part in enumerate(target_parts):
        if import_parts[i] != part:
            return False
    return True


def _import_violation(
    rule: ImportRule, target: str, node: Any, matched: str
) -> Violation:
    return Violation(
        kind="import",
        rule_id=rule.id or "forbid_import",
        severity=rule.severity,
        message=rule.message or f"Import of {target} forbidden",
        line=getattr(node, "lineno", 1),
        column=getattr(node, "col_offset", 0),
        matched_text=matched,
    )


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def _parse_yaml(path: Path) -> Dict[str, Any]:
    """Parse a YAML file; fall back to basic parser only in tests."""
    src = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        return yaml.safe_load(src) or {}
    except ImportError:
        if os.environ.get("RC_PLAN_CONTRACT_ALLOW_BASIC_YAML") == "1":
            return _basic_yaml_parse(src)
        raise


def _basic_yaml_parse(src: str) -> Dict[str, Any]:
    """Minimal YAML parser for contract files (fallback, tests only)."""
    lines = src.splitlines()
    root: Dict[str, Any] = {}
    stack: List[Tuple[int, Any, str]] = [(-1, root, "dict")]
    pending_key: Optional[str] = None
    pending_indent = -1

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        if pending_key is not None:
            if indent > pending_indent:
                if stripped.startswith("- "):
                    new_list: List[Any] = []
                    stack[-1][1][pending_key] = new_list
                    stack.append((pending_indent, new_list, "list"))
                else:
                    new_dict: Dict[str, Any] = {}
                    stack[-1][1][pending_key] = new_dict
                    stack.append((pending_indent, new_dict, "dict"))
            else:
                stack[-1][1][pending_key] = {}
            pending_key = None

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
                new_item: Dict[str, Any] = {}
                top_container.append(new_item)
                stack.append((indent, new_item, "dict"))
                if val == "":
                    pending_key = key
                    pending_indent = indent
                else:
                    new_item[key] = _yaml_scalar(val)
            else:
                top_container.append(_yaml_scalar(item_text))
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
                top_container[key] = _yaml_scalar(val)

        i += 1

    if pending_key is not None and stack:
        stack[-1][1][pending_key] = {}

    return root


def _yaml_scalar(val: str) -> Any:
    """Parse a YAML scalar."""
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


__all__ = [
    "Contract",
    "Phase",
    "ImportRule",
    "Invariant",
    "Violation",
]
