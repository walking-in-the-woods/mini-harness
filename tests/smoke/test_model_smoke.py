"""Smoke-тесты с реальной моделью. Проверяют базовую работоспособность.

Модель читается из SMOKE_MODEL_SMALL. Без неё — тесты скипаются,
а не падают: CI не должен требовать модель на каждом прогоне.

Параметр think=False отключает reasoning-фазу у моделей, которые
её поддерживают (у остальных игнорируется). Это даёт детерминированный
content и защищает тесты от пустого message.content при малом
num_predict.
"""

from __future__ import annotations

import json
import os

import ollama
import pytest


MODEL = os.environ.get("SMOKE_MODEL_SMALL", "")
HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")


pytestmark = pytest.mark.smoke


@pytest.fixture(scope="module")
def client() -> ollama.Client:
    return ollama.Client(host=HOST)


@pytest.fixture(scope="module", autouse=True)
def _require_model(client: ollama.Client):
    if not MODEL:
        pytest.skip("SMOKE_MODEL_SMALL не задан")
    try:
        client.show(MODEL)
    except Exception as e:
        pytest.skip(f"model {MODEL!r} not available: {e}")


def test_sentinel_pong(client: ollama.Client):
    resp = client.chat(
        model=MODEL,
        messages=[{
            "role": "user",
            "content": "Reply with exactly one word: PONG",
        }],
        think=False,
        options={"temperature": 0.0, "num_predict": 32},
    )
    text = (resp["message"]["content"] or "").strip()
    assert text, "empty response"
    assert "PONG" in text.upper(), f"sentinel not found: {text!r}"


def test_json_mode_structured(client: ollama.Client):
    resp = client.chat(
        model=MODEL,
        messages=[{
            "role": "user",
            "content": (
                "Return a JSON object with a single field 'status' "
                "whose value is the string 'ok'. "
                "Output ONLY the JSON, no prose."
            ),
        }],
        format="json",
        think=False,
        options={"temperature": 0.0, "num_predict": 128},
    )
    text = (resp["message"]["content"] or "").strip()
    assert text, "empty response"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        pytest.fail(f"invalid JSON: {e}; got: {text!r}")
    assert isinstance(payload, dict), f"not an object: {payload!r}"
    assert payload.get("status") == "ok", f"wrong payload: {payload!r}"


def test_who_are_you_ascii(client: ollama.Client):
    resp = client.chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": "You must answer in English only."},
            {"role": "user", "content": "Who are you?"},
        ],
        think=False,
        options={"temperature": 0.0, "num_predict": 128},
    )
    text = (resp["message"]["content"] or "").strip()
    assert text, "empty response"

    printable = sum(1 for c in text if 32 <= ord(c) < 127 or c in "\n\r\t")
    ratio = printable / len(text)
    assert ratio >= 0.9, f"non-ASCII garbage: ratio={ratio:.2f}, text={text!r}"

    lower = text.lower()
    assert any(w in lower for w in
               ("assistant", "model", "ai", "language")), (
        f"unexpected answer: {text!r}"
    )
