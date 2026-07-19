# AI Command Center — UX Implementation Roadmap

Status: **implementation-ready sequencing.** Splits `DESIGN_SYSTEM.md`, `KANBAN_REDESIGN.md`, and
`INTERACTION_MODEL.md` into six safe, independently-shippable increments (UX-1 through UX-6), plus
one forward-looking phase (UX-7) scoped separately below. Each of UX-1–UX-6 is scoped to land
without breaking the existing `AppTest` suite and without changing any data model, storage format,
or runtime behavior — only rendering, layout, and (where named explicitly) small, contained
interaction upgrades.

Sequencing rule: each increment depends only on the ones before it. None requires "finishing the
whole redesign" before it can ship and be used.

UX-7 is not part of that guarantee — it is documentation-only in this phase (matching
`docs/desktop/*`'s own D0 status) and is not scheduled against UX-1–UX-6's timeline. See §UX-7's
own status line below.

## UX-1 — Design tokens and App Shell

**Objective**: introduce the centralized theme module and the App Shell's structural regions
(Command Bar, grouped Sidebar, collapsible Execution Strip placeholder) without moving any page's
content into them yet.

- **Files likely affected**:
  - New: `command_center/ui/theme.py` (token constants + `inject_theme_css()`,
    `DESIGN_SYSTEM.md` §12.1).
  - New: `command_center/ui/app_shell.py` (Command Bar rendering, Sidebar grouping,
    `DESIGN_SYSTEM.md` §2.2–§2.3, §9.1–§9.3).
  - `app.py`: call `inject_theme_css()` once after `st.set_page_config`; replace the flat `NAV`
    `st.radio` rendering (`app.py:1816–1836`) with grouped rendering via `app_shell.py`; replace
    the per-page `st.title`/`st.caption` (`app.py:1793–1794`) with the Command Bar.
- **Architectural boundaries**: `theme.py` is the *only* file allowed to inject CSS
  (`DESIGN_SYSTEM.md` §12.1 — enforced by code review, not tooling, at this stage). `app_shell.py`
  reads `NAV` and `nav_page` exactly as `app.py` does today — no new routing keys, no renamed
  pages. The Execution Strip ships as a collapsed, mostly-empty placeholder in this increment
  (real content lands in UX-4); its only job here is to prove the bottom-docked CSS region works.
- **Acceptance criteria**:
  1. Every existing `NAV` entry renders under exactly one of the five groups
     (`DESIGN_SYSTEM.md` §2.2); none is dropped or duplicated.
  2. `nav_page` routing is unchanged — every existing page still renders for its existing key.
  3. The command palette (`Mod+K`) still opens and functions identically.
  4. Light and dark theme both render without unstyled/default-Streamlit fallback flashes.
  5. No `st.` widget outside `app_shell.py`/`theme.py` needs a `key=` change.
- **Tests**:
  - Extend `tests/test_app_streamlit.py`'s page-render smoke tests
    (`test_dashboard_renders_without_exception`, etc.) — should pass unmodified if routing is
    truly unchanged; treat any required test edit as a signal the increment overstepped scope.
  - New: one test asserting every `NAV` key appears in exactly one group
    (`DESIGN_SYSTEM.md` §9.2).
  - New: a manual check (not automated yet — UX-6 adds automation) of both themes at each
    breakpoint in `DESIGN_SYSTEM.md` §2.4.
- **Risks**:
  - `data-testid` selector drift across Streamlit versions (`DESIGN_SYSTEM.md` §12.4) — mitigate
    by pinning the Streamlit version tested against and noting the exact version in `theme.py`'s
    module docstring.
  - CSS fixed-positioning the Command Bar/Execution Strip inside Streamlit's own scrolling
    container can clip or overlap content unpredictably — mitigate by testing at all four
    breakpoints before merging, not just one.
- **Rollback**: revert `theme.py`, `app_shell.py`, and the three `app.py` call-site changes as one
  unit; `app.py`'s prior inline title/sidebar code is small enough to restore directly from git
  history rather than needing a feature flag.

## UX-2 — Project selector and KPI strip

**Objective**: retokenize the existing Project Selector and Project Intelligence strip onto UX-1's
theme; no structural change to either.

- **Files likely affected**: `command_center/ui/project_selector.py`,
  `command_center/ui/project_intelligence_panel.py` (visual/token changes only — retokenizing
  `st.pills` and `st.metric(border=True)` calls); no changes to
  `command_center/project_intelligence.py` (the read-model this panel renders — untouched).
- **Architectural boundaries**: neither file's public function signature changes
  (`render_project_selector(tasks, *, key=...)`,
  `render_project_intelligence_strip(tasks, *, project=...)`) — every existing call site
  (Kanban page, and any other page that reuses these) keeps working with zero edits.
- **Acceptance criteria**:
  1. Selecting a project still filters exactly the same downstream panels it does today.
  2. KPI tiles show identical values to today for identical input data (this is a pure visual
     pass — any value change is a bug).
  3. No-data states (`—` for `sprint_progress_pct` etc.) render as the Empty/no-data treatment
     from `DESIGN_SYSTEM.md` §9.5, not a bare em-dash with no visual context.
- **Tests**: existing Kanban-page `AppTest` coverage (which renders both components as part of
  page load) should pass unmodified — a required edit here is a scope-creep signal, same as UX-1.
- **Risks**: low — this is the lowest-risk increment in the roadmap, since both components are
  already extracted, already have stable function signatures, and change is purely cosmetic.
- **Rollback**: revert the two files independently; no cross-file coupling to unwind.

## UX-3 — Kanban layout and task cards

**Objective**: the highest-value increment — implements `KANBAN_REDESIGN.md` in full: fixed
column widths + horizontal scroll (§1), the compact task card (§2.1), and extraction of
`render_task_card` into `command_center/ui/task_card.py`. The Inspector Panel itself (where the
expanded card variant lives) is **not** part of this increment — see UX-5.

- **Files likely affected**:
  - New: `command_center/ui/task_card.py` (extracted from `app.py:862–1048`), exposing
    `render_task_card_compact(...)`.
  - `app.py`: the Kanban board loop (`app.py:2440–2460`) switches from `st.columns(len(KANBAN_
    COLUMNS))` to the fixed-width/scroll layout (`KANBAN_REDESIGN.md` §1.1); calls
    `task_card.render_task_card_compact` instead of the inline `render_task_card`.
  - `app.py`: Focus Mode's task rendering (`app.py:3182+`, wherever it currently calls
    `render_task_card`) switches to the same compact variant.
