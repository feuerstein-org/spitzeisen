"""
Rendering the generated client.

One template produces both surfaces: the awaitable and blocking variants differ by more than
`async`/`await` — the usage example and the wording of the summary have to change too — and a
mechanical source transform cannot rewrite a docstring. That is why operations are generated
twice rather than desugared, while spitzeisen's own hand-written core uses unasync.

Regenerated implementation modules expose `...Base` classes. Beside each one, generation
scaffolds a public subclass exactly once; that public module belongs to the client package and
is never overwritten. Aggregate client wiring follows the same generated-base/public-subclass
split, so adding an operation refreshes the wiring without erasing hand-written behaviour.
"""

from __future__ import annotations

import ast
import json
import keyword
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

if TYPE_CHECKING:
    from spitzeisen.codegen.python_plan import (
        PythonCoercionPlan,
        PythonDefaultPlan,
        PythonOperationPlan,
        PythonParameterPlan,
        PythonPlan,
        PythonTypePlan,
    )

TEMPLATES = Path(__file__).parent / "templates"

# Only the styles spitzeisen ships. A vendor paginating some other way declares `none` here
# and passes its own strategy on the SpitzeisenOperationSpec; see `spitzeisen.pagination`.
PAGINATION_CLASSES = {
    "none": "NoPagination",
    "page_number": "PageNumber",
}

ARG_INDENT = " " * 12
LINE_WIDTH = 116
_MIN_PRINTABLE_CODEPOINT = 32


@dataclass(frozen=True, slots=True)
class GeneratedModule:
    """One rendered file and where it belongs."""

    path: Path
    source: str
    # Public extension modules are scaffolded once and then belong to the client package.
    # Regeneration must keep any hand-written behaviour added to them.
    create_once: bool = False


def formatted_module(
    path: Path,
    source: str,
    *,
    package: str,
    create_once: bool = False,
) -> GeneratedModule:
    """Build one module, formatting it with its real destination for import classification."""
    formatted = format_python(source, str(path), package=package)
    try:
        ast.parse(formatted, filename=str(path))
    except SyntaxError as err:
        msg = f"renderer produced invalid Python for {path}: {err.msg} (line {err.lineno})"
        raise ValueError(msg) from err
    return GeneratedModule(path, formatted, create_once=create_once)


def prune_spec(spec: dict[str, Any], client: PythonPlan) -> dict[str, Any]:
    """Narrow a vendor document to the operations in the compiled Smithy service closure."""
    wanted = {operation.path for operation in client.operations if operation.generate_model}
    return {
        **spec,
        "paths": {path: item for path, item in spec.get("paths", {}).items() if path in wanted},
    }


def format_python(source: str, filename: str = "generated.py", *, package: str | None = None) -> str:
    """Run generated source through ruff, exactly as the rest of the tree is formatted."""
    import_config = (
        [
            "--config",
            f"lint.isort.known-first-party={json.dumps([package, 'spitzeisen'])}",
        ]
        if package is not None
        else []
    )
    for argv in (
        [
            python_tool("ruff"),
            "check",
            "--select",
            "I,F401",
            "--fix",
            "--quiet",
            "--stdin-filename",
            filename,
            *import_config,
            "-",
        ],
        [python_tool("ruff"), "format", "--quiet", "--stdin-filename", filename, "-"],
    ):
        result = subprocess.run(argv, input=source, capture_output=True, text=True, check=False)  # noqa: S603
        if result.returncode == 0 and result.stdout:
            source = result.stdout
    return source


def python_tool(name: str) -> str:
    """Resolve a codegen console script, including beside the active Python executable."""
    if executable := shutil.which(name):
        return executable
    adjacent = Path(sys.executable).with_name(name)
    return str(adjacent) if adjacent.is_file() else name


def wrap_arg(description: str, name: str) -> str:
    """Render one `Args:` entry, wrapped to the project's line length."""
    return textwrap.fill(
        f"{name}: {render_docstring(description)}",
        width=LINE_WIDTH,
        initial_indent=ARG_INDENT,
        subsequent_indent=ARG_INDENT + "    ",
    )


