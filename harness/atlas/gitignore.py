"""Минимальный парсер .gitignore → regex. Без внешних зависимостей."""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath


def _translate_pattern(pattern: str) -> str:
    pattern = pattern.rstrip("/")
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                out.append(".*")
                i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = pattern.find("]", i + 1)
            if j == -1:
                out.append(re.escape(c))
                i += 1
            else:
                out.append(pattern[i : j + 1])
                i = j + 1
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


class GitIgnoreMatcher:
    """Загружает .gitignore из корня проекта и проверяет пути."""

    def __init__(self, root: Path, extra_ignore_dirs: tuple[str, ...] = ()) -> None:
        self.root = root
        self._rules: list[tuple[re.Pattern[str], bool]] = []
        self._extra_dirs = set(extra_ignore_dirs)
        self._load(root)

    def _load(self, root: Path) -> None:
        gi = root / ".gitignore"
        if not gi.is_file():
            return
        for line in gi.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            negate = False
            if line.startswith("!"):
                negate = True
                line = line[1:].strip()
                if not line:
                    continue
            if line.startswith("/"):
                line = line[1:]
            regex = _translate_pattern(line)
            self._rules.append(
                (re.compile(r"(?:^|/)" + regex + r"(?:$|/)"), negate)
            )

    def is_ignored(self, rel_path: str) -> bool:
        posix = PurePosixPath(rel_path).as_posix()
        parts = posix.split("/")
        if any(p in self._extra_dirs for p in parts):
            return True
        ignored = False
        for regex, negate in self._rules:
            if regex.search(posix):
                ignored = not negate
        return ignored
