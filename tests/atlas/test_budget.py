from __future__ import annotations

from harness.atlas.formatter.budget import enforce_budget, estimate_tokens


def test_estimate_tokens_ascii():
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 400) == 100


def test_estimate_tokens_cyrillic():
    # кириллица 2 байта на символ
    assert estimate_tokens("а" * 400) == 200


def test_budget_short_passthrough():
    assert enforce_budget("hello", 100) == "hello"


def test_budget_truncates():
    text = "x" * 10000
    out = enforce_budget(text, 100)
    assert len(out) <= 100 * 4 + 20
    assert out.endswith("[truncated]")


def test_budget_preserves_utf8():
    text = "а" * 1000
    out = enforce_budget(text, 100)
    # Не должно быть UnicodeDecodeError
    assert isinstance(out, str)
    assert out.endswith("[truncated]")


def test_budget_zero_tokens():
    out = enforce_budget("anything", 0)
    assert "[truncated]" in out
