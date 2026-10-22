"""DDL и миграции для SQLite-графа Atlas (ФТ-11…ФТ-15)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    path        TEXT PRIMARY KEY,
    sha256      TEXT NOT NULL,
    indexed_at  TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    name            TEXT NOT NULL,
    qualified_name  TEXT NOT NULL,
    path            TEXT NOT NULL,
    start_line      INTEGER NOT NULL,
    end_line        INTEGER NOT NULL,
    summary         TEXT,
    contracts       TEXT,
    generation_hash TEXT NOT NULL,
    FOREIGN KEY(path) REFERENCES files(path) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_nodes_path  ON nodes(path);
CREATE INDEX IF NOT EXISTS idx_nodes_qname ON nodes(qualified_name);
CREATE INDEX IF NOT EXISTS idx_nodes_kind  ON nodes(kind);

CREATE TABLE IF NOT EXISTS edges (
    src_id          TEXT NOT NULL,
    dst_id          TEXT,
    kind            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'confirmed',
    reason          TEXT,
    line            INTEGER,
    via             TEXT,
    generation_hash TEXT NOT NULL,
    PRIMARY KEY (src_id, kind, dst_id, line),
    FOREIGN KEY(src_id) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY(dst_id) REFERENCES nodes(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_edges_src  ON edges(src_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst  ON edges(dst_id);
CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);

CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    name, qualified_name, summary,
    content='nodes', content_rowid='rowid'
);
"""


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Миграция: добавить колонки, которых нет в старых БД.

    Идемпотентно: проверяем PRAGMA table_info перед ALTER.
    Для новых БД (созданных через текущий DDL) эти колонки уже
    есть — ALTER не выполняется.
    """
    node_cols = {row[1] for row in conn.execute("PRAGMA table_info(nodes)")}
    if "contracts" not in node_cols:
        conn.execute("ALTER TABLE nodes ADD COLUMN contracts TEXT")

    edge_cols = {row[1] for row in conn.execute("PRAGMA table_info(edges)")}
    if "via" not in edge_cols:
        conn.execute("ALTER TABLE edges ADD COLUMN via TEXT")


def init_db(db_path: str | Path, timeout: float = 5.0) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=timeout)
    conn.executescript(DDL)
    _ensure_columns(conn)
    conn.commit()
    return conn
