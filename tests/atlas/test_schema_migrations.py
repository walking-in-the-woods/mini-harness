"""Тесты миграции схемы: старые БД должны получать новые колонки."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from harness.atlas.store.schema import init_db


def _make_old_db(path: Path) -> None:
    """Создать БД в формате Этапа 1: без contracts и без via."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE files (
            path TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
            indexed_at TEXT NOT NULL, size_bytes INTEGER NOT NULL
        );
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
            qualified_name TEXT NOT NULL, path TEXT NOT NULL,
            start_line INTEGER NOT NULL, end_line INTEGER NOT NULL,
            summary TEXT, generation_hash TEXT NOT NULL
        );
        CREATE TABLE edges (
            src_id TEXT NOT NULL, dst_id TEXT, kind TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'confirmed', reason TEXT,
            line INTEGER, generation_hash TEXT NOT NULL,
            PRIMARY KEY (src_id, kind, dst_id, line)
        );
    """)
    conn.commit()
    conn.close()


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_migration_adds_contracts_and_via(tmp_path: Path):
    db = tmp_path / "graph.db"
    _make_old_db(db)

    conn = init_db(db)
    try:
        assert "contracts" in _cols(conn, "nodes")
        assert "via" in _cols(conn, "edges")
    finally:
        conn.close()


def test_migration_idempotent(tmp_path: Path):
    db = tmp_path / "graph.db"
    init_db(db).close()
    conn = init_db(db)
    try:
        # повторный init не падает и не дублирует колонки
        assert "contracts" in _cols(conn, "nodes")
        assert "via" in _cols(conn, "edges")
    finally:
        conn.close()


def test_migration_preserves_data(tmp_path: Path):
    db = tmp_path / "graph.db"
    _make_old_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO files VALUES('a.py','h','now',10)"
    )
    conn.execute(
        "INSERT INTO nodes VALUES('id1','Module','a','a','a.py',1,1,"
        "'sum','g')"
    )
    conn.commit()
    conn.close()

    conn = init_db(db)
    try:
        row = conn.execute(
            "SELECT name, summary, contracts FROM nodes WHERE id='id1'"
        ).fetchone()
        assert row[0] == "a"
        assert row[1] == "sum"
        assert row[2] is None
    finally:
        conn.close()
