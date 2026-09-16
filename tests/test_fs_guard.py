"""Тесты файлового шлюза без обращения к модели."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from harness.fs_guard import FileSystemGuard


# --------------------------------------------------------------------------
# Базовая навигация
# --------------------------------------------------------------------------

def test_read_simple_file(guard: FileSystemGuard):
    ok, reason = guard.check_read("docs/readme.md")
    assert ok, reason


def test_read_nonexistent_is_permitted(guard: FileSystemGuard):
    """Проверка прав не зависит от существования файла (это делает tool)."""
    ok, _ = guard.check_read("docs/new.md")
    assert ok


# --------------------------------------------------------------------------
# Path traversal
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "../etc/passwd",
    "docs/../../etc/passwd",
    "..",
    "docs/..",
])
def test_path_traversal_blocked(guard: FileSystemGuard, path: str):
    ok, reason = guard.check_read(path)
    assert not ok
    assert "traversal" in reason.lower() or "escapes" in reason.lower()


def test_null_byte_blocked(guard: FileSystemGuard):
    ok, _ = guard.check_read("docs/readme.md\x00.txt")
    assert not ok


# --------------------------------------------------------------------------
# NFKC-нормализация
# --------------------------------------------------------------------------

def test_fullwidth_normalized(guard: FileSystemGuard):
    (guard.root / "notes").mkdir(exist_ok=True)
    (guard.root / "notes" / "abc.md").write_text("x", encoding="utf-8")
    ok, _ = guard.check_read("notes/\uff41\uff42\uff43.md")
    assert ok


# --------------------------------------------------------------------------
# Symlink escape
# --------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32",
                    reason="symlinks need admin on Windows")
def test_symlink_escape_blocked(workspace: Path, policy: dict):
    outside = workspace.parent / "outside_target"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("SHOULD NOT SEE", encoding="utf-8")

    link = workspace / "docs" / "escape"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("cannot create symlink in this environment")

    guard = FileSystemGuard(policy)
    ok, reason = guard.check_read("docs/escape/secret.txt")
    assert not ok
    assert "escapes" in reason.lower()


# --------------------------------------------------------------------------
# Whitelist / blacklist
# --------------------------------------------------------------------------

def test_whitelist_blocks_outside(tmp_path: Path):
    (tmp_path / "allowed").mkdir()
    (tmp_path / "blocked").mkdir()
    guard = FileSystemGuard({
        "root": str(tmp_path),
        "whitelist": ["allowed/**"],
        "blacklist": [],
        "writable": [],
    })

    ok, _ = guard.check_read("allowed/x.txt")
    assert ok
    ok, reason = guard.check_read("blocked/x.txt")
    assert not ok
    assert "whitelist" in reason.lower()


def test_empty_whitelist_denies_all(tmp_path: Path):
    """Пустой whitelist = ничего не разрешено (не «всё разрешено»)."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("x", encoding="utf-8")
    guard = FileSystemGuard({
        "root": str(tmp_path),
        "whitelist": [],
        "blacklist": [],
        "writable": [],
    })
    ok, reason = guard.check_read("docs/a.md")
    assert not ok
    assert "whitelist" in reason.lower()


def test_missing_whitelist_key_uses_default(tmp_path: Path):
    """Отсутствие ключа whitelist → дефолт ["**"] — всё видно."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("x", encoding="utf-8")
    guard = FileSystemGuard({
        "root": str(tmp_path),
        "blacklist": [],
        "writable": [],
    })
    ok, _ = guard.check_read("docs/a.md")
    assert ok


def test_blacklist_applied_after_whitelist(guard: FileSystemGuard):
    ok, reason = guard.check_read("secret.key")
    assert not ok
    assert "blacklist" in reason.lower()


def test_blacklist_git(guard: FileSystemGuard):
    ok, reason = guard.check_read(".git/config")
    assert not ok
    assert "blacklist" in reason.lower()


# --------------------------------------------------------------------------
# Writable
# --------------------------------------------------------------------------

def test_write_in_writable(guard: FileSystemGuard):
    ok, reason = guard.check_write("notes/a.md")
    assert ok, reason


def test_write_outside_writable(guard: FileSystemGuard):
    (guard.root / "src").mkdir()
    ok, reason = guard.check_write("src/a.md")
    assert not ok
    assert "writable" in reason.lower()


def test_write_disabled_when_writable_empty(tmp_path: Path):
    guard = FileSystemGuard({
        "root": str(tmp_path),
        "whitelist": ["**"],
        "blacklist": [],
        "writable": [],
    })
    ok, reason = guard.check_write("anything.txt")
    assert not ok
    assert "writable" in reason.lower() or "disabled" in reason.lower()


# --------------------------------------------------------------------------
# Блокировка расширений
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "notes/a.sh", "notes/a.py", "notes/a.js", "notes/a.rb", "notes/a.go",
    "notes/a.rs", "notes/a.exe", "notes/a.bat", "notes/a.ps1", "notes/a.php",
    "notes/a.whl",
])
def test_write_ext_blocked(guard: FileSystemGuard, name: str):
    ok, reason = guard.check_write(name)
    assert not ok
    assert "extension" in reason.lower()


def test_read_script_allowed(guard: FileSystemGuard):
    (guard.root / "notes" / "a.py").write_text("print(1)", encoding="utf-8")
    ok, _ = guard.check_read("notes/a.py")
    assert ok


# --------------------------------------------------------------------------
# resolve_read / resolve_write
# --------------------------------------------------------------------------

def test_resolve_read_returns_real_path(guard: FileSystemGuard):
    p, err = guard.resolve_read("docs/readme.md")
    assert p is not None, err
    assert p.is_file()
    assert str(p).startswith(str(guard.root))


def test_resolve_write_returns_target(guard: FileSystemGuard):
    p, err = guard.resolve_write("notes/new.md")
    assert p is not None, err
    assert str(p).startswith(str(guard.root))
