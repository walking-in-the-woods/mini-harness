"""Capsule Formatter — компактная сериализация узлов и подграфов."""
from __future__ import annotations

from harness.atlas.config import AtlasConfig
from harness.atlas.formatter.budget import enforce_budget
from harness.atlas.security.sanitizer import Sanitizer
from harness.atlas.store.queries import GraphQueries, NodeRow


class CapsuleFormatter:
    def __init__(self, queries: GraphQueries, sanitizer: Sanitizer,
                 config: AtlasConfig) -> None:
        self.q = queries
        self.s = sanitizer
        self.cfg = config

    # ---- overview ----------------------------------------------------

    def overview(self) -> str:
        stats = self.q.stats()
        gen = self.q.generation_hash() or "unknown"
        # Топ-5 модулей по числу узлов
        rows = self.q.conn.execute(
            "SELECT path, COUNT(*) c FROM nodes "
            "WHERE kind != 'Module' GROUP BY path ORDER BY c DESC LIMIT 5"
        ).fetchall()
        pkgs = ", ".join(
            f"{self._short_path(p)}({c})" for p, c in rows
        ) or "(empty)"
        lines = [
            f"project: atlas | stack: py",
            f"files: {stats['files']} | nodes: {stats['nodes']} | edges: {stats['edges']}",
            f"top: {pkgs}",
            f"gen: {gen}",
        ]
        return enforce_budget("\n".join(lines), self.cfg.budget_overview)

    # ---- search ------------------------------------------------------

    def search(self, query: str) -> str:
        if not query:
            return "ERROR: search требует непустой arg"
        rows = self.q.search(query, limit=10)
        if not rows:
            return "no matches"
        # Возвращаем qualified_name — именно его принимает card и
        # прочие операции. Раньше здесь был path::name, и модель,
        # получив от search строку, не могла передать её в card:
        # find_by_qualified_name её не находил, модель зацикливалась.
        lines = [r.qualified_name for r in rows]
        return enforce_budget("\n".join(lines), self.cfg.budget_search)

    # ---- card --------------------------------------------------------

    def card(self, node: NodeRow) -> str:
        if node.kind == "Function" or node.kind == "Method":
            body = self._card_function(node)
        elif node.kind == "Class":
            body = self._card_class(node)
        elif node.kind == "Module":
            body = self._card_module(node)
        else:
            body = (
                f"{node.qualified_name}\n"
                f"sum: {self.s.clean_summary(node.summary)}\n"
                f"gen: {node.generation_hash}"
            )
        return enforce_budget(body, self.cfg.budget_card)

    def _card_function(self, node: NodeRow) -> str:
        calls = self.q.outgoing(node.id, kind="calls")[:5]
        names = sorted({c["qname"].split("::")[-1] for c in calls if c["qname"]})
        lines = [
            node.qualified_name,
            f"sum: {self.s.clean_summary(node.summary) or '-'}",
        ]
        if names:
            lines.append("calls: " + ", ".join(names))
        lines.append(f"gen: {node.generation_hash}")
        return "\n".join(lines)

    def _card_class(self, node: NodeRow) -> str:
        bases = [e for e in self.q.outgoing(node.id, kind="inherits")]
        methods = self.q.conn.execute(
            "SELECT name FROM nodes WHERE qualified_name LIKE ? "
            "AND kind='Method' ORDER BY name LIMIT 10",
            (node.qualified_name + "::%",),
        ).fetchall()
        lines = [node.qualified_name]
        if bases:
            base_names = sorted({b["qname"].split("::")[-1] for b in bases if b["qname"]})
            if base_names:
                lines.append("bases: " + ", ".join(base_names))
        if methods:
            lines.append("methods: " + ", ".join(m[0] for m in methods))
        lines.append(f"sum: {self.s.clean_summary(node.summary) or '-'}")
        lines.append(f"gen: {node.generation_hash}")
        return "\n".join(lines)

    def _card_module(self, node: NodeRow) -> str:
        public = self.q.conn.execute(
            "SELECT name FROM nodes WHERE path=? "
            "AND kind IN ('Class','Function') AND name NOT LIKE '\\_%' ESCAPE '\\' "
            "ORDER BY name LIMIT 10",
            (node.path,),
        ).fetchall()
        imports_out = self.q.outgoing(node.id, kind="imports")[:10]
        imports_in = self.q.incoming(node.id, kind="imports")[:10]
        lines = [
            node.path,
            f"sum: {self.s.clean_summary(node.summary) or '-'}",
        ]
        if public:
            lines.append("public: " + ", ".join(p[0] for p in public))
        if imports_out:
            lines.append(
                "imports: " + ", ".join(
                    sorted({o["qname"].split("::")[-1] for o in imports_out if o["qname"]})
                )
            )
        if imports_in:
            lines.append(
                "imported_by: " + ", ".join(
                    sorted({i["qname"].split("::")[-1] for i in imports_in if i["qname"]})
                )
            )
        lines.append(f"gen: {node.generation_hash}")
        return "\n".join(lines)

    # ---- callers / callees -------------------------------------------

    def callers(self, node: NodeRow) -> str:
        rows = self.q.incoming(node.id, kind="calls")
        return enforce_budget(
            self._render_relations(f"callers of {node.qualified_name}", rows,
                                   src_key="src_id", src_qname_key="qname",
                                   src_path_key="path"),
            self.cfg.budget_callers,
        )

    def callees(self, node: NodeRow) -> str:
        rows = self.q.outgoing(node.id, kind="calls")
        return enforce_budget(
            self._render_relations(f"callees of {node.qualified_name}", rows,
                                   src_key="dst_id", src_qname_key="qname",
                                   src_path_key="path"),
            self.cfg.budget_callees,
        )

    def _render_relations(self, header: str, rows: list[dict],
                          *, src_key: str, src_qname_key: str,
                          src_path_key: str) -> str:
        confirmed = [r for r in rows if r["status"] == "confirmed"]
        unresolved = [r for r in rows if r["status"] == "unresolved"]
        lines = [header]
        lines.append(f"  confirmed: {len(confirmed)}")
        for r in confirmed[:10]:
            reason = r.get("reason") or "?"
            qname = r.get(src_qname_key) or r.get(src_key) or "?"
            line = r.get("line")
            loc = f"{qname}" + (f":{line}" if line else "")
            lines.append(f"    [{reason}] {loc}")
        lines.append(f"  unresolved: {len(unresolved)}")
        for r in unresolved[:10]:
            reason = r.get("reason") or "?"
            qname = r.get(src_qname_key) or r.get(src_key) or "?"
            lines.append(f"    [{reason}] {qname}")
        return "\n".join(lines)

    # ---- impact ------------------------------------------------------

    def impact(self, node: NodeRow) -> str:
        """Обратное замыкание по calls до глубины max_depth (≤10 узлов)."""
        seen: set[str] = set()
        frontier: list[tuple[str, int]] = [(node.id, 0)]
        result: list[tuple[str, int, dict]] = []
        while frontier:
            cur_id, depth = frontier.pop(0)
            if depth >= self.cfg.max_depth:
                continue
            rows = self.q.incoming(cur_id, kind="calls")
            for r in rows:
                src = r["src_id"]
                if src in seen:
                    continue
                seen.add(src)
                result.append((src, depth + 1, r))
                frontier.append((src, depth + 1))
                if len(result) >= 10:
                    break
            if len(result) >= 10:
                break
        lines = [f"impact of {node.qualified_name}:"]
        if not result:
            lines.append("  (no upstream callers found)")
        for src, depth, r in result:
            reason = r.get("reason") or r.get("status") or "?"
            qname = r.get("qname") or src
            lines.append(f"  [d{depth}/{reason}] {qname}")
        return enforce_budget("\n".join(lines), self.cfg.budget_impact)

    # ---- helpers -----------------------------------------------------

    @staticmethod
    def _short_path(path: str) -> str:
        return path.rsplit("/", 1)[0] if "/" in path else path
