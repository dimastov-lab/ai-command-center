# AI Command Center — Component Catalog

Status: **cross-cutting reference.** Every component named in `DESIGN_SYSTEM.md` §9,
`KANBAN_REDESIGN.md` §2, and `INTERACTION_MODEL.md`, in one table-of-record, tracked across three
axes those documents don't each carry on their own: **who owns the code today**, **what state
it's actually in right now**, and **what it maps to in the separate native PySide6/Qt initiative**
(`docs/desktop/*`) if and when that initiative reaches implementation. This document does not
redefine any component's purpose/anatomy/states/interactions — see the source document cited in
each entry for that. It exists so a founder or engineer can answer "where does this live, is it
built yet, and does the native side already have a name for it" in one place, without
cross-referencing four other documents.

Scope boundary, restated: the "Future native Qt mapping" column is a cross-reference to
already-written `docs/desktop/DESIGN_SYSTEM.md` §7 component contracts where one exists, or an
explicit "candidate new" note where it doesn't. Nothing in this column commits the native
initiative to anything — `docs/desktop/*` remains D0/documentation-only, governed entirely by its
own documents, not by this one.

## Status legend

| Status | Meaning |
|---|---|
| **Exists (needs retokenization)** | The component works today in `app.py`/`command_center/ui/*`; this redesign changes its visual tokens and/or layout position, not its underlying logic. |
| **Exists (needs extraction)** | The component's logic exists today but is inline in `app.py` rather than its own module; this redesign moves it, retokenizes it, and gives it a stable contract. |
| **New** | No equivalent exists in the current implementation; this redesign specifies and builds it from scratch. |

## Catalog

### App Shell

- **Purpose**: persistent frame — Command Bar + Sidebar + Central Workspace + optional Inspector
  + Execution Strip. Full spec: `DESIGN_SYSTEM.md` §2, §9.1.
- **Ownership**: new `command_center/ui/app_shell.py`; called once from `app.py`.
- **Current status**: New (the regions exist today only as an unstructured sequence of
  `st.sidebar`/`st.title`/page-body calls in `app.py`, with no shared shell module).
- **Planned evolution**: UX-1 (`IMPLEMENTATION_ROADMAP.md`) — structural regions and theme
  injection; Execution Strip gains real content in UX-4; Inspector slot activates in UX-5.
- **Future native Qt mapping**: direct — `docs/desktop/DESIGN_SYSTEM.md` §7.1 `AppShell`
  (`QMainWindow`-based), already named identically. The Streamlit and native `AppShell` are
  independent implementations of the same structural concept, built on independent timelines.

### Sidebar

- **Purpose**: persistent, grouped navigation. Full spec: `DESIGN_SYSTEM.md` §2.2, §9.2.
- **Ownership**: `command_center/ui/app_shell.py` (grouping logic); reads the existing `NAV` dict
  from `app.py` unchanged.
- **Current status**: Exists (needs retokenization) — today a flat, ungrouped `st.radio`
  (`app.py:1816–1836`, `UX_AUDIT.md` §2.7). No routing keys change.
- **Planned evolution**: UX-1.
- **Future native Qt mapping**: direct — `docs/desktop/DESIGN_SYSTEM.md` §7.2 `Sidebar` +
  §7.3 `NavigationItem`. The native side's item-level grouping is not yet specified in
  `docs/desktop/INFORMATION_ARCHITECTURE.md`'s Desktop Increment 1 scope; the Streamlit grouping
  in `DESIGN_SYSTEM.md` §2.2 is a reasonable reference point for that future native decision, not
  a binding one.

### Command Bar

- **Purpose**: per-page orientation, search/palette trigger, project scope, system status glyph.
  Full spec: `DESIGN_SYSTEM.md` §2.3, §9.3.
- **Ownership**: `command_center/ui/app_shell.py`.
- **Current status**: Exists (needs extraction) — today reduced to a single repeated
  `st.title`/`st.caption` pair on every page (`app.py:1793–1794`, `UX_AUDIT.md` §2.8) plus the
  already-separate, already-working command palette dialog (`app.py:1800–1880`, kept as-is).
- **Planned evolution**: UX-1.
- **Future native Qt mapping**: analogous — `docs/desktop/DESIGN_SYSTEM.md` §7.4 `TopBar`. Name
  differs (Command Bar vs. TopBar) because the Streamlit version's search/palette emphasis is a
  response to `UX_AUDIT.md`'s specific findings; whether the native `TopBar` adopts the same
  command-palette emphasis is an open native-side decision, not assumed here.

### KPI Card

- **Purpose**: one Project Intelligence metric tile (health, sprint progress, roadmap, remaining,
  blocked, completion). Full spec: `DESIGN_SYSTEM.md` §9.5.
