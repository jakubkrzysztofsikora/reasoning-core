"""Phase 5 drift guard: SKILL.md body must be identical across all hosts.

Frontmatter may legitimately differ per host. The body (everything after
the second `---`) is the source-of-truth interpretation guide and must
not silently drift between Claude / Gemini / Copilot / Vibe copies.
"""
import hashlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _body_after_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    rest = text[3:]
    end = rest.find("\n---")
    if end == -1:
        return text
    return rest[end + 4:].lstrip("\n")


def test_skill_md_body_identical_across_hosts():
    paths = [
        REPO / ".claude" / "skills" / "reasoning" / "SKILL.md",
        REPO / ".gemini" / "skills" / "reasoning" / "SKILL.md",
        REPO / ".copilot" / "skills" / "reasoning" / "SKILL.md",
        REPO / ".vibe" / "skills" / "reasoning" / "SKILL.md",
    ]
    hashes = {}
    for p in paths:
        assert p.exists(), f"missing skill copy: {p}"
        body = _body_after_frontmatter(p.read_text())
        hashes[p.parent.parent.name] = hashlib.sha256(body.encode()).hexdigest()
    distinct = set(hashes.values())
    assert len(distinct) == 1, f"SKILL.md body drift detected across hosts: {hashes}"
