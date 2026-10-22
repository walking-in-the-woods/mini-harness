"""Тесты расширенного Python-парсера (Этап 2, часть 1).

Constant, Decorator, contracts.
"""
from __future__ import annotations

import json

from harness.atlas.indexer.python_ast import (
    PythonAstParser,
    _extract_contracts,
)


SRC = '''"""Module."""
import functools

API_URL = "https://example.com"
TIMEOUT: int = 30
DEBUG = False


def simple_decorator(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)
    return wrapper


@simple_decorator
def decorated():
    return 1


@simple_decorator
@functools.lru_cache(maxsize=128)
def decorated_twice():
    return 2


def add(a: int, b: int) -> int:
    """Add two ints.

    Args:
        a: First.
        b: Second.

    Returns:
        int: Sum.

    Raises:
        ValueError: If negative.
    """
    if a < 0 or b < 0:
        raise ValueError("negative")
    return a + b
'''


def test_constant_extracted():
    r = PythonAstParser().parse("m.py", SRC)
    consts = [n for n in r.nodes if n.kind == "Constant"]
    names = {n.name for n in consts}
    assert names == {"API_URL", "TIMEOUT", "DEBUG"}
    for n in consts:
        assert n.qualified_name.startswith("m::")


def test_constant_edge_from_module():
    r = PythonAstParser().parse("m.py", SRC)
    contains = [
        e for e in r.edges
        if e.kind == "contains" and e.target_name.startswith("m::")
        and any(n.kind == "Constant" and n.qualified_name == e.target_name
                for n in r.nodes)
    ]
    assert len(contains) == 3


def test_constant_skips_non_literal():
    src = "X = some_call()\nY = 1\n"
    r = PythonAstParser().parse("m.py", src)
    names = {n.name for n in r.nodes if n.kind == "Constant"}
    assert names == {"Y"}


def test_decorator_nodes_unique():
    r = PythonAstParser().parse("m.py", SRC)
    decs = [n for n in r.nodes if n.kind == "Decorator"]
    names = {n.name for n in decs}
    assert "simple_decorator" in names
    assert "lru_cache" in names
    # decorated_twice использует simple_decorator — не должно быть дубля
    qnames = [n.qualified_name for n in decs]
    assert len(qnames) == len(set(qnames))


def test_decorator_edges():
    r = PythonAstParser().parse("m.py", SRC)
    dec_edges = [e for e in r.edges if e.kind == "decorates"]
    # decorated: @simple_decorator
    # decorated_twice: @simple_decorator, @functools.lru_cache
    assert len(dec_edges) == 3
    for e in dec_edges:
        assert e.target_name.startswith("m::")


def test_contracts_extracted():
    r = PythonAstParser().parse("m.py", SRC)
    fn = next(n for n in r.nodes if n.name == "add")
    assert fn.contracts is not None
    data = json.loads(fn.contracts)
    assert data["args"] == ["a", "b"]
    assert data["returns"].startswith("int")
    assert data["raises"] == ["ValueError"]


def test_contracts_none_without_docstring():
    r = PythonAstParser().parse("m.py", "def f():\n    return 1\n")
    fn = next(n for n in r.nodes if n.name == "f")
    assert fn.contracts is None


def test_contracts_none_with_plain_docstring():
    src = 'def f():\n    """Just a summary."""\n    return 1\n'
    r = PythonAstParser().parse("m.py", src)
    fn = next(n for n in r.nodes if n.name == "f")
    assert fn.contracts is None


def test_extract_contracts_only_args():
    src = (
        "def f(x):\n"
        '    """Summary.\n'
        "\n"
        "    Args:\n"
        "        x: Something.\n"
        '    """\n'
        "    return x\n"
    )
    r = PythonAstParser().parse("m.py", src)
    data = json.loads(next(n for n in r.nodes if n.name == "f").contracts)
    assert data["args"] == ["x"]
    assert "returns" not in data
    assert "raises" not in data


def test_extract_contracts_directly():
    doc = (
        "Summary.\n"
        "\n"
        "Arguments:\n"
        "    a (int): first\n"
        "    b (str): second\n"
        "\n"
        "Yields:\n"
        "    int: next value\n"
        "\n"
        "Exceptions:\n"
        "    StopIteration: when done\n"
    )
    import ast as _ast
    node = _ast.parse(
        'def f(a, b):\n    """'
        + doc.replace('"', '\\"')
        + '"""\n    pass\n'
    ).body[0]
    result = _extract_contracts(node)
    assert result is not None
    data = json.loads(result)
    assert data["args"] == ["a", "b"]
    assert data["returns"].startswith("int")
    assert data["raises"] == ["StopIteration"]
