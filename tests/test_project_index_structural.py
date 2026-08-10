from pathlib import Path

from src.project_index import find_duplicate_definitions, find_import_cycle


def test_detects_new_import_cycle(tmp_path: Path):
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "b.py").write_text("import a\n", encoding="utf-8")
    assert find_import_cycle(str(tmp_path), "a.py", "import b\n") == ["a", "b", "a"]


def test_detects_exact_and_semantic_duplicate_definition(tmp_path: Path):
    (tmp_path / "existing.py").write_text("def helper(x):\n    return x + 1\n", encoding="utf-8")
    exact = find_duplicate_definitions(str(tmp_path), "new.py", "def helper(value):\n    return value\n")
    semantic = find_duplicate_definitions(str(tmp_path), "new.py", "def other(x):\n    return x + 1\n")
    assert any(item["kind"] == "exact_name" for item in exact)
    assert any(item["kind"] == "semantic_body" for item in semantic)
