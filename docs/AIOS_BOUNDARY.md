# AIOS boundary fitness — mechanical enforcement of AC-01

## What this is

AIOS Core acceptance criterion **AC-01** requires that product repositories are
*mechanically prevented* from growing a parallel queue, orchestration, authz,
audit, or memory engine, with architecture fitness checks green in each
attached product repo. The doctrine behind it:

- **ADR-0015 (aios repo)** — AIOS is the single closed core; products are
  domain modules on top of its API/SDK/events; product-side engines are
  prohibited.
- **[ADR-0008](adr/0008-aios-first-control-plane-boundary.md) (this repo)** —
  AI Command Center is the operator-facing control plane; the legacy local
  `command_center.runtime` predates the boundary and "must not become a second
  platform engine"; "new engine capabilities are prohibited in AI Command
  Center".

This document describes the fitness checks that turn those sentences into a
merge gate.

## What is checked mechanically

The scanner lives in `tests/architecture/aios_boundary.py`; the gates run as
ordinary pytest tests in `tests/architecture/test_aios_boundary_fitness.py`
(collected by the required CI `pytest -q` step, and additionally by the
dedicated lightweight `AIOS boundary fitness` workflow).

### Gate 1 — core-internals import ban

No Python file anywhere in the repository (application code, scripts, tests,
packaging) may import `aios` or any `aios.*` submodule — via `import` /
`from ... import`, or via `importlib.import_module` / `__import__` with a
literal module name. The public SDK namespace `aios_sdk` may be imported only
by `command_center/application/aios_tasks.py`, the single AIOS adapter. That
adapter may import only the top-level package, never `aios_sdk.*` internals.
All other product code, scripts, and tests consume AICC's owned
`TasksGateway` contract instead. Detection is an AST walk, not a text grep —
comments, docstrings and strings cannot trigger or evade it.

A second published AIOS distribution is allowed on identical terms: `aios_db`
(universal PostgreSQL primitives) may be imported only by
`command_center/db/adapter.py`, and only at the top level. The rule is the same
because the reasoning is: these are versioned public contracts rather than Core
internals, and confining each to one module keeps the coupling in a place a
reviewer can see. Everything else in the repository reaches those primitives
through that adapter.

Each distribution is acquired from its own lock — `aios-sdk.lock.json` and
`aios-db.lock.json` — through the same verified fetch (`--lock` selects which).
For the SDK the inputs are an accepted-main SHA,
Actions run/artifact identity, filename, API major, version, and SHA-256 are all
immutable inputs. `scripts/fetch_aios_sdk_artifact.py` requires a dedicated
CI artifact-read credential and prefers `AIOS_ARTIFACT_READONLY_TOKEN`
with explicit fallback to `AIOS_ARTIFACT_READ_TOKEN` during rotation.
It rejects an absent credential, extra archive members, manifest mismatch,
or wheel checksum mismatch. There is deliberately no sibling checkout,
mutable ref, package-index, or vendored-wheel fallback.

That credential reads a private repository, and it is handed to a script taken
from the checked-out tree, so where the tree came from matters. On the
`pull_request` path GitHub withholds secrets from forks. The `merge_group` path
does not: the queue ref is created here, so the run is trusted with secrets,
while the code on it is the pull request's own. Every step that carries a
standing secret therefore begins with
`scripts/assert_trusted_head_repository.py`, which resolves the queued pull
request — its number is part of the queue ref, the payload naming no head
repository — and refuses the run unless the head repository is this one. It
refuses on anything it cannot establish: an unknown event, an unparseable ref,
a missing token, an API error, a deleted fork.
`tests/test_release_gate_policy.py` pins the guard to *every* secret-bearing
step and forbids widening a secret past the step that declares it, so the
protection cannot be dropped or side-stepped without failing the security gate.

The same sole adapter implements AICC's read-only status port with only the
SDK's public health, readiness, whoami, and workspace-timeline surfaces. Its
timeline DTO retains event id/type/time and request evidence only; actor,
subject, payload, prompts, and local paths are not projected. Availability is
not acceptance or deployment evidence, so those SHAs remain explicitly
unknown.

