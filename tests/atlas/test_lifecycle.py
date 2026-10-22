from __future__ import annotations

from pathlib import Path

from harness.atlas.config import AtlasConfig
from harness.atlas.facade import AtlasFacade


def _fresh_facade(project: Path) -> AtlasFacade:
    cfg = AtlasConfig(
        enabled=True,
        db_path=str(project / ".atlas" / "graph.db"),
        log_path=str(project / "atlas.log"),
        workspace_root=str(project),
    )
    return AtlasFacade(cfg, base_dir=project)


def test_rebuild_indexes_files(project: Path):
    f = _fresh_facade(project)
    try:
        r = f.lifecycle.rebuild()
        assert r["files_indexed"] == 2  # main.py + utils.py, broken упал
        assert r["errors"] == 1  # broken.py
        nodes = f.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        assert nodes > 0
    finally:
        f.close()


def test_rebuild_ignores_gitignore(project: Path):
    f = _fresh_facade(project)
    try:
        f.lifecycle.rebuild()
        paths = {r[0] for r in f.conn.execute(
            "SELECT path FROM files"
        ).fetchall()}
        assert "src/main.py" in paths
        assert not any("venv" in p for p in paths)
    finally:
        f.close()


def test_rebuild_idempotent(project: Path):
    f = _fresh_facade(project)
    try:
        f.lifecycle.rebuild()
        count1 = f.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        gen1 = f.queries.generation_hash()
        f.lifecycle.rebuild()
        count2 = f.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        gen2 = f.queries.generation_hash()
        assert count1 == count2
        assert gen1 == gen2
    finally:
        f.close()


def test_refresh_only_changed(project: Path):
    f = _fresh_facade(project)
    try:
        f.lifecycle.rebuild()
        # Изменяем один файл
        (project / "src" / "utils.py").write_text(
            '"""Utils v2."""\n'
            "\n"
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "def new_fn():\n"
            "    return 42\n",
            encoding="utf-8",
        )
        r = f.lifecycle.refresh()
        assert r["files_indexed"] == 1
        names = {row[0] for row in f.conn.execute(
            "SELECT name FROM nodes WHERE kind='Function'"
        ).fetchall()}
        assert "new_fn" in names
    finally:
        f.close()


def test_refresh_detects_deleted(project: Path):
    f = _fresh_facade(project)
    try:
        f.lifecycle.rebuild()
        (project / "src" / "utils.py").unlink()
        r = f.lifecycle.refresh()
        assert r["deleted"] == 1
    finally:
        f.close()


def test_generation_hash_changes_after_change(project: Path):
    f = _fresh_facade(project)
    try:
        f.lifecycle.rebuild()
        g1 = f.queries.generation_hash()
        (project / "src" / "main.py").write_text(
            "def only(): return 1\n", encoding="utf-8",
        )
        f.lifecycle.refresh()
        g2 = f.queries.generation_hash()
        assert g1 != g2
    finally:
        f.close()
