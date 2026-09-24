"""
Пакет бэкендов инференса.

Экспортирует:
    ChatBackend     — интерфейс
    BackendError    — исключение конфигурации/сети
    OllamaBackend   — клиент к ollama serve
    LlamaCppBackend — клиент к llama-server (OpenAI API)
    build_backend   — фабрика по cfg["backend"]
"""

from __future__ import annotations

from harness.backends.base import BackendError, ChatBackend
from harness.backends.llamacpp_backend import LlamaCppBackend
from harness.backends.ollama_backend import OllamaBackend


__all__ = [
    "BackendError",
    "ChatBackend",
    "OllamaBackend",
    "LlamaCppBackend",
    "build_backend",
]


def build_backend(cfg: dict) -> ChatBackend:
    """Собрать бэкенд по cfg["backend"].

    Допустимые значения (регистронезависимо):
      * "ollama" (по умолчанию)
      * "llamacpp", "llama.cpp", "llama-cpp", "llama_cpp"

    Бросает BackendError при неизвестном имени или отсутствии
    обязательных полей в cfg.
    """
    kind = (cfg.get("backend") or "ollama").strip().lower()

    if kind == "ollama":
        host = cfg.get("ollama_host") or ""
        if not host:
            raise BackendError(
                "backend=ollama: OLLAMA_HOST не задан в .env"
            )
        return OllamaBackend(host=host)

    if kind in ("llamacpp", "llama.cpp", "llama-cpp", "llama_cpp"):
        host = cfg.get("llamacpp_host") or ""
        if not host:
            raise BackendError(
                "backend=llamacpp: LLAMACPP_HOST не задан в .env"
            )
        return LlamaCppBackend(
            host=host,
            api_key=cfg.get("llamacpp_api_key") or "",
            timeout=float(cfg.get("llamacpp_timeout") or 300.0),
        )

    raise BackendError(
        f"unknown backend {kind!r}; expected 'ollama' or 'llamacpp'"
    )
