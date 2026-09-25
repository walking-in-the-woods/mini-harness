"""Тесты бэкендов инференса. Реальная сеть не используется.

Покрывает:
  * build_backend — выбор и валидация конфигурации
  * OllamaBackend — проброс параметров в ollama.Client
  * LlamaCppBackend — _normalize_response, payload запроса,
    headers, timeout, health, list_models

Мок httpx.Client — тот же паттерн, что в test_proxy.py: подменяем
класс целиком, ловим вызовы.
"""

from __future__ import annotations

import json

import httpx
import pytest

from harness.backends import (
    BackendError,
    ChatBackend,
    LlamaCppBackend,
    OllamaBackend,
    build_backend,
)


# ══════════════════════════════════════════════════════════════════════════
# build_backend
# ══════════════════════════════════════════════════════════════════════════

def test_build_backend_default_is_ollama():
    backend = build_backend({"ollama_host": "http://127.0.0.1:11434"})
    assert isinstance(backend, OllamaBackend)
    assert backend.name == "ollama"
    assert isinstance(backend, ChatBackend)


def test_build_backend_explicit_ollama():
    backend = build_backend({
        "backend": "ollama",
        "ollama_host": "http://example.test:11434",
    })
    assert isinstance(backend, OllamaBackend)
    assert backend.host == "http://example.test:11434"


@pytest.mark.parametrize("kind", [
    "llamacpp", "llama.cpp", "LLAMA-CPP", "llama_cpp", "  LlamaCpp  ",
])
def test_build_backend_llamacpp_aliases(kind: str):
    cfg = {"backend": kind, "llamacpp_host": "http://127.0.0.1:8080"}
    backend = build_backend(cfg)
    assert isinstance(backend, LlamaCppBackend)
    assert backend.name == "llamacpp"


def test_build_backend_unknown_raises():
    with pytest.raises(BackendError, match="unknown backend"):
        build_backend({"backend": "vllm"})


def test_build_backend_ollama_missing_host_raises():
    with pytest.raises(BackendError, match="OLLAMA_HOST"):
        build_backend({"backend": "ollama", "ollama_host": ""})


def test_build_backend_llamacpp_missing_host_raises():
    with pytest.raises(BackendError, match="LLAMACPP_HOST"):
        build_backend({"backend": "llamacpp", "llamacpp_host": ""})


def test_build_backend_llamacpp_passes_timeout_and_key():
    cfg = {
        "backend": "llamacpp",
        "llamacpp_host": "http://127.0.0.1:8080",
        "llamacpp_api_key": "tok",
        "llamacpp_timeout": 42.0,
    }
    backend = build_backend(cfg)
    assert isinstance(backend, LlamaCppBackend)
    assert backend.api_key == "tok"
    assert backend.timeout == 42.0


# ══════════════════════════════════════════════════════════════════════════
# OllamaBackend
# ══════════════════════════════════════════════════════════════════════════

