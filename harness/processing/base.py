"""
Базовые типы пакета processing/.

Не зависит от config.py (только stdlib), чтобы:
  * config.py мог импортировать ProcessError без цикла.
  * Тесты могли проверять estimate_tokens / compute_budget_bytes /
    Chunk в изоляции, без загрузки yaml.

Публичные имена:
    ProcessError              — общее исключение пакета
    Chunk                     — единица разбиения
    ValidationIssue           — замечание от validate()
    Processor                 — ABC режима (code / docs)
    estimate_tokens           — оценка токенов по UTF-8 байтам
    compute_budget_bytes      — бюджет чанка из num_ctx и prompt
    check_budget_consistency  — согласованность бюджета и минимума
    has_errors                — есть ли в issues уровня "error"
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar


# ── Константы ─────────────────────────────────────────────────────────────

# Оценка токенов по UTF-8 байтам: 4 байта на токен. Для ASCII
# эквивалент chars/4, для кириллицы (2 байта на символ) — chars/2.
# Единая константа, все расчёты бюджета идут через неё.
_BYTES_PER_TOKEN = 4

# Служебный overhead на шапку "[Part N/M of ...]" и обёртку.
# Не конфигурируется: при появлении потребности в тюнинге —
# новое значение здесь, а не в processing.yaml.
CHUNK_OVERHEAD_TOKENS = 50


# ── Исключение ────────────────────────────────────────────────────────────

class ProcessError(RuntimeError):
    """Общее исключение пакета processing/.

    Сообщение всегда user-facing: batch.py печатает его как есть,
    без обёртки. Ошибка ожидаемая (конфиг плохой, файл нельзя
    разбить, контекст слишком мал) — не баг в коде.
    """


# ── Оценка токенов ────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Приблизительное число токенов в тексте.

    Формула: (len(bytes) + 3) // 4.

    Для ASCII-текста (код, английский) даёт chars/4.
    Для кириллицы (2 байта на символ в UTF-8) даёт chars/2 —
    автоматическая коррекция без явного определения языка.

    Точность ±15% для смешанного текста. Для критичных решений
    (превышение контекста) есть safety margin на уровне бюджета,
    не здесь.
    """
    if not text:
        return 0
    return (len(text.encode("utf-8")) + _BYTES_PER_TOKEN - 1) \
        // _BYTES_PER_TOKEN


# ── Расчёт бюджета ────────────────────────────────────────────────────────

def compute_budget_bytes(
    *,
    num_ctx: int,
    prompt_text: str,
    output_reserve_ratio: float,
    budget_safety: float,
    chunk_overhead_tokens: int = CHUNK_OVERHEAD_TOKENS,
) -> int:
    """Размер чанка в UTF-8 байтах, вычисленный из контекста модели.

    Формула:
        prompt_tokens  = estimate_tokens(prompt_text)
        T_available    = num_ctx − prompt_tokens − chunk_overhead_tokens
        budget_input   = T_available × (1 − output_reserve_ratio)
        budget_used    = budget_input × budget_safety
        budget_bytes   = budget_used × 4

    Все входные — скаляры и текст, не объекты конфига. Так функция
    не зависит от config.py и тестируется без yaml.

    ProcessError, если:
      * num_ctx <= 0
      * T_available <= 0 (промпт и overhead уже не влезают)
      * output_reserve_ratio или budget_safety вне (0, 1)
      * итоговый бюджет < 100 байт (нельзя сформировать осмысленный чанк)
    """
    if num_ctx <= 0:
        raise ProcessError(
            f"num_ctx must be positive, got {num_ctx}"
        )
    if not (0.0 < output_reserve_ratio < 1.0):
        raise ProcessError(
            f"output_reserve_ratio must be in (0, 1), "
            f"got {output_reserve_ratio}"
        )
    if not (0.0 < budget_safety <= 1.0):
        raise ProcessError(
            f"budget_safety must be in (0, 1], got {budget_safety}"
        )

    prompt_tokens = estimate_tokens(prompt_text)
    t_available = num_ctx - prompt_tokens - chunk_overhead_tokens
    if t_available <= 0:
        raise ProcessError(
            f"prompt too large for context: "
            f"prompt≈{prompt_tokens} tokens, "
            f"overhead={chunk_overhead_tokens}, num_ctx={num_ctx}. "
            f"Increase HARNESS_NUM_CTX or shorten the prompt."
        )

    budget_input = t_available * (1.0 - output_reserve_ratio)
    budget_used = budget_input * budget_safety
    budget_bytes = int(budget_used * _BYTES_PER_TOKEN)

    if budget_bytes < 100:
        raise ProcessError(
            f"computed budget {budget_bytes} bytes is too small. "
            f"num_ctx={num_ctx}, prompt≈{prompt_tokens} tokens, "
            f"output_reserve={output_reserve_ratio}, "
            f"safety={budget_safety}. "
            f"Increase num_ctx or decrease output_reserve_ratio."
        )
    return budget_bytes


def check_budget_consistency(
    budget_bytes: int,
    chunk_min_bytes: int,
) -> None:
    """Проверка, что бюджет и минимум чанка совместимы.

    Если budget_bytes < chunk_min_bytes, невозможно создать ни один
    чанк, удовлетворяющий обоим требованиям. Это конфигурационная
    ошибка: пользователь должен либо увеличить num_ctx, либо
    уменьшить defaults.chunk_min_bytes в config/processing.yaml.
    """
    if budget_bytes < chunk_min_bytes:
        raise ProcessError(
            f"budget {budget_bytes} bytes < chunk_min "
            f"{chunk_min_bytes} bytes. No chunk can satisfy both. "
            f"Increase HARNESS_NUM_CTX or decrease "
            f"defaults.chunk_min_bytes in config/processing.yaml."
        )


