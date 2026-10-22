"""Обход дерева проекта с учётом .gitignore и ignore_dirs."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from harness.atlas.config import AtlasConfig
from harness.atlas.gitignore import GitIgnoreMatcher


@dataclass(frozen=True)
class FileEntry:
    rel_path: str          # posix-путь относительно корня проекта
    abs_path: Path
    sha256: str
    size_bytes: int


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class Walker:
    """Итератор по исходным файлам проекта.

    Корень проекта — `workspace_root` из AtlasConfig. Все относительные
    пути — posix-строки относительно этого корня (это совпадает с тем,
    как пути выглядят в nodes.path).
    """

    def __init__(self, root: Path, config: AtlasConfig) -> None:
        self.root = root.resolve()
        self.config = config
        self.matcher = GitIgnoreMatcher(
            self.root, extra_ignore_dirs=config.ignore_dirs
        )

    def iter_files(self) -> Iterator[FileEntry]:
        exts = self.config.python_extensions
        stack: list[Path] = [self.root]
        while stack:
            current = stack.pop()
            try:
                children = sorted(current.iterdir(), key=lambda p: p.name)
            except (PermissionError, OSError):
                continue
            for child in children:
                try:
                    rel = child.relative_to(self.root).as_posix()
                except ValueError:
                    continue
                if self.matcher.is_ignored(rel):
                    continue
                if child.is_dir():
                    stack.append(child)
                    continue
                if child.suffix.lower() not in exts:
                    continue
                try:
                    size = child.stat().st_size
                except OSError:
                    continue
                yield FileEntry(
                    rel_path=rel,
                    abs_path=child,
                    sha256=sha256_of_file(child),
                    size_bytes=size,
                )
