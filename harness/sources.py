"""
Внешние источники для чтения (за пределами workspace).

Каждый source — read-only корень за пределами workspace, разрешённый
пользователем явно в workspace/sources.yaml. Модель НЕ имеет прямого
доступа к этим путям — harness сам читает источник и складывает
результат в workspace/input/, откуда модель читает обычным read_file.

Безопасность:
* realpath внутри source (symlink escape блокируется)
* детектор циклов: если realpath элемента уже посещён в текущем
  обходе — пропускаем (защита от a/b -> a)
* чёрный список переиспользуется из config.yaml:fs.blacklist
* собственный ignore-list источника (dirs/files) — дополнительно
* лимиты: max_entries (для /files), max_tree_entries (для /tree,
  0 = без ограничений), max_files, max_file_bytes, max_total_bytes
* бинарные файлы пропускаются: null-байт в первых 8 КБ или
  UnicodeDecodeError
* сами внешние файлы не модифицируются ни в каком режиме

Про глубину: max_depth по умолчанию 0 = без ограничений. Глубина
в реальных проектах непредсказуема (3 уровня в одном, 12 в другом),
а единственная реальная причина её ограничивать — symlink-циклы —
закрыта детектором visited. Для патологического случая `root: /`
пользователь может задать max_depth явно или добавить ignore-паттерны.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import yaml

from harness.fs_guard import _glob_to_regex


class SourceError(Exception):
    """Ошибка конфигурации или доступа к источнику."""


@dataclass(frozen=True)
class Source:
    name: str
    root: Path
    description: str
    max_depth: int            # 0 = без ограничений
    max_entries: int          # для /files (список путей с размерами)
    max_tree_entries: int     # для /tree; 0 = без ограничений
    max_files: int
    max_file_bytes: int
    max_total_bytes: int
    ignore_dirs: tuple[str, ...]
    ignore_files: tuple[str, ...]

    @property
    def exists(self) -> bool:
        return self.root.is_dir()


@dataclass
class SourceRegistry:
    workspace_dir: Path
    global_blacklist: tuple[str, ...]      # из config.yaml:fs.blacklist
    _sources: dict[str, Source] = field(default_factory=dict)
    _config_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self._config_path = self.workspace_dir / "sources.yaml"
        self.reload()

    # -------------------------- load / reload -----------------------------

    def reload(self) -> None:
        self._sources.clear()
        if not self._config_path.is_file():
            return
        try:
            raw = yaml.safe_load(
                self._config_path.read_text(encoding="utf-8")
            ) or {}
        except yaml.YAMLError as e:
            raise SourceError(f"sources.yaml: {e}")
        if not isinstance(raw, dict):
            raise SourceError("sources.yaml: top level must be a mapping")

        defaults = raw.get("defaults") or {}
        if not isinstance(defaults, dict):
            raise SourceError("sources.yaml: 'defaults' must be a mapping")

        global_defaults = {
            "max_depth": int(defaults.get("max_depth", 0)),
            "max_entries": int(defaults.get("max_entries", 500)),
            "max_tree_entries": int(defaults.get("max_tree_entries", 0)),
            "max_files": int(defaults.get("max_files", 200)),
            "max_file_bytes": int(defaults.get("max_file_bytes", 50_000)),
            "max_total_bytes": int(defaults.get("max_total_bytes", 200_000)),
        }
        ignore = defaults.get("ignore") or {}
        default_ignore_dirs = tuple(
            ignore.get("dirs") or []
        ) if isinstance(ignore, dict) else ()
        default_ignore_files = tuple(
            ignore.get("files") or []
        ) if isinstance(ignore, dict) else ()

        sources_raw = raw.get("sources") or []
        if not isinstance(sources_raw, list):
            raise SourceError("sources.yaml: 'sources' must be a list")

        for entry in sources_raw:
            source = self._parse_source(
                entry, global_defaults,
                default_ignore_dirs, default_ignore_files,
            )
            if source.name in self._sources:
                raise SourceError(
                    f"duplicate source name: {source.name!r}"
                )
            self._sources[source.name] = source

    def _parse_source(self, entry: dict, global_defaults: dict,
                      default_ignore_dirs: tuple[str, ...],
                      default_ignore_files: tuple[str, ...]) -> Source:
        if not isinstance(entry, dict):
            raise SourceError("each source entry must be a mapping")

        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise SourceError(f"source name must be a non-empty string "
                              f"(got {name!r})")
        if "/" in name or name in (".", ".."):
            raise SourceError(f"source name {name!r} must not contain '/'")

        root_raw = entry.get("root")
        if not isinstance(root_raw, str) or not root_raw:
            raise SourceError(f"source {name!r}: 'root' is required")
        # expanduser — пользователь может писать ~/...
        root = Path(os.path.expanduser(root_raw)).resolve()

        per_source_ignore = entry.get("ignore") or {}
        ignore_dirs = default_ignore_dirs
        ignore_files = default_ignore_files
        if isinstance(per_source_ignore, dict):
            if "dirs" in per_source_ignore:
                ignore_dirs = tuple(per_source_ignore["dirs"] or [])
            if "files" in per_source_ignore:
                ignore_files = tuple(per_source_ignore["files"] or [])

        return Source(
            name=name,
            root=root,
            description=str(entry.get("description", "")),
            max_depth=int(entry.get("max_depth",
                                     global_defaults["max_depth"])),
            max_entries=int(entry.get("max_entries",
                                       global_defaults["max_entries"])),
            max_tree_entries=int(entry.get(
                "max_tree_entries",
                global_defaults["max_tree_entries"],
            )),
            max_files=int(entry.get("max_files",
                                     global_defaults["max_files"])),
            max_file_bytes=int(entry.get("max_file_bytes",
                                          global_defaults["max_file_bytes"])),
            max_total_bytes=int(entry.get("max_total_bytes",
                                           global_defaults["max_total_bytes"])),
            ignore_dirs=ignore_dirs,
            ignore_files=ignore_files,
        )

    # -------------------------- public API --------------------------------

    def list(self) -> list[Source]:
        return sorted(self._sources.values(), key=lambda s: s.name)

    def get(self, name: str) -> Source:
        s = self._sources.get(name)
        if s is None:
            available = ", ".join(sorted(self._sources)) or "(нет)"
            raise SourceError(
                f"unknown source {name!r}. Available: {available}"
            )
        if not s.exists:
            raise SourceError(
                f"source {name!r} root is not a directory: {s.root}"
            )
        return s

    # -------------------------- tree --------------------------------------

    def build_tree(self, name: str, subpath: str = "") -> tuple[str, int]:
        """Возвращает (markdown, entries_count).

        Ограничение max_tree_entries по умолчанию 0 = без ограничений.
        Дерево — только пути, без содержимого. Даже для очень большого
        проекта это компактный файл.

        Глубина тоже не ограничена по умолчанию. От symlink-циклов
        защищает детектор visited в _walk.
        """
        source = self.get(name)
        base = self._resolve_subpath(source, subpath)
        entries: list[tuple[str, bool, int]] = []
        truncated = False
        limit = source.max_tree_entries

        for rel, is_dir, depth in self._walk(source, base):
            if limit > 0 and len(entries) >= limit:
                truncated = True
                break
            entries.append((rel, is_dir, depth))

        md = self._render_tree(source, subpath, entries, truncated)
        return md, len(entries)

    # -------------------------- dump --------------------------------------

    def collect_dump(self, name: str, subpath: str = "") -> tuple[str, dict]:
        """Возвращает (markdown, stats)."""
        source = self.get(name)
        base = self._resolve_subpath(source, subpath)

        files: list[tuple[str, int]] = []   # (rel_path, size)
        stats = {
            "files_included": 0,
            "files_skipped_size": 0,
            "files_skipped_binary": 0,
            "bytes_total": 0,
            "truncated": False,
        }

        for rel, is_dir, depth in self._walk(source, base):
            if is_dir:
                continue
            full = source.root / rel
            try:
                size = full.stat().st_size
            except OSError:
                continue
            files.append((rel, size))

        # Выбираем файлы по порядку, пока вписываемся в лимиты.
        selected: list[tuple[str, str]] = []   # (rel, content)
        for rel, size in files:
            if len(selected) >= source.max_files:
                stats["truncated"] = True
                break
            if size > source.max_file_bytes:
                stats["files_skipped_size"] += 1
                continue
            if stats["bytes_total"] + size > source.max_total_bytes:
                stats["truncated"] = True
                break
            content = self._read_text(source.root / rel)
            if content is None:
                stats["files_skipped_binary"] += 1
                continue
            selected.append((rel, content))
            stats["bytes_total"] += size

        stats["files_included"] = len(selected)
        md = self._render_dump(source, subpath, selected, stats)
        return md, stats

    # -------------------------- files list --------------------------------

    def build_file_list(self, name: str, subpath: str = "") -> tuple[str, int]:
        """Плоский список путей с размерами. Дерево — отдельно."""
        source = self.get(name)
        base = self._resolve_subpath(source, subpath)
        lines: list[str] = []
        truncated = False

        for rel, is_dir, depth in self._walk(source, base):
            if is_dir:
                continue
            if len(lines) >= source.max_entries:
                truncated = True
                break
            full = source.root / rel
            try:
                size = full.stat().st_size
            except OSError:
                continue
            lines.append(f"{size:>10}  {rel}")

        if truncated:
            lines.append(f"... (обрезано на {source.max_entries} записей)")
        return "\n".join(lines), len(lines)

    # -------------------------- internals ---------------------------------

    def _resolve_subpath(self, source: Source, subpath: str) -> Path:
        """subpath внутри source.root. realpath-проверка."""
        if not subpath or subpath == ".":
            return source.root
        # Убираем ведущий слэш и ..
        clean = subpath.strip().lstrip("/")
        if any(p == ".." for p in Path(clean).parts):
            raise SourceError(f"subpath traversal in {subpath!r}")
        candidate = (source.root / clean).resolve()
        try:
            candidate.relative_to(source.root)
        except ValueError:
            raise SourceError(
                f"subpath {subpath!r} escapes source root"
            )
        if not candidate.exists():
            raise SourceError(
                f"subpath not found: {subpath!r} in source {source.name!r}"
            )
        return candidate

    def _walk(self, source: Source,
              base: Path) -> Iterator[tuple[str, bool, int]]:
        """Yields (rel_to_root: posix, is_dir, depth).

        Детектор циклов: если realpath элемента уже посещён в текущем
        обходе, элемент пропускается. Это ловит symlink loops
        (a/b -> a) и любые алиасы внутри source.

        max_depth: если > 0, обход не углубляется дальше. По умолчанию
        0 = без ограничений. Для патологического случая `root: /`
        пользователь может задать явно или использовать ignore.
        """
        yield from self._walk_inner(
            source, base, depth=0,
            ignore_matcher=self._make_matcher(source),
            visited=set(),
        )

    def _walk_inner(self, source: Source, base: Path, depth: int,
                    ignore_matcher,
                    visited: set[Path]) -> Iterator[tuple[str, bool, int]]:
        if source.max_depth > 0 and depth > source.max_depth:
            return

        # Регистрируем текущий base в visited по realpath. Это ловит
        # цикл a/b -> a, где обход вернётся в уже пройденную точку.
        try:
            base_real = base.resolve()
        except OSError:
            return
        if base_real in visited:
            return
        visited.add(base_real)

        try:
            items = sorted(base.iterdir(), key=lambda p: p.name)
        except (PermissionError, OSError):
            return

        for item in items:
            rel = item.relative_to(source.root).as_posix()
            is_dir = item.is_dir()

            if ignore_matcher(rel, is_dir):
                continue

            # realpath внутри source.root: symlink escape и broken link
            try:
                real = item.resolve()
                real.relative_to(source.root)
            except (ValueError, OSError):
                continue

            yield rel, is_dir, depth

            if is_dir:
                # visited передаётся вниз — цикл через symlink
                # поймается при попытке войти в уже пройденный
                # каталог.
                yield from self._walk_inner(
                    source, item, depth + 1, ignore_matcher, visited,
                )

    def _make_matcher(self, source: Source):
        """Единый предикат ignore: global blacklist + source-specific."""
        blacklist_res = [_glob_to_regex(p) for p in self.global_blacklist]
        dirs_res = [_glob_to_regex(p) for p in source.ignore_dirs]
        files_res = [_glob_to_regex(p) for p in source.ignore_files]

        def match(rel_posix: str, is_dir: bool) -> bool:
            for r in blacklist_res:
                if r.match(rel_posix):
                    return True
            if is_dir:
                for r in dirs_res:
                    if r.match(rel_posix):
                        return True
                for r in dirs_res:
                    if r.match(rel_posix + "/x"):
                        return True
            else:
                for r in files_res:
                    if r.match(rel_posix):
                        return True
            return False

        return match

    @staticmethod
    def _read_text(path: Path) -> str | None:
        """Читает как UTF-8. None — если бинарный или не читается.

        Бинарность определяется по двум признакам:

        1. Наличие \\x00 в первых 8192 байтах. Это эвристика,
           используемая git/grep/file: null-байт не встречается в
           осмысленном UTF-8 тексте, но характерен для PNG, ELF, PDF,
           сжатых данных и т. п. Проверять decode-ошибку недостаточно:
           \\x00-\\x1f валидны как однобайтовые UTF-8 control-символы,
           поэтому файл типа b"\\x00\\x01\\x02\\x03"*N успешно
           декодируется и попадает в dump мусором.

        2. UnicodeDecodeError при декодировании остального содержимого.
        """
        try:
            raw = path.read_bytes()
        except (PermissionError, IsADirectoryError, OSError):
            return None
        if b"\x00" in raw[:8192]:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None

    # -------------------------- rendering ---------------------------------

    @staticmethod
    def _render_tree(source: Source, subpath: str,
                     entries: list[tuple[str, bool, int]],
                     truncated: bool) -> str:
        header = source.root.as_posix()
        if subpath:
            header = f"{header}/{subpath.strip().lstrip('/')}"
        lines = ["```markdown-tree", f"{header}/"]
        for rel, is_dir, depth in entries:
            indent = "    " * depth
            name = rel.split("/")[-1]
            suffix = "/" if is_dir else ""
            lines.append(f"{indent}{name}{suffix}")
        if truncated:
            lines.append(f"... (обрезано на {source.max_tree_entries} записей)")
        lines.append("```")
        return "\n".join(lines)

    @staticmethod
    def _render_dump(source: Source, subpath: str,
                     selected: list[tuple[str, str]],
                     stats: dict) -> str:
        header = source.root.as_posix()
        if subpath:
            header = f"{header}/{subpath.strip().lstrip('/')}"

        out = [
            f"# Dump: {source.name}",
            "",
            f"- root: `{header}`",
            f"- files included: {stats['files_included']}",
        ]
        if stats["files_skipped_size"]:
            out.append(
                f"- files skipped (too large): "
                f"{stats['files_skipped_size']}"
            )
        if stats["files_skipped_binary"]:
            out.append(
                f"- files skipped (binary/unreadable): "
                f"{stats['files_skipped_binary']}"
            )
        if stats["truncated"]:
            out.append(
                "- **truncated**: достигнут лимит "
                "max_files или max_total_bytes"
            )
        out.append("")

        for rel, content in selected:
            ext = Path(rel).suffix.lower()
            lang = ext[1:] if ext.startswith(".") else ext
            # Для .md в исходниках используем ````` — чтобы вложенные
            # ``` в тексте не ломали разметку.
            fence = "````" if ext in (".md", ".mdx") else "```"
            out.append(f"## {rel}")
            if lang:
                out.append(f"{fence}{lang}")
            else:
                out.append(fence)
            out.append(content)
            out.append(fence)
            out.append("")

        return "\n".join(out)