def render_docstring(value: str) -> str:
    """Escape modeled prose for inclusion in a generated triple-quoted string."""
    escaped = value.replace("\\", "\\\\").replace('"""', '\\"""')
    return "".join(
        character if character == "\n" or ord(character) >= _MIN_PRINTABLE_CODEPOINT else f"\\x{ord(character):02x}"
        for character in escaped
    )


def render_path_kwargs(path_params: tuple[PythonParameterPlan, ...]) -> str:
    """Validate and render path values as keyword arguments."""
    if not path_params:
        return ""
    unsafe = [p for p in path_params if keyword.iskeyword(p.wire_name) or not p.wire_name.isidentifier()]
    if unsafe:
        pairs = ", ".join(
            (
                f'"{p.wire_name}": serialize_path_param('
                f'require_value({render_coercion(p.coercion, p.name)}, "{p.name}"), greedy={p.greedy!r})'
            )
            for p in path_params
        )
        return f"**{{{pairs}}},"
    return "".join(
        (
            f"\n            {p.wire_name}=serialize_path_param("
            f'require_value({render_coercion(p.coercion, p.name)}, "{p.name}"), greedy={p.greedy!r}),'
        )
        for p in path_params
    )


def render_type(type_plan: PythonTypePlan) -> str:  # noqa: PLR0911
    """Render a lowered Python type descriptor as a Python type expression."""
    primitives = {
        "bool": "bool",
        "bytes": "bytes",
        "date_input": "str | date | datetime",
        "datetime": "datetime",
        "decimal": "Decimal",
        "document": "dict[str, object]",
        "float": "float",
        "int": "int",
        "str": "str",
    }
    if type_plan.kind in primitives:
        return primitives[type_plan.kind]
    if type_plan.kind == "symbol":
        if type_plan.name is None:
            msg = f"type descriptor {type_plan.kind!r} requires a name"
            raise ValueError(msg)
        return type_plan.name
    if type_plan.kind == "literal":
        return f"Literal[{', '.join(repr(value) for value in type_plan.values)}]"
    if type_plan.kind == "list":
        return f"list[{render_type(type_plan.members[0])}]"
    if type_plan.kind == "set":
        return f"set[{render_type(type_plan.members[0])}]"
    if type_plan.kind == "dict":
        return f"dict[{render_type(type_plan.members[0])}, {render_type(type_plan.members[1])}]"
    if type_plan.kind == "union":
        return " | ".join(render_type(member) for member in type_plan.members)
    msg = f"unsupported type descriptor {type_plan.kind!r}"
    raise ValueError(msg)


def render_parameter_type(type_plan: PythonTypePlan, default: PythonDefaultPlan) -> str:
    """Render a public Python annotation, adding ``None`` for a null default only."""
    annotation = render_type(type_plan)
    return f"{annotation} | None" if default.allows_none else annotation


def render_default(default: PythonDefaultPlan) -> str:
    """Render the JSON value carried by an optional-argument default descriptor."""
    if default.is_required:
        msg = "a required argument does not have a Python default"
        raise ValueError(msg)
    return repr(default.value)


def render_coercion(coercion: PythonCoercionPlan, value_name: str) -> str:  # noqa: C901
    """Render a semantic wire-coercion descriptor as a Python runtime expression."""
    if coercion.kind == "identity":
        return value_name
    if coercion.kind == "date":
        return f"coerce_date({value_name}, {value_name!r})"
    if coercion.kind in {"timestamp", "timestamps"}:
        if coercion.timestamp_format is None:
            msg = f"{coercion.kind} coercion requires a timestamp format"
            raise ValueError(msg)
        function = "coerce_timestamp" if coercion.kind == "timestamp" else "coerce_timestamps"
        return f"{function}({value_name}, {coercion.timestamp_format!r}, {value_name!r})"
    if coercion.kind == "join":
        if coercion.separator is None:
            msg = "join coercion requires a separator"
            raise ValueError(msg)
        return f"{coercion.separator!r}.join({value_name}) if {value_name} else None"
    if coercion.kind in {"choice", "choices"}:
        if coercion.literal_type is None:
            msg = f"{coercion.kind} coercion requires a literal type"
            raise ValueError(msg)
        function = "coerce_choice" if coercion.kind == "choice" else "coerce_choices"
        return f"{function}({value_name}, {render_type(coercion.literal_type)}, {value_name!r})"
    if coercion.kind == "custom":
        if coercion.function is None:
            msg = "custom coercion requires a function"
            raise ValueError(msg)
        return f"{coercion.function.identifier}({value_name}, param_name={value_name!r})"
    msg = f"unsupported coercion descriptor {coercion.kind!r}"
    raise ValueError(msg)


