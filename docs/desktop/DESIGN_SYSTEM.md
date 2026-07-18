# AI Command Center — Desktop Design System

Status: **D0 — implementation-ready tokens and component contracts** for the Professional
Control Plane direction (`DESIGN_DIRECTIONS.md`). Values here are binding for Desktop Increment
1 and later increments unless a specific increment's acceptance criteria say otherwise. Terms
follow Qt/PySide6 vocabulary (`QWidget`, `QAbstractItemView`, accessible name/description, focus
policy) rather than web terminology, per binding decision — this is a Qt Widgets application,
not a web page.

## 1. Design tokens

All values are defined once, in a single `command_center.desktop` style module (a Python
constants module and/or Qt stylesheet (`.qss`) at implementation time — this document defines
values, not the mechanism), and referenced everywhere rather than hardcoded per-widget.

### 1.1 Spacing

| Token | Value (px, at 1.0 scale factor) | Usage |
|---|---|---|
| `space.xs` | 4 | Icon-to-label gaps, tight badge padding |
| `space.sm` | 8 | Within-component padding (e.g. inside a `NavigationItem`) |
| `space.md` | 12 | Default control padding, list-row vertical padding |
| `space.lg` | 16 | Between adjacent components (e.g. cards in a grid) |
| `space.xl` | 24 | Section padding, page margins |
| `space.xxl` | 32 | Page-level top/bottom margins |

### 1.2 Typography

Platform-native typography (§11) is used for the base font family; the tokens below define
*roles* and *sizes*, not a bundled custom font.

