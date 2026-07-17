# Workspace Home — Architecture

Sprint 3 — Universal Workspace · Increment: Workspace Home
Branch: `feature/v3-workspace-home-architecture` · Status: **architecture only, no code changed**

This document is grounded in the actual code as of this branch's HEAD (`git log` tip: merge of
Sprint 2 "Live Execution Center"). Where it says a function/module exists, it was read directly;
where it proposes something new, that is explicitly flagged as **NEW**.

**Revision note (post-independent-review):** an independent read-only review (Principal Product
Architect / Principal Software Architect / Security) returned verdict **BLOCKED** against the
prior revision of this document, on two HIGH-severity gaps (F1, F2) and three factual corrections
(F3, F4, F5). This revision resolves all five. Nothing about the underlying design direction
changed — every correction below is additive/corrective to the plan, not a redesign. See §1 for
the updated condition list and §17 for the updated implementation sequence.

**Finalization note (post-second-pass-review):** a second, independent read-only review verified
F1–F5 as fully **RESOLVED** and returned **APPROVED WITH NON-BLOCKING OBSERVATIONS**, flagging only
two LOW-severity, non-architectural observations: NF1 (some `app.py` line-number citations had
drifted after an unrelated upstream merge — PR #3, "Sprint 2 fast-follow UI fixes" — landed on
`main` between review passes) and NF2 (the document's own validation block still cited an earlier
test count). This finalization pass corrects both: every `app.py` citation below has been
re-verified against the current branch HEAD, and the test count is refreshed to the currently
verified baseline (§14/§18/the validation block, with an added note that this number will keep
moving as tests are added — always trust a fresh run over this document). No architecture decision
changed as part of either correction. Status is updated to **APPROVED FOR IMPLEMENTATION** (§1,
§20, and the closing Verdict section).

---

## 1. Executive verdict

Workspace Home is buildable almost entirely by **composing existing read APIs** — no new runtime,
no new database logic beyond one additive query extension, no new git-write logic, no schema
migration. Three small, additive, backward-compatible/well-isolated pieces of work are needed
first, sequenced as the first three implementation steps (§17):

1. **Git worktree/status discovery is currently hardcoded to the app's own repo root**
   (`app.py`'s `get_git_worktrees()` always runs `git worktree list --porcelain` with
   `cwd=ROOT`). Workspace Home needs this *per project*, against each project's configured
   `repository_path`. This requires generalizing an existing pure-Python helper, not writing new
   git logic. *(Condition 1.)*
2. **Both `command_center/runtime/db.py`'s `list_runs()` and
   `command_center/runtime/api.py`'s `ExecutionCenterAPI.list_runs()` have no `states` (plural) or
   `limit` parameter.** `app.py:1990` already does `execution_center_api.list_runs()[:20]` —
   loading the *entire* run table into Python memory just to slice the first 20 rows. Workspace
   Home's Active Runs / Recent Runs widgets make this worse (more call sites, rendered on every
   page visit), so the query itself should learn to filter/limit before this ships, not after.
   **Both the storage-layer function and the application-facing facade must change together** —
   the read model is required (§6) to call only through `ExecutionCenterAPI`, so extending `db.py`
   alone is not sufficient; a caller cannot reach the new filtering/limiting behavior without the
   facade also exposing it. See §8/§12 for the exact signatures. *(Condition 2.)*
3. **Workspace Home is the first page to render an ambient, always-visible rollup across every
   project at once, including BANK/LEGAL, with no explicit per-item "open this" click gating
   visibility** — the opposite of every existing sensitive-content boundary in this codebase,
   which is built around explicit per-item confirmation *before sending to a model*, not around
   what may passively render on screen. This requires a dedicated redaction stage inside the
   read model itself (`command_center/workspace_home.py`), not a rendering-layer convention in
   `app.py`. See §5/§13 for the exact design. *(Condition 3.)*

All three are scoped, additive pieces of work, each independently testable and each verified safe
against every current call site (§16, redone in full — see F4 below). Everything else — Projects,
Active Runs, Recent Runs, Recent Activity, Artifacts, Reports, Quick Actions — is direct reuse of
`command_center/*` public APIs.

**Verdict: APPROVED FOR IMPLEMENTATION** (see §17 for the three prerequisite steps above,
sequenced as implementation steps 1–3; this status was confirmed by a second, independent
architecture review after F1–F5 were verified fully resolved — see the finalization note above.
The three items are prerequisite *implementation sequencing*, not open architectural gaps: nothing
here blocks starting §17 step 1 today).

---

## 2. Existing repository capabilities

| Home requirement | Existing capability | Location |
|---|---|---|
| Projects | `load_project_configs()`, `PROJECT_IDS`, `is_sensitive()` | `command_center/project_config.py` |
| Active Git Worktrees | `get_git_worktrees()` / `run_git_command()` — **ROOT-only today** | `app.py:410-526` |
| Active Runs | `ExecutionCenterAPI.list_runs()` (v2, SQLite, async) | `command_center/runtime/api.py` |
| Recent Runs | same, plus `agent_runner.load_runs()` (v1.2, JSONL, sync) | `command_center/agent_runner.py` |
| Recent Activity | `activity_log.load_activity()` (typed event log) | `command_center/activity_log.py` |
| Artifacts | `list_markdown_files(GENERATED_DIR)` | `app.py:201-208` |
| Reports | `list_markdown_files(REPORTS_DIR)` + `report_parser` | `command_center/report_parser.py` |
| Quick Actions | `pending_nav` staged-navigation pattern, `render_agent_launcher`, `render_execution_center_launch_form` | `app.py` §5, §11.3 machinery |

The codebase already enforces one hard rule worth restating because Workspace Home must follow it:
*if a function calls `st.*`, it stays in `app.py`; if it doesn't, it lives in `command_center/`*
(ARCHITECTURE.md §3). Every new read-model function in this design obeys that rule — **including
the sensitivity redaction stage (§5, §13), which is plain Python, lives in
`command_center/workspace_home.py`, and must run before any data reaches `app.py`.**

---

## 3. Workspace Home UX

