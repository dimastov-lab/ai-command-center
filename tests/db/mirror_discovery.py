"""Which classes are mirrors — asked once, answered the same way everywhere.

Two suites need this list: the contract, which enrols every mirror in the
per-table checks, and the stored-reader fitness gate, which asks the authority
package a question per mirrored table. They had a copy each, and both copies
read `command_center/db/*_store.py`.

Slice 9's acceptance showed what that costs. A mirror declared in
`command_center/db/run_mirror.py` with a deliberately wrong key was collected by
neither, passed everything, and the contract still reported seventeen tables —
the blocking defect of that slice, relocated by renaming a file. Fixing one copy
would have left the other, which is the argument for this module existing at
all: a rule with two implementations has two answers eventually.

Sources are read, not imported. `command_center/db/pool.py` imports `aios_db`,
so importing the whole package would make both suites collectible only on a
machine with a PostgreSQL client library — and the serverless configuration is
exactly where the declaration checks are the only ones still running.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

from command_center.db.table_mirror import PostgresTableMirror

__all__ = ["mirror_classes", "modules_declaring_mirrors"]


def modules_declaring_mirrors() -> list[str]:
    """Module names under `command_center.db` that subclass the mirror base."""
    import command_center.db as db_package

    package_root = Path(db_package.__path__[0])
    declaring: list[str] = []
    for path in sorted(package_root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(
                getattr(base, "id", None) == "PostgresTableMirror"
                or getattr(base, "attr", None) == "PostgresTableMirror"
                for base in node.bases
            ):
                declaring.append(path.stem)
                break
    return declaring


def mirror_classes() -> dict[str, tuple[type[PostgresTableMirror], object]]:
    """`{table: (mirror class, its module)}` for every declared mirror.

    The module comes back with the class because the fitness gate asks a
    question of it — where the reconciliation for this table is declared — and
    rediscovering it from the class would be a second rule about layout.
    """
    found: dict[str, tuple[type[PostgresTableMirror], object]] = {}
    for module_name in modules_declaring_mirrors():
        module = importlib.import_module(f"command_center.db.{module_name}")
        for attribute in vars(module).values():
            if (
                isinstance(attribute, type)
                and issubclass(attribute, PostgresTableMirror)
                and attribute is not PostgresTableMirror
            ):
                found[attribute.spec.table] = (attribute, module)
    return dict(sorted(found.items()))
