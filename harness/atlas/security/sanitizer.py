"""Санитайзер карточек Atlas.

- обрезка строк до max_*_len
- удаление '#', обратных кавычек, переводов строк из name/summary
- фильтрация injection-паттернов → [filtered]
- маскирование секретов → [redacted]
"""
from __future__ import annotations

import re

from harness.atlas.config import AtlasConfig


_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above)", re.I),
    re.compile(r"disregard\s+(?:all\s+)?(?:previous|prior|above)", re.I),
    re.compile(r"forget\s+(?:everything|all|previous)", re.I),
    re.compile(r"new\s+(?:system\s+)?(?:instructions?|prompt|rules)", re.I),
    re.compile(r"reveal\s+(?:your\s+)?(?:system\s+)?prompt", re.I),
    re.compile(r"<\|im_start\|>|<\|im_end\|>", re.I),
    re.compile(r"^\s*system\s*:", re.I | re.M),
    re.compile(r"\byou\s+are\s+now\s+(?:a|an)\b", re.I),
]

_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(password|passwd|pwd)\s*=\s*\S+"),
    re.compile(r"(?i)\bapi[_-]?key\s*=\s*\S+"),
    re.compile(r"(?i)\btoken\s*=\s*\S+"),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
]

_FORBIDDEN_CHARS_RE = re.compile(r"[#`\r\n]+")


class Sanitizer:
    def __init__(self, config: AtlasConfig) -> None:
        self.config = config

    def clean_name(self, text: str | None) -> str:
        return self._clean(text or "", self.config.sanitize_max_name_len)

    def clean_summary(self, text: str | None) -> str:
        return self._clean(text or "", self.config.sanitize_max_summary_len)

    def _clean(self, text: str, max_len: int) -> str:
        if not text:
            return ""
        text = _FORBIDDEN_CHARS_RE.sub(" ", text)
        text = " ".join(text.split())
        for pat in _SECRET_PATTERNS:
            text = pat.sub("[redacted]", text)
        for pat in _INJECTION_PATTERNS:
            text = pat.sub("[filtered]", text)
        if len(text) > max_len:
            text = text[: max_len - 3] + "..."
        return text
