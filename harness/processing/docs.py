"""
DocProcessor — разбиение и merge для текстовых документов.

Три стратегии разбиения, применяются по убыванию предпочтения:

1. По заголовкам (markdown). Уровни, работающие как границы,
   задаются в config.docs.header_levels. Заголовки внутри
   ```-fence'ов игнорируются.

2. По параграфам (regex config.docs.paragraph_separator).
   Применяется, когда заголовков нет, или когда секция после
   разбиения по заголовкам всё ещё превышает бюджет.

3. Жёсткий рез по байтам (уважая границы UTF-8). Применяется,
   когда один параграф превышает бюджет. Границы структуры
   (заголовки, концы предложений) при этом теряются —
   пользователь получает warning.

Merge — конкатенация выходов. Для partial-режима на месте
проваленного чанка остаётся оригинальный текст: документ не
теряет секцию, пользователь видит, что её не обработали.

Валидация merged:
  * headers_preserved — все оригинальные заголовки на месте
                        (с учётом кратности)
  * fences_balanced   — чётное число ``` и ~~~ на верхнем уровне

Ограничения первой итерации:
  * overlap_bytes > 0 не поддерживается. Поле в конфиге есть,
    но при попытке включить — ProcessError при создании
    DocProcessor. Реализация overlap — будущая итерация.
  * Один гигантский параграф обрабатывается жёстким резом по
    байтам — структурные границы теряются, выдаётся warning.

Не зависит от BatchRunner. Конфиг приходит через конструктор.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import ClassVar

from harness.processing.base import (
    Chunk,
    ProcessError,
    Processor,
    ValidationIssue,
)
from harness.processing.config import DefaultsConfig, DocsConfig


# Заголовок markdown: 1–6 диезов, пробел, до конца строки.
_HEADER_RE = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*$", re.MULTILINE)

# Открытие fence-блока. Закрытие — тот же символ, длина не меньше
# открывающей. Для целей tracking'а достаточно открытий.
_FENCE_OPEN_RE = re.compile(r"^(`{3,}|~{3,})", re.MULTILINE)

# Максимальное количество байтов, которое может быть «отрезано»
# с конца среза при жёстком резе, чтобы получить валидный UTF-8.
_MAX_UTF8_TRIM = 4


class DocProcessor(Processor):
    """Разбиение и merge документов (markdown, rst, txt)."""

    mode: ClassVar[str] = "docs"

    def __init__(self, config: DocsConfig, defaults: DefaultsConfig):
        self.config = config
        self.defaults = defaults
        self._header_levels = frozenset(config.header_levels)

        # overlap_bytes пока не реализован. Молчаливое игнорирование
        # пользовательской настройки — плохо: пользователь думает,
        # что overlap работает, а его нет. Явный отказ.
        if defaults.overlap_bytes > 0:
            raise ProcessError(
                f"defaults.overlap_bytes={defaults.overlap_bytes} "
                f"не поддерживается в текущей версии "
                f"DocProcessor. Установите overlap_bytes: 0 в "
                f"config/processing.yaml."
            )

        try:
            self._paragraph_re = re.compile(config.paragraph_separator)
        except re.error as e:
            # config.py уже компилировал regex, но перестраховка
            # стоит одну строку: DocsConfig может прийти из теста
            # напрямую, минуя config.load().
            raise ProcessError(
                f"invalid paragraph_separator "
                f"{config.paragraph_separator!r}: {e}"
            ) from e

    # ── split ─────────────────────────────────────────────────────────────

    def split(
        self,
        source: str,
        budget_bytes: int,
    ) -> tuple[list[Chunk], list[ValidationIssue]]:
        issues: list[ValidationIssue] = []

        if not source or not source.strip():
            raise ProcessError("empty source")
        if budget_bytes < 10:
            raise ProcessError(
                f"budget_bytes={budget_bytes} too small"
            )

        if len(source.encode("utf-8")) <= budget_bytes:
            return (
                [Chunk(index=1, total=1, text=source, preamble="",
                       start=0, end=len(source), kind="whole")],
                issues,
            )

        # Первый уровень: по заголовкам.
        sections = self._split_by_headers(source)
        if sections is None or len(sections) < 2:
            # Заголовков нет или одна секция — по параграфам.
            sections = self._split_by_paragraphs(source)
            if sections is None:
                # Один гигантский абзац — жёсткий рез.
                issues.append(ValidationIssue(
                    level="warning",
                    code="hard_split_used",
                    message=(
                        "source has no section or paragraph "
                        "boundaries; hard-split by bytes was used, "
                        "structural boundaries lost"
                    ),
                ))
                sections = self._split_hard(source, budget_bytes)

        # Второй уровень: секции, всё ещё превышающие бюджет,
        # разбиваем по параграфам или жёстко.
        chunks_data: list[tuple[int, int, str, str]] = []
        for start, end, text, kind in sections:
            if len(text.encode("utf-8")) <= budget_bytes:
                chunks_data.append((start, end, text, kind))
                continue
            sub = self._split_by_paragraphs(text)
            if sub is None:
                issues.append(ValidationIssue(
                    level="warning",
                    code="hard_split_used",
                    message=(
                        f"section at byte offset "
                        f"{len(source[:start].encode('utf-8'))} "
                        f"({kind}) exceeds budget and has no "
                        f"internal paragraph boundaries; "
                        f"hard-split by bytes was used"
                    ),
                ))
                sub = self._split_hard(text, budget_bytes)
            for s_start, s_end, s_text, s_kind in sub:
                chunks_data.append(
                    (start + s_start, start + s_end, s_text, s_kind),
                )

        # Слияние мелких чанков.
        chunks_data = self._merge_small_chunks(chunks_data, budget_bytes)

        total = len(chunks_data)
        chunks = [
            Chunk(
                index=i + 1,
                total=total,
                text=text,
                preamble="",
                start=start,
                end=end,
                kind=kind,
            )
            for i, (start, end, text, kind) in enumerate(chunks_data)
        ]
        return chunks, issues

    # ── merge ─────────────────────────────────────────────────────────────

    def merge(
        self,
        source: str,
        chunks: list[Chunk],
        outputs: list[str | None],
    ) -> str:
        if len(chunks) != len(outputs):
            raise ProcessError(
                f"chunks/outputs length mismatch: "
                f"{len(chunks)} vs {len(outputs)}"
            )

        parts: list[str] = []
        for chunk, output in zip(chunks, outputs):
            if output is None:
                # partial-режим: сохранить оригинальный фрагмент.
                parts.append(chunk.text)
            else:
                parts.append(output)

        merged = "".join(parts)

        # Для одного chunk'а (kind="whole") merge тривиален: это
        # просто output[0]. Всё остальное — конкатенация фрагментов,
        # покрывающих source без пропусков (гарантия split).
        return merged

    # ── validate ──────────────────────────────────────────────────────────

    def validate(
        self,
        source: str,
        merged: str,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        if "headers_preserved" in self.config.validate:
            issues.extend(self._validate_headers(source, merged))

        if "fences_balanced" in self.config.validate:
            issues.extend(self._validate_fences(merged))

        return issues

    # ── split internals ───────────────────────────────────────────────────

    def _fence_ranges(self, text: str) -> list[tuple[int, int]]:
        """Диапазоны [start, end) внутри ``` или ~~~ блоков.

        Сканер идёт по всем открывающим маркерам (в начале строки),
        чередует их как открытие/закрытие. Учитывает смешанные
        маркеры (нельзя закрыть ``` через ~~~).

        Незакрытый fence «съедает» весь остаток текста — это
        соответствует поведению markdown-парсеров и семантике
        «внутри незакрытого блока не парсим».
        """
        ranges: list[tuple[int, int]] = []
        in_fence = False
        fence_start = 0
        fence_char = ""
        fence_len = 0

        for m in _FENCE_OPEN_RE.finditer(text):
            marker = m.group(1)
            char = marker[0]
            length = len(marker)

            if not in_fence:
                in_fence = True
                fence_start = m.start()
                fence_char = char
                fence_len = length
            elif char == fence_char and length >= fence_len:
                ranges.append((fence_start, m.end()))
                in_fence = False

        if in_fence:
            ranges.append((fence_start, len(text)))
        return ranges

    @staticmethod
    def _in_ranges(pos: int, ranges: list[tuple[int, int]]) -> bool:
        """Проверка вхождения позиции в любой из диапазонов.

        Диапазоны отсортированы и не пересекаются (так их строит
        _fence_ranges). Early exit — если pos меньше начала
        текущего range, дальше смотреть бессмысленно.
        """
        for start, end in ranges:
            if pos < start:
                return False
            if start <= pos < end:
                return True
        return False

    def _split_by_headers(
        self,
        source: str,
    ) -> list[tuple[int, int, str, str]] | None:
        """Разбить по заголовкам, пропуская те, что внутри fence'ов.

        Возвращает список (start, end, text, kind) или None, если
        заголовков меньше двух — то есть разбиение бессмысленно.
        """
        fence_ranges = self._fence_ranges(source)
        positions: list[int] = []

        for m in _HEADER_RE.finditer(source):
            if self._in_ranges(m.start(), fence_ranges):
                continue
            level = len(m.group(1))
            if level not in self._header_levels:
                continue
            positions.append(m.start())

        if len(positions) < 2:
            # Один заголовок — это не разбиение, это один маркер.
            return None

        boundaries = sorted(set([0, *positions, len(source)]))
        sections: list[tuple[int, int, str, str]] = []
        for i in range(len(boundaries) - 1):
            start, end = boundaries[i], boundaries[i + 1]
            text = source[start:end]
            if not text.strip():
                continue
            # Заголовок в начале — это "section". Иначе (текст до
            # первого заголовка) — "preamble".
            kind = "section" if _HEADER_RE.match(text) else "preamble"
            sections.append((start, end, text, kind))

        if len(sections) < 2:
            return None
        return sections

    def _split_by_paragraphs(
        self,
        text: str,
    ) -> list[tuple[int, int, str, str]] | None:
        """Разбить по regex paragraph_separator.

        Разделитель попадает в конец предыдущего параграфа — так
        при merge между output'ами восстанавливаются пустые строки,
        которые модель могла потерять.
        """
        matches = list(self._paragraph_re.finditer(text))
        if not matches:
            return None

        boundaries = [0]
        for m in matches:
            if m.end() > boundaries[-1]:
                boundaries.append(m.end())
        boundaries.append(len(text))

        boundaries = sorted(set(boundaries))
        sections: list[tuple[int, int, str, str]] = []
        for i in range(len(boundaries) - 1):
            start, end = boundaries[i], boundaries[i + 1]
            text_slice = text[start:end]
            if not text_slice.strip():
                continue
            sections.append((start, end, text_slice, "paragraph"))

        if not sections:
            return None
        return sections

    def _split_hard(
        self,
        text: str,
        budget_bytes: int,
    ) -> list[tuple[int, int, str, str]]:
        """Жёсткий рез по байтам, не разрывая UTF-8.

        Границы структуры теряются: пользователь уже получил
        warning от split(). Возвращает чанки, покрывающие text
        без пропусков.
        """
        result: list[tuple[int, int, str, str]] = []
        pos = 0
        text_len = len(text)

        while pos < text_len:
            remaining = text[pos:]
            # Верхняя оценка: не более budget_bytes символов
            # (для ASCII 1 символ = 1 байт).
            slice_candidate = remaining[:budget_bytes]
            encoded = slice_candidate.encode("utf-8")

            if len(encoded) <= budget_bytes:
                chunk_str = slice_candidate
            else:
                # Урезаем по байтам, потом откатываемся до валидной
                # границы UTF-8.
                raw_bytes = encoded[:budget_bytes]
                chunk_str = ""
                for trim in range(_MAX_UTF8_TRIM + 1):
                    try:
                        chunk_str = raw_bytes[:len(raw_bytes) - trim] \
                            .decode("utf-8")
                        break
                    except UnicodeDecodeError:
                        continue
                if not chunk_str:
                    raise ProcessError(
                        f"cannot hard-split at char offset {pos}: "
                        f"budget_bytes={budget_bytes} too small "
                        f"for a single UTF-8 character"
                    )

            if not chunk_str:
                raise ProcessError(
                    f"hard-split produced empty chunk at offset {pos}"
                )

            result.append((
                pos, pos + len(chunk_str), chunk_str, "hard_split",
            ))
            pos += len(chunk_str)

        return result

    def _merge_small_chunks(
        self,
        chunks_data: list[tuple[int, int, str, str]],
        budget_bytes: int,
    ) -> list[tuple[int, int, str, str]]:
        """Склеить чанки меньше chunk_min_bytes с предыдущим.

        Если предыдущего нет или склейка превысит budget_bytes,
        маленький чанк остаётся отдельным — это не ошибка, но
        приведёт к лишнему вызову модели. Лучше так, чем потерять
        данные или превысить контекст.
        """
        if len(chunks_data) <= 1:
            return chunks_data

        min_bytes = self.defaults.chunk_min_bytes
        result: list[tuple[int, int, str, str]] = []

        for start, end, text, kind in chunks_data:
            text_bytes = len(text.encode("utf-8"))

            if text_bytes >= min_bytes or not result:
                result.append((start, end, text, kind))
                continue

            prev_start, prev_end, prev_text, prev_kind = result[-1]
            combined = prev_text + text
            if len(combined.encode("utf-8")) <= budget_bytes:
                result[-1] = (prev_start, end, combined, prev_kind)
            else:
                result.append((start, end, text, kind))

        return result

    # ── validate internals ────────────────────────────────────────────────

    def _extract_headers(self, text: str) -> Counter[str]:
        """Заголовки как Counter по строке 'N:title'.

        Использование Counter, а не set — если в файле два
        одинаковых заголовка, потеря одного должна быть видна.
        """
        fence_ranges = self._fence_ranges(text)
        counter: Counter[str] = Counter()
        for m in _HEADER_RE.finditer(text):
            if self._in_ranges(m.start(), fence_ranges):
                continue
            level = len(m.group(1))
            title = m.group(2).strip()
            counter[f"{level}:{title}"] += 1
        return counter

    def _validate_headers(
        self,
        source: str,
        merged: str,
    ) -> list[ValidationIssue]:
        src = self._extract_headers(source)
        mrg = self._extract_headers(merged)

        missing = [
            (key, src[key] - mrg.get(key, 0))
            for key in src
            if src[key] > mrg.get(key, 0)
        ]
        if missing:
            sample = ", ".join(
                f"{key!r}×{count}" for key, count in missing[:3]
            )
            return [ValidationIssue(
                level="error",
                code="headers_missing",
                message=(
                    f"{len(missing)} header(s) lost or reduced: "
                    f"{sample}"
                ),
            )]

        added = [
            (key, mrg[key] - src.get(key, 0))
            for key in mrg
            if mrg[key] > src.get(key, 0)
        ]
        if added:
            sample = ", ".join(
                f"{key!r}×{count}" for key, count in added[:3]
            )
            return [ValidationIssue(
                level="warning",
                code="headers_added",
                message=(
                    f"model added {len(added)} header(s): {sample}"
                ),
            )]

        return []

    def _validate_fences(self, merged: str) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for marker_char in ("`", "~"):
            pattern = rf"^{re.escape(marker_char) * 3}"
            count = len(re.findall(pattern, merged, re.MULTILINE))
            if count % 2 != 0:
                issues.append(ValidationIssue(
                    level="error",
                    code="fences_unbalanced",
                    message=(
                        f"unbalanced {marker_char * 3} markers in "
                        f"merged: {count} (odd count)"
                    ),
                ))
        return issues
