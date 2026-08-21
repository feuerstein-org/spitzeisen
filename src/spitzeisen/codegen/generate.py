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

import ast
import keyword
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from spitzeisen.codegen import params as param_tools

if TYPE_CHECKING:
    from spitzeisen.codegen.manifest import Endpoint, Manifest

TEMPLATES = Path(__file__).parent / "templates"

# Only the styles spitzeisen ships. A vendor paginating some other way declares `none` here
# and passes its own strategy on the SpitzeisenEndpointSpec; see `spitzeisen.pagination`.
PAGINATION_CLASSES = {
    "none": "NoPagination",
    "page_number": "PageNumber",
}

# Coercion helpers a generated module imports only if its parameters actually use them.
HELPER_FOR_STYLE = {
    "coerce_date(": "coerce_date",
    "coerce_choices(": "coerce_choices",
    "coerce_choice(": "coerce_choice",
    "coerce_sort(": "coerce_sort",
    "require_value(": "require_value",
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


def prune_spec(spec: dict[str, Any], manifest: Manifest) -> dict[str, Any]:
    """
    Narrow a vendor document to the paths a manifest actually implements.

    Vendors routinely publish an order of magnitude more operations than any one client
    wraps, and generating models for all of them produces thousands of lines nobody
    imports, plus name collisions when two unrelated endpoints want the same schema name.
    """
    wanted = {endpoint.path for endpoint in manifest.endpoints.values() if endpoint.generate_model}
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


def render_path_kwargs(path_params: list[param_tools.ResolvedParam]) -> str:
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


def helpers_used(rendered_calls: list[str], model_imports: list[str]) -> list[str]:
    """
    Which framework helpers a module actually calls, so imports stay honest.

    Names the client already exports are left out: a manifest is free to point `literal`
    at its own type, and importing the same name from the framework would shadow it.
    """
    joined = " ".join(rendered_calls)
    found = {helper for call, helper in HELPER_FOR_STYLE.items() if call in joined}
    always = {
        "build_header_params",
        "NoPagination",
        "serialize_query_param",
    }
    return sorted((found | always) - set(model_imports))


def render_endpoint(
    manifest: Manifest,
    endpoint: Endpoint,
    operation: dict[str, Any] | None,
    *,
    is_async: bool,
) -> str:
    """Render one endpoint module for one surface."""
    resolved = param_tools.resolve(endpoint, operation)
    query_params = [param for param in resolved if param.location == "query"]
    header_params = [param for param in resolved if param.location == "header"]
    path_params = param_tools.resolve_path_params(endpoint, operation)
    example_args = [param.name for param in path_params]
    example_args.extend(param.name for param in resolved if param.required)
    sorting = param_tools.sorting(endpoint, operation)
    page_size = param_tools.page_size(endpoint, operation)

    # A parameter shaped as a choice validates against a Literal that lives beside the
    # models, so it has to be imported too — not just the response model.
    borrowed = {
        name
        for name in (
            endpoint.sort_literal,
            endpoint.order_literal,
            *(param.literal for param in endpoint.params.values()),
            *(param.literal for param in endpoint.declared_params.values()),
        )
        if name
    }
    model_imports = sorted(borrowed)
    coerce_function_imports = sorted(
        {
            param.coerce_function
            for param in (*endpoint.params.values(), *endpoint.declared_params.values())
            if param.coerce_function
        }
    )

    # Path values are coerced too, so their helpers count towards the imports.
    calls = [param.coercion for param in (*resolved, *path_params)]
    calls.extend("require_value(" for param in resolved if param.required and param.location == "header")
    if sorting:
        calls.extend((sorting.sort.coercion, sorting.order.coercion))
    if sorting and sorting.style == "suffix":
        calls.append("coerce_sort(")
    template = "endpoint.py.jinja" if endpoint.shape == "collection" else "endpoint_single.py.jinja"
    return (
        environment()
        .get_template(template)
        .render(
            ep=endpoint,
            params=resolved,
            query_params=query_params,
            header_params=header_params,
            path_params=path_params,
            example_args=example_args,
            path_kwargs=render_path_kwargs(path_params),
            cost=endpoint.cost,
            sorting=sorting,
            page_size=page_size,
            pagination_class=PAGINATION_CLASSES[endpoint.pagination],
            helpers=helpers_used(calls, model_imports),
            # Whether an absent resource is a return value or an exception. Resolved here so
            # the policy never reaches runtime: it picks which core helper to call, and
            # `_request` keeps its unconditional "a body, or an exception" contract.
            optional=endpoint.not_found == "empty",
            base_class="AsyncSpitzeisenApi" if is_async else "SyncSpitzeisenApi",
            model_imports=model_imports,
            model_module=f"{manifest.package}.models.{endpoint.key}",
            coerce_function_imports=coerce_function_imports,
            package=manifest.package,
            client_name=manifest.client_name,
            is_async=is_async,
        )
    )


def render_public_endpoint(manifest: Manifest, endpoint: Endpoint, *, is_async: bool) -> str:
    """Render the create-once public subclass for one generated endpoint base."""
    return (
        environment()
        .get_template("endpoint_public.py.jinja")
        .render(
            ep=endpoint,
            package=manifest.package,
            prefix="Async" if is_async else "Sync",
            folder="_async" if is_async else "_sync",
        )
    )


def render_client(manifest: Manifest, *, is_async: bool) -> str:
    """Render the regenerated aggregate client base for one surface."""
    return (
        environment()
        .get_template("client.py.jinja")
        .render(
            manifest=manifest,
            prefix="Async" if is_async else "Sync",
            surface="awaitable" if is_async else "blocking",
            folder="_async" if is_async else "_sync",
            is_async=is_async,
        )
    )


def render_public_client(manifest: Manifest, *, is_async: bool) -> str:
    """Render the create-once public subclass for one generated aggregate client base."""
    return (
        environment()
        .get_template("client_public.py.jinja")
        .render(
            manifest=manifest,
            package=manifest.package,
            prefix="Async" if is_async else "Sync",
            folder="_async" if is_async else "_sync",
        )
    )


def render_public_model(manifest: Manifest, endpoint: Endpoint) -> str:
    """Render one create-once public response-model subclass."""
    return (
        environment()
        .get_template("model_public.py.jinja")
        .render(
            ep=endpoint,
            package=manifest.package,
        )
    )


def generated_model_names(source: str) -> list[str]:
    """Return public class names emitted by datamodel-code-generator in source order."""
    tree = ast.parse(source)
    return [node.name for node in tree.body if isinstance(node, ast.ClassDef) and not node.name.startswith("_")]


def render_model_exports(manifest: Manifest, model_names: list[str]) -> str:
    """Render the regenerated public facade for generated and extended schema models."""
    public_modules = {
        endpoint.model: endpoint.key
        for endpoint in manifest.endpoints.values()
        if endpoint.generate_model and endpoint.model in model_names
    }
    generated_names = sorted(name for name in model_names if name not in public_modules)
    public_models = sorted(public_modules.items())
    return (
        environment()
        .get_template("model_exports.py.jinja")
        .render(
            package=manifest.package,
            generated_names=generated_names,
            public_models=public_models,
            exported_names=sorted(model_names),
        )
    )


def model_exports_module(manifest: Manifest, package_root: Path, model_source: str) -> GeneratedModule:
    """Build the regenerated facade that gives every schema class a public import path."""
    return formatted_module(
        package_root / "models" / "_exports.py",
        render_model_exports(manifest, generated_model_names(model_source)),
    )


def model_extension_modules(manifest: Manifest, package_root: Path) -> list[GeneratedModule]:
    """Render package markers and one public response-model extension point per generated model."""
    modules = [
        formatted_module(
            package_root / "models" / "__init__.py",
            (
                '"""Public schema models and client-owned parameter types."""\n\n'
                f"from {manifest.package}.models._exports import *  # noqa: F403\n"
            ),
            create_once=True,
        ),
    ]
    modules.extend(
        formatted_module(
            package_root / "models" / f"{endpoint.key}.py",
            render_public_model(manifest, endpoint),
            create_once=True,
        )
        for endpoint in manifest.endpoints.values()
        if endpoint.generate_model
    )
    return modules


def generate(manifest: Manifest, spec: dict[str, Any] | None, package_root: Path) -> list[GeneratedModule]:
    """
    Render every module for a manifest.

    `spec` is optional: without one, parameters come from the manifest's `declared_params`,
    which is what lets an API that publishes no OpenAPI document generate the same client.
    """
    manifest.resolve(spec)
    paths: dict[str, Any] = (spec or {}).get("paths", {})
    modules: list[GeneratedModule] = []

    for key, endpoint in manifest.endpoints.items():
        path_item = paths.get(endpoint.path, {})
        operation: dict[str, Any] | None = path_item.get("get") if spec else None
        if operation is not None and spec is not None:
            operation = {
                **operation,
                "parameters": param_tools.effective_parameters(path_item, operation, spec),
            }
        for is_async in (True, False):
            folder = "_async" if is_async else "_sync"
            generated_path = package_root / folder / "_generated" / f"{key}.py"
            modules.append(
                formatted_module(
                    generated_path,
                    render_endpoint(manifest, endpoint, operation, is_async=is_async),
                ),
            )
            public_path = package_root / folder / f"{key}.py"
            modules.append(
                formatted_module(
                    public_path,
                    render_public_endpoint(manifest, endpoint, is_async=is_async),
                    create_once=True,
                ),
            )

    modules.extend(model_extension_modules(manifest, package_root))

    for is_async in (True, False):
        folder = "_async" if is_async else "_sync"
        modules.extend(
            (
                formatted_module(
                    package_root / folder / "_generated" / "client.py",
                    render_client(manifest, is_async=is_async),
                ),
                formatted_module(
                    package_root / folder / "client.py",
                    render_public_client(manifest, is_async=is_async),
                    create_once=True,
                ),
            ),
        )

    return modules
