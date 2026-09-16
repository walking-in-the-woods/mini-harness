"""Тесты guided mode: парсер плана и связка read → transform → write.

Модель мокается через ScriptedClient. Реальный сервер не нужен.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.agent import (
    HarnessAgent, _parse_guided_plan, _strip_code_fence,
)
from harness.audit import AuditLog
from harness.fs_guard import FileSystemGuard
from harness.proxy import ApiProxy


class ScriptedClient:
    """Возвращает заранее заданную последовательность ответов."""

    def __init__(self, *responses: str):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("ScriptedClient ran out of responses")
        content = self._responses.pop(0)
        return {"message": {"content": content, "tool_calls": []}}


@pytest.fixture
def source_workspace(tmp_path: Path) -> Path:
    (tmp_path / "input").mkdir()
    (tmp_path / "output").mkdir()
    (tmp_path / "input" / "article.md").write_text(
        "# Тест\n\nЭто тестовый файл с несколькими строками.\n"
        "Он содержит заголовки и списки.\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def guard(source_workspace: Path) -> FileSystemGuard:
    return FileSystemGuard({
        "root": str(source_workspace),
        "whitelist": ["**"],
        "blacklist": [],
        "writable": ["output/**", "notes/**", "*.md", "*.txt"],
    })


def _make_agent(guard: FileSystemGuard, workspace: Path,
                client: ScriptedClient) -> HarnessAgent:
    cfg = {
        "model": "test-model",
        "ollama_host": "http://127.0.0.1:1",
        "num_ctx": 2048,
        "num_predict": 512,
        "keep_alive": "5m",
        "temperature": 0.1,
        "workspace_root": str(workspace),
        "max_tool_rounds": 4,
        "max_read_bytes": 200_000,
        "max_write_bytes": 1_000_000,
        "max_list_entries": 500,
    }
    proxy = ApiProxy({}, timeout=5.0, max_request=1000, max_response=1000)
    audit = AuditLog(str(workspace / "audit.jsonl"))
    return HarnessAgent(cfg, guard, proxy, audit, client=client)


# ---------------------------------------------------------------------------
# _strip_code_fence
# ---------------------------------------------------------------------------

def test_strip_code_fence_plain():
    assert _strip_code_fence("just text") == "just text"


def test_strip_code_fence_wrapped():
    text = "```\nsome text\nmore\n```"
    assert _strip_code_fence(text) == "some text\nmore"


def test_strip_code_fence_with_language():
    text = "```markdown\n# Header\n\nBody\n```"
    assert _strip_code_fence(text) == "# Header\n\nBody"


def test_strip_code_fence_partial_untouched():
    # Не весь ответ в обёртке — не трогаем.
    text = "prefix\n```\ninner\n```"
    assert _strip_code_fence(text) == text


# ---------------------------------------------------------------------------
# _parse_guided_plan
# ---------------------------------------------------------------------------

def test_parse_plan_ru_summarize(guard: FileSystemGuard):
    plan = _parse_guided_plan(
        "прочитай input/article.md и напиши краткое резюме в output/summary.md",
        guard,
    )
    assert plan is not None
    assert plan["operation"] == "summarize"
    assert plan["source"] == "input/article.md"
    assert plan["target"] == "output/summary.md"


def test_parse_plan_ru_display_only(guard: FileSystemGuard):
    """Без целевого файла — guided mode только для чтения и вывода."""
    plan = _parse_guided_plan(
        "прочитай input/article.md и расскажи своими словами о чём он",
        guard,
    )
    assert plan is not None
    assert plan["operation"] in ("summarize", "explain")
    assert plan["source"] == "input/article.md"
    assert plan["target"] is None


def test_parse_plan_en_summarize(guard: FileSystemGuard):
    plan = _parse_guided_plan(
        "read input/article.md and summarize to output/summary.md",
        guard,
    )
    assert plan is not None
    assert plan["operation"] == "summarize"
    assert plan["source"] == "input/article.md"
    assert plan["target"] == "output/summary.md"


def test_parse_plan_translate(guard: FileSystemGuard):
    plan = _parse_guided_plan(
        "переведи input/article.md в output/translated.md",
        guard,
    )
    assert plan is not None
    assert plan["operation"] == "translate"


def test_parse_plan_no_keyword(guard: FileSystemGuard):
    """Промпт без ключевых слов операции — guided mode не включается."""
    assert _parse_guided_plan("переименуй input/article.md", guard) is None


def test_parse_plan_no_paths(guard: FileSystemGuard):
    """Промпт без путей — guided mode не включается."""
    assert _parse_guided_plan("напиши резюме чего-нибудь", guard) is None


def test_parse_plan_source_missing(guard: FileSystemGuard):
    """Путь-источник не существует и не разрешён на запись — плана нет."""
    plan = _parse_guided_plan(
        "прочитай input/missing.md и напиши резюме в output/summary.md",
        guard,
    )
    # source не найден → None, autonomous fallback
    assert plan is None


# ---------------------------------------------------------------------------
# Guided mode: end-to-end (мок модели)
# ---------------------------------------------------------------------------

def test_guided_mode_writes_summary(guard: FileSystemGuard,
                                     source_workspace: Path):
    client = ScriptedClient("Краткое резюме файла: это тестовый документ "
                            "с заголовками.")
    agent = _make_agent(guard, source_workspace, client)

    result = agent.run(
        "прочитай input/article.md и напиши краткое резюме в output/summary.md"
    )

    # Ровно один вызов модели — трансформация. Без tools.
    assert len(client.calls) == 1
    assert "tools" not in client.calls[0]

    # Результат — текст трансформации + pending_write.
    assert "Краткое резюме" in result["text"]
    assert len(result["pending_writes"]) == 1
    w = result["pending_writes"][0]
    assert w["canonical"] == "output/summary.md"
    assert "Краткое резюме файла" in w["content"]


def test_guided_mode_display_only(guard: FileSystemGuard,
                                   source_workspace: Path):
    client = ScriptedClient("Это тестовый файл с заголовками.")
    agent = _make_agent(guard, source_workspace, client)

    result = agent.run(
        "прочитай input/article.md и расскажи своими словами о чём он"
    )

    assert len(client.calls) == 1
    assert "tools" not in client.calls[0]
    assert "тестовый файл" in result["text"]
    assert result["pending_writes"] == []


def test_guided_mode_strips_code_fence(guard: FileSystemGuard,
                                        source_workspace: Path):
    client = ScriptedClient("```\nКраткое резюме.\n```")
    agent = _make_agent(guard, source_workspace, client)

    result = agent.run(
        "прочитай input/article.md и напиши краткое резюме в output/summary.md"
    )
    assert result["pending_writes"][0]["content"] == "Краткое резюме."


def test_guided_mode_handles_empty_response(guard: FileSystemGuard,
                                             source_workspace: Path):
    client = ScriptedClient("")
    agent = _make_agent(guard, source_workspace, client)

    result = agent.run(
        "прочитай input/article.md и напиши краткое резюме в output/summary.md"
    )
    assert "[STOP]" in result["text"]
    assert result["pending_writes"] == []


def test_guided_mode_sends_source_content(guard: FileSystemGuard,
                                           source_workspace: Path):
    """В промпт к модели должен попасть текст исходного файла."""
    client = ScriptedClient("Резюме.")
    agent = _make_agent(guard, source_workspace, client)
    agent.run(
        "прочитай input/article.md и напиши краткое резюме в output/summary.md"
    )

    call = client.calls[0]
    user_msg = call["messages"][1]["content"]
    assert "Это тестовый файл" in user_msg
    assert "input/article.md" in user_msg


def test_guided_mode_skipped_for_unknown_task(guard: FileSystemGuard,
                                               source_workspace: Path):
    """Промпт без ключевых слов → autonomous mode (tools передаются)."""
    # ScriptedClient вернёт текст, autonomous завершится после 1 раунда.
    client = ScriptedClient("Готово.")
    agent = _make_agent(guard, source_workspace, client)
    result = agent.run("посчитай файлы в input/")

    # autonomous mode передаёт tools=TOOLS
    assert "tools" in client.calls[0]
    assert result["text"] == "Готово."


# ---------------------------------------------------------------------------
# Retry при плохом резюме
# ---------------------------------------------------------------------------

def test_guided_retries_when_output_too_short(guard: FileSystemGuard,
                                               source_workspace: Path):
    """Первый ответ "Тест" — 4 символа, сработает retry."""
    client = ScriptedClient(
        "Тест",                                       # 1-й: плохо
        "Это тестовый документ с двумя заголовками.", # 2-й: хорошо
    )
    agent = _make_agent(guard, source_workspace, client)
    result = agent.run(
        "прочитай input/article.md и напиши краткое резюме в output/summary.md"
    )
    # Два вызова модели: первый + retry.
    assert len(client.calls) == 2
    assert result["pending_writes"][0]["content"] == \
        "Это тестовый документ с двумя заголовками."


def test_guided_retries_when_output_is_copy(guard: FileSystemGuard,
                                             source_workspace: Path):
    """Первый ответ — точная подстрока исходника, retry."""
    # "Текст для первого заголовка." есть в исходнике.
    client = ScriptedClient(
        "Текст для первого заголовка.",
        "Документ описывает структуру с двумя разделами.",
    )
    agent = _make_agent(guard, source_workspace, client)
    result = agent.run(
        "прочитай input/article.md и напиши краткое резюме в output/summary.md"
    )
    assert len(client.calls) == 2
    assert "структуру" in result["pending_writes"][0]["content"]


def test_guided_no_retry_when_output_is_good(guard: FileSystemGuard,
                                              source_workspace: Path):
    """Хорошее резюме — retry не запускается."""
    client = ScriptedClient(
        "Документ содержит тестовую структуру с двумя заголовками "
        "и примерами форматирования."
    )
    agent = _make_agent(guard, source_workspace, client)
    agent.run(
        "прочитай input/article.md и напиши краткое резюме в output/summary.md"
    )
    assert len(client.calls) == 1


def test_guided_no_retry_for_non_summarize(guard: FileSystemGuard,
                                            source_workspace: Path):
    """Для перевода короткий результат — нормально, retry не срабатывает."""
    client = ScriptedClient("Test")
    agent = _make_agent(guard, source_workspace, client)
    agent.run(
        "переведи input/article.md в output/translated.md"
    )
    assert len(client.calls) == 1
