"""
Файловый шлюз.

* NFKC-нормализация (гомоглифы).
* os.path.realpath (symlink escape).
* whitelist → blacklist последовательно.
* Собственная glob→regex с поддержкой **.
* Разделение read/write: расширения и writable — только к записи.
* ext_allow_paths — исключения из WRITE_EXT_BLOCK для конкретных
  директорий (например, output/code/** разрешает .py).

Семантика списков:
* whitelist: пустой = ничего не разрешено. Отсутствие ключа → ["**"].
* blacklist: пустой = ничего не запрещено.
* writable: пустой = запись везде запрещена.
* ext_allow_paths: пустой или отсутствует = все WRITE_EXT_BLOCK
  запрещены везде, как раньше.

Порядок проверок в check_write:
  1. whitelist / blacklist (через _match_lists)
  2. writable: путь вообще разрешён для записи?
  3. extension: если расширение в WRITE_EXT_BLOCK, проверить
     ext_allow_paths.

Порядок 2 → 3 выбран так, чтобы диагностика была точной: если
путь вне writable, пользователь получает сообщение про writable,
а не про extension. Раньше порядок был обратным, и сообщение
про extension сбивало с толку, когда реальная причина — writable.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Optional


def _glob_to_regex(pattern: str) -> re.Pattern:
    """*  -> [^/]*; ?  -> [^/]; ** -> .*; **/ -> (?:.*/)?"""
    parts: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                if i + 2 < n and pattern[i + 2] == "/":
                    parts.append(r"(?:.*/)?")
                    i += 3
                    continue
                parts.append(r".*")
                i += 2
                continue
            parts.append(r"[^/]*")
            i += 1
            continue
        if c == "?":
            parts.append(r"[^/]")
            i += 1
            continue
        parts.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(parts) + "$")


class FileSystemGuard:
    WRITE_EXT_BLOCK = {
        ".sh", ".bash", ".zsh", ".fish", ".ksh",
        ".exe", ".bat", ".cmd", ".com",
        ".ps1", ".vbs", ".wsf", ".scr",
        ".so", ".dll", ".dylib",
        ".php", ".jsp", ".asp", ".aspx", ".cgi", ".pl",
        ".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
        ".rb", ".go", ".rs", ".lua", ".tcl", ".r",
        ".whl", ".egg",
    }

    def __init__(self, policy: dict):
        self.root = Path(policy["root"]).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise RuntimeError(f"workspace root does not exist: {self.root}")

        # policy.get("whitelist", ["**"]) — НЕ `or ["**"]`.
        # Пустой список = «ничего не разрешено».
        self.whitelist: list[str] = policy.get("whitelist", ["**"])
        self.blacklist: list[str] = policy.get("blacklist", [])
        self.writable: list[str] = policy.get("writable", [])
        # Исключения из WRITE_EXT_BLOCK. Пустой список или
        # отсутствие ключа = исключений нет.
        self.ext_allow_paths: list[str] = policy.get("ext_allow_paths", [])

        self._wl = [_glob_to_regex(p) for p in self.whitelist]
        self._bl = [_glob_to_regex(p) for p in self.blacklist]
        self._wr = [_glob_to_regex(p) for p in self.writable]
        self._ext_allow = [_glob_to_regex(p) for p in self.ext_allow_paths]

    # -------------------------- public API ---------------------------------

    def check_read(self, rel: str) -> tuple[bool, str]:
        p, err = self._resolve(rel)
        if p is None:
            return False, err
        return self._match_lists(p.relative_to(self.root).as_posix())

    def check_write(self, rel: str) -> tuple[bool, str]:
        p, err = self._resolve(rel)
        if p is None:
            return False, err
        rel_posix = p.relative_to(self.root).as_posix()

        # 1. whitelist / blacklist
        ok, err = self._match_lists(rel_posix)
        if not ok:
            return False, err

        # 2. writable — путь вообще разрешён для записи?
        if not self._wr:
            return False, "writes are disabled (empty 'writable' list)"
        if not any(r.match(rel_posix) for r in self._wr):
            return False, f"path not in writable list: {rel_posix}"

        # 3. extension — блокировка .py/.sh/... с исключением
        #    по ext_allow_paths.
        ext = p.suffix.lower()
        if ext in self.WRITE_EXT_BLOCK:
            ext_allowed = any(r.match(rel_posix) for r in self._ext_allow)
            if not ext_allowed:
                return False, (
                    f"extension {ext!r} blocked for writes "
                    f"(save as .txt or use a directory listed "
                    f"in ext_allow_paths)"
                )

        return True, "OK"

    def resolve_read(self, rel: str) -> tuple[Optional[Path], str]:
        p, err = self._resolve(rel)
        if p is None:
            return None, err
        rel_posix = p.relative_to(self.root).as_posix()
        ok, err = self._match_lists(rel_posix)
        if not ok:
            return None, err
        return p, ""

    def resolve_write(self, rel: str) -> tuple[Optional[Path], str]:
        ok, err = self.check_write(rel)
        if not ok:
            return None, err
        return self._resolve(rel)

    # -------------------------- internals ----------------------------------

    def _resolve(self, rel: str) -> tuple[Optional[Path], str]:
        if not isinstance(rel, str) or not rel:
            return None, "empty path"

        # NFKC нормализация: ловит гомоглифы и совместимые формы.
        rel = unicodedata.normalize("NFKC", rel)

        if "\x00" in rel:
            return None, "null byte in path"

        posix = rel.replace("\\", "/")
        try:
            parts = PurePosixPath(posix).parts
        except Exception as e:
            return None, f"invalid path: {e}"

        if any(p == ".." for p in parts):
            return None, "path traversal ('..') forbidden"

        candidate = self.root / posix
        # realpath разворачивает ВСЕ symlink'и в цепочке компонентов.
        try:
            real = Path(os.path.realpath(candidate))
        except OSError as e:
            return None, f"realpath failed: {e}"

        try:
            real.relative_to(self.root)
        except ValueError:
            return None, f"path escapes workspace root: {rel}"

        return real, ""

    def _match_lists(self, rel_posix: str) -> tuple[bool, str]:
        # Пустой whitelist → _wl = [] → any([]) = False → всё запрещено.
        if not any(r.match(rel_posix) for r in self._wl):
            return False, f"not in whitelist: {rel_posix}"
        if any(r.match(rel_posix) for r in self._bl):
            return False, f"in blacklist: {rel_posix}"
        return True, "OK"