def _type_references(type_plan: PythonTypePlan) -> set[tuple[str, str]]:
    """Find target symbols and their modules required by a lowered type."""
    names: set[tuple[str, str]] = set()
    if type_plan.kind == "symbol" and type_plan.name and type_plan.module:
        names.add((type_plan.module, type_plan.name))
    for member in type_plan.members:
        names.update(_type_references(member))
    return names


def _coercion_references(coercion: PythonCoercionPlan) -> set[tuple[str, str]]:
    """Find named literal types used by a coercion descriptor."""
    return set() if coercion.literal_type is None else _type_references(coercion.literal_type)


def _operation_type_imports(operation: PythonOperationPlan) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Group lowered target-symbol imports by their exact Python module."""
    names: set[tuple[str, str]] = set()
    arguments = (*operation.params, *operation.path_params)
    for parameter in arguments:
        names.update(_type_references(parameter.type))
        names.update(_coercion_references(parameter.coercion))
    if operation.sorting:
        for argument in (operation.sorting.sort, operation.sorting.order):
            names.update(_type_references(argument.type))
            names.update(_coercion_references(argument.coercion))
    names.discard((operation.model_module, operation.model))
    modules: dict[str, set[str]] = {}
    for module, name in names:
        modules.setdefault(module, set()).add(name)
    return tuple((module, tuple(sorted(module_names))) for module, module_names in sorted(modules.items()))


def _operation_imports(operation: PythonOperationPlan) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Combine exact target-symbol and input-adapter imports by module."""
    modules = {module: set(names) for module, names in _operation_type_imports(operation)}
    coercions = [parameter.coercion for parameter in (*operation.params, *operation.path_params)]
    if operation.sorting:
        coercions.extend((operation.sorting.sort.coercion, operation.sorting.order.coercion))
    for coercion in coercions:
        function = coercion.function
        if coercion.kind == "custom" and function is not None:
            imported = function.name if function.alias is None else f"{function.name} as {function.alias}"
            modules.setdefault(function.module, set()).add(imported)
    return tuple((module, tuple(sorted(names))) for module, names in sorted(modules.items()))


def _operation_helpers(operation: PythonOperationPlan) -> tuple[str, ...]:
    """Derive required runtime helpers from structured coercions and bindings."""
    helpers = {"NoPagination", "build_header_params", "serialize_query_param"}
    coercions = [parameter.coercion for parameter in (*operation.params, *operation.path_params)]
    if operation.sorting:
        coercions.extend((operation.sorting.sort.coercion, operation.sorting.order.coercion))
        if operation.sorting.style == "suffix":
            helpers.add("coerce_sort")
    helper_by_kind = {
        "date": "coerce_date",
        "choice": "coerce_choice",
        "choices": "coerce_choices",
        "timestamp": "coerce_timestamp",
        "timestamps": "coerce_timestamps",
    }
    helpers.update(helper_by_kind[coercion.kind] for coercion in coercions if coercion.kind in helper_by_kind)
    if operation.path_params or any(parameter.required for parameter in operation.header_params):
        helpers.add("require_value")
    if operation.path_params:
        helpers.add("serialize_path_param")
    return tuple(sorted(helpers))


