# AI Command Center — Desktop Design Directions

Status: **Resolved.** Three directions were reviewed. **Professional Control Plane is the
approved direction (binding decision 13).** This document records all three for context and the
rationale for the decision — it does not leave the choice open.

## 1. Professional Control Plane — APPROVED

### Visual character
Dense, structured, information-forward. Clear grid alignment, moderate corner radii, a
restrained accent color used only for interactive/status elements, borders over shadows for
separating regions (matching the existing Streamlit application's `st.container(border=True)`
visual language rather than introducing a heavier card-shadow aesthetic).

### Information density
Medium-high. Tables and cards show multiple data points per row/card (project name, sensitivity
badge, repository state, task count, active-run count) without requiring a drill-down for basic
orientation — the same density Workspace Home's Streamlit page already targets
(`WORKSPACE_HOME_ARCHITECTURE.md` §3's Header KPI strip + per-project cards).

### Strengths
- Matches the existing product's actual usage pattern: a developer scanning multiple projects'
  state at once, not a single focused task.
- Translates directly from the existing Streamlit information architecture (KPI strip, cards,
  bounded lists) — lowest risk of losing functional parity during the native rewrite.
- Scales cleanly to more data (more projects, more runs) without a redesign, because density is
  already part of the design rather than something added later.
- Reads as a professional tool a developer would trust with production-adjacent work, appropriate
  for a control plane that touches BANK/LEGAL-adjacent project metadata (even though it never
  touches production data itself, per `PRODUCT_VISION.md` §5).

### Weaknesses
- Higher initial implementation cost per component than a minimal direction — each component
  (`MetricCard`, `ProjectCard`, `RunSummary`, etc.) needs a defined dense layout, not just a
  generic container.
- Density can read as cluttered if not paired with disciplined spacing/typography tokens (see
  `DESIGN_SYSTEM.md`) — the direction depends on the token system being followed consistently,
  not on visual style alone.

### Expected evolution
Adding new sections (Sessions, Execution, Git, Artifacts, Reports, Agents — see
`INFORMATION_ARCHITECTURE.md` §1) extends the existing card/table/badge vocabulary rather than
introducing new visual language per section.

### Fit for Desktop Increment 1
Direct fit. D1's scope (Home, Projects, Settings) is exactly the kind of dense, multi-project,
multi-field content this direction is built for.

### Fit for later execution/session functionality
Direct fit. Live run tables, session lists, and event streams are naturally dense, tabular
content — the direction does not need to change character to accommodate them, only add new
component instances (`RunSummary`, future live-status rows) within the same system.

## 2. Minimal Native Workspace — considered, not selected

### Visual character
Sparse, generous whitespace, large touch-friendly targets, one primary focus per screen —
closer to a consumer productivity app than a control plane.

### Information density
Low. One or two data points surfaced per card; everything else requires a click-through.

### Strengths
- Fastest to build initially — fewer states per component, less token complexity.
- Very approachable for a first-time user.

### Weaknesses
- **Does not match how this product is actually used today.** The existing Streamlit
  application's core value is exactly the KPI-strip/cross-project rollup pattern
  (`WORKSPACE_HOME_ARCHITECTURE.md` §3) — a minimal direction would require either compressing
  that content awkwardly or forcing extra clicks for information the current tool already shows
  at a glance, a functional regression framed as a redesign.
- Scales poorly to more projects/more runs without either abandoning the "one focus per screen"
  premise or adding pagination/drill-down friction that the current tool does not have.

### Expected evolution
Would likely need to grow *toward* Professional Control Plane's density as Sessions/Execution
functionality is added, meaning much of its initial component work would be redone rather than
extended.

### Fit for Desktop Increment 1
Poor fit — D1's Home/Projects content is inherently multi-field per item; a minimal direction
would immediately need exceptions to its own premise.

### Fit for later execution/session functionality
Poor fit — live run/session data benefits from density (multiple runs visible at once), which
this direction actively works against.

## 3. Mission Control — considered, not selected (for now)

### Visual character
High-density, dark-theme-forward, status-board aesthetic — persistent live indicators, strong
use of color-coded status, closer to an ops/NOC dashboard than a developer tool.

### Information density
Very high. Optimized for glanceable status across many concurrent live items (e.g. many
simultaneous agent runs).

### Strengths
- Best fit for a future where many agent sessions run concurrently and the primary need is
  glanceable live status, not per-item drill-down.
- The status-badge and top-bar-status vocabulary this direction implies is worth reserving space
  for now, even without building the full direction (see §4).

### Weaknesses
- **Premature for Desktop Increment 1.** D1 is read-only, single-session-at-a-time in practice
  (starting/cancelling runs is explicitly out of scope — see `DESKTOP_INCREMENT_1.md`), so a
  design optimized for many concurrent live items has no real content to organize yet.
- A dark-theme-forward, ops-board aesthetic risks looking over-built relative to the product's
  actual current capability, which could misrepresent what the tool does today (binding decision
  14 requires reserving affordances *without* prematurely implementing them — a full Mission
  Control build would violate that by implementing more than the product does).

### Expected evolution
Natural target for a future increment once multi-session live execution control ships (explicitly
out of scope for D1 — see `PRODUCT_VISION.md` §9, `DESKTOP_INCREMENT_1.md`).

### Fit for Desktop Increment 1
No fit — nothing in D1's scope needs a live multi-session status board.

### Fit for later execution/session functionality
Strong future fit, once Sessions/Execution ship with real concurrent-run content to display.

## 4. Approved decision

**Professional Control Plane is approved (binding decision 13).**

**Why:** it is the only one of the three directions that matches the product's actual, already-
validated usage pattern (a dense, cross-project rollup, exactly as implemented in
`command_center.workspace_home` and its Streamlit renderer) without either under-building (Minimal
Native Workspace) or over-building relative to Desktop Increment 1's real, read-only,
single-session scope (Mission Control). It carries the lowest risk of a functional regression
during the native rewrite, because it does not require compressing or reinterpreting content the
existing product already shows.

## 5. Forward-compatible Mission Control affordances (binding decision 14)

Two specific, narrow affordances are reserved now, without building the rest of Mission Control:

1. **A reusable status-badge system.** `StatusBadge` (see `DESIGN_SYSTEM.md`) is designed with
   enough semantic-color/state vocabulary (active, queued, completed, failed, cancelled,
   timed-out — matching `command_center.runtime.db`'s existing run states) to extend to a denser,
   more numerous badge display later, without a redesign of the component's contract. Desktop
   Increment 1 uses it only in the low-cardinality contexts Home/Projects need (a handful of
   badges per view, not a status-board's worth).
2. **A reserved top-bar status area.** `TopBar`'s layout (see `DESIGN_SYSTEM.md`) reserves visual
   space for a future ambient status indicator (e.g. "N runs active") without populating it with
   live-updating content in D1 — Desktop Increment 1 either leaves it empty or shows a static,
   manually-refreshed count, never a polling/live-updating element (which would violate
   `INFORMATION_ARCHITECTURE.md` §6's "no automatic polling" rule for D1).

Neither affordance implements Mission Control's live-update behavior, concurrent-session
handling, or dark-theme-forward aesthetic — they exist only to avoid a breaking layout change
when a future increment does build that functionality.
