"""Общие фикстуры тестов Atlas. Модель не вызывается."""
from __future__ import annotations

from pathlib import Path

import pytest

from harness.atlas.config import AtlasConfig
from harness.atlas.facade import AtlasFacade


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Мини-проект с несколькими .py-файлами и .gitignore."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text(
        '"""Main module."""\n'
        "import os\n"
        "from src.utils import add\n"
        "\n"
        "\n"
        "def greet(name):\n"
        '    """Say hello."""\n'
        "    return f'hi {name}'\n"
        "\n"
        "\n"
        "def run():\n"
        "    print(greet('world'))\n"
        "    return add(1, 2)\n",
        encoding="utf-8",
    )
    (src / "utils.py").write_text(
        '"""Utilities."""\n'
        "\n"
        "\n"
        "def add(a, b):\n"
        '    """Add two ints."""\n'
        "    return a + b\n"
        "\n"
        "\n"
        "def _private():\n"
        "    return 0\n",
        encoding="utf-8",
    )
    (src / "broken.py").write_text("def broken(  \n", encoding="utf-8")
    # Игнорируемое
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "ignored.py").write_text("def no(): pass\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(
        ".venv/\n__pycache__/\n", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def atlas_cfg(project: Path) -> AtlasConfig:
    return AtlasConfig(
        enabled=True,
        db_path=str(project / ".atlas" / "graph.db"),
        log_path=str(project / "atlas.log"),
        workspace_root=str(project),
        max_depth=3,
    )


@pytest.fixture
def facade(atlas_cfg: AtlasConfig, project: Path) -> AtlasFacade:
    f = AtlasFacade(atlas_cfg, base_dir=project)
    yield f
    f.close()
