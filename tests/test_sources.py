"""Тесты SourceRegistry: парсинг конфига, лимиты, безопасность путей."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

from harness.sources import SourceError, SourceRegistry


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "input").mkdir()
    return ws


@pytest.fixture
def external(tmp_path: Path) -> Path:
    """Внешний корень вне workspace."""
    root = tmp_path / "external"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text(
        "print('hello')\n", encoding="utf-8"
    )
    (root / "src" / "utils.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    (root / "docs").mkdir()
    (root / "docs" / "readme.md").write_text(
        "# Readme\n\nSome text.\n", encoding="utf-8"
    )
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text(
        "[core]\n", encoding="utf-8"
    )
    (root / "big.bin").write_bytes(b"\x00\x01\x02\x03" * 1000)
    (root / "secret.key").write_text("SECRET", encoding="utf-8")
    return root


def _write_sources_config(workspace: Path, sources: list[dict],
                          defaults: dict | None = None) -> None:
    cfg = {"sources": sources}
    if defaults is not None:
        cfg["defaults"] = defaults
    (workspace / "sources.yaml").write_text(
        yaml.safe_dump(cfg), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Загрузка конфига
# ---------------------------------------------------------------------------

def test_no_config_file_is_empty_registry(workspace: Path):
    reg = SourceRegistry(workspace_dir=workspace, global_blacklist=())
    assert reg.list() == []


def test_parse_minimal_source(workspace: Path, external: Path):
    _write_sources_config(workspace, [
        {"name": "proj", "root": str(external)}
    ])
    reg = SourceRegistry(workspace_dir=workspace, global_blacklist=())
    assert len(reg.list()) == 1
    s = reg.get("proj")
    assert s.name == "proj"
    assert s.root == external.resolve()


def test_expanduser(workspace: Path, external: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(external.parent))
    _write_sources_config(workspace, [
        {"name": "proj", "root": "~/external"}
    ])
    reg = SourceRegistry(workspace_dir=workspace, global_blacklist=())
    assert reg.get("proj").root == external.resolve()


def test_duplicate_name_rejected(workspace: Path, external: Path):
    _write_sources_config(workspace, [
        {"name": "p", "root": str(external)},
        {"name": "p", "root": str(external)},
    ])
    with pytest.raises(SourceError, match="duplicate"):
        SourceRegistry(workspace_dir=workspace, global_blacklist=())


def test_missing_root_rejected(workspace: Path):
    _write_sources_config(workspace, [{"name": "p"}])
    with pytest.raises(SourceError, match="root"):
        SourceRegistry(workspace_dir=workspace, global_blacklist=())


def test_slash_in_name_rejected(workspace: Path, external: Path):
    _write_sources_config(workspace, [
        {"name": "a/b", "root": str(external)}
    ])
    with pytest.raises(SourceError, match="must not contain"):
        SourceRegistry(workspace_dir=workspace, global_blacklist=())


def test_unknown_source_raises(workspace: Path):
    reg = SourceRegistry(workspace_dir=workspace, global_blacklist=())
    with pytest.raises(SourceError, match="unknown source"):
        reg.get("nope")


def test_missing_root_dir_raises(workspace: Path, tmp_path: Path):
    _write_sources_config(workspace, [
        {"name": "p", "root": str(tmp_path / "nonexistent")}
    ])
    reg = SourceRegistry(workspace_dir=workspace, global_blacklist=())
    with pytest.raises(SourceError, match="not a directory"):
        reg.get("p")


# ---------------------------------------------------------------------------
# build_tree
# ---------------------------------------------------------------------------

def _make_registry(workspace: Path, external: Path,
                   blacklist: tuple[str, ...] = (),
                   defaults: dict | None = None) -> SourceRegistry:
    _write_sources_config(
        workspace,
        [{"name": "proj", "root": str(external)}],
        defaults,
    )
    return SourceRegistry(
        workspace_dir=workspace, global_blacklist=blacklist,
    )


def test_tree_basic(workspace: Path, external: Path):
    reg = _make_registry(workspace, external)
    body, count = reg.build_tree("proj")
    assert "src/" in body
    assert "main.py" in body
    assert "readme.md" in body
    assert count > 0


def test_tree_excludes_git(workspace: Path, external: Path):
    reg = _make_registry(
        workspace, external,
        defaults={"ignore": {"dirs": ["**/.git/**"]}},
    )
    body, _ = reg.build_tree("proj")
    assert ".git" not in body


def test_tree_global_blacklist(workspace: Path, external: Path):
    reg = _make_registry(
        workspace, external,
        blacklist=("**/*.key",),
    )
    body, _ = reg.build_tree("proj")
    assert "secret.key" not in body


def test_tree_max_depth(workspace: Path, external: Path):
    deep = external / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    (deep / "leaf.txt").write_text("x", encoding="utf-8")

    reg = _make_registry(
        workspace, external,
        defaults={"max_depth": 2},
    )
    body, _ = reg.build_tree("proj")
    assert "leaf.txt" not in body


def test_tree_max_entries(workspace: Path, external: Path):
    """/tree обрезается по max_tree_entries, отдельно от /files."""
    for i in range(20):
        (external / f"file{i:02d}.txt").write_text("x", encoding="utf-8")
    reg = _make_registry(
        workspace, external,
        defaults={"max_tree_entries": 5},
    )
    body, count = reg.build_tree("proj")
    assert count <= 5
    assert "обрезано" in body


def test_tree_subpath(workspace: Path, external: Path):
    reg = _make_registry(workspace, external)
    body, _ = reg.build_tree("proj", "src")
    assert "main.py" in body
    assert "readme.md" not in body


def test_tree_subpath_traversal_rejected(workspace: Path, external: Path):
    reg = _make_registry(workspace, external)
    with pytest.raises(SourceError, match="traversal"):
        reg.build_tree("proj", "../etc")


def test_tree_subpath_nonexistent(workspace: Path, external: Path):
    reg = _make_registry(workspace, external)
    with pytest.raises(SourceError, match="not found"):
        reg.build_tree("proj", "nonexistent_dir")


# ---------------------------------------------------------------------------
# collect_dump
# ---------------------------------------------------------------------------

def test_dump_includes_text_files(workspace: Path, external: Path):
    reg = _make_registry(workspace, external)
    body, stats = reg.collect_dump("proj")
    assert "print('hello')" in body
    assert "readme.md" in body
    assert stats["files_included"] >= 3


def test_dump_skips_binary(workspace: Path, external: Path):
    # big.bin: null-bytes — ловятся эвристикой
    reg = _make_registry(workspace, external)
    body, stats = reg.collect_dump("proj")
    assert "big.bin" not in body
    assert (stats["files_skipped_binary"] +
            stats["files_skipped_size"]) >= 1


def test_dump_skips_invalid_utf8(workspace: Path, external: Path):
    """Файл с байтами, невалидными в UTF-8 — тоже пропускается."""
    (external / "invalid.dat").write_bytes(b"\xff\xfe\xfd" * 100)
    reg = _make_registry(workspace, external)
    body, stats = reg.collect_dump("proj")
    assert "invalid.dat" not in body
    assert stats["files_skipped_binary"] >= 1


def test_dump_skips_large_file(workspace: Path, external: Path):
    big = external / "big_text.txt"
    big.write_text("x" * 200_000, encoding="utf-8")
    reg = _make_registry(
        workspace, external,
        defaults={"max_file_bytes": 1000},
    )
    body, stats = reg.collect_dump("proj")
    assert "big_text.txt" not in body
    assert stats["files_skipped_size"] >= 1


def test_dump_respects_total_bytes(workspace: Path, external: Path):
    for i in range(20):
        (external / f"f{i}.txt").write_text("x" * 500, encoding="utf-8")
    reg = _make_registry(
        workspace, external,
        defaults={"max_total_bytes": 2000},
    )
    body, stats = reg.collect_dump("proj")
    assert stats["truncated"] is True
    assert stats["bytes_total"] <= 2000


def test_dump_max_files(workspace: Path, external: Path):
    for i in range(20):
        (external / f"f{i}.txt").write_text("x", encoding="utf-8")
    reg = _make_registry(
        workspace, external,
        defaults={"max_files": 3},
    )
    body, stats = reg.collect_dump("proj")
    assert stats["files_included"] <= 3
    assert stats["truncated"] is True


def test_dump_global_blacklist_applied(workspace: Path, external: Path):
    reg = _make_registry(
        workspace, external,
        blacklist=("**/*.key",),
    )
    body, _ = reg.collect_dump("proj")
    assert "secret.key" not in body
    assert "SECRET" not in body


# ---------------------------------------------------------------------------
# Symlink escape
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32",
                    reason="symlinks need admin on Windows")
def test_symlink_escape_blocked(workspace: Path, external: Path,
                                 tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("SHOULD NOT READ",
                                         encoding="utf-8")
    link = external / "escape"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("cannot create symlink")

    reg = _make_registry(workspace, external)
    body, _ = reg.collect_dump("proj")
    assert "SHOULD NOT READ" not in body


# ---------------------------------------------------------------------------
# build_file_list
# ---------------------------------------------------------------------------

def test_file_list_has_sizes(workspace: Path, external: Path):
    reg = _make_registry(workspace, external)
    body, count = reg.build_file_list("proj")
    assert "main.py" in body
    assert count >= 3
    # Есть числовой столбец с размером
    assert any(line.strip().split()[0].isdigit()
               for line in body.splitlines())


# ---------------------------------------------------------------------------
# reload
# ---------------------------------------------------------------------------

def test_reload_picks_up_changes(workspace: Path, external: Path,
                                  tmp_path: Path):
    reg = _make_registry(workspace, external)
    assert len(reg.list()) == 1

    second = tmp_path / "second"
    second.mkdir()
    (workspace / "sources.yaml").write_text(
        yaml.safe_dump({"sources": [
            {"name": "proj", "root": str(external)},
            {"name": "second", "root": str(second)},
        ]}),
        encoding="utf-8",
    )
    reg.reload()
    assert len(reg.list()) == 2


# ---------------------------------------------------------------------------
# Лимиты дерева / files — раздельность и дефолты
# ---------------------------------------------------------------------------

def test_files_still_limited_by_max_entries(workspace: Path, external: Path):
    """/files остаётся под max_entries, отдельно от дерева."""
    for i in range(50):
        (external / f"file{i:02d}.txt").write_text("x", encoding="utf-8")
    reg = _make_registry(
        workspace, external,
        defaults={"max_entries": 5},
    )
    # tree не обрезается
    tree_body, tree_count = reg.build_tree("proj")
    assert tree_count >= 50
    # files — обрезается
    files_body, files_count = reg.build_file_list("proj")
    assert files_count <= 6
    assert "обрезано" in files_body


def test_tree_depth_unlimited_by_default(workspace: Path,
                                          external: Path):
    """По умолчанию глубина не ограничена."""
    deep = external
    for i in range(15):
        deep = deep / f"level{i}"
    deep.mkdir(parents=True)
    (deep / "leaf.txt").write_text("x", encoding="utf-8")

    reg = _make_registry(workspace, external)
    body, _ = reg.build_tree("proj")
    assert "leaf.txt" in body
    # 15 уровней отступа по 4 пробела
    assert " " * 60 in body or "level14" in body


def test_tree_max_depth_applied_when_set(workspace: Path,
                                          external: Path):
    """Явный max_depth=N обрезает обход."""
    deep = external
    for i in range(10):
        deep = deep / f"lvl{i}"
    deep.mkdir(parents=True)
    (deep / "leaf.txt").write_text("x", encoding="utf-8")

    reg = _make_registry(
        workspace, external,
        defaults={"max_depth": 3},
    )
    body, _ = reg.build_tree("proj")
    assert "leaf.txt" not in body


@pytest.mark.skipif(sys.platform == "win32",
                    reason="symlinks need admin on Windows")
def test_symlink_loop_doesnt_hang(workspace: Path, external: Path):
    """Symlink-цикл не приводит к бесконечному обходу.

    a/b -> a. Без детектора циклов обход ушёл бы в рекурсию
    до RecursionError. С visited — обрывается на первой повторной
    точке входа.
    """
    (external / "a").mkdir()
    (external / "a" / "file.txt").write_text("x", encoding="utf-8")
    link = external / "a" / "b"
    try:
        os.symlink(external / "a", link)
    except OSError:
        pytest.skip("cannot create symlink")

    reg = _make_registry(workspace, external)
    # Просто проверяем, что вызов завершается и не падает
    body, count = reg.build_tree("proj")
    assert count > 0
    assert "file.txt" in body
