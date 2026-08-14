"""Pre-flight checks for a mirror slice — the ones acceptance kept having to run.

Eight slices produced five rejections, and four of them were caught by two
mechanical probes the writer could have run before pushing:

* **statements** — the SQL each mirror generates, diffed against a base SHA.
  Green test suites cannot distinguish a changed `ON CONFLICT` target, a
  reordered parameter list or a lost `ORDER BY` from correct behaviour; on
  small fixtures a wrong statement produces the right rows. Slice 7's
  acceptance found this by building the probe itself. A diff is only a diff,
  though: a table the base does not have is compared against nothing, so those
  are printed in full instead of silently counted as fine.
* **counts** — how many tests actually need PostgreSQL. Two slices shipped a
  transposed figure in a commit message, which is a false claim in a document
  that goes into history.

Run before `git push`, paste the output into the PR. The point is not
discipline; it is that a check written down is a check that survives the end of
a long slice, when discipline is what runs out first.

Usage
-----
    uv run python scripts/mirror_slice_checks.py statements --base origin/main
    uv run python scripts/mirror_slice_checks.py counts tests/db/test_batch_stores.py
    uv run python scripts/mirror_slice_checks.py all --base origin/main tests/db/...

`statements` needs no database: it records what would be sent.
"""

from __future__ import annotations

import argparse
import importlib
import json
import pkgutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

def _root() -> Path:
    """The repository this run measures — never guessed.

    An earlier version preferred the parent of `scripts/` and fell back to the
    working directory. That silently defeated the whole tool: probing an older
    SHA copies *this* script into a throwaway worktree, so `parents[1]` pointed
    back at the current checkout and the "base" run measured the head tree.
    Every table then compared IDENTICAL to itself, including four tables that
    do not exist at the base at all — a green result that meant nothing.

    So the base run is told where to look with `--root`, and there is no
    inference left to be wrong about.
    """
    for index, argument in enumerate(sys.argv):
        if argument == "--root" and index + 1 < len(sys.argv):
            return Path(sys.argv[index + 1]).resolve()
    return Path(__file__).resolve().parents[1]


ROOT = _root()


# --- recording connection ----------------------------------------------------


class _Cursor:
    def __init__(self, log: list[dict]) -> None:
        self._log = log

    def execute(self, sql: str, params: object = None) -> None:
        self._log.append({"sql": " ".join(sql.split()), "params": _renderable(params)})

    def fetchone(self):  # noqa: ANN201 - shaped for the callers below
        # `resync_identity` reads a sequence name and then a `setval` result.
        return ("public.seq",) if len(self._log) % 2 == 1 else (1,)

    def fetchall(self):  # noqa: ANN201
        return []

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _Connection:
    def __init__(self, log: list[dict]) -> None:
        self._log = log

    def cursor(self) -> _Cursor:
        return _Cursor(self._log)

    @contextmanager
    def transaction(self):  # noqa: ANN201
        yield self

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _renderable(params: object) -> object:
    """Parameters as JSON-safe values, with types kept visible.

    The type matters: a `datetime` reaching the driver where a string used to
    is precisely the kind of change this probe exists to catch, and rendering
    both as their `str()` would hide it.
    """
    if params is None:
        return None
    return [f"{type(value).__name__}:{value!r}" for value in params]


# --- probing every declared mirror -------------------------------------------


def _sample(spec: object) -> dict:
    row: dict[str, object] = {}
    for column in spec.columns:  # type: ignore[attr-defined]
        if column in spec.codec.json_values:  # type: ignore[attr-defined]
            row[column] = '{"b": 1, "a": 2}'
        elif column in spec.codec.timestamps:  # type: ignore[attr-defined]
            row[column] = "2026-08-14T00:00:00"
        elif column in spec.codec.flags:  # type: ignore[attr-defined]
            row[column] = 1
        else:
            row[column] = f"<{column}>"
    return row


def collect_statements() -> dict[str, list[dict]]:
    """Every statement every declared mirror would send, keyed by table."""
    sys.path.insert(0, str(ROOT))
    from command_center.db.table_mirror import PostgresTableMirror

    import command_center.db as db_package

    out: dict[str, list[dict]] = {}
    for module_info in pkgutil.iter_modules(db_package.__path__):
        if not module_info.name.endswith("_store"):
            continue
        module = importlib.import_module(f"command_center.db.{module_info.name}")
        for attribute in vars(module).values():
            if (
                isinstance(attribute, type)
                and issubclass(attribute, PostgresTableMirror)
                and attribute is not PostgresTableMirror
                and isinstance(getattr(attribute, "spec", None), object)
                and hasattr(attribute, "spec")
            ):
                log: list[dict] = []
                mirror = attribute(connection_factory=lambda log=log: _Connection(log))
                spec = attribute.spec
                mirror.upsert(_sample(spec))
                mirror.list_records()
                if spec.identity:
                    mirror.resync_identity()
                # Whole-predicate deletes exist on one table today; probe the
                # capability wherever a store exposes it rather than naming it.
                for extra in ("delete_day",):
                    if hasattr(mirror, extra):
                        getattr(mirror, extra)("2026-08-14")
                out[spec.table] = log
    return dict(sorted(out.items()))


