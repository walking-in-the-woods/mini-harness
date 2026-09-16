"""Тесты JSONL-аудита."""

from __future__ import annotations

import json
from pathlib import Path

from harness.audit import AuditLog


def test_write_creates_jsonl(tmp_path: Path):
    log_path = tmp_path / "sub" / "audit.jsonl"
    a = AuditLog(str(log_path))
    a.write("test_event", key="value", num=42)

    assert log_path.is_file()
    line = log_path.read_text(encoding="utf-8").strip()
    record = json.loads(line)
    assert record["event"] == "test_event"
    assert record["key"] == "value"
    assert record["num"] == 42
    assert "ts" in record and "pid" in record


def test_write_unreachable_path_does_not_raise(tmp_path: Path, capsys):
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    a = AuditLog(str(blocker / "sub" / "audit.jsonl"))
    a.write("event")
    captured = capsys.readouterr()
    assert "audit log disabled" in captured.err


def test_durable_event_written(tmp_path: Path):
    log_path = tmp_path / "audit.jsonl"
    a = AuditLog(str(log_path))
    a.write("apply_result", outcomes=["[OK] notes/a.txt"])
    line = log_path.read_text(encoding="utf-8").strip()
    assert "apply_result" in line


def test_non_durable_event_written(tmp_path: Path):
    log_path = tmp_path / "audit.jsonl"
    a = AuditLog(str(log_path))
    a.write("tool_call", tool="list_dir", args={"path": "."})
    line = log_path.read_text(encoding="utf-8").strip()
    record = json.loads(line)
    assert record["event"] == "tool_call"
    assert record["tool"] == "list_dir"


def test_multiple_events_appended(tmp_path: Path):
    log_path = tmp_path / "audit.jsonl"
    a = AuditLog(str(log_path))
    a.write("event_one", x=1)
    a.write("event_two", x=2)
    a.write("event_three", x=3)
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    for i, line in enumerate(lines, start=1):
        assert json.loads(line)["x"] == i
