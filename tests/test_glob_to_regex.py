"""Тесты трансляции glob -> regex. Ключевая защита — корректная
семантика **."""

from __future__ import annotations

import pytest

from harness.fs_guard import _glob_to_regex


@pytest.mark.parametrize("pattern,path,expected", [
    # Одиночная звёздочка: один сегмент
    ("*.md", "readme.md", True),
    ("*.md", "docs/readme.md", False),
    # ** матчит любое число сегментов
    ("**.md", "readme.md", True),
    ("**.md", "docs/readme.md", True),
    ("**/*.md", "readme.md", True),
    ("**/*.md", "docs/readme.md", True),
    ("**/*.md", "a/b/c/readme.md", True),
    # Префикс с **/
    ("docs/**", "docs/a.txt", True),
    ("docs/**", "docs/a/b/c.txt", True),
    ("docs/**", "docs", False),
    ("docs/**", "other/a.txt", False),
    # ? — один символ в сегменте
    ("file?.txt", "file1.txt", True),
    ("file?.txt", "file12.txt", False),
    # Спецсимволы экранируются
    ("a.b", "a.b", True),
    ("a.b", "axb", False),
    ("a+b", "a+b", True),
    ("a+b", "aab", False),
    # Нет частичного матчинга — только от начала до конца
    ("notes/*", "notes/a", True),
    ("notes/*", "notes/a/b", False),
])
def test_glob_to_regex(pattern: str, path: str, expected: bool):
    assert bool(_glob_to_regex(pattern).match(path)) is expected
