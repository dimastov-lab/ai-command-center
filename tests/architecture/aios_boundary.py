"""AIOS boundary fitness scanner — the mechanics behind AC-01 for this repo.

Doctrine (ADR-0008 here; ADR-0015 in the `aios` repo): AIOS is the single
infrastructure engine; AI Command Center is its operator-facing control plane
and consumes AIOS only through versioned public API/SDK/event contracts.
Subsystems that overlap AIOS Core domains (queue, orchestration, authz, audit,
memory/persistence) predate that doctrine; they are *frozen*, not deleted —
they may keep running, but they may not grow.

This module implements two mechanical gates, consumed by
``tests/architecture/test_aios_boundary_fitness.py``:

1. **Core-internals import ban.** No Python file anywhere in the repository may
   import ``aios`` or any ``aios.*`` submodule (statically or via
   ``importlib.import_module``/``__import__`` with a literal name). Only the
   public SDK namespace ``aios_sdk`` is allowed, if/when it appears.

2. **Anti-engine growth gate.** Every non-test Python file is classified
   against engine *signatures* (see below). The set of currently matching
   files, with their categories, is frozen in
   ``tests/architecture/AIOS_BOUNDARY_BASELINE.json``. The gate fails when the
   detector's output differs from the baseline in the growth direction — a new
   file matches an engine signature, or an existing file gains a new engine
   category — and also when the baseline goes stale (entries for files that no
   longer match), so the snapshot always equals reality.

Engine signatures (deliberately structural, never a grep over comments):

- ``runtime_package``: any file under ``command_center/runtime/`` — the legacy
  local execution engine ADR-0008 froze wholesale.
- import signatures: importing an embedded-database/driver module (sqlite3,
  sqlalchemy, ...) marks ``memory``; importing a task-queue/workflow framework
  (celery, apscheduler, ...) marks ``queue``/``orchestration``; importing an
  auth/crypto-token library (jwt, passlib, ...) marks ``authz``;
  ``multiprocessing`` marks ``orchestration``.
- call signatures: process-lifecycle spawning — ``subprocess.Popen``,
  ``os.fork``/``os.posix_spawn``/``os.spawn*``/``multiprocessing.Process`` —
  marks ``orchestration`` (plain ``subprocess.run`` is not a signature: the
  control plane legitimately shells out to ``git`` and CLIs synchronously).
- name signatures: path segments whose tokens name an engine
  (queue/scheduler/supervisor/store/repository/audit/...). Applied only
  outside pure presentation layers (``command_center/ui``,
  ``command_center/desktop``, ``web``) so that UI *panels over* existing
  engines don't count as engines; presentation layers still fall under the
  import/call signatures, so an engine can't hide there.

Baseline maintenance (a reviewed change, never a casual one — see
``docs/AIOS_BOUNDARY.md``):

    python -m tests.architecture.aios_boundary            # show drift
    python -m tests.architecture.aios_boundary --write-baseline
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_FILE = Path(__file__).resolve().parent / "AIOS_BOUNDARY_BASELINE.json"

#: The one namespace AICC is allowed to import from the AIOS side.
SDK_ALLOWED_TOP_LEVEL = "aios_sdk"
#: The one production adapter allowed to import that public top-level package.
SDK_ADAPTER_PATH = "command_center/application/aios_tasks.py"
#: The banned core namespace.
CORE_TOP_LEVEL = "aios"

#: Directory names never scanned (VCS, caches, third-party trees, envs).
EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "build",
        "dist",
        ".claude",
    }
)

#: The legacy local engine package ADR-0008 froze wholesale.
FROZEN_RUNTIME_PACKAGE = "command_center/runtime"

#: Pure presentation layers: exempt from *name* signatures only.
PRESENTATION_PREFIXES = ("command_center/ui/", "command_center/desktop/", "web/")

# --- import signatures (top-level module name → category) --------------------

DB_DRIVER_MODULES = frozenset(
    {
        "sqlite3",
        "sqlalchemy",
        "psycopg",
        "psycopg2",
        "asyncpg",
        "aiosqlite",
        "dbm",
        "shelve",
        "pymongo",
        "redis",
        "tinydb",
        "lmdb",
        "duckdb",
    }
)
QUEUE_FRAMEWORK_MODULES = frozenset(
    {"celery", "rq", "dramatiq", "huey", "arq", "taskiq", "kombu", "pika"}
)
ORCHESTRATION_FRAMEWORK_MODULES = frozenset(
    {
        "apscheduler",
        "schedule",
        "airflow",
        "prefect",
        "dagster",
        "temporalio",
        "luigi",
        "multiprocessing",
    }
)
AUTHZ_LIBRARY_MODULES = frozenset(
    {"jwt", "authlib", "passlib", "bcrypt", "oauthlib", "casbin", "keycloak"}
)

IMPORT_SIGNATURES: dict[str, frozenset[str]] = {
    "memory": DB_DRIVER_MODULES,
    "queue": QUEUE_FRAMEWORK_MODULES,
    "orchestration": ORCHESTRATION_FRAMEWORK_MODULES,
    "authz": AUTHZ_LIBRARY_MODULES,
}

# --- call signatures ---------------------------------------------------------

#: ``os.<attr>`` calls that spawn/adopt processes.
OS_SPAWN_ATTRS_PREFIXES = ("fork", "spawn", "posix_spawn")

# --- name signatures ---------------------------------------------------------

#: Path-segment tokens (segments split on non-alphanumerics) → category.
NAME_TOKEN_SIGNATURES: dict[str, frozenset[str]] = {
    "queue": frozenset({"queue", "queues"}),
    "orchestration": frozenset(
        {
            "scheduler",
            "schedulers",
            "supervisor",
            "orchestrator",
            "orchestration",
            "dispatch",
            "dispatcher",
            "autopilot",
            "autonomy",
            "daemon",
            "executor",
            "executors",
            "runner",
            "runners",
            "worker",
            "workers",
            "pipeline",
            "launcher",
            "engine",
        }
    ),
    "authz": frozenset(
        {"auth", "authn", "authz", "rbac", "acl", "oauth", "sso", "iam", "permission", "permissions"}
    ),
    "audit": frozenset({"audit", "provenance"}),
    "memory": frozenset(
        {
            "store",
            "stores",
            "storage",
            "repository",
            "repositories",
            "persistence",
            "db",
            "database",
            "memory",
        }
    ),
}

#: Whole-stem substrings → category (multi-token names a token split misses).
NAME_SUBSTRING_SIGNATURES: dict[str, tuple[str, ...]] = {
    "audit": ("activity_log", "audit_log", "event_log"),
}

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _is_excluded_part(part: str) -> bool:
    return (
        part in EXCLUDED_DIR_NAMES
        or part.startswith((".venv", "venv"))
        or part == "site-packages"
    )


def iter_python_files(root: Path = REPO_ROOT) -> list[Path]:
    """Every ``*.py`` under ``root``, skipping excluded directories."""
    files: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        rel_parts = path.relative_to(root).parts
        if any(_is_excluded_part(part) for part in rel_parts):
            continue
        files.append(path)
    return files


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _is_test_path(rel_path: str) -> bool:
    parts = rel_path.split("/")
    return "tests" in parts or parts[-1].startswith("test_")


def _is_presentation(rel_path: str) -> bool:
    return rel_path.startswith(PRESENTATION_PREFIXES)


def _top_level(module_name: str) -> str:
    return module_name.split(".", 1)[0]


# ---------------------------------------------------------------------------
# Gate 1: core-internals import ban
# ---------------------------------------------------------------------------


def _is_banned_module_name(name: str) -> bool:
    top = _top_level(name)
    return top == CORE_TOP_LEVEL


def _is_forbidden_aios_module(name: str, rel_path: str) -> bool:
    top = _top_level(name)
    if top == CORE_TOP_LEVEL:
        return True
    if top != SDK_ALLOWED_TOP_LEVEL:
        return False
    # Even the adapter cannot couple to generated/private SDK modules: every
    # consumed symbol must be a documented top-level export.
    return rel_path != SDK_ADAPTER_PATH or name != SDK_ALLOWED_TOP_LEVEL


def _literal_string(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def find_banned_aios_imports(tree: ast.AST) -> list[tuple[int, str]]:
    """(lineno, description) for every ``aios`` core import in ``tree``.

    ``aios_sdk`` (the public SDK namespace) is explicitly allowed; only the
    core package ``aios`` and its submodules are banned.
    """
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_banned_module_name(alias.name):
                    violations.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            # Relative imports (level > 0) are inside this repo by definition.
            if node.level == 0 and node.module and _is_banned_module_name(node.module):
                violations.append((node.lineno, f"from {node.module} import ..."))
        elif isinstance(node, ast.Call):
            literal: str | None = None
            func = node.func
            is_dynamic_import = (
                isinstance(func, ast.Name) and func.id == "__import__"
            ) or (isinstance(func, ast.Attribute) and func.attr == "import_module")
            if is_dynamic_import and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    literal = first.value
            if literal is not None and _is_banned_module_name(literal):
                violations.append((node.lineno, f"dynamic import of {literal!r}"))
    return violations


def find_forbidden_aios_imports(
    tree: ast.AST, rel_path: str
) -> list[tuple[int, str]]:
    """Find core imports and SDK imports outside the sole public adapter."""
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden_aios_module(alias.name, rel_path):
                    violations.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            if (
                node.level == 0
                and node.module
                and _is_forbidden_aios_module(node.module, rel_path)
            ):
                violations.append((node.lineno, f"from {node.module} import ..."))
        elif isinstance(node, ast.Call):
            literal: str | None = None
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "importlib"
                and node.func.attr == "import_module"
                and node.args
            ):
                literal = _literal_string(node.args[0])
            elif isinstance(node.func, ast.Name) and node.func.id == "__import__" and node.args:
                literal = _literal_string(node.args[0])
            if literal and _is_forbidden_aios_module(literal, rel_path):
                violations.append((node.lineno, f"dynamic import {literal}"))
    return violations


# ---------------------------------------------------------------------------
# Gate 2: anti-engine growth detector
# ---------------------------------------------------------------------------


def _import_categories(tree: ast.AST) -> tuple[set[str], set[str]]:
    """(categories, local aliases of ``subprocess``/``os``) from import nodes."""
    categories: set[str] = set()
    spawn_capable_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = _top_level(alias.name)
                for category, modules in IMPORT_SIGNATURES.items():
                    if top in modules:
                        categories.add(category)
                if top in {"subprocess", "os"}:
                    spawn_capable_aliases.add(alias.asname or _top_level(alias.name))
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            top = _top_level(node.module)
            for category, modules in IMPORT_SIGNATURES.items():
                if top in modules:
                    categories.add(category)
            if top == "subprocess":
                for alias in node.names:
                    if alias.name == "Popen":
                        categories.add("orchestration")
            if top == "multiprocessing":
                categories.add("orchestration")
            if top == "os":
                for alias in node.names:
                    if alias.name.startswith(OS_SPAWN_ATTRS_PREFIXES):
                        categories.add("orchestration")
    return categories, spawn_capable_aliases


def _call_categories(tree: ast.AST, spawn_capable_aliases: set[str]) -> set[str]:
    """Process-spawning call sites: ``subprocess.Popen``, ``os.fork``/``spawn*``."""
    categories: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        base = func.value
        if not (isinstance(base, ast.Name) and base.id in spawn_capable_aliases):
            continue
        if func.attr == "Popen" or func.attr.startswith(OS_SPAWN_ATTRS_PREFIXES):
            categories.add("orchestration")
    return categories


def _name_categories(rel_path: str) -> set[str]:
    """Engine-named path segments (skipped for pure presentation layers)."""
    if _is_presentation(rel_path):
        return set()
    categories: set[str] = set()
    segments = rel_path.lower().removesuffix(".py").split("/")
    # The repository/package roots themselves are not signal-bearing names.
    tokens = {
        token
        for segment in segments[1:] or segments
        for token in _TOKEN_SPLIT.split(segment)
        if token
    }
    stem = segments[-1]
    for category, signature_tokens in NAME_TOKEN_SIGNATURES.items():
        if tokens & signature_tokens:
            categories.add(category)
    for category, substrings in NAME_SUBSTRING_SIGNATURES.items():
        if any(sub in stem for sub in substrings):
            categories.add(category)
    return categories


def classify_engine_categories(rel_path: str, tree: ast.AST) -> set[str]:
    """All engine categories ``rel_path`` matches (empty set = not an engine)."""
    categories: set[str] = set()
    if rel_path.startswith(FROZEN_RUNTIME_PACKAGE + "/"):
        categories.add("runtime_package")
    import_cats, spawn_aliases = _import_categories(tree)
    categories |= import_cats
    categories |= _call_categories(tree, spawn_aliases)
    categories |= _name_categories(rel_path)
    return categories


def compute_engine_inventory() -> dict[str, list[str]]:
    """Current detector output: rel path → sorted engine categories.

    Test files are out of scope (tests exercise the frozen engines; they do not
    ship capability), but they remain fully covered by the import ban.
    """
    inventory: dict[str, list[str]] = {}
    for path in iter_python_files():
        rel_path = _rel(path)
        if _is_test_path(rel_path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel_path)
        categories = classify_engine_categories(rel_path, tree)
        if categories:
            inventory[rel_path] = sorted(categories)
    return inventory


def load_baseline() -> dict[str, list[str]]:
    raw = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    return {path: sorted(categories) for path, categories in raw["entries"].items()}


def diff_against_baseline(
    inventory: dict[str, list[str]], baseline: dict[str, list[str]]
) -> list[str]:
    """Human-readable drift lines; empty list means the gate is green."""
    problems: list[str] = []
    for rel_path in sorted(inventory):
        current = set(inventory[rel_path])
        frozen = set(baseline.get(rel_path, []))
        if rel_path not in baseline:
            problems.append(
                f"NEW ENGINE MODULE: {rel_path} matches frozen categories "
                f"{sorted(current)} but is not in the baseline"
            )
        elif current - frozen:
            problems.append(
                f"ENGINE GROWTH: {rel_path} gained categories "
                f"{sorted(current - frozen)} (baseline: {sorted(frozen)})"
            )
    for rel_path in sorted(set(baseline) - set(inventory)):
        problems.append(
            f"STALE BASELINE ENTRY: {rel_path} no longer matches any engine "
            "signature (or was removed) — shrink the baseline to keep the "
            "snapshot equal to reality"
        )
    for rel_path in sorted(set(baseline) & set(inventory)):
        lost = set(baseline[rel_path]) - set(inventory[rel_path])
        if lost:
            problems.append(
                f"STALE BASELINE CATEGORIES: {rel_path} no longer matches "
                f"{sorted(lost)} — shrink the baseline entry"
            )
    return problems


def _write_baseline(inventory: dict[str, list[str]]) -> None:
    payload = {
        "policy": (
            "Frozen inventory of AICC-native subsystems overlapping AIOS Core "
            "domains (queue/orchestration/authz/audit/memory). These predate "
            "ADR-0008 (this repo) / ADR-0015 (aios repo) and are frozen until "
            "their convergence into AIOS Core (post-AIOS-CORE-ACCEPTED): they "
            "keep running, but growth is prohibited — no new files in these "
            "categories, no new engine categories in existing files. Any edit "
            "to this file is a reviewed architectural decision; see "
            "docs/AIOS_BOUNDARY.md for the procedure."
        ),
        "references": [
            "docs/AIOS_BOUNDARY.md",
            "docs/adr/0008-aios-first-control-plane-boundary.md",
            "aios repo: ADR-0015 (single-core doctrine), docs/AIOS_CORE_ACCEPTANCE.md AC-01",
        ],
        "entries": {path: inventory[path] for path in sorted(inventory)},
    }
    BASELINE_FILE.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main(argv: list[str]) -> int:
    inventory = compute_engine_inventory()
    if "--write-baseline" in argv:
        _write_baseline(inventory)
        print(f"wrote {len(inventory)} entries to {_rel(BASELINE_FILE)}")
        return 0
    if not BASELINE_FILE.exists():
        print("no baseline yet; run with --write-baseline to create it")
        return 1
    problems = diff_against_baseline(inventory, load_baseline())
    if problems:
        print("\n".join(problems))
        return 1
    print(f"boundary fitness green: {len(inventory)} frozen entries, no drift")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
