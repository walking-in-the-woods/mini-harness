"""Запросы к графу Atlas."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass
class NodeRow:
    id: str
    kind: str
    name: str
    qualified_name: str
    path: str
    start_line: int
    end_line: int
    summary: str | None
    generation_hash: str


_NODE_COLS = (
    "id,kind,name,qualified_name,path,start_line,end_line,"
    "summary,generation_hash"
)


class GraphQueries:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def get_node(self, node_id: str) -> NodeRow | None:
        cur = self.conn.execute(
            f"SELECT {_NODE_COLS} FROM nodes WHERE id=?", (node_id,)
        )
        row = cur.fetchone()
        return NodeRow(*row) if row else None

    def find_by_qualified_name(self, qname: str) -> NodeRow | None:
        cur = self.conn.execute(
            f"SELECT {_NODE_COLS} FROM nodes WHERE qualified_name=? LIMIT 1",
            (qname,),
        )
        row = cur.fetchone()
        return NodeRow(*row) if row else None

    def search(self, query: str, limit: int = 20) -> list[NodeRow]:
        try:
            cur = self.conn.execute(
                "SELECT n.id,n.kind,n.name,n.qualified_name,n.path,"
                "n.start_line,n.end_line,n.summary,n.generation_hash "
                "FROM nodes_fts f JOIN nodes n ON n.rowid=f.rowid "
                "WHERE nodes_fts MATCH ? ORDER BY rank LIMIT ?",
                (query, limit),
            )
            return [NodeRow(*r) for r in cur.fetchall()]
        except sqlite3.OperationalError:
            # FTS-синтаксис мог не понравиться — fallback по LIKE
            like = f"%{query}%"
            cur = self.conn.execute(
                f"SELECT {_NODE_COLS} FROM nodes "
                "WHERE name LIKE ? OR qualified_name LIKE ? LIMIT ?",
                (like, like, limit),
            )
            return [NodeRow(*r) for r in cur.fetchall()]

    def incoming(self, node_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT e.src_id, e.kind, e.status, e.reason, e.line, "
            "n.qualified_name, n.path "
            "FROM edges e JOIN nodes n ON n.id=e.src_id WHERE e.dst_id=?"
        )
        params: list[Any] = [node_id]
        if kind:
            sql += " AND e.kind=?"
            params.append(kind)
        return [
            dict(zip(
                ("src_id", "kind", "status", "reason", "line", "qname", "path"),
                r,
            ))
            for r in self.conn.execute(sql, params).fetchall()
        ]

    def outgoing(self, node_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT e.dst_id, e.kind, e.status, e.reason, e.line, "
            "n.qualified_name, n.path "
            "FROM edges e JOIN nodes n ON n.id=e.dst_id WHERE e.src_id=?"
        )
        params: list[Any] = [node_id]
        if kind:
            sql += " AND e.kind=?"
            params.append(kind)
        return [
            dict(zip(
                ("dst_id", "kind", "status", "reason", "line", "qname", "path"),
                r,
            ))
            for r in self.conn.execute(sql, params).fetchall()
        ]

    def generation_hash(self) -> str | None:
        cur = self.conn.execute(
            "SELECT value FROM meta WHERE key='generation_hash'"
        )
        row = cur.fetchone()
        return row[0] if row else None

    def set_generation_hash(self, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key,value) VALUES('generation_hash',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (value,),
        )

    def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for key, sql in (
            ("nodes", "SELECT COUNT(*) FROM nodes"),
            ("edges", "SELECT COUNT(*) FROM edges"),
            ("files", "SELECT COUNT(*) FROM files"),
        ):
            row = self.conn.execute(sql).fetchone()
            out[key] = int(row[0]) if row else 0
        return out