class _ScriptedOllamaClient:
    """Мок ollama.Client. Записывает все вызовы chat()."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.list_calls = 0
        self.list_error: Exception | None = None
        self.list_result: dict = {"models": []}

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("ScriptedOllamaClient ran out of responses")
        return self._responses.pop(0)

    def list(self):
        self.list_calls += 1
        if self.list_error is not None:
            raise self.list_error
        return self.list_result


def _empty_response():
    return {"message": {"content": "", "tool_calls": []}}


def test_ollama_backend_name_and_passthrough():
    client = _ScriptedOllamaClient(_empty_response())
    backend = OllamaBackend.from_client(client)
    assert backend.name == "ollama"

    resp = backend.chat(
        model="qwen3:1.7b",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert resp["message"]["content"] == ""


def test_ollama_backend_passes_options_and_flags():
    client = _ScriptedOllamaClient(_empty_response())
    backend = OllamaBackend.from_client(client)

    backend.chat(
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.2,
        num_predict=128,
        num_ctx=2048,
        keep_alive="5m",
        think=False,
    )

    call = client.calls[0]
    assert call["model"] == "m"
    assert call["think"] is False
    assert call["keep_alive"] == "5m"
    assert call["options"] == {
        "temperature": 0.2,
        "num_predict": 128,
        "num_ctx": 2048,
    }
    assert "tools" not in call


def test_ollama_backend_tools_added_when_given():
    client = _ScriptedOllamaClient(_empty_response())
    backend = OllamaBackend.from_client(client)

    tools = [{"type": "function", "function": {"name": "x"}}]
    backend.chat(model="m", messages=[], tools=tools)

    assert client.calls[0]["tools"] == tools


def test_ollama_backend_tools_not_added_when_none():
    client = _ScriptedOllamaClient(_empty_response())
    backend = OllamaBackend.from_client(client)
    backend.chat(model="m", messages=[], tools=None)
    assert "tools" not in client.calls[0]


def test_ollama_backend_keep_alive_omitted_when_empty():
    client = _ScriptedOllamaClient(_empty_response())
    backend = OllamaBackend.from_client(client)
    backend.chat(model="m", messages=[], keep_alive=None)
    assert "keep_alive" not in client.calls[0]


def test_ollama_backend_health_true():
    client = _ScriptedOllamaClient()
    client.list_result = {"models": [{"name": "qwen3:1.7b"}]}
    backend = OllamaBackend.from_client(client)
    assert backend.health() is True


def test_ollama_backend_health_false_on_error():
    client = _ScriptedOllamaClient()
    client.list_error = RuntimeError("server down")
    backend = OllamaBackend.from_client(client)
    assert backend.health() is False


def test_ollama_backend_list_models():
    client = _ScriptedOllamaClient()
    client.list_result = {
        "models": [
            {"name": "qwen3:1.7b"},
            {"name": "llama3.2:3b"},
        ]
    }
    backend = OllamaBackend.from_client(client)
    assert backend.list_models() == ["qwen3:1.7b", "llama3.2:3b"]


def test_ollama_backend_list_models_empty_on_error():
    client = _ScriptedOllamaClient()
    client.list_error = RuntimeError("boom")
    backend = OllamaBackend.from_client(client)
    assert backend.list_models() == []


# ══════════════════════════════════════════════════════════════════════════
# LlamaCppBackend: конструктор
# ══════════════════════════════════════════════════════════════════════════

def test_llamacpp_trailing_slash_stripped():
    b = LlamaCppBackend("http://127.0.0.1:8080/")
    assert b.host == "http://127.0.0.1:8080"


def test_llamacpp_empty_host_raises():
    with pytest.raises(BackendError, match="HOST"):
        LlamaCppBackend("")


def test_llamacpp_name():
    b = LlamaCppBackend("http://127.0.0.1:8080")
    assert b.name == "llamacpp"


# ══════════════════════════════════════════════════════════════════════════
# LlamaCppBackend: _normalize_response
# ══════════════════════════════════════════════════════════════════════════

def test_normalize_response_no_tool_calls():
    data = {"choices": [{"message": {"content": "hello"}}]}
    out = LlamaCppBackend._normalize_response(data)
    assert out == {"message": {"content": "hello", "tool_calls": []}}


def test_normalize_response_null_content_with_tool_call():
    data = {"choices": [{"message": {
        "content": None,
        "tool_calls": [{
            "function": {
                "name": "read_file",
                "arguments": '{"path": "a.txt"}',
            }
        }],
    }}]}
    out = LlamaCppBackend._normalize_response(data)
    assert out["message"]["content"] == ""
    assert out["message"]["tool_calls"] == [
        {"function": {
            "name": "read_file",
            "arguments": {"path": "a.txt"},
        }}
    ]


def test_normalize_response_arguments_already_dict():
    data = {"choices": [{"message": {
        "content": "",
        "tool_calls": [{"function": {
            "name": "list_dir", "arguments": {"path": "."},
        }}],
    }}]}
    out = LlamaCppBackend._normalize_response(data)
    assert out["message"]["tool_calls"][0]["function"]["arguments"] == \
        {"path": "."}


def test_normalize_response_bad_json_arguments_becomes_empty():
    data = {"choices": [{"message": {
        "content": "",
        "tool_calls": [{"function": {
            "name": "x", "arguments": "not json",
        }}],
    }}]}
    out = LlamaCppBackend._normalize_response(data)
    assert out["message"]["tool_calls"][0]["function"]["arguments"] == {}


def test_normalize_response_json_scalar_arguments_becomes_empty():
    """Валидный JSON, но не объект — например число."""
    data = {"choices": [{"message": {
        "content": "",
        "tool_calls": [{"function": {
            "name": "x", "arguments": "42",
        }}],
    }}]}
    out = LlamaCppBackend._normalize_response(data)
    assert out["message"]["tool_calls"][0]["function"]["arguments"] == {}


def test_normalize_response_empty_choices():
    out = LlamaCppBackend._normalize_response({"choices": []})
    assert out == {"message": {"content": "", "tool_calls": []}}


def test_normalize_response_missing_choices():
    out = LlamaCppBackend._normalize_response({})
    assert out == {"message": {"content": "", "tool_calls": []}}


def test_normalize_response_skips_malformed_tool_calls():
    data = {"choices": [{"message": {
        "content": "",
        "tool_calls": [
            {"function": {"arguments": "{}"}},              # нет name
            {"function": {"name": "", "arguments": "{}"}},  # пустой name
            {"function": {"name": 123, "arguments": "{}"}}, # name не str
            {"not_function": "x"},                          # не function
            {"function": {"name": "ok", "arguments": "{}"}},
        ],
    }}]}
    out = LlamaCppBackend._normalize_response(data)
    assert len(out["message"]["tool_calls"]) == 1
    assert out["message"]["tool_calls"][0]["function"]["name"] == "ok"


def test_normalize_response_accepts_dataclass_shaped_dict():
    """llama.cpp иногда возвращает tool_call как dict с вложенным dict —
    это тот же случай, что уже dict arguments."""
    data = {"choices": [{"message": {
        "content": "",
        "tool_calls": [{
            "type": "function",
            "id": "call_1",
            "function": {"name": "f", "arguments": {"a": 1}},
        }],
    }}]}
    out = LlamaCppBackend._normalize_response(data)
    assert out["message"]["tool_calls"][0]["function"] == {
        "name": "f", "arguments": {"a": 1},
    }


# ══════════════════════════════════════════════════════════════════════════
# LlamaCppBackend: chat, health, list_models через мок httpx.Client
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def fake_http(monkeypatch):
    state = {
        "init_kwargs": {},
        "last_call": {},
        "response": (200, {"choices": [{"message": {"content": "ok"}}]}),
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

        def __exit__(self, *exc):
            return False

        def post(self, url, headers=None, json=None):
            state["last_call"] = {
                "method": "POST", "url": url,
                "headers": headers, "json": json,
            }
            return _Resp(*state["response"])

        def get(self, url, headers=None):
            state["last_call"] = {
                "method": "GET", "url": url, "headers": headers,
            }
            return _Resp(*state["response"])

    monkeypatch.setattr(
        "harness.backends.llamacpp_backend.httpx.Client", _Client,
    )
    return state


def test_llamacpp_chat_basic(fake_http):
    backend = LlamaCppBackend("http://127.0.0.1:8080", timeout=42.0)
    resp = backend.chat(
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.1,
        num_predict=512,
    )
    assert resp["message"]["content"] == "ok"

    call = fake_http["last_call"]
    assert call["url"] == "http://127.0.0.1:8080/v1/chat/completions"
    payload = call["json"]
    assert payload["model"] == "m"
    assert payload["max_tokens"] == 512
    assert payload["temperature"] == 0.1
    assert payload["stream"] is False
    assert "tools" not in payload
    assert "tool_choice" not in payload
    assert fake_http["init_kwargs"]["timeout"] == 42.0


def test_llamacpp_chat_adds_tools_and_tool_choice(fake_http):
    backend = LlamaCppBackend("http://127.0.0.1:8080")
    tools = [{"type": "function", "function": {"name": "x"}}]
    backend.chat(model="m", messages=[], tools=tools)
    payload = fake_http["last_call"]["json"]
    assert payload["tools"] == tools
    assert payload["tool_choice"] == "auto"


def test_llamacpp_chat_ignores_num_ctx_keep_alive_think(fake_http):
    backend = LlamaCppBackend("http://127.0.0.1:8080")
    backend.chat(
        model="m", messages=[],
        num_ctx=9999, keep_alive="1h", think=True,
    )
    payload = fake_http["last_call"]["json"]
    assert "num_ctx" not in payload
    assert "keep_alive" not in payload
    assert "think" not in payload


def test_llamacpp_chat_api_key_goes_to_authorization(fake_http):
    backend = LlamaCppBackend("http://127.0.0.1:8080", api_key="tok")
    backend.chat(model="m", messages=[])
    headers = fake_http["last_call"]["headers"]
    assert headers["Authorization"] == "Bearer tok"


def test_llamacpp_chat_no_auth_header_without_key(fake_http):
    backend = LlamaCppBackend("http://127.0.0.1:8080")
    backend.chat(model="m", messages=[])
    headers = fake_http["last_call"]["headers"]
    assert "Authorization" not in headers


def test_llamacpp_chat_default_model_placeholder(fake_http):
    """Пустая строка в model превращается в 'default' — иначе
    llama-server может отвергнуть запрос."""
    backend = LlamaCppBackend("http://127.0.0.1:8080")
    backend.chat(model="", messages=[])
    assert fake_http["last_call"]["json"]["model"] == "default"


def test_llamacpp_chat_http_error_raises_runtime_error(fake_http):
    fake_http["response"] = (500, {"error": "context overflow"})
    backend = LlamaCppBackend("http://127.0.0.1:8080")
    with pytest.raises(RuntimeError, match="HTTP 500"):
        backend.chat(model="m", messages=[])


def test_llamacpp_chat_network_error_raises_runtime_error(monkeypatch):
    class _Bad:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *e): return False
        def post(self, *a, **kw):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(
        "harness.backends.llamacpp_backend.httpx.Client", _Bad,
    )
    backend = LlamaCppBackend("http://127.0.0.1:8080")
    with pytest.raises(RuntimeError, match="request failed"):
        backend.chat(model="m", messages=[])


def test_llamacpp_health_true(fake_http):
    fake_http["response"] = (200, {})
    backend = LlamaCppBackend("http://127.0.0.1:8080")
    assert backend.health() is True
    assert fake_http["last_call"]["url"].endswith("/health")


def test_llamacpp_health_false_on_500(fake_http):
    fake_http["response"] = (500, {})
    backend = LlamaCppBackend("http://127.0.0.1:8080")
    assert backend.health() is False


def test_llamacpp_health_false_on_network_error(monkeypatch):
    class _Bad:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *e): return False
        def get(self, *a, **kw):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(
        "harness.backends.llamacpp_backend.httpx.Client", _Bad,
    )
    backend = LlamaCppBackend("http://127.0.0.1:8080")
    assert backend.health() is False


def test_llamacpp_list_models_openai_format(fake_http):
    """OpenAI-формат: {"object": "list", "data": [{"id": ...}]}."""
    fake_http["response"] = (200, {
        "object": "list",
        "data": [
            {"id": "qwen-coder-3b", "object": "model"},
            {"id": "qwen-coder-7b", "object": "model"},
            {"not_id": "x"},     # пропускается
            "junk",              # пропускается
        ],
    })
    backend = LlamaCppBackend("http://127.0.0.1:8080")
    assert backend.list_models() == ["qwen-coder-3b", "qwen-coder-7b"]


def test_llamacpp_list_models_extended_format(fake_http):
    """Расширенный формат свежего llama-server:
    {"models": [{"name": "/path/to/model.gguf"}]}."""
    fake_http["response"] = (200, {
        "models": [
            {"name": "/home/as/models/qwen3.gguf",
             "model": "/home/as/models/qwen3.gguf"},
        ],
    })
    backend = LlamaCppBackend("http://127.0.0.1:8080")
    assert backend.list_models() == [
        "/home/as/models/qwen3.gguf"
    ]


def test_llamacpp_list_models_empty_on_error(fake_http):
    fake_http["response"] = (500, {})
    backend = LlamaCppBackend("http://127.0.0.1:8080")
    assert backend.list_models() == []
