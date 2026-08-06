"""Architecture fitness gates for the AIOS boundary (AC-01 / ADR-0008 / ADR-0015).

These tests are the mechanical half of the AIOS Core acceptance criterion
AC-01 for this product repository: AI Command Center must be *mechanically
prevented* from growing a parallel queue, orchestration, authz, audit, or
memory engine, and must never reach into AIOS Core internals (only the public
SDK/API surface is allowed).

The scanner and its signatures live in ``tests/architecture/aios_boundary.py``;
the frozen inventory lives in ``tests/architecture/AIOS_BOUNDARY_BASELINE.json``;
the policy and the baseline-change procedure are documented in
``docs/AIOS_BOUNDARY.md``.
"""

from __future__ import annotations

import ast

from tests.architecture import aios_boundary as boundary


def test_no_aios_core_internals_imported_anywhere():
    """No Python file in the repository imports ``aios`` core internals.

    Covers every ``*.py`` in the repo (application code, scripts, tests,
    packaging) — static imports and literal dynamic imports alike. The public
    SDK namespace ``aios_sdk`` is explicitly allowed.
    """
    violations: list[str] = []
    for path in boundary.iter_python_files():
        rel_path = path.relative_to(boundary.REPO_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel_path)
        for lineno, description in boundary.find_banned_aios_imports(tree):
            violations.append(f"{rel_path}:{lineno}: {description}")
    assert not violations, (
        "AIOS Core internals must never be imported by a product repo "
        "(ADR-0008 / aios ADR-0015). Use the public SDK namespace "
        f"'{boundary.SDK_ALLOWED_TOP_LEVEL}' or the versioned HTTP API "
        "instead.\n" + "\n".join(violations)
    )


def test_engine_inventory_matches_frozen_baseline():
    """The frozen-engine inventory equals the baseline snapshot exactly.

    Growth direction (the AC-01 bar): a new file matching a queue /
    orchestration / authz / audit / memory signature, a new file inside
    ``command_center/runtime/``, or an existing file gaining a new engine
    category fails this test. Shrink direction: entries whose files stopped
    matching (retired or converged into AIOS) must be removed from the
    baseline, so the snapshot always equals reality. Either edit to
    ``AIOS_BOUNDARY_BASELINE.json`` is a reviewed architectural decision —
    see docs/AIOS_BOUNDARY.md for the procedure.
    """
    inventory = boundary.compute_engine_inventory()
    baseline = boundary.load_baseline()
    problems = boundary.diff_against_baseline(inventory, baseline)
    assert not problems, (
        "AIOS boundary drift detected (docs/AIOS_BOUNDARY.md, ADR-0008 / "
        "aios ADR-0015): AICC's engine-overlapping subsystems are frozen "
        "until convergence into AIOS Core; new engine capability belongs in "
        "AIOS, consumed via its API/SDK/events.\n" + "\n".join(problems)
    )


def test_scanner_semantics_are_stable():
    """The gate itself must not rot: sdk allowed, core banned, engines detected."""
    allowed = ast.parse("import aios_sdk\nfrom aios_sdk.client import Client\n")
    assert boundary.find_banned_aios_imports(allowed) == []

    banned = ast.parse(
        "import aios\n"
        "from aios.core import engine\n"
        "import importlib\n"
        "importlib.import_module('aios.queue')\n"
        "__import__('aios')\n"
    )
    assert len(boundary.find_banned_aios_imports(banned)) == 4

    db_engine = ast.parse("import sqlite3\n")
    assert "memory" in boundary.classify_engine_categories("command_center/new_cache.py", db_engine)

    spawner = ast.parse("import subprocess\nsubprocess.Popen(['sleep', '1'])\n")
    assert "orchestration" in boundary.classify_engine_categories(
        "command_center/new_helper.py", spawner
    )
    # Synchronous tool invocation is not an engine signature.
    runner = ast.parse("import subprocess\nsubprocess.run(['git', 'status'])\n")
    assert boundary.classify_engine_categories("command_center/new_helper.py", runner) == set()

    empty = ast.parse("")
    assert "runtime_package" in boundary.classify_engine_categories(
        "command_center/runtime/new_module.py", empty
    )
    assert "queue" in boundary.classify_engine_categories(
        "command_center/retry_queue.py", empty
    )
    # Presentation layers are exempt from name signatures only...
    assert boundary.classify_engine_categories("command_center/ui/queue_panel_v2.py", empty) == set()
    # ...but not from structural ones: an engine cannot hide in the UI layer.
    assert "memory" in boundary.classify_engine_categories(
        "command_center/ui/some_panel.py", db_engine
    )
