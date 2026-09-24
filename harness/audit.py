"""JSONL-аудит. fsync только для критичных событий."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


# Durable-события пишутся с fsync. Список намеренно узкий: только
# то, что нельзя потерять при сбое питания между write и закрытием
# файла. Всё остальное — flush без fsync.
#
# backend_error заменил прежнее ollama_error: с появлением
# llama.cpp-бэкенда имя движка в аудите перестало быть осмысленным.
_DURABLE_EVENTS = frozenset({
    "session_start",
    "session_end",
    "apply_result",
    "propose_write",
    "user_prompt_blocked",
    "backend_error",
})


class AuditLog:
    def __init__(self, path: str):
        self.path = Path(path)
        self._enabled = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8"):
                pass
            self._enabled = True
        except OSError:
            import sys
            print(f"[warn] audit log disabled: cannot open {self.path}",
                  file=sys.stderr)

    def write(self, event: str, **fields: Any) -> None:
        if not self._enabled:
            return
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            "pid": os.getpid(),
            "event": event,
            **fields,
        }
        durable = event in _DURABLE_EVENTS
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
                if durable:
                    os.fsync(fh.fileno())
        except OSError:
            pass