def environment() -> Environment:
    """A Jinja environment with the filters the templates rely on."""
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=False,
        autoescape=False,  # noqa: S701 - rendering Python source, not markup
    )
    # jinja2 types these registries narrowly; they take any callable.
    filters: dict[str, Any] = env.filters
    filters["wrap_arg"] = wrap_arg
    # `tojson` would render None as `null`; generated files are Python, not JSON.
    filters["py"] = repr
    filters["py_doc"] = render_docstring
    filters["py_type"] = render_parameter_type
    filters["py_default"] = render_default
    filters["py_coercion"] = render_coercion
    return env


def render_operation(
    client: PythonPlan,
    operation: PythonOperationPlan,
    *,
    is_async: bool,
) -> str:
    """Render one operation module for one surface."""
    template = "operation.py.jinja" if operation.response_cardinality == "collection" else "operation_single.py.jinja"
    return (
        environment()
        .get_template(template)
        .render(
            operation=operation,
            params=operation.params,
            query_params=operation.query_params,
            header_params=operation.header_params,
            path_params=operation.path_params,
            example_args=operation.example_args,
            path_kwargs=render_path_kwargs(operation.path_params),
            cost=operation.cost,
            sorting=operation.sorting,
            page_size=operation.page_size,
            pagination_class=PAGINATION_CLASSES[operation.pagination],
            helpers=_operation_helpers(operation),
            # If true return type gets " | None" appended
            not_found_is_absent=operation.not_found == "absent",
            base_class="AsyncSpitzeisenApi" if is_async else "SyncSpitzeisenApi",
            imports=_operation_imports(operation),
            model_module=operation.model_module,
            package=client.package,
            client_name=client.client_name,
            is_async=is_async,
        )
    )


def render_public_operation(client: PythonPlan, operation: PythonOperationPlan, *, is_async: bool) -> str:
    """Render the create-once public subclass for one generated operation base."""
    return (
        environment()
        .get_template("operation_public.py.jinja")
        .render(
            operation=operation,
            package=client.package,
            prefix="Async" if is_async else "Sync",
            folder="_async" if is_async else "_sync",
        )
    )


def render_client(client: PythonPlan, *, is_async: bool) -> str:
    """Render the regenerated aggregate client base for one surface."""
    return (
        environment()
        .get_template("client.py.jinja")
        .render(
            client=client,
            prefix="Async" if is_async else "Sync",
            surface="asynchronous" if is_async else "synchronous",
            folder="_async" if is_async else "_sync",
            is_async=is_async,
        )
    )


def render_public_client(client: PythonPlan, *, is_async: bool) -> str:
    """Render the create-once public subclass for one generated aggregate client base."""
    return (
        environment()
        .get_template("client_public.py.jinja")
        .render(
            client=client,
            package=client.package,
            prefix="Async" if is_async else "Sync",
            folder="_async" if is_async else "_sync",
        )
    )


def render_public_package(client: PythonPlan) -> str:
    """Render the create-once package facade for clients and generated response models."""
    model_names = sorted({operation.model for operation in client.operations if operation.generate_model})
    exported_names = sorted(
        {
            f"Async{client.client_name}",
            f"Sync{client.client_name}",
            *model_names,
        },
    )
    return (
        environment()
        .get_template("package_public.py.jinja")
        .render(
            client=client,
            model_names=model_names,
            exported_names=exported_names,
        )
    )


def render_public_model(package: str, operation: PythonOperationPlan) -> str:
    """Render one create-once public response-model subclass."""
    return (
        environment()
        .get_template("model_public.py.jinja")
        .render(
            operation=operation,
            package=package,
        )
    )


def _local_module_name(client: PythonPlan, operation: PythonOperationPlan) -> str:
    """Return a generated response model's module relative to ``<package>.models``."""
    prefix = f"{client.package}.models."
    if not operation.model_module.startswith(prefix):
        msg = f"generated response model {operation.response_shape!r} has non-local module {operation.model_module!r}"
        raise ValueError(msg)
    module = operation.model_module.removeprefix(prefix)
    if not module:
        msg = f"generated response model {operation.response_shape!r} has no definition module"
        raise ValueError(msg)
    return module


def _local_module_path(
    client: PythonPlan,
    package_root: Path,
    operation: PythonOperationPlan,
) -> Path:
    """Map one exact local import module to its generated package path."""
    return package_root / "models" / Path(*_local_module_name(client, operation).split(".")).with_suffix(".py")


