"""Python AST-парсер для Atlas."""
from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RawNode:
    kind: str                # Module | Class | Function | Method | Import | Decorator | Constant
    name: str
    qualified_name: str
    path: str                # реальный путь файла
    start_line: int
    end_line: int
    summary: str | None = None
    contracts: str | None = None   # JSON: {"args": [...], "returns": "...", "raises": [...]}


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


# ── Контракты из docstring ────────────────────────────────────────────────

_ARGS_HEADERS = {"Args:", "Arguments:", "Parameters:"}
_RETURNS_HEADERS = {"Returns:", "Return:", "Yields:"}
_RAISES_HEADERS = {"Raises:", "Exceptions:"}

_ARG_NAME_RE = re.compile(r"^([A-Za-z_]\w*)\s*[:(]")


def _extract_block_names(doc: str, headers: set[str]) -> list[str]:
    """Извлечь имена из блока Args: / Raises:.

    Google-style: после заголовка идёт список строк с большим
    отступом, каждая вида `name (type): описание` или `name: описание`.
    Пустая строка или строка с меньшим отступом — конец блока.
    """
    lines = doc.splitlines()
    for i, line in enumerate(lines):
        if line.strip() not in headers:
            continue
        base_indent = len(line) - len(line.lstrip())
        names: list[str] = []
        for j in range(i + 1, len(lines)):
            ln = lines[j]
            if not ln.strip():
                break
            indent = len(ln) - len(ln.lstrip())
            if indent <= base_indent:
                break
            m = _ARG_NAME_RE.match(ln.strip())
            if m:
                names.append(m.group(1))
        return names
    return []


def _extract_returns(doc: str) -> str | None:
    """Извлечь первую строку из блока Returns:.

    Возвращает текст первой непустой строки блока (обрезается до
    60 символов), либо None.
    """
    lines = doc.splitlines()
    for i, line in enumerate(lines):
        if line.strip() not in _RETURNS_HEADERS:
            continue
        base_indent = len(line) - len(line.lstrip())
        for j in range(i + 1, len(lines)):
            ln = lines[j]
            if not ln.strip():
                continue
            indent = len(ln) - len(ln.lstrip())
            if indent <= base_indent:
                break
            return ln.strip()[:60]
        return None
    return None


def _extract_contracts(node: ast.AST) -> str | None:
    """JSON-строка контрактов из docstring, либо None.

    Формат: {"args": ["a", "b"], "returns": "int", "raises": ["ValueError"]}.
    Пустые списки/строки опускаются. Если все три пусты — None.
    """
    doc = ast.get_docstring(node, clean=True)
    if not doc:
        return None
    args = _extract_block_names(doc, _ARGS_HEADERS)
    returns = _extract_returns(doc)
    raises = _extract_block_names(doc, _RAISES_HEADERS)
    if not (args or returns or raises):
        return None
    payload: dict[str, object] = {}
    if args:
        payload["args"] = args
    if returns:
        payload["returns"] = returns
    if raises:
        payload["raises"] = raises
    return json.dumps(payload, ensure_ascii=False)


# ── Парсер ────────────────────────────────────────────────────────────────

class PythonAstParser:
    """Парсер одного .py-файла в RawNode/RawEdge."""

    def parse(self, path: str, source: str) -> ParseResult:
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as e:
            return ParseResult(error=f"syntax error at line {e.lineno}: {e.msg}")

        result = ParseResult()
        module_qname = path.removesuffix(".py").replace("/", "::")
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

        # Константы (top-level NAME = <literal>)
        for node in tree.body:
            if isinstance(node, ast.Assign):
                # NAME = value (одиночный target)
                if (len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name)
                        and isinstance(node.value, ast.Constant)):
                    self._add_constant(
                        result, module_qname, file_path,
                        node.targets[0].id, node,
                    )
            elif isinstance(node, ast.AnnAssign):
                # NAME: type = value
                if (isinstance(node.target, ast.Name)
                        and node.value is not None
                        and isinstance(node.value, ast.Constant)):
                    self._add_constant(
                        result, module_qname, file_path,
                        node.target.id, node,
                    )

        # Уникальные декораторы в файле — по одному узлу на имя.
        # Собираем до обхода, чтобы _add_function/_add_class могли
        # ссылаться на qname узла Decorator.
        decorator_names: set[str] = set()
        for node in ast.walk(tree):
            for dec in getattr(node, "decorator_list", []):
                name = _dotted_name(dec)
                if name:
                    decorator_names.add(name)

        for name in sorted(decorator_names):
            dec_qname = f"{module_qname}::{name}"
            # start/end — грубо: строка объявления первой встреченной
            # функции с этим декоратором не нужна; ставим 0/0.
            result.nodes.append(RawNode(
                kind="Decorator",
                name=name.split(".")[-1],
                qualified_name=dec_qname,
                path=file_path,
                start_line=0,
                end_line=0,
                summary=None,
            ))

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

    def _add_constant(self, result: ParseResult, module_qname: str,
                      file_path: str, name: str,
                      node: ast.Assign | ast.AnnAssign) -> None:
        qname = f"{module_qname}::{name}"
        end_line = getattr(node, "end_lineno", node.lineno) or node.lineno
        result.nodes.append(RawNode(
            kind="Constant",
            name=name,
            qualified_name=qname,
            path=file_path,
            start_line=node.lineno,
            end_line=end_line,
            summary=None,
        ))
        result.edges.append(RawEdge(
            src_qualified=module_qname,
            kind="contains",
            target_name=qname,
            line=node.lineno,
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
            contracts=None,
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
        module_qname = file_path.removesuffix(".py").replace("/", "::")
        result.nodes.append(RawNode(
            kind=kind,
            name=node.name,
            qualified_name=qname,
            path=file_path,
            start_line=node.lineno,
            end_line=node.end_lineno or node.lineno,
            summary=_first_line_docstring(node),
            contracts=_extract_contracts(node),
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
                # target — qname узла Decorator в этом же файле
                result.edges.append(RawEdge(
                    src_qualified=qname,
                    kind="decorates",
                    target_name=f"{module_qname}::{dec_name}",
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
