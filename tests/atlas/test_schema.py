from __future__ import annotations

from pathlib import Path

from harness.atlas.store.schema import init_db


def test_init_creates_tables(tmp_path: Path):
    conn = init_db(tmp_path / "g.db")
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for t in ("meta", "files", "nodes", "edges"):
        assert t in tables
    conn.close()


def test_foreign_keys_cascade(tmp_path: Path):
    conn = init_db(tmp_path / "g.db")
    conn.execute(
        "INSERT INTO files(path,sha256,indexed_at,size_bytes) "
        "VALUES('a.py','h','now',10)"
    )
    conn.execute(
        "INSERT INTO nodes(id,kind,name,qualified_name,path,"
        "start_line,end_line,generation_hash) "
        "VALUES('id1','Module','a','a::a','a.py',1,1,'g')"
    )
    conn.execute("DELETE FROM files WHERE path='a.py'")
    count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    assert count == 0
    conn.close()


def test_idempotent_init(tmp_path: Path):
    """Повторный init не падает и не дублирует таблицы."""
    db = tmp_path / "g.db"
    init_db(db).close()
    conn = init_db(db)
    conn.execute(
        "INSERT INTO meta(key,value) VALUES('k','v') "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    )
    conn.commit()
    conn.close()
