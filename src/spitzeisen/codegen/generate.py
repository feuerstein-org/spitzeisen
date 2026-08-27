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
import keyword
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

if TYPE_CHECKING:
    from spitzeisen.codegen.policy import ClientPlan, OperationPlan, ParamPlan

TEMPLATES = Path(__file__).parent / "templates"

# Only the styles spitzeisen ships. A vendor paginating some other way declares `none` here
# and passes its own strategy on the SpitzeisenOperationSpec; see `spitzeisen.pagination`.
PAGINATION_CLASSES = {
    "none": "NoPagination",
    "page_number": "PageNumber",
}

ARG_INDENT = " " * 12
LINE_WIDTH = 116


@dataclass(frozen=True, slots=True)
class GeneratedModule:
    """One rendered file and where it belongs."""

    path: Path
    source: str
    # Public extension modules are scaffolded once and then belong to the client package.
    # Regeneration must keep any hand-written behaviour added to them.
    create_once: bool = False


def formatted_module(path: Path, source: str, *, create_once: bool = False) -> GeneratedModule:
    """Build one module, formatting it with its real destination for import classification."""
    return GeneratedModule(path, format_python(source, str(path)), create_once=create_once)


def prune_spec(spec: dict[str, Any], client: ClientPlan) -> dict[str, Any]:
    """Narrow a vendor document to the operations in the compiled Smithy service closure."""
    wanted = {operation.path for operation in client.operations if operation.generate_model}
    return {
        **spec,
        "paths": {path: item for path, item in spec.get("paths", {}).items() if path in wanted},
    }


def format_python(source: str, filename: str = "generated.py") -> str:
    """Run generated source through ruff, exactly as the rest of the tree is formatted."""
    for argv in (
        ["ruff", "check", "--select", "I,F401", "--fix", "--quiet", "--stdin-filename", filename, "-"],
        ["ruff", "format", "--quiet", "--stdin-filename", filename, "-"],
    ):
        result = subprocess.run(argv, input=source, capture_output=True, text=True, check=False)  # noqa: S603
        if result.returncode == 0 and result.stdout:
            source = result.stdout
    return source


def wrap_arg(description: str, name: str) -> str:
    """Render one `Args:` entry, wrapped to the project's line length."""
    return textwrap.fill(
        f"{name}: {description}",
        width=LINE_WIDTH,
        initial_indent=ARG_INDENT,
        subsequent_indent=ARG_INDENT + "    ",
    )


def render_path_kwargs(path_params: tuple[ParamPlan, ...]) -> str:
    """Validate and render path values as keyword arguments."""
    if not path_params:
        return ""
    unsafe = [p for p in path_params if keyword.iskeyword(p.wire_name) or not p.wire_name.isidentifier()]
    if unsafe:
        pairs = ", ".join(f'"{p.wire_name}": require_value({p.coercion}, "{p.name}")' for p in path_params)
        return f"**{{{pairs}}},"
    return "".join(f'\n            {p.wire_name}=require_value({p.coercion}, "{p.name}"),' for p in path_params)


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
    return env


def render_operation(
    client: ClientPlan,
    operation: OperationPlan,
    *,
    is_async: bool,
) -> str:
    """Render one operation module for one surface."""
    template = "operation.py.jinja" if operation.shape == "collection" else "operation_single.py.jinja"
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
            helpers=operation.helpers,
            # If true return type gets " | None" appended
            not_found_is_empty=operation.not_found == "empty",
            base_class="AsyncSpitzeisenApi" if is_async else "SyncSpitzeisenApi",
            model_imports=operation.model_imports,
            model_module=f"{client.package}.models.{operation.key}",
            coerce_function_imports=operation.coerce_function_imports,
            package=client.package,
            client_name=client.client_name,
            is_async=is_async,
        )
    )


def render_public_operation(client: ClientPlan, operation: OperationPlan, *, is_async: bool) -> str:
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


def render_client(client: ClientPlan, *, is_async: bool) -> str:
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


def render_public_client(client: ClientPlan, *, is_async: bool) -> str:
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


def render_public_model(package: str, operation: OperationPlan) -> str:
    """Render one create-once public response-model subclass."""
    return (
        environment()
        .get_template("model_public.py.jinja")
        .render(
            operation=operation,
            package=package,
        )
    )


def generated_model_names(source: str) -> list[str]:
    """Return public class names emitted by datamodel-code-generator in source order."""
    tree = ast.parse(source)
    return [node.name for node in tree.body if isinstance(node, ast.ClassDef) and not node.name.startswith("_")]


def render_model_exports(client: ClientPlan, model_names: list[str]) -> str:
    """Render the regenerated public facade for generated and extended schema models."""
    package = client.package
    public_modules = {
        operation.model: operation.key
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


def model_exports_module(client: ClientPlan, package_root: Path, model_source: str) -> GeneratedModule:
    """Build the regenerated facade that gives every schema class a public import path."""
    return formatted_module(
        package_root / "models" / "_exports.py",
        render_model_exports(client, generated_model_names(model_source)),
    )


def model_extension_modules(client: ClientPlan, package_root: Path) -> list[GeneratedModule]:
    """Render package markers and one public response-model extension point per generated model."""
    package = client.package
    modules = [
        formatted_module(
            package_root / "models" / "__init__.py",
            (
                '"""Public schema models and client-owned param types."""\n\n'
                f"from {package}.models._exports import *  # noqa: F403\n"
            ),
            create_once=True,
        ),
    ]
    modules.extend(
        formatted_module(
            package_root / "models" / f"{operation.key}.py",
            render_public_model(package, operation),
            create_once=True,
        )
        for operation in client.operations
        if operation.generate_model
    )
    return modules


def generate_plan(client: ClientPlan, package_root: Path) -> list[GeneratedModule]:
    """Render every operation and extension module from a compiled plan."""
    modules: list[GeneratedModule] = []
    for is_async in (True, False):
        folder = "_async" if is_async else "_sync"
        label = "Asynchronous" if is_async else "Synchronous"
        modules.extend(
            (
                formatted_module(
                    package_root / folder / "_generated" / "__init__.py",
                    f'"""{label} generated implementation, do not edit."""\n',
                ),
                formatted_module(
                    package_root / folder / "__init__.py",
                    f'"""{label} API surface."""\n',
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
                ),
            )
            public_path = package_root / folder / f"{operation.key}.py"
            modules.append(
                formatted_module(
                    public_path,
                    render_public_operation(client, operation, is_async=is_async),
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
                ),
                formatted_module(
                    package_root / folder / "client.py",
                    render_public_client(client, is_async=is_async),
                    create_once=True,
                ),
            ),
        )

    return modules
