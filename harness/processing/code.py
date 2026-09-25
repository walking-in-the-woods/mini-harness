"""
CodeProcessor — разбиение и merge для Python-кода.

Стратегия разбиения:
    * Top-level def / async def / class (boundary_nodes из
      config.code) → один chunk на узел. Декораторы входят в
      диапазон чанка.
    * Всё остальное — module docstring, импорты, top-level
      assignments, if __name__, try/except — НЕ разбивается.
      Сохраняется merge'ом как есть, между чанками.
    * Если source уже влезает в budget — один chunk kind="whole".

Preamble:
    * Если config.code.include_preamble = true, текст до первого
      chunk'а (module docstring + импорты + глобалы) добавляется
      как Chunk.preamble к каждому chunk'у.
    * Ограничение config.code.preamble_max_bytes. Превышение —
      ProcessError, не молчаливая обрезка.

Merge (replace_block):
    * Chunks отсортированы по start. Идём по source, копируем
      регионы между chunks без изменений, chunks заменяем на
      output.
    * output=None (провал чанка при partial) → оригинальный текст
      чанка, обрамлённый ornament-комментариями.

Validate — реализованные проверки зависят от config.code.validate:
    ast_parseable              — merged парсится как Python
    defs_preserved             — все top-level имена на месте
    bodies_nonempty            — тела функций не опустели
    signature_args_preserved   — имена аргументов не изменились
    no_new_top_level           — WARN, если модель добавила defs

Ограничения первой итерации:
    * Class не разбивается на методы. Если класс превышает budget
      — ProcessError. Это осознанно: class-splitting требует
      аккуратной работы с self, class vars, порядком методов.
    * Только Python. ast.parse вызывается всегда; для не-Python
      source — ProcessError с понятным сообщением.
    * Дублирующиеся имена top-level defs обрабатываются как одно
      (dict semantics). Редкий кейс, осознанный trade-off.
    * Изменение preamble моделью не детектируется. Preamble — это
      hint, а не контракт. Будущая итерация.
"""

from __future__ import annotations

import ast
from typing import ClassVar

from harness.processing.base import (
    Chunk,
    ProcessError,
    Processor,
    ValidationIssue,
)
from harness.processing.config import CodeConfig, DefaultsConfig


# ── Утилиты смещений ──────────────────────────────────────────────────────

def _line_col_to_char_offset(
    source: str,
    lineno: int,
    col_offset: int,
) -> int:
    """Преобразовать (lineno 1-based, col_offset в UTF-8 байтах)
    в символьное смещение в source.

    Python AST даёт col_offset как байтовое смещение в UTF-8-кодировке
    строки. Python-срез `source[a:b]` работает по символам. Для ASCII
    эти величины совпадают; для не-ASCII (кириллица в строковых
    литералах или комментариях до def на той же строке) — нет.

    Разделитель строк — только `\\n`, как в AST. `\\r` (если есть) —
    часть предыдущей строки.
    """
    if lineno < 1:
        raise ProcessError(
            f"invalid lineno {lineno} (must be >= 1)"
        )
    lines = source.split("\n")
    if lineno > len(lines):
        raise ProcessError(
            f"lineno {lineno} out of range "
            f"(source has {len(lines)} lines)"
        )
    char_offset = 0
    for i in range(lineno - 1):
        char_offset += len(lines[i]) + 1   # +1 за \n
    line = lines[lineno - 1]
    line_bytes = line.encode("utf-8")
    if col_offset > len(line_bytes):
        raise ProcessError(
            f"col_offset {col_offset} > line {lineno} "
            f"byte length {len(line_bytes)}"
        )
    prefix_bytes = line_bytes[:col_offset]
    try:
        prefix = prefix_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ProcessError(
            f"col_offset {col_offset} not on UTF-8 boundary "
            f"of line {lineno}: {e}"
        ) from e
    return char_offset + len(prefix)


# ── CodeProcessor ─────────────────────────────────────────────────────────

