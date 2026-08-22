"""The unified, reproducible code-generation command."""

from pathlib import Path

import yaml
from click.testing import CliRunner

from spitzeisen.codegen.cli import main


def manifest_only_project(root: Path) -> tuple[Path, Path]:
    """Create inputs that exercise endpoint generation without a model backend subprocess."""
    spec_dir = root / "spec"
    package_root = root / "example_sdk"
    spec_dir.mkdir()
    (package_root / "models").mkdir(parents=True)
    (package_root / "models" / "things.py").write_text(
        "from spitzeisen import SpitzeisenModel\n\nclass Thing(SpitzeisenModel):\n    pass\n",
    )
    (spec_dir / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "vendor": "example",
                "base_url": "https://api.example.test",
                "package": "example_sdk",
                "client_name": "ExampleApi",
                "endpoints": {
                    "things": {
                        "path": "/things",
                        "method_name": "get_things",
                        "model": "Thing",
                        "generate_model": False,
                        "declared_params": {"category": {"description": "Category to return."}},
                    },
                },
            },
        ),
    )
    return spec_dir, package_root


def test_cli_exposes_one_generation_transaction_and_one_drift_check(tmp_path: Path) -> None:
    """SDK authors do not have to coordinate independent model and endpoint phases."""
    spec_dir, package_root = manifest_only_project(tmp_path)
    runner = CliRunner()

    help_result = runner.invoke(main, ["--help"])
    generated = runner.invoke(
        main,
        ["generate", "--spec-dir", str(spec_dir), "--package-root", str(package_root)],
    )
    checked = runner.invoke(
        main,
        ["check", "--spec-dir", str(spec_dir), "--package-root", str(package_root)],
    )

    assert help_result.exit_code == 0
    assert "generate" in help_result.output
    assert "check" in help_result.output
    assert "\n  models " not in help_result.output
    assert "\n  endpoints " not in help_result.output
    assert generated.exit_code == 0, generated.output
    assert "generated 13 files" in generated.output
    assert checked.exit_code == 0, checked.output
    assert "13 generated or scaffolded modules are up to date" in checked.output


def test_check_catches_modified_and_orphaned_generated_modules(tmp_path: Path) -> None:
    """CI checks both byte drift and files left behind after an endpoint is removed."""
    spec_dir, package_root = manifest_only_project(tmp_path)
    runner = CliRunner()
    arguments = ["--spec-dir", str(spec_dir), "--package-root", str(package_root)]
    assert runner.invoke(main, ["generate", *arguments]).exit_code == 0

    generated = package_root / "_async" / "_generated" / "things.py"
    generated.write_text("# hand edited generated code\n")
    orphan = package_root / "_sync" / "_generated" / "removed_endpoint.py"
    orphan.write_text("# stale\n")

    result = runner.invoke(main, ["check", *arguments])

    assert result.exit_code == 1
    assert str(generated) in result.output
    assert str(orphan) in result.output