- **Architectural boundaries**: `task_card.py` depends only on `task_view`, `models`,
  `executors`, and `execution_queue` (for the new queue-state dot, `KANBAN_REDESIGN.md` §2.6) —
  the same dependency set `render_task_card` already has today, no new coupling introduced. It
  does **not** yet import or depend on an Inspector module (that dependency arrives in UX-5) —
  in this increment, clicking a compact card is inert (or, at minimum, degrades to opening
  today's existing expander content inline as an interim measure) rather than opening a
  not-yet-built Inspector.
- **Acceptance criteria**:
  1. All five `KANBAN_COLUMNS` still render, in order, with correct per-column task counts.
  2. No column renders narrower than `kanban.column.width.minimum` (260px) at any tested
     breakpoint; the board scrolls horizontally instead.
  3. Task titles render at `type.card-title` (15px) and wrap to at most 2 lines before
     ellipsis, verified against the longest existing task title in `data/tasks.json` (or
     `tasks.example.json` in a fresh environment).
  4. Badge rows (planning/execution/readiness, `KANBAN_REDESIGN.md` §2.3) never exceed 3 badges
     per row without moving the remainder out of the compact card.
  5. `update_task_status` and `delete_task` remain reachable (even if only via a temporary
     inline affordance pending UX-5's Inspector) — no regression in "can the user still change a
     task's lane or delete it."
  6. Blocked/dependency-met/queue-state indicators (`KANBAN_REDESIGN.md` §2.5–§2.6) render
     correctly against the existing `models.is_blocked` / `execution_queue` data.
- **Tests**:
  - Re-point every existing Kanban `AppTest` (`test_kanban_launcher_present_but_never_calls_
    subprocess_on_render`, `test_kanban_launcher_refuses_unconfigured_repository`,
    `test_kanban_launcher_blocking_validation_error_cannot_be_bypassed`,
    `test_full_launch_flow_records_run_and_parses_verdict`,
    `test_launcher_launches_claude_against_task_workspace_not_project_repository`,
    `test_launcher_workspace_action_buttons_use_task_workspace_not_project_repository`,
    `test_launcher_missing_task_workspace_falls_back_to_project_default_workspace`) at their new
    `key=` values in `task_card.py` — this is the increment's largest testing surface, since
    these tests currently assert against `key=f"kanban_{task_id}_..."` strings defined inline in
    `app.py`.
  - New: assert every `KANBAN_COLUMNS` value renders as a column header exactly once.
  - New: assert the queue-state indicator appears exactly when a task's id is present in
    `execution_queue.load_queue`'s entries.
- **Risks**:
  - Largest single risk in the roadmap: this touches the most heavily-tested existing behavior
    (the entire launch flow lives inside what was `render_task_card`'s Actions expander).
    Mitigate by extracting in two sub-steps within the increment — first move the code verbatim
    into `task_card.py` with zero behavior change and confirm all existing tests pass unmodified
    except for `key=` updates, *then* apply the compact-card visual/layout changes as a second
    commit within the same increment.
  - CSS-driven horizontal scroll interacting with Streamlit's own page scroll could produce
    nested-scroll UX problems (e.g. scrolling the board also scrolls the page). Mitigate with
    explicit `overflow-x`/`overflow-y` containment in `theme.py` and manual testing before merge.
- **Rollback**: revert `task_card.py` and the `app.py` call-site changes as one unit; because the
  extraction is designed to be a verbatim move first (see mitigation above), reverting to the
  pre-UX-3 inline `render_task_card` is a clean git revert with no partial-state risk.

## UX-4 — Recommendations and execution queue

**Objective**: retokenize `recommendations_panel.py` and `queue_panel.py` onto the design system,
apply the fixed-width recommendation-card pattern (`KANBAN_REDESIGN.md` §7), and move the
Execution Queue's display (not its data/logic) into the Execution Strip (`DESIGN_SYSTEM.md`
§2.1) introduced as a placeholder in UX-1.

- **Files likely affected**: `command_center/ui/recommendations_panel.py`,
  `command_center/ui/queue_panel.py` (visual/layout changes; `render_execution_queue_panel`'s
  call site moves from the Kanban page body, `app.py:2462–2472`, into the Execution Strip
  region defined by `app_shell.py`); `command_center/ui/app_shell.py` (Execution Strip gains
  real content).
- **Architectural boundaries**: neither panel's core logic (`execution_queue.launch_ready`,
  `execution_queue.reevaluate_and_persist`, `recommendation_service.build_recommendation_views`)
  changes — this increment is a display-location and visual-token change, consistent with
  `queue_panel.py`'s own existing docstring guarantee ("Nothing in this module ... launches
  anything on a plain rerun") which remains true.
- **Acceptance criteria**:
  1. Recommendation cards render at fixed comfortable width with a scroll/wrap fallback instead
     of dividing evenly by count, per `KANBAN_REDESIGN.md` §7.
  2. The Execution Queue's Waiting/Ready lists and Launch-ready/Launch-next actions are reachable
     from the Execution Strip on every page, not only the Kanban page — this is a genuine
     behavior improvement (queue visibility becomes page-independent), flagged explicitly since
     it's the one place this increment goes beyond pure retokenization.
  3. `st.info`/`st.caption`-based empty states in both panels
     (`recommendations_panel.py:41`, `queue_panel.py:53`) are replaced with the shared Empty
     State component (`DESIGN_SYSTEM.md` §9.14) with no change in when they trigger.
- **Tests**: existing recommendation/queue-flow tests (exercised via Kanban page `AppTest`
  coverage today) are re-verified at their new render location; add one test confirming the
  queue panel renders identically regardless of which page is active (proving the Execution
  Strip's page-independence).
- **Risks**: moving the queue panel's mount point out of the Kanban page body risks a
  session-state key collision if the Execution Strip and any page-local queue reference both
  exist during a transition; mitigate by removing the old Kanban-page mount point in the same
  commit that adds the Execution Strip one, never running both simultaneously.
- **Rollback**: revert `app_shell.py`'s Execution Strip content and restore the Kanban-page-body
  mount point for `queue_panel` from git history.

## UX-5 — Inspector and keyboard workflow

**Objective**: build the Inspector Panel (`DESIGN_SYSTEM.md` §9.13), wire task-card clicks to
open it (`INTERACTION_MODEL.md` §2), migrate the Actions-expander content into it
(`KANBAN_REDESIGN.md` §2.2), add the task-deletion Confirmation Dialog
(`DESIGN_SYSTEM.md` §9.16), and implement the keyboard navigation baseline
(`INTERACTION_MODEL.md` §3).

- **Files likely affected**:
  - New: `command_center/ui/inspector.py` (Inspector Panel rendering + `selected_task_id`
    session-state management).
  - New: `command_center/ui/confirm_dialog.py` (generic Confirmation Dialog, first consumer:
    task deletion).
  - `command_center/ui/task_card.py`: add `render_task_card_expanded(...)`
    (`KANBAN_REDESIGN.md` §2.2) and wire the compact card's click to set `selected_task_id`.
  - `app.py`: mount the Inspector in the App Shell's right-panel region (defined structurally in
    UX-1); remove the now-migrated Actions-expander code path.
- **Architectural boundaries**: `inspector.py` reuses `render_agent_launcher`,
  `render_task_timeline`, `render_dependency_graph` (currently in `app.py`, candidates to also
  move into `command_center/ui/` in this increment per `DESIGN_SYSTEM.md` §12.5's monolith-
  avoidance rule, though moving them is not required for the Inspector to function — flagged as
  a nice-to-have within this increment, not a blocker). No launch/queue/git logic is
  reimplemented — every action in the Inspector calls the exact same underlying functions
  (`launch.open_folder_at`, `execution_queue.enqueue`, `tasks_repository.set_manual_launch_
  status`, etc.) the old expander called.
- **Acceptance criteria**:
  1. Every field and action present in today's Actions expander (`app.py:940–1031`, enumerated
     in `KANBAN_REDESIGN.md` §2.2) is reachable from the Inspector — this is a migration-parity
     requirement, verified item-by-item (§Tests below).
  2. Deleting a task requires passing through the Confirmation Dialog; `delete_task` is never
     called without explicit confirmation, including under `AppTest.click()` (server-side
     re-check, matching the existing Cancel-run defense-in-depth pattern at `app.py:1382–1389`).
  3. Selecting a new task while the Inspector is open replaces its content (never stacks a
     second panel) — `INTERACTION_MODEL.md` §1.
  4. Every focusable element in the Inspector and on Kanban cards shows a visible focus ring on
     keyboard focus (`DESIGN_SYSTEM.md` §3.4, `INTERACTION_MODEL.md` §3).
  5. `Esc` closes the topmost layer only (Confirmation Dialog, then Inspector) —
     `INTERACTION_MODEL.md` §3.
  6. `↓`/`↑`/`Enter` column-internal navigation (`INTERACTION_MODEL.md` §3) is attempted; if not
     reliably achievable within this increment's Streamlit-version constraints, this is
     explicitly documented as deferred (not silently dropped) and `Tab`-based native focus
     remains the verified baseline.
