"""Тесты подтверждения записи. Без модели."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.confirm import ConfirmSession
from harness.fs_guard import FileSystemGuard


@pytest.fixture
def session(workspace: Path, guard: FileSystemGuard) -> ConfirmSession:
    return ConfirmSession(workspace, guard)


def test_preview_new_file(session: ConfirmSession):
    text = session.render_preview([
        {"path": "notes/a.txt", "content": "hello world"},
    ])
    assert "notes/a.txt" in text
    assert "NEW FILE" in text
    assert "hello world" in text
    assert session.nonce in text


def test_preview_existing_file_shows_diff(session: ConfirmSession,
                                          workspace: Path):
    (workspace / "docs" / "readme.md").write_text("old line\n",
                                                   encoding="utf-8")
    text = session.render_preview([
        {"path": "docs/readme.md", "content": "new line\n"},
    ])
    assert "-old line" in text
    assert "+new line" in text


def test_preview_truncates_huge_new_file(session: ConfirmSession):
    text = session.render_preview([
        {"path": "notes/big.txt", "content": "x" * 10_000},
    ])
    assert "truncated for display" in text


def test_apply_wrong_nonce_cancels(session: ConfirmSession,
                                    workspace: Path):
    out = session.apply([{"path": "notes/a.txt", "content": "x"}],
                        "WRONG-CODE")
    assert any("CANCELLED" in line for line in out)
    assert not (workspace / "notes" / "a.txt").exists()


def test_apply_empty_nonce_cancels(session: ConfirmSession):
    out = session.apply([{"path": "notes/a.txt", "content": "x"}], "")
    assert any("CANCELLED" in line for line in out)


def test_apply_correct_nonce_writes_file(session: ConfirmSession,
                                          workspace: Path):
    out = session.apply([{"path": "notes/a.txt", "content": "payload"}],
                        session.nonce)
    assert any(line.startswith("[OK]") for line in out)
    assert (workspace / "notes" / "a.txt").read_text() == "payload"


def test_apply_is_case_insensitive(session: ConfirmSession):
    out = session.apply([{"path": "notes/a.txt", "content": "x"}],
                        session.nonce.lower())
    assert any(line.startswith("[OK]") for line in out)


def test_apply_denies_forbidden_path(session: ConfirmSession,
                                      workspace: Path):
    (workspace / "src").mkdir()
    out = session.apply([{"path": "src/a.txt", "content": "x"}],
                        session.nonce)
    assert any(line.startswith("[DENIED]") for line in out)
    assert not (workspace / "src" / "a.txt").exists()


def test_apply_multiple_writes(session: ConfirmSession, workspace: Path):
    writes = [
        {"path": "notes/one.txt", "content": "1"},
        {"path": "notes/two.txt", "content": "2"},
    ]
    out = session.apply(writes, session.nonce)
    assert all(line.startswith("[OK]") for line in out)
    assert (workspace / "notes" / "one.txt").read_text() == "1"
    assert (workspace / "notes" / "two.txt").read_text() == "2"


def test_apply_overwrites_existing(session: ConfirmSession, workspace: Path):
    p = workspace / "notes" / "old.txt"
    p.write_text("before", encoding="utf-8")
    session.apply([{"path": "notes/old.txt", "content": "after"}],
                  session.nonce)
    assert p.read_text() == "after"


def test_apply_sets_mode_0644(session: ConfirmSession, workspace: Path):
    out = session.apply([{"path": "notes/mode.txt", "content": "x"}],
                        session.nonce)
    assert any(line.startswith("[OK]") for line in out)
    mode = (workspace / "notes" / "mode.txt").stat().st_mode & 0o777
    assert mode == 0o644, f"unexpected mode: {oct(mode)}"
