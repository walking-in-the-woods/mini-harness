from __future__ import annotations

from harness.atlas.indexer.python_ast import (
    PythonAstParser,
    node_id,
)


SRC = '''"""Module doc."""
import os
from typing import Optional


class Base:
    """Base class."""


class Counter(Base):
    """A counter."""

    def __init__(self):
        self.n = 0

    def inc(self):
        """Increment."""
        return helper(self.n) + 1


def helper(x: int) -> int:
    """Helper fn."""
    if x < 0:
        raise ValueError("neg")
    return x
'''


def test_node_id_deterministic():
    a = node_id("src/x.py", "src::x::foo")
    b = node_id("src/x.py", "src::x::foo")
    assert a == b
    assert len(a) == 16


def test_node_id_depends_on_path():
    assert node_id("a.py", "m::f") != node_id("b.py", "m::f")


def test_parse_extracts_module_and_classes():
    r = PythonAstParser().parse("src/x.py", SRC)
    assert r.error is None
    kinds = {(n.kind, n.name) for n in r.nodes}
    assert ("Module", "x") in kinds
    assert ("Class", "Base") in kinds
    assert ("Class", "Counter") in kinds
    assert ("Function", "helper") in kinds
    assert ("Method", "__init__") in kinds
    assert ("Method", "inc") in kinds


def test_parse_nodes_all_have_real_path():
    """Все узлы файла должны иметь path = реальный путь файла.

    Регресс на FOREIGN KEY constraint failed: раньше path вычислялся
    из qualified_name и для функций/классов не совпадал с files.path.
    """
    r = PythonAstParser().parse("src/x.py", SRC)
    for n in r.nodes:
        assert n.path == "src/x.py", (
            f"node {n.qualified_name} has path={n.path!r}, "
            f"expected 'src/x.py'"
        )


def test_parse_nested_class_path():
    src = (
        "class Outer:\n"
        "    class Inner:\n"
        "        def m(self):\n"
        "            return 1\n"
    )
    r = PythonAstParser().parse("pkg/mod.py", src)
    for n in r.nodes:
        assert n.path == "pkg/mod.py", n.qualified_name


def test_parse_imports():
    r = PythonAstParser().parse("src/x.py", SRC)
    imports = [e for e in r.edges if e.kind == "imports"]
    names = {e.target_name for e in imports}
    assert "os" in names
    assert "typing" in names
    assert all(e.status == "confirmed" for e in imports)


def test_parse_inherits():
    r = PythonAstParser().parse("src/x.py", SRC)
    inh = [e for e in r.edges if e.kind == "inherits"]
    assert len(inh) == 1
    assert inh[0].target_name == "Base"


def test_parse_calls():
    """В SRC есть реальный вызов helper(...) внутри Counter.inc."""
    r = PythonAstParser().parse("src/x.py", SRC)
    calls = {e.target_name for e in r.edges if e.kind == "calls"}
    assert "helper" in calls
    # self.n — это атрибут в аргументе, тоже попадает как Call(helper),
    # но сам атрибут не является Call
    assert not any(e.kind == "calls" and e.target_name == "self.n"
                   for e in r.edges)


def test_parse_raises():
    r = PythonAstParser().parse("src/x.py", SRC)
    raises = [e for e in r.edges if e.kind == "raises"]
    assert any(e.target_name == "ValueError" for e in raises)


def test_parse_syntax_error():
    r = PythonAstParser().parse("src/b.py", "def x(  \n")
    assert r.error is not None
    assert "syntax error" in r.error


def test_module_qname_uses_path():
    r = PythonAstParser().parse("pkg/sub/mod.py", "x = 1\n")
    module = [n for n in r.nodes if n.kind == "Module"][0]
    assert module.qualified_name == "pkg::sub::mod"
