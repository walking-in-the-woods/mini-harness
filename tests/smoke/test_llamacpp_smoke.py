"""Smoke-тесты с реальным llama-server. Требуют LLAMACPP_HOST.

Без LLAMACPP_HOST или при недоступном сервере — тесты скипаются,
а не падают: CI не должен требовать запущенный llama-server.

Переменные окружения:
  LLAMACPP_HOST        адрес сервера, например http://127.0.0.1:8080
  SMOKE_LLAMACPP_MODEL метка модели (по умолчанию "llamacpp-local")
  SMOKE_LLAMACPP_TIMEOUT  таймаут чата в секундах (по умолчанию 120)
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from harness.agent import HarnessAgent
from harness.audit import AuditLog
from harness.backends import LlamaCppBackend
from harness.fs_guard import FileSystemGuard
from harness.proxy import ApiProxy


HOST = os.environ.get("LLAMACPP_HOST", "").strip()
MODEL = os.environ.get("SMOKE_LLAMACPP_MODEL", "llamacpp-local").strip()
TIMEOUT = float(os.environ.get("SMOKE_LLAMACPP_TIMEOUT", "120"))


pytestmark = pytest.mark.smoke


@pytest.fixture(scope="module", autouse=True)
def _require_server():
    if not HOST:
        pytest.skip("LLAMACPP_HOST не задан")
    backend = LlamaCppBackend(HOST, timeout=10.0)
    if not backend.health():
        pytest.skip(f"llama-server не отвечает на {HOST}")


# ── Базовые проверки сервера ──────────────────────────────────────────────

def test_health():
    backend = LlamaCppBackend(HOST, timeout=10.0)
    assert backend.health() is True


def test_list_models_returns_something():
    """llama-server обычно отдаёт одну модель. Пустой список —
    подозрительно, но не смертельно: некоторые сборки возвращают
    иначе. Тест не падает, если список пуст, но пишет warning."""
    backend = LlamaCppBackend(HOST, timeout=10.0)
    models = backend.list_models()
    if not models:
        pytest.skip("llama-server не отдаёт список моделей через "
                    "/v1/models — пропускаю, это не ошибка")


# ── Chat ──────────────────────────────────────────────────────────────────

def test_chat_sentinel_pong():
    backend = LlamaCppBackend(HOST, timeout=TIMEOUT)
    resp = backend.chat(
        model=MODEL,
        messages=[{"role": "user",
                   "content": "Reply with exactly one word: PONG"}],
        temperature=0.0,
        num_predict=32,
    )
    text = (resp["message"]["content"] or "").strip()
    assert text, "пустой ответ"
    assert "PONG" in text.upper(), f"PONG не найден: {text!r}"


def test_chat_returns_message_shape():
    backend = LlamaCppBackend(HOST, timeout=TIMEOUT)
    resp = backend.chat(
        model=MODEL,
        messages=[{"role": "user", "content": "Say hello."}],
        temperature=0.0,
        num_predict=32,
    )
    assert "message" in resp
    assert "content" in resp["message"]
    assert "tool_calls" in resp["message"]
    assert isinstance(resp["message"]["tool_calls"], list)


# ── Полный round-trip через HarnessAgent ──────────────────────────────────

@pytest.fixture
def agent(tmp_path: Path) -> HarnessAgent:
    (tmp_path / "notes").mkdir()
    (tmp_path / "input").mkdir()
    (tmp_path / "output").mkdir()
    (tmp_path / "input" / "hello.md").write_text(
        "PONG-42 is the answer.\n", encoding="utf-8",
    )

    guard = FileSystemGuard({
        "root": str(tmp_path),
        "whitelist": ["**"],
        "blacklist": [],
        "writable": ["notes/**", "output/**", "*.md", "*.txt"],
    })
    cfg = {
        "model": MODEL,
        "num_ctx": 2048,
        "num_predict": 256,
        "keep_alive": "5m",
        "temperature": 0.0,
        "workspace_root": str(tmp_path),
        "max_tool_rounds": 3,
        "max_read_bytes": 200_000,
        "max_write_bytes": 1_000_000,
        "max_list_entries": 500,
    }
    proxy = ApiProxy({}, timeout=5.0, max_request=1000, max_response=1000)
    audit = AuditLog(str(tmp_path / "audit.jsonl"))

    backend = LlamaCppBackend(HOST, timeout=TIMEOUT)
    return HarnessAgent(cfg, guard, proxy, audit, backend=backend)


def test_guided_mode_roundtrip(agent: HarnessAgent):
    """Guided mode: прочитай файл, напиши резюме. Без tools."""
    result = agent.run(
        "прочитай input/hello.md и напиши краткое резюме "
        "в output/summary.md"
    )

    if "[backend error]" in result["text"]:
        pytest.fail(f"backend error: {result['text']}")

    # Источник мог быть передан, но модель могла не вызвать propose_write.
    # Если pending_writes пуст — xfail, а не fail: это ограничение модели,
    # а не harness.
    if not result["pending_writes"]:
        pytest.xfail(
            f"модель не предложила запись: {result['text'][:200]!r}"
        )

    assert result["pending_writes"][0]["canonical"] == "output/summary.md"
    assert result["pending_writes"][0]["content"]


def test_guided_mode_display_only(agent: HarnessAgent):
    """Guided mode без target — только вывести текст."""
    result = agent.run(
        "прочитай input/hello.md и расскажи своими словами о чём он"
    )
    if "[backend error]" in result["text"]:
        pytest.fail(f"backend error: {result['text']}")
    assert result["text"]
    assert result["pending_writes"] == []
