"""Tests for ``rc init`` / ``rc init-uninstall`` (the wheel-installed replacement
for ``install.sh`` / ``uninstall.sh``).

Covers:
- Manifest emission and idempotency on re-run.
- File-tree shape produced for every supported CLI.
- All written JSON files parse cleanly.
- Uninstall round-trips: every manifest entry is removed and the
  gitignore marker block is stripped.
- Refusal of out-of-tree paths during uninstall.
- ``--no-sidecar`` / ``--no-model`` flags actually skip those phases.

Marked ``slow`` because each invocation touches the user's home
directory (writes to ``~/.copilot/mcp-config.json`` etc.) and is not
safe to run in a shared CI sandbox without isolation.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Make src importable when running this file in isolation.
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from src import _init as _init_mod  # noqa: E402
from src._init import init, uninstall, InitResult  # noqa: E402


def _isolated_home(tmp: Path) -> dict:
    """Patch Path.home() so the test never touches the real ~/.* files."""
    fake_home = tmp / "home"
    fake_home.mkdir()
    return {"HOME": str(fake_home), "USERPROFILE": str(fake_home)}


class RcInitTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.target = self.tmp / "repo"
        self.target.mkdir()
        self._home_patch = mock.patch.dict(os.environ if False else {}, {})  # placeholder
        self._home_mock = mock.patch.object(Path, "home", lambda: self.tmp / "home")
        (self.tmp / "home").mkdir()
        self._home_mock.start()
        self.addCleanup(self._home_mock.stop)

    def test_init_emits_every_cli_hook(self) -> None:
        result = init(self.target, install_sidecar=False, install_model=False)
        # Every supported CLI should produce a config file or noted global edit.
        relative_writes = [w for w in result.wrote if w.startswith(".")]
        for rel in [
            ".envrc",
            ".claude/settings.local.json",
            ".codex/settings.json",
            ".gemini/settings.json",
            ".copilot/copilot-instructions.md",
            ".kimi/settings.json",
            ".vibe/config.toml",
            ".vibe/AGENTS.md",
            ".pi/settings.json",
            ".pi/extensions/reasoning_core_gate.ts",
        ]:
            self.assertIn(rel, relative_writes, msg=f"missing {rel} in init() writes")

    def test_init_writes_only_json_files_that_parse(self) -> None:
        init(self.target, install_sidecar=False, install_model=False)
        for rel in [
            ".claude/settings.local.json",
            ".codex/settings.json",
            ".gemini/settings.json",
            ".kimi/settings.json",
            ".pi/settings.json",
        ]:
            path = self.target / rel
            self.assertTrue(path.exists(), msg=rel)
            json.loads(path.read_text())  # raises if not valid JSON

    def test_init_manifest_records_writes(self) -> None:
        init(self.target, install_sidecar=False, install_model=False)
        manifest = self.target / ".reasoning-core" / "install.manifest"
        self.assertTrue(manifest.exists())
        lines = manifest.read_text().splitlines()
        # Every recorded path must be relative to the target repo.
        for line in lines:
            self.assertFalse(line.startswith("/"), msg=f"absolute path in manifest: {line}")
        self.assertIn(".envrc", lines)

    def test_init_is_idempotent(self) -> None:
        first = init(self.target, install_sidecar=False, install_model=False)
        # All writes should be new on the first run.
        self.assertGreater(len(first.wrote), 0)
        # Second run must skip every per-repo file (manifest is append-only
        # by design; we only check that no NEW per-repo file is written).
        second = init(self.target, install_sidecar=False, install_model=False)
        repo_relative_new = [w for w in second.wrote if w.startswith(".")]
        self.assertEqual(repo_relative_new, [], msg=f"unexpected new files on re-run: {repo_relative_new}")
        for rel in [
            ".envrc",
            ".claude/settings.local.json",
            ".codex/settings.json",
            ".gemini/settings.json",
            ".kimi/settings.json",
            ".vibe/config.toml",
            ".pi/settings.json",
        ]:
            self.assertIn(rel, second.skipped, msg=f"{rel} should be skipped on re-run")
        # Manifest must NOT have duplicate lines.
        manifest_lines = (self.target / ".reasoning-core" / "install.manifest").read_text().splitlines()
        self.assertEqual(len(manifest_lines), len(set(manifest_lines)), "manifest has duplicate lines")

    def test_init_handles_no_sidecar_and_no_model(self) -> None:
        result = init(self.target, install_sidecar=False, install_model=False)
        self.assertFalse(result.sidecar_installed)
        self.assertFalse(result.model_downloaded)
        self.assertNotIn("sidecar supervisor not auto-installed", "\n".join(result.warned))

    def test_uninstall_round_trip(self) -> None:
        init(self.target, install_sidecar=False, install_model=False)
        # The gitignore was created by init; grab its size to confirm restoration.
        before = (self.target / ".gitignore").read_text()
        self.assertIn("reasoning-core", before)
        result = uninstall(self.target)
        self.assertEqual(result["refused"], [])
        for rel in [
            ".envrc",
            ".claude/settings.local.json",
            ".codex/settings.json",
            ".gemini/settings.json",
            ".kimi/settings.json",
            ".vibe/config.toml",
            ".vibe/AGENTS.md",
            ".pi/settings.json",
            ".pi/extensions/reasoning_core_gate.ts",
        ]:
            self.assertIn(rel, result["removed"], msg=f"{rel} should be removed by uninstall")
            self.assertFalse((self.target / rel).exists(), msg=f"{rel} still on disk after uninstall")
        # Gitignore block must be stripped.
        after = (self.target / ".gitignore").read_text()
        self.assertNotIn("reasoning-core", after)

    def test_uninstall_refuses_out_of_tree_manifest_entries(self) -> None:
        manifest = self.target / ".reasoning-core" / "install.manifest"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        # Tamper: include an absolute out-of-tree path.
        manifest.write_text("/etc/passwd\n")
        result = uninstall(self.target)
        self.assertIn("/etc/passwd", result["refused"])
        # /etc/passwd must still exist (we are running as the user, so
        # we only assert it was refused, not that we couldn't delete it
        # in principle).
        self.assertTrue(Path("/etc/passwd").exists())

    def test_envrc_template_renders_python_path(self) -> None:
        init(self.target, install_sidecar=False, install_model=False)
        envrc = (self.target / ".envrc").read_text()
        # Must reference a real interpreter path, not a placeholder.
        self.assertIn("RC_PYTHON=", envrc)
        self.assertNotIn("@RC_PYTHON@", envrc)


if __name__ == "__main__":
    unittest.main()
