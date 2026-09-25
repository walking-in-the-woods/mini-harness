"""
Пакет chunked-обработки для /batch.

Публичный API:
    ProcessingConfig        — корневой конфиг (dataclass)
    DefaultsConfig          — суб-конфиг defaults
    CodeConfig              — суб-конфиг code
    DocsConfig              — суб-конфиг docs
    ProcessingConfigError   — ошибка загрузки или валидации конфига
    ProcessError            — общее исключение пакета
    get_processor           — фабрика процессора по режиму
    load                    — загрузка processing.yaml

Всё остальное — приватное. Импортировать `harness.processing.code`
или `harness.processing.docs` напрямую снаружи пакета не следует.
"""

from __future__ import annotations

from harness.processing.base import ProcessError
from harness.processing.config import (
    CodeConfig,
    DefaultsConfig,
    DocsConfig,
    ProcessingConfig,
    ProcessingConfigError,
    load,
)


__all__ = [
    "CodeConfig",
    "DefaultsConfig",
    "DocsConfig",
    "ProcessError",
    "ProcessingConfig",
    "ProcessingConfigError",
    "get_processor",
    "load",
]


def get_processor(mode: str, config: ProcessingConfig):
    """Фабрика процессоров.

    Импорт конкретных реализаций — отложенный, чтобы не грузить
    ast / re на старте, когда chunked-режим может не понадобиться.

    mode: "code" | "docs"
    """
    if mode == "code":
        from harness.processing.code import CodeProcessor
        return CodeProcessor(config.code, config.defaults)
    if mode == "docs":
        from harness.processing.docs import DocProcessor
        return DocProcessor(config.docs, config.defaults)
    raise ProcessError(
        f"unknown mode {mode!r}; expected 'code' or 'docs'"
    )
