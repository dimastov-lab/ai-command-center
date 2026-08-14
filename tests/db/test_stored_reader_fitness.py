"""Every mirrored table must have a reader that returns the stored shape.

Three tables have now hit the same trap. `digest_item` popped `refs_json` in
every public reader and slice 4 was rejected for shipping a reconciliation
nobody could run. `model_event` decodes the same way, and slice 6 shipped the
reader with the mirror only because slice 4's rejection was fresh. `audit_run`
decodes too, and slice 8 got it right for the same reason — memory.

Memory is what fails on the fourth table. This is the mechanical version: it
reads the authority's own source, finds which readers decode, and requires a
stored-shape reader wherever one does. A table that repeats the trap now says
so in CI instead of waiting for a reviewer to ask what an operator would call.

Deliberately structural rather than behavioural. Asking "does reconciliation
fail against the decoding reader?" needs a database and a row per table; asking
"does a stored reader exist, and does the reconciliation's own docstring point
at it?" needs neither and catches the same omission at the moment it is made.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

from command_center.db.table_mirror import PostgresTableMirror

ROOT = Path(__file__).resolve().parents[2]
AUTHORITY = ROOT / "command_center/runtime/db"


def _mirrored_tables() -> dict[str, object]:
    """Every declared mirror, keyed by table — discovered, not listed."""
    import command_center.db as db_package

    found: dict[str, object] = {}
    for module_info in pkgutil.iter_modules(db_package.__path__):
        if not module_info.name.endswith("_store"):
            continue
        module = importlib.import_module(f"command_center.db.{module_info.name}")
        for attribute in vars(module).values():
            if (
                isinstance(attribute, type)
                and issubclass(attribute, PostgresTableMirror)
                and attribute is not PostgresTableMirror
            ):
                found[attribute.spec.table] = module
    return dict(sorted(found.items()))


MIRRORED = _mirrored_tables()


def _returns_decoded(function: ast.FunctionDef) -> bool:
    """True when every row this function returns passes through a `_decode_*`."""
    for node in ast.walk(function):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id.startswith("_decode"):
                return True
    return False


def _reads_table(function: ast.FunctionDef, table: str) -> bool:
    for node in ast.walk(function):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if f"FROM {table} " in node.value or node.value.endswith(f"FROM {table}"):
                return True
            if f"FROM {table}\n" in node.value or f"FROM {table}{{" in node.value:
                return True
    return False


def _readers(table: str) -> tuple[list[str], list[str]]:
    """`(decoding, stored)` reader names across the authority package."""
    decoding: list[str] = []
    stored: list[str] = []
    for path in sorted(AUTHORITY.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not _reads_table(node, table):
                continue
            # Only the readers an operator would actually call. The first
            # version of this rule matched any function containing a `SELECT
            # ... FROM <table>`, and promptly mis-classified `append_model_event`
            # — a write path that reads `model_entry` to check the model exists
            # and returns a decoded *event* — as a decoding reader of
            # `model_entry`. A write path is not a reconciliation entry point,
            # and the convention this package follows is `get_*` / `list_*`.
            if not node.name.startswith(("get_", "list_")):
                continue
            (decoding if _returns_decoded(node) else stored).append(node.name)
    return decoding, stored


@pytest.mark.parametrize("table", list(MIRRORED), ids=list(MIRRORED))
def test_a_table_whose_readers_decode_has_a_stored_reader(table: str) -> None:
    """The rule, stated once instead of remembered per slice.

    A decoding reader is fine — it is the right default for callers. What is
    not fine is *only* decoding readers: reconciliation compares what the
    authority stores against what the mirror stores, so fed a decoded row it
    reports every row divergent on the converted column, and the failure looks
    like a broken mirror rather than a wrong question.
    """
    decoding, stored = _readers(table)
    if not decoding:
        pytest.skip(f"{table}: no reader decodes, so any of them serves reconciliation")
    assert stored, (
        f"{table}: every reader decodes ({', '.join(sorted(decoding))}) and none returns the "
        "stored shape — reconciliation has no entry point. Add a `*_stored` reader beside them "
        "(see list_digest_items_stored / list_model_events_stored / list_audit_runs_stored)."
    )


@pytest.mark.parametrize("table", list(MIRRORED), ids=list(MIRRORED))
def test_the_reconciliation_points_at_the_stored_reader(table: str) -> None:
    """Where the trap exists, the warning must be where the mistake is made.

    An operator wiring the cutover gate reaches for the public reader first,
    because it is the one that exists. The reconciliation's own docstring is
    the last thing between them and a permanently-red gate, so it has to name
    the reader that works — and `help()` has to show it, which is why slice 8
    gave the closures their docstrings back.
    """
    decoding, stored = _readers(table)
    if not decoding:
        pytest.skip(f"{table}: nothing to warn about")

    module = MIRRORED[table]
    docs = [
        (getattr(value, "__doc__", "") or "")
        for name, value in vars(module).items()
        if name.endswith("divergence") or name == "divergence"
    ]
    assert any(reader in doc for doc in docs for reader in stored), (
        f"{table}: readers {sorted(decoding)} decode, and no reconciliation docstring in "
        f"{module.__name__} names one of {sorted(stored)}. The warning has to be readable "
        "where the mistake is made."
    )
