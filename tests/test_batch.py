"""Тесты batch-режима. Модель мокается через ScriptedAgent."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.audit import AuditLog
from harness.batch import (
    BatchItem, BatchRunner,
    _check_brace_balance,
    _check_line_preservation,
    _check_python_structure,
    _check_structure,
)
from harness.fs_guard import FileSystemGuard


class ScriptedAgent:
    """Фиктивный агент, возвращает заранее заданные результаты."""

    def __init__(self, responses: list[tuple[str | None, str | None]]):
        self._responses = list(responses)
        self.calls: list[tuple[str, str, str | None]] = []
        self.max_read = 200_000
        self.audit = AuditLog("/dev/null")

    def transform_for_batch(self, source_path: str, source_content: str,
                            prompt_content: str,
                            retry_hint: str | None = None
                            ) -> tuple[str | None, str | None]:
        self.calls.append((source_path, source_content, retry_hint))
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
    """Guard с ext_allow_paths, чтобы код в output/batch/ был разрешён."""
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


def _make_runner(agent, guard, workspace, audit, target_dir=None):
    return BatchRunner(agent, guard, workspace, audit,
                        target_dir=target_dir)


# ---------------------------------------------------------------------------
# _collect_files
# ---------------------------------------------------------------------------

def test_collect_files_matches_glob(workspace, guard, audit):
    (workspace / "input" / "batch" / "a.py").write_text("x")
    (workspace / "input" / "batch" / "b.py").write_text("x")
    (workspace / "input" / "batch" / "c.txt").write_text("x")

    runner = _make_runner(None, guard, workspace, audit)
    files = runner._collect_files("input/batch", "*.py")
    assert files == ["input/batch/a.py", "input/batch/b.py"]


def test_collect_files_ignores_directories(workspace, guard, audit):
    (workspace / "input" / "batch" / "a.py").write_text("x")
    (workspace / "input" / "batch" / "subdir").mkdir()

    runner = _make_runner(None, guard, workspace, audit)
    files = runner._collect_files("input/batch", "*")
    assert files == ["input/batch/a.py"]


def test_collect_files_missing_dir(workspace, guard, audit):
    runner = _make_runner(None, guard, workspace, audit)
    with pytest.raises(ValueError, match="does not exist"):
        runner._collect_files("input/missing", "*.py")


def test_collect_files_not_a_dir(workspace, guard, audit):
    (workspace / "input" / "batch" / "file.txt").write_text("x")
    runner = _make_runner(None, guard, workspace, audit)
    with pytest.raises(ValueError, match="not a directory"):
        runner._collect_files("input/batch/file.txt", "*.py")


# ---------------------------------------------------------------------------
# Полный прогон
# ---------------------------------------------------------------------------

def test_batch_writes_all_transformed(workspace, guard, audit,
                                        monkeypatch):
    (workspace / "input" / "batch" / "a.py").write_text("def foo(): pass")
    (workspace / "input" / "batch" / "b.py").write_text("def bar(): pass")
    (workspace / "input" / "prompts" / "p.md").write_text("add docstrings")

    agent = ScriptedAgent([
        ("def foo():\n    '''doc'''\n    pass\n", None),
        ("def bar():\n    '''doc'''\n    pass\n", None),
    ])
    runner = _make_runner(agent, guard, workspace, audit,
                           target_dir="output/batch/test1")

    monkeypatch.setattr("builtins.input", lambda _: runner.nonce)
    runner.run("input/batch", "*.py", "input/prompts/p.md")

    out = workspace / "output" / "batch" / "test1"
    assert (out / "a.py").exists()
    assert (out / "b.py").exists()


def test_batch_skip_excludes_item(workspace, guard, audit, monkeypatch):
    (workspace / "input" / "batch" / "a.py").write_text("def f(): pass")
    (workspace / "input" / "batch" / "b.py").write_text("def g(): pass")
    (workspace / "input" / "prompts" / "p.md").write_text("p")

    agent = ScriptedAgent([
        ("def f():\n    '''doc'''\n    pass\n", None),
        ("def g():\n    '''doc'''\n    pass\n", None),
    ])
    runner = _make_runner(agent, guard, workspace, audit,
                           target_dir="output/batch/test2")

    inputs = iter(["1 skip", runner.nonce])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    runner.run("input/batch", "*.py", "input/prompts/p.md")

    out = workspace / "output" / "batch" / "test2"
    assert not (out / "a.py").exists()
    assert (out / "b.py").exists()


def test_batch_skip_and_restore(workspace, guard, audit, monkeypatch):
    (workspace / "input" / "batch" / "a.py").write_text("def f(): pass")
    (workspace / "input" / "prompts" / "p.md").write_text("p")

    agent = ScriptedAgent([
        ("def f():\n    '''doc'''\n    pass\n", None),
    ])
    runner = _make_runner(agent, guard, workspace, audit,
                           target_dir="output/batch/test3")

    inputs = iter(["1 skip", "1 skip", runner.nonce])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    runner.run("input/batch", "*.py", "input/prompts/p.md")

    out = workspace / "output" / "batch" / "test3"
    assert (out / "a.py").exists()


def test_batch_cancel_writes_nothing(workspace, guard, audit,
                                       monkeypatch):
    (workspace / "input" / "batch" / "a.py").write_text("def f(): pass")
    (workspace / "input" / "prompts" / "p.md").write_text("p")

    agent = ScriptedAgent([
        ("def f():\n    '''doc'''\n    pass\n", None),
    ])
    runner = _make_runner(agent, guard, workspace, audit,
                           target_dir="output/batch/test4")

    monkeypatch.setattr("builtins.input", lambda _: "wrong code")
    runner.run("input/batch", "*.py", "input/prompts/p.md")

    target_dir = workspace / "output" / "batch" / "test4"
    assert not target_dir.exists()


def test_batch_failed_item_is_skipped_in_write(
        workspace, guard, audit, monkeypatch):
    (workspace / "input" / "batch" / "a.py").write_text("def f(): pass")
    (workspace / "input" / "batch" / "b.py").write_text("def g(): pass")
    (workspace / "input" / "prompts" / "p.md").write_text("p")

    agent = ScriptedAgent([
        ("def f():\n    '''doc'''\n    pass\n", None),
        (None, "reasoning leaked twice"),
    ])
    runner = _make_runner(agent, guard, workspace, audit,
                           target_dir="output/batch/test5")

    monkeypatch.setattr("builtins.input", lambda _: runner.nonce)
    runner.run("input/batch", "*.py", "input/prompts/p.md")

    out = workspace / "output" / "batch" / "test5"
    assert (out / "a.py").exists()
    assert not (out / "b.py").exists()


def test_batch_empty_source_dir(workspace, guard, audit, capsys):
    (workspace / "input" / "prompts" / "p.md").write_text("p")

    runner = _make_runner(None, guard, workspace, audit)
    runner.run("input/batch", "*.py", "input/prompts/p.md")

    captured = capsys.readouterr()
    assert "нет файлов" in captured.out


def test_batch_missing_prompt_file(workspace, guard, audit, capsys):
    (workspace / "input" / "batch" / "a.py").write_text("x")

    runner = _make_runner(None, guard, workspace, audit)
    runner.run("input/batch", "*.py", "input/prompts/missing.md")

    captured = capsys.readouterr()
    assert "prompt-файл" in captured.out


def test_batch_default_target_dir_has_timestamp(
        workspace, guard, audit, monkeypatch):
    (workspace / "input" / "batch" / "a.py").write_text("def f(): pass")
    (workspace / "input" / "prompts" / "p.md").write_text("p")

    agent = ScriptedAgent([
        ("def f():\n    '''doc'''\n    pass\n", None),
    ])
    runner = _make_runner(agent, guard, workspace, audit)

    monkeypatch.setattr("builtins.input", lambda _: runner.nonce)
    runner.run("input/batch", "*.py", "input/prompts/p.md")

    assert len(runner.items) == 1
    assert runner.items[0].target_rel.startswith("output/batch/")
    assert runner.items[0].target_rel.endswith("/a.py")


def test_batch_target_not_writable_marks_failed(
        workspace, guard, audit, monkeypatch):
    (workspace / "input" / "batch" / "a.py").write_text("x")
    (workspace / "input" / "prompts" / "p.md").write_text("p")

    agent = ScriptedAgent([])
    runner = _make_runner(agent, guard, workspace, audit,
                           target_dir="notes/batch")

    monkeypatch.setattr("builtins.input", lambda _: "x")
    runner.run("input/batch", "*.py", "input/prompts/p.md")

    assert runner.items[0].status == "failed"
    assert "writable" in (runner.items[0].error or "").lower()
    assert agent.calls == []


def test_batch_skip_invalid_number(workspace, guard, audit,
                                     monkeypatch, capsys):
    (workspace / "input" / "batch" / "a.py").write_text("def f(): pass")
    (workspace / "input" / "prompts" / "p.md").write_text("p")

    agent = ScriptedAgent([
        ("def f():\n    '''doc'''\n    pass\n", None),
    ])
    runner = _make_runner(agent, guard, workspace, audit,
                           target_dir="output/batch/test6")

    inputs = iter(["99 skip", runner.nonce])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    runner.run("input/batch", "*.py", "input/prompts/p.md")

    out = workspace / "output" / "batch" / "test6"
    assert (out / "a.py").exists()


def test_batch_skip_failed_item_warns(workspace, guard, audit,
                                        monkeypatch, capsys):
    (workspace / "input" / "batch" / "a.py").write_text("x")
    (workspace / "input" / "prompts" / "p.md").write_text("p")

    agent = ScriptedAgent([(None, "boom")])
    runner = _make_runner(agent, guard, workspace, audit,
                           target_dir="output/batch/test7")

    inputs = iter(["1 skip", runner.nonce])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    runner.run("input/batch", "*.py", "input/prompts/p.md")

    out = workspace / "output" / "batch" / "test7"
    assert not (out / "a.py").exists()


# ---------------------------------------------------------------------------
# Python AST (для .py)
# ---------------------------------------------------------------------------

def test_check_python_structure_ok_identical():
    src = "def f(a, b):\n    return a + b\n"
    assert _check_python_structure(src, src) is None


def test_check_python_structure_ok_with_docstring_added():
    original = "def add(a, b):\n    return a + b\n"
    transformed = (
        'def add(a, b):\n'
        '    """Adds two numbers."""\n'
        '    return a + b\n'
    )
    assert _check_python_structure(original, transformed) is None


def test_check_python_structure_detects_lost_body():
    original = "def add(a, b):\n    return a + b\n"
    transformed = (
        'def add(a, b):\n'
        '    """Adds two numbers."""\n'
    )
    err = _check_python_structure(original, transformed)
    assert err is not None
    assert "add" in err
    assert "body" in err.lower()


def test_check_python_structure_detects_lost_definition():
    original = (
        "def add(a, b):\n"
        "    return a + b\n"
        "\n"
        "def multiply(a, b):\n"
        "    return a * b\n"
    )
    transformed = (
        'def add(a, b):\n'
        '    """Adds."""\n'
        '    return a + b\n'
    )
    err = _check_python_structure(original, transformed)
    assert err is not None
    assert "multiply" in err


def test_check_python_structure_detects_invalid_python():
    original = "def f():\n    return 1\n"
    transformed = "def f():\n    return 1\n@@@ broken"
    err = _check_python_structure(original, transformed)
    assert err is not None
    assert "does not parse" in err


def test_check_python_structure_ignores_docstring_in_size():
    original = (
        "def f():\n"
        '    """Old docstring."""\n'
        "    x = 1\n"
        "    y = 2\n"
        "    return x + y\n"
    )
    transformed = (
        "def f():\n"
        '    """New longer docstring with more text."""\n'
        "    x = 1\n"
        "    y = 2\n"
        "    return x + y\n"
    )
    assert _check_python_structure(original, transformed) is None


def test_check_python_structure_ok_small_body_reduction():
    original = (
        "def f():\n"
        "    a = 1\n"
        "    b = 2\n"
        "    c = 3\n"
        "    return a + b + c\n"
    )
    transformed = (
        'def f():\n'
        '    """Doc."""\n'
        "    a = 1\n"
        "    b = 2\n"
        "    return a + b\n"
    )
    assert _check_python_structure(original, transformed) is None


def test_check_python_structure_detects_class_body_loss():
    original = (
        "class Counter:\n"
        "    def __init__(self):\n"
        "        self.value = 0\n"
        "\n"
        "    def inc(self):\n"
        "        self.value += 1\n"
        "        return self.value\n"
    )
    transformed = (
        'class Counter:\n'
        '    """Counter class."""\n'
        "\n"
        "    def __init__(self):\n"
        '        """Init."""\n'
        "\n"
        "    def inc(self):\n"
        '        """Increments."""\n'
    )
    err = _check_python_structure(original, transformed)
    assert err is not None
    assert "body" in err.lower()


# ---------------------------------------------------------------------------
# Line preservation (универсальный)
# ---------------------------------------------------------------------------

def test_line_preservation_ok_identical():
    src = "function add(a, b) {\n    return a + b;\n}\n"
    assert _check_line_preservation(src, src) is None


def test_line_preservation_ok_jsdoc_added():
    original = (
        "function add(a, b) {\n"
        "    return a + b;\n"
        "}\n"
    )
    transformed = (
        "/**\n"
        " * Adds two numbers.\n"
        " */\n"
        "function add(a, b) {\n"
        "    return a + b;\n"
        "}\n"
    )
    assert _check_line_preservation(original, transformed) is None


def test_line_preservation_detects_type_hint_change():
    """Line preservation строг: добавление type hints меняет
    сигнатуру и ловится как потеря. Это осознанное решение —
    для «add X» задач менять сигнатуру неправильно."""
    original = "function add(a, b) {\n    return a + b;\n}\n"
    transformed = (
        "function add(a: number, b: number): number {\n"
        "    return a + b;\n"
        "}\n"
    )
    # Сигнатура изменилась — значимая строка потеряна.
    err = _check_line_preservation(original, transformed)
    assert err is not None
    assert "lost" in err


def test_line_preservation_detects_lost_body_ts():
    original = (
        "function add(a, b) {\n"
        "    return a + b;\n"
        "}\n"
        "\n"
        "function multiply(a, b) {\n"
        "    return a * b;\n"
        "}\n"
    )
    transformed = (
        "/** Adds two numbers. */\n"
        "function add(a, b) {\n"
        "    return a + b;\n"
        "}\n"
        "\n"
        "/** Multiplies. */\n"
        "function multiply(a, b) {\n"
        "}\n"
    )
    err = _check_line_preservation(original, transformed)
    assert err is not None
    assert "lost" in err


def test_line_preservation_detects_lost_text():
    """Markdown-файл: значимые строки текста тоже проверяются."""
    original = (
        "# Заголовок\n"
        "\n"
        "Это тестовый файл.\n"
    )
    transformed = (
        "# Заголовок\n"
        "\n"
    )
    err = _check_line_preservation(original, transformed)
    assert err is not None
    assert "lost" in err


def test_line_preservation_ignores_comments_and_docstrings():
    original = (
        "# TODO: fix\n"
        "def f():\n"
        "    return 1\n"
    )
    transformed = (
        "# FIXED\n"
        '"""Module docstring."""\n'
        "def f():\n"
        '    """Doc."""\n'
        "    return 1\n"
    )
    assert _check_line_preservation(original, transformed) is None


# ---------------------------------------------------------------------------
# Brace balance (для C-like)
# ---------------------------------------------------------------------------

def test_brace_balance_ok():
    src = "function f() {\n    return 1;\n}\n"
    assert _check_brace_balance(src, src) is None


def test_brace_balance_detects_unbalanced_output():
    original = "function f() {\n    return 1;\n}\n"
    transformed = "function f() {\n    return 1;\n"  # нет }
    err = _check_brace_balance(original, transformed)
    assert err is not None
    assert "brace" in err.lower()


def test_brace_balance_ignores_strings_and_comments():
    src = (
        'function f() {\n'
        '    // {{ не считается\n'
        '    return "{ не считается }";\n'
        '}\n'
    )
    assert _check_brace_balance(src, src) is None


# ---------------------------------------------------------------------------
# Диспетчер _check_structure
# ---------------------------------------------------------------------------

def test_structure_dispatcher_py():
    """Для .py используется AST, не line preservation."""
    original = "def f():\n    a = 1\n    b = 2\n    return a + b\n"
    transformed = (
        'def f():\n'
        '    """Doc."""\n'
        "    a = 1\n"
        "    b = 2\n"
        "    return a + b\n"
    )
    assert _check_structure(original, transformed, "f.py") is None


def test_structure_dispatcher_ts():
    """Для .ts применяется brace balance + line preservation."""
    original = "function f() {\n    return 1;\n}\n"
    transformed = "function f() {\n    return 1;\n"  # нет }
    err = _check_structure(original, transformed, "f.ts")
    assert err is not None
    assert "brace" in err.lower()


def test_structure_dispatcher_unknown_ext():
    """Для неизвестного расширения — только line preservation."""
    original = "line one\nline two\n"
    transformed = "line one\n"
    err = _check_structure(original, transformed, "f.unknownext")
    assert err is not None
    assert "lost" in err


# ---------------------------------------------------------------------------
# End-to-end: структурный провал
# ---------------------------------------------------------------------------

def test_batch_structural_failure_marks_item_failed(
        workspace, guard, audit, monkeypatch):
    """End-to-end: тело потеряно, item помечается failed, не пишется."""
    (workspace / "input" / "batch" / "a.py").write_text(
        "def add(a, b):\n    return a + b\n"
    )
    (workspace / "input" / "prompts" / "p.md").write_text("p")

    # Два одинаковых ответа: первая попытка и retry оба дают
    # результат без тела. Мок должен покрыть оба вызова.
    body_lost = 'def add(a, b):\n    """Adds two numbers."""\n'
    agent = ScriptedAgent([
        (body_lost, None),
        (body_lost, None),
    ])
    runner = _make_runner(agent, guard, workspace, audit,
                           target_dir="output/batch/test8")

    monkeypatch.setattr("builtins.input", lambda _: runner.nonce)
    runner.run("input/batch", "*.py", "input/prompts/p.md")

    out = workspace / "output" / "batch" / "test8"
    assert not (out / "a.py").exists()
    assert runner.items[0].status == "failed"
    assert "structure" in (runner.items[0].error or "").lower()


def test_batch_structural_retry_with_hint(
        workspace, guard, audit, monkeypatch):
    """End-to-end: первая попытка теряет тело, retry с хинтом
    в user message — сохраняет."""
    (workspace / "input" / "batch" / "a.py").write_text(
        "def add(a, b):\n    return a + b\n"
    )
    (workspace / "input" / "prompts" / "p.md").write_text("p")

    body_lost = 'def add(a, b):\n    """Doc."""\n'
    body_ok = 'def add(a, b):\n    """Doc."""\n    return a + b\n'
    agent = ScriptedAgent([
        (body_lost, None),  # первая — плохо
        (body_ok, None),    # retry — хорошо
    ])
    runner = _make_runner(agent, guard, workspace, audit,
                           target_dir="output/batch/test10")

    monkeypatch.setattr("builtins.input", lambda _: runner.nonce)
    runner.run("input/batch", "*.py", "input/prompts/p.md")

    out = workspace / "output" / "batch" / "test10"
    assert (out / "a.py").exists()
    content = (out / "a.py").read_text()
    assert "return a + b" in content

    # Проверяем, что второй вызов был с hint
    assert len(agent.calls) == 2
    _, _, second_hint = agent.calls[1]
    assert second_hint is not None
    assert "ВНИМАНИЕ" in second_hint


def test_batch_structural_ok_still_written(
        workspace, guard, audit, monkeypatch):
    """Если тело сохранено — item записывается."""
    (workspace / "input" / "batch" / "a.py").write_text(
        "def add(a, b):\n    return a + b\n"
    )
    (workspace / "input" / "prompts" / "p.md").write_text("p")

    agent = ScriptedAgent([
        ('def add(a, b):\n'
         '    """Adds two numbers."""\n'
         '    return a + b\n', None),
    ])
    runner = _make_runner(agent, guard, workspace, audit,
                           target_dir="output/batch/test9")

    monkeypatch.setattr("builtins.input", lambda _: runner.nonce)
    runner.run("input/batch", "*.py", "input/prompts/p.md")

    out = workspace / "output" / "batch" / "test9"
    assert (out / "a.py").exists()
    content = (out / "a.py").read_text()
    assert "return a + b" in content
    assert '"""Adds two numbers."""' in content


def test_structure_dispatcher_catches_python_signature_change():
    """Изменение сигнатуры ловится line preservation, даже если
    AST его пропускает (определение есть, тело непустое)."""
    original = "def add(a, b):\n    return a + b\n"
    transformed = (
        "def add(a: int, b: int) -> int:\n"
        '    """Adds two numbers."""\n'
        "    return a + b\n"
    )
    err = _check_structure(original, transformed, "f.py")
    assert err is not None
    assert "lost" in err.lower()
