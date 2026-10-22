"""Конфигурация Atlas. Читается из config.yaml (секция atlas)."""
from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any


@dataclass
class AtlasConfig:
    enabled: bool = True
    # Управляет поведением при старте REPL:
    #   False (по умолчанию) — Atlas ничего не индексирует сам,
    #     только открывает/создаёт пустую БД. Индексация — через
    #     /atlas rebuild|refresh.
    #   True — если БД пуста, выполняется полная индексация при
    #     старте. На больших проектах это блокирует REPL.
    auto_index_on_start: bool = False
    db_path: str = "workspace/.atlas/graph.db"
    log_path: str = "logs/atlas.log"
    workspace_root: str = "workspace"
    max_depth: int = 3
    budget_overview: int = 200
    budget_search: int = 80
    budget_card: int = 150
    budget_callers: int = 120
    budget_callees: int = 120
    budget_impact: int = 180
    sanitize_max_name_len: int = 80
    sanitize_max_summary_len: int = 300
    python_extensions: tuple[str, ...] = (".py",)
    ignore_dirs: tuple[str, ...] = (
        ".git", "__pycache__", ".venv", "venv",
        "node_modules", ".atlas", ".pytest_cache",
    )
    sqlite_timeout: float = 5.0

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "AtlasConfig":
        if not raw:
            return cls()
        known = {f.name for f in fields(cls)}
        clean: dict[str, Any] = {}
        for k, v in raw.items():
            if k not in known:
                continue
            if k in {"python_extensions", "ignore_dirs"} and isinstance(v, list):
                v = tuple(v)
            clean[k] = v
        return cls(**clean)

    def resolve_db_path(self, base_dir: Path) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else (base_dir / p)
