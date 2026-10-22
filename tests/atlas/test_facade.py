from __future__ import annotations

from pathlib import Path

from harness.atlas.facade import AtlasFacade, atlas_tool_schema


def _indexed(project: Path) -> AtlasFacade:
    from harness.atlas.config import AtlasConfig
    cfg = AtlasConfig(
        enabled=True,
        db_path=str(project / ".atlas" / "graph.db"),
        log_path=str(project / "atlas.log"),
        workspace_root=str(project),
    )
    f = AtlasFacade(cfg, base_dir=project)
    f.lifecycle.rebuild()
    return f


def test_tool_schema_shape():
    s = atlas_tool_schema()
    fn = s["function"]
    assert fn["name"] == "atlas"
    assert fn["parameters"]["required"] == ["op"]
    assert set(fn["parameters"]["properties"]["op"]["enum"]) == {
        "overview", "search", "card", "callers", "callees", "impact",
    }


def test_overview(project: Path):
    f = _indexed(project)
    try:
        out = f.call("overview", "")
        assert "project:" in out
        assert "gen:" in out
        assert len(out.encode()) <= 200 * 4 + 50
    finally:
        f.close()


def test_card_by_qname(project: Path):
    f = _indexed(project)
    try:
        out = f.call("card", "src::utils::add")
        assert "src::utils::add" in out
        assert "sum:" in out
        assert "gen:" in out
        assert len(out.encode()) <= 150 * 4 + 50
    finally:
        f.close()


def test_card_not_found(project: Path):
    f = _indexed(project)
    try:
        out = f.call("card", "no::such::node")
        assert out.startswith("ERROR:")
    finally:
        f.close()


def test_search_finds(project: Path):
    f = _indexed(project)
    try:
        out = f.call("search", "add")
        assert "add" in out
    finally:
        f.close()


def test_search_empty(project: Path):
    f = _indexed(project)
    try:
        out = f.call("search", "")
        assert out.startswith("ERROR:")
    finally:
        f.close()


def test_callers_callees(project: Path):
    f = _indexed(project)
    try:
        out1 = f.call("callers", "src::utils::add")
        assert "callers of" in out1
        out2 = f.call("callees", "src::main::run")
        assert "callees of" in out2
    finally:
        f.close()


def test_impact(project: Path):
    f = _indexed(project)
    try:
        out = f.call("impact", "src::utils::add")
        assert "impact of" in out
    finally:
        f.close()


def test_unknown_op(project: Path):
    f = _indexed(project)
    try:
        assert f.call("garbage", "") == "ERROR: unknown op 'garbage'"
    finally:
        f.close()


def test_ensure_indexed_noop_when_filled(project: Path):
    f = _indexed(project)
    try:
        msg = f.ensure_indexed()
        assert "ready" in msg
    finally:
        f.close()


def test_stats_and_rebuild_commands(project: Path):
    f = _indexed(project)
    try:
        s = f.stats()
        assert "[atlas] files=" in s
        r = f.rebuild()
        assert "[atlas] rebuilt:" in r
        r2 = f.refresh()
        assert "[atlas] refreshed:" in r2
    finally:
        f.close()


def test_not_indexed_by_default(project: Path):
    """Свежий facade без rebuild → is_indexed() == False, call() даёт ошибку."""
    from harness.atlas.config import AtlasConfig
    cfg = AtlasConfig(
        enabled=True, auto_index_on_start=False,
        db_path=str(project / ".atlas" / "graph.db"),
        log_path=str(project / "atlas.log"),
        workspace_root=str(project),
    )
    f = AtlasFacade(cfg, base_dir=project)
    try:
        assert f.is_indexed() is False
        out = f.call("card", "src::utils::add")
        assert "not indexed" in out
        assert "/atlas rebuild" in out
    finally:
        f.close()


def test_ensure_indexed_respects_flag_off(project: Path):
    """auto_index_on_start=False → ensure_indexed() не индексирует."""
    from harness.atlas.config import AtlasConfig
    cfg = AtlasConfig(
        enabled=True, auto_index_on_start=False,
        db_path=str(project / ".atlas" / "graph.db"),
        log_path=str(project / "atlas.log"),
        workspace_root=str(project),
    )
    f = AtlasFacade(cfg, base_dir=project)
    try:
        msg = f.ensure_indexed()
        assert "not indexed" in msg
        assert "0 files" not in msg
        assert f.is_indexed() is False
    finally:
        f.close()


def test_ensure_indexed_respects_flag_on(project: Path):
    """auto_index_on_start=True → ensure_indexed() индексирует пустую БД."""
    from harness.atlas.config import AtlasConfig
    cfg = AtlasConfig(
        enabled=True, auto_index_on_start=True,
        db_path=str(project / ".atlas" / "graph.db"),
        log_path=str(project / "atlas.log"),
        workspace_root=str(project),
    )
    f = AtlasFacade(cfg, base_dir=project)
    try:
        msg = f.ensure_indexed()
        assert "initial index" in msg
        assert f.is_indexed() is True
    finally:
        f.close()


def test_stats_reports_not_indexed(project: Path):
    from harness.atlas.config import AtlasConfig
    cfg = AtlasConfig(
        enabled=True, auto_index_on_start=False,
        db_path=str(project / ".atlas" / "graph.db"),
        log_path=str(project / "atlas.log"),
        workspace_root=str(project),
    )
    f = AtlasFacade(cfg, base_dir=project)
    try:
        out = f.stats()
        assert "not indexed" in out
        assert "/atlas rebuild" in out
        out2 = f.overview()
        assert "not indexed" in out2
    finally:
        f.close()


def test_search_output_accepted_by_card(project: Path):
    """search и card должны использовать один и тот же формат имени.

    Регресс: раньше search возвращал 'path::name', а card искал по
    'qualified_name'. Модель, получив от search 'input/counter.py::Counter',
    не могла передать это в card — тот отвечал 'node not found',
    и модель зацикливалась.
    """
    f = _indexed(project)
    try:
        out = f.call("search", "add")
        assert out and not out.startswith("ERROR")
        first = out.splitlines()[0]
        # Строка из search должна приниматься card-ом как arg
        card = f.call("card", first)
        assert not card.startswith("ERROR"), (
            f"card не нашёл узел {first!r}, который вернул search"
        )
        assert first in card, (
            f"card вернул карточку не того узла: {card[:120]!r}"
        )
    finally:
        f.close()
