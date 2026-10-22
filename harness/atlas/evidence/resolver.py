"""Evidence Engine: разрешение рёбер до dst_id с status/reason."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from harness.atlas.indexer.python_ast import RawEdge


@dataclass
class ResolvedEdge:
    src_id: str
    dst_id: str | None
    kind: str
    status: str
    reason: str | None
    line: int | None


class EvidenceResolver:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def resolve(self, src_id: str, src_path: str,
                edges: list[RawEdge]) -> list[ResolvedEdge]:
        out: list[ResolvedEdge] = []
        for e in edges:
            if e.kind == "contains":
                dst_id = self._lookup_qname(e.target_name)
                out.append(ResolvedEdge(
                    src_id=src_id, dst_id=dst_id, kind="contains",
                    status=e.status, reason=e.reason, line=e.line,
                ))
                continue
            if e.kind in ("decorates", "raises"):
                out.append(ResolvedEdge(
                    src_id=src_id, dst_id=None, kind=e.kind,
                    status=e.status, reason=e.reason, line=e.line,
                ))
                continue

            if e.kind == "imports":
                dst_id = self._lookup_module(e.target_name)
                out.append(ResolvedEdge(
                    src_id=src_id, dst_id=dst_id, kind="imports",
                    status="confirmed" if dst_id else "unresolved",
                    reason="import-binding" if dst_id else "unknown",
                    line=e.line,
                ))
                continue

            if e.kind == "inherits":
                candidates = self._lookup_by_name(e.target_name, kind="Class")
                if len(candidates) == 1:
                    out.append(ResolvedEdge(
                        src_id=src_id, dst_id=candidates[0],
                        kind="inherits", status="confirmed",
                        reason="same-name", line=e.line,
                    ))
                else:
                    out.append(ResolvedEdge(
                        src_id=src_id, dst_id=None, kind="inherits",
                        status="unresolved",
                        reason="unknown" if not candidates else "same-name",
                        line=e.line,
                    ))
                continue

            if e.kind == "calls":
                short = e.target_name.split(".")[-1]
                local = self._lookup_local(src_path, short)
                if len(local) == 1:
                    reason = "import-binding" if "." in e.target_name else "same-name"
                    out.append(ResolvedEdge(
                        src_id=src_id, dst_id=local[0], kind="calls",
                        status="confirmed", reason=reason, line=e.line,
                    ))
                    continue
                global_hits = self._lookup_by_name(short, kind=None)
                if len(global_hits) == 1:
                    out.append(ResolvedEdge(
                        src_id=src_id, dst_id=global_hits[0], kind="calls",
                        status="confirmed", reason="import-binding",
                        line=e.line,
                    ))
                else:
                    out.append(ResolvedEdge(
                        src_id=src_id, dst_id=None, kind="calls",
                        status="unresolved",
                        reason="unknown" if not global_hits else "same-name",
                        line=e.line,
                    ))
                continue

            out.append(ResolvedEdge(
                src_id=src_id, dst_id=None, kind=e.kind,
                status=e.status, reason=e.reason, line=e.line,
            ))
        return out

    def _lookup_qname(self, qname: str) -> str | None:
        row = self.conn.execute(
            "SELECT id FROM nodes WHERE qualified_name=? LIMIT 1", (qname,)
        ).fetchone()
        return row[0] if row else None

    def _lookup_module(self, module_name: str) -> str | None:
        """Ищем узел Module с qname == '<module с :: вместо .>'."""
        target_qname = module_name.replace(".", "::")
        row = self.conn.execute(
            "SELECT id FROM nodes WHERE kind='Module' AND qualified_name=? "
            "LIMIT 1",
            (target_qname,),
        ).fetchone()
        return row[0] if row else None

    def _lookup_local(self, src_path: str, short_name: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT id FROM nodes WHERE path=? AND name=? "
            "AND kind IN ('Function','Method')",
            (src_path, short_name),
        ).fetchall()
        return [r[0] for r in rows]

    def _lookup_by_name(self, name: str, kind: str | None) -> list[str]:
        short = name.split(".")[-1]
        if kind:
            rows = self.conn.execute(
                "SELECT id FROM nodes WHERE kind=? AND name=?",
                (kind, short),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT id FROM nodes WHERE name=? "
                "AND kind IN ('Function','Method')",
                (short,),
            ).fetchall()
        return [r[0] for r in rows]
