# Founder Functional Audit 9761459 — Backlog Reconciliation

Reconciliation of all 33 task candidates in `FOUNDER_FUNCTIONAL_AUDIT_TASKS_9761459.json`
against the current repository state.

## Baseline

| | |
| --- | --- |
| Audit HEAD (historical) | `9761459` |
| Reconciled against | `origin/main` @ `5eed19c` (merge of PR #77, after the CI and launcher integration gates) |
| Worktree / branch | `ai-command-center-audit-reconciliation` / `audit/founder-backlog-reconciliation` |
| Rows reconciled | 33 |
| Runtime data touched | none — `data/` holds only tracked `*.example.*` files in this worktree; the package validation below ran with `AICC_DATA_DIR` redirected to a temp dir |

## Result

| Status | Count | Rows |
| --- | ---: | --- |
| Done | 14 | W0-001…W0-005, W1-001, W1-003, W1-008, W1-010, W2-003, W2-005, W2-007, W2-008, W3-001 |
| Still Open | 14 | W0-006, W1-002, W1-004, W1-005, W1-006, W1-007, W1-009, W2-001, W2-002, W2-004, W2-006, W3-002, W4-003, W4-004 |
| Superseded | 4 | W3-003, W3-004, W3-005, W4-002 |
| Duplicate | 1 | W4-001 |

Only the 14 **Still Open** rows are converted in
`FOUNDER_AUDIT_9761459_STILL_OPEN_IMPORT_PACKAGE.json`. Nothing was imported.

---

## Wave 0

### AUDIT-W0-001 — Ограничить сетевой bind Streamlit до localhost — **Done**

`.streamlit/config.toml` pins `[server] address = "localhost"` with an in-file rationale, so
even a bare `streamlit run app.py` is bound. `scripts/start-ui.sh` independently injects
`--server.address localhost` unless the operator passes their own. `README.md:60-65` and
`README.md:310-312` document the bind and the explicit override.
Git: `449377b` ("Launch/CI — pin server.address=localhost … so no launch path … exposes the
unauthenticated console").

### AUDIT-W0-002 — Блокировка записи в `tasks_repository.save_tasks` — **Done**

`tasks_repository.mutate_tasks` (line 222) runs every load→modify→save cycle inside
`tasks_lock` (`storage.file_lock`, an OS advisory lock), and `save_tasks` (line 199) delegates
to the shared fsync-ing `storage.atomic_write_json` instead of a weaker local writer.
Tests: `tests/test_tasks_repository_concurrency.py` —
`test_many_concurrent_processes_creating_tasks_lose_nothing`,
`test_create_update_delete_and_import_all_share_one_lock_and_lose_nothing`.
Git: `98d7714` (transactional task import + shared task-storage locking).

### AUDIT-W0-003 — Блокировка записи в `execution_queue.save_queue` — **Done**

`execution_queue.queue_lock` (line 219) + `mutate_queue` (line 237) hold a dedicated advisory
lock over the whole read-modify-write cycle; `save_queue` writes atomically.
Tests: `tests/test_execution_queue_concurrency.py` (12 tests), including
`test_enqueue_provides_real_cross_process_exclusion` and
`test_a_crashed_lock_holder_never_permanently_blocks_a_later_caller`.
Git: `aca5ae9`, `b0ec15e`, `79f74a2`, `c726fa8`.

### AUDIT-W0-004 — Уникальность ID задачи и `delete_task` — **Done**

`load_tasks` drops duplicate ids keep-first (`_dedupe_by_id`) and `delete_task` (line 441)
removes at most one record, both explicitly citing audit BLOCKER-3.
Tests: `tests/test_tasks_repository_blocker_3_4.py` —
`test_delete_task_removes_only_one_of_duplicate_ids`,
`test_load_tasks_dedupes_duplicate_ids_keeping_first`.
Git: `741164c` ("close audit BLOCKER-3 and BLOCKER-4").

### AUDIT-W0-005 — Валидация записей `tasks.json` при загрузке — **Done**

`tasks_repository.validate_tasks` (line 99) checks required fields, unknown `project` against
`models.PROJECT_IDS` (with an alias "did you mean?" hint) and duplicate ids; `load_tasks` runs
it on every read and surfaces each issue via `_warn_once` without crashing. `strict=True` on
the mutate path prevents a transient bad read from persisting `[]`.
Tests: `test_validate_tasks_flags_unknown_project_with_alias_hint`,
`test_validate_tasks_flags_missing_required_field`, `test_validate_tasks_flags_duplicate_ids`.
Git: `741164c`, `fdaf39e`.

### AUDIT-W0-006 — Containment-проверка для `project` в путях отчётов — **Still Open**

`runtime/reports.py:41-49` still builds `REPORTS_ROOT / project / …` straight from
`run.get("project")`, with no `models.PROJECT_IDS` allowlist and no `_assert_within_root`;
`agent_runner.report_path_for` (line 504) does the same. Only the **read** side is contained
(`resolve_report_path`, tested by `test_resolve_report_path_rejects_traversal_outside_reports_root`).
Practical mitigation, not the requested guard: `portfolio_launch.py:536-538` blocks a card whose
`project` has no mapped repository path, so an unmapped `../`-style project cannot reach a launch
today. The DoD's test — a Portfolio card with `../` in `project` cannot write outside `reports/` —
does not exist.

---

## Wave 1

### AUDIT-W1-001 — Авто-worktree в основном лаунчере — **Done**

`launch_service.prepare_launch` classifies an absent-but-provisionable workspace as
`PROVISIONABLE` (line 118), and `app.py:767-770` surfaces it as a recoverable notice so the
launch button stays enabled; `workspace_provisioning` performs the `git worktree add` with
containment and rollback. An explicit `workspace_path` still takes precedence unchanged
(`launch.resolve_workspace_path`).
Tests: `tests/test_workspace_provisioning.py` — `test_missing_workspace_is_created_automatically`,
`test_parallel_tasks_get_separate_worktrees`, `test_existing_correct_worktree_is_reused_untouched`.

### AUDIT-W1-002 — `scripts/start-task.sh` и полный реестр `PROJECT_IDS` — **Still Open**

The script's `case` still accepts only `AIOS`, `BANK`, `LEGAL` and exits 1 otherwise, and the
Create Task form still gates task persistence on the script exiting 0
(`app.py:4528` runs it, `create_task` only runs inside `if ok:`, `app.py:4556` errors otherwise).
`README.md:83-84` and `ARCHITECTURE.md:119` now honestly label it legacy, but that is
documentation of the gap, not a fix. The gap has widened since the audit: `models.PROJECT_IDS`
now holds 9 ids, not 6.

### AUDIT-W1-003 — Убрать legacy синхронный путь запуска — **Done**

`launch_service.execute_agent_launch`, `_apply_run_outcome_to_task` and `LaunchOutcome` are
deleted; `executors.claude_code.launch` now fails closed via `_v2_only` like every other
provider. `agent_runner.run_claude_code` is deliberately kept for Project Chat only.
Git: `9bd62fe` ("retire the legacy synchronous launch path (audit MAJOR-3)"), net -419/+54.

### AUDIT-W1-004 — Раздельные подтверждения branch-mismatch и dirty-tree — **Still Open**

`launch.validate_launch` reports `ISSUE_DIRTY_TREE` and `ISSUE_BRANCH_MISMATCH` as distinct
machine-readable codes (launch.py:114-115) and both are displayed, but `app.py:796-800` still
clears the whole set with one shared checkbox ("Я подтверждаю запуск несмотря на предупреждения
выше"). The DoD requires a separate explicit acknowledgement per condition.

### AUDIT-W1-005 — Защита от git worktree/branch операций над `main` — **Still Open**

`portfolio_launch._validate_branch_name` (line 190) only delegates to `git check-ref-format`,
which accepts `main`; there is no protected-branch list and no pre-`git worktree add` rejection.
Adjacent, non-substituting protection exists in `workspace_provisioning.MAIN_BRANCH_NAMES` /
`is_feature_task` for the *Kanban* launcher (`test_main_repository_cannot_be_used_for_a_feature_task`).
The DoD's test — a Portfolio task with `branch: main` rejected with a clear error before git is
called — does not exist.

### AUDIT-W1-006 — Восстановление после зависшего Portfolio claim-lock — **Still Open**

`portfolio_launch._claim` (line 344) states in its own docstring that an orphaned claim is
cleared by the operator deleting `data/portfolio_locks/<task_id>.lock` by hand and that "there is
deliberately no in-app helper for that". No staleness/age detection, no liveness check on the
owning process, no UI recovery action.

### AUDIT-W1-007 — Подтверждение перед удалением задачи — **Still Open**

`app.py:1268-1270`: the "Удалить" button calls `delete_task(task_id)` on first click. No
confirmation state, no dialog.

### AUDIT-W1-008 — Фоновая синхронизация run→task — **Done (opt-in by design)**

`task_pipeline.start_background_sync` / `stop_background_sync` add a bounded daemon poller
(one `tick` per 15s) reusing the host-wide `pipeline_lock`, started once per server process by
`app.py`'s `get_execution_center_api`.
Git: `c72c4ed` ("opt-in background sync daemon for headless hosts (audit MAJOR-8)").
**Caveat, recorded rather than hidden:** it is gated on `AICC_BACKGROUND_SYNC` and off by
default, so the interactive default remains page-driven — `reconcile_and_sync` is still called
only from `_render_live_execution_center_body` (app.py:3067). The finding's remediation shipped;
the DoD's literal scenario ("open Kanban and the fields are already synced") holds only with the
daemon enabled. See AUDIT-W4-003 for the part that stays open because of this.

### AUDIT-W1-009 — Pre-flight проверка бинарника `claude` — **Still Open**

`agent_runner.claude_cli_available()` exists (line 233) but its only caller is
`chat_service.py:109`. `render_agent_launcher` never calls it, so a missing `claude` on PATH is
still discovered after confirmation. (v2 providers resolve the binary at spawn time —
`providers.py:662` — which is not a pre-flight either.)

### AUDIT-W1-010 — Реальный batch-импорт задач — **Done**

`command_center/task_import.py` implements parse → validate → preview → apply for JSON/YAML/
Markdown/text packages, with a 5 MiB ceiling, per-task error reporting, project-id
normalization, id dedup, and an atomic apply held under the *shared* `tasks_repository` lock.
Surfaces: `app.py:4563` ("Импорт пакета задач") and `scripts/import_tasks.py` (`--dry-run` /
`--apply`). Docs: `docs/TASK_IMPORT_INTEGRATION.md`.
Tests: `tests/test_task_import.py` (42), `test_task_import_formats.py`,
`test_task_import_concurrency.py`, `test_import_tasks_cli.py`.
Git: `98d7714`, `df58024`.

---

## Wave 2

### AUDIT-W2-001 — Единая каноническая схема задачи — **Still Open**

The task shape is still three layers merged on read: base fields plus
`models.normalize_task_workflow` (line 176) plus `models.normalize_task_execution` (line 276).
There is no single dataclass/TypedDict, no task-level schema version, and no migration.
`tasks_repository.validate_tasks` added integrity *surfacing* (BLOCKER-4) but is not a schema.

### AUDIT-W2-002 — Обнаружение циклов зависимостей задач — **Still Open**

`models.unmet_dependencies` / `is_blocked` (lines 394-403) do a one-hop status check with no
cycle detection; `derive_dependency_edges` computes reverse edges only. Kahn-style cycle-safe
traversal exists only in `portfolio_intelligence` for Portfolio cards, over a different data
model. The Create Task dependency picker (`app.py:4545`) performs no cycle check.

### AUDIT-W2-003 — `repository_path` для всех проектов реестра — **Done (via the DoD's second branch)**

The DoD allows either configured paths or an explicit "not configured" marker. The marker
exists and is used throughout: `app.py:3265` `"unconfigured": "Путь к репозиторию не настроен"`
in the Workspace Home worktree-state labels, plus `app.py:675`, `1410`, `5204`.
`data/project_config.json` is machine-local and gitignored, so no repository-data change was
made or needed here.
Tests: `tests/test_workspace_home.py::test_snapshot_all_projects_unconfigured_renders_without_exception`.

### AUDIT-W2-004 — Project Intelligence / Recommendations на Workspace Home — **Still Open**

`render_workspace_home_page` (app.py:3301-3400) renders metrics, projects, active/recent runs,
artifacts and reports — and calls neither panel. Both are still wired only to the Kanban page
(`app.py:4669` `render_project_intelligence_strip`, `app.py:4678` `render_recommendations_panel`).

### AUDIT-W2-005 — Судьба Universal Workspace scaffolding — **Done (resolved by removal)**

The decision was made and executed: `command_center/workspace_context.py`,
`workspace_service.py` and `ui/panel_registry.py` no longer exist, and their tests went with them.
Git: `552f2d6` / `b798bf2` ("Remove the dead workspace/panel-registry cluster; fix the autonomy-UI
docs (audit H7/H8)").

### AUDIT-W2-006 — ahead/behind и `git fetch` в `git_info.py` — **Still Open**

`git_info.py` exposes exactly `run_git_command`, `get_status`, `get_log`, `get_diff_stat`,
`get_branches`, `get_remotes`, `get_worktrees`. No ahead/behind computation, no fetch of any
kind, no "updated N minutes ago" indicator.

### AUDIT-W2-007 — `#`-комментарии в парсере Portfolio-карточек — **Done**

`portfolio_models._parse_scalar` (lines 354-363) now fails closed on an inline `# …` comment in
an unquoted scalar with an explicit error naming the file and field, while a `#` inside a quoted
value stays literal text — one of the two outcomes the DoD accepts.
Test: `tests/test_portfolio_models.py::test_parse_card_rejects_inline_comment_in_unquoted_scalar`.

### AUDIT-W2-008 — Pause/Resume/Restart на карточке задачи — **Done**

The row is now headed **"Ручной статус (метка плана, не управление процессом)"**, the buttons are
"Приостановить" / "Возобновить" / "К перезапуску", each only setting a planning label, and a
caption states that a synchronous Claude Code run cannot be paused mid-flight and that real
cancellation lives on the Execution Center run card (`app.py:1211-1229`). The labels now match
the effect, which is the DoD.

---

## Wave 3

### AUDIT-W3-001 — Минимальный PySide6-каркас (D1) — **Done**

`command_center/desktop/` exists (`__main__.py`, `app.py`, `main_window.py`, `sidebar.py`,
`top_bar.py`, `theme.py`, `sections.py`, `settings.py`, `pages/`, `components/`), launched as
`python -m command_center.desktop`. `sections.py` encodes the nine IA sections with the three
active in D1. Tests: `tests/desktop/` (lifecycle, shell navigation, theme, top bar, settings
persistence). `docs/desktop/DESKTOP_INCREMENT_1.md`: "D1 (Native Shell Prototype, §2) has shipped".
Git: `449377b` (docs correction: the PySide6 shell "is Desktop Increment 1 — shipped").

### AUDIT-W3-002 — Портировать Workspace Home на desktop — **Still Open**

This is stage **D2** of the frozen scope, explicitly "not yet implemented"
(`DESKTOP_INCREMENT_1.md` header and §3). `desktop/pages/home.py` is still an `EmptyState`
placeholder whose own docstring says D1 forbids real data wiring. The row remains valid and in
scope — it is simply the next stage.

### AUDIT-W3-003 — Портировать Tasks/Kanban на desktop — **Superseded**

Superseded by the frozen desktop scope. `desktop/sections.py` transcribes the binding nine-section
IA — Home, Projects, Sessions, Execution, Git, Artifacts, Reports, Agents, Settings — and there is
no Tasks/Kanban section at all. `DESKTOP_INCREMENT_1.md` §0 (binding decision 11) makes the desktop
app read-only across all of D1–D4 except repository-path config, theme and window geometry, so the
row's DoD (desktop create/status/delete of tasks against the shared store) is excluded by decision,
not by omission. Re-raise as a post-Increment-1 item if still wanted.

### AUDIT-W3-004 — Портировать Execution Center на desktop — **Superseded**

`DESKTOP_INCREMENT_1.md` §1 (binding decision 12) puts "starting agents, run cancellation,
streaming/live output" out of scope for all of D1–D4, and `sections.py` renders the Execution
section disabled. The row's DoD (launch, cancel and monitor from the desktop app) is exactly the
excluded set.

### AUDIT-W3-005 — Портировать Git Center на desktop — **Superseded**

Same binding decision: "git writes of any kind" are out of scope for D1–D4 and the Git section is
rendered disabled. The read-only slice the row asks for is not separately scheduled in the frozen
D1–D4 plan; it also inherits AUDIT-W2-006, which is itself still open.

---

## Wave 4

### AUDIT-W4-001 — Определить механизм Self Backlog — **Duplicate of AUDIT-W1-010**

The row's own `adds` frames it as a decision between "via AUDIT-W1-010 batch import" and "a
separate self-backlog mechanism". That decision landed on the former and shipped: the audit
package format is imported through `command_center/task_import.py` (UI + `scripts/import_tasks.py`),
documented in `docs/TASK_IMPORT_INTEGRATION.md`. The row's DoD — a documented and implemented way
to bring such a backlog in through the product rather than around it — is met in full by
AUDIT-W1-010's delivery, and this reconciliation's own package is an instance of that path. No
residual work; nothing to import for this row.

### AUDIT-W4-002 — Прототип AI Orchestrator для очереди — **Superseded**

The orchestrator role shipped under a different, documented design than the one this row
specifies:
- `runtime/scheduler.py` — a deterministic, side-effect-free planner ("there is no hidden
  scheduler"; `plan()` never launches);
- `task_pipeline.tick` — an opt-in autopilot that, once `settings.enabled`, plans **and launches**
  directly (task_pipeline.py:1941), with safety carried by fail-closed persisted settings
  (`pipeline_settings.py`, every gate defaults off, non-`true` reads as `False`) and concurrency
  caps rather than a per-batch approval;
- `runtime/autonomy.py` + `ui/proposals_panel.py` — a RECOMMENDATION → APPROVAL → EXECUTION
  proposal layer (ADR 0005) that is an experimental foundation and is not wired to queue launches.

The row's DoD ("explicit approval for each batch of launches; tests confirm no auto-launch without
confirmation") contradicts the shipped autopilot's deliberate behaviour, so the row cannot be
closed as written. The residual safety gap it was protecting is carried by AUDIT-W4-004, which
stays open.

### AUDIT-W4-003 — Автоматический сборщик результатов run в Reports/Timeline — **Still Open**

Half delivered. The **report** half is done and is genuinely page-independent:
`Supervisor._supervise` persists the report at process exit (`supervisor.py:1899-1910` —
`reports.save_report` + `db.create_report`) on its own thread. The **Timeline** half is not
guaranteed: task-level timeline events are appended by `runtime/task_sync.py` (lines 143-361),
which runs page-driven from the Live Execution Center or via the opt-in `AICC_BACKGROUND_SYNC`
daemon (see AUDIT-W1-008). With the default configuration, a run that finishes while no page is
open leaves the report on disk but the task's Timeline unwritten until someone opens the app.

### AUDIT-W4-004 — UI подтверждения founder для оркестратор-инициированных запусков — **Still Open**

No batch-approval surface exists. `ui/proposals_panel.py` is a per-proposal Принять/Отклонить
inbox that deliberately does not dispatch execution, and the `task_pipeline` autopilot launches
directly once enabled without an intermediate screen listing the tasks/branches/worktrees it is
about to start. Its original dependency AUDIT-W4-002 is Superseded, so this row now stands on its
own as the missing confirmation surface over the *shipped* autopilot rather than over a
hypothetical one.

---

## Converted package

`FOUNDER_AUDIT_9761459_STILL_OPEN_IMPORT_PACKAGE.json` — 14 tasks, envelope form
(`schema_version: "1"`, `package_id: "founder-audit-9761459-still-open"`).

**Scope rule.** "Approved" is taken to mean *approved by this reconciliation*, i.e. exactly the
rows classified Still Open. Done, Superseded and Duplicate rows are excluded entirely.

**Field mapping** to the active `command_center.task_import` schema:

| Source field | Package field | Rule |
| --- | --- | --- |
| `id` | `id` | prefixed → `AICC-AUDIT-W…`, keeping row traceability and giving the store a project-scoped, dedup-stable identity |
| `project: "AI Command Center"` | `project` | canonical `"AICC"` (`models.PROJECT_IDS`), written explicitly rather than relying on alias normalization |
| `dependencies` | `depends_on` | filtered to ids present in this package; every dropped edge is preserved in `dropped_dependencies` |
| `worktree_branch` | `branch` | verbatim |
| — | `repository_path` | `/Users/dmitrijcernikov/Projects/ai-command-center` (the git common-dir owner for this checkout) |
| — | `workspace_path` | `/Users/dmitrijcernikov/Projects/ai-command-center-worktrees/<branch-with-slashes-as-dashes>`, matching the convention already in `git worktree list`; the main launcher provisions it automatically (AUDIT-W1-001) |
| — | `base_branch` | `main` |
| `goal`/`adds`/`fixes`/`definition_of_done` | `prompt` | composed brief, with this reconciliation's finding appended so the agent starts from current state, not the 9761459 snapshot |
| `status` | `status` | `Backlog` (unchanged, and a valid `models.KANBAN_STATUSES` value) |
| `source`, `target_version`, `parallel_group`, `component`, `release_note`, `wave` | preserved | carried as extra keys; the importer stores every unknown key under `record["metadata"]` |

**Dropped dependency edges** (dependency already Done, or Superseded):
`W1-007→W0-004` (Done), `W2-001→W0-005` (Done), `W3-002→W3-001` (Done),
`W4-003→W1-008` (Done), `W4-004→W4-002` (Superseded).
The single surviving edge is `AICC-AUDIT-W2-002 → AICC-AUDIT-W2-001`.

**Validation performed** (dry-run only, `AICC_DATA_DIR` redirected to a temp directory, no live
store read or written):

```
python3 scripts/import_tasks.py \
  docs/audits/FOUNDER_AUDIT_9761459_STILL_OPEN_IMPORT_PACKAGE.json --dry-run
→ Total tasks: 14 | New: 14 | Duplicates: 0 | Errors: 0 | Warnings: 0 | exit 0
```

**Not done, deliberately:** the package has not been imported. `apply` remains a founder action
(`scripts/import_tasks.py … --apply`, or the "Импорт пакета задач" uploader on the Create Task
page). Re-validate against the live store before applying — the dry-run above ran against an empty
temp store, so it proves schema validity, not id-uniqueness against current `data/tasks.json`.
