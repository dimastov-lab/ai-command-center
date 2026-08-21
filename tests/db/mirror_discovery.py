"""Which classes are mirrors — the test-side view of one shared rule.

The rule itself lives in `command_center/db/mirror_registry.py` and this module
is the thin adapter the suites already import. It moved there at
`VOYN-W0-AICC-SRV-07b`, when the cutover parity gate became a third caller and
the first one that is not a test: the gate has to compare *every* mirrored
table, so it needs the same answer to "what is a mirror" that the contract and
the fitness gate use.

Keeping a second implementation on this side would have been the third round of
the failure this rule's history is made of. Two acceptances in a row defeated a
membership rule by exploiting a copy of it — a mirror in a differently-named
file, then three ways of hiding the base class from an AST scan — and both were
fixed by collapsing copies into one. The registry's docstring carries that
reasoning; what stays here is the one thing the suites need and production does
not: the *module* each mirror is declared in.

`mirror_classes()` therefore keeps its `{table: (class, module)}` shape, so no
test file changed when the rule moved.
"""

from __future__ import annotations

import sys

from command_center.db import mirror_registry
from command_center.db.table_mirror import PostgresTableMirror

__all__ = ["mirror_classes", "modules_declaring_mirrors"]

#: Re-exported unchanged: `test_mirror_probe` and the fitness gate reach for the
#: module list directly, and a second copy of the import rule here would be the
#: duplication this delegation exists to remove.
modules_declaring_mirrors = mirror_registry.modules_declaring_mirrors


def mirror_classes() -> dict[str, tuple[type[PostgresTableMirror], object]]:
    """`{table: (mirror class, its module)}` for every declared mirror.

    The module comes back with the class because the fitness gate asks a
    question of it — where the reconciliation for this table is declared — and
    rediscovering it from the class would be a second rule about layout.

    The refusal of a table with two mirrors happens inside the registry, and is
    a `DuplicateMirrorError` rather than the `AssertionError` this module used
    to raise: the rule now also runs in production, where `python -O` would
    strip an `assert` and leave the refusal refusing nothing.
    """
    return {
        table: (mirror, sys.modules[mirror.__module__])
        for table, mirror in mirror_registry.mirror_classes().items()
    }
