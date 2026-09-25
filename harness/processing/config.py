"""
Загрузка и валидация config/processing.yaml.

Стратегия:

* Fail-fast. Любая ошибка (неизвестный ключ, тип, диапазон)
  поднимает ProcessingConfigError с указанием файла и ключа.
  Никаких «тихих» подстановок дефолтов на месте опечатки.

* Опционален. Если файла нет — возвращается ProcessingConfig
  с дефолтами, эквивалентными содержимому репозиторного
  processing.yaml. Пользователю не нужно его создавать.

* Warning при опасных настройках. `on_chunk_failure: partial`
  в code-режиме — валидная конфигурация, но её нужно явно
  увидеть один раз при старте. Печатается в stderr.

* Расширения нормализуются. `py` и `.py` эквивалентны, `.PY`
  приводится к `.py`. Дубликаты удаляются с сохранением порядка.

Не импортирует yaml-парсер из batch.py или agent.py — только
PyYAML. Пакет processing/ не должен зависеть от остального
harness'а, чтобы его можно было тестировать в изоляции.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from harness.processing.base import ProcessError


__all__ = [
    "CodeConfig",
    "DefaultsConfig",
    "DocsConfig",
    "ProcessingConfig",
    "ProcessingConfigError",
    "load",
]


# ── Исключение ────────────────────────────────────────────────────────────

class ProcessingConfigError(ProcessError):
    """Ошибка загрузки или валидации processing.yaml.

    Отдельный тип нужен, чтобы batch.py мог отличить «конфиг плохой»
    от «модель не смогла» и показать пользователю разные сообщения.
    """


# ── Подсекции конфига ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class DefaultsConfig:
    chunk_target_bytes: int = 0
    chunk_min_bytes: int = 800
    budget_safety: float = 0.7
    output_reserve_ratio: float = 0.4
    overlap_bytes: int = 0
    on_chunk_failure: str = "fail"
    on_merge_invalid: str = "fail"
    on_insufficient_context: str = "fail"
    insufficient_context_marker: str = "INSUFFICIENT_CONTEXT"


@dataclass(frozen=True)
class CodeConfig:
    extensions: tuple[str, ...] = (".py", ".pyw")
    boundary_nodes: tuple[str, ...] = (
        "FunctionDef", "AsyncFunctionDef", "ClassDef",
    )
    include_preamble: bool = False
    preamble_max_bytes: int = 2000
    validate: tuple[str, ...] = (
        "ast_parseable", "defs_preserved",
        "bodies_nonempty", "signature_args_preserved",
    )
    merge_strategy: str = "replace_block"
    partial_ornament_open: str = (
        "# ======== NOT PROCESSED: chunk {index}/{total} ========"
    )
    partial_ornament_close: str = "# ======== END NOT PROCESSED ========"


@dataclass(frozen=True)
class DocsConfig:
    extensions: tuple[str, ...] = (".md", ".mdx", ".rst", ".txt")
    header_levels: tuple[int, ...] = (1, 2)
    paragraph_separator: str = r"\n\s*\n"
    merge_strategy: str = "concatenate"
    validate: tuple[str, ...] = ("headers_preserved", "fences_balanced")


@dataclass(frozen=True)
class ProcessingConfig:
    version: int = 1
    defaults: DefaultsConfig = field(default_factory=DefaultsConfig)
    code: CodeConfig = field(default_factory=CodeConfig)
    docs: DocsConfig = field(default_factory=DocsConfig)


# ── Известные ключи ───────────────────────────────────────────────────────
# Используется для fail-fast на опечатках. При добавлении нового
# поля в dataclass — добавить и сюда.

_DEFAULTS_KEYS = frozenset({
    "chunk_target_bytes", "chunk_min_bytes", "budget_safety",
    "output_reserve_ratio", "overlap_bytes",
    "on_chunk_failure", "on_merge_invalid",
    "on_insufficient_context", "insufficient_context_marker",
})

_CODE_KEYS = frozenset({
    "extensions", "boundary_nodes", "include_preamble",
    "preamble_max_bytes", "validate", "merge_strategy",
    "partial_ornament_open", "partial_ornament_close",
})

_DOCS_KEYS = frozenset({
    "extensions", "header_levels", "paragraph_separator",
    "merge_strategy", "validate",
})

_TOP_KEYS = frozenset({"version", "defaults", "code", "docs"})


# ── Известные значения enum'ов ────────────────────────────────────────────

_ON_CHUNK_FAILURE = frozenset({"fail", "partial"})
_ON_MERGE_INVALID = frozenset({"fail", "partial"})
_ON_INSUFFICIENT = frozenset({"fail", "skip"})
_CODE_MERGE = frozenset({"replace_block"})
_DOCS_MERGE = frozenset({"concatenate"})
_CODE_VALIDATE = frozenset({
    "ast_parseable", "defs_preserved", "bodies_nonempty",
    "signature_args_preserved", "no_new_top_level",
})
_DOCS_VALIDATE = frozenset({"headers_preserved", "fences_balanced"})
_AST_NODES = frozenset({
    "FunctionDef", "AsyncFunctionDef", "ClassDef",
    "Import", "ImportFrom", "Assign", "AnnAssign",
})


# ── Хелперы валидации ─────────────────────────────────────────────────────

def _check_unknown_keys(raw: dict, allowed: frozenset[str],
                        section: str) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise ProcessingConfigError(
            f"unknown key(s) in section {section!r}: "
            f"{', '.join(sorted(unknown))}. "
            f"Allowed: {', '.join(sorted(allowed))}"
        )


def _get_int(raw: dict, key: str, default: int, *,
             minimum: int = 0, maximum: int | None = None,
             section: str) -> int:
    if key not in raw:
        return default
    value = raw[key]
    if isinstance(value, bool):  # bool — подкласс int, отсекаем
        raise ProcessingConfigError(
            f"{section}.{key}: expected int, got bool ({value!r})"
        )
    if not isinstance(value, int):
        raise ProcessingConfigError(
            f"{section}.{key}: expected int, got "
            f"{type(value).__name__} ({value!r})"
        )
    if value < minimum:
        raise ProcessingConfigError(
            f"{section}.{key}: must be >= {minimum}, got {value}"
        )
    if maximum is not None and value > maximum:
        raise ProcessingConfigError(
            f"{section}.{key}: must be <= {maximum}, got {value}"
        )
    return value


def _get_float(raw: dict, key: str, default: float, *,
               minimum: float, maximum: float,
               section: str) -> float:
    if key not in raw:
        return default
    value = raw[key]
    if isinstance(value, bool):
        raise ProcessingConfigError(
            f"{section}.{key}: expected float, got bool ({value!r})"
        )
    if not isinstance(value, (int, float)):
        raise ProcessingConfigError(
            f"{section}.{key}: expected float, got "
            f"{type(value).__name__} ({value!r})"
        )
    fv = float(value)
    if not (minimum <= fv <= maximum):
        raise ProcessingConfigError(
            f"{section}.{key}: must be in [{minimum}, {maximum}], "
            f"got {fv}"
        )
    return fv


def _get_bool(raw: dict, key: str, default: bool, *,
              section: str) -> bool:
    if key not in raw:
        return default
    value = raw[key]
    if not isinstance(value, bool):
        raise ProcessingConfigError(
            f"{section}.{key}: expected bool, got "
            f"{type(value).__name__} ({value!r})"
        )
    return value


def _get_enum(raw: dict, key: str, default: str, *,
              allowed: frozenset[str], section: str) -> str:
    if key not in raw:
        return default
    value = raw[key]
    if not isinstance(value, str) or value not in allowed:
        raise ProcessingConfigError(
            f"{section}.{key}: must be one of "
            f"{sorted(allowed)}, got {value!r}"
        )
    return value


def _get_str(raw: dict, key: str, default: str, *,
             allow_empty: bool = True, section: str) -> str:
    if key not in raw:
        return default
    value = raw[key]
    if not isinstance(value, str):
        raise ProcessingConfigError(
            f"{section}.{key}: expected str, got "
            f"{type(value).__name__} ({value!r})"
        )
    if not allow_empty and not value:
        raise ProcessingConfigError(
            f"{section}.{key}: empty string not allowed"
        )
    return value


def _get_str_tuple(raw: dict, key: str, default: tuple[str, ...], *,
                   normalize_ext: bool = False,
                   section: str) -> tuple[str, ...]:
    if key not in raw:
        return default
    value = raw[key]
    if not isinstance(value, list):
        raise ProcessingConfigError(
            f"{section}.{key}: expected list of strings, got "
            f"{type(value).__name__}"
        )
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ProcessingConfigError(
                f"{section}.{key}: list item is not a string: {item!r}"
            )
        s = item.strip()
        if not s:
            raise ProcessingConfigError(
                f"{section}.{key}: empty string in list"
            )
        if normalize_ext:
            if not s.startswith("."):
                s = "." + s
            s = s.lower()
        result.append(s)
    if not result:
        raise ProcessingConfigError(
            f"{section}.{key}: empty list not allowed"
        )
    # Дедупликация с сохранением порядка. `[py, .PY, .pyw]` после
    # нормализации становится `[.py, .py, .pyw]` — без этого
    # расширение .py считалось бы дважды.
    deduped = list(dict.fromkeys(result))
    return tuple(deduped)


def _get_int_tuple(raw: dict, key: str, default: tuple[int, ...], *,
                   minimum: int, maximum: int,
                   section: str) -> tuple[int, ...]:
    if key not in raw:
        return default
    value = raw[key]
    if not isinstance(value, list):
        raise ProcessingConfigError(
            f"{section}.{key}: expected list of ints, got "
            f"{type(value).__name__}"
        )
    result: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise ProcessingConfigError(
                f"{section}.{key}: list item is not an int: {item!r}"
            )
        if not (minimum <= item <= maximum):
            raise ProcessingConfigError(
                f"{section}.{key}: item {item} outside "
                f"[{minimum}, {maximum}]"
            )
        result.append(item)
    if not result:
        raise ProcessingConfigError(
            f"{section}.{key}: empty list not allowed"
        )
    return tuple(sorted(set(result)))


def _get_str_enum_tuple(raw: dict, key: str,
                        default: tuple[str, ...], *,
                        allowed: frozenset[str],
                        section: str) -> tuple[str, ...]:
    if key not in raw:
        return default
    value = raw[key]
    if not isinstance(value, list):
        raise ProcessingConfigError(
            f"{section}.{key}: expected list of strings, got "
            f"{type(value).__name__}"
        )
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or item not in allowed:
            raise ProcessingConfigError(
                f"{section}.{key}: item {item!r} not in "
                f"{sorted(allowed)}"
            )
        result.append(item)
    if not result:
        raise ProcessingConfigError(
            f"{section}.{key}: empty list not allowed"
        )
    return tuple(result)


def _compile_regex(raw: dict, key: str, default: str, *,
                   section: str) -> str:
    pattern = _get_str(raw, key, default, allow_empty=False,
                       section=section)
    try:
        re.compile(pattern)
    except re.error as e:
        raise ProcessingConfigError(
            f"{section}.{key}: invalid regex {pattern!r}: {e}"
        ) from e
    return pattern


# ── Загрузка ──────────────────────────────────────────────────────────────

def load(path: Path | None) -> ProcessingConfig:
    """Загрузить конфиг из файла. None или отсутствующий файл —
    дефолты. Ошибки — ProcessingConfigError.

    Побочный эффект: печатает warning в stderr, если конфиг
    включает `on_chunk_failure: partial` или
    `on_merge_invalid: partial`. Это валидная настройка, но
    пользователь должен один раз её увидеть.
    """
    if path is None or not path.is_file():
        return ProcessingConfig()

    text = path.read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise ProcessingConfigError(f"{path}: YAML error: {e}") from e

    if not isinstance(raw, dict):
        raise ProcessingConfigError(
            f"{path}: top level must be a mapping, got "
            f"{type(raw).__name__}"
        )

    if not raw:
        # Файл пуст или содержит только комментарии — дефолты.
        return ProcessingConfig()

    _check_unknown_keys(raw, _TOP_KEYS, "root")

    version = _get_int(raw, "version", 1, minimum=1, maximum=1,
                       section="root")

    defaults = _parse_defaults(raw.get("defaults"))
    code = _parse_code(raw.get("code"))
    docs = _parse_docs(raw.get("docs"))

    config = ProcessingConfig(
        version=version,
        defaults=defaults,
        code=code,
        docs=docs,
    )
    _warn_on_dangerous_settings(config, path)
    return config


def _parse_defaults(section: Any) -> DefaultsConfig:
    if section is None:
        return DefaultsConfig()
    if not isinstance(section, dict):
        raise ProcessingConfigError(
            f"defaults: expected mapping, got {type(section).__name__}"
        )
    _check_unknown_keys(section, _DEFAULTS_KEYS, "defaults")

    return DefaultsConfig(
        chunk_target_bytes=_get_int(
            section, "chunk_target_bytes", 0,
            minimum=0, section="defaults",
        ),
        chunk_min_bytes=_get_int(
            section, "chunk_min_bytes", 800,
            minimum=0, section="defaults",
        ),
        budget_safety=_get_float(
            section, "budget_safety", 0.7,
            minimum=0.1, maximum=0.95, section="defaults",
        ),
        output_reserve_ratio=_get_float(
            section, "output_reserve_ratio", 0.4,
            minimum=0.1, maximum=0.7, section="defaults",
        ),
        overlap_bytes=_get_int(
            section, "overlap_bytes", 0,
            minimum=0, section="defaults",
        ),
        on_chunk_failure=_get_enum(
            section, "on_chunk_failure", "fail",
            allowed=_ON_CHUNK_FAILURE, section="defaults",
        ),
        on_merge_invalid=_get_enum(
            section, "on_merge_invalid", "fail",
            allowed=_ON_MERGE_INVALID, section="defaults",
        ),
        on_insufficient_context=_get_enum(
            section, "on_insufficient_context", "fail",
            allowed=_ON_INSUFFICIENT, section="defaults",
        ),
        insufficient_context_marker=_get_str(
            section, "insufficient_context_marker",
            "INSUFFICIENT_CONTEXT", section="defaults",
        ),
    )


def _parse_code(section: Any) -> CodeConfig:
    if section is None:
        return CodeConfig()
    if not isinstance(section, dict):
        raise ProcessingConfigError(
            f"code: expected mapping, got {type(section).__name__}"
        )
    _check_unknown_keys(section, _CODE_KEYS, "code")

    return CodeConfig(
        extensions=_get_str_tuple(
            section, "extensions",
            (".py", ".pyw"),
            normalize_ext=True, section="code",
        ),
        boundary_nodes=_get_str_enum_tuple(
            section, "boundary_nodes",
            ("FunctionDef", "AsyncFunctionDef", "ClassDef"),
            allowed=_AST_NODES, section="code",
        ),
        include_preamble=_get_bool(
            section, "include_preamble", False, section="code",
        ),
        preamble_max_bytes=_get_int(
            section, "preamble_max_bytes", 2000,
            minimum=100, section="code",
        ),
        validate=_get_str_enum_tuple(
            section, "validate",
            ("ast_parseable", "defs_preserved",
             "bodies_nonempty", "signature_args_preserved"),
            allowed=_CODE_VALIDATE, section="code",
        ),
        merge_strategy=_get_enum(
            section, "merge_strategy", "replace_block",
            allowed=_CODE_MERGE, section="code",
        ),
        partial_ornament_open=_get_str(
            section, "partial_ornament_open",
            "# ======== NOT PROCESSED: chunk {index}/{total} ========",
            allow_empty=False, section="code",
        ),
        partial_ornament_close=_get_str(
            section, "partial_ornament_close",
            "# ======== END NOT PROCESSED ========",
            allow_empty=False, section="code",
        ),
    )


def _parse_docs(section: Any) -> DocsConfig:
    if section is None:
        return DocsConfig()
    if not isinstance(section, dict):
        raise ProcessingConfigError(
            f"docs: expected mapping, got {type(section).__name__}"
        )
    _check_unknown_keys(section, _DOCS_KEYS, "docs")

    return DocsConfig(
        extensions=_get_str_tuple(
            section, "extensions",
            (".md", ".mdx", ".rst", ".txt"),
            normalize_ext=True, section="docs",
        ),
        header_levels=_get_int_tuple(
            section, "header_levels", (1, 2),
            minimum=1, maximum=6, section="docs",
        ),
        paragraph_separator=_compile_regex(
            section, "paragraph_separator", r"\n\s*\n",
            section="docs",
        ),
        merge_strategy=_get_enum(
            section, "merge_strategy", "concatenate",
            allowed=_DOCS_MERGE, section="docs",
        ),
        validate=_get_str_enum_tuple(
            section, "validate",
            ("headers_preserved", "fences_balanced"),
            allowed=_DOCS_VALIDATE, section="docs",
        ),
    )


# ── Предупреждения ────────────────────────────────────────────────────────

def _warn_on_dangerous_settings(config: ProcessingConfig,
                                path: Path) -> None:
    """Печатает warning в stderr для настроек, требующих внимания.

    Ничего не блокирует: пользователь получит большой баннер в
    batch.py, когда дойдёт до реального запуска. Здесь — только
    однократное напоминание при загрузке конфига.
    """
    if config.defaults.on_chunk_failure == "partial":
        print(
            f"[!] {path}: on_chunk_failure=partial. "
            f"В code-режиме необработанные чанки останутся в файле "
            f"с орнамент-комментарием, а исходники — в "
            f"<target>.failed/. Результат требует ручной проверки.",
            file=sys.stderr,
        )
    if config.defaults.on_merge_invalid == "partial":
        print(
            f"[!] {path}: on_merge_invalid=partial. "
            f"При невалидном merge будет записан сырой результат "
            f"и отчёт в <target>.failed/. Требует ручной проверки.",
            file=sys.stderr,
        )
