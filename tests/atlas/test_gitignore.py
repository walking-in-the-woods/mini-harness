from __future__ import annotations

from pathlib import Path

from harness.atlas.gitignore import GitIgnoreMatcher


def test_no_gitignore(tmp_path: Path):
    m = GitIgnoreMatcher(tmp_path, extra_ignore_dirs=())
    assert not m.is_ignored("src/main.py")


def test_basic_pattern(tmp_path: Path):
    (tmp_path / ".gitignore").write_text("*.pyc\n__pycache__/\n",
                                          encoding="utf-8")
    m = GitIgnoreMatcher(tmp_path, extra_ignore_dirs=())
    assert m.is_ignored("foo.pyc")
    assert m.is_ignored("deep/foo.pyc")
    assert m.is_ignored("__pycache__/x.py")
    assert not m.is_ignored("src/main.py")


def test_negation(tmp_path: Path):
    (tmp_path / ".gitignore").write_text(
        "*.log\n!important.log\n", encoding="utf-8"
    )
    m = GitIgnoreMatcher(tmp_path, extra_ignore_dirs=())
    assert m.is_ignored("debug.log")
    assert not m.is_ignored("important.log")


def test_extra_ignore_dirs(tmp_path: Path):
    m = GitIgnoreMatcher(tmp_path, extra_ignore_dirs=(".venv",))
    assert m.is_ignored(".venv/lib/x.py")
    assert m.is_ignored("nested/.venv/x")
    assert not m.is_ignored("src/main.py")


def test_comment_ignored(tmp_path: Path):
    (tmp_path / ".gitignore").write_text(
        "# comment\n*.tmp\n", encoding="utf-8"
    )
    m = GitIgnoreMatcher(tmp_path, extra_ignore_dirs=())
    assert m.is_ignored("a.tmp")
    assert not m.is_ignored("comment")
