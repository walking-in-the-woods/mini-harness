"""
Batch-обработка коллекции файлов одним prompt-файлом.

Сценарий: «примени инструкцию X ко всем .py / .ts / .md файлам
директории» — один prompt, N источников, N результатов. Каждый
файл трансформируется независимо, без tools, с той же логикой
reasoning-retry, что и одиночный guided mode.

Дизайн:

* Директория + glob. Без рекурсии, без манифеста. Плоская структура.
* Один nonce на весь batch. Пользователь может `N skip` до ввода
  nonce, чтобы исключить отдельные items.
* Skip-and-continue. Провал одного item не прерывает batch.
* Target directory по умолчанию output/batch/<timestamp>/.

Структурная валидация — универсальная:

Задача — убедиться, что модель не потеряла часть исходного кода
при трансформации. Проверка не привязана к конкретному языку:

* .py — точная AST-проверка (definitions lost, body lost, reduced)
  плюс line preservation.
* C-like (.js/.ts/.go/.rs/.java/.c/.cpp/.cs/.php/.kt/.swift/.scala/
  .dart) — баланс скобок/кавычек плюс line preservation.
* Все остальные расширения — line preservation.

Line preservation — универсальный критерий, применяется ко ВСЕМ
расширениям, включая .py. Он не привязан к синтаксису языка и ловит
главный сценарий отказа: «модель добавила X, но выкинула часть
кода». Для .py он дополняет AST: AST видит определение и непустое
тело, но пропускает изменение сигнатуры
(`def add(a, b):` -> `def add(a: int, b: int) -> int:`) — line
preservation это ловит, потому что оригинальная строка `def add(a, b):`
не найдена в результате.

Однострочные определения:

`def foo(): pass` разбивается на две значимые строки —
`def foo():` и `pass`. Без этого любое добавление docstring с
переносом тела на новую строку ломало бы line preservation:
оригинальная строка `def foo(): pass` исчезает из результата,
хотя семантически ничего не потеряно, изменилось только
форматирование. Разбиение через _split_oneliner в _significant_lines.

При структурной ошибке — retry с коротким хинтом в user message
(_STRUCTURAL_RETRY_HINT). Хинт в user, а не в system: system уже
занят prompt-файлом, а длинный составной system модель читает хуже.
"""

from __future__ import annotations

import ast
import logging
import re
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from harness.agent import HarnessAgent
from harness.audit import AuditLog
from harness.fs_guard import FileSystemGuard


log = logging.getLogger(__name__)


# Предупреждение при большом количестве файлов.
_BIG_BATCH_WARN = 20

# Порог потери statement'ов в теле функции (для Python AST).
_BODY_LOSS_RATIO = 0.5

# Количество попыток трансформации. Вторая — только при структурной
# ошибке, с хинтом в user message. Ошибки модели (пустой ответ,
# reasoning) не ретраятся.
_MAX_TRANSFORM_ATTEMPTS = 2

# Расширения, для которых применяется точная AST-проверка.
_PY_EXT = {".py", ".pyw"}

# Расширения C-like языков — дополнительно проверяется баланс
# скобок и кавычек.
_C_LIKE_EXT = {
    ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".java", ".c", ".h", ".cpp", ".hpp", ".cc", ".hh",
    ".cs", ".go", ".rs", ".php", ".kt", ".kts", ".swift",
    ".scala", ".dart",
}

# Хинт для структурного retry. Идёт в начало user message —
# короткий, императивный, без markdown.
_STRUCTURAL_RETRY_HINT = (
    "ВНИМАНИЕ: предыдущий ответ потерял часть исходного кода. "
    "В этот раз выведи файл ЦЕЛИКОМ — каждую строку исходника без "
    "изменений, включая тела всех функций. Не добавляй импорты, "
    "не меняй сигнатуры, не используй type hints. Только вставь "
    "новый текст согласно инструкции, всё остальное оставь как было."
)


@dataclass
class BatchItem:
    source_rel: str
    target_rel: str
    status: str = "pending"  # pending / transformed / failed / skipped
    content: str | None = None
    error: str | None = None
    duration_s: float = 0.0


