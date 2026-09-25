"""
Batch-обработка коллекции файлов одним prompt-файлом.

Два режима:

* Non-chunked (по умолчанию). Файл уходит в модель целиком,
  результат проходит structural check (AST + line preservation).
  Если файл больше контекстного бюджета — item помечается failed
  с сообщением «use --chunk».

* Chunked (--chunk + --mode=code|docs). Файл разбивается на чанки
  через harness.processing. Каждый чанк трансформируется отдельно,
  результаты собираются через Processor.merge, merged проверяется
  через Processor.validate.

Структурная валидация non-chunked:

* .py — AST-проверка + line preservation
* C-like — баланс скобок + line preservation
* Остальное — line preservation

Chunked-валидация — см. harness.processing.code / .docs.
"""

from __future__ import annotations

import ast
import logging
import re
import secrets
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from harness.agent import HarnessAgent
from harness.audit import AuditLog
from harness.fs_guard import FileSystemGuard
from harness.processing import (
    ProcessError,
    ProcessingConfig,
    get_processor,
)
from harness.processing.base import (
    Chunk,
    ValidationIssue,
    check_budget_consistency,
    compute_budget_bytes,
    has_errors,
)


log = logging.getLogger(__name__)


# Предупреждение при большом количестве файлов.
_BIG_BATCH_WARN = 20

# Порог потери statement'ов в теле функции (для Python AST).
_BODY_LOSS_RATIO = 0.5

# Количество попыток трансформации в non-chunked режиме.
# Вторая — только при структурной ошибке, с хинтом в user message.
_MAX_TRANSFORM_ATTEMPTS = 2

# Расширения, для которых применяется точная AST-проверка.
_PY_EXT = {".py", ".pyw"}

# Расширения C-like языков.
_C_LIKE_EXT = {
    ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".java", ".c", ".h", ".cpp", ".hpp", ".cc", ".hh",
    ".cs", ".go", ".rs", ".php", ".kt", ".kts", ".swift",
    ".scala", ".dart",
}

# Хинт для структурного retry (non-chunked).
_STRUCTURAL_RETRY_HINT = (
    "ВНИМАНИЕ: предыдущий ответ потерял часть исходного кода. "
    "В этот раз выведи файл ЦЕЛИКОМ — каждую строку исходника без "
    "изменений, включая тела всех функций. Не добавляй импорты, "
    "не меняй сигнатуры, не используй type hints. Только вставь "
    "новый текст согласно инструкции, всё остальное оставь как было."
)

# Дефолтный marker «недостаточно контекста». Переопределяется
# из processing_config.defaults.insufficient_context_marker.
_DEFAULT_INSUFFICIENT_MARKER = "INSUFFICIENT_CONTEXT"


@dataclass
class FailedChunk:
    """Информация о проваленном чанке для дампа в <target>.failed/."""
    index: int
    kind: str
    source_text: str
    output: str | None
    error: str
    start: int
    end: int


@dataclass
class BatchItem:
    source_rel: str
    target_rel: str
    status: str = "pending"  # pending / transformed / failed / skipped
    content: str | None = None
    error: str | None = None
    duration_s: float = 0.0

    # Chunked-специфичное (дефолты в non-chunked).
    mode: str = ""
    chunks_total: int = 0
    chunks_ok: int = 0
    chunks_failed: int = 0
    chunks_skipped: int = 0
    failed_chunks: list[FailedChunk] = field(default_factory=list)
    validation_issues: list[ValidationIssue] = field(default_factory=list)


