"""Lifecycle Manager: полная и инкрементальная индексация."""
from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from pathlib import Path

from harness.atlas.config import AtlasConfig
from harness.atlas.evidence.resolver import EvidenceResolver
from harness.atlas.indexer.python_ast import (
    PythonAstParser,
    RawEdge,
    RawNode,
    node_id,
)
from harness.atlas.indexer.walker import FileEntry, Walker


log = logging.getLogger("atlas")


class LifecycleManager:
    def __init__(self, conn: sqlite3.Connection, config: AtlasConfig,
                 root: Path, log_path: Path) -> None:
        self.conn = conn
        self.cfg = config
        self.root = root
        self.log_path = log_path
        self.parser = PythonAstParser()
        self.resolver = EvidenceResolver(conn)

    # ---- public ------------------------------------------------------

    def rebuild(self) -> dict:
        t0 = time.monotonic()
        with self.conn:
            self.conn.execute("DELETE FROM files")
        stats = self._index_files(self._walk_all(), mode="full")
        self._finalize_generation()
        stats["seconds"] = round(time.monotonic() - t0, 2)
        self._log("rebuild", stats)
        return stats

    def refresh(self) -> dict:
        t0 = time.monotonic()
        existing: dict[str, str] = dict(self.conn.execute(
            "SELECT path, sha256 FROM files"
        ).fetchall())
        on_disk: dict[str, FileEntry] = {}
        changed: list[FileEntry] = []
        for entry in self._walk_all():
            on_disk[entry.rel_path] = entry
            if existing.get(entry.rel_path) != entry.sha256:
                changed.append(entry)
        deleted = [p for p in existing if p not in on_disk]

        for p in deleted:
            with self.conn:
                self.conn.execute("DELETE FROM files WHERE path=?", (p,))

        stats = self._index_files(changed, mode="incremental")
        stats["deleted"] = len(deleted)
        self._finalize_generation()
        stats["seconds"] = round(time.monotonic() - t0, 2)
        self._log("refresh", stats)
        return stats

    # ---- internals ---------------------------------------------------

    def _walk_all(self) -> list[FileEntry]:
        return list(Walker(self.root, self.cfg).iter_files())

    def _index_files(self, files: list[FileEntry], *, mode: str) -> dict:
        ok = skipped = errors = 0
        for entry in files:
            try:
                self._index_one(entry)
                ok += 1
            except Exception as e:
                errors += 1
                log.warning("atlas: index failed for %s: %s",
                            entry.rel_path, e)
        return {"mode": mode, "files_indexed": ok, "errors": errors}

    def _index_one(self, entry: FileEntry) -> None:
        try:
            source = entry.abs_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            raise RuntimeError(f"read error: {e}")

        parse = self.parser.parse(entry.rel_path, source)
        if parse.error:
            raise RuntimeError(parse.error)

        with self.conn:  # транзакция на файл (ФТ-14)
            self.conn.execute(
                "DELETE FROM nodes WHERE path=?", (entry.rel_path,)
            )
            self.conn.execute(
                "INSERT INTO files(path,sha256,indexed_at,size_bytes) "
                "VALUES(?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
                "sha256=excluded.sha256, indexed_at=excluded.indexed_at, "
                "size_bytes=excluded.size_bytes",
                (
                    entry.rel_path,
                    entry.sha256,
                    time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
                    entry.size_bytes,
                ),
            )

            node_ids: dict[str, str] = {}
            for raw in parse.nodes:
                nid = node_id(raw.path, raw.qualified_name)
                node_ids[raw.qualified_name] = nid
                self.conn.execute(
                    "INSERT OR REPLACE INTO nodes("
                    "id,kind,name,qualified_name,path,start_line,"
                    "end_line,summary,contracts,generation_hash) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        nid, raw.kind, raw.name, raw.qualified_name,
                        raw.path, raw.start_line, raw.end_line,
                        raw.summary, raw.contracts, "pending",
                    ),
                )

            by_src: dict[str, list[RawEdge]] = {}
            for e in parse.edges:
                by_src.setdefault(e.src_qualified, []).append(e)

            for src_qname, edges in by_src.items():
                src_id = node_ids.get(src_qname)
                if src_id is None:
                    continue
                resolved = self.resolver.resolve(
                    src_id, entry.rel_path, edges
                )
                for r in resolved:
                    self.conn.execute(
                        "INSERT OR REPLACE INTO edges("
                        "src_id,dst_id,kind,status,reason,line,via,"
                        "generation_hash) VALUES(?,?,?,?,?,?,?,?)",
                        (
                            r.src_id, r.dst_id, r.kind, r.status,
                            r.reason, r.line,
                            getattr(r, "via", None), "pending",
                        ),
                    )

        self.conn.execute(
            "INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')"
        )

    def _finalize_generation(self) -> None:
        rows = self.conn.execute(
            "SELECT path, sha256 FROM files ORDER BY path"
        ).fetchall()
        h = hashlib.sha1()
        for path, sha in rows:
            h.update(path.encode("utf-8"))
            h.update(b"\0")
            h.update(sha.encode("utf-8"))
            h.update(b"\n")
        gen = h.hexdigest()[:16]
        with self.conn:
            self.conn.execute(
                "UPDATE nodes SET generation_hash=?", (gen,)
            )
            self.conn.execute(
                "UPDATE edges SET generation_hash=?", (gen,)
            )
            self.conn.execute(
                "INSERT INTO meta(key,value) VALUES('generation_hash',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (gen,),
            )

    def _log(self, event: str, payload: dict) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} "
                         f"{event} {payload}\n")
        except OSError:
            pass
