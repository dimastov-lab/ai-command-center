"""Which classes are mirrors — asked once, answered the same way everywhere.

The rule used to live in `tests/db/mirror_discovery.py` because only tests
needed it: the contract suite enrols every mirror in the per-table checks, and
the stored-reader fitness gate asks the authority package a question per
mirrored table. `VOYN-W0-AICC-SRV-07b` gives it a third caller that is not a
test — the cutover parity gate has to compare *every* mirrored table before
reads move to PostgreSQL, and a gate that walks a hand-written list is a gate
that silently stops covering the table someone forgets to add to it. That is
the same failure the migration has already had twice, and the whole point of a
gate is that it is not the thing you have to remember.

So the rule moves here, to production code, and the test helper delegates. That
direction rather than the other because the alternative is the failure this
module's history is entirely about: `mirror_discovery`'s own docstring records
two rounds of an acceptance defeating a membership rule, and both were fixed by
collapsing copies of the rule into one. A production copy alongside the test
copy would be the third round.

How membership is decided has not changed, and the reasoning is worth repeating
where the code now is:

* **Sources are read only to choose what to import.** Text, not AST —
  `_MARKERS` is deliberately broader than "declares a subclass", because the
  cost of a false positive is one import and the cost of a false negative is a
  mirror nothing checks and nothing backfills.
* **The set itself comes from `PostgresTableMirror.__subclasses__()`,
  transitively.** A class cannot lie to `issubclass`, whereas an AST rule
  looking for the base by name was walked around three separate ways: an
  aliased base, a subclass of an existing mirror, and a class defined inside a
  function.
* **The package is not imported wholesale.** `command_center/db/pool.py`
  imports `aios_db`, so importing everything would make the declaration checks
  collectible only on a machine with a PostgreSQL client library — and the
  serverless configuration is exactly where those checks are the only ones
  still running.
"""

from __future__ import annotations

import importlib
from pathlib import Path

from command_center.db.table_mirror import PostgresTableMirror

__all__ = ["DuplicateMirrorError", "mirror_classes", "modules_declaring_mirrors"]

#: What a module must mention to be worth importing. Deliberately broader than
#: "declares a subclass": the cost of a false positive is one import, and the
#: cost of a false negative is a mirror nothing checks.
_MARKERS = ("table_mirror", "PostgresTableMirror")


class DuplicateMirrorError(RuntimeError):
    """Two classes declare the same table.

    A `RuntimeError` rather than the `AssertionError` the test helper used to
    raise, now that the rule runs in production: `python -O` strips `assert`,
    and a discovery rule that stops refusing under an optimisation flag refuses
    nothing on the deploy that matters.
    """


def modules_declaring_mirrors() -> list[str]:
    """Dotted names of `command_center.db` modules that might declare a mirror.

    `rglob`, so a future `command_center/db/<subpackage>/` cannot hide a mirror
    by being one directory deeper.
    """
    import command_center.db as db_package

    package_root = Path(db_package.__path__[0])
    found: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        source = path.read_text(encoding="utf-8")
        if not any(marker in source for marker in _MARKERS):
            continue
        relative = path.relative_to(package_root).with_suffix("")
        found.append("command_center.db." + ".".join(relative.parts))
    return found


def _every_subclass(root: type) -> list[type]:
    """`root`'s subclasses, transitively — a subclass of a mirror is a mirror."""
    seen: list[type] = []
    for subclass in root.__subclasses__():
        seen.append(subclass)
        seen.extend(_every_subclass(subclass))
    return seen


def mirror_classes() -> dict[str, type[PostgresTableMirror]]:
    """`{table: mirror class}` for every declared mirror, sorted by table.

    Two mirrors for one table is refused rather than resolved. An earlier
    version of this rule silently kept whichever sorted last, so a second
    declaration could hide a broken production mirror from every check — an
    acceptance proved exactly that by giving the real `task` mirror a wrong key
    and adding a correct-looking duplicate. A table with two mirrors has two
    opinions about itself, and picking one is not this module's decision to
    make; for the backfill it would additionally mean copying a table through a
    mirror nobody reviewed.
    """
    for module_name in modules_declaring_mirrors():
        importlib.import_module(module_name)

    found: dict[str, type[PostgresTableMirror]] = {}
    for subclass in _every_subclass(PostgresTableMirror):
        if not subclass.__module__.startswith("command_center.db"):
            continue
        table = subclass.spec.table
        if table in found and found[table] is not subclass:
            first = found[table]
            raise DuplicateMirrorError(
                f"two mirrors declare `{table}`: {first.__module__}.{first.__name__} and "
                f"{subclass.__module__}.{subclass.__name__}. A table with two mirrors has "
                "two opinions about its statements, and the checks would have run against "
                "only one of them."
            )
        found[table] = subclass
    return dict(sorted(found.items()))
