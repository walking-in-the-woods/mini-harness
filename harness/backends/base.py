"""
Абстракция клиента инференса.

Оба бэкенда возвращают ответ в едином формате:

    {"message": {"content": str, "tool_calls": list[dict]}}

где tool_calls — список в OpenAI-формате:

    {"function": {"name": str, "arguments": dict}}

Аргументы ВСЕГДА нормализуются в dict: ollama отдаёт dict, llama.cpp
отдаёт JSON-строку. Разница скрывается в бэкенде, а не в агенте.

Исключения сети/сервера пробрасываются наружу — вызывающий код
(HarnessAgent) их ловит и логирует как backend_error.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BackendError(RuntimeError):
    """Ошибка конфигурации или несовместимости бэкенда."""


class ChatBackend(ABC):
    """Интерфейс клиента инференса."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Короткое имя бэкенда: 'ollama' | 'llamacpp'."""

    @abstractmethod
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
        """Один вызов чата.

        Параметры num_ctx, keep_alive, think могут игнорироваться
        конкретным бэкендом (например, llama.cpp задаёт контекст
        на старте сервера, а режим reasoning — chat-template'ом).

        Возвращает {"message": {"content": str, "tool_calls": list}}.
        """

    @abstractmethod
    def health(self) -> bool:
        """True, если сервер отвечает. Не должен бросать."""

    def list_models(self) -> list[str]:
        """Список моделей, если бэкенд это поддерживает.

        По умолчанию — пустой список. Используется только для
        диагностики; отсутствие данных не критично.
        """
        return []