def generated_model_names(source: str) -> list[str]:
    """Return public class names emitted by datamodel-code-generator in source order."""
    tree = ast.parse(source)
    return [node.name for node in tree.body if isinstance(node, ast.ClassDef) and not node.name.startswith("_")]


def render_model_exports(client: PythonPlan, model_names: list[str]) -> str:
    """Render the regenerated public facade for generated and extended schema models."""
    package = client.package
    public_modules = {
        operation.model: _local_module_name(client, operation)
        for operation in client.operations
        if operation.generate_model and operation.model in model_names
    }
    generated_names = sorted(name for name in model_names if name not in public_modules)
    public_models = sorted(public_modules.items())
    return (
        environment()
        .get_template("model_exports.py.jinja")
        .render(
            package=package,
            generated_names=generated_names,
            public_models=public_models,
            exported_names=sorted(model_names),
        )
    )


def model_exports_module(client: PythonPlan, package_root: Path, model_source: str) -> GeneratedModule:
    """Build the regenerated facade that gives every schema class a public import path."""
    return formatted_module(
        package_root / "models" / "_exports.py",
        render_model_exports(client, generated_model_names(model_source)),
        package=client.package,
    )


def model_extension_modules(client: PythonPlan, package_root: Path) -> list[GeneratedModule]:
    """Render package markers and one public response-model extension point per generated model."""
    package = client.package
    modules = [
        formatted_module(
            package_root / "models" / "__init__.py",
            (
                '"""Public schema models and client-owned param types."""\n\n'
                f"from {package}.models._exports import *  # noqa: F403\n"
            ),
            package=package,
            create_once=True,
        ),
    ]
    generated_shapes: set[tuple[str, str]] = set()
    for operation in client.operations:
        identity = (operation.response_shape, operation.model_module)
        if not operation.generate_model or identity in generated_shapes:
            continue
        generated_shapes.add(identity)
        modules.append(
            formatted_module(
                _local_module_path(client, package_root, operation),
                render_public_model(package, operation),
                package=package,
                create_once=True,
            ),
        )
    return modules


def generate_plan(client: PythonPlan, package_root: Path) -> list[GeneratedModule]:
    """Render every operation and extension module from a compiled plan."""
    modules = [
        formatted_module(
            package_root / "__init__.py",
            render_public_package(client),
            package=client.package,
            create_once=True,
        ),
    ]
    for is_async in (True, False):
        folder = "_async" if is_async else "_sync"
        label = "Asynchronous" if is_async else "Synchronous"
        modules.extend(
            (
                formatted_module(
                    package_root / folder / "_generated" / "__init__.py",
                    f'"""{label} generated implementation, do not edit."""\n',
                    package=client.package,
                ),
                formatted_module(
                    package_root / folder / "__init__.py",
                    f'"""{label} API surface."""\n',
                    package=client.package,
                    create_once=True,
                ),
            ),
        )

    for operation in client.operations:
        for is_async in (True, False):
            folder = "_async" if is_async else "_sync"
            generated_path = package_root / folder / "_generated" / f"{operation.key}.py"
            modules.append(
                formatted_module(
                    generated_path,
                    render_operation(client, operation, is_async=is_async),
                    package=client.package,
                ),
            )
            public_path = package_root / folder / f"{operation.key}.py"
            modules.append(
                formatted_module(
                    public_path,
                    render_public_operation(client, operation, is_async=is_async),
                    package=client.package,
                    create_once=True,
                ),
            )

    modules.extend(model_extension_modules(client, package_root))

    for is_async in (True, False):
        folder = "_async" if is_async else "_sync"
        modules.extend(
            (
                formatted_module(
                    package_root / folder / "_generated" / "client.py",
                    render_client(client, is_async=is_async),
                    package=client.package,
                ),
                formatted_module(
                    package_root / folder / "client.py",
                    render_public_client(client, is_async=is_async),
                    package=client.package,
                    create_once=True,
                ),
            ),
        )

    return modules