def _collect_at(ref: str) -> dict[str, list[dict]]:
    """The same probe, run in a throwaway worktree at `ref`."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "tree"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(tree), ref],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        try:
            probe = tree / "scripts" / "mirror_slice_checks.py"
            if not probe.exists():
                # The tool did not exist at `ref`; run this copy against that tree.
                probe = Path(__file__)
            result = subprocess.run(
                [sys.executable, str(probe), "statements", "--emit-json", "--root", str(tree)],
                cwd=tree,
                capture_output=True,
                text=True,
                check=False,
                env={"PYTHONPATH": str(tree), "PATH": "/usr/bin:/bin"},
            )
            if result.returncode != 0:
                raise SystemExit(f"probe failed at {ref}:\n{result.stderr}")
            return json.loads(result.stdout)
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(tree)],
                cwd=ROOT,
                check=False,
                capture_output=True,
            )


def cmd_statements(args: argparse.Namespace) -> int:
    current = collect_statements()
    if args.emit_json:
        print(json.dumps(current))
        return 0
    if not args.base:
        for table, log in current.items():
            print(f"{table}: {len(log)} statements")
        return 0

    base = _collect_at(args.base)
    added = sorted(set(current) - set(base))
    removed = sorted(set(base) - set(current))
    changed = [t for t in sorted(set(current) & set(base)) if current[t] != base[t]]

    for table in sorted(set(current) & set(base)):
        print(f"{table:22s} {'IDENTICAL' if current[table] == base[table] else 'CHANGED'}")
    for table in added:
        print(f"{table:22s} NEW (not present at {args.base})")
    for table in removed:
        print(f"{table:22s} GONE (present at {args.base})")

    if changed:
        print(f"\n{len(changed)} table(s) generate different SQL than at {args.base}:\n")
        for table in changed:
            for before, after in zip(base[table], current[table], strict=False):
                if before != after:
                    print(f"  {table}\n    - {before['sql']}\n      {before['params']}")
                    print(f"    + {after['sql']}\n      {after['params']}\n")

    if added:
        # A diff has nothing to say about a table the base does not have, and
        # that is not a small corner: when a slice introduces the machinery as
        # well as its tables, *every* table is new and the comparison covers
        # nothing while still printing a reassuring list. Independent review
        # demonstrated it — a corrupted statement builder produced zero CHANGED
        # lines. So new tables get their SQL printed in full: unreviewable by
        # diff, reviewable by reading.
        print(f"\n{len(added)} new table(s) have no base to diff against — read their SQL:\n")
        for table in added:
            for entry in current[table]:
                print(f"  {table}\n    {entry['sql']}\n      {entry['params']}")
            print()
    return 1 if changed else 0


# --- test counts -------------------------------------------------------------


def cmd_counts(args: argparse.Namespace) -> int:
    """How many of a file's tests need PostgreSQL, measured rather than counted."""
    collected = subprocess.run(
        [sys.executable, "-m", "pytest", *args.paths, "--collect-only", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    total = sum(1 for line in collected.stdout.splitlines() if "::" in line)

    env_without_dsn = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home())}
    serverless = subprocess.run(
        [sys.executable, "-m", "pytest", *args.paths, "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env_without_dsn,
    )
    tail = serverless.stdout.strip().splitlines()[-1] if serverless.stdout.strip() else ""
    print(f"collected: {total}")
    print(f"without a database: {tail}")
    print("→ the PG-backed count is the 'skipped' number; the rest need no server.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    statements = sub.add_parser("statements", help="diff generated SQL against a base ref")
    statements.add_argument("--base", help="git ref to compare against (e.g. origin/main)")
    statements.add_argument("--emit-json", action="store_true", help=argparse.SUPPRESS)
    statements.add_argument("--root", help="repository to measure (used when probing a base SHA)")
    statements.set_defaults(func=cmd_statements)

    counts = sub.add_parser("counts", help="PG-backed vs serverless test split")
    counts.add_argument("paths", nargs="+")
    counts.set_defaults(func=cmd_counts)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
