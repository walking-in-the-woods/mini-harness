"""Одноразовое подтверждение записи: nonce, diff, атомарная запись."""

from __future__ import annotations

import difflib
import os
import secrets
import tempfile
from pathlib import Path

from harness.fs_guard import FileSystemGuard


class ConfirmSession:

    def __init__(self, workspace: Path, guard: FileSystemGuard):
        self.workspace = workspace
        self.guard = guard
        self.nonce = secrets.token_hex(4).upper()

    def render_preview(self, writes: list[dict]) -> str:
        out: list[str] = []
        out.append("=" * 64)
        out.append(" PENDING WRITES — ПРОВЕРЬТЕ ПЕРЕД ПОДТВЕРЖДЕНИЕМ")
        out.append("=" * 64)

        for w in writes:
            path = w["path"]
            target = self.workspace / path
            out.append("")
            out.append(f"--- {path} ---")

            if target.exists() and target.is_file():
                try:
                    old = target.read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines()
                except Exception:
                    old = ["<unreadable>"]
                new = w["content"].splitlines()
                diff = list(difflib.unified_diff(
                    old, new,
                    fromfile="current", tofile="proposed",
                    lineterm="",
                ))
                out.extend(diff if diff else ["(no textual change)"])
            else:
                size = len(w["content"].encode("utf-8"))
                out.append(f"[NEW FILE, {size} bytes]")
                preview = w["content"]
                if len(preview) > 4000:
                    preview = preview[:4000] + "\n... [truncated for display]"
                out.append(preview)

        out.append("")
        out.append("=" * 64)
        out.append(f" Введите код для ПРИМЕНЕНИЯ: {self.nonce}")
        out.append(" Любой другой ввод -> ОТМЕНА")
        out.append("=" * 64)
        return "\n".join(out)

    def apply(self, writes: list[dict], user_code: str) -> list[str]:
        if not secrets.compare_digest(
            (user_code or "").strip().upper(), self.nonce
        ):
            return ["[CANCELLED] Код не совпал. Файлы не записаны."]

        results: list[str] = []
        for w in writes:
            path = w["path"]
            content = w["content"]

            ok, err = self.guard.check_write(path)
            if not ok:
                results.append(f"[DENIED] {path}: {err}")
                continue

            target, err = self.guard.resolve_write(path)
            if target is None:
                results.append(f"[DENIED] {path}: {err}")
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            fd = None
            tmp_path = None
            try:
                fd, tmp_path = tempfile.mkstemp(
                    dir=str(target.parent),
                    prefix=".harness-tmp-",
                    suffix=".part",
                )
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fd = None
                    fh.write(content)
                os.chmod(tmp_path, 0o644)
                os.replace(tmp_path, target)
                results.append(
                    f"[OK] {path} ({len(content.encode('utf-8'))} bytes)"
                )
            except Exception as e:
                results.append(f"[ERROR] {path}: {e}")
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
        return results
