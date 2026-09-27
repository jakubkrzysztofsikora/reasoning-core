from pathlib import Path

from src.project_index import find_duplicate_definitions, find_import_cycle


def test_detects_new_import_cycle(tmp_path: Path):
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "b.py").write_text("import a\n", encoding="utf-8")
    assert find_import_cycle(str(tmp_path), "a.py", "import b\n") == ["a", "b", "a"]


def test_ignores_preexisting_reachable_cycle(tmp_path: Path):
    (tmp_path / "a.py").write_text("import b\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("import c\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("import b\n", encoding="utf-8")
    assert find_import_cycle(str(tmp_path), "a.py", "import b\nvalue = 1\n") is None


def test_detects_new_cycle_when_repository_already_has_another(tmp_path: Path):
    (tmp_path / "a.py").write_text("import b\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("import c\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("import b\nimport a\n", encoding="utf-8")
    assert find_import_cycle(str(tmp_path), "a.py", "import c\n") == ["a", "c", "a"]


def test_detects_cycle_when_new_module_is_imported_by_existing_module(tmp_path: Path):
    (tmp_path / "a.py").write_text("import new_module\n", encoding="utf-8")
    assert find_import_cycle(str(tmp_path), "new_module.py", "import a\n") == [
        "a", "new_module", "a"
    ]


def test_detects_exact_and_semantic_duplicate_definition(tmp_path: Path):
    (tmp_path / "existing.py").write_text("def helper(x):\n    return x + 1\n", encoding="utf-8")
    exact = find_duplicate_definitions(str(tmp_path), "new.py", "def helper(value):\n    return value\n")
    semantic = find_duplicate_definitions(str(tmp_path), "new.py", "def other(x):\n    return x + 1\n")
    assert any(item["kind"] == "exact_name" for item in exact)
    assert any(item["kind"] == "semantic_body" for item in semantic)


def test_ignores_preexisting_duplicate_on_unrelated_edit(tmp_path: Path):
    source = "def helper():\n    return 1\n"
    (tmp_path / "a.py").write_text(source, encoding="utf-8")
    (tmp_path / "b.py").write_text(source, encoding="utf-8")
    assert find_duplicate_definitions(str(tmp_path), "a.py", source + "value = 2\n") == []


def test_detects_new_duplicate_after_changed_definition(tmp_path: Path):
    (tmp_path / "a.py").write_text("def first():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def second():\n    return 2\n", encoding="utf-8")
    result = find_duplicate_definitions(
        str(tmp_path), "a.py", "def first():\n    return 2\n"
    )
    assert any(item["kind"] == "semantic_body" and item["path"] == "b.py" for item in result)


def test_detects_new_same_file_duplicate(tmp_path: Path):
    (tmp_path / "a.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    result = find_duplicate_definitions(
        str(tmp_path), "a.py", "def helper():\n    return 1\n\ndef helper():\n    return 2\n"
    )
    assert any(item["kind"] == "exact_name" and item["path"] == "a.py" for item in result)
