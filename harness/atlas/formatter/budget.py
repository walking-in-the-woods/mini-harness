"""Контроль бюджета токенов. Эвристика: 1 токен ≈ 4 байта UTF-8."""
from __future__ import annotations


def estimate_tokens(text: str) -> int:
    return (len(text.encode("utf-8")) + 3) // 4


def enforce_budget(text: str, max_tokens: int) -> str:
    if not text:
        return text
    if estimate_tokens(text) <= max_tokens:
        return text
    max_bytes = max_tokens * 4
    truncated = text.encode("utf-8")[:max_bytes]
    # Не разрываем UTF-8
    for trim in range(4):
        try:
            return truncated[: len(truncated) - trim].decode("utf-8") + " [truncated]"
        except UnicodeDecodeError:
            continue
    return text[:max_bytes] + " [truncated]"
