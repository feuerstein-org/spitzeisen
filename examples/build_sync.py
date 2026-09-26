"""
Generate the blocking weather SDK from its async implementation.

Run `python build_sync.py` to regenerate, or add `python build_sync.py --check` to CI.
Copy this script with the SDK and adjust ASYNC_DIR and SYNC_DIR for your package layout.
The development environment needs unasync and ruff, the script uses no core build helpers.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import unasync

ROOT = Path(__file__).resolve().parent
# Keep code shared by the async and sync surfaces outside these folders.
ASYNC_DIR = ROOT / "weather_sdk/_async"
SYNC_DIR = ROOT / "weather_sdk/_sync"

# unasync converts Async-prefixed class names to Sync, public exports drop the Async prefix.
# E.g. it's "Api" and "SyncApi" instead of "AsyncApi" and "SyncApi". Async is our main usecase.
REPLACEMENTS = {
    # Route generated imports to the matching module tree.
    "_async": "_sync",
    # httpx2 names its sync client `Client`.
    "AsyncClient": "Client",
    "aclose": "close",
    # Core types also use unprefixed async names.
    "SpitzeisenApi": "SyncSpitzeisenApi",
    "SpitzeisenConfig": "SyncSpitzeisenConfig",
}

HEADER = f"""# Generated from {ASYNC_DIR.relative_to(ROOT)}/ by build_sync.py -- do not edit.
# Change the async module and run `python build_sync.py`.
"""


def generate(destination: Path) -> None:
    """Convert async modules, check imports and names, then format using the project's Ruff configuration."""
    sources = sorted(ASYNC_DIR.rglob("*.py"))
    if not sources:
        msg = f"No Python modules found in {ASYNC_DIR}"
        raise RuntimeError(msg)
    rule = unasync.Rule(
        fromdir=f"{ASYNC_DIR}/",
        todir=f"{destination}/",
        additional_replacements=REPLACEMENTS,
    )
    unasync.unasync_files([str(path) for path in sources], [rule])
    for path in sorted(destination.rglob("*.py")):
        path.write_text(HEADER + path.read_text())
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "ruff", "check", "--select", "I,F", "--fix", "--quiet", str(destination)],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "ruff", "format", "--quiet", str(destination)],
        cwd=ROOT,
        check=True,
    )


def stale_files(expected: Path) -> list[Path]:
    """Find missing, changed, or extra Python modules in the generated package."""
    wanted = {path.relative_to(expected) for path in expected.rglob("*.py")}
    actual = {path.relative_to(SYNC_DIR) for path in SYNC_DIR.rglob("*.py")}
    changed = {path for path in wanted & actual if (expected / path).read_bytes() != (SYNC_DIR / path).read_bytes()}
    return sorted((wanted ^ actual) | changed)


def main() -> int:
    """Regenerate the sync package, or verify it without changing files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if generated files are stale")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
        # Preserve the package context so Ruff groups internal imports consistently.
        expected = Path(temporary) / SYNC_DIR.relative_to(ROOT)
        expected.parent.mkdir(parents=True)
        (expected.parent / "__init__.py").touch()
        generate(expected)
        if args.check:
            stale = stale_files(expected)
            if stale:
                print("Sync package is stale; run `python build_sync.py`:")
                for path in stale:
                    print(f"  {path}")
                return 1
            print("Sync package is up to date")
        else:
            shutil.rmtree(SYNC_DIR, ignore_errors=True)
            shutil.copytree(expected, SYNC_DIR)
            print(f"Generated {len(list(SYNC_DIR.rglob('*.py')))} modules in {SYNC_DIR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
