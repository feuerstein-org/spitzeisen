"""
Rendering the generated client.

One template produces both surfaces: the awaitable and blocking variants differ by more than
`async`/`await` — the usage example and the wording of the summary have to change too — and a
mechanical source transform cannot rewrite a docstring. That is why endpoints are generated
twice rather than desugared, while spitzeisen's own hand-written core uses unasync.

Regenerated implementation modules expose `...Base` classes. Beside each one, generation
scaffolds a public subclass exactly once; that public module belongs to the client package and
is never overwritten. Aggregate client wiring follows the same generated-base/public-subclass
split, so adding an endpoint refreshes the wiring without erasing hand-written behaviour.
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

from spitzeisen.codegen.document import OpenAPIDocument
from spitzeisen.codegen.lower import lower_document
from spitzeisen.codegen.policy import ClientPlan, EndpointPlan, ParameterPlan, compile_manifest

if TYPE_CHECKING:
    from spitzeisen.codegen.manifest import Endpoint, Manifest

TEMPLATES = Path(__file__).parent / "templates"

# Only the styles spitzeisen ships. A vendor paginating some other way declares `none` here
# and passes its own strategy on the SpitzeisenEndpointSpec; see `spitzeisen.pagination`.
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


def prune_spec(spec: dict[str, Any], manifest: Manifest | ClientPlan) -> dict[str, Any]:
    """
    Narrow a vendor document to the paths a manifest actually implements.

    Vendors routinely publish an order of magnitude more operations than any one client
    wraps, and generating models for all of them produces thousands of lines nobody
    imports, plus name collisions when two unrelated endpoints want the same schema name.
    """
    endpoints = manifest.endpoints if isinstance(manifest, ClientPlan) else tuple(manifest.endpoints.values())
    wanted = {endpoint.path for endpoint in endpoints if endpoint.generate_model}
    return {
        **spec,
        "paths": {path: item for path, item in spec.get("paths", {}).items() if path in wanted},
    }


def format_python(source: str, filename: str = "generated.py") -> str:
    """
    Run generated source through ruff, exactly as the rest of the tree is formatted.

    Formatting belongs to generation rather than to whoever writes the file: `check` compares
    what generation produces against what is committed, and those two can only be compared if
    both have been through the same formatter. Unused imports are pruned here too, which lets
    the template emit a generous import block instead of computing the exact set in Jinja.
    """
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


def render_path_kwargs(path_params: tuple[ParameterPlan, ...]) -> str:
    """
    Render path values as keyword arguments to the request helper.

    A vendor is free to call a placeholder `{from}`, which cannot be a Python keyword
    argument, so any such name forces the whole group into dictionary-unpacking form. The
    common case stays readable.
    """
    if not path_params:
        return ""
    unsafe = [p for p in path_params if keyword.iskeyword(p.wire_name) or not p.wire_name.isidentifier()]
    if unsafe:
        pairs = ", ".join(f'"{p.wire_name}": {p.coercion}' for p in path_params)
        return f"**{{{pairs}}},"
    return "".join(f"\n            {p.wire_name}={p.coercion}," for p in path_params)


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


def render_endpoint(
    client: ClientPlan,
    endpoint: EndpointPlan,
    *,
    is_async: bool,
) -> str:
    """Render one endpoint module for one surface."""
    template = "endpoint.py.jinja" if endpoint.shape == "collection" else "endpoint_single.py.jinja"
    return (
        environment()
        .get_template(template)
        .render(
            ep=endpoint,
            params=endpoint.params,
            query_params=endpoint.query_params,
            header_params=endpoint.header_params,
            path_params=endpoint.path_params,
            example_args=endpoint.example_args,
            path_kwargs=render_path_kwargs(endpoint.path_params),
            cost=endpoint.cost,
            sorting=endpoint.sorting,
            page_size=endpoint.page_size,
            pagination_class=PAGINATION_CLASSES[endpoint.pagination],
            helpers=endpoint.helpers,
            # Whether an absent resource is a return value or an exception. Resolved here so
            # the policy never reaches runtime: it picks which core helper to call, and
            # `_request` keeps its unconditional "a body, or an exception" contract.
            optional=endpoint.not_found == "empty",
            base_class="AsyncSpitzeisenApi" if is_async else "SyncSpitzeisenApi",
            model_imports=endpoint.model_imports,
            model_module=f"{client.package}.models.{endpoint.key}",
            coerce_function_imports=endpoint.coerce_function_imports,
            package=client.package,
            client_name=client.client_name,
            is_async=is_async,
        )
    )


def render_public_endpoint(client: ClientPlan, endpoint: EndpointPlan, *, is_async: bool) -> str:
    """Render the create-once public subclass for one generated endpoint base."""
    return (
        environment()
        .get_template("endpoint_public.py.jinja")
        .render(
            ep=endpoint,
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


def render_public_model(package: str, endpoint: EndpointPlan | Endpoint) -> str:
    """Render one create-once public response-model subclass."""
    return (
        environment()
        .get_template("model_public.py.jinja")
        .render(
            ep=endpoint,
            package=package,
        )
    )


def generated_model_names(source: str) -> list[str]:
    """Return public class names emitted by datamodel-code-generator in source order."""
    tree = ast.parse(source)
    return [node.name for node in tree.body if isinstance(node, ast.ClassDef) and not node.name.startswith("_")]


def render_model_exports(manifest: Manifest | ClientPlan, model_names: list[str]) -> str:
    """Render the regenerated public facade for generated and extended schema models."""
    package = manifest.package
    endpoints = manifest.endpoints if isinstance(manifest, ClientPlan) else tuple(manifest.endpoints.values())
    public_modules = {
        endpoint.model: endpoint.key
        for endpoint in endpoints
        if endpoint.generate_model and endpoint.model in model_names
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


def model_exports_module(manifest: Manifest | ClientPlan, package_root: Path, model_source: str) -> GeneratedModule:
    """Build the regenerated facade that gives every schema class a public import path."""
    return formatted_module(
        package_root / "models" / "_exports.py",
        render_model_exports(manifest, generated_model_names(model_source)),
    )


def model_extension_modules(manifest: Manifest | ClientPlan, package_root: Path) -> list[GeneratedModule]:
    """Render package markers and one public response-model extension point per generated model."""
    package = manifest.package
    endpoints = manifest.endpoints if isinstance(manifest, ClientPlan) else tuple(manifest.endpoints.values())
    modules = [
        formatted_module(
            package_root / "models" / "__init__.py",
            (
                '"""Public schema models and client-owned parameter types."""\n\n'
                f"from {package}.models._exports import *  # noqa: F403\n"
            ),
            create_once=True,
        ),
    ]
    modules.extend(
        formatted_module(
            package_root / "models" / f"{endpoint.key}.py",
            render_public_model(package, endpoint),
            create_once=True,
        )
        for endpoint in endpoints
        if endpoint.generate_model
    )
    return modules


def generate(manifest: Manifest, spec: dict[str, Any] | None, package_root: Path) -> list[GeneratedModule]:
    """
    Render every module for a manifest.

    `spec` is optional: without one, parameters come from the manifest's `declared_params`,
    which is what lets an API that publishes no OpenAPI document generate the same client.
    """
    document = lower_document(OpenAPIDocument.from_mapping(spec)) if spec is not None else None
    client = compile_manifest(manifest, document)
    return generate_plan(client, package_root)


def generate_plan(client: ClientPlan, package_root: Path) -> list[GeneratedModule]:
    """Render every endpoint and extension module from an already compiled plan."""
    modules: list[GeneratedModule] = []
    for is_async in (True, False):
        folder = "_async" if is_async else "_sync"
        label = "Asynchronous" if is_async else "Synchronous"
        modules.extend(
            (
                formatted_module(
                    package_root / folder / "_generated" / "__init__.py",
                    f'"""{label} generated implementation; do not edit."""\n',
                ),
                formatted_module(
                    package_root / folder / "__init__.py",
                    f'"""{label} API surface."""\n',
                    create_once=True,
                ),
            ),
        )

    for endpoint in client.endpoints:
        for is_async in (True, False):
            folder = "_async" if is_async else "_sync"
            generated_path = package_root / folder / "_generated" / f"{endpoint.key}.py"
            modules.append(
                formatted_module(
                    generated_path,
                    render_endpoint(client, endpoint, is_async=is_async),
                ),
            )
            public_path = package_root / folder / f"{endpoint.key}.py"
            modules.append(
                formatted_module(
                    public_path,
                    render_public_endpoint(client, endpoint, is_async=is_async),
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