- **Ownership**: `command_center/ui/project_intelligence_panel.py` (existing file, unchanged
  function signature).
- **Current status**: Exists (needs retokenization) — `project_intelligence_panel.py:22–40`,
  already uses `st.metric(border=True)`, close to spec today.
- **Planned evolution**: UX-2.
- **Future native Qt mapping**: analogous — `docs/desktop/DESIGN_SYSTEM.md` §7.7 `MetricCard`.
  Closest of any component in this catalog to a direct 1:1 (both are a label/value tile with an
  optional hover reason) — low-risk future migration candidate.

### Task Card

- **Purpose**: the primary planning+execution+readiness unit — compact (board) and expanded
  (Inspector) variants. Full spec: `KANBAN_REDESIGN.md` §2, `DESIGN_SYSTEM.md` §9.6.
- **Ownership**: new `command_center/ui/task_card.py`, extracted from `app.py:862–1048`.
- **Current status**: Exists (needs extraction) — the single largest extraction in this roadmap;
  see `KANBAN_REDESIGN.md` §4 and `IMPLEMENTATION_ROADMAP.md` UX-3's two-step
  (verbatim-move-then-redesign) mitigation.
- **Planned evolution**: UX-3 (compact variant + extraction); UX-5 (expanded variant, once the
  Inspector exists to host it).
- **Future native Qt mapping**: **candidate new** — no directly-named equivalent exists yet in
  `docs/desktop/DESIGN_SYSTEM.md` §7. The closest existing native precedent is §7.8 `ProjectCard`
  (a bordered summary card with a `StatusBadge`), which is a reasonable structural starting point
  for a future native `TaskCard`, but Desktop Increment 1 is explicitly read-only-except-config
  (`docs/desktop/README.md` binding decision 11) and has no task-launch surface yet — a native
  Task Card with launch actions is out of scope until a later, not-yet-scoped native increment.

### Recommendation Card

- **Purpose**: "why this task next" — reasons, dependencies, impact, readiness, launch/enqueue
  actions. Full spec: `DESIGN_SYSTEM.md` §9.10, `KANBAN_REDESIGN.md` §7.
- **Ownership**: `command_center/ui/recommendations_panel.py` (existing file, unchanged function
  signature and unchanged underlying logic in `recommendation_service.py`).
- **Current status**: Exists (needs retokenization) — `recommendations_panel.py:22–115`, already
  functionally complete; this redesign changes card width behavior (`KANBAN_REDESIGN.md` §7) and
  visual tokens only.
- **Planned evolution**: UX-4.
- **Future native Qt mapping**: **candidate new** — no native equivalent exists; recommendation
  scoring/surfacing has no presence yet in `docs/desktop/*`'s scoped increments. Structurally
  closest to §7.8 `ProjectCard` + §7.9 `StatusBadge` composed together, but this is an
  observation about reusable primitives, not a commitment.

### Queue Item

- **Purpose**: one Execution Queue row (waiting or ready). Full spec: `DESIGN_SYSTEM.md` §9.11.
- **Ownership**: `command_center/ui/queue_panel.py` (existing file, unchanged function signature
  and unchanged underlying `execution_queue.py` logic).
- **Current status**: Exists (needs retokenization) — `queue_panel.py:92–110`, functionally
  complete today; this redesign relocates its mount point into the Execution Strip
  (`IMPLEMENTATION_ROADMAP.md` UX-4) and retokenizes its row styling.
- **Planned evolution**: UX-4.
- **Future native Qt mapping**: **candidate new**, structurally closest to
  `docs/desktop/DESIGN_SYSTEM.md` §7.11 `ActivityItem` or §7.14 `WorktreeRow` (both are
  compact, single-line, icon-led row patterns already established natively) — a future native
  Queue Item would likely reuse one of those row primitives rather than inventing a third.

### Inspector

- **Purpose**: full task/run detail on demand, replacing the on-card Actions expander. Full spec:
  `DESIGN_SYSTEM.md` §9.13, `KANBAN_REDESIGN.md` §2.2.
- **Ownership**: new `command_center/ui/inspector.py`.
- **Current status**: New — no equivalent panel exists today; its content is a straight migration
  of `app.py:940–1031`'s existing Actions-expander fields and actions into a new, wider surface
  (`KANBAN_REDESIGN.md` §2.2 — a layout migration, not new functionality).
- **Planned evolution**: UX-5 — the roadmap's highest state-management-risk increment
  (`IMPLEMENTATION_ROADMAP.md` UX-5 risks).