- **Tests**:
  - One migration-parity test per action named in `KANBAN_REDESIGN.md` §2.2 (Workspace, Git,
    Промпт, Отчёт, В очередь, Pause, Resume, Restart, launcher, timeline, dependency graph) —
    largest test-writing surface in the roadmap.
  - New: task-deletion Confirmation Dialog test, structurally mirroring
    `test_kanban_launcher_blocking_validation_error_cannot_be_bypassed`.
  - New: selection-replacement test (select task A, then task B without closing, assert
    Inspector shows only B's data).
  - New: keyboard-focus-ring presence test where feasible under `AppTest`'s capabilities (likely
    limited — flag any gap for UX-6's manual/visual pass).
- **Risks**:
  - Highest state-management complexity in the roadmap (`DESIGN_SYSTEM.md` §9.13's own
    feasibility note) — keeping `selected_task_id` in sync with which card was clicked across
    Streamlit reruns is the increment's central risk. Mitigate by reusing the exact
    `pending_*`-staging pattern already proven at `app.py:1773–1784` rather than inventing a new
    state-sync mechanism.
  - The keyboard-navigation stretch goal (§3's `↓`/`↑`/`Enter`) may not be reliably achievable
    without a custom component (`DESIGN_SYSTEM.md` §12.3) — scope this explicitly as
    best-effort within the increment's time-box, not a hard gate on shipping the rest of UX-5.
- **Rollback**: revert `inspector.py`, `confirm_dialog.py`, and the `task_card.py`/`app.py`
  wiring changes as one unit; the old inline Actions-expander code, if kept available in git
  history from UX-3's verbatim-move step, can be restored as an interim fallback if the
  Inspector needs to be pulled post-merge.

## UX-6 — Visual regression and accessibility hardening

**Objective**: close the testing gap named throughout this roadmap and `UX_AUDIT.md` §2.11 —
add automated visual regression coverage (the one class of defect this entire redesign targets
that `AppTest` structurally cannot catch) and an accessibility pass across the shell and
components built in UX-1–UX-5.

- **Files likely affected**: new `tests/visual/` directory (tooling choice — e.g. Playwright or
  a Streamlit-compatible screenshot-diff harness — is an implementation decision at this
  increment's start, not fixed by this roadmap); CI config, if this repo runs one, gains a new
  visual-diff job; no `app.py`/`command_center/ui/*` changes expected unless the accessibility
  pass surfaces concrete defects to fix.
- **Architectural boundaries**: visual tests run against a real running Streamlit instance
  (unlike `AppTest`, which executes the script tree without a browser) — this is a genuinely new
  testing tier, additive to, not a replacement for, the existing `AppTest` suite.
- **Acceptance criteria**:
  1. Baseline screenshots exist for: Kanban board at all four breakpoints
     (`DESIGN_SYSTEM.md` §2.4), compact and expanded task card, Inspector open/closed, empty
     states for board/queue/recommendations, both light and dark themes.
  2. A visual diff beyond an agreed pixel/perceptual threshold fails CI (or, if no CI exists in
     this repo, fails a documented local pre-merge check) rather than silently passing.
  3. Every interactive element audited for: visible focus ring (§UX-5 acceptance criterion 4,
     re-verified here at the whole-app level), sufficient color contrast for `status.*` tokens in
     both themes (`DESIGN_SYSTEM.md` §3.6), and a non-empty accessible name/label (no icon-only
     button without a `help=` tooltip or equivalent).
  4. `data-testid`-selector fragility (`DESIGN_SYSTEM.md` §12.4) is documented with the exact
     Streamlit version the visual baseline was captured against, so a future Streamlit upgrade
     has a clear "re-baseline" trigger instead of an unexplained CI failure.
- **Tests**: this increment *is* the test-authoring increment — see acceptance criteria above.
- **Risks**:
  - Visual regression tooling is inherently flaky (font rendering, anti-aliasing differences
    across CI runners) — mitigate with a perceptual-diff threshold, not pixel-exact matching, and
    by running the baseline capture and the comparison run on the same OS/renderer.
  - Accessibility findings at this stage could surface issues that require reopening UX-1–UX-5
    components rather than being fixable in isolation — budget for this explicitly rather than
    treating UX-6 as pure test-writing with zero app-code risk.
- **Rollback**: the new test tier is additive and non-blocking to ship in isolation; if it proves
  too flaky to trust, it can be disabled/removed without affecting any of UX-1–UX-5's shipped
  functionality, since no increment before it depends on UX-6 existing.

## UX-7 — Native Desktop Migration

**Status: documentation-only, not scheduled, not committed.** This phase does not begin until the
founder decides the native PySide6/Qt initiative (`docs/desktop/*`) should move from D0
(documentation) toward implementation, and does not begin until UX-1–UX-6 have shipped — the
Streamlit component contracts this phase migrates *from* need to be stable and real before a
migration plan can be trusted. Nothing here modifies `docs/desktop/*`, which remains governed
entirely by its own documents and its own D0→D1A→… sequencing
(`docs/desktop/IMPLEMENTATION_ROADMAP.md`). This section exists so the Streamlit-side redesign is
built with a known future migration path in mind (`FOUNDER_DESIGN_PRINCIPLES.md` §9,
Native-first mindset), not to commit engineering time against it now.

### Objective

Define how the component and interaction contracts established in UX-1–UX-6 translate into the
native Qt Widgets application, so that when native implementation work does begin, it inherits a
settled information architecture and interaction model instead of re-deriving one from scratch —
and so the two products never accidentally diverge into a different information architecture in
the meantime.

### Streamlit → PySide6 component parity

Full mapping in `COMPONENT_CATALOG.md`'s "Future native Qt mapping" column; summarized by
migration difficulty:

| Parity class | Components | Migration note |
|---|---|---|
| **Direct (name and contract already exist natively)** | App Shell → `AppShell`, Sidebar → `Sidebar`/`NavigationItem`, Status Badge → `StatusBadge`, Dialog → `Dialog`, Empty State → `EmptyState` | `docs/desktop/DESIGN_SYSTEM.md` §7 already specifies these independently; migration is reconciling two independently-arrived-at specs, not writing a new one — reconciliation, where the two differ, is scoped as UX-7's first concrete task. |
| **Analogous (native equivalent exists, different name/shape)** | Command Bar → `TopBar`, KPI Card → `MetricCard`, Notification → `Toast`, Error Banner → `ErrorState` | Native contract exists and is close; migration adapts the Streamlit component's specific content (e.g. Command Bar's command-palette emphasis) onto the native shape rather than assuming a 1:1 copy. |
| **Candidate new (no native equivalent yet)** | Task Card, Recommendation Card, Queue Item, Inspector, Progress Indicator | These carry this product's actual domain logic (task/run/queue state) and have no native precedent because Desktop Increment 1 is scoped read-only with no task-launch surface (`docs/desktop/README.md` binding decision 11). Native specs for these are net-new work, informed by — not copied from — the Streamlit versions' proven information hierarchy (`KANBAN_REDESIGN.md` §2.3 in particular). |

### Migration principles

1. **Behavior migrates before pixels do.** A native component is considered "migrated" when it
   reads the same underlying data (`command_center/*` read models, unchanged per
   `docs/desktop/ARCHITECTURE.md`'s existing reuse principle) and supports the same actions as its
   Streamlit counterpart — not when it merely looks similar. Visual polish native to Qt Widgets
   conventions is expected to differ from the Streamlit version's exact pixel values; the
   *information* it conveys is what must match.
2. **Data model stays the single source of truth across both.** Every increment in UX-1–UX-6 was
   scoped to change zero data models (`Cross-increment notes` below); this is what makes UX-7
   possible at all — a native `TaskCard` and a Streamlit `task_card.py` reading the same
   `data/tasks.json`/`runtime.db` state via the same `command_center/*` modules is the whole
   migration strategy in one sentence.
3. **Qt Widgets conventions win over Streamlit conventions where they conflict.** Where this
   redesign made a decision specifically to work around a Streamlit limitation (e.g. CSS-driven
   horizontal scroll standing in for a native scroll view, `DESIGN_SYSTEM.md` §12.1), the native
   version uses the native-idiomatic answer instead of replicating the workaround
   (`docs/desktop/ARCHITECTURE.md` §Qt Widgets threading/widget rules govern there, not this
   document).
4. **`FOUNDER_DESIGN_PRINCIPLES.md` and `VISUAL_LANGUAGE.md` bind both products equally.** Those
   two documents are explicitly implementation-independent (their own status lines say so) — a
   native component that violates "progressive disclosure" or reads as "dashboard aesthetics" has
   failed the same bar a Streamlit component would, regardless of which framework renders it.

### Compatibility expectations

- **No expectation of pixel-for-pixel visual parity.** Qt Widgets and a browser-rendered Streamlit
  app have different native text rendering, control chrome, and platform conventions
  (`docs/desktop/PLATFORM_BEHAVIOR.md`) — the migration target is equivalent information
  hierarchy and interaction model, specified in `FOUNDER_DESIGN_PRINCIPLES.md`/
  `VISUAL_LANGUAGE.md` terms, not identical rendering.
- **No expectation that both products ship every feature simultaneously.** `docs/desktop/README.md`
  binding decision 10 already states existing Streamlit functionality remains available until
  native parity is deliberately achieved — UX-7 does not change that; the two products are
  expected to coexist, with the native client trailing the Streamlit one in feature coverage for
  as long as that decision holds.
- **No expectation that drag-and-drop or other Streamlit-deferred interactions become "free" on
  the native side.** `INTERACTION_MODEL.md` §7 deferred true drag-and-drop specifically *to* the
  native client as a candidate feature — UX-7 tracks this as a real, unscoped design/engineering
  task for whichever future native increment picks it up, not an automatic benefit of migrating.

### Incremental migration strategy

Migration is not a single cutover; it follows the same increment-by-increment logic UX-1–UX-6
used, mapped onto native scope:

1. **Reconcile direct-parity component contracts first** (App Shell, Sidebar, Status Badge,
   Dialog, Empty State) — since both a Streamlit and a native spec already exist independently for
   these, the first UX-7 task is a diff-and-reconcile pass, not new design work, resolving any
   place the two documents currently disagree in favor of whichever is better-reasoned (not
   automatically the native one, not automatically the Streamlit one).
2. **Design the candidate-new components against real Streamlit usage data**, not from a blank
   page — by the time UX-7 begins (after UX-1–UX-6 have shipped and been used), the Streamlit
   Task Card, Inspector, and Queue Item will have real operational history to inform their native
   equivalents, which a same-time parallel design effort would not have had.
3. **Migrate read-only surfaces before write/action surfaces**, consistent with
   `docs/desktop/README.md`'s own binding decision 11 scoping Desktop Increment 1 to read-only —
   KPI Card and a read-only Task Card variant are natural early native candidates; launch/queue/
   delete actions (Inspector, Recommendation Card, Queue Item's mutating actions) come later,
   once the native side's own increment sequence (`docs/desktop/DESKTOP_INCREMENT_1.md` and
   beyond) reaches write capability.
4. **Never remove Streamlit functionality as a side effect of native progress.** Per binding
   decision 10, each native component landing is additive; retiring a Streamlit surface is a
   separate, explicit, future founder decision — not an automatic consequence of this roadmap.

### What UX-7 explicitly does not do

- It does not commit any engineering time, timeline, or resourcing — it is a migration *map*, not
  a migration *schedule*.
- It does not modify any file under `docs/desktop/*` — reconciliation (Migration principle 1
  above) happens as its own future, explicit editing pass on both sides, not silently via this
  document.
- It does not change anything about UX-1–UX-6 — those increments are scoped, justified, and
  sequenced entirely on their own Streamlit-side merits (`UX_AUDIT.md`'s findings), independent of
  whether or when UX-7 ever begins.

## Cross-increment notes

The three notes below govern **UX-1 through UX-6** — the committed, sequenced, Streamlit-only
increments. UX-7 is explicitly exempt from the "no increment changes the data model" framing
above only in the sense that it is not an "increment" in this roadmap's committed sense at all
(see UX-7's own status line) — it does not, in itself, touch `data/tasks.json`, `execution_queue`,
or `runtime.db` either, since it is documentation-only in this phase.

- **No increment changes `data/tasks.json`'s schema, `execution_queue`'s persisted format, or any
  `runtime.db` table.** Every increment in this roadmap is additive/relocative at the UI layer
  only, consistent with `UX_AUDIT.md`'s finding that the state model is already correct
  (§2.13) — the redesign's job is entirely presentational.
- **Every increment keeps `app.py` from growing**, per `DESIGN_SYSTEM.md` §12.5 — each adds new
  code to `command_center/ui/*` modules and only edits `app.py`'s existing call sites, never
  appends new inline rendering blocks to it. If a future increment finds itself adding more than
  a few lines directly to `app.py`, that's a signal the new logic belongs in its own module
  instead.
- **Do not commit any increment without running the existing full `AppTest` suite first** —
  every increment above is scoped specifically so that suite should pass with at most `key=`
  updates, never assertion-logic rewrites; an assertion-logic rewrite mid-roadmap is a signal
  that an increment silently changed behavior it wasn't supposed to.