Single new page, `st.subheader("Workspace Home")`, following the existing visual language
(`st.container(horizontal=True)` metric strips, `st.container(border=True)` cards,
`st.expander` for drill-down — no new component library, no custom HTML/JS, matching
ARCHITECTURE.md §10's "no JavaScript/HTML component code" rule).

Layout, top to bottom:

1. **Header KPI strip** — project count, active runs (v2), open Kanban tasks, artifacts,
   reports — `st.metric(..., border=True)` row, same pattern as today's Dashboard.
2. **Projects** — one card per `PROJECT_IDS` entry: display name, sensitivity badge,
   configured/unconfigured repository-path state, active Kanban task count, active run count.
   Each card carries the Quick Actions from §11. **The default rendering path this section must be
   verified against is "0 of 6 projects have a configured `repository_path`"** — see §7 for why
   that, not "5 of 6," is the correct baseline assumption for a fresh checkout.
3. **Active Git Worktrees** — grouped by project, only for projects with a configured and
   verified repository path; empty/unconfigured projects get a single "configure repository
   path" affordance instead of an error.
4. **Active Runs** — v2 runs in `{PREPARED, QUEUED, RUNNING}`, cross-project, each row linking
   into Execution Center for the live-watch panel (`render_execution_center_watch`, already
   auto-refreshing via `st.fragment(run_every=2.0)` — Home does not reimplement live-watch).
5. **Recent Runs** — merged v1.2 + v2, source-tagged (§8), most recent first, bounded. **Every row
   key, dedup check, and click-through target is keyed on `(source, run_id)`, never bare
   `run_id`** — see §8/F5 for why.
6. **Recent Activity** — `activity_log` events, folded with derived v2 lifecycle rows (§10).
7. **Artifacts** / **Reports** — two side-by-side bounded lists, each with a "View all" deep
   link into the existing `generated`/`reports` pages.
8. **Quick Actions** — a persistent action bar (New Task, Launch Run, Open Project), plus
   inline per-card actions described in §11.

For BANK/LEGAL project cards specifically, every one of sections 4–7 renders **only the fields
the redaction stage (§5/§13) already allowed through** — the renderer has no access to, and
therefore cannot accidentally render, prompt/log/report-body content for those projects. See §13.

**Open product question, explicitly not resolved here (§19):** this page's required sections
substantially overlap today's "Обзор" (Dashboard) and "Workspace Launcher" pages. This design
adds Workspace Home as a new, additional NAV entry and leaves both existing pages unchanged —
whether they are later merged into or deprecated in favor of Home is a product decision for a
future increment.

---

## 4. Data-source map

| Section | Source | Medium | Cost per render | Sensitive-project handling |
|---|---|---|---|---|
| Projects | `project_config.load_project_configs()` + `load_tasks()` (Kanban count) + `db.list_tasks()` | JSON + SQLite | cheap | metadata only (id, display name, counts) — no task titles for sensitive projects in the card summary; full titles remain behind the existing Kanban board, unchanged |
| Active Git Worktrees | **NEW** per-repo git helper (§7) | subprocess (`git worktree list --porcelain`) | moderate — 1 spawn per configured project, cache (§12) | branch name + short HEAD only; no commit subject/message rendered on Home for sensitive projects (commit subjects can contain free-form, potentially sensitive text) |
| Active Runs | `ExecutionCenterAPI.list_runs(states=ACTIVE, limit=N)` **(extended, §8)** | SQLite | cheap with limit | redacted via `workspace_home.py`'s sanitize stage before entering the snapshot (§5/§13) |
| Recent Runs | `ExecutionCenterAPI.list_runs(states=TERMINAL, limit=N)` **(extended, §8)** + `agent_runner.load_runs()` tail | SQLite + JSONL | cheap with limit | same |
| Recent Activity | `activity_log.load_activity(limit=N)` + derived v2 lifecycle rows from Recent Runs | JSONL (+ in-memory derivation) | cheap | same — derived rows are built from the *already-redacted* Recent Runs list, never from raw run rows |
| Artifacts | `list_markdown_files(GENERATED_DIR)[:N]` | filesystem `rglob` | moderate, already paid by `generated` page today | filename replaced with a generic label (`"<task_type> artifact — <date>"`) for sensitive projects; real path never sent to the renderer for those rows (§13) |
| Reports | `list_markdown_files(REPORTS_DIR)[:N]` + `report_parser` + run/report join | filesystem + JSONL/SQLite | moderate, already paid by `reports` page today | verdict/severity **badge only** (already a small enum, not free text) for sensitive projects; report body, findings text, and file path excluded |
| Quick Actions | none (pure dispatch) | — | free | unaffected — Quick Actions never carry document content, only ids/navigation targets |

---

## 5. Read model

**NEW module: `command_center/workspace_home.py`.** Plain Python, no `st.*` import, independently
unit-testable — same "dict + factory function" convention as `models.new_run_record` etc.

```python
def build_workspace_home_snapshot(
    *,
    execution_center_api: ExecutionCenterAPI,   # injected — never constructed here
    active_runs_limit: int = 20,
    recent_runs_limit: int = 20,
    activity_limit: int = 20,
    artifacts_limit: int = 20,
    reports_limit: int = 20,
) -> dict:
    ...
```

Returns a single plain dict: `{"projects": [...], "worktrees_by_project": {...},
"active_runs": [...], "recent_runs": [...], "recent_activity": [...], "artifacts": [...],
"reports": [...]}`. `app.py`'s new page section becomes a thin renderer over this one dict —
no business logic beyond `st.*` calls, exactly matching every other page's split today.

`execution_center_api` is **injected**, not constructed inside the read model. `app.py` already
owns the one process-wide `ExecutionCenterAPI`/`Supervisor` singleton via
`get_execution_center_api()` (`@st.cache_resource`). Constructing a second one here would run
`db.migrate()` again (idempotent but wasteful) and maintain a second, useless empty `_active`
registry — the read model must reuse the existing singleton, never create its own.

### 5.1 Sensitivity redaction stage (resolves F2)

`build_workspace_home_snapshot` calls a dedicated, pure-Python sanitizer **before** any
project's data is assembled into the returned dict — not as a rendering convention applied
afterward in `app.py`. This is the structural fix the independent review required: the raw,
unredacted fields must never exist in the dict handed to the UI layer for a sensitive project, so
no future renderer change, debug print, logging call, or additional card can accidentally expose
them — the data isn't there to expose.

```python
def sanitize_workspace_project_entry(
    project_id: str,
    *,
    runs: list[dict],       # raw rows, tagged with `source` (§8), already source-scoped
    reports: list[dict],    # raw report_parser output + report_path/report row, per run
    artifacts: list[Path],  # raw markdown file paths under generated/<project>
    activity: list[dict],   # raw activity_log events + derived v2 lifecycle rows, this project only
) -> dict:
    """Returns `{"runs": [...], "reports": [...], "artifacts": [...], "activity": [...]}`
    with every entry passed through the field allowlist for this project. For a
    non-sensitive project (`not project_config.is_sensitive(project_id)`), every field
    of every entry passes through unchanged — this function is the *single* place
    Workspace Home's redaction policy lives, applied uniformly rather than as an
    if-sensitive branch scattered per section."""
```

`build_workspace_home_snapshot` calls this once per project, immediately after fetching that
project's runs/reports/artifacts/activity and before folding them into the cross-project
`active_runs`/`recent_runs`/`recent_activity`/`artifacts`/`reports` lists returned to `app.py`.
The renderer (`app.py`) therefore only ever sees post-redaction data — it has no code path back to
the raw `run`/`report` rows, and needs no sensitivity-awareness of its own. **The renderer is not
the security boundary; it cannot be, because it never receives the data that would need
redacting.** This does not remove the existing, separate `st.expander`/explicit-open pattern used
by the dedicated Runs/Reports pages for non-Home views — those are unchanged and out of scope
here.

#### Allowlist (applies to sensitive projects only — `project_config.is_sensitive(project_id)`)

For a **sensitive** project (`BANK`, `LEGAL`), each entry type keeps only these fields; every
field not listed is dropped, never merely hidden by the renderer:

| Entry type | Allowed fields |
|---|---|
| Run (active/recent) | `run_id` (or v1.2 `id`), `source` (`"v1.2"`/`"v2"`), `project`, `task_type`, `state`/`status` (enum value, not free text), `created_at`, `started_at`, `completed_at`, `exit_code` (int), `duration_seconds` |
| Report | `run_id`, `source`, `project`, `verdict` (enum, from `models.VERDICT_*`), `severity_counts` (int counts per `models.SEVERITIES`, from `report_parser.severity_counts`), `created_at` |
| Artifact | `project`, `task_type` (inferred from filename via existing `infer_task_type_from_filename`, itself just an enum lookup, not the filename), `created_at` (mtime), a generic navigation target (project + section, not the file path) |
| Activity | `project`, `event_type` (enum, from `models.ACTIVITY_EVENT_TYPES`), `ts`, `run_id`/`task_id` (ids only, no `message` field) |

**Explicitly excluded for sensitive projects, in all cases:** `prompt`, `instruction`,
`candidate_content`/`confirmed_items`, `command_json`, stdout/stderr, any `run_event.payload`
text, report body/findings/recommended-next-action text, generated file content, `failure_reason`
free text (verified today to only ever be the literal string `"timeout"` or `None` — see §13 for
why this one field is allowed as an enum, not as an exception to the free-text rule), Git commit
subjects/messages, artifact/report file paths, and `activity_log`'s `message` field (which
`models.new_activity_event` itself already documents as "never a full report or chat message
body," but Home does not rely on that upstream discipline alone — the field is dropped here too,
defense in depth).

