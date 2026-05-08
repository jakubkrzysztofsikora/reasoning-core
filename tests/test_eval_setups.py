"""Phase 6: per-host eval setup definitions parse + cover all 4 hosts."""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SETUPS = REPO / "eval" / "setups"


def _yaml_load(text):
    """Tiny YAML-subset parser sufficient for the setup files (no deps)."""
    import re
    out = {}
    current_key = None
    multiline = []
    for raw in text.splitlines():
        if raw.startswith("#") or not raw.strip():
            continue
        if multiline is not None and raw.startswith("  ") and current_key in ("notes",):
            multiline.append(raw[2:] if len(raw) > 2 else "")
            continue
        m = re.match(r"^([a-zA-Z_][\w]*):\s*(.*)$", raw)
        if not m:
            continue
        if multiline and current_key:
            out[current_key] = "\n".join(multiline).rstrip()
            multiline = []
        current_key = m.group(1)
        val = m.group(2)
        if val == "|":
            multiline = []
        elif val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            if not inner:
                out[current_key] = []
            else:
                out[current_key] = [s.strip().strip('"') for s in inner.split(",")]
        elif val == "":
            # nested dict — we only care about flat keys for this test
            out[current_key] = {}
        else:
            out[current_key] = val.strip().strip('"')
    if multiline and current_key:
        out[current_key] = "\n".join(multiline).rstrip()
    return out


def test_all_four_hosts_have_setup_files():
    expected = {"claude", "gemini", "copilot", "vibe"}
    found = {p.stem for p in SETUPS.glob("*.yml")}
    assert expected == found, f"missing setups: {expected - found}, extras: {found - expected}"


@pytest.mark.parametrize("host", ["claude", "gemini", "copilot", "vibe"])
def test_setup_has_required_fields(host):
    text = (SETUPS / f"{host}.yml").read_text()
    parsed = _yaml_load(text)
    for required in ("host", "binary", "trust_args"):
        assert required in parsed, f"{host}.yml missing field: {required}"
    assert parsed["host"] == host


def test_dataset_has_seed_fixtures():
    """sidecar_block_pairs.jsonl must have at least 6 fixtures across
    Python / TypeScript / C# / SQL with both block and allow expectations."""
    import json
    ds = REPO / "eval" / "datasets" / "sidecar_block_pairs.jsonl"
    rows = [json.loads(l) for l in ds.read_text().splitlines() if l.strip()]
    assert len(rows) >= 6
    decisions = {r["expected_decision"] for r in rows}
    assert {"block", "allow"} <= decisions, "must have both block and allow expectations"
    languages = {r["language"] for r in rows}
    assert len(languages) >= 3, f"need ≥3 languages, got {languages}"
