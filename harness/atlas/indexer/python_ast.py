"""Python AST-парсер для Atlas."""
from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RawNode:
    kind: str                # Module | Class | Function | Method | Import | Decorator
    name: str
    qualified_name: str
    path: str                # ← РЕАЛЬНЫЙ путь файла, не производная от qname
    start_line: int
    end_line: int
    summary: str | None = None


@dataclass
class RawEdge:
    src_qualified: str
    kind: str                # contains | imports | calls | inherits | decorates | raises
    target_name: str
    line: int | None = None
    status: str = "confirmed"
    reason: str | None = None


@dataclass
class ParseResult:
    nodes: list[RawNode] = field(default_factory=list)
    edges: list[RawEdge] = field(default_factory=list)
    error: str | None = None


def node_id(path: str, qualified_name: str) -> str:
    """Детерминированный ID узла: sha1(path::qualified_name)[:16]."""
    h = hashlib.sha1(f"{path}::{qualified_name}".encode("utf-8"))
    return h.hexdigest()[:16]


def _first_line_docstring(node: ast.AST) -> str | None:
    doc = ast.get_docstring(node, clean=True)
    if not doc:
        return None
    first = doc.splitlines()[0].strip()
    return first or None


class PythonAstParser:
    """Парсер одного .py-файла в RawNode/RawEdge."""

    def parse(self, path: str, source: str) -> ParseResult:
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as e:
            return ParseResult(error=f"syntax error at line {e.lineno}: {e.msg}")

        result = ParseResult()
        # Qualified name модуля: путь без .py, / → ::
        module_qname = path.removesuffix(".py").replace("/", "::")
        # Реальный путь файла — прокидывается во все узлы этого файла.
        file_path = path

        result.nodes.append(RawNode(
            kind="Module",
            name=Path(path).stem,
            qualified_name=module_qname,
            path=file_path,
            start_line=1,
            end_line=len(source.splitlines()) or 1,
            summary=_first_line_docstring(tree),
        ))

        # Импорты
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._add_import(result, module_qname, file_path,
                                     alias.name, node.lineno)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    self._add_import(result, module_qname, file_path,
                                     node.module, node.lineno)

        # Top-level: классы, функции
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                self._add_class(result, module_qname, file_path, node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._add_function(result, module_qname, file_path,
                                   node, kind="Function")

        return result

    # ---- helpers ------------------------------------------------------

    def _add_import(self, result: ParseResult, module_qname: str,
                    file_path: str, target: str, line: int) -> None:
        name = target.split(".")[-1]
        qname = f"{module_qname}::import:{target}"
        result.nodes.append(RawNode(
            kind="Import",
            name=name,
            qualified_name=qname,
            path=file_path,
            start_line=line,
            end_line=line,
        ))
        result.edges.append(RawEdge(
            src_qualified=module_qname,
            kind="imports",
            target_name=target,
            line=line,
            status="confirmed",
            reason="import-binding",
        ))

    def _add_class(self, result: ParseResult, parent_qname: str,
                   file_path: str, node: ast.ClassDef) -> None:
        qname = f"{parent_qname}::{node.name}"
        result.nodes.append(RawNode(
            kind="Class",
            name=node.name,
            qualified_name=qname,
            path=file_path,
            start_line=node.lineno,
            end_line=node.end_lineno or node.lineno,
            summary=_first_line_docstring(node),
        ))
        result.edges.append(RawEdge(
            src_qualified=parent_qname,
            kind="contains",
            target_name=qname,
            line=node.lineno,
        ))

        for base in node.bases:
            base_name = _dotted_name(base)
            if base_name:
                result.edges.append(RawEdge(
                    src_qualified=qname,
                    kind="inherits",
                    target_name=base_name,
                    line=getattr(base, "lineno", node.lineno),
                    status="unresolved",
                    reason="unknown",
                ))

        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._add_function(result, qname, file_path, child,
                                   kind="Method")
            elif isinstance(child, ast.ClassDef):
                self._add_class(result, qname, file_path, child)

    def _add_function(self, result: ParseResult, parent_qname: str,
                      file_path: str,
                      node: ast.FunctionDef | ast.AsyncFunctionDef,
                      kind: str) -> None:
        qname = f"{parent_qname}::{node.name}"
        result.nodes.append(RawNode(
            kind=kind,
            name=node.name,
            qualified_name=qname,
            path=file_path,
            start_line=node.lineno,
            end_line=node.end_lineno or node.lineno,
            summary=_first_line_docstring(node),
        ))
        result.edges.append(RawEdge(
            src_qualified=parent_qname,
            kind="contains",
            target_name=qname,
            line=node.lineno,
        ))

        for dec in node.decorator_list:
            dec_name = _dotted_name(dec)
            if dec_name:
                result.edges.append(RawEdge(
                    src_qualified=qname,
                    kind="decorates",
                    target_name=dec_name,
                    line=getattr(dec, "lineno", node.lineno),
                ))

        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                callee = _dotted_name(child.func)
                if callee:
                    result.edges.append(RawEdge(
                        src_qualified=qname,
                        kind="calls",
                        target_name=callee,
                        line=getattr(child, "lineno", node.lineno),
                        status="unresolved",
                        reason="same-name",
                    ))
            elif isinstance(child, ast.Raise):
                exc_name = _dotted_name(child.exc) if child.exc else None
                if exc_name:
                    result.edges.append(RawEdge(
                        src_qualified=qname,
                        kind="raises",
                        target_name=exc_name,
                        line=getattr(child, "lineno", node.lineno),
                    ))


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return _dotted_name(node.func)
    if isinstance(node, ast.Subscript):
        return _dotted_name(node.value)
    return None
