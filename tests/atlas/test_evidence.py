from __future__ import annotations

from pathlib import Path

from harness.atlas.evidence.resolver import EvidenceResolver
from harness.atlas.indexer.python_ast import RawEdge
from harness.atlas.store.schema import init_db


def _seed(conn):
    conn.execute(
        "INSERT INTO files(path,sha256,indexed_at,size_bytes) "
        "VALUES('a.py','h','now',1)"
    )
    conn.execute(
        "INSERT INTO files(path,sha256,indexed_at,size_bytes) "
        "VALUES('b.py','h2','now',1)"
    )
    for nid, kind, name, qn, path in [
        ("a1", "Module", "a", "a", "a.py"),
        ("a2", "Function", "foo", "a::foo", "a.py"),
        ("b1", "Function", "foo", "b::foo", "b.py"),
        ("b2", "Class", "Bar", "b::Bar", "b.py"),
        ("b3", "Module", "b", "b", "b.py"),
    ]:
        conn.execute(
            "INSERT INTO nodes(id,kind,name,qualified_name,path,"
            "start_line,end_line,generation_hash) "
            "VALUES(?,?,?,?,?,1,1,'g')",
            (nid, kind, name, qn, path),
        )
    conn.commit()


def test_calls_resolved_local(tmp_path: Path):
    """Локальный вызов 'bar' — такой функции нет → unknown."""
    conn = init_db(tmp_path / "g.db")
    _seed(conn)
    r = EvidenceResolver(conn)
    out = r.resolve("a2", "a.py", [
        RawEdge("a::foo", "calls", "bar", line=5),
    ])
    assert out[0].status == "unresolved"
    assert out[0].reason == "unknown"


def test_calls_local_same_name(tmp_path: Path):
    """Локальный вызов 'foo' из a.py → функция a::foo в том же файле.

    Разрешение идёт по имени (не через import), поэтому reason =
    same-name, а не import-binding.
    """
    conn = init_db(tmp_path / "g.db")
    _seed(conn)
    r = EvidenceResolver(conn)
    out = r.resolve("a2", "a.py", [
        RawEdge("a::foo", "calls", "foo", line=5),
    ])
    assert out[0].status == "confirmed"
    assert out[0].reason == "same-name"
    assert out[0].dst_id == "a2"


def test_calls_global_unique_after_delete(tmp_path: Path):
    """Удаляем b::foo — остаётся единственный foo глобально.

    Из файла a.py 'foo' всё равно локально (a::foo), но проверим
    глобальный путь: src_path='x.py' не даёт local hits, тогда
    срабатывает global lookup.
    """
    conn = init_db(tmp_path / "g.db")
    _seed(conn)
    conn.execute("DELETE FROM nodes WHERE id='b1'")
    conn.commit()
    r = EvidenceResolver(conn)
    out = r.resolve("a2", "x.py", [   # src_path специально чужой
        RawEdge("a::foo", "calls", "foo", line=5),
    ])
    assert out[0].status == "confirmed"
    assert out[0].reason == "import-binding"
    assert out[0].dst_id == "a2"


def test_calls_same_name_ambiguous(tmp_path: Path):
    """Два foo (a.py и b.py) → глобально неоднозначно."""
    conn = init_db(tmp_path / "g.db")
    _seed(conn)
    r = EvidenceResolver(conn)
    out = r.resolve("a1", "x.py", [
        RawEdge("a::foo", "calls", "foo", line=1),
    ])
    assert out[0].status == "unresolved"
    assert out[0].reason == "same-name"


def test_calls_unknown_globally(tmp_path: Path):
    conn = init_db(tmp_path / "g.db")
    _seed(conn)
    r = EvidenceResolver(conn)
    out = r.resolve("a1", "x.py", [
        RawEdge("a::foo", "calls", "zzz", line=1),
    ])
    assert out[0].status == "unresolved"
    assert out[0].reason == "unknown"


def test_inherits_unique(tmp_path: Path):
    conn = init_db(tmp_path / "g.db")
    _seed(conn)
    r = EvidenceResolver(conn)
    out = r.resolve("a2", "a.py", [
        RawEdge("a::foo", "inherits", "Bar", line=1),
    ])
    assert out[0].status == "confirmed"
    assert out[0].reason == "same-name"
    assert out[0].dst_id == "b2"


def test_inherits_missing(tmp_path: Path):
    conn = init_db(tmp_path / "g.db")
    _seed(conn)
    r = EvidenceResolver(conn)
    out = r.resolve("a2", "a.py", [
        RawEdge("a::foo", "inherits", "Nope", line=1),
    ])
    assert out[0].status == "unresolved"
    assert out[0].reason == "unknown"


def test_imports_resolved(tmp_path: Path):
    """Импорт b (qname = 'b') → резолвится в b3 (Module)."""
    conn = init_db(tmp_path / "g.db")
    _seed(conn)
    r = EvidenceResolver(conn)
    out = r.resolve("a1", "a.py", [
        RawEdge("a", "imports", "b", line=1),
    ])
    assert out[0].status == "confirmed"
    assert out[0].reason == "import-binding"
    assert out[0].dst_id == "b3"


def test_imports_unresolved(tmp_path: Path):
    conn = init_db(tmp_path / "g.db")
    _seed(conn)
    r = EvidenceResolver(conn)
    out = r.resolve("a1", "a.py", [
        RawEdge("a", "imports", "zzz", line=1),
    ])
    assert out[0].status == "unresolved"
    assert out[0].reason == "unknown"
