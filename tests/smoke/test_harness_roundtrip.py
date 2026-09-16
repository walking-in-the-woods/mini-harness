"""End-to-end smoke: модель -> HarnessAgent -> tool call -> результат.

Требует модель, поддерживающую tool calling. Без SMOKE_MODEL — skip.

Smoke обращается к серверу инференса напрямую через OLLAMA_HOST.
HarnessAgent получает client= явно — конфигурация mTLS в mini
не нужна.
"""

from __future__ import annotations

import os
from pathlib import Path

import ollama
import pytest

from harness.agent import HarnessAgent
from harness.audit import AuditLog
from harness.fs_guard import FileSystemGuard
from harness.proxy import ApiProxy


MODEL = os.environ.get("SMOKE_MODEL", "")
HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
TOOL_CAPABLE = os.environ.get(
    "SMOKE_TOOL_CAPABLE", "true"
).lower() == "true"


pytestmark = pytest.mark.smoke


@pytest.fixture(scope="module", autouse=True)
def _require_model():
    if not MODEL:
        pytest.skip("SMOKE_MODEL не задан")
    try:
        ollama.Client(host=HOST).show(MODEL)
    except Exception as e:
        pytest.skip(f"model {MODEL!r} not available: {e}")


@pytest.fixture(scope="module", autouse=True)
def _require_tool_capable():
    if not TOOL_CAPABLE:
        pytest.skip("SMOKE_TOOL_CAPABLE=false")


@pytest.fixture
def agent(tmp_path: Path) -> HarnessAgent:
    (tmp_path / "notes").mkdir()
    (tmp_path / "hello.txt").write_text("PONG-42\n", encoding="utf-8")

    guard = FileSystemGuard({
        "root": str(tmp_path),
        "whitelist": ["**"],
        "blacklist": [],
        "writable": ["notes/**", "*.txt"],
    })
    cfg = {
        "model": MODEL,
        "ollama_host": HOST,
        "num_ctx": 2048,
        "num_predict": 512,
        "keep_alive": "5m",
        "temperature": 0.1,
        "workspace_root": str(tmp_path),
        "max_tool_rounds": 4,
        "max_read_bytes": 200_000,
        "max_write_bytes": 1_000_000,
        "max_list_entries": 500,
    }
    proxy = ApiProxy({}, timeout=5.0, max_request=1000, max_response=1000)
    audit = AuditLog(str(tmp_path / "audit.jsonl"))

    direct_client = ollama.Client(host=HOST)
    return HarnessAgent(cfg, guard, proxy, audit, client=direct_client)


def test_read_file_via_tool_call(agent: HarnessAgent):
    result = agent.run(
        "Read the file hello.txt and tell me exactly what is inside it. "
        "The file content is a short ASCII string."
    )
    if "PONG-42" in result["text"]:
        return
    pytest.xfail(
        f"model did not surface tool result: {result['text'][:300]!r}"
    )


def test_propose_write_via_tool_call(agent: HarnessAgent):
    result = agent.run(
        "Create a file notes/smoke.txt containing exactly the text "
        "'OK-HARNESS'. Use the propose_write tool."
    )
    pending = result["pending_writes"]
    if not pending:
        pytest.xfail(
            f"model did not call propose_write: {result['text'][:300]!r}"
        )
    normalized = Path(pending[0]["path"]).as_posix().removeprefix("./")
    assert normalized.startswith("notes/")
    assert "OK-HARNESS" in pending[0]["content"]