### Gate 2 — anti-engine growth gate

Every non-test Python file is classified against *structural* engine
signatures:

| Signal | Category | Rationale |
| --- | --- | --- |
| file under `command_center/runtime/` | `runtime_package` | the legacy local engine ADR-0008 froze wholesale |
| imports a DB driver/embedded store (`sqlite3`, `sqlalchemy`, `psycopg*`, `asyncpg`, `dbm`, `shelve`, `redis`, ...) | `memory` | a new module owning a database is a new persistence engine |
| imports a task-queue framework (`celery`, `rq`, `dramatiq`, `huey`, ...) | `queue` | |
| imports a scheduler/workflow framework (`apscheduler`, `airflow`, `prefect`, `temporalio`, `multiprocessing`, ...) | `orchestration` | |
| imports an auth/token library (`jwt`, `passlib`, `bcrypt`, `authlib`, `casbin`, ...) | `authz` | |
| *calls* `subprocess.Popen`, `os.fork`/`os.spawn*`/`os.posix_spawn` | `orchestration` | owning a process lifecycle is orchestration; synchronous `subprocess.run` (git/CLI invocation) deliberately is **not** a signature |
| path segment named like an engine (`queue`, `scheduler`, `supervisor`, `executor`, `launcher`, `autonomy`, `audit`, ...) | per token | catches home-grown engines that use no framework at all |
| path segment named like a store (`db`, `database`, `store`, `storage`, `repository`, `persistence`, `memory`) **and** the file persists data | `memory` | see *Corroborated names* below |

### Corroborated names (`memory` only)

The `memory` tokens are the one group where a name is a question rather than a
verdict. They name what a file is *about* as readily as what it *is*: a package
directory called `db/` says where code lives, not whether the code inside owns
an engine. Under a name-only rule a docstring-only `__init__.py` and a module
that renders SQL text were violations — which left the control plane unable to
keep any database-adjacent module at all, and turned the gate into one that
polices names instead of behaviour.

So a `memory` name classifies only when the same file also *persists data*:

- it imports a DB driver — statically, under an alias, or through `importlib`
  with a literal module name;
- it calls into a driver it bound itself (`pg.connect(...)`,
  `importlib.import_module("psycopg").connect(...)`);
- it writes durably to the filesystem (`os.replace`, `shutil.copyfile`,
  `json.dump`, `Path.write_text`, `open(..., "w"/"a"/"x")`). A JSON/JSONL store
  is a persistence engine even with no driver anywhere in it, and a
  driver-only definition would let one out of the gate entirely.

What deliberately does **not** corroborate is executing SQL on a connection the
caller opened. That is delegation, not ownership — the engine is wherever the
driver is — and counting it would flag SQL-rendering and grant modules while
catching no engine the driver rule misses.

Nothing loosened for `queue`, `orchestration`, `authz` or `audit`: those names
still classify on their own. And one signature was **tightened** at the same
time: `psycopg_pool` is a separate distribution from `psycopg` and was missing
from the driver list, so a file could open a PostgreSQL connection pool without
the gate seeing a driver at all.

Acknowledged limit, stated rather than papered over: a driver reached through a
*non-literal* dynamic import (`__import__(name_from_config)`) is beyond static
analysis and is not detected. Literal `importlib`/`__import__` and aliases are.

Name signatures are skipped for pure presentation layers
(`command_center/ui/`, `command_center/desktop/`, `web/`) so UI *panels over*
existing engines don't count as engines — but the import/call signatures still
apply there, so an engine cannot hide in the UI layer.

The current matches are frozen in
[`tests/architecture/AIOS_BOUNDARY_BASELINE.json`](../tests/architecture/AIOS_BOUNDARY_BASELINE.json)
(41 entries at freeze time). The gate fails when the detector's output differs
from that snapshot:

- **NEW ENGINE MODULE** — a file not in the baseline matches a signature;
- **ENGINE GROWTH** — a baseline file gains a category it did not have (e.g. a
  queue module starts importing `sqlite3`);
- **STALE BASELINE** — a baseline entry no longer matches (file retired or
  refactored), so the snapshot must shrink to stay equal to reality.