# ── Chunk ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Chunk:
    """Единица разбиения.

    index, total — 1-based, для шапки "[Part index/total of ...]".
    text         — сам контент, без preamble и без шапки.
    preamble     — read-only контекст (для code: импорты и глобалы,
                   если include_preamble=true). Для docs — "".
    start, end   — СИМВОЛЬНЫЕ (не байтовые) смещения в оригинальной
                   строке, диапазон [start, end). Нужны для
                   replace-block merge: source[:start] + out + source[end:].
    kind         — тип чанка для логов и диагностики.
    """
    index: int
    total: int
    text: str
    preamble: str
    start: int
    end: int
    kind: str

    @property
    def byte_size(self) -> int:
        """Размер text в UTF-8 байтах."""
        return len(self.text.encode("utf-8"))

    @property
    def preamble_byte_size(self) -> int:
        """Размер preamble в UTF-8 байтах (0, если preamble пуст)."""
        if not self.preamble:
            return 0
        return len(self.preamble.encode("utf-8"))

    @property
    def total_byte_size(self) -> int:
        """text + preamble — то, что реально уйдёт в модель."""
        return self.byte_size + self.preamble_byte_size


# ── ValidationIssue ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class ValidationIssue:
    """Одно замечание от validate().

    level:
      * "error"   — merged невалиден, batch.py решает по on_merge_invalid
      * "warning" — merged валиден, но требует внимания; показывается
                    в preview, не блокирует запись
    code    — машиночитаемый идентификатор (для тестов и логов).
    message — человекочитаемое описание для пользователя.
    """
    level: str
    code: str
    message: str


def has_errors(issues: list[ValidationIssue]) -> bool:
    """True, если среди issues есть хотя бы один уровня "error"."""
    return any(issue.level == "error" for issue in issues)


# ── Processor ABC ─────────────────────────────────────────────────────────

class Processor(ABC):
    """Абстрактный процессор режима.

    Конкретные реализации — CodeProcessor, DocProcessor.
    Каждая получает на вход свой суб-конфиг (CodeConfig / DocsConfig)
    и общий DefaultsConfig — детали см. в code.py / docs.py.
    """

    mode: ClassVar[str]

    @abstractmethod
    def split(
        self,
        source: str,
        budget_bytes: int,
    ) -> tuple[list[Chunk], list[ValidationIssue]]:
        """Разбить источник на чанки.

        Возвращает (chunks, issues):

        chunks — list[Chunk]. Гарантии реализации:
          * Возвращает непустой список.
          * Все chunks имеют одинаковый total == len(chunks).
          * Каждый chunk.total_byte_size <= budget_bytes.
          * chunk.start < chunk.end, диапазоны не пересекаются.
          * Chunks покрывают source в зависимости от стратегии:
            - docs: покрывают весь source без пропусков.
            - code (replace_block): покрывают только границы
              def/class; регионы между ними (module docstring,
              импорты, глобалы) НЕ являются чанками и
              сохраняются merge'ом без изменений.
          * Если source уже влезает в budget_bytes, возвращается
            один chunk с kind="whole" и start=0, end=len(source).

        issues — list[ValidationIssue] с level="warning" или
          level="info". Сообщения о применённых fallback'ах
          («использован жёсткий рез», «секция не имела заголовков»).
          Пустой список, если разбиение прошло без замечаний.
          Уровень "error" в split() не используется: критическая
          невозможность разбить источник — ProcessError.

        ProcessError, если разбиение невозможно:
          * source пуст или состоит из пробелов
          * budget_bytes слишком мал для осмысленного чанка
          * одна атомарная секция (например, единственный
            гигантский параграф) не влезает и не может быть
            разрезана без потери структуры, а политика
            запрещает жёсткий рез
        """

    @abstractmethod
    def merge(
        self,
        source: str,
        chunks: list[Chunk],
        outputs: list[str | None],
    ) -> str:
        """Собрать результат из выходов модели.

        outputs[i] — результат для chunks[i], или None если чанк
        провален. Поведение при None определяется настройкой
        on_chunk_failure:
          * fail    — merge не вызывается (batch.py прерывает)
          * partial — Processor сохраняет оригинальный фрагмент
                      на месте проваленного. Для code — с
                      орнамент-комментарием из CodeConfig.

        Гарантии реализации:
          * Длина outputs == длина chunks.
          * Все offsets используются без пересечений.
          * Порядок частей в результате соответствует порядку
            в source.
        """

    @abstractmethod
    def validate(
        self,
        source: str,
        merged: str,
    ) -> list[ValidationIssue]:
        """Проверить merged против source.

        Пустой список — валидно. Issues уровня "error" означают
        невалидный merge, "warning" — замечание без блокировки.
        Какие проверки выполняются — задаётся списком validate
        в суб-конфиге (code / docs).
        """


__all__ = [
    "CHUNK_OVERHEAD_TOKENS",
    "Chunk",
    "ProcessError",
    "Processor",
    "ValidationIssue",
    "check_budget_consistency",
    "compute_budget_bytes",
    "estimate_tokens",
    "has_errors",
]