- **Future native Qt mapping**: **candidate new**. No named equivalent exists in
  `docs/desktop/DESIGN_SYSTEM.md` §7 today; a `QDockWidget`-based detail panel is the natural Qt
  Widgets pattern for this role (persistent, dockable, doesn't overlay the main content — directly
  analogous to this document's "push, not overlay" requirement at wide breakpoints,
  `DESIGN_SYSTEM.md` §2.4). Out of scope for Desktop Increment 1 for the same reason Task Card is
  (binding decision 11 — read-only, no launch surface yet).

### Status Badge

- **Purpose**: one semantic fact — priority, launch status, verdict, git-dirty, etc. Full spec:
  `DESIGN_SYSTEM.md` §9.7, §3.6–§3.7.
- **Ownership**: no dedicated module — a direct, native `st.badge` call at each use site, governed
  by the color-token mapping in `DESIGN_SYSTEM.md` §3.7 (a convention, not a wrapper function,
  since `st.badge` already is the shared primitive).
- **Current status**: Exists (needs retokenization) — already used extensively and correctly as a
  primitive (`app.py:893`, `902`, etc.); this redesign only maps its existing ad hoc color
  choices onto the fixed semantic table.
- **Planned evolution**: incremental, alongside whichever increment touches each use site (UX-2
  through UX-5) — no dedicated increment of its own, since there's no extraction to do.
- **Future native Qt mapping**: direct — `docs/desktop/DESIGN_SYSTEM.md` §7.9 `StatusBadge`,
  already named identically and already documented there as pairing color with a non-color signal
  for accessibility (`docs/desktop/DESIGN_SYSTEM.md` §4) — the same rule
  `DESIGN_SYSTEM.md` §4 of this document applies ("never rely on color alone").

### Progress Indicator

- **Purpose**: execution progress + stage label for a task/run in flight. Full spec:
  `DESIGN_SYSTEM.md` §9.9 (named "Run Progress" there — same component).
- **Ownership**: candidate for a small new shared helper (e.g. inside `task_card.py` or a new
  `command_center/ui/run_progress.py`) unifying the two existing inline call sites.
- **Current status**: Exists (needs extraction) — functionally duplicated today at
  `app.py:879–881` (Task Card) and `app.py:1304–1308` (Execution Center card) via direct
  `st.progress` calls with matching format strings; this redesign unifies them into one function
  so the two can't drift apart (`DESIGN_SYSTEM.md` §9.9's reuse note).
