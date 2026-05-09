"""Tests for src.mcp_diff_validator.validate_unified_diff."""
from __future__ import annotations

import unittest

from src.mcp_diff_validator import validate_unified_diff


CLEAN = (
    "diff --git a/x.py b/x.py\n"
    "--- a/x.py\n+++ b/x.py\n"
    "@@ -1,3 +1,3 @@\n"
    " a\n-b\n+c\n d\n"
)


class TestValidateUnifiedDiff(unittest.TestCase):
    def test_clean_passes(self):
        r = validate_unified_diff(CLEAN)
        self.assertTrue(r["ok"], r["errors"])
        self.assertEqual(r["hunk_count"], 1)
        self.assertEqual(r["repaired_patch"], CLEAN)

    def test_empty_input(self):
        r = validate_unified_diff("")
        self.assertFalse(r["ok"])
        self.assertEqual(r["errors"][0]["kind"], "empty_input")

    def test_missing_prefix_caught(self):
        # Pylint-4661 shape: long minus line wrapped onto two physical lines.
        bad = (
            "diff --git a/p.py b/p.py\n"
            "--- a/p.py\n+++ b/p.py\n"
            "@@ -1,4 +1,4 @@\n"
            " a\n"
            "-PYLINT_HOME = os.path.join(USER_HOME,\n"
            "\".pylint.d\")\n"
            "+PYLINT_HOME = appdirs.user_cache_dir(\"pylint\")\n"
            " z\n"
        )
        r = validate_unified_diff(bad)
        self.assertFalse(r["ok"])
        kinds = {e["kind"] for e in r["errors"]}
        self.assertIn("missing_prefix", kinds)
        # Repair should rejoin the orphan line onto the previous body line.
        rp = r["repaired_patch"]
        self.assertIsNotNone(rp)
        for ln in rp.splitlines():
            if ln.startswith(("@@", "diff", "---", "+++")) or not ln:
                continue
            self.assertIn(ln[:1], (" ", "+", "-", "\\"),
                          f"unprefixed body line: {ln!r}")

    def test_count_mismatch_caught(self):
        bad = (
            "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
            "@@ -1,7 +1,7 @@\n a\n-b\n+c\n d\n"
        )
        r = validate_unified_diff(bad)
        self.assertFalse(r["ok"])
        self.assertIn("count_mismatch", {e["kind"] for e in r["errors"]})

    def test_empty_context_caught(self):
        bad = (
            "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
            "@@ -1,3 +1,3 @@\n a\n\n d\n"
        )
        r = validate_unified_diff(bad)
        kinds = {e["kind"] for e in r["errors"]}
        self.assertIn("empty_context", kinds)
        # Repair must convert bare empty into single-space context.
        rp = r["repaired_patch"]
        self.assertIsNotNone(rp)
        self.assertNotIn("\n\n d\n", rp)

    def test_repair_idempotent_on_clean(self):
        r = validate_unified_diff(CLEAN)
        self.assertEqual(r["repaired_patch"], CLEAN)

    def test_no_hunks(self):
        r = validate_unified_diff("diff --git a/x b/x\n--- a/x\n+++ b/x\n")
        self.assertFalse(r["ok"])
        self.assertIn("missing_hunk", {e["kind"] for e in r["errors"]})

    def test_never_raises_on_garbage(self):
        for inp in (None, 42, b"bytes", "totally not a diff", "@@ broken @@"):
            r = validate_unified_diff(inp)  # type: ignore[arg-type]
            self.assertIn("ok", r)
            self.assertIsInstance(r["errors"], list)


if __name__ == "__main__":
    unittest.main()