## What is frozen (the baseline, by subsystem)

Existing AICC-native subsystems that overlap AIOS Core domains. They predate
the doctrine, keep running unchanged, and are **frozen until their convergence
into AIOS Core (post-AIOS-CORE-ACCEPTED); growth is prohibited.**

- **Local execution engine** — all of `command_center/runtime/` (SQLite run
  store, session supervisor, scheduler, completion/autonomy pipelines,
  provider probes, task sync).
- **Queue** — `command_center/execution_queue.py`.
- **Orchestration** — `command_center/agent_runner.py`, `executors.py`,
  `task_pipeline.py`, `pipeline_settings.py`, `worktree_launcher.py`,
  `scripts/daily_audit_daemon.py`, `scripts/demo_*` drivers.
- **Audit** — `command_center/activity_log.py`, `daily_audit.py`,
  `daily_audit_backend.py`.
- **Authz** — `command_center/companion/auth.py` (an explicit placeholder; the
  gate keeps it one).
- **Memory/persistence** — `command_center/storage.py`,
  `tasks_repository.py`, `aml_store.py`.

Recorded exception, frozen-adjacent rather than legacy (see the procedure
section below for why it exists at all):

- **Server work queue (SRV lane)** — `command_center/db/work_queue_store.py`
  (queue client), `command_center/worker/` (daemon, entrypoint) — clients of
  the PL/pgSQL queue authority accepted via VOYN-W0-AICC-SRV-03/04b.

## Changing the baseline (a reviewed change)

The baseline may only change through an ordinary reviewed PR that explains the
architectural decision, in one of three legitimate directions:

1. **Shrink** — a frozen subsystem was retired or converged into an accepted
   AIOS contract (the migration path ADR-0008 §Consequences describes). This
   is the desired direction.
2. **Reclassify** — a detector false positive (e.g. a new UI-adjacent module
   whose name collides with a signature token). Prefer renaming the module;
   only if the name is genuinely right, add the entry with a justification in
   the PR description.
3. **Signature tuning** — improving the scanner itself (new frameworks in the
   import lists, new tokens). Tightening is routine; loosening requires the
   same scrutiny as a baseline addition.

Regenerate after a deliberate change and review the diff:

```
python -m tests.architecture.aios_boundary            # show drift
python -m tests.architecture.aios_boundary --write-baseline
```

Adding a *new* engine capability is never a baseline edit — new capability of
these categories belongs in AIOS Core and is consumed here through its
versioned API/SDK/event contracts (ADR-0008).

**One recorded exception, and its shape (2026-08-19).** The server work queue
of the VOYN SRV lane (`VOYN-W0-AICC-SRV-01..09`) was accepted by the central
backlog *after* this doctrine was written, and its acceptance placed the
capability in this repository deliberately: the queue's entire authority —
claim atomicity, claimant identity, the stale-owner fence, heartbeat clamps,
reaping — is PL/pgSQL running under PostgreSQL role separation
(`command_center/db/sql/0002_queue_claim.up.sql`, merged via PR #311/#313).
The Python modules that accompany it (`command_center/db/work_queue_store.py`,
`command_center/worker/`) are thin clients of that server authority: they
render calls to the SQL functions and react to their refusals, exactly the
delegation-not-ownership shape the `memory` rule above already declines to
count. The name tokens (`queue`, `worker`) still classify them, so they are
carried in the baseline under their own subsystem heading rather than
silently exempted from the scanner. Growth of this subsystem follows the SRV
lane's own reviewed PRs; growth of the *legacy* frozen engines remains
prohibited, and convergence into AIOS Core remains this subsystem's stated
end state once the core's dispatch contract (aios ADR-0022) is accepted and
covers it.

## CI wiring

- The required merge gate already runs these tests: `pytest -q` in
  `.github/workflows/ci.yml` collects `tests/architecture/`.
- `.github/workflows/arch-fitness.yml` additionally runs *only* the boundary
  tests in a minimal environment (`requirements.txt` + pytest) as a dedicated,
  fast, named status — the per-repo evidence line item for AC-01.