- **Planned evolution**: UX-3 (Task Card's usage); the Execution Center card's usage is not
  otherwise touched by UX-1–UX-6 and can adopt the shared helper opportunistically once it exists.
- **Future native Qt mapping**: **candidate new** — no dedicated progress component is named yet
  in `docs/desktop/DESIGN_SYSTEM.md` §7; a `QProgressBar`-based indicator would most likely be
  embedded within §7.10 `RunSummary` (the native run-state component) rather than exist
  standalone, mirroring how it's embedded within Task Card and the Execution Center card here.

### Dialog

- **Purpose**: modal overlay for the command palette and for gated confirmations (task deletion).
  Full spec: `DESIGN_SYSTEM.md` §9.16 ("Confirmation Dialog" there is this component's primary
  new consumer; the command palette, `app.py:1843`, is its existing consumer).
- **Ownership**: existing `st.dialog` usage in `app.py` for the command palette (unchanged); new
  `command_center/ui/confirm_dialog.py` generalizing the same `st.dialog` primitive for
  confirmations.
- **Current status**: Exists (needs extraction, for the confirmation use case only) — the command
  palette's dialog usage is already correct and untouched; task deletion today has **no** dialog
  at all (`app.py:1046–1048`, a bare button, `INTERACTION_MODEL.md` §11) and gains one.
- **Planned evolution**: UX-5.
- **Future native Qt mapping**: direct — `docs/desktop/DESIGN_SYSTEM.md` §7.19 `Dialog`
  (`QDialog`-based, modal, Tab-trapped focus while open) — already named identically and already
  specifies the same "Escape closes without committing" behavior
  (`docs/desktop/DESIGN_SYSTEM.md` §5) this document's `INTERACTION_MODEL.md` §11 independently
  requires for the Streamlit version.

### Notification

- **Purpose**: transient, non-blocking confirmation that an action completed. Full spec:
  `DESIGN_SYSTEM.md` §7's "Success" row (toast, auto-dismissing).
- **Ownership**: direct `st.success`/`st.toast`-family calls at each use site — no wrapper module,
  same reasoning as Status Badge (the native primitive already matches the spec).
- **Current status**: Exists (needs retokenization) — already used correctly today, including the
  good existing pattern of linking directly to the result
  (`st.success(f"Запуск начат: \`{result.run_id}\`.")`, `recommendations_panel.py:111`), which
  this redesign explicitly preserves rather than replaces.
- **Planned evolution**: incremental, alongside whichever increment touches each use site — no
  dedicated increment.
- **Future native Qt mapping**: analogous — `docs/desktop/DESIGN_SYSTEM.md` §7.18 `Toast`. Name
  differs (Notification vs. Toast) but behavior matches closely: both are non-blocking,
  auto-dismissing, semantic-color-coded (info/success/warning/danger).

### Empty State

- **Purpose**: shared "nothing here yet" rendering across Kanban columns, Queue, Recommendations,
  Runs log, etc. Full spec: `DESIGN_SYSTEM.md` §7, §9.14.
- **Ownership**: new shared helper, likely `command_center/ui/empty_state.py`.
- **Current status**: Exists (needs extraction) — today three different ad hoc treatments for the
  same concept (`st.caption("Пусто")` at `app.py:2450`; `st.info(...)` at
  `recommendations_panel.py:41`; plain `st.caption(...)` at `queue_panel.py:53`) — this redesign
  unifies them into one function and one visual treatment.
- **Planned evolution**: UX-3 (Kanban column), UX-4 (Recommendations, Queue) — each call site
  migrates to the shared helper as its surrounding increment touches it.
- **Future native Qt mapping**: direct — `docs/desktop/DESIGN_SYSTEM.md` §7.15 `EmptyState`,
  already named identically.

### Error Banner

- **Purpose**: shared rendering for recoverable failures — launch validation, git-status read
  failures, report-parse failures. Full spec: `DESIGN_SYSTEM.md` §7, §9.15.
- **Ownership**: new shared helper, likely `command_center/ui/error_banner.py`.
- **Current status**: Exists (needs extraction) — today ad hoc `st.error`/`st.warning` calls at
  many independent sites (e.g. `app.py:982`, `1389`, `1422`) with no shared shape; this redesign
  unifies them into one function (icon + one-line cause + optional "Подробнее" detail + optional
  retry) without changing when any of them fire.
- **Planned evolution**: UX-5 (alongside the Inspector migration, since most current error sites
  live inside the Actions expander being migrated there); any error site outside that migration
  can adopt the shared helper opportunistically.
- **Future native Qt mapping**: analogous — `docs/desktop/DESIGN_SYSTEM.md` §7.16 `ErrorState`.
  Name differs (Error Banner vs. ErrorState) — the native version's exact shape (inline vs.
  full-region replacement) is that document's own decision, not constrained by this one.

## Summary table

| Component | Ownership (Streamlit) | Status | Increment | Native Qt mapping |
|---|---|---|---|---|
| App Shell | `app_shell.py` | New | UX-1 | `AppShell` (direct) |
| Sidebar | `app_shell.py` | Needs retokenization | UX-1 | `Sidebar` + `NavigationItem` (direct) |
| Command Bar | `app_shell.py` | Needs extraction | UX-1 | `TopBar` (analogous) |
| KPI Card | `project_intelligence_panel.py` | Needs retokenization | UX-2 | `MetricCard` (analogous) |
| Task Card | `task_card.py` | Needs extraction | UX-3 / UX-5 | Candidate new (`ProjectCard`-adjacent) |
| Recommendation Card | `recommendations_panel.py` | Needs retokenization | UX-4 | Candidate new |
| Queue Item | `queue_panel.py` | Needs retokenization | UX-4 | Candidate new (`ActivityItem`-adjacent) |
| Inspector | `inspector.py` | New | UX-5 | Candidate new (`QDockWidget`-based) |
| Status Badge | inline `st.badge` | Needs retokenization | Incremental | `StatusBadge` (direct) |
| Progress Indicator | `task_card.py` / `run_progress.py` | Needs extraction | UX-3 | Candidate new (embedded in `RunSummary`) |
| Dialog | `confirm_dialog.py` (+ existing palette) | Needs extraction | UX-5 | `Dialog` (direct) |
| Notification | inline `st.success` | Needs retokenization | Incremental | `Toast` (analogous) |
| Empty State | `empty_state.py` | Needs extraction | UX-3 / UX-4 | `EmptyState` (direct) |
| Error Banner | `error_banner.py` | Needs extraction | UX-5 | `ErrorState` (analogous) |

## How to use this document

When starting any increment in `IMPLEMENTATION_ROADMAP.md`, check this catalog first for every
component that increment touches — it's the fastest way to confirm which file owns the change and
whether the underlying logic already exists (retokenize/extract) or needs to be built (new). When
scoping a future native-client increment under `docs/desktop/*`, check the "Future native Qt
mapping" column before inventing a new native component name — several Streamlit-side components
already have a direct native counterpart with an established contract; only the "candidate new"
rows represent genuinely open native design decisions.
