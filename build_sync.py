"""
Generate the sync version in `src/spitzeisen/_sync/` from `src/spitzeisen/_async/`.

Run via `mise run build-sync`, `mise run check-sync` fails instead of writing,
which is what CI uses to prove the committed `_sync` tree is not stale.

Only mechanically convertible code belongs in `_async/`. Anything genuinely concurrent - see
`spitzeisen/concurrency.py` - is written by hand for each surface and lives outside this tree.
"""

import argparse
import filecmp
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import unasync

ROOT = Path(__file__).parent
ASYNC_DIR = ROOT / "src/spitzeisen/_async"
SYNC_DIR = ROOT / "src/spitzeisen/_sync"

# Only the names unasync cannot infer for itself.
REPLACEMENTS = {
    # Import paths. unasync built-in rule is uppercase-only, so this one is spelled out.
    "_async": "_sync",
    # httpx2 names its sync client `Client`
    "AsyncClient": "Client",
    # works only for `asyncio.sleep(...)` -> `time.sleep(...)`, unasync or pyright should fail once for example
    # asyncio.Semaphore is being used.
    "asyncio": "time",
    "aclose": "close",
}

HEADER = """# Generated from src/spitzeisen/_async/ by build_sync.py -- do not edit.
# Change the async module and run `mise run build-sync`.
"""


def generate(todir: Path) -> None:
    """
    Transform every module under `_async/` into `todir`, then tidy the result.

    Converting leaves imports in their async order, so the output goes through ruff afterwards. Generated code that is
    formatted like the rest of the tree keeps its diffs reviewable.
    """
    rule = unasync.Rule(
        fromdir=f"{ASYNC_DIR}/",
        todir=f"{todir}/",
        additional_replacements=REPLACEMENTS,
    )
    unasync.unasync_files([str(path) for path in sorted(ASYNC_DIR.rglob("*.py"))], [rule])
    for path in sorted(todir.rglob("*.py")):
        path.write_text(HEADER + path.read_text())
    # TODO: I believe sometimes the whole file needs to be reformatted not just imports.
    subprocess.run(["ruff", "check", "--select", "I", "--fix", "--quiet", str(todir)], check=False)  # noqa: S603, S607
    subprocess.run(["ruff", "format", "--quiet", str(todir)], check=False)  # noqa: S603, S607


def check() -> int:
    """Regenerate into a temporary tree and report whether the committed one matches, used in CI."""
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        expected = Path(tmp) / "_sync"
        generate(expected)
        stale = [
            path.relative_to(expected)
            for path in sorted(expected.rglob("*.py"))
            if not (SYNC_DIR / path.relative_to(expected)).exists()
            or not filecmp.cmp(path, SYNC_DIR / path.relative_to(expected), shallow=False)
        ]
    if stale:
        print("_sync is stale; run `mise run build-sync`:")  # noqa: T201
        for path in stale:
            print(f"  {path}")  # noqa: T201
        return 1
    print("_sync is up to date")  # noqa: T201
    return 0


def main() -> int:
    """Generate the sync tree, or verify the committed one is current."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if _sync is stale instead of writing")
    args = parser.parse_args()
    if args.check:
        return check()
    shutil.rmtree(SYNC_DIR, ignore_errors=True)
    generate(SYNC_DIR)
    print(f"generated {len(list(SYNC_DIR.rglob('*.py')))} modules in {SYNC_DIR.relative_to(ROOT)}")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