class BatchRunner:
    """Один прогон batch-обработки."""

    _SKIP_RE = re.compile(r"(\d+)\s+skip", re.IGNORECASE)

    def __init__(
        self,
        agent: HarnessAgent,
        fs_guard: FileSystemGuard,
        workspace: Path,
        audit: AuditLog,
        *,
        target_dir: str | None = None,
        mode: str = "",
        chunk_enabled: bool = False,
        processing_config: ProcessingConfig | None = None,
        overrides: dict | None = None,
    ):
        self.agent = agent
        self.fs_guard = fs_guard
        self.workspace = workspace
        self.audit = audit
        self.target_dir = target_dir
        self.mode = mode
        self.chunk_enabled = chunk_enabled
        self.processing_config = processing_config or ProcessingConfig()
        self.overrides = overrides or {}
        self.nonce = secrets.token_hex(4).upper()
        self.items: list[BatchItem] = []

        # Вычисляется в run() до обработки. В non-chunked может
        # остаться 0, если бюджет невычислим (тогда pre-flight
        # отключается — обратная совместимость).
        self._budget_bytes: int = 0

    # ------------------------ entry point ---------------------------------

    def run(self, source_dir: str, glob_pattern: str,
            prompt_path: str) -> None:
        source_dir = source_dir.rstrip("/")

        prompt_content, err = self._read_workspace_file(prompt_path)
        if err:
            print(f"[!] не удалось прочитать prompt-файл "
                  f"{prompt_path}: {err}")
            return

        # Overrides из CLI поверх конфига.
        self.processing_config = self._apply_overrides(
            self.processing_config
        )

        # Бюджет — до создания items, чтобы pre-flight знал цифру.
        self._budget_bytes = self._compute_budget(prompt_content)

        try:
            source_files = self._collect_files(source_dir, glob_pattern)
        except ValueError as e:
            print(f"[!] {e}")
            return

        if not source_files:
            print(f"[!] нет файлов, матчащих "
                  f"{source_dir}/{glob_pattern}")
            return

        target_dir = self.target_dir or self._default_target_dir()

        for source_rel in source_files:
            rel_within_source = source_rel[
                len(source_dir):].lstrip("/")
            target_rel = f"{target_dir.rstrip('/')}/{rel_within_source}"
            self.items.append(BatchItem(
                source_rel=source_rel,
                target_rel=target_rel,
                mode=self.mode,
            ))

        mode_label = self.mode or "(non-chunked)"
        chunk_label = "chunked" if self.chunk_enabled else "whole-file"
        print(f"[*] batch: {len(self.items)} файлов, "
              f"prompt: {prompt_path}, "
              f"target: {target_dir}/")
        if self.chunk_enabled:
            print(f"[*] режим: {mode_label}, {chunk_label}, "
                  f"budget: {self._budget_bytes} байт")
        print(file=sys.stderr)

        if len(self.items) >= _BIG_BATCH_WARN:
            est = len(self.items) * 1.5
            print(f"[i] {len(self.items)} файлов — ориентировочно "
                  f"{est:.0f} минут. Ctrl+C для отмены.",
                  file=sys.stderr)

        for i, item in enumerate(self.items, 1):
            print(f"[*] [{i}/{len(self.items)}] {item.source_rel}",
                  file=sys.stderr, flush=True)
            self._process_item(item, prompt_content)
            if item.status == "transformed":
                if item.chunks_total:
                    print(f"    -> ok, {item.chunks_total} chunks, "
                          f"{len(item.content or '')} chars, "
                          f"{item.duration_s:.1f}s",
                          file=sys.stderr, flush=True)
                else:
                    print(f"    -> ok, {len(item.content or '')} chars, "
                          f"{item.duration_s:.1f}s",
                          file=sys.stderr, flush=True)
            else:
                print(f"    -> FAILED: {item.error}",
                      file=sys.stderr, flush=True)

        self.audit.write(
            "batch_transformed",
            source_dir=source_dir,
            glob=glob_pattern,
            prompt_path=prompt_path,
            target_dir=target_dir,
            mode=self.mode or None,
            chunked=self.chunk_enabled,
            total=len(self.items),
            transformed=sum(1 for it in self.items
                            if it.status == "transformed"),
            failed=sum(1 for it in self.items if it.status == "failed"),
        )

        print()
        self._render_preview()

        self._interactive_apply()

    # ------------------------ config & budget -----------------------------

    def _apply_overrides(self, config: ProcessingConfig) -> ProcessingConfig:
        """Применить overrides к defaults. Overrides — dict с
        ключами из DefaultsConfig. Пустой — config без изменений."""
        if not self.overrides:
            return config
        # Фильтруем overrides по известным полям DefaultsConfig,
        # чтобы случайный ключ из CLI не сломал dataclass.
        valid_keys = {
            "on_chunk_failure", "on_merge_invalid",
            "on_insufficient_context",
        }
        filtered = {
            k: v for k, v in self.overrides.items()
            if k in valid_keys and v is not None
        }
        if not filtered:
            return config
        new_defaults = replace(config.defaults, **filtered)
        return replace(config, defaults=new_defaults)

    def _compute_budget(self, prompt_content: str) -> int:
        """Вычислить бюджет в байтах.

        Если в конфиге задан `chunk_target_bytes > 0` — использовать
        его как фиксированный бюджет. Это override для тестов и для
        случаев, когда пользователь хочет явно управлять размером
        чанка вместо авто-формулы.

        Иначе — авто-формула из num_ctx и prompt. Agent может быть
        моком без num_ctx (тесты test_batch.py с ScriptedAgent).
        В этом случае возвращаем 0 — pre-flight отключается.

        При chunked-режиме ошибка — фатально: без бюджета chunking
        работать не может.
        """
        cfg = self.processing_config.defaults

        # Явный override из конфига.
        if cfg.chunk_target_bytes > 0:
            if self.chunk_enabled:
                try:
                    check_budget_consistency(
                        cfg.chunk_target_bytes, cfg.chunk_min_bytes,
                    )
                except ProcessError as e:
                    print(f"[!] несовместимый конфиг: {e}",
                          file=sys.stderr)
                    raise
            return cfg.chunk_target_bytes

        num_ctx_raw = getattr(self.agent, "num_ctx", None)
        if num_ctx_raw is None:
            if self.chunk_enabled:
                print("[!] agent has no num_ctx; chunked mode требует "
                      "реального HarnessAgent или chunk_target_bytes > 0",
                      file=sys.stderr)
                raise ProcessError(
                    "chunked mode requires agent.num_ctx or "
                    "chunk_target_bytes > 0"
                )
            return 0

        try:
            num_ctx = int(num_ctx_raw)
        except (TypeError, ValueError):
            if self.chunk_enabled:
                raise ProcessError(
                    f"agent.num_ctx is not an int: {num_ctx_raw!r}"
                )
            return 0

        try:
            budget = compute_budget_bytes(
                num_ctx=num_ctx,
                prompt_text=prompt_content,
                output_reserve_ratio=cfg.output_reserve_ratio,
                budget_safety=cfg.budget_safety,
            )
        except ProcessError as e:
            if self.chunk_enabled:
                print(f"[!] не удалось вычислить бюджет: {e}",
                      file=sys.stderr)
                raise
            print(f"[!] бюджет невычислим ({e}); pre-flight "
                  f"проверка размера отключена", file=sys.stderr)
            return 0

        if self.chunk_enabled:
            try:
                check_budget_consistency(budget, cfg.chunk_min_bytes)
            except ProcessError as e:
                print(f"[!] несовместимый конфиг: {e}", file=sys.stderr)
                raise

        return budget

    # ------------------------ internals -----------------------------------

    def _default_target_dir(self) -> str:
        ts = time.strftime("%Y-%m-%d-%H%M%S", time.localtime())
        return f"output/batch/{ts}"

    def _collect_files(self, source_dir: str,
                       glob_pattern: str) -> list[str]:
        src_abs, err = self.fs_guard.resolve_read(source_dir)
        if src_abs is None:
            raise ValueError(f"source_dir: {err}")
        if not src_abs.exists():
            raise ValueError(
                f"source_dir does not exist: {source_dir}"
            )
        if not src_abs.is_dir():
            raise ValueError(f"not a directory: {source_dir}")

        result: list[str] = []
        for path in sorted(src_abs.iterdir()):
            if not path.is_file():
                continue
            if not path.match(glob_pattern):
                continue
            rel = path.relative_to(self.workspace).as_posix()
            ok, reason = self.fs_guard.check_read(rel)
            if not ok:
                print(f"[i] пропущен {rel}: {reason}",
                      file=sys.stderr)
                continue
            result.append(rel)
        return result

    def _read_workspace_file(self, rel: str) -> tuple[str, str | None]:
        p, err = self.fs_guard.resolve_read(rel)
        if p is None:
            return "", err
        if not p.is_file():
            return "", f"not a file: {rel}"

        max_read = int(getattr(self.agent, "max_read", 200_000))
        try:
            size = p.stat().st_size
        except OSError as e:
            return "", str(e)
        if size > max_read:
            return "", f"file too large ({size} > {max_read})"
        try:
            return p.read_text(encoding="utf-8"), None
        except (OSError, UnicodeDecodeError) as e:
            return "", str(e)

    # ── диспетчер обработки одного item ───────────────────────────────────

    def _process_item(self, item: BatchItem, prompt_content: str) -> None:
        """Трансформация одного файла.

        Non-chunked: pre-flight по бюджету, затем старый путь
        через _transform + _check_structure.

        Chunked: split → transform по чанкам → merge → validate.
        """
        t0 = time.monotonic()

        source_content, err = self._read_workspace_file(item.source_rel)
        if err:
            item.status = "failed"
            item.error = f"read: {err}"
            item.duration_s = time.monotonic() - t0
            return

        ok, reason = self.fs_guard.check_write(item.target_rel)
        if not ok:
            item.status = "failed"
            item.error = f"target: {reason}"
            item.duration_s = time.monotonic() - t0
            return

        if self.chunk_enabled:
            self._process_item_chunked(
                item, source_content, prompt_content, t0,
            )
        else:
            self._process_item_whole(
                item, source_content, prompt_content, t0,
            )

    # ── non-chunked ветка ────────────────────────────────────────────────

    def _process_item_whole(
        self,
        item: BatchItem,
        source_content: str,
        prompt_content: str,
        t0: float,
    ) -> None:
        # Pre-flight: бюджет известен, и он превышен.
        if self._budget_bytes > 0:
            source_bytes = len(source_content.encode("utf-8"))
            if source_bytes > self._budget_bytes:
                item.status = "failed"
                item.error = (
                    f"source {source_bytes} bytes > budget "
                    f"{self._budget_bytes}. Use --chunk to enable "
                    f"splitting."
                )
                item.duration_s = time.monotonic() - t0
                print(f"    [!] pre-flight: {item.error}",
                      file=sys.stderr, flush=True)
                return

        result: str | None = None
        last_err: str | None = None
        last_err_is_structural = False

        for attempt in range(1, _MAX_TRANSFORM_ATTEMPTS + 1):
            hint = _STRUCTURAL_RETRY_HINT if attempt > 1 else None
            attempt_result, attempt_err = self.agent.transform_for_batch(
                source_path=item.source_rel,
                source_content=source_content,
                prompt_content=prompt_content,
                retry_hint=hint,
            )

            if attempt_err or attempt_result is None:
                last_err = attempt_err or "empty result"
                last_err_is_structural = False
                break

            struct_err = _check_structure(
                original=source_content,
                transformed=attempt_result,
                source_rel=item.source_rel,
            )
            if struct_err:
                last_err = f"structure: {struct_err}"
                last_err_is_structural = True
                if attempt < _MAX_TRANSFORM_ATTEMPTS:
                    print(f"    [!] structure: {struct_err}; "
                          f"retry с хинтом в user message",
                          file=sys.stderr, flush=True)
                    self.audit.write(
                        "batch_structural_retry",
                        source=item.source_rel,
                        attempt=attempt,
                        reason=struct_err,
                    )
                    continue
                break

            result = attempt_result
            last_err = None
            break

        item.duration_s = time.monotonic() - t0

        if result is None:
            item.status = "failed"
            item.error = last_err or "unknown"
            if last_err_is_structural and last_err:
                self.audit.write(
                    "batch_structural_failure",
                    source=item.source_rel,
                    reason=last_err.removeprefix("structure: "),
                )
            return

        item.status = "transformed"
        item.content = result

    # ── chunked ветка ────────────────────────────────────────────────────

    def _process_item_chunked(
        self,
        item: BatchItem,
        source_content: str,
        prompt_content: str,
        t0: float,
    ) -> None:
        try:
            processor = get_processor(
                self.mode, self.processing_config,
            )
        except ProcessError as e:
            item.status = "failed"
            item.error = f"processor init: {e}"
            item.duration_s = time.monotonic() - t0
            return

        try:
            chunks, split_issues = processor.split(
                source_content, self._budget_bytes,
            )
        except ProcessError as e:
            item.status = "failed"
            item.error = f"split: {e}"
            item.duration_s = time.monotonic() - t0
            return

        item.chunks_total = len(chunks)
        self.audit.write(
            "batch_chunked_start",
            source=item.source_rel,
            mode=self.mode,
            budget_bytes=self._budget_bytes,
            chunks_total=len(chunks),
        )
        for issue in split_issues:
            print(f"    [i] split: {issue.message}",
                  file=sys.stderr, flush=True)

        outputs: list[str | None] = []
        insufficient_marker = (
            self.processing_config.defaults.insufficient_context_marker
        )

        for chunk in chunks:
            chunk_t0 = time.monotonic()
            user_content = self._format_chunk_user_message(
                chunk, item.source_rel,
            )

            result, error = self.agent.transform_for_batch(
                source_path=item.source_rel,
                source_content=user_content,
                prompt_content=prompt_content,
                retry_hint=None,
            )

            chunk_seconds = time.monotonic() - chunk_t0

            if error or result is None:
                reason = error or "empty result"
                outputs.append(None)
                item.chunks_failed += 1
                item.failed_chunks.append(FailedChunk(
                    index=chunk.index, kind=chunk.kind,
                    source_text=chunk.text, output=None,
                    error=reason, start=chunk.start, end=chunk.end,
                ))
                self.audit.write(
                    "batch_chunk_failed",
                    source=item.source_rel,
                    index=chunk.index,
                    kind=chunk.kind,
                    reason=reason,
                )
                print(f"    [!] chunk {chunk.index}/{chunk.total} "
                      f"[{chunk.kind}] FAILED: {reason}",
                      file=sys.stderr, flush=True)

                if (self.processing_config.defaults.on_chunk_failure
                        == "fail"):
                    print(f"    [!] on_chunk_failure=fail → "
                          f"прерываю обработку этого файла",
                          file=sys.stderr, flush=True)
                    break
                continue

            # Проверка на честный отказ модели.
            stripped = result.strip()
            if (insufficient_marker and stripped == insufficient_marker):
                on_insuff = (
                    self.processing_config.defaults.on_insufficient_context
                )
                if on_insuff == "fail":
                    outputs.append(None)
                    item.chunks_failed += 1
                    item.failed_chunks.append(FailedChunk(
                        index=chunk.index, kind=chunk.kind,
                        source_text=chunk.text, output=result,
                        error="insufficient_context (model)",
                        start=chunk.start, end=chunk.end,
                    ))
                    print(f"    [!] chunk {chunk.index}/{chunk.total} "
                          f"[{chunk.kind}]: INSUFFICIENT_CONTEXT "
                          f"→ fail", file=sys.stderr, flush=True)
                    self.audit.write(
                        "batch_chunk_failed",
                        source=item.source_rel,
                        index=chunk.index,
                        kind=chunk.kind,
                        reason="insufficient_context",
                    )
                    if (self.processing_config.defaults.on_chunk_failure
                            == "fail"):
                        break
                    continue
                else:
                    # skip
                    outputs.append(chunk.text)
                    item.chunks_skipped += 1
                    print(f"    [i] chunk {chunk.index}/{chunk.total} "
                          f"[{chunk.kind}]: INSUFFICIENT_CONTEXT "
                          f"→ skip", file=sys.stderr, flush=True)
                    self.audit.write(
                        "batch_chunk_skipped",
                        source=item.source_rel,
                        index=chunk.index,
                        reason="insufficient_context",
                    )
                    continue

            outputs.append(result)
            item.chunks_ok += 1
            self.audit.write(
                "batch_chunk_ok",
                source=item.source_rel,
                index=chunk.index,
                kind=chunk.kind,
                bytes=len(result.encode("utf-8")),
                seconds=round(chunk_seconds, 2),
            )
            print(f"    [*] chunk {chunk.index}/{chunk.total} "
                  f"[{chunk.kind}] ok, {len(result)} chars, "
                  f"{chunk_seconds:.1f}s",
                  file=sys.stderr, flush=True)

        item.duration_s = time.monotonic() - t0

        # Если прервались раньше — дозаполнить outputs=None.
        while len(outputs) < len(chunks):
            remaining = chunks[len(outputs)]
            outputs.append(None)
            item.chunks_failed += 1
            item.failed_chunks.append(FailedChunk(
                index=remaining.index, kind=remaining.kind,
                source_text=remaining.text, output=None,
                error="skipped: on_chunk_failure=fail",
                start=remaining.start, end=remaining.end,
            ))

        # Если был fail — выходим.
        if (item.chunks_failed > 0
                and self.processing_config.defaults.on_chunk_failure
                == "fail"):
            item.status = "failed"
            item.error = (
                f"{item.chunks_failed}/{item.chunks_total} "
                f"chunks failed"
            )
            return

        # Merge.
        try:
            merged = processor.merge(source_content, chunks, outputs)
        except ProcessError as e:
            item.status = "failed"
            item.error = f"merge: {e}"
            return

        # Validate.
        try:
            issues = processor.validate(source_content, merged)
        except ProcessError as e:
            item.status = "failed"
            item.error = f"validate: {e}"
            return

        item.validation_issues = issues
        for issue in issues:
            level_tag = "!" if issue.level == "error" else "i"
            print(f"    [{level_tag}] validate: {issue.message}",
                  file=sys.stderr, flush=True)

        if has_errors(issues):
            self.audit.write(
                "batch_merge_invalid",
                source=item.source_rel,
                issues=[i.message for i in issues],
            )
            if (self.processing_config.defaults.on_merge_invalid
                    == "fail"):
                item.status = "failed"
                item.error = (
                    f"merge invalid: "
                    f"{'; '.join(i.message for i in issues)}"
                )
                return

        item.status = "transformed"
        item.content = merged

    @staticmethod
    def _format_chunk_user_message(
        chunk: Chunk, source_rel: str,
    ) -> str:
        """Сформировать user message для чанка.

        Формат:
            [Part N/M of path]

            <text>

        Если есть preamble:
            [Part N/M of path]

            === Read-only context (do not modify) ===
            <preamble>

            === Your chunk ===
            <text>
        """
        header = (
            f"[Part {chunk.index}/{chunk.total} of {source_rel}]"
        )
        if not chunk.preamble:
            return f"{header}\n\n{chunk.text}"
        return (
            f"{header}\n\n"
            f"=== Read-only context (do not modify) ===\n"
            f"{chunk.preamble}"
            f"=== Your chunk ===\n"
            f"{chunk.text}"
        )

    # ── failed-чанки ─────────────────────────────────────────────────────

    def _write_failed_chunks(self, item: BatchItem) -> Path | None:
        """Записать failed_chunks в <target>.failed/.

        Возвращает путь к директории или None, если писать нечего.
        """
        if not item.failed_chunks:
            return None

        failed_dir_rel = item.target_rel + ".failed"
        failed_dir, err = self.fs_guard.resolve_write(
            failed_dir_rel + "/README.md",
        )
        if failed_dir is None:
            print(f"    [!] не могу создать {failed_dir_rel}: {err}",
                  file=sys.stderr)
            return None

        target_dir = failed_dir.parent
        target_dir.mkdir(parents=True, exist_ok=True)

        readme_parts: list[str] = [
            f"# Failed chunks for {item.source_rel}",
            "",
            f"- **Target:** `{item.target_rel}`",
            f"- **Mode:** `{item.mode}`",
            f"- **Chunks:** total={item.chunks_total}, "
            f"ok={item.chunks_ok}, failed={item.chunks_failed}, "
            f"skipped={item.chunks_skipped}",
            "",
            "## Failed chunks",
            "",
        ]

        for fc in item.failed_chunks:
            stem = f"chunk_{fc.index:02d}"
            try:
                (target_dir / f"{stem}_source.txt").write_text(
                    fc.source_text, encoding="utf-8",
                )
            except OSError as e:
                print(f"    [!] не записал {stem}_source.txt: {e}",
                      file=sys.stderr)

            if fc.output is not None:
                try:
                    (target_dir / f"{stem}_output.txt").write_text(
                        fc.output, encoding="utf-8",
                    )
                except OSError as e:
                    print(f"    [!] не записал {stem}_output.txt: {e}",
                          file=sys.stderr)

            try:
                (target_dir / f"{stem}_error.txt").write_text(
                    fc.error, encoding="utf-8",
                )
            except OSError as e:
                print(f"    [!] не записал {stem}_error.txt: {e}",
                      file=sys.stderr)

            readme_parts.extend([
                f"### Chunk {fc.index}/{item.chunks_total} — "
                f"kind={fc.kind}, chars {fc.start}..{fc.end}",
                "",
                f"**Error:** {fc.error}",
                "",
                f"**Source:** `{stem}_source.txt`",
                f"**Output:** "
                + (f"`{stem}_output.txt`" if fc.output is not None
                   else "_(model returned nothing)_"),
                f"**Error details:** `{stem}_error.txt`",
                "",
            ])

        readme_parts.extend([
            "## Что делать",
            "",
            f"Оригинальный текст проваленных чанков сохранён в "
            f"`{item.target_rel}` без изменений (для code-режима — "
            f"с маркерами `NOT PROCESSED`).",
            "",
            "Для ручной обработки проваленных фрагментов используйте "
            "guided mode с тем же prompt-файлом.",
            "",
        ])

        try:
            (target_dir / "README.md").write_text(
                "\n".join(readme_parts), encoding="utf-8",
            )
        except OSError as e:
            print(f"    [!] не записал README.md: {e}",
                  file=sys.stderr)
            return None

        return target_dir

    # ── preview ──────────────────────────────────────────────────────────

    def _render_preview(self) -> None:
        print("=" * 64)
        print(f" BATCH PREVIEW — {len(self.items)} items")
        print("=" * 64)
        print()

        any_partial = False
        any_merge_warn = False
        for i, item in enumerate(self.items, 1):
            marker, status = self._preview_row(item)
            if item.chunks_failed and item.status == "transformed":
                any_partial = True
            if any(issue.level == "warning"
                   for issue in item.validation_issues):
                any_merge_warn = True
            print(f"  [{marker}] {i:>2}. "
                  f"{item.source_rel:<40} {status}")

        print()
        will_write = sum(1 for it in self.items
                         if it.status == "transformed")
        failed = sum(1 for it in self.items if it.status == "failed")

        print(f"Будет записано: {will_write} из {len(self.items)}")
        if failed:
            print(f"Провалено при трансформации: {failed}")
        if any_merge_warn:
            print("[i] у части items есть warning'и от validate; "
                  "см. вывод выше")

        if any_partial:
            print()
            print("=" * 64)
            print(" ВНИМАНИЕ: некоторые файлы записаны ЧАСТИЧНО.")
            print(" Необработанные чанки помечены в файлах маркерами")
            print(" NOT PROCESSED и сохранены в <target>.failed/")
            print(" вместе с README.md. Прочитайте README перед")
            print(" использованием этих файлов.")
            print("=" * 64)

        print()
        print("=" * 64)
        print(f" Введите код для применения: {self.nonce}")
        print(f" '<номер> skip' — исключить файл из записи")
        print(" Любой другой ввод — отмена")
        print("=" * 64)

    @staticmethod
    def _preview_row(item: BatchItem) -> tuple[str, str]:
        if item.status == "transformed":
            if item.chunks_total:
                status = (
                    f"{item.chunks_total} chunks, "
                    f"{item.chunks_ok} ok"
                )
                if item.chunks_skipped:
                    status += f", {item.chunks_skipped} skipped"
                if item.chunks_failed:
                    status += f", {item.chunks_failed} FAILED"
                marker = "!" if item.chunks_failed else " "
            else:
                status = f"{len(item.content or ''):>6} chars"
                marker = " "
        elif item.status == "failed":
            status = f"FAILED: {item.error}"
            marker = "!"
        elif item.status == "skipped":
            status = "SKIPPED"
            marker = "x"
        else:
            status = "?"
            marker = "?"
        return marker, status

    # ── apply ────────────────────────────────────────────────────────────

    def _interactive_apply(self) -> None:
        while True:
            try:
                user_input = input("code> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                print("[CANCELLED] Batch отменён.")
                self.audit.write("batch_cancelled")
                return

            m = self._SKIP_RE.fullmatch(user_input)
            if m:
                idx = int(m.group(1)) - 1
                if not (0 <= idx < len(self.items)):
                    print(f"[!] нет item с номером {idx + 1}")
                    continue
                item = self.items[idx]
                if item.status == "failed":
                    print(f"[!] item {idx + 1} провален при "
                          f"трансформации, исключать нечего")
                    continue
                if item.status == "skipped":
                    item.status = "transformed"
                    print(f"[+] item {idx + 1} снова участвует в записи")
                else:
                    item.status = "skipped"
                    print(f"[+] item {idx + 1} исключён из записи")
                continue

            if secrets.compare_digest(user_input.upper(), self.nonce):
                self._apply_items()
                return

            print("[CANCELLED] Batch отменён.")
            self.audit.write("batch_cancelled")
            return

    def _apply_items(self) -> None:
        print()
        print("[*] Применяю batch...")
        written = 0
        errors = 0
        partial_count = 0

        for i, item in enumerate(self.items, 1):
            if item.status != "transformed" or item.content is None:
                continue

            target_abs, err = self.fs_guard.resolve_write(item.target_rel)
            if target_abs is None:
                print(f"  [!] {item.target_rel}: {err}")
                self.audit.write("batch_item_failed",
                                 idx=i, target=item.target_rel,
                                 reason=err)
                errors += 1
                continue

            try:
                target_abs.parent.mkdir(parents=True, exist_ok=True)
                target_abs.write_text(item.content, encoding="utf-8")
                print(f"  [+] {item.target_rel} "
                      f"({len(item.content)} chars)")
                written += 1
            except Exception as e:
                print(f"  [!] {item.target_rel}: "
                      f"{type(e).__name__}: {e}")
                self.audit.write("batch_item_failed",
                                 idx=i, target=item.target_rel,
                                 reason=str(e))
                errors += 1
                continue

            # Partial-режим: записать failed-чанки в <target>.failed/.
            if item.failed_chunks:
                failed_dir = self._write_failed_chunks(item)
                if failed_dir is not None:
                    partial_count += 1
                    print(f"  [!] {failed_dir.name}/ — "
                          f"{len(item.failed_chunks)} failed chunk(s)")
                    self.audit.write(
                        "batch_partial_written",
                        source=item.source_rel,
                        failed_dir=str(failed_dir.relative_to(
                            self.workspace)),
                        chunks_failed=len(item.failed_chunks),
                    )

        print()
        print(f"[OK] Записано: {written}, ошибок: {errors}")
        if partial_count:
            print(f"[!] Частично: {partial_count} файл(ов) с "
                  f"необработанными чанками. См. <target>.failed/.")
        self.audit.write(
            "batch_applied",
            items_total=len(self.items),
            items_written=written,
            items_errors=errors,
            items_partial=partial_count,
            nonce_used=True,
        )


# ══════════════════════════════════════════════════════════════════════════
# Структурная валидация (non-chunked)
# ══════════════════════════════════════════════════════════════════════════

def _check_structure(original: str, transformed: str,
                     source_rel: str) -> str | None:
    """Универсальная структурная проверка.

    Порядок — от точного к общему:
      1. Brace balance для C-like.
      2. AST для .py.
      3. Line preservation — для всех.
    """
    ext = Path(source_rel).suffix.lower()

    if ext in _C_LIKE_EXT:
        brace_err = _check_brace_balance(original, transformed)
        if brace_err:
            return brace_err

    if ext in _PY_EXT:
        ast_err = _check_python_structure(original, transformed)
        if ast_err:
            return ast_err

    return _check_line_preservation(original, transformed)


# ── Line preservation (универсальный) ─────────────────────────────────────

_IGNORED_LINE_PREFIXES = (
    "#", "//", "/*", "*/", "*", "--",
    '"""', "'''",
)

_PY_DEF_ONELINER_RE = re.compile(
    r"^((?:async\s+)?(?:def|class)\s+\w+\s*(?:\(.*\))?\s*:)\s+(.+)$"
)


def _is_ignorable_line(stripped: str) -> bool:
    if not stripped:
        return True
    for prefix in _IGNORED_LINE_PREFIXES:
        if stripped.startswith(prefix):
            return True
    return False


def _split_oneliner(line: str) -> list[str]:
    """Разбить однострочное определение Python на сигнатуру и тело.

    `def foo(): pass`       -> ['def foo():', 'pass']
    `def add(a, b): return a + b`
                            -> ['def add(a, b):', 'return a + b']
    """
    m = _PY_DEF_ONELINER_RE.match(line)
    if not m:
        return [line]
    return [m.group(1), m.group(2)]


def _significant_lines(text: str) -> list[str]:
    """Строки без пустых, комментариев и docstring-разделителей.

    Однострочные определения разбиваются на сигнатуру и тело через
    _split_oneliner. Это устраняет false positive: `def foo(): pass`
    при добавлении docstring становится `def foo():` + docstring +
    `pass`, и без разбиения выглядело бы как потеря строки.
    """
    result: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if _is_ignorable_line(stripped):
            continue
        result.extend(_split_oneliner(stripped))
    return result


def _normalize_line(line: str) -> str:
    """Убрать все пробельные символы — сравнение не зависит от
    форматирования и отступов."""
    return "".join(line.split())


def _check_line_preservation(original: str,
                              transformed: str) -> str | None:
    """Универсальная проверка: все значимые строки оригинала должны
    присутствовать в результате.
    """
    orig_lines = _significant_lines(original)
    if not orig_lines:
        return None

    new_set = {_normalize_line(line)
               for line in _significant_lines(transformed)}

    lost = [line for line in orig_lines
            if _normalize_line(line) not in new_set]

    if not lost:
        return None

    sample = lost[:3]
    sample_str = " | ".join(s[:50] for s in sample)
    return (f"{len(lost)} of {len(orig_lines)} significant lines lost "
            f"(sample: {sample_str})")


# ── Brace balance (для C-like) ────────────────────────────────────────────

def _count_braces(text: str) -> dict[str, int]:
    """Считает баланс {}, (), [] вне строк, комментариев и template-
    literals. Простой сканер без полноценного лексера."""
    counts = {"{": 0, "(": 0, "[": 0}
    in_string: str | None = None
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if in_string:
            if c == "\\":
                i += 2
                continue
            if c == in_string:
                in_string = None
            i += 1
            continue
        if c in ('"', "'", "`"):
            in_string = c
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c == "{":
            counts["{"] += 1
        elif c == "}":
            counts["{"] -= 1
        elif c == "(":
            counts["("] += 1
        elif c == ")":
            counts["("] -= 1
        elif c == "[":
            counts["["] += 1
        elif c == "]":
            counts["["] -= 1
        i += 1
    return counts


def _check_brace_balance(original: str,
                          transformed: str) -> str | None:
    """Проверяет, что баланс скобок сохранён."""
    orig_balance = _count_braces(original)
    new_balance = _count_braces(transformed)

    for key in ("{", "(", "["):
        if orig_balance[key] == 0 and new_balance[key] != 0:
            return (f"brace balance broken: {key!r} "
                    f"became {new_balance[key]:+d}")
    return None


# ── Python AST (для .py) ──────────────────────────────────────────────────

def _collect_defs(tree: ast.AST) -> dict[str, ast.AST]:
    """Все FunctionDef / AsyncFunctionDef / ClassDef по имени."""
    defs: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            defs[node.name] = node
    return defs


def _body_size(node: ast.AST) -> int:
    """Количество содержательных statement'ов в теле функции или
    класса. Docstring не считается содержательным."""
    if isinstance(node, ast.ClassDef):
        total = 0
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                total += _body_size(child)
        total += sum(1 for c in node.body
                     if not isinstance(c, (ast.FunctionDef,
                                            ast.AsyncFunctionDef)))
        return total
    body = getattr(node, "body", None)
    if body is None:
        return 0
    real = [
        stmt for stmt in body
        if not (isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str))
    ]
    return len(real)


def _check_python_structure(original: str,
                             transformed: str) -> str | None:
    """Python-специфичная проверка: определения сохранены, тела
    функций не обнулились и не уменьшились критично."""
    try:
        orig_tree = ast.parse(original)
    except SyntaxError:
        return None

    try:
        new_tree = ast.parse(transformed)
    except SyntaxError as e:
        return f"transformed file does not parse: {e.msg} line {e.lineno}"

    orig_defs = _collect_defs(orig_tree)
    new_defs = _collect_defs(new_tree)

    missing = set(orig_defs) - set(new_defs)
    if missing:
        names = ", ".join(sorted(missing))
        return f"definitions lost: {names}"

    for name, orig_node in orig_defs.items():
        orig_size = _body_size(orig_node)
        if orig_size == 0:
            continue
        new_node = new_defs.get(name)
        if new_node is None:
            continue
        new_size = _body_size(new_node)
        if new_size == 0:
            return f"body of '{name}' lost (was {orig_size} statements)"
        if orig_size >= 3 and new_size < orig_size * _BODY_LOSS_RATIO:
            return (f"body of '{name}' reduced "
                    f"({orig_size} -> {new_size} statements)")

    return None
