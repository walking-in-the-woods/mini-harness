"""
Бэкенд Ollama.

Оборачивает ollama.Client, приводя его интерфейс к ChatBackend.
Все нюансы ollama (options, think, keep_alive) остаются здесь.
"""

from __future__ import annotations

from typing import Any

import ollama

from harness.backends.base import ChatBackend


class OllamaBackend(ChatBackend):
    """Клиент к локальному ollama serve."""

    def __init__(self, host: str, client: Any | None = None):
        self.host = host
        self._client = client if client is not None else ollama.Client(
            host=host
        )

    @classmethod
    def from_client(cls, client: Any,
                    host: str = "http://127.0.0.1:11434"
                    ) -> "OllamaBackend":
        """Обёртка вокруг уже готового клиента.

        Используется в тестах и внешнем коде, где клиент создаётся
        с нестандартными настройками (mock, кастомный transport).
        """
        return cls(host=host, client=client)

    @property
    def name(self) -> str:
        return "ollama"

    def chat(
        self,
        *,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.1,
        num_predict: int = 1024,
        num_ctx: int = 2048,
        keep_alive: str | None = None,
        think: bool = False,
    ) -> dict:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "think": think,
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
                "num_ctx": num_ctx,
            },
        }
        if tools is not None:
            kwargs["tools"] = tools
        if keep_alive:
            kwargs["keep_alive"] = keep_alive

        return self._client.chat(**kwargs)

    def health(self) -> bool:
        try:
            self._client.list()
            return True
        except Exception:
            return False

    def list_models(self) -> list[str]:
        try:
            resp = self._client.list()
        except Exception:
            return []
        # ollama-python возвращает {"models": [{"name": ...}, ...]}
        # или объект с .get("models"). Пробуем оба варианта.
        if hasattr(resp, "get"):
            models = resp.get("models") or []
        else:
            models = getattr(resp, "models", None) or []
        result: list[str] = []
        for m in models:
            if isinstance(m, dict):
                name = m.get("name") or m.get("model") or ""
            else:
                name = getattr(m, "name", "") or getattr(m, "model", "")
            if name:
                result.append(name)
        return result
