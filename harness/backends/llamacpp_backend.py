"""
Бэкенд llama.cpp (llama-server).

llama-server предоставляет OpenAI-совместимый endpoint
/v1/chat/completions и /v1/models. Ходим туда напрямую через httpx
(без openai SDK — он тянет лишние зависимости и конфликтует с
ollama по версиям pydantic).

Особенности, которые учитываем:

1. function.arguments приходит JSON-строкой (OpenAI-формат), а не
   dict. Нормализуем в dict, чтобы агент не думал о разнице.

2. Параметры num_ctx и keep_alive игнорируются: контекст задаётся
   при старте сервера (--ctx-size), модель всегда в RAM.

3. think=False игнорируется: у llama.cpp нет reasoning-фазы,
   chat-template решает, будет ли модель рассуждать.

4. Таймаут по умолчанию — 300 секунд. На N100+7B Q4 генерация
   идёт ~2–4 t/s; ответ на 1024 токенов занимает 4–8 минут.
   30-секундный дефолт (как у ApiProxy) здесь неприемлем.

5. Проверка health() — GET /health (специфично для llama-server,
   а не OpenAI). Не блокирует, вызывается только из CLI/диагностики.
"""

from __future__ import annotations

import json

import httpx

from harness.backends.base import BackendError, ChatBackend


class LlamaCppBackend(ChatBackend):
    """Клиент к llama-server (OpenAI-compatible)."""

    def __init__(self, host: str, *, api_key: str = "",
                 timeout: float = 300.0):
        if not host:
            raise BackendError("LLAMACPP_HOST пуст")
        self.host = host.rstrip("/")
        self.api_key = api_key
        self.timeout = float(timeout)

    @property
    def name(self) -> str:
        return "llamacpp"

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def chat(
        self,
        *,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.1,
        num_predict: int = 1024,
        num_ctx: int = 2048,           # noqa: ARG002 — server-side
        keep_alive: str | None = None,  # noqa: ARG002 — не применимо
        think: bool = False,            # noqa: ARG002 — не применимо
    ) -> dict:
        payload: dict = {
            "model": model or "default",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": num_predict,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        url = f"{self.host}/v1/chat/completions"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, headers=self._headers(),
                                   json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"llama.cpp HTTP {e.response.status_code}: "
                f"{e.response.text[:200]}"
            ) from e
        except httpx.RequestError as e:
            raise RuntimeError(
                f"llama.cpp request failed: {e}"
            ) from e

        return self._normalize_response(data)

    @staticmethod
    def _normalize_response(data: dict) -> dict:
        """OpenAI → формат ollama-совместимого ответа.

        {"choices": [{"message": {...}}]} → {"message": {...}}
        """
        choices = data.get("choices") or [{}]
        choice = choices[0] if choices else {}
        msg = choice.get("message") or {}

        content = msg.get("content") or ""
        raw_calls = msg.get("tool_calls") or []

        tool_calls: list[dict] = []
        for tc in raw_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function")
            if not isinstance(fn, dict):
                continue
            name = fn.get("name")
            if not isinstance(name, str) or not name:
                continue

            args_raw = fn.get("arguments")
            if isinstance(args_raw, dict):
                args = args_raw
            elif isinstance(args_raw, str):
                try:
                    args = json.loads(args_raw)
                except json.JSONDecodeError:
                    args = {}
                if not isinstance(args, dict):
                    args = {}
            else:
                args = {}

            tool_calls.append({
                "function": {"name": name, "arguments": args}
            })

        return {
            "message": {
                "content": content,
                "tool_calls": tool_calls,
            }
        }

    def health(self) -> bool:
        try:
            with httpx.Client(timeout=5.0) as client:
                r = client.get(f"{self.host}/health")
                return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        try:
            with httpx.Client(timeout=5.0) as client:
                r = client.get(f"{self.host}/v1/models",
                               headers=self._headers())
                r.raise_for_status()
                data = r.json()
        except Exception:
            return []
        items = data.get("data") or []
        result: list[str] = []
        for m in items:
            if isinstance(m, dict):
                mid = m.get("id") or ""
                if mid:
                    result.append(mid)
        return result
