"""Общие фикстуры для юнит-тестов. Модель не вызывается."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.fs_guard import FileSystemGuard


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Изолированный «workspace» для каждого теста."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "readme.md").write_text("# Hello\n", encoding="utf-8")
    (tmp_path / "notes").mkdir()
    (tmp_path / "output").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (tmp_path / "secret.key").write_text("SECRET\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def policy(workspace: Path) -> dict:
    """Стандартная политика: whitelist **, чёрный список + writable."""
    return {
        "root": str(workspace),
        "whitelist": ["**"],
        "blacklist": [
            "**/.git/**",
            "**/*.key",
        ],
        "writable": [
            "notes/**",
            "output/**",
            "docs/**",
            "*.md",
            "*.txt",
        ],
    }


@pytest.fixture
def guard(policy: dict) -> FileSystemGuard:
    return FileSystemGuard(policy)