| Token | Size (pt) | Weight | Usage |
|---|---|---|---|
| `type.display` | 20 | Semibold | Page titles (`PageHeader`) |
| `type.title` | 15 | Semibold | Card/section titles |
| `type.body` | 13 | Regular | Default body text, table cells |
| `type.body.emphasis` | 13 | Medium | Emphasized inline text (e.g. a project's display name in a list) |
| `type.caption` | 11 | Regular | Secondary/meta text (timestamps, counts) |
| `type.mono` | 12 | Regular (monospace) | Run ids, paths, commit hashes — anything a user may copy verbatim |

### 1.3 Control heights

| Token | Value (px) | Usage |
|---|---|---|
| `control.height.sm` | 24 | `StatusBadge`, inline chips |
| `control.height.md` | 32 | Buttons, single-line inputs, `NavigationItem` (compact density) |
| `control.height.lg` | 40 | `NavigationItem` (comfortable density), primary form fields |
| `control.height.topbar` | 48 | `TopBar` |

### 1.4 Corner radii

| Token | Value (px) | Usage |
|---|---|---|
| `radius.sm` | 4 | Badges, chips |
| `radius.md` | 8 | Cards, buttons, inputs |
| `radius.lg` | 12 | Dialogs, top-level containers |

### 1.5 Icon sizes

| Token | Value (px) | Usage |
|---|---|---|
| `icon.sm` | 16 | Inline icons (badge glyphs, table-row icons) |
| `icon.md` | 20 | Navigation and toolbar icons |
| `icon.lg` | 32 | Empty-state illustrations, dialog header icons |

### 1.6 Borders

| Token | Value | Usage |
|---|---|---|
| `border.hairline` | 1px, theme-dependent neutral color | Card/section separators (matches the existing Streamlit `st.container(border=True)` visual language, §1 of `DESIGN_DIRECTIONS.md`) |
| `border.focus` | 2px, accent color | Focus ring (§1.9) |
| `border.emphasis` | 1px, accent color | Selected `NavigationItem`, active tab |

### 1.7 Elevation

Qt Widgets does not have CSS-style box-shadow by default; elevation is expressed through border
+ background-color contrast, not drop shadows, matching the existing product's border-forward
visual language (`DESIGN_DIRECTIONS.md` §1):

| Token | Treatment | Usage |
|---|---|---|
| `elevation.flat` | `border.hairline` only, no background shift | Cards, list rows |
| `elevation.raised` | `border.hairline` + a background one step lighter/darker than the page background (theme-dependent) | Dialogs, `Toast` |

A native drop-shadow effect (`QGraphicsDropShadowEffect`) may be used **only** for `Toast` and
`Dialog`, subtly, to distinguish transient overlays from the page — never for in-page cards.

### 1.8 Animations

Animations are used sparingly and only for state transitions that would otherwise feel abrupt:

| Token | Duration | Usage |
|---|---|---|
| `motion.fast` | 100ms | Hover/press state changes, badge color transitions |
| `motion.base` | 150ms | Page-section fade-in on data load, `Toast` enter/exit |
| `motion.none` | 0ms | Respect the OS "reduce motion" setting (§10) — all durations above collapse to 0 |

No animation blocks interaction — every transition is purely cosmetic and never gates a widget
becoming interactive.

### 1.9 Focus rings

Every focusable widget shows a visible **focus ring** (`border.focus`, 2px, accent color, offset
1px from the widget's own border) when focused via keyboard, using Qt's `QWidget.setFocusPolicy`
and a custom paint/stylesheet rule keyed on `:focus`. Mouse-driven focus (a click) shows the ring
only if the platform convention calls for it (macOS and Windows differ here — follow each
platform's native convention rather than forcing one behavior on both, consistent with
`PLATFORM_BEHAVIOR.md`'s "native, not reimplemented" principle for system behaviors).

### 1.10 Semantic status colors

Status colors are semantic tokens, not raw hex values referenced directly by components — this
indirection is what lets `StatusBadge` support both themes and the reserved Mission Control
extension (`DESIGN_DIRECTIONS.md` §5) without a contract change.

| Token | Maps to (existing run/report states) |
|---|---|
| `status.neutral` | unconfigured, no data |
| `status.info` | `PREPARED`, `QUEUED` |
| `status.active` | `RUNNING` |
| `status.success` | `COMPLETED`, verdicts `APPROVED FOR COMMIT` / `READY FOR COMMIT` / `READY FOR FINAL REVIEW` |
| `status.warning` | `NOT READY FOR FINAL REVIEW`, stale/invalid repository path, `INTERRUPTED`/`UNKNOWN` (reconciliation-classified, ambiguous outcome — see `Supervisor`'s reconciliation logic — never conflated with `FAILED`, which is a definite outcome) |
| `status.danger` | `FAILED`, `NOT APPROVED FOR COMMIT`, timed-out (`failure_reason == "timeout"`, itself a `FAILED` run, not a separate state) |
| `status.cancelled` | `CANCELLED` |

Exact hex values per theme are defined in the implementation's style module at D1A
(`IMPLEMENTATION_ROADMAP.md`) — this document fixes the semantic set and its mapping to existing
domain states, which is the part later code must not silently drift from.

## 2. Themes

Three theme modes, all backed by `command_center.platform`'s settings abstraction
(`PLATFORM_BEHAVIOR.md`):

- **Light** — explicit light palette.
- **Dark** — explicit dark palette.
- **System** — follows the OS appearance setting live (macOS "Appearance," Windows 11
  "Choose your mode"), updating without a restart when the OS setting changes, via
  `command_center.platform`'s system-theme-change notification (`ARCHITECTURE.md` §6).

Every token in §1 has both a light and dark value; no component may hardcode a color that
bypasses theme switching. System theme is the default for a fresh install; the user's last
explicit choice (Light/Dark/System) persists via window/workspace preferences (`ARCHITECTURE.md`
§14).

## 3. Density modes

Two density modes, both available from Settings:

- **Comfortable** (default) — `control.height.lg` (40px) rows, `space.lg` (16px) inter-component
  gaps.
- **Compact** — `control.height.md` (32px) rows, `space.md` (12px) inter-component gaps.

Density affects control heights and spacing only — it never changes typography size or which
fields are shown. Every component in §12 specifies which density modes it supports; a component
that does not vary by density (e.g. `Dialog`) says so explicitly rather than leaving it
ambiguous.

## 4. Accessibility

- Every interactive widget sets an accessible name (`QWidget.setAccessibleName`) and, where the
  visible label alone is insufficient, an accessible description
  (`QWidget.setAccessibleDescription`) — the Qt Widgets equivalent of an ARIA label, exposed to
  macOS VoiceOver and Windows Narrator through Qt's native accessibility bridge.
  `QAccessible`-based tooling (not a browser accessibility tree) is the target for any automated
  accessibility check at implementation time.
- Color is never the only signal for state: `StatusBadge` always pairs its semantic color with a
  text label and, at `icon.sm`, a distinct glyph per status family (success/warning/danger use
  visually distinct icons, not just different hues) — for color-blind users and for the
  monochrome-icon case some platform themes force.
- Minimum contrast ratio: 4.5:1 for body text against its background, in both light and dark
  themes, for every token pairing in §1.10 and §2 — verified at implementation time against the
  final palette, not asserted here as already measured.
- Every dialog and toast is reachable and dismissible via keyboard alone (§5).

## 5. Keyboard behavior

- Standard Qt focus-traversal order (Tab/Shift+Tab) follows visual top-to-bottom, left-to-right
  order within each container; no custom tab-order override unless a specific component's
  contract (§12) says otherwise.
- Enter/Space activates the focused control, matching native `QPushButton`/`QAbstractButton`
  behavior — no component reimplements this.
- Escape closes the focused `Dialog`/`Toast` without committing any pending change.
- Arrow keys navigate within a list/table (`QAbstractItemView`'s native row-to-row navigation) —
  `ProjectCard` grids, `ActivityItem`/`ArtifactRow`/`ReportRow`/`WorktreeRow` lists all use this
  native behavior rather than a custom keyboard handler.
- A global keyboard shortcut for refresh (`INFORMATION_ARCHITECTURE.md` §10) is registered once,
  application-wide, not per-page.

## 6. Platform-native typography

- **macOS**: system font (San Francisco), resolved via Qt's default font handling on macOS — no
  bundled font override. The point sizes in §1.2 apply as-is.
- **Windows 11**: system font (Segoe UI Variable, or the OS default Qt resolves to) — same point
  sizes in §1.2; Qt's per-platform DPI handling is relied on for scaling, not a custom scaling
  layer.
- No custom/bundled font is shipped in Desktop Increment 1 — using each platform's native font
  keeps text rendering and hinting consistent with every other native application on that OS,
  and avoids a font-licensing/bundling concern for a D0/D1-stage product.

## 7. Component contracts

Every component below specifies purpose, states, interactions, keyboard behavior, accessibility
behavior, and supported density. Components are Qt Widgets composites (a `QWidget` subclass
composing standard Qt widgets, styled via the tokens in §1) — none require custom
painting/OpenGL.

### 7.1 AppShell

- **Purpose**: top-level window composing `Sidebar`, `TopBar`, and the active page's content
  area.
- **States**: normal; a single "busy" indicator state while a page-level refresh is in flight
  (does not block interaction with the sidebar/top bar — see `ARCHITECTURE.md` §9).
- **Interactions**: hosts window drag/resize (native `QMainWindow` behavior); restores geometry
  on launch (`PLATFORM_BEHAVIOR.md`).
- **Keyboard**: no component-specific handling beyond standard `QMainWindow` behavior.
- **Accessibility**: sets the window's accessible name to the application name; does not
  intercept focus itself.
- **Density**: not density-variant itself; hosts density-variant children.

### 7.2 Sidebar

- **Purpose**: primary navigation, listing the nine sections (`INFORMATION_ARCHITECTURE.md` §1).
- **States**: expanded (default); collapsed-to-icons (optional, user-toggleable, persisted as a
  window preference).
- **Interactions**: click a `NavigationItem` to switch the active page.
- **Keyboard**: Tab through visible/enabled items in order; Enter/Space activates.
- **Accessibility**: the sidebar itself is an accessible list/tree (`QAccessible::List` role);
  each item exposes its label as its accessible name.
- **Density**: Comfortable uses `control.height.lg` per item; Compact uses `control.height.md`.

### 7.3 NavigationItem

- **Purpose**: one selectable (or disabled, §2.1 of `INFORMATION_ARCHITECTURE.md`) sidebar
  entry.
- **States**: default, hovered, selected (current page), disabled (future section).
- **Interactions**: click selects; disabled items ignore clicks entirely (no toast, no
  no-op animation — clicking a disabled item is simply inert).
- **Keyboard**: focusable via Tab only when enabled (§2.1); Enter/Space selects.
- **Accessibility**: accessible name = section label; accessible description communicates
  disabled state ("Available in a future release") when applicable, mirrored from the visible
  tooltip (§2.1 of `INFORMATION_ARCHITECTURE.md`) so screen-reader users get the same information
  sighted users get from the tooltip.
- **Density**: both (`control.height.lg`/`control.height.md`).

### 7.4 TopBar

- **Purpose**: hosts `ProjectSwitcher`, the global refresh action, the reserved status area
  (`DESIGN_DIRECTIONS.md` §5), and the future command/search entry point
  (`INFORMATION_ARCHITECTURE.md` §7).
- **States**: default; refresh-in-progress (refresh control shows a busy indicator, remains
  clickable-disabled during its own in-flight call only, per `ARCHITECTURE.md` §10).
- **Interactions**: click refresh triggers the active page's data adapter call.
- **Keyboard**: refresh is reachable by its global shortcut (§5) without requiring focus to be in
  the `TopBar` first.
- **Accessibility**: each hosted control has its own accessible name; the bar itself is not a
  single opaque accessible element.
- **Density**: fixed height (`control.height.topbar`) regardless of density mode — the top bar
  does not compress in Compact density.

### 7.5 ProjectSwitcher

- **Purpose**: select the active project scope for per-project pages
  (`INFORMATION_ARCHITECTURE.md` §5).
- **States**: default (a project selected), placeholder ("Select a project") when none is
  selected yet, disabled on pages that are not project-scoped (e.g. Home).
- **Interactions**: opens a dropdown/combo list of the six configured `PROJECT_IDS`; selecting
  one updates every project-scoped page.
- **Keyboard**: standard `QComboBox` keyboard behavior (arrow keys to navigate options, type-
  ahead to jump to a project by name, Enter to confirm, Escape to close without changing
  selection).
- **Accessibility**: accessible name "Project switcher"; each option's accessible name is the
  project's display name plus its sensitivity state (e.g. "BANK, sensitive project") so a
  screen-reader user gets the same sensitivity signal a sighted user gets from `StatusBadge`.
- **Density**: both.

### 7.6 PageHeader

- **Purpose**: page title + optional short description + optional page-level actions (e.g. "Add
  repository path" on Projects).
- **States**: default; loading (title shown, action buttons disabled until the page's initial
  data fetch completes).
- **Interactions**: hosted action buttons behave per standard button conventions.
- **Keyboard**: standard tab order through hosted actions.
- **Accessibility**: title uses `QAccessible::Heading` semantics via Qt's accessible-role API.
- **Density**: both (`type.display` size is constant across density; only vertical padding
  changes).

### 7.7 MetricCard

- **Purpose**: single KPI display (e.g. project count, active-run count) — the desktop
  equivalent of the existing Streamlit `st.metric(..., border=True)` used in Workspace Home's
  header strip.
- **States**: default; empty ("—" shown instead of a number when data is not yet loaded, never a
  fabricated zero).
- **Interactions**: none (display-only) in Desktop Increment 1; may become clickable
  (drill-down) in a later increment without a contract change.
- **Keyboard**: not focusable in D1 (display-only); would need a focus policy added if made
  interactive later.
- **Accessibility**: accessible name combines the label and value (e.g. "Active runs: 3"), read
  as one unit rather than two separate elements.
- **Density**: both.

### 7.8 ProjectCard

- **Purpose**: one project's summary — display name, `StatusBadge` for sensitivity/repository
  state, task count, active-run count — the native equivalent of Workspace Home's per-project
  card (`WORKSPACE_HOME_ARCHITECTURE.md` §3).
- **States**: configured/healthy, unconfigured (no repository path), invalid path, non-git
  directory, detached HEAD — see `WORKSPACE_HOME_SPEC.md` for the exact state set and labels.
- **Interactions**: click navigates to that project's detail on the Projects page
  (`INFORMATION_ARCHITECTURE.md` §8); a per-card "Configure repository path" affordance for
  unconfigured projects jumps directly to the relevant Settings/Projects form field.
- **Keyboard**: focusable as a single unit (Tab lands on the whole card); Enter/Space activates
  the card's primary navigation action, matching its click behavior.
- **Accessibility**: accessible name combines project display name + sensitivity + repository
  state (e.g. "BANK, sensitive, repository not configured").
- **Density**: both — Compact reduces card padding and stacks fewer fields per row without
  dropping any field.

### 7.9 StatusBadge

- **Purpose**: compact status indicator, semantic-colored (§1.10), reused across runs, reports,
  repository state, and project sensitivity.
- **States**: one per semantic token in §1.10, plus a `sensitive` variant (used only for
  BANK/LEGAL project sensitivity, visually distinct from run-state badges so the two meanings are
  never confused).
- **Interactions**: display-only; never itself clickable (a badge inside a clickable row does not
  make the badge a separate target).
- **Keyboard**: not independently focusable — its containing row/card owns focus.
- **Accessibility**: accessible name is the status label text (e.g. "Completed," "Sensitive
  project"), never just a color name.
- **Density**: `control.height.sm` fixed regardless of density mode.

### 7.10 RunSummary

- **Purpose**: one run's compact summary row (source tag, project, task type, state,
  created/started/completed timestamps, duration) — the desktop equivalent of a merged v1.2/v2
  Recent Runs row (`WORKSPACE_HOME_ARCHITECTURE.md` §8).
- **States**: active (`PREPARED`/`QUEUED`/`RUNNING`), terminal (`COMPLETED`/`FAILED`/
  `CANCELLED`/`INTERRUPTED`/`UNKNOWN`, matching `command_center.runtime.db.TERMINAL_STATES`
  exactly; `failure_reason == "timeout"` is a `FAILED` run with a specific reason, not a
  fifth state).
- **Interactions**: click routes to the correct detail view based on the row's `source` tag
  (`v1.2` vs `v2`) — never inferred from the id's shape (`WORKSPACE_HOME_ARCHITECTURE.md` §8/F5).
- **Keyboard**: standard list-row focus/activation (Tab to focus, Enter to activate).
- **Accessibility**: accessible name/description includes source, project, task type, and state
  — enough for a screen-reader user to distinguish two rows that might otherwise look identical
  (e.g. two runs with colliding-looking ids, per the `(source, run_id)` identity rule).
- **Density**: both.

### 7.11 ActivityItem

- **Purpose**: one Recent Activity row (project, event type, timestamp) — desktop equivalent of
  Workspace Home's folded `activity_log` + derived v2 lifecycle rows (`WORKSPACE_HOME_ARCHITECTURE.md`
  §10).
- **States**: default only — activity rows are historical and do not carry an active/inactive
  distinction.
- **Interactions**: click navigates to the related run/task if the entry carries a `run_id`/
  `task_id`; entries without one are display-only.
- **Keyboard**: standard list-row focus/activation.
- **Accessibility**: accessible name combines project + event type + relative timestamp.
- **Density**: both.

### 7.12 ArtifactRow

- **Purpose**: one generated-task-file entry (`generated/<PROJECT>/*.md`).
- **States**: default; redacted (BANK/LEGAL — shows only project + task type + date, per
  `WORKSPACE_HOME_ARCHITECTURE.md` §5.1's artifact allowlist; the real filename/path is never
  present in the row's data, not merely hidden visually).
- **Interactions**: click navigates to a generic project/section navigation target
  (`WORKSPACE_HOME_ARCHITECTURE.md` §5.1's `nav_target`), never opens a raw file path directly
  for a sensitive project.
- **Keyboard**: standard list-row focus/activation.
- **Accessibility**: accessible name reflects exactly what is visible — for a sensitive project,
  this means the accessible name also excludes the filename, matching the visual redaction (no
  screen-reader-only "leak" of data the visual layer hides).
- **Density**: both.

### 7.13 ReportRow

- **Purpose**: one run report entry, with a verdict/severity `StatusBadge`.
- **States**: default; redacted (BANK/LEGAL — verdict/severity badge only, no report body/path,
  per `WORKSPACE_HOME_ARCHITECTURE.md` §5.1's report allowlist); unmatched (a report file with no
  linked run — shows file metadata only, no verdict badge).
- **Interactions**: click navigates to the report detail (non-sensitive) or the redacted-safe
  navigation target (sensitive).
- **Keyboard**: standard list-row focus/activation.
- **Accessibility**: accessible name includes the verdict `StatusBadge`'s label.
- **Density**: both.

### 7.14 WorktreeRow

- **Purpose**: one git worktree entry for a project (path, branch, short HEAD) — desktop
  equivalent of Workspace Home's per-project worktree list (`WORKSPACE_HOME_ARCHITECTURE.md` §7).
- **States**: ok, unconfigured (no repository path), invalid path, not a git repository,
  detached HEAD — mirrors `_discover_worktrees`'s existing state set in
  `command_center/workspace_home.py`.
- **Interactions**: display-only in Desktop Increment 1 (no worktree mutation — binding decision
  12); a future increment may add a "reveal in file manager" action via
  `command_center.platform.reveal_in_file_manager`.
- **Keyboard**: standard list-row focus (no activation behavior in D1, since there is no action
  to trigger yet — a future increment adding the reveal action would also add Enter/Space
  activation at that time).
- **Accessibility**: accessible name includes path, branch, and state (e.g. "unconfigured,"
  "not a git repository") so the state is conveyed without relying on color alone.
- **Density**: both.

### 7.15 EmptyState

- **Purpose**: shown when a page/section has no data to display (e.g. no projects configured) —
  desktop equivalent of the existing Streamlit `st.info(...)` "nothing here yet" convention
  (`WORKSPACE_HOME_ARCHITECTURE.md` §18).
- **States**: one per context (no projects configured, no runs yet, no artifacts yet, no reports
  yet) — each with its own short message and, where applicable, a next-action button (e.g.
  "Configure a repository path").
- **Interactions**: the next-action button, if present, navigates directly to the relevant
  Settings/Projects field.
- **Keyboard**: the next-action button follows standard button keyboard behavior; the state's
  message text itself is not focusable.
- **Accessibility**: message text and any action button both expose accessible names; the
  `icon.lg` illustration (if used) is marked decorative (no accessible name) so it is not
  announced redundantly alongside the message.
- **Density**: both — message/illustration scale down slightly in Compact but remain legible.

### 7.16 ErrorState

- **Purpose**: shown when a specific page/section fails to load, isolated to that region only
  (per-project/per-section failure isolation, `WORKSPACE_HOME_ARCHITECTURE.md` §6/§12 and
  `INFORMATION_ARCHITECTURE.md` §11).
- **States**: transient (a retry is likely to succeed — shows a "Retry" button that re-triggers
  the failed adapter call) and persistent (e.g. "path no longer valid" — shows the next-action
  button relevant to *that* state, such as "Update repository path," not a generic retry).
- **Interactions**: "Retry" or the state-specific next action.
- **Keyboard**: standard button keyboard behavior.
- **Accessibility**: the error message is exposed as an accessible live-region-equivalent
  update — Qt's `QAccessible::Alert` event is raised when the `ErrorState` first appears, so a
  screen-reader user is notified without needing to discover it by navigating there.
- **Density**: both.

### 7.17 LoadingSkeleton

- **Purpose**: placeholder shown while a page/section's initial data fetch is in flight (via the
  `QThreadPool` pattern, `ARCHITECTURE.md` §10).
- **States**: active only — it is removed entirely once data (or an `ErrorState`/`EmptyState`)
  replaces it, never left visible alongside real content.
- **Interactions**: none (non-interactive placeholder).
- **Keyboard**: not focusable.
- **Accessibility**: exposes an accessible "busy" state (`QAccessible::StateFlag::Busy` or
  equivalent) on the region it is replacing, so assistive technology does not read stale/empty
  content as final.
- **Density**: both — skeleton row heights match the density-appropriate row height of the
  content it is standing in for (`RunSummary`, `ProjectCard`, etc.), so there is no layout shift
  when real content replaces it.

### 7.18 Toast

- **Purpose**: transient, non-blocking notification (e.g. "Repository path saved," or a
  background refresh error surfaced from `ARCHITECTURE.md` §12).
- **States**: info, success, warning, danger (mapped to the same semantic tokens as `StatusBadge`,
  §1.10).
- **Interactions**: auto-dismisses after a fixed duration (implementation-time value, consistent
  across all toasts) or is dismissible early via an explicit close control; never requires
  dismissal to continue using the app.
- **Keyboard**: the close control (if focused) responds to Enter/Space; Escape dismisses the most
  recently shown toast if any toast currently has focus.
- **Accessibility**: raised as an accessible alert/notification event on appearance (same
  `QAccessible::Alert` mechanism as `ErrorState`), so it is announced without requiring the user
  to be looking at the corner of the screen it renders in.
- **Density**: not density-variant (fixed size regardless of density mode, since it is an overlay,
  not page content).

### 7.19 Dialog

- **Purpose**: short-lived, modal or non-modal interaction (confirmations, a settings sub-form)
  — never a substitute for sidebar navigation (`INFORMATION_ARCHITECTURE.md` §3).
- **States**: default; busy (an in-flight action inside the dialog disables its confirm button
  and shows a busy indicator, never closes the dialog automatically mid-action).
- **Interactions**: primary/secondary action buttons; a modal `Dialog` blocks interaction with
  the rest of the window until dismissed (native `QDialog.exec()` behavior).
- **Keyboard**: Tab is trapped within the dialog while open (native `QDialog` modal focus
  behavior); Escape cancels/dismisses; Enter activates the default (primary) button, unless focus
  is on a control where Enter has a different native meaning (e.g. a multi-line text field).
- **Accessibility**: accessible role is `QAccessible::Dialog`; focus moves to the dialog's first
  focusable control (or its title, if no control should be pre-focused) on open, and returns to
  the control that triggered the dialog on close.
- **Density**: not density-variant — dialogs use Comfortable spacing regardless of the
  application's current density mode, since dialogs are infrequent, high-stakes interactions
  where extra clarity outweighs density savings.

### 7.20 SettingsForm

- **Purpose**: the Settings page's preference form — theme, density, window-geometry reset,
  workspace preferences (`DESKTOP_INCREMENT_1.md` D3).
- **States**: default; a field showing a validation error (e.g. an invalid path, if a
  path-like preference is ever added to Settings itself — repository-path validation for
  Projects lives on the Projects page, not here, per `INFORMATION_ARCHITECTURE.md` §4).
- **Interactions**: changes apply immediately (theme/density) or on explicit "Save" (anything
  requiring confirmation) — the exact per-field behavior is fixed at D3 implementation time, but
  a field must not silently discard an in-progress edit on navigation away from Settings.
- **Keyboard**: standard form tab order; Enter in a single-line field commits that field's value
  where immediate-apply semantics are used.
- **Accessibility**: every field has an associated accessible label (Qt's `QLabel.setBuddy` or
  equivalent), not a placeholder-only label.
- **Density**: both — Compact reduces row height and inter-field spacing without removing any
  field.
