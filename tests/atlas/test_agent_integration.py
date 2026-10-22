"""Интеграция Atlas с HarnessAgent._dispatch. Модель не вызывается."""
from __future__ import annotations

from pathlib import Path

import pytest

from harness.agent import HarnessAgent
from harness.atlas.config import AtlasConfig
from harness.atlas.facade import AtlasFacade
from harness.audit import AuditLog
from harness.fs_guard import FileSystemGuard
from harness.proxy import ApiProxy


class _NoopClient:
    def chat(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError


@pytest.fixture
def atlas(project: Path) -> AtlasFacade:
    cfg = AtlasConfig(
        enabled=True,
        db_path=str(project / ".atlas" / "graph.db"),
        log_path=str(project / "atlas.log"),
        workspace_root=str(project),
    )
    f = AtlasFacade(cfg, base_dir=project)
    f.lifecycle.rebuild()
    yield f
    f.close()


@pytest.fixture
def agent(project: Path, atlas: AtlasFacade) -> HarnessAgent:
    guard = FileSystemGuard({
        "root": str(project),
        "whitelist": ["**"],
        "blacklist": [],
        "writable": ["output/**"],
    })
    cfg = {
        "model": "t", "ollama_host": "http://127.0.0.1:1",
        "num_ctx": 2048, "num_predict": 512, "keep_alive": "5m",
        "temperature": 0.1, "workspace_root": str(project),
        "max_tool_rounds": 4, "max_read_bytes": 200_000,
        "max_write_bytes": 1_000_000, "max_list_entries": 500,
    }
    return HarnessAgent(
        cfg, guard,
        ApiProxy({}, timeout=5, max_request=1000, max_response=1000),
        AuditLog(str(project / "audit.jsonl")),
        client=_NoopClient(), atlas=atlas,
    )


def test_atlas_schema_present_when_configured(agent: HarnessAgent):
    schemas = agent._tool_schemas()
    names = [t["function"]["name"] for t in schemas]
    assert "atlas" in names
    assert "read_file" in names  # существующие tools тоже здесь


def test_atlas_absent_when_not_configured(project: Path):
    guard = FileSystemGuard({
        "root": str(project), "whitelist": ["**"],
        "blacklist": [], "writable": ["output/**"],
    })
    cfg = {
        "model": "t", "ollama_host": "http://127.0.0.1:1",
        "num_ctx": 2048, "num_predict": 512, "keep_alive": "5m",
        "temperature": 0.1, "workspace_root": str(project),
        "max_tool_rounds": 4, "max_read_bytes": 200_000,
        "max_write_bytes": 1_000_000, "max_list_entries": 500,
    }
    a = HarnessAgent(
        cfg, guard,
        ApiProxy({}, timeout=5, max_request=1000, max_response=1000),
        AuditLog(str(project / "audit.jsonl")),
        client=_NoopClient(),  # без atlas=
    )
    names = [t["function"]["name"] for t in a._tool_schemas()]
    assert "atlas" not in names


def test_dispatch_calls_facade(agent: HarnessAgent):
    out = agent._dispatch("atlas", {"op": "overview"})
    assert "project:" in out


def test_dispatch_without_facade():
    """Если фасад не сконфигурирован, dispatch возвращает ошибку."""
    from harness.agent import HarnessAgent
    # минимальный dummy-агент: создаём без atlas
    from pathlib import Path
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        guard = FileSystemGuard({
            "root": str(p), "whitelist": ["**"],
            "blacklist": [], "writable": [],
        })
        cfg = {
            "model": "t", "ollama_host": "http://127.0.0.1:1",
            "num_ctx": 2048, "num_predict": 512, "keep_alive": "5m",
            "temperature": 0.1, "workspace_root": str(p),
            "max_tool_rounds": 4, "max_read_bytes": 200_000,
            "max_write_bytes": 1_000_000, "max_list_entries": 500,
        }
        a = HarnessAgent(
            cfg, guard,
            ApiProxy({}, timeout=5, max_request=1000, max_response=1000),
            AuditLog(str(p / "audit.jsonl")),
            client=_NoopClient(),
        )
        out = a._dispatch("atlas", {"op": "overview"})
        assert out.startswith("ERROR: atlas")
