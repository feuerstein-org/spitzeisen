"""Make Pydantic-emitted response constraints selectable through validation context."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from itertools import accumulate
from typing import Any, cast

_CONSTRAINTS = frozenset({"ge", "gt", "le", "lt", "multiple_of", "min_length", "max_length", "pattern"})
_ANNOTATED = "_SpitzeisenAnnotated"
_VALIDATOR = "_spitzeisen_response_constraints"


def runtime_response_constraints(source: str) -> str:
    """
    Retain Pydantic's field rules as optional validators, including nested annotations.

    The model backend owns schema interpretation and field naming. This pass only rewrites
    its Python annotations: aliases/defaults stay on Field, constraints become conditional,
    and enum annotations expose their underlying string/integer type in every mode.
    """
    tree = ast.parse(source)
    annotations = _Annotations()
    encoded = source.encode()
    offsets = list(accumulate((len(line) for line in encoded.splitlines(keepends=True)), initial=0))
    edits: list[tuple[int, int, bytes]] = []

    def replace(original: ast.expr | ast.stmt, updated: ast.expr | ast.stmt) -> None:
        start = offsets[original.lineno - 1] + original.col_offset
        end = offsets[cast("int", original.end_lineno) - 1] + cast("int", original.end_col_offset)
        edits.append((start, end, ast.unparse(ast.fix_missing_locations(updated)).encode()))

    for node in _annotation_nodes(tree):
        previous = ast.dump(node)
        updated = cast("ast.expr | ast.stmt", annotations.visit(node))
        if ast.dump(updated) != previous:
            replace(node, updated)
    if not annotations.changed:
        return source
    # Edit annotation spans only, retaining the backend's comments and field documentation.
    # AST columns count UTF-8 bytes, so the offsets and edits use bytes too.
    imports = (
        f"from typing import Annotated as {_ANNOTATED}\n"
        f"from spitzeisen.models import response_constraints as {_VALIDATOR}\n"
    ).encode()
    index = max(
        (offsets[cast("int", node.end_lineno)] for node in tree.body if isinstance(node, ast.Import | ast.ImportFrom)),
        default=0,
    )
    edits.append((index, index, imports))
    for start, end, replacement in sorted(edits, reverse=True):
        encoded = encoded[:start] + replacement + encoded[end:]
    return encoded.decode()


def _annotation_nodes(tree: ast.Module) -> Iterator[ast.expr | ast.AnnAssign]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            yield from node.bases
        elif isinstance(node, ast.AnnAssign):
            yield node
        elif (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and _named(node.value.func, "TypeAliasType")
        ):
            yield node.value


def _named(node: ast.expr, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _annotated(annotation: ast.expr, metadata: ast.expr) -> ast.Subscript:
    return ast.Subscript(
        value=ast.Name(id=_ANNOTATED, ctx=ast.Load()),
        slice=ast.Tuple(elts=[annotation, metadata], ctx=ast.Load()),
        ctx=ast.Load(),
    )


def _validator(keywords: list[ast.keyword]) -> ast.Call:
    return ast.Call(func=ast.Name(id=_VALIDATOR, ctx=ast.Load()), args=[], keywords=keywords)


class _Annotations(ast.NodeTransformer):
    changed = False

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AnnAssign:
        node.annotation = cast("ast.expr", self.visit(node.annotation))
        if isinstance(node.value, ast.Call) and _named(node.value.func, "Field"):
            constraints = self.extract(node.value)
            if constraints:
                node.annotation = _annotated(node.annotation, _validator(constraints))
        return node

    def extract(self, field: ast.Call) -> list[ast.keyword]:
        constraints = [keyword for keyword in field.keywords if keyword.arg in _CONSTRAINTS]
        field.keywords = [keyword for keyword in field.keywords if keyword.arg not in _CONSTRAINTS]
        self.changed |= bool(constraints)
        return constraints

    def visit_Subscript(self, node: ast.Subscript) -> ast.expr:
        if _named(node.value, "Literal"):
            elements = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            values: list[Any] = [ast.literal_eval(element) for element in elements]
            non_null: list[Any] = [value for value in values if value is not None]
            types: set[type[Any]] = {type(value) for value in non_null}
            if types not in ({str}, {int}):
                msg = "response enum values must be strings or integers"
                raise ValueError(msg)
            annotation: ast.expr = ast.Name(id="str" if types == {str} else "int", ctx=ast.Load())
            if None in values:
                annotation = ast.BinOp(left=annotation, op=ast.BitOr(), right=ast.Constant(value=None))
            self.changed = True
            choices = [element for element, value in zip(elements, values, strict=True) if value is not None]
            return _annotated(
                annotation,
                _validator([ast.keyword(arg="choices", value=ast.Tuple(elts=choices, ctx=ast.Load()))]),
            )
        node = cast("ast.Subscript", self.generic_visit(node))
        if _named(node.value, "Annotated") and isinstance(node.slice, ast.Tuple):
            for metadata in list(node.slice.elts[1:]):
                if isinstance(metadata, ast.Call) and _named(metadata.func, "Field"):
                    constraints = self.extract(metadata)
                    if constraints:
                        node.slice.elts.append(_validator(constraints))
        return node