class CodeProcessor(Processor):
    """Разбиение и merge Python-кода."""

    mode: ClassVar[str] = "code"

    def __init__(self, config: CodeConfig, defaults: DefaultsConfig):
        self.config = config
        self.defaults = defaults
        self._boundary_nodes = frozenset(config.boundary_nodes)

        # Проверка ornament-шаблона один раз при конструировании:
        # при merge опечатка в шаблоне вылетела бы в середине batch,
        # после долгой обработки предыдущих чанков.
        try:
            self.config.partial_ornament_open.format(index=0, total=0)
        except (KeyError, IndexError, ValueError) as e:
            raise ProcessError(
                f"invalid partial_ornament_open template "
                f"{self.config.partial_ornament_open!r}: {e}"
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

        source_bytes = len(source.encode("utf-8"))
        if source_bytes <= budget_bytes:
            return (
                [Chunk(
                    index=1, total=1, text=source, preamble="",
                    start=0, end=len(source), kind="whole",
                )],
                issues,
            )

        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            raise ProcessError(
                f"source does not parse as Python: "
                f"line {e.lineno}: {e.msg}"
            ) from e

        boundaries = self._collect_boundaries(source, tree)
        if not boundaries:
            raise ProcessError(
                f"source ({source_bytes} bytes) exceeds budget "
                f"({budget_bytes}) and has no top-level "
                f"def/class/async def to split by"
            )

        chunk_preamble = ""
        if self.config.include_preamble:
            preamble_text = source[:boundaries[0][0]]
            if preamble_text.strip():
                pre_bytes = len(preamble_text.encode("utf-8"))
                if pre_bytes > self.config.preamble_max_bytes:
                    raise ProcessError(
                        f"preamble ({pre_bytes} bytes) exceeds "
                        f"preamble_max_bytes="
                        f"{self.config.preamble_max_bytes}"
                    )
                chunk_preamble = self._format_preamble(preamble_text)
                issues.append(ValidationIssue(
                    level="info",
                    code="preamble_included",
                    message=(
                        f"module preamble ({pre_bytes} bytes) "
                        f"included in every chunk as read-only "
                        f"context"
                    ),
                ))

        preamble_bytes = len(chunk_preamble.encode("utf-8"))

        total = len(boundaries)
        chunks: list[Chunk] = []
        for i, (start, end, kind, name) in enumerate(boundaries):
            text = source[start:end]
            text_bytes = len(text.encode("utf-8"))
            if text_bytes + preamble_bytes > budget_bytes:
                raise ProcessError(
                    f"chunk {i+1}/{total} ({kind} {name!r}) "
                    f"is {text_bytes} bytes "
                    f"(+{preamble_bytes} preamble), "
                    f"exceeds budget {budget_bytes}. "
                    f"Split the {kind} internally or increase "
                    f"HARNESS_NUM_CTX."
                )
            chunks.append(Chunk(
                index=i + 1,
                total=total,
                text=text,
                preamble=chunk_preamble,
                start=start,
                end=end,
                kind=kind,
            ))

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

        # Подготовить outputs: для None — обернуть оригинал
        # в орнамент; для непустых — снять возможный
        # скопированный preamble.
        prepared: list[str] = []
        for chunk, output in zip(chunks, outputs):
            if output is None:
                prepared.append(self._wrap_with_ornament(chunk))
            else:
                prepared.append(
                    self._strip_preamble(output, chunk.preamble)
                )

        # Walk по source, копируя регионы между chunks.
        ordered = sorted(
            zip(chunks, prepared),
            key=lambda pair: pair[0].start,
        )

        parts: list[str] = []
        cursor = 0
        for chunk, output in ordered:
            if chunk.start < cursor:
                raise ProcessError(
                    f"chunk overlap: chunk {chunk.index} "
                    f"starts at {chunk.start}, previous ended "
                    f"at {cursor}"
                )
            parts.append(source[cursor:chunk.start])
            parts.append(output)
            cursor = chunk.end
        parts.append(source[cursor:])

        return "".join(parts)

    # ── validate ──────────────────────────────────────────────────────────

    def validate(
        self,
        source: str,
        merged: str,
    ) -> list[ValidationIssue]:
        checks = frozenset(self.config.validate)

        try:
            src_tree = ast.parse(source)
        except SyntaxError as e:
            return [ValidationIssue(
                level="error",
                code="source_invalid",
                message=(
                    f"source does not parse: line {e.lineno}: "
                    f"{e.msg}"
                ),
            )]

        mrg_tree = None
        if "ast_parseable" in checks:
            try:
                mrg_tree = ast.parse(merged)
            except SyntaxError as e:
                return [ValidationIssue(
                    level="error",
                    code="merged_invalid",
                    message=(
                        f"merged does not parse: "
                        f"line {e.lineno}: {e.msg}"
                    ),
                )]

        if mrg_tree is None:
            try:
                mrg_tree = ast.parse(merged)
            except SyntaxError:
                # Без ast_parseable в checks merged невалиден —
                # остальные проверки невозможны, но это не error.
                return []

        issues: list[ValidationIssue] = []

        if "defs_preserved" in checks:
            issues.extend(self._check_defs_preserved(
                src_tree, mrg_tree,
            ))
        if "bodies_nonempty" in checks:
            issues.extend(self._check_bodies_nonempty(
                src_tree, mrg_tree,
            ))
        if "signature_args_preserved" in checks:
            issues.extend(self._check_signature_args(
                src_tree, mrg_tree,
            ))
        if "no_new_top_level" in checks:
            issues.extend(self._check_no_new_top_level(
                src_tree, mrg_tree,
            ))

        return issues

    # ── split helpers ─────────────────────────────────────────────────────

    def _collect_boundaries(
        self,
        source: str,
        tree: ast.Module,
    ) -> list[tuple[int, int, str, str]]:
        """Список (start, end, kind, name) для top-level defs/classes.

        Порядок соответствует порядку в tree.body (порядок в
        исходнике). Если boundary_nodes не матчит ни один узел —
        пустой список.
        """
        result: list[tuple[int, int, str, str]] = []
        for node in tree.body:
            node_type = type(node).__name__
            if node_type not in self._boundary_nodes:
                continue

            if node_type in ("FunctionDef", "AsyncFunctionDef"):
                kind = "function"
            elif node_type == "ClassDef":
                kind = "class"
            else:
                kind = node_type.lower()

            start = self._node_start(source, node)
            end = self._node_end(source, node)
            result.append((start, end, kind, node.name))
        return result

    @staticmethod
    def _node_start(source: str, node: ast.AST) -> int:
        """Начало диапазона, включая декораторы.

        node.lineno указывает на строку `def`/`class`, но при
        наличии декораторов его нужно расширить назад до первой
        строки декоратора. Для top-level узлов `@` стоит в
        колонке 0.
        """
        decorators = getattr(node, "decorator_list", None)
        if decorators:
            first = decorators[0]
            return _line_col_to_char_offset(source, first.lineno, 0)
        return _line_col_to_char_offset(
            source, node.lineno, node.col_offset,
        )

    @staticmethod
    def _node_end(source: str, node: ast.AST) -> int:
        """Конец диапазона.

        end_lineno/end_col_offset доступны с Python 3.8.
        Проект требует 3.10+, поэтому без fallback.
        """
        return _line_col_to_char_offset(
            source, node.end_lineno, node.end_col_offset,
        )

    @staticmethod
    def _format_preamble(text: str) -> str:
        """Обернуть preamble в делимитеры, гарантировать перевод
        строки в конце, чтобы следующий фрагмент не склеился с
        комментарием.
        """
        if text and not text.endswith("\n"):
            text = text + "\n"
        return (
            "# === Module preamble "
            "(read-only context, do not modify) ===\n"
            f"{text}"
            "# === End preamble ===\n"
        )

    # ── merge helpers ─────────────────────────────────────────────────────

    def _wrap_with_ornament(self, chunk: Chunk) -> str:
        """Обернуть оригинальный текст чанка орнамент-комментариями.

        Используется при on_chunk_failure=partial: чанк не обработан,
        но в merged должен быть виден как маркер.
        """
        try:
            open_line = self.config.partial_ornament_open.format(
                index=chunk.index, total=chunk.total,
            )
        except (KeyError, IndexError, ValueError) as e:
            raise ProcessError(
                f"invalid partial_ornament_open template "
                f"{self.config.partial_ornament_open!r}: {e}"
            ) from e
        return (
            f"{open_line}\n"
            f"{chunk.text}\n"
            f"{self.config.partial_ornament_close}"
        )

    @staticmethod
    def _strip_preamble(output: str, preamble: str) -> str:
        """Снять preamble с начала output, если модель его
        скопировала.

        Модели свойственно включать весь user-message в ответ,
        включая обёртку preamble. Это приводит к дублированию
        импортов при merge. Точное совпадение (посимвольное) —
        достаточный признак; частичное совпадение игнорируем.
        """
        if not preamble:
            return output
        if output.startswith(preamble):
            return output[len(preamble):].lstrip("\n")
        return output

    # ── validate helpers ──────────────────────────────────────────────────

    @staticmethod
    def _top_level_defs(
        tree: ast.Module,
    ) -> dict[str, ast.AST]:
        """Имена top-level def/async def/class → узел.

        При дублирующихся именах побеждает последний. Редкий кейс;
        потеря «первого foo» при таком дублировании не
        детектируется. Осознанный trade-off первой итерации.
        """
        result: dict[str, ast.AST] = {}
        for node in tree.body:
            if isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            ):
                result[node.name] = node
        return result

    def _check_defs_preserved(
        self,
        src_tree: ast.Module,
        mrg_tree: ast.Module,
    ) -> list[ValidationIssue]:
        src = set(self._top_level_defs(src_tree))
        mrg = set(self._top_level_defs(mrg_tree))
        missing = src - mrg
        if not missing:
            return []
        return [ValidationIssue(
            level="error",
            code="defs_lost",
            message=(
                "top-level defs lost: "
                + ", ".join(f"{name!r}" for name in sorted(missing))
            ),
        )]

    def _check_bodies_nonempty(
        self,
        src_tree: ast.Module,
        mrg_tree: ast.Module,
    ) -> list[ValidationIssue]:
        src_defs = self._top_level_defs(src_tree)
        mrg_defs = self._top_level_defs(mrg_tree)

        issues: list[ValidationIssue] = []
        for name, src_node in src_defs.items():
            src_size = self._body_size(src_node)
            if src_size == 0:
                continue
            mrg_node = mrg_defs.get(name)
            if mrg_node is None:
                # defs_preserved отловит; здесь не дублируем.
                continue
            if self._body_size(mrg_node) == 0:
                issues.append(ValidationIssue(
                    level="error",
                    code="body_lost",
                    message=(
                        f"body of {name!r} lost "
                        f"(was {src_size} statements)"
                    ),
                ))
        return issues

    @staticmethod
    def _body_size(node: ast.AST) -> int:
        """Число содержательных statements в теле.

        Docstring (первый Expr с Constant[str]) не считается —
        функция с одним docstring'ом имеет body_size=0.
        """
        body = getattr(node, "body", None)
        if body is None:
            return 0
        real = [
            s for s in body
            if not (
                isinstance(s, ast.Expr)
                and isinstance(s.value, ast.Constant)
                and isinstance(s.value.value, str)
            )
        ]
        return len(real)

    @staticmethod
    def _signature_arg_names(node: ast.AST) -> list[str] | None:
        """Имена аргументов функции в порядке появления.

        Возвращает None для ClassDef (у класса нет args).
        Учитывает posonlyargs, args, vararg, kwonlyargs, kwarg.
        Аннотации и default-значения не сравниваются — задача
        «добавь типы» их меняет.
        """
        if not isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            return None
        a = node.args
        names: list[str] = []
        names.extend(arg.arg for arg in a.posonlyargs)
        names.extend(arg.arg for arg in a.args)
        if a.vararg is not None:
            names.append(a.vararg.arg)
        names.extend(arg.arg for arg in a.kwonlyargs)
        if a.kwarg is not None:
            names.append(a.kwarg.arg)
        return names

    def _check_signature_args(
        self,
        src_tree: ast.Module,
        mrg_tree: ast.Module,
    ) -> list[ValidationIssue]:
        src_defs = self._top_level_defs(src_tree)
        mrg_defs = self._top_level_defs(mrg_tree)

        issues: list[ValidationIssue] = []
        for name, src_node in src_defs.items():
            src_args = self._signature_arg_names(src_node)
            if src_args is None:
                continue
            mrg_node = mrg_defs.get(name)
            if mrg_node is None:
                continue
            mrg_args = self._signature_arg_names(mrg_node)
            if src_args != mrg_args:
                issues.append(ValidationIssue(
                    level="error",
                    code="signature_changed",
                    message=(
                        f"signature args of {name!r} changed: "
                        f"{src_args} -> {mrg_args}"
                    ),
                ))
        return issues

    def _check_no_new_top_level(
        self,
        src_tree: ast.Module,
        mrg_tree: ast.Module,
    ) -> list[ValidationIssue]:
        src = set(self._top_level_defs(src_tree))
        mrg = set(self._top_level_defs(mrg_tree))
        added = mrg - src
        if not added:
            return []
        return [ValidationIssue(
            level="warning",
            code="new_top_level",
            message=(
                f"merged has new top-level defs: "
                f"{', '.join(sorted(added))}"
            ),
        )]


__all__ = ["CodeProcessor"]
