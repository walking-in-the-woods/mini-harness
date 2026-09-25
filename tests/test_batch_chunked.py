"""Тесты chunked-режима BatchRunner.

Покрывает:
  * pre-flight без --chunk: файл > budget → failed
  * chunked успешный (code): split → transform → merge → validate
  * chunked partial: on_chunk_failure=partial, .failed/ создаётся
  * chunked fail: on_chunk_failure=fail, ничего не записано
  * INSUFFICIENT_CONTEXT: fail vs skip
  * overrides из CLI
  * merge-invalid: on_merge_invalid=fail
  * preview с [!] для partial
  * _format_chunk_user_message

Модель замокана через ScriptedAgent. Реальный CodeProcessor и
DocProcessor используются, потому что они детерминированы и
быстры.

Бюджет в тестах задаётся через `chunk_target_bytes` — override
авто-формулы. chunk_min_bytes=0 отключает склейку мелких чанков.
Так мелкие тестовые источники (30-80 байт) реально разбиваются,
а не уходят в один whole-chunk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.audit import AuditLog
from harness.batch import BatchRunner
from harness.fs_guard import FileSystemGuard
from harness.processing import (
    CodeConfig,
    DefaultsConfig,
    DocsConfig,
    ProcessingConfig,
)


class ScriptedAgent:
    """Скриптованный агент для chunked-тестов.

    Возвращает заранее заданные ответы по порядку вызовов.
    """

    def __init__(self, responses: list[tuple[str | None, str | None]]):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.max_read = 200_000
        self.audit = AuditLog("/dev/null")
        self.num_ctx = 4096

    def transform_for_batch(
        self,
        source_path: str,
        source_content: str,
        prompt_content: str,
        retry_hint: str | None = None,
    ) -> tuple[str | None, str | None]:
        self.calls.append({
            "source_path": source_path,
            "source_content": source_content,
            "prompt_content": prompt_content,
            "retry_hint": retry_hint,
        })
        if not self._responses:
            return None, "no scripted response"
        return self._responses.pop(0)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "input" / "batch").mkdir(parents=True)
    (tmp_path / "input" / "prompts").mkdir(parents=True)
    (tmp_path / "output").mkdir()
    return tmp_path


@pytest.fixture
def guard(workspace: Path) -> FileSystemGuard:
    return FileSystemGuard({
        "root": str(workspace),
        "whitelist": ["**"],
        "blacklist": ["**/.git/**"],
        "writable": ["output/**", "input/**"],
        "ext_allow_paths": ["output/**"],
    })


@pytest.fixture
def audit(workspace: Path) -> AuditLog:
    return AuditLog(str(workspace / "audit.jsonl"))


@pytest.fixture
def proc_config() -> ProcessingConfig:
    """Конфиг с маленьким фиксированным бюджетом.

    chunk_target_bytes=30 — override авто-формулы. Файлы в тестах
    35–70 байт, бюджет 30 заставляет split разбивать, а не возвращать
    whole.

    chunk_min_bytes=0 — чтобы мелкие чанки не склеивались в один.
    """
    return ProcessingConfig(
        defaults=DefaultsConfig(
            chunk_target_bytes=30,
            chunk_min_bytes=0,
        ),
        code=CodeConfig(),
        docs=DocsConfig(),
    )


def _make_runner(agent, guard, workspace, audit, *,
                 target_dir=None, mode="code", chunk=True,
                 config=None, overrides=None):
    return BatchRunner(
        agent, guard, workspace, audit,
        target_dir=target_dir,
        mode=mode,
        chunk_enabled=chunk,
        processing_config=config,
        overrides=overrides,
    )


# ══════════════════════════════════════════════════════════════════════════
# Pre-flight без chunk
# ══════════════════════════════════════════════════════════════════════════

class TestPreFlight:
    def test_small_file_passes(self, workspace, guard, audit,
                                monkeypatch):
        (workspace / "input" / "batch" / "small.py").write_text(
            "def f(): return 1\n"
        )
        (workspace / "input" / "prompts" / "p.md").write_text("add docs")

        agent = ScriptedAgent([
            ("def f():\n    '''doc'''\n    return 1\n", None),
        ])
        runner = _make_runner(agent, guard, workspace, audit,
                               chunk=False, mode="",
                               target_dir="output/batch/t1")
        monkeypatch.setattr("builtins.input", lambda _: runner.nonce)
        runner.run("input/batch", "*.py", "input/prompts/p.md")

        assert runner.items[0].status == "transformed"
        assert len(agent.calls) == 1

    def test_large_file_fails_without_chunk(self, workspace, guard, audit,
                                              monkeypatch):
        big_body = "    x = 1\n" * 800
        big = f"def huge():\n{big_body}"
        (workspace / "input" / "batch" / "big.py").write_text(big)
        (workspace / "input" / "prompts" / "p.md").write_text("p")

        agent = ScriptedAgent([])
        runner = _make_runner(agent, guard, workspace, audit,
                               chunk=False, mode="",
                               target_dir="output/batch/t2")
        monkeypatch.setattr("builtins.input", lambda _: "x")
        runner.run("input/batch", "*.py", "input/prompts/p.md")

        assert runner.items[0].status == "failed"
        assert "Use --chunk" in (runner.items[0].error or "")
        assert agent.calls == []


# ══════════════════════════════════════════════════════════════════════════
# Chunked: успешный путь
# ══════════════════════════════════════════════════════════════════════════

class TestChunkedSuccess:
    def test_code_three_functions(self, workspace, guard, audit,
                                    monkeypatch, proc_config):
        src = (
            "def a():\n    return 1\n"
            "\n"
            "def b():\n    return 2\n"
            "\n"
            "def c():\n    return 3\n"
        )
        (workspace / "input" / "batch" / "three.py").write_text(src)
        (workspace / "input" / "prompts" / "p.md").write_text("add doc")

        agent = ScriptedAgent([
            ('def a():\n    """A."""\n    return 1\n', None),
            ('def b():\n    """B."""\n    return 2\n', None),
            ('def c():\n    """C."""\n    return 3\n', None),
        ])
        runner = _make_runner(agent, guard, workspace, audit,
                               config=proc_config,
                               target_dir="output/batch/t3")
        monkeypatch.setattr("builtins.input", lambda _: runner.nonce)
        runner.run("input/batch", "*.py", "input/prompts/p.md")

        item = runner.items[0]
        assert item.status == "transformed", item.error
        assert item.chunks_total == 3
        assert item.chunks_ok == 3
        assert item.chunks_failed == 0
        assert len(agent.calls) == 3

        out = workspace / "output" / "batch" / "t3" / "three.py"
        assert out.exists()
        content = out.read_text()
        assert '"""A."""' in content
        assert '"""B."""' in content
        assert '"""C."""' in content

    def test_docs_two_sections(self, workspace, guard, audit,
                                monkeypatch, proc_config):
        src = (
            "# Section A\n\nContent A.\n\n"
            "# Section B\n\nContent B.\n"
        )
        (workspace / "input" / "batch" / "doc.md").write_text(src)
        (workspace / "input" / "prompts" / "p.md").write_text("p")

        agent = ScriptedAgent([
            ("# Section A\n\nMODIFIED A.\n\n", None),
            ("# Section B\n\nMODIFIED B.\n", None),
        ])
        runner = _make_runner(agent, guard, workspace, audit,
                               mode="docs", config=proc_config,
                               target_dir="output/batch/t4")
        monkeypatch.setattr("builtins.input", lambda _: runner.nonce)
        runner.run("input/batch", "*.md", "input/prompts/p.md")

        item = runner.items[0]
        assert item.status == "transformed", item.error
        assert item.chunks_total == 2
        content = (workspace / "output" / "batch" / "t4" / "doc.md") \
            .read_text()
        assert "MODIFIED A." in content
        assert "MODIFIED B." in content

    def test_whole_file_no_chunks(self, workspace, guard, audit,
                                    monkeypatch, proc_config):
        (workspace / "input" / "batch" / "small.py").write_text(
            "def f():\n    return 1\n"
        )
        (workspace / "input" / "prompts" / "p.md").write_text("p")

        agent = ScriptedAgent([
            ('def f():\n    """Doc."""\n    return 1\n', None),
        ])
        runner = _make_runner(agent, guard, workspace, audit,
                               config=proc_config,
                               target_dir="output/batch/t5")
        monkeypatch.setattr("builtins.input", lambda _: runner.nonce)
        runner.run("input/batch", "*.py", "input/prompts/p.md")

        item = runner.items[0]
        # Файл 26 байт, budget 30 — влезает, whole.
        assert item.status == "transformed"
        assert item.chunks_total == 1
        assert item.chunks_ok == 1


# ══════════════════════════════════════════════════════════════════════════
# Chunked: partial-режим
# ══════════════════════════════════════════════════════════════════════════

class TestChunkedPartial:
    def test_partial_writes_file_and_failed_dir(
        self, workspace, guard, audit, monkeypatch,
    ):
        src = (
            "def a():\n    return 1\n"
            "\n"
            "def b():\n    return 2\n"
            "\n"
            "def c():\n    return 3\n"
        )
        (workspace / "input" / "batch" / "three.py").write_text(src)
        (workspace / "input" / "prompts" / "p.md").write_text("p")

        agent = ScriptedAgent([
            ('def a():\n    """A."""\n    return 1\n', None),
            (None, "reasoning leaked twice"),
            ('def c():\n    """C."""\n    return 3\n', None),
        ])
        cfg = ProcessingConfig(
            defaults=DefaultsConfig(
                on_chunk_failure="partial",
                chunk_target_bytes=30,
                chunk_min_bytes=0,
            ),
            code=CodeConfig(),
            docs=DocsConfig(),
        )
        runner = _make_runner(agent, guard, workspace, audit,
                               config=cfg,
                               target_dir="output/batch/t6")
        monkeypatch.setattr("builtins.input", lambda _: runner.nonce)
        runner.run("input/batch", "*.py", "input/prompts/p.md")

        item = runner.items[0]
        assert item.status == "transformed", item.error
        assert item.chunks_total == 3
        assert item.chunks_ok == 2
        assert item.chunks_failed == 1
        assert len(item.failed_chunks) == 1
        assert item.failed_chunks[0].index == 2

        target = workspace / "output" / "batch" / "t6"
        assert (target / "three.py").exists()
        failed_dir = target / "three.py.failed"
        assert failed_dir.is_dir()
        assert (failed_dir / "README.md").exists()
        assert (failed_dir / "chunk_02_source.txt").exists()
        assert (failed_dir / "chunk_02_error.txt").exists()

        content = (target / "three.py").read_text()
        assert "NOT PROCESSED" in content
        assert "def b():\n    return 2" in content

    def test_fail_stops_and_writes_nothing(
        self, workspace, guard, audit, monkeypatch, proc_config,
    ):
        src = (
            "def a():\n    return 1\n"
            "\n"
            "def b():\n    return 2\n"
            "\n"
            "def c():\n    return 3\n"
        )
        (workspace / "input" / "batch" / "three.py").write_text(src)
        (workspace / "input" / "prompts" / "p.md").write_text("p")

        agent = ScriptedAgent([
            ('def a():\n    """A."""\n    return 1\n', None),
            (None, "reasoning leaked twice"),
        ])
        runner = _make_runner(agent, guard, workspace, audit,
                               config=proc_config,
                               target_dir="output/batch/t7")
        monkeypatch.setattr("builtins.input", lambda _: "x")
        runner.run("input/batch", "*.py", "input/prompts/p.md")

        item = runner.items[0]
        assert item.status == "failed"
        assert "chunks failed" in (item.error or "")
        # Третий чанк не вызывается — fail останавливает.
        assert len(agent.calls) == 2
        assert not (workspace / "output" / "batch" / "t7" / "three.py").exists()


# ══════════════════════════════════════════════════════════════════════════
# INSUFFICIENT_CONTEXT
# ══════════════════════════════════════════════════════════════════════════

class TestInsufficientContext:
    def test_fail_on_insufficient(self, workspace, guard, audit,
                                    monkeypatch, proc_config):
        src = "def a(): return 1\n\ndef b(): return 2\n"
        (workspace / "input" / "batch" / "x.py").write_text(src)
        (workspace / "input" / "prompts" / "p.md").write_text("p")

        agent = ScriptedAgent([
            ('def a():\n    """A."""\n    return 1\n', None),
            ("INSUFFICIENT_CONTEXT", None),
        ])
        runner = _make_runner(agent, guard, workspace, audit,
                               config=proc_config,
                               target_dir="output/batch/t8")
        monkeypatch.setattr("builtins.input", lambda _: "x")
        runner.run("input/batch", "*.py", "input/prompts/p.md")

        item = runner.items[0]
        # on_chunk_failure=fail по умолчанию → item failed.
        assert item.status == "failed"

    def test_skip_on_insufficient(self, workspace, guard, audit,
                                    monkeypatch):
        src = "def a(): return 1\n\ndef b(): return 2\n"
        (workspace / "input" / "batch" / "x.py").write_text(src)
        (workspace / "input" / "prompts" / "p.md").write_text("p")

        agent = ScriptedAgent([
            ('def a():\n    """A."""\n    return 1\n', None),
            ("INSUFFICIENT_CONTEXT", None),
        ])
        cfg = ProcessingConfig(
            defaults=DefaultsConfig(
                on_insufficient_context="skip",
                chunk_target_bytes=30,
                chunk_min_bytes=0,
            ),
            code=CodeConfig(),
            docs=DocsConfig(),
        )
        runner = _make_runner(agent, guard, workspace, audit,
                               config=cfg,
                               target_dir="output/batch/t9")
        monkeypatch.setattr("builtins.input", lambda _: runner.nonce)
        runner.run("input/batch", "*.py", "input/prompts/p.md")

        item = runner.items[0]
        assert item.status == "transformed"
        assert item.chunks_skipped == 1
        assert item.chunks_failed == 0
        content = (workspace / "output" / "batch" / "t9" / "x.py").read_text()
        assert "def b(): return 2" in content


# ══════════════════════════════════════════════════════════════════════════
# Overrides
# ══════════════════════════════════════════════════════════════════════════

class TestOverrides:
    def test_override_partial_changes_behaviour(
        self, workspace, guard, audit, monkeypatch, proc_config,
    ):
        src = "def a(): return 1\n\ndef b(): return 2\n"
        (workspace / "input" / "batch" / "x.py").write_text(src)
        (workspace / "input" / "prompts" / "p.md").write_text("p")

        agent = ScriptedAgent([
            ('def a():\n    """A."""\n    return 1\n', None),
            (None, "boom"),
        ])
        runner = _make_runner(
            agent, guard, workspace, audit,
            config=proc_config,
            overrides={"on_chunk_failure": "partial"},
            target_dir="output/batch/t10",
        )
        monkeypatch.setattr("builtins.input", lambda _: runner.nonce)
        runner.run("input/batch", "*.py", "input/prompts/p.md")

        item = runner.items[0]
        assert item.status == "transformed"
        assert item.chunks_failed == 1
        assert (workspace / "output" / "batch" / "t10" / "x.py").exists()


# ══════════════════════════════════════════════════════════════════════════
# Merge invalid
# ══════════════════════════════════════════════════════════════════════════

class TestMergeInvalid:
    def test_merge_invalid_fail(self, workspace, guard, audit,
                                  monkeypatch, proc_config):
        src = "def a(): return 1\n\ndef b(): return 2\n"
        (workspace / "input" / "batch" / "x.py").write_text(src)
        (workspace / "input" / "prompts" / "p.md").write_text("p")

        agent = ScriptedAgent([
            ('def a():\n    """A."""\n    return 1\n', None),
            ('# nothing here\n', None),
        ])
        runner = _make_runner(agent, guard, workspace, audit,
                               config=proc_config,
                               target_dir="output/batch/t11")
        monkeypatch.setattr("builtins.input", lambda _: "x")
        runner.run("input/batch", "*.py", "input/prompts/p.md")

        item = runner.items[0]
        assert item.status == "failed"
        assert "merge" in (item.error or "").lower() \
            or "defs" in (item.error or "").lower()
        assert not (workspace / "output" / "batch" / "t11" / "x.py").exists()

    def test_merge_invalid_partial_writes(self, workspace, guard, audit,
                                            monkeypatch):
        src = "def a(): return 1\n\ndef b(): return 2\n"
        (workspace / "input" / "batch" / "x.py").write_text(src)
        (workspace / "input" / "prompts" / "p.md").write_text("p")

        agent = ScriptedAgent([
            ('def a():\n    """A."""\n    return 1\n', None),
            ('# nothing here\n', None),
        ])
        cfg = ProcessingConfig(
            defaults=DefaultsConfig(
                on_merge_invalid="partial",
                on_chunk_failure="fail",
                chunk_target_bytes=30,
                chunk_min_bytes=0,
            ),
            code=CodeConfig(),
            docs=DocsConfig(),
        )
        runner = _make_runner(agent, guard, workspace, audit,
                               config=cfg,
                               target_dir="output/batch/t12")
        monkeypatch.setattr("builtins.input", lambda _: runner.nonce)
        runner.run("input/batch", "*.py", "input/prompts/p.md")

        item = runner.items[0]
        # Merge невалиден, но partial — записан.
        assert item.status == "transformed"
        assert (workspace / "output" / "batch" / "t12" / "x.py").exists()
        # issues должны быть зафиксированы в item.
        assert any(
            i.code == "defs_lost" for i in item.validation_issues
        )


# ══════════════════════════════════════════════════════════════════════════
# Формат user message
# ══════════════════════════════════════════════════════════════════════════

class TestChunkUserMessage:
    def test_header_no_preamble(self):
        from harness.processing.base import Chunk
        chunk = Chunk(2, 5, "def f(): pass\n", "",
                       100, 116, "function")
        msg = BatchRunner._format_chunk_user_message(
            chunk, "input/batch/x.py",
        )
        assert msg.startswith("[Part 2/5 of input/batch/x.py]")
        assert "def f(): pass" in msg
        assert "Read-only" not in msg

    def test_header_with_preamble(self):
        from harness.processing.base import Chunk
        chunk = Chunk(
            1, 3, "def f(): pass\n",
            "# === Module preamble ===\nimport re\n",
            50, 66, "function",
        )
        msg = BatchRunner._format_chunk_user_message(
            chunk, "input/batch/x.py",
        )
        assert msg.startswith("[Part 1/3 of input/batch/x.py]")
        assert "Read-only context" in msg
        assert "import re" in msg
        assert "Your chunk" in msg
        assert "def f(): pass" in msg

    def test_calls_pass_chunk_header(self, workspace, guard, audit,
                                       monkeypatch, proc_config):
        """Проверяем, что заголовок [Part N/M] попадает в
        source_content, который получает агент.

        Preamble отдельно тестируется в test_processing_code
        (test_include_preamble). Здесь — только заголовок и
        реальное разбиение на 2 чанка.
        """
        src = "def a(): return 1\n\ndef b(): return 2\n"
        (workspace / "input" / "batch" / "x.py").write_text(src)
        (workspace / "input" / "prompts" / "p.md").write_text("p")

        agent = ScriptedAgent([
            ('def a():\n    """A."""\n    return 1\n', None),
            ('def b():\n    """B."""\n    return 2\n', None),
        ])
        runner = _make_runner(agent, guard, workspace, audit,
                               config=proc_config,
                               target_dir="output/batch/t13")
        monkeypatch.setattr("builtins.input", lambda _: runner.nonce)
        runner.run("input/batch", "*.py", "input/prompts/p.md")

        assert len(agent.calls) == 2
        assert "[Part 1/2 of input/batch/x.py]" in \
            agent.calls[0]["source_content"]
        assert "[Part 2/2 of input/batch/x.py]" in \
            agent.calls[1]["source_content"]


# ══════════════════════════════════════════════════════════════════════════
# Preview
# ══════════════════════════════════════════════════════════════════════════

class TestPreview:
    def test_partial_preview_contains_warning(self, workspace, guard,
                                                audit, monkeypatch,
                                                capsys, proc_config):
        src = "def a(): return 1\n\ndef b(): return 2\n"
        (workspace / "input" / "batch" / "x.py").write_text(src)
        (workspace / "input" / "prompts" / "p.md").write_text("p")

        agent = ScriptedAgent([
            ('def a():\n    """A."""\n    return 1\n', None),
            (None, "boom"),
        ])
        cfg = ProcessingConfig(
            defaults=DefaultsConfig(
                on_chunk_failure="partial",
                chunk_target_bytes=30,
                chunk_min_bytes=0,
            ),
            code=CodeConfig(),
            docs=DocsConfig(),
        )
        runner = _make_runner(agent, guard, workspace, audit,
                               config=cfg,
                               target_dir="output/batch/t14")
        monkeypatch.setattr("builtins.input", lambda _: "x")
        runner.run("input/batch", "*.py", "input/prompts/p.md")

        out = capsys.readouterr().out
        assert "[!]" in out
        assert "ЧАСТИЧНО" in out
        assert "NOT PROCESSED" in out
        assert ".failed/" in out
