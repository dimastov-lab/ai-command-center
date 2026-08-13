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


def test_aios_imports_are_confined_to_the_public_sdk_adapter():
    """No Python file in the repository imports ``aios`` core internals.

    Covers every ``*.py`` in the repo (application code, scripts, tests,
    packaging) — static imports and literal dynamic imports alike. The public
    SDK namespace ``aios_sdk`` is explicitly allowed.
    """
    violations: list[str] = []
    for path in boundary.iter_python_files():
        rel_path = path.relative_to(boundary.REPO_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel_path)
        for lineno, description in boundary.find_forbidden_aios_imports(tree, rel_path):
            violations.append(f"{rel_path}:{lineno}: {description}")
    assert not violations, (
        "AIOS Core internals must never be imported, and the public SDK "
        f"namespace may be imported only by {boundary.SDK_ADAPTER_PATH}. "
        "Consume AICC's TasksGateway contract everywhere else.\n" + "\n".join(violations)
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
    allowed = ast.parse("import aios_sdk\n")
    assert boundary.find_forbidden_aios_imports(allowed, boundary.SDK_ADAPTER_PATH) == []
    assert boundary.find_forbidden_aios_imports(allowed, "command_center/other.py")
    deep_sdk = ast.parse("from aios_sdk.client import Client\n")
    assert boundary.find_forbidden_aios_imports(deep_sdk, boundary.SDK_ADAPTER_PATH)

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


def test_a_memory_name_alone_is_not_an_engine_signature():
    """`db`/`store`/`repository` name a file's subject as readily as its nature.

    A package directory called `db/` says where code lives, not whether the code
    inside owns an engine. Under a name-only rule the control plane could not
    keep *any* database-adjacent module — not even a docstring-only `__init__`
    or a module that renders SQL text — which is the opposite of a boundary: it
    stops describing what the code does and starts policing what it is called.
    """
    docstring_only = ast.parse('"""Package docstring."""\n__all__ = ["config"]\n')
    assert boundary.classify_engine_categories("command_center/db/__init__.py", docstring_only) == set()

    # Rendering SQL and executing it on a connection the caller opened is
    # delegation, not ownership: the engine is wherever the driver is.
    sql_renderer = ast.parse(
        "def render():\n"
        "    return ['CREATE ROLE app']\n"
        "def apply(conn):\n"
        "    with conn.cursor() as cur:\n"
        "        cur.execute(render()[0])\n"
    )
    assert boundary.classify_engine_categories("command_center/db/roles.py", sql_renderer) == set()

    # The same file with a driver of its own is an engine again.
    with_driver = ast.parse("import psycopg\ndef connect(dsn):\n    return psycopg.connect(dsn)\n")
    assert "memory" in boundary.classify_engine_categories("command_center/db/pool.py", with_driver)


def test_a_driver_is_detected_however_it_is_imported():
    """Alias and `importlib` are not hiding places."""
    aliased = ast.parse("import psycopg as pg\ndef go(dsn):\n    return pg.connect(dsn)\n")
    assert "memory" in boundary.classify_engine_categories("command_center/helper.py", aliased)

    dynamic = ast.parse(
        "import importlib\n"
        "driver = importlib.import_module('sqlite3')\n"
        "def go(path):\n"
        "    return driver.connect(path)\n"
    )
    assert "memory" in boundary.classify_engine_categories("command_center/helper.py", dynamic)

    underscore = ast.parse("__import__('psycopg2')\n")
    assert "memory" in boundary.classify_engine_categories("command_center/helper.py", underscore)

    # The connection *pool* package is its own distribution; a file that opens a
    # pool owns an engine just as much as one that opens a connection.
    pooled = ast.parse("from psycopg_pool import ConnectionPool\n")
    assert "memory" in boundary.classify_engine_categories("command_center/helper.py", pooled)


def test_a_file_backed_store_is_an_engine_even_with_no_driver():
    """JSON/JSONL persistence is persistence.

    If "engine" meant "imports a driver", the atomic-write JSON stores this
    repository actually runs on would drop out of the frozen inventory — the
    gate would get quieter while the code got no safer.
    """
    atomic_write = ast.parse(
        "import json\nimport os\n"
        "def save(path, data):\n"
        "    with open(path + '.tmp', 'w') as handle:\n"
        "        json.dump(data, handle)\n"
        "    os.replace(path + '.tmp', path)\n"
    )
    assert "memory" in boundary.classify_engine_categories(
        "command_center/storage.py", atomic_write
    )

    appender = ast.parse(
        "from pathlib import Path\n"
        "def record(path, line):\n"
        "    Path(path).write_text(line)\n"
    )
    assert "memory" in boundary.classify_engine_categories(
        "command_center/tasks_repository.py", appender
    )

    # Reading is not persisting: a module that only loads a store is not one.
    reader = ast.parse(
        "import json\n"
        "def load(path):\n"
        "    with open(path) as handle:\n"
        "        return json.load(handle)\n"
    )
    assert boundary.classify_engine_categories("command_center/db/config.py", reader) == set()


def test_corroboration_applies_only_to_the_memory_category():
    """The ruling loosened one signature, not the gate.

    `queue`, `orchestration`, `authz` and `audit` names still classify on the
    name alone — those tokens name what a module *is*, and nothing in the
    boundary ruling touched them.
    """
    empty = ast.parse("")
    for path, category in (
        ("command_center/retry_queue.py", "queue"),
        ("command_center/task_scheduler.py", "orchestration"),
        ("command_center/rbac_rules.py", "authz"),
        ("command_center/audit_trail.py", "audit"),
    ):
        assert category in boundary.classify_engine_categories(path, empty), path


def test_the_second_public_distribution_is_confined_to_its_own_adapter():
    """`aios_db` is allowed on the same terms as `aios_sdk`: one file, top-level only.

    It is a published, independently versioned contract rather than Core
    internals, so importing it is not a boundary crossing — but letting every
    module import it would scatter the coupling that the adapter exists to keep
    in one reviewed place.
    """
    top_level = ast.parse("import aios_db\n")
    assert boundary.find_forbidden_aios_imports(top_level, boundary.DB_ADAPTER_PATH) == []
    assert boundary.find_forbidden_aios_imports(top_level, "command_center/db/pool.py")
    assert boundary.find_forbidden_aios_imports(top_level, boundary.SDK_ADAPTER_PATH)

    # Private submodules are off limits even to the adapter: the contract is
    # what the package exports at the top level, not what happens to be inside.
    deep = ast.parse("from aios_db.migrations import MigrationRunner\n")
    assert boundary.find_forbidden_aios_imports(deep, boundary.DB_ADAPTER_PATH)

    # Core remains banned everywhere, including from the db adapter.
    core = ast.parse("from aios.storage.sql import Database\n")
    assert boundary.find_forbidden_aios_imports(core, boundary.DB_ADAPTER_PATH)


def test_a_store_that_opens_its_own_file_as_an_attribute_is_still_an_engine():
    """The reviewer's escape: `self._path.open("a")` instead of `open(path, "a")`.

    A store that owns its file usually holds it on `self` and never names a
    module at the call site, so checking only the builtin `open` let an
    append-only JSONL store in a directory called `db/` classify as no engine at
    all — while the old name-only rule had blocked it. That is the loosening the
    corroboration rule exists to avoid, and the docstring claimed it could not
    happen, which is worse than the gap itself.
    """
    attribute_open = ast.parse(
        "from pathlib import Path\n"
        "class EventStore:\n"
        "    def __init__(self, path):\n"
        "        self._path = path\n"
        "    def append(self, record):\n"
        "        with self._path.open('a', encoding='utf-8') as fh:\n"
        "            fh.write(record + '\\n')\n"
    )
    assert "memory" in boundary.classify_engine_categories(
        "command_center/db/eventstore.py", attribute_open
    )

    constructed = ast.parse(
        "from pathlib import Path\n"
        "def save(root, data):\n"
        "    with Path(root, 'tasks.json').open('w') as handle:\n"
        "        handle.write(data)\n"
    )
    assert "memory" in boundary.classify_engine_categories(
        "command_center/task_store.py", constructed
    )

    # Reading is still not persisting, in either form.
    reader = ast.parse(
        "def load(path):\n"
        "    with path.open('r') as handle:\n"
        "        return handle.read()\n"
    )
    assert boundary.classify_engine_categories("command_center/db/config.py", reader) == set()