A **non-sensitive** project's entries pass through `sanitize_workspace_project_entry` unchanged
(full fields, same as they exist in the source system) — the function is a no-op identity
transform for `AIOS`/`AICOS`/`BUSINESS`/`PERSONAL`, verified by a dedicated test (§14).

---

## 6. Service boundary

**Workspace Home's read model MAY:**
- Call `ExecutionCenterAPI`'s *read* methods only: `list_runs` (extended, §8), `list_sessions`,
  `list_tasks`, `get_run`, `get_events`, `get_report`.
- Call `agent_runner.load_runs()` (read-only fold of the JSONL run log).
- Call `project_config.load_project_configs()` / `is_sensitive()`.
- Call `activity_log.load_activity()`.
- Call the new per-repo git helper (§7) — read-only git subcommands only.
- Call `list_markdown_files()` / `read_text()`.

**Workspace Home's read model MUST:**
- Apply `sanitize_workspace_project_entry` (§5.1) to every sensitive project's runs, reports,
  artifacts, and activity **before** they are folded into the returned snapshot dict. This is not
  optional and not deferrable to the renderer — it is the one new invariant this increment adds to
  the service boundary, on top of every rule below (which are all carried over unchanged).

**Workspace Home's read model MUST NOT:**
- Construct a second `Supervisor`.
- Touch `runtime/db.py`'s SQLite connection directly with ad-hoc SQL — always through `db.py`'s
  own functions (which already enforce the `_UPDATABLE_RUN_FIELDS` allowlist, CAS versioning,
  and transition table).
- Call any git-write subcommand.
- Call `agent_runner.run_claude_code` or `ExecutionCenterAPI.start_run`/`request_cancel` as part
  of *building* the snapshot. Those are only ever invoked from an explicit, user-clicked Quick
  Action, landing on the existing launcher form — never as a side effect of rendering Home.
- Write to any data file. Home is a pure read surface; the only writes reachable from this page
  are the ones Quick Actions delegate to already-reviewed, already-gated forms (§11).
- Return a run/report/artifact/activity entry for a sensitive project that has not passed through
  `sanitize_workspace_project_entry`. `app.py`'s renderer has no mechanism to detect or correct a
  violation of this rule, by design (§5.1) — so it must be enforced entirely inside
  `workspace_home.py`, and covered by the snapshot-level tests in §14.

---

## 7. Git/worktree discovery

**Gap:** `app.py`'s `run_git_command()`/`get_git_status()`/`get_git_worktrees()`/etc. all hardcode
`cwd=ROOT` — they report on the AI Command Center's own repository, not on any of the six managed
projects' repositories. Today's "Workspace Launcher" page's "Git worktrees" section is therefore
showing the *wrong* repo for this use case; it happens to look plausible because ROOT itself is a
git repo, but it is not per-project.

**NEW module: `command_center/git_info.py`** — extract the existing helpers, parameterized by
`cwd: Path` instead of the module-level `ROOT` constant:

```python
def run_git_command(cwd: Path, args: list[str], timeout: int = 5) -> subprocess.CompletedProcess | None
def get_status(cwd: Path) -> dict
def get_worktrees(cwd: Path) -> list[dict]
def get_log(cwd: Path, limit: int = 20) -> list[dict]
# ... get_diff_stat, get_branches, get_remotes, same signatures + cwd
```

Identical subprocess-safety properties as today (fixed argv, never `shell=True`,
`capture_output=True`, `text=True`, explicit timeout, read-only subcommands only — the exact list
already documented in ARCHITECTURE.md §6: `rev-parse`, `branch --show-current`,
`status --porcelain`, `log`, `diff --stat`, `remote -v`, `worktree list --porcelain`). Zero new
subcommand classes.

`app.py`'s Git Center and Workspace Launcher pages become thin call sites:
`git_info.get_worktrees(ROOT)` reproduces today's exact behavior — a pure refactor, zero
user-visible change (§17 step 1).

Workspace Home calls `git_info.get_worktrees(Path(cfg["repository_path"]))` once per project
**that has a configured repository path**. Before shelling out, reuse
`project_config.validate_repository_path()` (already exists, already handles "not absolute" /
"doesn't exist" / "not a directory") to short-circuit a stale/moved path into a clean "path no
longer valid" card state instead of a raw subprocess failure.

### 7.1 Repository configuration baseline (resolves F3)

