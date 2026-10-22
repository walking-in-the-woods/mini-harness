"""CLI для управления Atlas без запуска REPL.

Используется из scripts/atlas-setup.sh. Работает независимо от
бэкенда инференса: нужен только Python и стандартная библиотека
плюс PyYAML (он уже в requirements.txt).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from harness.atlas.config import AtlasConfig
from harness.atlas.facade import AtlasFacade


def _load_atlas_config(base_dir: Path) -> AtlasConfig:
    cfg_path = base_dir / "config.yaml"
    if not cfg_path.is_file():
        print(f"[fatal] config not found: {cfg_path}", file=sys.stderr)
        sys.exit(1)
    with cfg_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return AtlasConfig.from_dict(raw.get("atlas") or {})


def _make_facade(base_dir: Path) -> AtlasFacade:
    cfg = _load_atlas_config(base_dir)
    return AtlasFacade(cfg, base_dir)


def cmd_rebuild(base_dir: Path) -> int:
    f = _make_facade(base_dir)
    try:
        print(f.rebuild())
    finally:
        f.close()
    return 0


def cmd_refresh(base_dir: Path) -> int:
    f = _make_facade(base_dir)
    try:
        print(f.refresh())
    finally:
        f.close()
    return 0


def cmd_stats(base_dir: Path) -> int:
    f = _make_facade(base_dir)
    try:
        print(f.stats())
    finally:
        f.close()
    return 0


def cmd_clean(base_dir: Path) -> int:
    cfg = _load_atlas_config(base_dir)
    db = cfg.resolve_db_path(base_dir)
    log = base_dir / cfg.log_path
    removed: list[str] = []
    for p in (db, log):
        if p.is_file():
            p.unlink()
            removed.append(str(p))
    if removed:
        print("[atlas] removed:")
        for r in removed:
            print(f"  {r}")
    else:
        print("[atlas] nothing to remove")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="atlas-setup.sh",
        description=(
            "Управление Project Atlas — семантическим графом проекта. "
            "Не требует запущенного сервера инференса."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name, help_text in (
        ("rebuild", "полная переиндексация (создаёт БД, если её нет)"),
        ("init",    "алиас для rebuild"),
        ("refresh", "инкрементальная переиндексация по SHA-256"),
        ("stats",   "статистика: файлы, узлы, рёбра, размер БД"),
        ("clean",   "удалить graph.db и atlas.log"),
    ):
        sub.add_parser(name, help=help_text)

    args = parser.parse_args(argv)
    base_dir = Path.cwd().resolve()

    if args.cmd in ("rebuild", "init"):
        return cmd_rebuild(base_dir)
    if args.cmd == "refresh":
        return cmd_refresh(base_dir)
    if args.cmd == "stats":
        return cmd_stats(base_dir)
    if args.cmd == "clean":
        return cmd_clean(base_dir)
    # argparse с required=True не даст сюда попасть, но перестрахуемся
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
