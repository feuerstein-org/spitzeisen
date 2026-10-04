"""
Generate the sync version in `src/spitzeisen/_sync/` from `src/spitzeisen/_async/`.

Run via `mise run build-sync`, `mise run check-sync` fails instead of writing,
which is what CI uses to prove the committed `_sync` tree is not stale.

Only mechanically convertible code belongs in `_async/`. Anything genuinely concurrent - see
`spitzeisen/concurrency.py` - is written by hand for each surface and lives outside this tree.
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
ASYNC_DIR = ROOT / "src/spitzeisen/_async"
SYNC_DIR = ROOT / "src/spitzeisen/_sync"

# unasync converts Async-prefixed class names to Sync, public exports drop the Async prefix.
# E.g. it's "Api" and "SyncApi" instead of "AsyncApi" and "SyncApi". Async is our main usecase.
REPLACEMENTS = {
    # Route generated imports to the matching module tree.
    "_async": "_sync",
    # httpx2 names its sync client `Client`.
    "AsyncClient": "Client",
    # Only asyncio.sleep is used in the convertible modules.
    "asyncio": "time",
    "aclose": "close",
}

HEADER = f"""# Generated from {ASYNC_DIR.relative_to(ROOT)}/ by build_sync.py -- do not edit.
# Change the async module and run `python build_sync.py`.
"""


def specialize_sync_config(path: Path) -> None:
    """Replace the async no-op state guard with the real lock required by blocking callers."""
    source = path.read_text()
    marker = "from contextlib import nullcontext"
    if marker not in source:
        msg = "generated sync config is missing the async state-guard marker"
        raise RuntimeError(msg)
    path.write_text(
        source.replace(marker, "from threading import Lock")
        .replace("nullcontext[None]", "Lock")
        .replace("default_factory=nullcontext", "default_factory=Lock")
    )


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
    specialize_sync_config(destination / "config.py")
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
                print("Sync package is stale; run `python build_sync.py`:")  # noqa: T201
                for path in stale:
                    print(f"  {path}")  # noqa: T201
                return 1
            print("Sync package is up to date")  # noqa: T201
        else:
            shutil.rmtree(SYNC_DIR, ignore_errors=True)
            shutil.copytree(expected, SYNC_DIR)
            print(f"Generated {len(list(SYNC_DIR.rglob('*.py')))} modules in {SYNC_DIR.relative_to(ROOT)}")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
