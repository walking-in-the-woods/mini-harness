"""Atlas Facade — единая точка входа.

- call(op, arg, id) → строка (read-only, ФТ-23…ФТ-27)
- rebuild() / refresh() / stats() — для /atlas-команд
- ensure_indexed() — уважает auto_index_on_start

Поведение по умолчанию: БД открывается/создаётся дёшево (пустые
таблицы), никакой индексации не происходит. Если БД пуста,
call() возвращает понятную ошибку «not indexed», а /atlas stats
и /atlas overview — подсказку. Это делает Atlas полностью
пассивным: приложение работает независимо от того, индексирован
ли проект.
"""
from __future__ import annotations

import logging
from pathlib import Path

from harness.atlas.config import AtlasConfig
from harness.atlas.formatter.capsule import CapsuleFormatter
from harness.atlas.lifecycle.manager import LifecycleManager
from harness.atlas.security.sanitizer import Sanitizer
from harness.atlas.store.schema import init_db
from harness.atlas.store.queries import GraphQueries


log = logging.getLogger("atlas")

NOT_INDEXED_HINT = "run /atlas rebuild"


def atlas_tool_schema() -> dict:
    """Схема инструмента ≤ 80 токенов (ФТ-24)."""
    return {
        "type": "function",
        "function": {
            "name": "atlas",
            "description": (
                "Read-only semantic project map. Prefer this over reading files. "
                "ops: overview|search|card|callers|callees|impact. "
                "arg = query or qualified_name. id optional."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "op": {
                        "type": "string",
                        "enum": ["overview", "search", "card",
                                 "callers", "callees", "impact"],
                    },
                    "arg": {"type": "string"},
                    "id":  {"type": "string"},
                },
                "required": ["op"],
            },
        },
    }


class AtlasFacade:
    def __init__(self, config: AtlasConfig, base_dir: Path) -> None:
        self.cfg = config
        self.base_dir = base_dir
        self.workspace_root = (
            (base_dir / config.workspace_root).resolve()
            if not Path(config.workspace_root).is_absolute()
            else Path(config.workspace_root).resolve()
        )
        self.db_path = config.resolve_db_path(base_dir)
        self.log_path = (
            base_dir / config.log_path
            if not Path(config.log_path).is_absolute()
            else Path(config.log_path)
        )
        self.conn = init_db(self.db_path, timeout=config.sqlite_timeout)
        self.queries = GraphQueries(self.conn)
        self.sanitizer = Sanitizer(config)
        self.formatter = CapsuleFormatter(self.queries, self.sanitizer, config)
        self.lifecycle = LifecycleManager(
            self.conn, config, self.workspace_root, self.log_path
        )

    # ---- lifecycle ---------------------------------------------------

    def is_indexed(self) -> bool:
        """True, если в БД есть хотя бы один файл."""
        return self.queries.stats()["files"] > 0

    def ensure_indexed(self) -> str:
        """Инициализация при старте.

        Ведёт себя по-разному в зависимости от auto_index_on_start:
          * False (дефолт) — ничего не делает, возвращает статус.
          * True — если БД пуста, выполняет полную индексацию.

        Никогда не бросает исключение: ошибки логируются и
        возвращаются в виде строки.
        """
        if self.is_indexed():
            s = self.queries.stats()
            return (f"[atlas] ready: {s['files']} files, "
                    f"{s['nodes']} nodes")
        if not self.cfg.auto_index_on_start:
            return (f"[atlas] not indexed ({NOT_INDEXED_HINT})")
        try:
            r = self.lifecycle.rebuild()
        except Exception as e:
            log.exception("atlas: initial index failed")
            return f"[atlas] index failed: {type(e).__name__}: {e}"
        return (f"[atlas] initial index: {r['files_indexed']} files, "
                f"errors={r['errors']}, {r.get('seconds', '?')}s")

    def rebuild(self) -> str:
        r = self.lifecycle.rebuild()
        return (f"[atlas] rebuilt: {r['files_indexed']} files, "
                f"errors={r['errors']}, {r.get('seconds', '?')}s")

    def refresh(self) -> str:
        r = self.lifecycle.refresh()
        return (f"[atlas] refreshed: {r['files_indexed']} indexed, "
                f"{r['deleted']} deleted, errors={r['errors']}, "
                f"{r.get('seconds', '?')}s")

    def stats(self) -> str:
        s = self.queries.stats()
        if s["files"] == 0:
            return f"[atlas] not indexed ({NOT_INDEXED_HINT})"
        gen = self.queries.generation_hash() or "unknown"
        try:
            db_size = self.db_path.stat().st_size
        except OSError:
            db_size = -1
        return (f"[atlas] files={s['files']} nodes={s['nodes']} "
                f"edges={s['edges']} db={db_size}B gen={gen}")

    def overview(self) -> str:
        if not self.is_indexed():
            return f"[atlas] not indexed ({NOT_INDEXED_HINT})"
        return self.formatter.overview()

    # ---- tool entry --------------------------------------------------

    def call(self, op: str, arg: str, node_id_arg: str = "") -> str:
        """Главная точка входа инструмента `atlas` (read-only)."""
        try:
            if not self.is_indexed() and op != "overview":
                # Overview без индекса тоже бессмыслен, но формально
                # может вернуть «пустую карту». Остальные ops — ошибка.
                return (f"ERROR: atlas not indexed ({NOT_INDEXED_HINT})")
            if op == "overview":
                return self.formatter.overview()
            if op == "search":
                return self.formatter.search(arg)
            if op in ("card", "callers", "callees", "impact"):
                node = None
                if node_id_arg:
                    node = self.queries.get_node(node_id_arg)
                if node is None and arg:
                    node = self.queries.find_by_qualified_name(arg)
                if node is None:
                    return f"ERROR: node not found: {arg or node_id_arg!r}"
                if op == "card":
                    return self.formatter.card(node)
                if op == "callers":
                    return self.formatter.callers(node)
                if op == "callees":
                    return self.formatter.callees(node)
                return self.formatter.impact(node)
            return f"ERROR: unknown op {op!r}"
        except Exception as e:
            log.exception("atlas.call failed")
            return f"ERROR: {type(e).__name__}: {e}"

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass
