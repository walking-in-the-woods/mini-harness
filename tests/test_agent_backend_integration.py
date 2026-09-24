"""Интеграционные тесты HarnessAgent с бэкендами.

Две секции:

  A. HarnessAgent + ScriptedBackend (реализация ChatBackend).
     Проверяет, что агент работает с произвольным бэкендом через
     интерфейс, а не через конкретный ollama.Client.

  B. HarnessAgent + реальный LlamaCppBackend с замоканным httpx.
     Проверяет полный путь: OpenAI-форма → нормализация в бэкенде →
     _parse_tool_call → _dispatch.

Отдельно проверяется приоритет параметров: backend= > client= >
build_backend(cfg).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from harness.agent import (
    HarnessAgent,
    _extract_tool_calls_from_content,
    _tool_call_to_dict,
)
from harness.audit import AuditLog
from harness.backends import BackendError, ChatBackend, OllamaBackend
from harness.backends.llamacpp_backend import LlamaCppBackend
from harness.fs_guard import FileSystemGuard
from harness.proxy import ApiProxy


# ═══════════════════════════════════════════════════════════════════════════
# ScriptedBackend — реализация ChatBackend для секции A
# ═══════════════════════════════════════════════════════════════════════════

class ScriptedBackend(ChatBackend):
    """Возвращает заранее заданные ответы в нормализованном формате.

    Записывает все kwargs вызовов chat(), чтобы тесты могли
    проверить, что агент передаёт num_ctx/think/tools и т. д.
    """

    def __init__(self, *responses: dict):
        self._responses = list(responses)
        self.calls: list[dict] = []

    @property
    def name(self) -> str:
        return "scripted"

    def chat(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("ScriptedBackend ran out of responses")
        return self._responses.pop(0)

    def health(self) -> bool:
        return True


def _empty_message(content: str = "",
                   tool_calls: list | None = None) -> dict:
    return {"message": {
        "content": content,
        "tool_calls": tool_calls or [],
    }}


# ═══════════════════════════════════════════════════════════════════════════
# Общие фикстуры
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def ws(tmp_path: Path) -> Path:
    (tmp_path / "input").mkdir()
    (tmp_path / "output").mkdir()
    (tmp_path / "notes").mkdir()
    (tmp_path / "input" / "article.md").write_text(
        "# Заголовок\n\nНесколько строк про тестовый файл.\n",
        encoding="utf-8",
    )
    (tmp_path / "notes" / "hello.txt").write_text(
        "PONG-42\n", encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def guard(ws: Path) -> FileSystemGuard:
    return FileSystemGuard({
        "root": str(ws),
        "whitelist": ["**"],
        "blacklist": ["**/.git/**"],
        "writable": ["output/**", "notes/**", "*.md", "*.txt"],
    })


def _cfg(ws: Path) -> dict:
    return {
        "model": "test-model",
        "ollama_host": "http://127.0.0.1:1",
        "num_ctx": 2048,
        "num_predict": 512,
        "keep_alive": "5m",
        "temperature": 0.1,
        "workspace_root": str(ws),
        "max_tool_rounds": 4,
        "max_read_bytes": 200_000,
        "max_write_bytes": 1_000_000,
        "max_list_entries": 500,
    }


def _make_agent(ws: Path, guard: FileSystemGuard,
                *, backend: ChatBackend | None = None,
                client=None,
                cfg_override: dict | None = None) -> HarnessAgent:
    cfg = _cfg(ws)
    if cfg_override:
        cfg.update(cfg_override)
    proxy = ApiProxy({}, timeout=5.0, max_request=1000, max_response=1000)
    audit = AuditLog(str(ws / "audit.jsonl"))
    if backend is not None:
        return HarnessAgent(cfg, guard, proxy, audit, backend=backend)
    if client is not None:
        return HarnessAgent(cfg, guard, proxy, audit, client=client)
    return HarnessAgent(cfg, guard, proxy, audit)


# ═══════════════════════════════════════════════════════════════════════════
# Секция A: ScriptedBackend
# ═══════════════════════════════════════════════════════════════════════════

def test_backend_param_priority_over_client(ws, guard):
    """При наличии обоих — backend= выигрывает."""
    scripted = ScriptedBackend(_empty_message("from backend"))

    class _Client:
        def chat(self, **kw):  # pragma: no cover
            raise AssertionError("client= должен быть проигнорирован")

    agent = _make_agent(ws, guard, backend=scripted,
                        client=_Client())
    assert agent.backend is scripted


def test_client_param_wrapped_in_ollama_backend(ws, guard):
    """client= оборачивается в OllamaBackend для обратной совместимости."""

    class _Client:
        def chat(self, **kw):  # pragma: no cover
            raise AssertionError("chat не должен вызываться")

    agent = _make_agent(ws, guard, client=_Client())
    assert isinstance(agent.backend, OllamaBackend)


def test_backend_built_from_cfg_when_nothing_passed(ws, guard):
    """Ничего не передано — build_backend(cfg) по cfg['backend']."""
    agent = _make_agent(ws, guard)
    assert isinstance(agent.backend, OllamaBackend)
    assert agent.backend.name == "ollama"


def test_unknown_backend_in_cfg_raises(ws, guard):
    with pytest.raises(BackendError, match="unknown backend"):
        _make_agent(ws, guard,
                    cfg_override={"backend": "vllm",
                                   "ollama_host": "http://x"})


def test_guided_mode_with_scripted_backend(ws, guard):
    """Guided mode работает через ChatBackend, не через ollama.Client."""
    scripted = ScriptedBackend(_empty_message(
        "Это тестовый документ с одним заголовком и парой строк."
    ))
    agent = _make_agent(ws, guard, backend=scripted)

    result = agent.run(
        "прочитай input/article.md и напиши краткое резюме в output/summary.md"
    )
    assert len(scripted.calls) == 1
    call = scripted.calls[0]
    assert "tools" not in call
    assert call["model"] == "test-model"
    assert call["num_ctx"] == 2048
    assert call["num_predict"] == 512
    assert call["keep_alive"] == "5m"
    assert call["think"] is False

    assert len(result["pending_writes"]) == 1
    assert result["pending_writes"][0]["canonical"] == "output/summary.md"


def test_autonomous_with_scripted_backend_tool_call(ws, guard):
    """Autonomous mode: tool_call с arguments в виде dict."""
    scripted = ScriptedBackend(
        _empty_message("", tool_calls=[{
            "function": {
                "name": "read_file",
                "arguments": {"path": "notes/hello.txt"},
            },
        }]),
        _empty_message("Внутри PONG-42"),
    )
    agent = _make_agent(ws, guard, backend=scripted)

    result = agent.run("прочитай notes/hello.txt")

    assert len(scripted.calls) == 2
    assert scripted.calls[0]["tools"]  # непустой список TOOLS
    assert "PONG-42" in result["text"]


def test_backend_error_written_to_audit(ws, guard):
    """Исключение из backend.chat() → backend_error в аудите."""

    class _BoomBackend(ChatBackend):
        @property
        def name(self) -> str:
            return "boom"

        def chat(self, **kw):
            raise RuntimeError("server exploded")

        def health(self) -> bool:
            return False

    agent = _make_agent(ws, guard, backend=_BoomBackend())
    result = agent.run("прочитай notes/hello.txt")

    assert "[backend error]" in result["text"]

    log_path = ws / "audit.jsonl"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    backend_errors = [
        json.loads(l) for l in lines
        if json.loads(l).get("event") == "backend_error"
    ]
    assert len(backend_errors) == 1
    assert backend_errors[0]["backend"] == "boom"
    assert "exploded" in backend_errors[0]["error"]


# ═══════════════════════════════════════════════════════════════════════════
# Секция B: реальный LlamaCppBackend + замоканный httpx
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def fake_llamacpp(monkeypatch):
    """Мок httpx.Client с очередью ответов.

    state["responses"] — список (status, data). Каждый POST берёт
    следующий. Ошибка, если очередь пуста.
    """
    state = {
        "init_kwargs": {},
        "requests": [],
        "responses": [],
    }

    class _Resp:
        def __init__(self, status, data):
            self.status_code = status
            self._data = data
            self.text = json.dumps(data)

        def json(self):
            return self._data

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    "err", request=None, response=self,
                )

    class _Client:
        def __init__(self, **kw):
            state["init_kwargs"] = kw

        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

        def post(self, url, headers=None, json=None):
            state["requests"].append({
                "url": url, "headers": headers, "json": json,
            })
            if not state["responses"]:
                raise AssertionError("нет заготовленных ответов")
            return _Resp(*state["responses"].pop(0))

        def get(self, url, headers=None):
            state["requests"].append({
                "url": url, "headers": headers, "json": None,
            })
            if not state["responses"]:
                raise AssertionError("нет заготовленных ответов")
            return _Resp(*state["responses"].pop(0))

    monkeypatch.setattr(
        "harness.backends.llamacpp_backend.httpx.Client", _Client,
    )
    return state


def _llamacpp_backend() -> LlamaCppBackend:
    return LlamaCppBackend("http://127.0.0.1:8080", timeout=42.0)


def test_llamacpp_guided_mode_end_to_end(ws, guard, fake_llamacpp):
    """Guided mode через LlamaCppBackend, httpx замокан."""
    fake_llamacpp["responses"] = [
        (200, {"choices": [{"message": {
            "content": "Документ описывает тестовый файл с заголовком.",
        }}]}),
    ]

    agent = _make_agent(ws, guard, backend=_llamacpp_backend())
    result = agent.run(
        "прочитай input/article.md и напиши краткое резюме в output/summary.md"
    )

    assert len(fake_llamacpp["requests"]) == 1
    req = fake_llamacpp["requests"][0]
    assert req["url"] == "http://127.0.0.1:8080/v1/chat/completions"
    payload = req["json"]
    assert payload["max_tokens"] == 512
    assert payload["temperature"] == 0.1
    assert "tools" not in payload        # guided mode — без tools
    assert "num_ctx" not in payload      # llama.cpp игнорирует
    assert "keep_alive" not in payload

    assert len(result["pending_writes"]) == 1
    assert result["pending_writes"][0]["canonical"] == "output/summary.md"


def test_llamacpp_autonomous_tool_call_json_string_args(
        ws, guard, fake_llamacpp):
    """Ключевой сценарий: llama.cpp отдаёт arguments как JSON-строку.

    Без нормализации в бэкенде _parse_tool_call получил бы строку,
    а не dict, и read_file упал бы с AttributeError.
    """
    fake_llamacpp["responses"] = [
        # Раунд 1: tool_call с arguments как JSON-строкой
        (200, {"choices": [{"message": {
            "content": None,
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": json.dumps({"path": "notes/hello.txt"}),
                },
            }],
        }}]}),
        # Раунд 2: финальный ответ
        (200, {"choices": [{"message": {
            "content": "В файле написано PONG-42.",
            "tool_calls": [],
        }}]}),
    ]

    agent = _make_agent(ws, guard, backend=_llamacpp_backend())
    result = agent.run("прочитай notes/hello.txt и скажи что внутри")

    assert len(fake_llamacpp["requests"]) == 2
    # Второй запрос содержит историю с tool_result
    second_payload = fake_llamacpp["requests"][1]["json"]
    messages = second_payload["messages"]
    assert any(m.get("role") == "tool" for m in messages)

    assert "PONG-42" in result["text"]


def test_llamacpp_autonomous_multi_round_with_tools(
        ws, guard, fake_llamacpp):
    """Второй раунд тоже отправляет tools (не только первый)."""
    fake_llamacpp["responses"] = [
        (200, {"choices": [{"message": {
            "content": None,
            "tool_calls": [{"function": {
                "name": "read_file",
                "arguments": json.dumps({"path": "notes/hello.txt"}),
            }}],
        }}]}),
        (200, {"choices": [{"message": {
            "content": "done",
            "tool_calls": [],
        }}]}),
    ]

    agent = _make_agent(ws, guard, backend=_llamacpp_backend())
    agent.run("прочитай notes/hello.txt")

    for i, req in enumerate(fake_llamacpp["requests"]):
        assert "tools" in req["json"], f"round {i}: tools отсутствует"


def test_llamacpp_tool_calls_from_text_content(
        ws, guard, fake_llamacpp):
    """Fallback: chatml без --jinja отдаёт tool_call текстом в content.

    _extract_tool_calls_from_content должен выловить JSON-объект
    в начале строки и превратить в tool_call.
    """
    text_with_call = (
        '{"name": "read_file", "arguments": {"path": "notes/hello.txt"}}'
    )
    fake_llamacpp["responses"] = [
        (200, {"choices": [{"message": {
            "content": text_with_call,
            "tool_calls": [],
        }}]}),
        (200, {"choices": [{"message": {
            "content": "Внутри PONG-42",
            "tool_calls": [],
        }}]}),
    ]

    agent = _make_agent(ws, guard, backend=_llamacpp_backend())
    result = agent.run("прочитай notes/hello.txt")

    assert len(fake_llamacpp["requests"]) == 2
    second = fake_llamacpp["requests"][1]["json"]
    assert any(m.get("role") == "tool" for m in second["messages"])
    assert "PONG-42" in result["text"]


def test_llamacpp_http_error_autonomous_written_to_audit(
        ws, guard, fake_llamacpp):
    """Autonomous mode: HTTP 500 → [backend error] в тексте + аудит.

    Запрос без ключевых слов guided mode (нет summarize / explain /
    translate / rewrite / custom prompt). Уходит в autonomous loop,
    первый же вызов backend.chat() бросает — исключение ловится
    в _run_autonomous, пользователь видит [backend error].
    """
    fake_llamacpp["responses"] = [
        (500, {"error": "context length exceeded"}),
    ]

    agent = _make_agent(ws, guard, backend=_llamacpp_backend())
    # "покажи" не входит в _TASK_KEYWORDS, prompt-файла нет →
    # operation=None → автономный режим.
    result = agent.run("прочитай notes/hello.txt")

    assert "[backend error]" in result["text"]

    lines = (ws / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    errors = [
        json.loads(l) for l in lines
        if json.loads(l).get("event") == "backend_error"
    ]
    assert len(errors) == 1
    assert errors[0]["backend"] == "llamacpp"
    assert "500" in errors[0]["error"]


def test_llamacpp_http_error_guided_written_to_audit(
        ws, guard, fake_llamacpp):
    """Guided mode: HTTP 500 → [STOP] в тексте, но аудит всё равно
    содержит backend_error с backend='llamacpp'.

    Отличие от autonomous: пользователю не показывается сырое
    исключение, потому что guided mode не различает «модель
    вернула пустоту» и «сеть отвалилась» — задача в обоих случаях
    не выполнена. Аудит, однако, фиксирует именно backend_error,
    а не guided_transform_failed.
    """
    fake_llamacpp["responses"] = [
        (500, {"error": "context length exceeded"}),
    ]

    agent = _make_agent(ws, guard, backend=_llamacpp_backend())
    # "расскажи" попадает под explain → guided mode.
    result = agent.run("прочитай notes/hello.txt и расскажи о нём")

    assert "[STOP]" in result["text"]
    assert "backend error" not in result["text"]

    lines = (ws / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    errors = [
        json.loads(l) for l in lines
        if json.loads(l).get("event") == "backend_error"
    ]
    assert len(errors) == 1
    assert errors[0]["backend"] == "llamacpp"
    assert "500" in errors[0]["error"]


def test_llamacpp_timeout_propagated_to_httpx(
        ws, guard, fake_llamacpp):
    """Кастомный таймаут из cfg доходит до httpx.Client."""
    fake_llamacpp["responses"] = [
        (200, {"choices": [{"message": {"content": "ok"}}]}),
    ]

    backend = LlamaCppBackend("http://127.0.0.1:8080", timeout=123.0)
    agent = _make_agent(ws, guard, backend=backend)
    agent.run("прочитай input/article.md и напиши краткое резюме "
              "в output/summary.md")

    assert fake_llamacpp["init_kwargs"]["timeout"] == 123.0


def test_llamacpp_empty_content_and_no_tools_stops(ws, guard,
                                                    fake_llamacpp):
    """Пустой content и нет tool_calls → [STOP], без зацикливания."""
    fake_llamacpp["responses"] = [
        (200, {"choices": [{"message": {
            "content": "",
            "tool_calls": [],
        }}]}),
    ]

    agent = _make_agent(ws, guard, backend=_llamacpp_backend())
    result = agent.run("прочитай notes/hello.txt и объясни")

    assert "[STOP]" in result["text"]


# ═══════════════════════════════════════════════════════════════════════════
# Вспомогательные функции
# ═══════════════════════════════════════════════════════════════════════════

def test_tool_call_to_dict_passes_normalized_llamacpp_shape():
    """LlamaCppBackend уже нормализует аргументы — _tool_call_to_dict
    должен пропустить dict насквозь."""
    normalized = {"function": {
        "name": "read_file",
        "arguments": {"path": "a.txt"},     # уже dict
    }}
    assert _tool_call_to_dict(normalized) == normalized


def test_extract_tool_calls_from_content_single():
    content = '{"name": "read_file", "arguments": {"path": "x"}}'
    calls = _extract_tool_calls_from_content(content)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "read_file"
    assert calls[0]["function"]["arguments"] == {"path": "x"}


def test_extract_tool_calls_from_content_ignores_inline_json():
    """JSON не в начале строки — не tool_call."""
    content = 'text before {"name": "x", "arguments": {}}'
    assert _extract_tool_calls_from_content(content) == []