class BatchRunner:
    """Один прогон batch-обработки."""

    _SKIP_RE = re.compile(r"(\d+)\s+skip", re.IGNORECASE)

    def __init__(self, agent: HarnessAgent, fs_guard: FileSystemGuard,
                 workspace: Path, audit: AuditLog, *,
                 target_dir: str | None = None):
        self.agent = agent
        self.fs_guard = fs_guard
        self.workspace = workspace
        self.audit = audit
        self.target_dir = target_dir
        self.nonce = secrets.token_hex(4).upper()
        self.items: list[BatchItem] = []

    # ------------------------ entry point ---------------------------------

    def run(self, source_dir: str, glob_pattern: str,
            prompt_path: str) -> None:
        source_dir = source_dir.rstrip("/")

        prompt_content, err = self._read_workspace_file(prompt_path)
        if err:
            print(f"[!] не удалось прочитать prompt-файл "
                  f"{prompt_path}: {err}")
            return

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
            ))

        print(f"[*] batch: {len(self.items)} файлов, "
              f"prompt: {prompt_path}, "
              f"target: {target_dir}/", file=sys.stderr)

        if len(self.items) >= _BIG_BATCH_WARN:
            est = len(self.items) * 1.5
            print(f"[i] {len(self.items)} файлов — ориентировочно "
                  f"{est:.0f} минут. Ctrl+C для отмены.",
                  file=sys.stderr)

        print(file=sys.stderr)

        for i, item in enumerate(self.items, 1):
            print(f"[*] [{i}/{len(self.items)}] {item.source_rel}",
                  file=sys.stderr, flush=True)
            self._process_item(item, prompt_content)
            if item.status == "transformed":
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
            total=len(self.items),
            transformed=sum(1 for it in self.items
                            if it.status == "transformed"),
            failed=sum(1 for it in self.items if it.status == "failed"),
        )

        print()
        self._render_preview()

        self._interactive_apply()

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

    def _process_item(self, item: BatchItem, prompt_content: str) -> None:
        """Трансформация одного файла с retry при структурной ошибке."""
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

    def _render_preview(self) -> None:
        print("=" * 64)
        print(f" BATCH PREVIEW — {len(self.items)} items")
        print("=" * 64)
        print()

        for i, item in enumerate(self.items, 1):
            if item.status == "transformed":
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

            print(f"  [{marker}] {i:>2}. "
                  f"{item.source_rel:<40} {status}")

        print()
        will_write = sum(1 for it in self.items
                         if it.status == "transformed")
        failed = sum(1 for it in self.items if it.status == "failed")

        print(f"Будет записано: {will_write} из {len(self.items)}")
        if failed:
            print(f"Провалено при трансформации: {failed}")

        print()
        print("=" * 64)
        print(f" Введите код для применения: {self.nonce}")
        print(f" '<номер> skip' — исключить файл из записи")
        print(" Любой другой ввод — отмена")
        print("=" * 64)

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

        print()
        print(f"[OK] Записано: {written}, ошибок: {errors}")
        self.audit.write(
            "batch_applied",
            items_total=len(self.items),
            items_written=written,
            items_errors=errors,
            nonce_used=True,
        )


# ══════════════════════════════════════════════════════════════════════════
# Структурная валидация
#
# Диспетчер _check_structure выбирает стратегию по расширению:
#   * .py           — AST + line preservation
#   * C-like        — brace balance + line preservation
#   * всё остальное — line preservation
#
# Line preservation применяется ко ВСЕМ расширениям, включая .py.
# Это универсальный fallback, который не знает про синтаксис языка
# и ловит главный сценарий отказа: «модель добавила X, но потеряла
# часть исходного кода».
# ══════════════════════════════════════════════════════════════════════════

def _check_structure(original: str, transformed: str,
                     source_rel: str) -> str | None:
    """Универсальная структурная проверка.

    Порядок — от точного к общему:

      1. Brace balance для C-like: самая быстрая диагностика на
         явно сломанном файле.

      2. AST для .py: точная диагностика на потерю определения
         или тела функции.

      3. Line preservation — универсальный fallback для ВСЕХ
         расширений, включая .py. Ловит изменение сигнатуры
         и потерю любой значимой строки.

    Добавленные строки (import, комментарии, docstrings) не
    считаются ошибкой: любая «add X» задача добавляет текст.
    Проверяется только сохранность исходных строк.
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

# Однострочное определение Python/класса: `def foo(): pass`,
# `class Foo: pass`, `def add(a, b): return a + b`.
# Используется в _split_oneliner, чтобы разбить такую строку на
# сигнатуру и тело. Иначе добавление docstring с переносом тела
# на отдельную строку считалось бы потерей `def foo(): pass`.
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
    `class Foo: pass`       -> ['class Foo:', 'pass']
    `def foo():`            -> ['def foo():']  (нет тела на этой строке)

    Для не-Python строк — возвращает [line] как есть. Регулярка
    матчит только `def`/`class`/`async def`.
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
    `pass`, и без разбиения выглядело бы как потеря строки
    `def foo(): pass`.
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

    Сравнение — после удаления всех пробелов. Добавление новых
    строк (docstrings, комментарии) не мешает. Потеря хотя бы
    одной значимой строки — провал.
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
    """Проверяет, что баланс скобок сохранён. Исходник должен быть
    сбалансирован; если результат несбалансирован — файл сломан.
    """
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