The prior revision of this document claimed "5 of 6 projects" lack a configured
`repository_path` today, presented as a fact read directly from code. That claim was incorrect
and has been removed: `repository_path` configuration lives in `data/project_config.json`, which
is **gitignored, user-machine-specific state** (`command_center/project_config.py`'s own
docstring: "Local, machine-specific configuration... is stored in `data/project_config.json`,
which is gitignored"). On a fresh checkout of this branch, that file does not exist, and
`load_project_configs()` returns `repository_path: None` for **all six** projects.
`discover_candidate_repository_path("AIOS")` never changes this by itself — it only returns a
*candidate suggestion*, surfaced in the settings UI, that requires an explicit user save via
`save_repository_path`; nothing in `project_config.py` writes a path automatically.

The architecture therefore treats the following as equally normal, equally supported states, not
as an edge case relative to some "mostly configured" baseline:

- **all six projects unconfigured** (the default fresh-checkout state, and the state Workspace
  Home's empty-state design must be primarily verified against);
- **most projects unconfigured, one or two configured**;
- **a project configured with a path that no longer exists or was moved** (caught by
  `validate_repository_path`, rendered as "path no longer valid," not a raw error);
- **a project configured with a path that exists but is not a git repository** (`validate_repository_path`
  checks existence/directory-ness, not git-repo-ness — `git_info.get_status()`'s
  `rev-parse --show-toplevel` failure is what actually detects this; rendered as "not a git
  repository" rather than a worktree list);
- **a project with a fully valid, git-backed `repository_path`.**

No default repository path is ever invented for any project (`discover_candidate_repository_path`
remains the only exception, and even it requires the path to independently exist and contain
`.git` before it is ever surfaced as a suggestion — never as configuration). One project failing
validation must never block rendering of the other five project cards (per-project error
isolation, §6/§12).

---

## 8. Runtime integration

Active Runs and part of Recent Runs come from the v2 Session Supervisor via
`ExecutionCenterAPI` — the one application-facing launch/list surface, per its own module
docstring ("the only route application code should use").

### 8.1 `list_runs` signature extension (resolves F1)

Both layers change together, additively:

**`command_center/runtime/db.py`** (repository-aligned typing: this module already uses
`from typing import Any, Callable, Iterator, TypeVar`; `command_center/storage.py` already
imports `Iterable` for exactly this kind of "collection of values" parameter — `states` follows
that existing convention rather than introducing `Collection` as a new import):

```python
def list_runs(
    db_path: Path,
    *,
    session_id: str | None = None,
    task_id: str | None = None,
    state: str | None = None,
    states: Iterable[str] | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Additive extension of the existing `list_runs`. `state` (singular) is unchanged
    and remains for exact backward compatibility with every existing call site (see §16
    for the full, re-audited call-site inventory). `states` (plural) is new: a run
    matches if `state IN (...)`. Passing both `state` and `states` raises `ValueError`
    at the top of the function, before any SQL is built — the two parameters are
    mutually exclusive, not merged/ORed, so a caller can never end up with an
    ambiguous filter it didn't ask for. `limit`, if given, is appended as a SQL
    `LIMIT ?` — bounding the result set inside SQLite, not truncating a
    Python list after a full-table fetch. Ordering is unchanged
    (`ORDER BY created_at DESC`, already existing, applied before `LIMIT`). No schema
    change: this touches only the Python query-building function, not the `run` table
    or any index."""
```

**`command_center/runtime/api.py`** — the facade must be extended with the identical two
parameters, forwarding them unchanged to `db.list_runs`. This is the piece the prior revision of
this document omitted (independent review finding F1): the read model is required by §6 to call
only through `ExecutionCenterAPI`, so a `db.py`-only extension is unreachable from Workspace Home.

```python
def list_runs(
    self,
    *,
    session_id: str | None = None,
    task_id: str | None = None,
    state: str | None = None,
    states: Iterable[str] | None = None,
    limit: int | None = None,
) -> list[dict]:
    return db.list_runs(
        self.db_path,
        session_id=session_id,
        task_id=task_id,
        state=state,
        states=states,
        limit=limit,
    )
```

Workspace Home's read model calls the facade, never `db.py` directly (§6):

```python
active_runs = execution_center_api.list_runs(
    states=EXECUTION_CENTER_ACTIVE_STATES,
    limit=active_runs_limit,
)
recent_runs = execution_center_api.list_runs(
    states=runtime_db.TERMINAL_STATES,
    limit=recent_runs_limit,
)
```

This is the exact, final approved call shape for both Active Runs and Recent Runs.

- **Active Runs** = `list_runs(states=EXECUTION_CENTER_ACTIVE_STATES, limit=N)`. Today
  `EXECUTION_CENTER_ACTIVE_STATES = frozenset({"PREPARED", "QUEUED", "RUNNING"})` is defined in
  `app.py` (line ~963) as a UI-only constant. It should move to `command_center/runtime/db.py`
  next to the existing `TERMINAL_STATES` (its natural home — both are run-state semantics, not UI
  concerns), so the new non-UI read model and `app.py` import the same source of truth instead of
  `app.py` owning knowledge a `command_center` module now also needs.
- **Recent Runs** = `list_runs(states=TERMINAL_STATES, limit=N)` **merged** with
  `agent_runner.load_runs()`'s most recent N — two genuinely separate systems, both still live
  today (v1.2's "AI-агенты" page still launches synchronously via `agent_runner.run_claude_code`;
  v2's "Live Execution Center" launches async via the Supervisor). Each merged row is tagged
  `source: "v1.2"` or `source: "v2"` so:
  - the UI can badge them distinctly,
  - click-through routes to the *correct* detail page — `runs` for v1.2 (JSONL `id`,
    `models.new_run_record`) vs `execution_center` for v2 (SQLite `run.id`) — getting this wrong
    means a broken or misleading link, not just a cosmetic issue.

  **Identity correction (resolves F5):** the prior revision of this document claimed v1.2 and v2
  run ids have a "disjoint id namespace/shape." That is factually incorrect and has been removed.
  Both systems generate ids via the *identical* function, `command_center.models.new_id()`
  (`uuid.uuid4().hex`, a 32-character lowercase hex string) — `command_center/models.py`'s
  `new_run_record` (v1.2) and `command_center/runtime/db.py`'s `create_run` (v2, which imports
  `new_id` from `command_center.models`) both call it. The two id spaces are therefore
  **shape-identical**; only the explicit `source` tag actually distinguishes them, and it is the
  *only* thing that may be relied on to do so. Concretely:
  - a merged Recent Runs row's identity, everywhere in the read model and the UI, is the tuple
    `(source, run_id)`, never bare `run_id`;
  - deduplication (e.g. folding a run that appears in both a "just launched" `session_state` hint
    and a fresh `list_runs()` read) must compare `(source, run_id)`, never `run_id` alone;
  - any Streamlit widget `key=` derived from a merged run (buttons, expanders) must include both
    `source` and `run_id`, e.g. `f"home_run_{source}_{run_id}"`, not `f"home_run_{run_id}"` —
    otherwise a v1.2 run and a v2 run that happen to share the same 32-hex-char value (astronomically
    unlikely for any single pair, given 128 bits of randomness, but not structurally excluded the
    way "disjoint namespaces" would have implied) collide on widget identity;
  - click-through routing dispatches on `source` explicitly (`"v1.2"` → `runs` page, `"v2"` →
    `execution_center` page), never by inspecting the id's shape (there is no shape difference to
    inspect).
- **Explicitly not done:** Home never triggers `legacy_import.py`'s v1.2→v2 import as a side
  effect of being viewed. That module's import is a deliberate, idempotent, explicitly-triggered
  operation; running it silently on every Home render would be a write during what must stay a
  read-only page (§6).
- **Cancellation is out of scope for Home.** A card may link into Execution Center (where
  cancellation's confirmation gate already lives via `request_cancel(..., confirmed=...)`), but
  Home itself never renders a cancel button that could auto-confirm or bypass that gate.

---

## 9. Artifact discovery

Explicit definition, since the brief lists Artifacts and Reports as two distinct sections:

- **Artifacts** = generated task files, `generated/<PROJECT>/*.md`, via
  `list_markdown_files(GENERATED_DIR)` — identical source to today's "Сгенерированные задачи"
  page, just a bounded recent-N slice with a "View all" deep link (`pending_nav="generated"`).
- **Reports** = run-linked structured reports, `reports/<PROJECT>/*.md`, joined against
  `agent_runner.load_runs()`'s `report_path` (v1.2) and `ExecutionCenterAPI.get_report(run_id)`
  (v2, `report.path` column) for verdict/severity badges via `report_parser` — identical source
  to today's "Отчёты" page, same bounded-slice-plus-deep-link treatment.

No new file-classification logic. `list_markdown_files`'s existing mtime-descending sort is
reused verbatim; Home does not introduce a second sort order to keep track of. For sensitive
projects, both lists pass through the artifact/report branch of `sanitize_workspace_project_entry`
(§5.1) before reaching the renderer — file paths and filenames are replaced with a generic label,
never rendered raw on Home for BANK/LEGAL.

**Deferred backlog, not solved by this increment:** `list_markdown_files` has no explicit maximum
recursion depth, no per-file size cap, and no documented symlink-following policy. This is
*pre-existing, already-shipped, already-tested behavior* — Increment 1 reuses it verbatim and does
not expand its blast radius (Home applies the same `rglob` call the `generated`/`reports` pages
already pay for on every render today, just capped to a smaller display slice before rendering).
Hardening this function (depth cap, size cap, explicit symlink policy) is out of scope for
Workspace Home and is tracked as backlog (§17) rather than presented as resolved.

---

## 10. Activity model

Home's Recent Activity is `activity_log.load_activity(limit=N)` — the typed event log
(`models.ACTIVITY_EVENT_TYPES`), the same source Dashboard and (indirectly, via
`build_timeline_events`) Timeline already use. Prefer this lighter call over recomputing
`build_timeline_events` on Home: that function does a full 3-way merge over *all* tasks, *all*
v1.2 runs, and *all* activity events, appropriate for the dedicated Timeline page, too expensive
to pay again on a page that reruns on every interaction. **This design decision is preserved
unchanged from the prior revision** — Home remains an explicitly limited/bounded feed, not a
second implementation of the full timeline, and does not claim to be one.

**Gap this design must account for, not silently ignore:** v2 Supervisor lifecycle events
(`process_started`, `process_exited`, `cancel_requested`, `reconciliation_orphaned`,
`reconciliation_classified`, ...) are written only to the `run_event` SQLite table — never to
`activity.jsonl`. A Recent Activity feed built purely from `activity_log.load_activity()` would
therefore show v1.2 activity (which does log into `activity.jsonl` — `manual_field_correction`,
`next_task_created`, etc.) but be silently blind to all v2 run activity. Fix: derive display-only
activity rows from the **already-fetched, already-redacted** Recent Runs list (§8) — a v2 run's
`started_at`/`completed_at`/`state` transitions become synthesized activity rows, merged and
re-sorted with the real `activity_log` events before display. This is read-model composition
(folding two already-fetched lists together), not a new data source or a write to any log. Because
the source list is already-redacted (§5.1), the derived activity rows inherit that redaction for
free — there is no second place this needs to be enforced.

---

## 11. Quick Actions

Every action reuses an existing mutation entry point; no new business logic is introduced.

| Action | Mechanism | Reuses |
|---|---|---|
| New Task | stage `pending_nav="create"` (+ `pending_create_project`) | existing `create` page |
| Launch Agent Run (v2) | stage `pending_nav="execution_center"` + **NEW** `pending_exec_center_project` | `render_execution_center_launch_form` |
| Open Project | stage `pending_nav="projects"` + `pending_project_browser=<id>` | exact existing Workspace Launcher behavior |
| Resume/View Session | stage `pending_nav="execution_center"` + reuse **existing** `pending_exec_center_run` (already used internally at `app.py:1100` after a fresh launch — do not invent a parallel key); routing must dispatch on the run's `source` tag first (§8) to land on the correct existing detail page for v1.2 vs v2 | `render_execution_center_watch` (v2) / existing `runs` page (v1.2) |
| View Report / Artifact | stage `pending_nav="reports"`/`"generated"` | existing pages, existing project filter |
| Configure repository path | stage `pending_nav="projects"` + `pending_project_browser=<id>` | existing "Настройки репозитория" tab |

`pending_exec_center_project` is the one genuinely new pending key, following the exact existing
convention (add to `_PENDING_KEY_MAP`, consume it before any widget is instantiated, per
ARCHITECTURE.md §5).

**No Quick Action performs a mutating call directly from Home's render path.** Every mutation
(launch, cancel, save-repo-path) navigates to the existing page whose form already implements the
confirm-then-execute gate (`context_service.require_launch_confirmation`, the sensitivity
checkbox, the disabled-until-ready button). Home may pre-fill a selector via a pending key, exactly
as Workspace Launcher already does for "Новая задача" today — it must never pre-check a
confirmation box or auto-submit on the user's behalf. Quick Actions never carry document content
(prompt, report body, etc.) through a pending key — only ids and navigation targets — so the
redaction stage (§5.1) has no bearing on this section: there is nothing here for it to redact.

---

## 12. Performance strategy

- Same execution model as every other page: Streamlit reruns `app.py` top-to-bottom on every
  interaction (ARCHITECTURE.md §2) — Home's read model runs once per rerun, nothing new here.
- **Bound every list.** `list_runs(states=..., limit=20)`, `activity_log.load_activity(limit=20)`,
  `list_markdown_files(...)[:20]` — never load full history into a page rendered on every
  interaction. This is the direct fix for the pattern already present at `app.py:1990`
  (`list_runs()[:20]`, loads the whole table today).
- **`db.list_runs` / `ExecutionCenterAPI.list_runs` extension (§17 step 2, additive only — see §8.1
  for exact signatures):** `states` (an iterable) maps to `state IN (...)`, `limit` appends
  `LIMIT ?`. `state` (singular) stays for backward compatibility with all existing call sites
  (§16, re-audited in full — none pass positional args, all keyword-only — safe to extend). Both
  the `db.py` query function and the `api.py` facade change together; extending only one leaves
  the other unreachable from the read model (§6 restricts the read model to the facade).
- **Git worktree discovery** costs one subprocess spawn per configured project (bounded by
  `len(PROJECT_IDS)` = 6 today). Cache with `st.cache_data(ttl=15)` — a genuinely **new pattern**
  for this codebase (today only `st.cache_resource` exists, for the Supervisor singleton) — worth
  calling out explicitly rather than presenting as if precedented. 10–20s TTL: fresh enough that a
  worktree added moments ago in a terminal shows up quickly, long enough to avoid 6 subprocess
  spawns on every widget interaction within one browser session. `validate_repository_path`'s
  cheap local-filesystem pre-check (§7) means an unconfigured or stale-path project never reaches
  the subprocess call at all, keeping the worst-case wall-clock cost bounded by the count of
  *validly-pathed* projects, not all six.
- **Artifacts/Reports** reuse the exact `rglob` calls the `generated`/`reports` pages already pay
  every render — no new cost class, just capped to the display limit before rendering (rendering
  is the expensive part in Streamlit, not the file stat).
- **Redaction (§5.1) is in-memory dict/string filtering** — no additional I/O, subprocess, or
  database call; its cost is negligible relative to the calls that produced the data being
  filtered.
- **No new background thread/poller.** Unlike Execution Center's `st.fragment(run_every=2.0)`
  live-watch (appropriate for one open run), Home is a summary page — auto-refresh is explicitly
  out of scope (§19); a manual "Refresh" button (`st.rerun()`) is sufficient and matches the
  top-to-bottom rerun model used everywhere else.

---

## 13. Security model

- Home is entirely read-only except Quick Actions, which delegate to already-reviewed, already-
  gated mutation entry points (§11) — this increment introduces **no new mutation surface**.
- **BANK/LEGAL sensitivity — the one genuinely new consideration this increment introduces, not a
  reuse of an existing guarantee.** Every existing sensitive-project boundary
  (ARCHITECTURE.md §11.3, `context_service.assemble_context`) was designed around *explicit,
  per-item confirmation before content reaches a model* — the threat model is "don't send this to
  Claude/OpenAI without the user choosing to." No existing page shows a rollup across *all*
  projects at once with *ambient* visibility (rendered on every visit, no explicit "open this"
  click). Home does exactly that.

  **The security boundary is the read model, not the renderer (resolves F2).** The independent
  review correctly found that specifying "must show metadata only" as a rendering-layer rule in
  `app.py`, backed only by a UI test, is not a structural guarantee — it relies on every present
  and future line of `app.py`'s Home section never being changed carelessly. This revision moves
  the boundary into `command_center/workspace_home.py`: `sanitize_workspace_project_entry` (§5.1)
  strips every non-allowlisted field for a sensitive project's runs/reports/artifacts/activity
  *before* they are placed into the snapshot dict `build_workspace_home_snapshot` returns.
  `app.py`'s renderer receives only already-redacted data for BANK/LEGAL entries — it has no
  reference to the raw `run`/`report` rows at all, so there is no code path in the renderer that
  could leak them, accidentally or otherwise. **`app.py` is not the primary security boundary for
  this page; it cannot be, by construction, because the data it would need to leak is never handed
  to it.**
- Concretely, for BANK/LEGAL, Home shows only the allowlisted fields from §5.1 (ids, states,
  timestamps, counts, verdict/severity enum badges, generic navigation targets) and never: prompt
  content, instruction text, `command_json`, stdout/stderr, event payload text, report
  body/findings text, generated file content, free-form failure detail, Git commit
  messages/subjects, or raw artifact/report file paths/filenames. This is enforced at the data
  layer (§5.1, §6), not left as documented-only intent, and is covered by two independent
  automated tests, not one (§14) — a snapshot-level test (asserting the *dict* never contains
  banned fields for a sensitive project) and a rendering-level test (asserting the *rendered
  output* never contains banned values either, as a second line of defense in case a future
  section is added to the renderer that reads a field the allowlist should have blocked but
  didn't). This dual-layer test requirement reflects this codebase's own documented history of
  exactly this class of gap being caught only in manual review (the `--tools` vs.
  `--disallowedTools` finding in ARCHITECTURE.md §11.3; the reports-dir test leak in
  `tests/conftest.py`'s `isolated_reports_dir` docstring) — one test layer alone was judged
  insufficient by the independent review, and is not repeated here.
- Repository paths are absolute local filesystem paths, already shown today (Workspace Launcher,
  Projects settings) — Home widens the surface (all projects, one page) but introduces no new
  exposure *class*; acceptable for a local, single-user, `localhost`-only app. Repository paths
  themselves are not treated as sensitive content and are not subject to the §5.1 allowlist (they
  are configuration, not run/report/artifact/activity content).
- No new subprocess call classes: worktree discovery reuses the exact read-only git subcommand
  allowlist already documented in ARCHITECTURE.md §6 — nothing else. Commit subjects (from
  `get_log`, not used on Home directly today, but available via `git_info`) are treated as
  potentially sensitive free text and are excluded from any Home rendering path for sensitive
  projects, matching the report/prompt exclusion above.
- Quick Action launch/cancel affordances must not pre-check the confirmation checkbox or
  auto-submit; they may only pre-select a project/run via a pending key (ids only, never content —
  §11).
- Run identity for BANK/LEGAL entries is subject to the same `(source, run_id)` rule as every
  other project (§8/F5) — sensitivity and identity are orthogonal concerns, and neither
  substitutes for the other.

---

## 14. Test strategy

Follows existing conventions exactly (plain pytest for `command_center/*`, `streamlit.testing.v1.AppTest`
for pages, `tests/conftest.py`'s fixtures, no real `claude` CLI ever invoked):

- **`tests/test_workspace_home.py`** (new, plain pytest) — build a scenario with `isolated_data_dir`:
  a couple of Kanban tasks, a v1.2 legacy run (`agent_runner.append_run`), a v2 run via
  `ExecutionCenterAPI.start_run` + the `fake_claude` fixture, some `activity_log` events, some
  generated/report files under `tmp_path`. Assert: snapshot shape, limits respected, states
  filtered correctly, source-tagging correct, unconfigured-repo projects degrade gracefully (no
  exception) — including the **all-six-unconfigured** case as the primary scenario, not just a
  spot check (§7.1/F3). Additional cases required by this revision:
  - `test_sanitize_identity_transform_for_non_sensitive_project` — for `AIOS`/`AICOS`/`BUSINESS`/
    `PERSONAL`, `sanitize_workspace_project_entry` returns every field unchanged.
  - `test_sanitize_strips_banned_fields_for_sensitive_project` — for `BANK`/`LEGAL`, construct a
    run/report/artifact/activity entry containing `prompt`, `command_json`, report body text, a
    real file path, and a `message` field; assert every one of those keys/values is **absent from
    the returned dict**, not merely blank/masked, and that the allowlisted fields (§5.1) are still
    present and correct.
  - `test_snapshot_never_contains_banned_fields_for_sensitive_project` — end-to-end through
    `build_workspace_home_snapshot` with a mixed sensitive/non-sensitive scenario: assert the
    *snapshot dict itself* (not the rendered page) has no banned field/value anywhere under a
    `BANK`/`LEGAL` entry, while a non-sensitive project's entries in the same snapshot retain full
    fields. This is the read-model-level assertion the independent review required in addition to
    the existing UI-level one.
  - `test_merged_run_identity_uses_source_and_run_id` — construct a v1.2 run and a v2 run with
    colliding `id`/`run_id` values (both 32-hex-char, per F5) and distinct `source` tags; assert
    the merged Recent Runs list treats them as distinct entries (no accidental de-dup / no
    overwritten row), and that each row's derived Streamlit widget key differs.
- **`tests/test_git_info.py`** (new) — parameterize over the existing `git_repo` fixture, plus a
  `git worktree add` scenario and a missing/unconfigured-path scenario, a valid-but-non-git
  directory scenario (§7.1), and a detached-HEAD scenario; assert graceful empty/flagged results,
  never a raised exception (matches every existing git helper's `None`-on-`OSError`/
  `SubprocessError` convention).
- **`tests/test_runtime_db.py`** (extend) — add cases for `list_runs(states=[...])` filtering,
  `limit` truncation/ordering, and `ValueError` when both `state` and `states` are passed
  together (§8.1). *(Line-count claim removed — see §16.)*
- **`tests/test_runtime_api.py`** (extend) — mirror the same `states`/`limit`/mutual-exclusion
  cases through `ExecutionCenterAPI.list_runs`, since the facade is now a second place this logic
  is reachable from and must be independently verified, not assumed to inherit correctness from
  `db.py`'s test coverage alone.
- **`tests/test_workspace_home_ui.py`** (new, `AppTest`) — mirrors
  `tests/test_execution_center_ui.py`'s structure (including its `_fresh_execution_center_singleton`
  cache-clearing pattern). Cases: empty state (**all six projects unconfigured**, nothing else
  configured either — the default scenario, §7.1), populated state, and a BANK/LEGAL-only state
  that asserts **both**:
  1. the underlying snapshot passed to the renderer contains no banned field (reusing the
     `workspace_home.py`-level assertion helper from `test_workspace_home.py`, not re-deriving it), and
  2. the rendered page text/markup contains no banned value (`AppTest`'s rendered-element text),
     as the second, independent line of defense described in §13.
- No test invokes the real `claude` CLI — reuse `fake_claude`/`fake_claude_tree` exactly as today.
- Baseline today: 333 tests, ~16-17s (re-verified, §18) — the currently verified count, not a
  permanent one; it will grow as tests are added on this and other branches, so report the actual
  number from whatever run validates the implementation rather than trusting this figure. Home's
  addition should stay in that ballpark. This document also does not restate an approximate line
  count for any existing test file as evidence, since that class of claim was found to be
  unreliable (§16, F6).

---

## 15. Capability matrix

| Home section | Existing capability | Reuse level | New code needed |
|---|---|---|---|
| Projects | `project_config.load_project_configs`, `load_tasks` | Full | none — composition only |
| Active Git Worktrees | `get_git_worktrees` (ROOT-only) | Partial | `command_center/git_info.py` (parameterized), thin wrappers for 2 existing call sites |
| Active Runs | `ExecutionCenterAPI.list_runs` | Full, needs filter extension | additive `states`/`limit` on **both** `db.list_runs` and `ExecutionCenterAPI.list_runs` |
| Recent Runs | `ExecutionCenterAPI.list_runs` + `agent_runner.load_runs` | Full | small merge/sort/tag function, `(source, run_id)`-keyed |
| Recent Activity | `activity_log.load_activity` | Full, needs v2 folding | small derive-from-recent-runs helper (no new storage) |
| Artifacts | `list_markdown_files(GENERATED_DIR)` | Full | none (redaction applied at fold-in, §5.1) |
| Reports | `list_markdown_files(REPORTS_DIR)` + `report_parser` | Full | none (redaction applied at fold-in, §5.1) |
| Sensitivity redaction | none — genuinely new | New | `sanitize_workspace_project_entry` (§5.1), the one net-new piece of business logic in this increment |
| Quick Actions | `pending_nav` pattern, existing forms | Full | one new pending key |

---

## 16. Risks

- **Two parallel run systems is the single biggest architectural risk this increment surfaces.**
  Home is the first page required to show v1.2 (sync, JSONL, no cancel) and v2 (async, SQLite,
  cancellable) runs side by side. Mitigated by explicit source badges + correct per-source
  click-through routing (§8), with identity now explicitly specified as `(source, run_id)` rather
  than relying on any structural difference between the two id spaces (§8/F5 — the id spaces are
  in fact shape-identical, so `source` is load-bearing, not a convenience label). The underlying
  question — merge, migrate, or formally retire one system — is a product decision flagged for a
  follow-up increment, not solved here.
- **Sensitive-project ambient-visibility gap (§13) is a genuinely new exposure class**, not a reuse
  of an existing guarantee. This revision resolves the structural gap the independent review found
  (redaction now lives in the read model, §5.1, not only in the renderer) and requires two
  independent automated regression tests (§14: snapshot-level and rendering-level), given this
  project's own history of exactly this kind of boundary gap being caught only in manual review.
- **Git worktree discovery cost scales with configured-project count × subprocess latency** — fine
  at today's scale (6 projects, local disk), would degrade on networked filesystems or a much
  larger project list; mitigated by `st.cache_data(ttl=15)` (§12) and the `validate_repository_path`
  pre-check (§7, §12), documented as a scaling assumption rather than solved generally.
- **`db.list_runs`/`ExecutionCenterAPI.list_runs` signature change** — additive only on both
  layers. Full re-audited call-site inventory (supersedes the prior revision's approximate "4 call
  sites" claim, which the independent review found undercounted the real total by missing a
  production call site):

  | Call site | File | Call shape | Kwargs-only? |
  |---|---|---|---|
  | `Supervisor.reconcile()` | `command_center/runtime/supervisor.py:602` | `db.list_runs(self.db_path, state="RUNNING")` | Yes |
  | `ExecutionCenterAPI.list_runs` (the facade itself) | `command_center/runtime/api.py:113` | `db.list_runs(self.db_path, session_id=session_id, task_id=task_id, state=state)` | Yes |
  | Workspace Launcher / Recent Runs widget | `app.py:1990` | `execution_center_api.list_runs()[:20]` (through the facade) | Yes (no args) |
  | `execution_center_debug.py` CLI | `scripts/execution_center_debug.py:146` | `api.list_runs(session_id=args.session_id, task_id=args.task_id, state=args.state)` | Yes |
  | Test | `tests/test_runtime_api.py:46,48,50` | `api.list_runs()`, `api.list_runs(session_id=...)`, `api.list_runs(state="COMPLETED")` | Yes |
  | Test | `tests/test_runtime_db.py:628` | `db.list_runs(path, session_id=session["id"])` | Yes |
  | Test | `tests/test_runtime_legacy_import.py:68` | `db.list_runs(db_path)` (length check only) | Yes |
  | Test | `tests/test_runtime_supervisor.py:118` | `db.list_runs(sup.db_path) == []` | Yes |

  Every real call site — including `supervisor.py`'s own internal reconciliation call, which the
  prior revision's audit did not mention at all — passes keyword arguments exclusively, so the
  additive `states`/`limit` extension on both `db.py` and `api.py` is backward compatible with
  every one of them. This table is the audit; implementation should re-grep before landing §17
  step 2 to catch any call site introduced since this document was written, rather than trusting
  this table as permanently exhaustive.
- **Unconfigured `repository_path` is the common case today, and the fresh-checkout default is all
  six projects unconfigured** (§7.1/F3 — corrected from the prior revision's unverified "5 of 6"
  claim). Active Git Worktrees / Active Runs will be sparse or empty for most/all projects out of
  the box. This is expected, not a bug — the empty-state UX (§3) must make "configure a repository
  path" the obvious next step rather than reading as broken, and must be verified against the
  all-unconfigured case specifically, not just a partially-configured one.
- **Artifact scanning hardening (depth/size/symlink limits) is deferred**, not solved by this
  increment (§9) — tracked explicitly as backlog (§17) so it isn't mistaken for a resolved
  concern in a future review.

---

## 17. Recommended implementation plan

Small, independently testable, in dependency order. Split explicitly into what this increment
must build versus what is intentionally deferred, per the independent review's request.

### Required implementation changes

1. **Extract `command_center/git_info.py`** from `app.py`'s existing git helpers (lines 410–526),
   parameterized by `cwd: Path`. Repoint Git Center + Workspace Launcher at it with **zero
   behavior change** — pure refactor, own PR, verified by existing `test_app_streamlit.py`
   page-render tests passing unmodified. *(Condition 1, §1.)*
2. **Add `states`/`limit` to both `db.list_runs` and `ExecutionCenterAPI.list_runs`** (§8.1,
   exact signatures given there); move `EXECUTION_CENTER_ACTIVE_STATES` from `app.py` into `db.py`
   beside `TERMINAL_STATES`; update the `app.py:1990` call site; add the mutual-exclusion
   (`state` + `states` together → `ValueError`) check in `db.list_runs`. *(Condition 2, §1.)*
3. **Build `command_center/workspace_home.py`** — the read model (§5) and the sensitivity
   redaction stage `sanitize_workspace_project_entry` (§5.1) together, not as separable pieces:
   the redaction stage has no independent purpose outside the snapshot builder that calls it, and
   the snapshot builder is not complete/safe to use without it. Unit-test standalone (no
   Streamlit), including every case in §14's `test_workspace_home.py` list. *(Condition 3, §1.)*
4. **Add the `workspace_home` NAV entry** and a thin `app.py` renderer over the snapshot dict; add
   the `AppTest` UI test (including the dual-layer BANK/LEGAL regression case, §14).
5. **Wire Quick Actions** to existing `pending_nav` targets; add the one new pending key.
6. **Manual smoke pass**: all-six-unconfigured empty state, populated state, BANK/LEGAL-only
   state (visually confirm no banned field renders, in addition to the automated tests),
   partially-configured state, invalid-path state, non-git-directory state.

Steps 1–3 change nothing user-visible on their own (steps 1–2 are pure refactor/additive-API; step
3 is a new, unused-until-step-4 module) and can each ship ahead of the Home page itself —
lowest-risk sequencing, and all three are prerequisites for §7–§9/§13 regardless of when Home
itself lands.

### Deferred backlog (explicitly out of scope for this increment)

- Artifact scanning hardening: depth cap, per-file size cap, explicit symlink-following policy
  for `list_markdown_files` (§9).
- Runtime store migration or unification of the v1.2/v2 run systems (§8, §16) — a follow-up
  product decision, not an engineering task for this increment.
- A unified, persisted, single-source timeline (§10) — Home's Recent Activity remains an
  explicitly limited/bounded feed, not a replacement for `build_timeline_events`.
- Worktree mutation of any kind (create/remove/checkout) — Increment 1 is discovery-only,
  read-only git subcommands exclusively (§7, §13).
- Retry/resume orchestration beyond what `ExecutionCenterAPI`/`Supervisor` already implement.
- Multi-agent orchestration of any kind.
- Real-time worktree change detection (filesystem watchers) — polling/manual refresh only (§12,
  §19).
- Merging/deprecating "Обзор" (Dashboard) or "Workspace Launcher" in favor of Home (§3, §19) — a
  product decision for a future increment.

---

## 18. Acceptance criteria

- All 8 required sections render for a workspace with ≥1 configured project, ≥1 active v2 run,
  ≥1 completed run in each system, ≥1 artifact, ≥1 report, ≥1 activity event — no exception.
- Empty state (fresh checkout, **all six projects unconfigured** — the corrected default per
  §7.1/F3, not "5 of 6") renders without exception, using the existing `st.info(...)`
  "nothing here yet" convention.
- Active Runs shows only v2 runs in `{PREPARED, QUEUED, RUNNING}`; a run completing between two
  renders moves from Active to Recent on the next rerun with no extra logic.
- Recent Runs source-tags and routes v1.2 vs v2 runs correctly, keyed on `(source, run_id)`
  throughout (§8/F5); no dead/wrong-page links, no identity collision even under a same-value-id
  coincidence between the two systems.
- **The Workspace Home snapshot dict itself** (not only the rendered page) contains no
  prompt/instruction/command/log/report-body/raw-file-path content for BANK/LEGAL entries —
  verified by a snapshot-level test (§14), independent of the existing rendering-level test.
- BANK/LEGAL entries never render report/run/prompt body text inline on Home, verified by an
  automated rendering-level test in addition to the snapshot-level one (§13/§14).
- Every Quick Action lands on the correct existing page/form, pre-filled where specified, with
  zero auto-submitted mutation.
- `db.list_runs(states=[...], limit=...)` **and** `ExecutionCenterAPI.list_runs(states=[...],
  limit=...)` are both correctly filtered/bounded/ordered; existing no-arg and single-`state`
  call sites (§16's full inventory) are unaffected (regression tests pass); passing `state` and
  `states` together raises `ValueError`.
- `python -m compileall -q .`, `ruff check .`, `pytest -q` remain green (currently: green,
  333 tests, ~16-17s — the currently verified baseline; re-verify against the actual run at
  implementation time, since the count will have grown by then).
- No new subprocess call site outside the existing read-only git-subcommand allowlist.

---

## 19. Explicit non-goals

- Not deciding or implementing a merge/deprecation of the v1.2 synchronous run system into v2.
- Not deciding whether Workspace Home replaces/subsumes "Обзор" (Dashboard) or "Workspace
  Launcher" — both keep working unchanged.
- No auto-refresh/live-polling on Home (that remains Execution Center's job for one open run);
  manual refresh only.
- No cross-project bulk actions (bulk cancel, bulk repository-path configuration).
- No new authentication, multi-user support, or network exposure beyond `localhost`
  (ARCHITECTURE.md §10 stands unchanged).
- No database schema migration — only additive, backward-compatible parameter changes to
  existing functions.
- No changes to Supervisor process-lifecycle, cancellation, timeout, or reconciliation logic.
- No triggering of `legacy_import.py` as a side effect of viewing Home.
- No real-time worktree change detection (filesystem watchers) — polling/manual refresh only.
- No artifact-scanning hardening (depth/size/symlink limits) — deferred backlog (§17).

---

## 20. Exact next implementation step

Land **§17 step 1** first: extract `command_center/git_info.py` from `app.py`'s
`run_git_command`/`get_git_status`/`get_git_worktrees`/etc. (`app.py:410-526`), parameterized by
`cwd: Path` instead of the hardcoded `ROOT`. Add `tests/test_git_info.py` covering the existing
`git_repo` fixture plus a `git worktree add` scenario, an unconfigured/missing-path scenario, a
valid-but-non-git-directory scenario, and a detached-HEAD scenario. Repoint the two existing call
sites (Git Center page, Workspace Launcher page) at the new module with no behavioral change,
verified by the existing `test_app_streamlit.py` page-render tests continuing to pass unmodified.
This is the smallest, most isolated, zero-user-visible-change piece of this plan.

Step 2 (§8.1: additive `states`/`limit` on both `db.list_runs` and `ExecutionCenterAPI.list_runs`)
and step 3 (§5/§5.1: `workspace_home.py` with the sensitivity redaction stage built in from the
start, not retrofitted) should follow before any Streamlit UI work begins — §7–§9/§13 of this
design all depend on per-repository-path git discovery, the extended runtime query surface, and
the redaction stage existing before Workspace Home itself can be built or reviewed for security.

---

## Read-only validation (run against current HEAD, before any implementation change)

```
$ python -m compileall -q .
(exit 0, no output)

$ python -m ruff check .
All checks passed!
(exit 0)

$ python -m pytest -q -n 8
333 passed in 17.28s

$ git diff --check
(exit 0, no output — no whitespace errors)

$ git status --short --branch
## feature/v3-workspace-home-architecture...origin/main
?? WORKSPACE_HOME_ARCHITECTURE.md
(clean — no other output; this file remains the only untracked/changed file)
```

**333 is the currently verified baseline**, not a permanent figure — the count will keep moving as
tests are added on this and other branches. Do not treat the number above as authoritative at
implementation time: re-run `pytest -q -n 8` against whatever HEAD is current and report the
actual result, the same way this number was produced.

---

## Verdict

**APPROVED FOR IMPLEMENTATION** — confirmed by a second, independent architecture review after
F1–F5 (from the first review) were verified fully **RESOLVED**. The three items in §1 (git helper
extraction, additive `db.list_runs`/`ExecutionCenterAPI.list_runs` extension, sensitivity-aware
read model with `sanitize_workspace_project_entry`) remain the required first three implementation
steps in §17 — they are prerequisite sequencing, not unresolved architecture conditions blocking
this approval. The two LOW-severity, non-blocking observations from the second review (stale
`app.py` line citations; a stale test count) are corrected in this revision. No architecture
decision changed as a result of either correction. §17 step 1 may begin immediately.
