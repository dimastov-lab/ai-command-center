# Roadmap Reconciliation — Phase 2

Status: **Analysis and classification only. No documents merged, no code/runtime changed, no
`data/tasks.json` write, nothing committed.**

Input: `docs/roadmap/ROADMAP_SOURCE_INDEX.md` (Phase 1). This document re-reads the highest-priority
sources in full and cross-checks their claims directly against the current repository code and the
current (live, on-disk) `data/tasks.json`, since several Phase 1 sources turned out to describe a
moving target.

**Methodology note**: "cross-checked against code" below means read-only inspection
(`grep`, `git log`/`git show`, `python -c` reads of JSON) performed during this analysis — no file
was modified, no script in `roadmap/program/` was executed, and `data/tasks.json` was only read,
never written, by this task.

---

## 0. Legend — the six status classes, defined precisely

Several documents in this repository are authoritative for a *decision* that hasn't shipped as code
yet (e.g. `docs/desktop/*`), which is a different kind of "true" than a document describing *running*
behavior. The classification below distinguishes the two rather than forcing one label:

| Class | Meaning |
|---|---|
| **Source of Truth — Current (SoT-Current)** | Authoritative for what the system *does right now*; verified or verifiable directly against code/runtime. |
| **Source of Truth — Target (SoT-Target)** | Authoritative for an *approved, binding* decision about what will be built; not yet implemented, but not open for re-litigation either. |
| **Derived** | Synthesized/summarized from other SoT documents; should track them over time but may lag. |
| **Historical** | An accurate record of a past point in time; not maintained forward; not authoritative for current state. |
| **Deprecated** | Explicitly superseded by a newer decision; candidate for archival. |
| **Draft** | Proposed, not yet approved/adopted/triaged; a candidate input, not authoritative on its own. |
| **Unknown** | Provenance, authorship, approval status, or relationship to the rest of the doc set cannot be determined. |

A document can legitimately carry two classes for two different sections (e.g. `CURRENT_STATE.md`'s
operating rules vs. its per-project snapshot) — this is called out explicitly where it applies,
rather than forced into one label.

---

## 1. Source Matrix

### 1.1 Root-level product documents

| Document | Scope of responsibility | Detail level | Classification | Basis |
|---|---|---|---|---|
| `README.md` | User-facing setup, runtime-data contract, v1.1/v1.2 feature summary, scope/limitations | Medium | **SoT-Current** (setup/testing instructions) + **Historical** (feature list — stops at v1.2) | Setup steps verified plausible against `scripts/start-ui.sh`/`requirements.txt`; feature list has zero mentions of ADR 0001–0003 or PR #9 (`grep` confirmed) |
| `ARCHITECTURE.md` (root) | Streamlit app's module layout, execution model, security boundaries, as of v1.2 | High | **SoT-Current** for §1–§10 (v1.1 baseline) and §11 (v1.2 agent workflow) + **Historical** beyond that point | Describes `app.py`/`command_center/*` accurately for v1.2; does not mention `launch.py`, `launch_service.py`, `executors.py`, `tasks_repository.py`, `task_import.py`, `os_actions.py`, or `task_view.py` — all real, `git log`-confirmed modules added by ADR 0001/0002 and PR #9 |
| `CHANGELOG.md` | Version-by-version release history | Medium | **Derived** (from ADRs/commits) but **materially incomplete** | Has `[1.0.0]`→`[1.2.0]` plus two `[Unreleased]` entries (Desktop D0 docs; Sprint 3 Workspace Home). Zero mention of ADR 0001/0002/0003, "Engineering Control Center v2," Portfolio Execution, `runtime.db`/Session Supervisor, or PR #9's transactional task import — all `git log`-confirmed as merged to `main` |
| `CURRENT_STATE.md` | Per-project status snapshot (5 projects) + 7 "Global Operating Rules" | Low–Medium | **Split**: Global Operating Rules = **SoT-Current** (process principles, not falsifiable by code, no contradicting evidence found) · Per-project status = **Historical** | Dated 2026-07-15 — before v1.2, all 3 ADRs, `docs/desktop/*`, and the Founder Audit |
| `WORKSPACE_HOME_ARCHITECTURE.md` | Workspace Home read-model architecture, review history, implementation plan | Very High (1149 lines) | **SoT-Current** (for the read-model/redaction design — its own header confirms all 10 §17 steps are built, matching `CHANGELOG.md`'s "Sprint 3 Increment 1" entry) + **Historical** (the review-iteration narrative: BLOCKED→resolved→APPROVED passes) | Cross-verified against `CHANGELOG.md`'s Workspace Home entry — consistent |
| `RELEASE_NOTES_v1.1.md` | User-facing v1.1 summary | Medium | **Historical** | Frozen snapshot, superseded as an ongoing source by `CHANGELOG.md` |
| `RELEASE_NOTES_v1.2.md` | User-facing v1.2 summary | Medium | **Historical** | Same reasoning. No `v1.3`+ release notes exist despite substantial shipped work since (ADRs, PR #9) — the release-notes *practice* itself appears to have lapsed after v1.2 |
| `DECISIONS.md`, `INBOX.md` | Intended decision/inbox log | None (0 bytes) | **Unknown / Vacant** | No content to classify |

### 1.2 `docs/adr/` — Architecture Decision Records

| Document | Scope of responsibility | Detail level | Classification | Basis |
|---|---|---|---|---|
| ADR 0001 (Engineering Control Center v2, Increment 1) | Task-model extension, `app.py`→`command_center/*` logic migration, executor abstraction, automation-safety boundary | High | **SoT-Current** for the Decision section (self-reports "Accepted, implemented"; `git log` confirms `f220699`/`52ee3c4` landed the described modules) · **Draft/Roadmap** for its own "Scope explicitly not built (Tier B)" section | Verified: `command_center.launch`/`launch_service`/`executors`/`tasks_repository`/`os_actions` all exist per repo structure |
| ADR 0002 (Project Config as canonical engineering defaults) | `default_branch`/`default_executor`/`default_prompt` ownership, task-creation inheritance | High | **SoT-Current** for the decision · **Historical/Stale** for its "Known limitation" section's claim that `models.PROJECT_IDS` "remains a fixed, hardcoded list (AIOS, AICOS, BANK, LEGAL, BUSINESS, PERSONAL)" | **Directly falsified by current code** — see §2 Conflict 1 below. `PROJECT_IDS` was expanded to 9 entries in commit `98d7714`, after this ADR was written |
| ADR 0003 (Live Execution Center v2 / Kanban Launch Bridge) | `runtime.db` as execution source of truth, async Kanban launch bridge | High | **SoT-Current** | Self-reports "Accepted, implemented"; consistent with the Founder Audit's independent confirmation ("сегодня основной UI использует исключительно v2") |

**Dependency note**: none of the three ADRs has been amended since PR #9 (`98d7714`) shipped — by
ADR convention this is expected (ADRs are immutable point-in-time records), but it means **no ADR
currently documents the `PROJECT_IDS` expansion or the transactional task-import pipeline** — see
Conflict 1/2.

### 1.3 `docs/audits/` — Founder Functional Audit

| Document | Scope of responsibility | Detail level | Classification | Basis |
|---|---|---|---|---|
| `FOUNDER_FUNCTIONAL_AUDIT_9761459.md` | Full-product independent functional audit at HEAD `9761459` | Very High (324 lines, 26 findings) | **Historical, Partially Superseded** | Its own companion STATUS doc explicitly forbids treating it as current source of truth until reconciled. Independently confirmed here: `main` has advanced 3 commits past `9761459` (`98d7714`→`4447619`→`12fe1ad`→`6c8336b`), and PR #9 (`98d7714`) directly addresses BLOCKER-2 (`tasks.json` locking), BLOCKER-3 (transactional import), and part of BLOCKER-4 (registry expansion) |
| `FOUNDER_FUNCTIONAL_AUDIT_9761459_STATUS.md` | Tracks audit-to-current-state reconciliation | Low (55 lines) | **SoT-Current** for "what is the reconciliation process and what remains to be done" | Actively correct as of this analysis — but **itself now slightly stale**: it names PR #9's scope only generically ("transactional task import and shared task-storage locking") without enumerating which of the 26 findings (BLOCKER-2/3, part of BLOCKER-4, part of MINOR-*) are actually closed by it — see Conflict 4 |
| `FOUNDER_FUNCTIONAL_AUDIT_TASKS_9761459.json` | 33-item candidate remediation backlog, wave-tagged | High (structured) | **Draft** | Its own STATUS doc says explicitly: not imported, not schema-compatible with the current `task_import.py`, requires triage against current `main` before use |

### 1.4 `docs/desktop/` — Desktop application documentation set (D0)

| Document | Scope of responsibility | Detail level | Classification | Basis |
|---|---|---|---|---|
| `docs/desktop/README.md` | Reading order + 14 "binding decisions" | Medium | **SoT-Target** | Internally consistent, no contradicting evidence; explicitly labeled D0 throughout |
| `docs/desktop/PRODUCT_VISION.md` | Product purpose, boundary vs. AIOS/AICOS, six-project framing | Medium | **SoT-Target**, but **contains a stale factual claim** | States "the six projects it tracks (AIOS, AICOS, BANK, LEGAL, BUSINESS, PERSONAL)" — same staleness as ADR 0002, see Conflict 1 |
| `docs/desktop/ARCHITECTURE.md` | Target `command_center.desktop`/`.application`/`.platform` package architecture | High | **SoT-Target** | No code exists yet to contradict it; internally consistent with ADR 0001's binding-decision-9 cross-reference (verified: ADR 0001 §Context point 3 references this document, and this document's package-reuse principle matches ADR 0001's module table) |
| `docs/desktop/INFORMATION_ARCHITECTURE.md` | 9-section target navigation, D1 subset | Medium | **SoT-Target**, inherits the same six-project staleness indirectly (navigation doesn't enumerate projects directly, low risk) |
| `docs/desktop/DESIGN_DIRECTIONS.md` | 3 evaluated directions; Professional Control Plane approved | High | **SoT-Target** (explicitly "Resolved," binding decision 13) |
| `docs/desktop/DESIGN_SYSTEM.md` | Design tokens, component contracts | High | **SoT-Target**, implements the decision above |
| `docs/desktop/WORKSPACE_HOME_SPEC.md` | Native Home page spec | Medium-High | **SoT-Target**, explicitly defers to `WORKSPACE_HOME_ARCHITECTURE.md` for the read model — also repeats the "all six projects" framing (§ "Empty install" edge case) |
| `docs/desktop/PLATFORM_BEHAVIOR.md` | macOS/Windows platform contract | Medium | **SoT-Target** |
| `docs/desktop/DESKTOP_INCREMENT_1.md` | Frozen D1–D4 scope, binding decisions 11/12 | High | **SoT-Target** |
| `docs/desktop/IMPLEMENTATION_ROADMAP.md` | D1A→D4-final-gate commit-sized sequencing | Very High | **SoT-Target / Draft-Roadmap** (it is the most concrete forward roadmap document that is also internally approved, but zero steps have started per its own header) |

**Independently confirmed**: the Founder Audit's Desktop UI section ("0% … Кода нет вообще") and this
analysis's own `grep` for `PySide6`/`QtWidgets`/`QApplication` outside `.venv/` both confirm zero
desktop code exists — every "SoT-Target" label above is honest about not yet being code.

### 1.5 Context / per-project status documents

| Document | Scope of responsibility | Detail level | Classification | Basis |
|---|---|---|---|---|
| `context/AIOS_CONTEXT.md`, `projects/AIOS.md` | AIOS product context/status (a different tracked project, not AI Command Center itself) | High | **Historical / Out-of-scope** for an AICC roadmap | Dated 2026-07-15; describes the AIOS platform's own roadmap, not this repository's product |
| `context/BANK_CONTEXT.md`, `context/LEGAL_CONTEXT.md` | Sensitive-project context | Low | **Historical / Out-of-scope** | Same reasoning, sensitive-project scope |
| `projects/BANK_STRATEGY.md`, `BUSINESS.md`, `LEGAL.md`, `PERSONAL.md` | Intended per-project control cards | None (0 bytes) | **Unknown / Vacant** | No content |

### 1.6 `roadmap/` — the newly-appeared program roadmap package

| Document | Scope of responsibility | Detail level | Classification | Basis |
|---|---|---|---|---|
| `roadmap/program/README.md` | Package usage instructions | Low | **Unknown** | No authorship, date, or approval trail found anywhere in the repository's own history/commits |
| `roadmap/program/PROGRAM_ROADMAP.md` | Human-readable 46-task, 9-level "Global AI Platform" program roadmap across 6 project scopes | High | **Draft / Unknown provenance** | Not linked from, or referenced by, any other document in this repository (README, CHANGELOG, ADRs, `docs/desktop/*`, the audit) — see Conflict 5 |
| `roadmap/program/program_roadmap.json` | Canonical machine-readable form of the same 46 tasks | High | **Draft / Unknown provenance** | Same reasoning; additionally uses a **project-id taxonomy that does not resolve against the live code's canonical registry** — see Conflict 1/2, the most severe finding in this analysis |
| `roadmap/program/import_program_roadmap.py`, `ready_tasks.py` | Ad-hoc import/worktree-prep tooling | — (code, not doc) | **Deprecated-on-arrival, relative to the canonical pipeline** | `command_center/task_import.py` + `scripts/import_tasks.py` (shipped in PR #9, *before* this package appeared) already provide a validated, tested, UI-integrated equivalent — see Conflict 2 |

---

## 2. Conflict Matrix

Six conflicts were found, ranked by severity. All are stated with direct evidence gathered during
this analysis (code reads, `git log`, live `data/tasks.json` inspection) — not simply inferred from
Phase 1's document text.

### Conflict 1 — The canonical project registry has three incompatible versions in play [SEVERITY: HIGH]

| Source | Registry claimed |
|---|---|
| README.md, `ARCHITECTURE.md`, ADR 0002 ("Known limitation"), every `docs/desktop/*` document, `FOUNDER_FUNCTIONAL_AUDIT_9761459.md` | 6 projects: `AIOS, AICOS, BANK, LEGAL, BUSINESS, PERSONAL` |
| **`command_center/models.py` on `main`, right now** (`PROJECT_IDS`) | **9 projects: `AICC, AIOS, AICOS, PRODUCT, ECOSYSTEM, BANK, LEGAL, BUSINESS, PERSONAL`** — changed in commit `98d7714` (PR #9), *after* every document above was written |
| `roadmap/program/program_roadmap.json` | 6 project *scopes*, but named `AIOS, AICOS, AI_COMMAND_CENTER, AIOS_PRODUCT, PORTFOLIO, PLATFORM` — a **fourth, independent naming scheme** that maps to *none* of the above cleanly |

**Verified with `project_config.normalize_project_id`/`PROJECT_NAME_ALIASES`**: `"AI Command Center"`
(with spaces, the audit's own observed value) *does* resolve to `"AICC"` via the alias table. But
`"AI_COMMAND_CENTER"` (underscores — the value actually used throughout `roadmap/program/`) does
**not** — the alias lookup only folds whitespace, not underscores, so this exact string fails
validation.

**Who should win**: the live code (`command_center/models.py`) — it is runtime ground truth and
cannot reasonably be argued with. Every document in the first row is stale and needs an update.
The `roadmap/program/` package's project-naming convention is incompatible on its face and needs
remapping before any future (re-)import.

**Documents needing update**: `README.md`, `ARCHITECTURE.md` (root), `docs/desktop/PRODUCT_VISION.md`,
`docs/desktop/WORKSPACE_HOME_SPEC.md` (its "all six projects" edge-case language), ADR 0002 should
**not** be edited (ADRs are immutable historical records by convention) — instead, a **new ADR**
documenting the registry expansion is the correct fix, and none currently exists for it.

### Conflict 2 — Live `data/tasks.json` already re-triggers the exact defect PR #9 was built to close [SEVERITY: HIGH, ACTIVE]

The Founder Audit's **Finding B-1 (Blocker)** and **BLOCKER-4** describe production `tasks.json` data
landing via a process outside the application, with `project` values not in the canonical registry.
PR #9 (`98d7714`) shipped `command_center/task_import.py` + `scripts/import_tasks.py` specifically to
close this class of defect — `task_import.py`'s `validate_task_package` calls
`project_config.normalize_project_id` and **rejects** ("Неизвестный проект: …") any task whose
`project` field doesn't resolve.

**Independently verified in this analysis** (read-only inspection of the live, on-disk
`data/tasks.json`, performed during this reconciliation — not modified): of 56 tasks currently
present, **26 have a `project` value that does not resolve against the current registry**:
`AI_COMMAND_CENTER` (13), `PLATFORM` (4), `AIOS_PRODUCT` (9). A further 6 values (`AI Command Center`
×5, `AIOS Product` ×1) *would* resolve via the alias table if re-validated, and `Ecosystem`/`PORTFOLIO`
are 1 each — `Ecosystem` resolves (case-insensitive alias exists), `PORTFOLIO` does not (no alias,
not a canonical id).

`data/backups/tasks-before-program-roadmap-*.json` (two timestamped snapshots, both present on disk)
confirm a script recently wrote `tasks.json` under the name `*-before-program-roadmap-*` — consistent
with `roadmap/program/import_program_roadmap.py` having been run, which does **not** call
`task_import.py`'s validation at all (it is a separate, standalone script).

**Who should win**: the validated `command_center/task_import.py` / `scripts/import_tasks.py`
pipeline is the intended, tested, canonical mechanism — it is documented in its own module docstring
as "the exact same parse/validate/preview/apply pipeline the Create Task page's uploader uses."
`roadmap/program/import_program_roadmap.py` bypasses it entirely and should not be run again without
first remapping its project values through the canonical alias table (or extending
`PROJECT_NAME_ALIASES` deliberately, as a founder decision, not a script workaround).

**This is a live, present-tense data-integrity issue, not a historical one** — flagged here for
founder attention; no fix was applied, per this task's constraints.

### Conflict 3 — README.md / CHANGELOG.md do not document roughly half of what's shipped on `main` [SEVERITY: MEDIUM-HIGH]

ADR 0001–0003 and PR #9 describe substantial, tested, "Accepted, implemented" functionality:
the v2 task-model extension, `launch`/`launch_service`/`executors`/`os_actions`/`task_view` modules,
the async Kanban↔Supervisor bridge, and the transactional task-import pipeline. **None of this
appears in `CHANGELOG.md`'s version history or `README.md`'s feature list** (`grep` for
`task_import|import_tasks|transactional|AICC` across both returned zero hits).

**Who should win**: the ADRs + code. `README.md`/`CHANGELOG.md` need a substantial refresh — this is
the single most user-visible staleness gap, since these are the first two documents a reader
(founder or contributor) opens.

### Conflict 4 — The Founder Audit's status doc under-specifies exactly what PR #9 closed [SEVERITY: MEDIUM]

`FOUNDER_FUNCTIONAL_AUDIT_9761459_STATUS.md` correctly identifies that PR #9 landed after the audit
and that some findings "may therefore already be resolved" — but it does not enumerate *which* of the
26 findings. This analysis can now state precisely, from code:

- **BLOCKER-2** (no locking on `tasks.json`/`execution_queue.json` writes) and **BLOCKER-3** (no
  task-ID uniqueness check) — both named directly in PR #9's own commit message ("shared task
  storage locking"); very likely closed, though this analysis did not re-run the audit's specific
  concurrency tests to confirm.
- **BLOCKER-4** (production data outside the registry, landing via a non-code path) — **partially**
  addressed (the registry now includes `AICC`/`PRODUCT`/`ECOSYSTEM`, and a validating import path
  now exists) but **actively unresolved in practice** — see Conflict 2, which shows the exact same
  defect class recurring via a different out-of-band script *after* PR #9 shipped.
- **Task Import 25%** (audit's readiness score, "единственные реально работающие пути создания —
  по одной задаче за раз") — very likely obsolete: batch import now exists and is UI-integrated.
- Everything else in the audit (Parallel Execution, Git/Worktree, Portfolio Execution, Runtime v2,
  Security BLOCKER-1 network bind, Reliability, Desktop UI 0%) is **not** touched by PR #9's stated
  scope and should be treated as still open.

**Who should win**: this is new information this analysis produced by reading code directly — it
does not exist in any document yet. `FOUNDER_FUNCTIONAL_AUDIT_9761459_STATUS.md` is the correct place
for it and should be updated with this finding-by-finding breakdown before Phase 3.

### Conflict 5 — `docs/desktop/*` and `roadmap/program/*` both claim a "desktop shell" epic, with no cross-reference [SEVERITY: MEDIUM]

`docs/desktop/IMPLEMENTATION_ROADMAP.md` sequences the desktop shell into nine granular,
independently-reviewable steps (D1A→D4B). `roadmap/program/program_roadmap.json`'s `AICC-D1-001`
("Desktop shell and Workspace Home") represents the same overall initiative as a single flat
Level-0 task in a 46-item graph. Neither document references the other; nobody has reconciled
"one task in a 9-level cross-project graph" against "nine sequenced sub-increments with per-stage
acceptance criteria."

**Who should win**: neither, outright — they operate at different granularities and are not
mutually exclusive (the `docs/desktop/` sequencing could become the definition-of-done for
`AICC-D1-001`). This needs an explicit founder/maintainer decision in Phase 3, not a silent merge.

### Conflict 6 — Three independent, non-cross-referencing backlogs exist for "what's next" [SEVERITY: MEDIUM]

| Backlog | Source | Shape |
|---|---|---|
| ADR 0001 "Tier B" | Architecture-driven | Prose list: Project Dashboard/Engineering Health rebuild, Live Execution Center rebuild, periodic Engineering Supervisor, standalone Health/Workspace-Manager/Reporting pages, global search, real ChatGPT/Codex/Gemini/Remote executors |
| Founder Audit's 33-item JSON | Audit-driven, safety/integrity-first | Wave-tagged (`wave: 0`–`4`), dependency-annotated, `target_version: v1.3` |
| `roadmap/program/program_roadmap.json` | Unknown provenance, cross-project | 46 tasks, 9 dependency levels, 6 project scopes, Level-0-through-Level-8 ("Foundation" → "Global Platform") |

Some apparent overlaps were spotted (not confirmed as true duplicates): `AICC-SELF-001`
("Self-development planning engine," roadmap/program Level 5) plausibly overlaps the audit backlog's
"Wave 4 (Self-Development Bootstrap)" grouping named in the audit's §15; `AICC-GIT-001` ("Git Center,"
roadmap/program Level 2) plausibly overlaps the audit's MINOR-4 (no ahead/behind) and NIT-1 (Git
Center shows the tool's own repo, not the selected project's). None of the three documents
acknowledges the other two.

**Who should win**: none unilaterally — see §4 recommendation. All three should feed a single
triage pass in Phase 3, not be merged mechanically.

---

## 3. Dependency Map

Documents that explicitly cite or structurally depend on another document (verified by in-text
cross-reference, not inferred):

```
command_center/models.py (CODE — ultimate ground truth)
  └─▶ ADR 0002                              (documents PROJECT_IDS; now stale re: registry size)
        └─▶ docs/desktop/PRODUCT_VISION.md   (inherits "six projects" framing)
              ├─▶ docs/desktop/ARCHITECTURE.md
              ├─▶ docs/desktop/INFORMATION_ARCHITECTURE.md
              ├─▶ docs/desktop/WORKSPACE_HOME_SPEC.md   (also directly cites WORKSPACE_HOME_ARCHITECTURE.md, see below)
              ├─▶ docs/desktop/DESIGN_DIRECTIONS.md ─▶ docs/desktop/DESIGN_SYSTEM.md
              └─▶ docs/desktop/DESKTOP_INCREMENT_1.md ─▶ docs/desktop/IMPLEMENTATION_ROADMAP.md

ADR 0001 (Engineering Control Center v2, Increment 1)
  ├─▶ ADR 0002 (extends ADR 0001's task model)
  │     └─▶ ADR 0003 (builds the Kanban bridge on ADR 0001/0002's launch pipeline)
  └─◀▶ docs/desktop/ARCHITECTURE.md   (ONE mutual cross-reference: ADR 0001 §Context cites this
                                        doc's binding decision 9; this doc's package-reuse principle
                                        matches ADR 0001's module table — the only two-way link
                                        found between the "Desktop" track and the "Engineering
                                        Control Center v2" track)

WORKSPACE_HOME_ARCHITECTURE.md (root — read-model architecture, implemented)
  └─▶ docs/desktop/WORKSPACE_HOME_SPEC.md   (explicit, documented deferral: "this document does not
                                              redefine Workspace Home's architecture — WORKSPACE_
                                              HOME_ARCHITECTURE.md ... remains authoritative")

CHANGELOG.md
  └── should be derived from: ADR 0001/0002/0003, WORKSPACE_HOME_ARCHITECTURE.md, docs/desktop/* D0
      landing, PR #9 — currently derived from only a subset (v1.0–v1.2, Workspace Home, Desktop D0
      docs); ADR 0001–0003 and PR #9 are MISSING inputs (Conflict 3)

README.md
  └── should be derived from: CHANGELOG.md — currently only reflects CHANGELOG.md's state through
      v1.2, missing everything after (Conflict 3)

FOUNDER_FUNCTIONAL_AUDIT_9761459.md
  └─▶ FOUNDER_FUNCTIONAL_AUDIT_9761459_STATUS.md   (explicit tracking/reconciliation doc)
        └─▶ FOUNDER_FUNCTIONAL_AUDIT_TASKS_9761459.json   (backlog derived from the audit's findings)

roadmap/program/program_roadmap.json
  └─▶ roadmap/program/PROGRAM_ROADMAP.md   (human-readable projection of the same 46 tasks — the
                                             only dependency this package has, and it is entirely
                                             internal to the package; NO edge connects it to any
                                             other document in the repository)

data/tasks.json (RUNTIME, not a doc)
  ◀── command_center/task_import.py / scripts/import_tasks.py   (canonical, validating writer)
  ◀── roadmap/program/import_program_roadmap.py                 (ad-hoc writer, bypasses validation —
                                                                   two independent writers, no
                                                                   cross-check between them)
```

**The single most important structural observation**: `roadmap/program/*` is a true dependency-graph
island. Every other document cluster in this repository connects to at least one other cluster
(desktop↔ADR via binding decision 9; audit→status→backlog; Workspace Home root↔desktop spec). The
program roadmap package connects to nothing else — which is itself evidence for its "Unknown
provenance" classification in §1.6, independent of the project-naming incompatibility in Conflict 1.

---

## 4. Recommendations for unification

1. **Fix the registry documentation gap first, with a new ADR, not edits to old ones.** Write
   ADR 0004 recording the `PROJECT_IDS` expansion (`AICC`, `PRODUCT`, `ECOSYSTEM` added) and the
   `task_import.py`/`scripts/import_tasks.py` pipeline from PR #9 — both already shipped and
   untraceable to any ADR today. This single new document resolves Conflict 1 and half of Conflict 3
   at the architecture-record layer.
2. **Refresh `README.md` and `CHANGELOG.md` against `main`, not against v1.2.** This is the most
   user-visible gap (Conflict 3) and the cheapest to close once ADR 0004 exists to summarize from.
3. **Do not re-run `roadmap/program/import_program_roadmap.py`.** Its already-imported rows
   (26 of 56 live tasks) should be resolved by an explicit founder decision — either extend
   `PROJECT_NAME_ALIASES` to accept the package's naming convention (if the package is judged
   authoritative) or correct/re-import the affected task records through the canonical
   `scripts/import_tasks.py --dry-run` path (if not). This is a data decision, not a documentation
   one, and is out of scope for this phase — flagged for the founder, not resolved here.
4. **Update `FOUNDER_FUNCTIONAL_AUDIT_9761459_STATUS.md` with the finding-by-finding PR #9 mapping**
   in Conflict 4, so the audit's 26 findings have an accurate open/closed state before its 33-item
   backlog is triaged in Phase 3.
5. **Establish, in Phase 3, an explicit relationship between the three backlogs** (ADR 0001 Tier B;
   the audit's 33-item wave-tagged package; `roadmap/program`'s 46-item cross-project graph) rather
   than treating any one as authoritative by default. A single triage pass — classify every item as
   `Done` / `Still Open` / `Superseded` / `Duplicate` against current `main`, exactly as
   `FOUNDER_FUNCTIONAL_AUDIT_9761459_STATUS.md` already prescribes for its own backlog — should be
   extended to cover all three, not just the audit's.
6. **Decide `roadmap/program/`'s status explicitly before Phase 3 uses it.** Given it is a dependency
   island with an incompatible project-naming scheme and an import mechanism that already caused a
   live data-integrity issue, it should not be silently treated as equal-weight to the in-repo,
   reviewed `docs/desktop/*`/ADR track until a founder confirms its provenance and intent.
7. **`CURRENT_STATE.md`'s per-project section** should either be refreshed from the Founder Audit's
   system map (§4 of that audit) or explicitly retired in favor of it — keep the "Global Operating
   Rules" section, which is not stale.
8. **Do not touch the ADRs themselves.** Their "Accepted, implemented" status and content are
   accurate for what they describe; the gap is that *nothing newer* documents what changed since —
   addressed by recommendation 1, not by editing 0001–0003.

Nothing above was executed in this phase — these are recommendations for a future phase/founder
decision, consistent with "no merging, analysis and classification only."

---

## 5. Proposed unified documentation hierarchy

```
Tier 0 — Ground truth (not a document)
    command_center/models.py, command_center/*, tests/*
    → arbiter of any conflict about "what currently exists"

Tier 1 — Canonical architecture record (SoT-Current, immutable once written)
    ARCHITECTURE.md (root), docs/adr/000N-*.md, WORKSPACE_HOME_ARCHITECTURE.md
    → new decisions get a new ADR (e.g. 0004 for the registry expansion), never an edit to an old one

Tier 2 — Product-facing summary (Derived from Tier 1, must be kept current)
    README.md, CHANGELOG.md, RELEASE_NOTES_*.md
    → highest priority to refresh; currently the stalest tier (Conflict 3)

Tier 3 — Approved target architecture, not yet built (SoT-Target)
    docs/desktop/* (all 10 files)
    → binding decisions are real and current; periodically re-check against Tier 0, since "not yet
      built" docs silently drift when Tier 0 moves (this is exactly how Conflict 1 happened)

Tier 4 — Assessment / audit (Historical, always dated, always paired with a status tracker)
    docs/audits/FOUNDER_FUNCTIONAL_AUDIT_9761459.md + its _STATUS.md companion
    → never read alone; the _STATUS.md is the current-truth layer on top of the frozen audit

Tier 5 — Candidate backlog / draft (Draft, requires triage before becoming roadmap)
    docs/audits/FOUNDER_FUNCTIONAL_AUDIT_TASKS_9761459.json
    ADR 0001's "Tier B" section
    roadmap/program/* (pending the provenance/naming decision in recommendation 6)

Tier 6 — Historical / reference, out of AICC's own roadmap scope
    CURRENT_STATE.md (per-project section), context/*, projects/AIOS.md, old RELEASE_NOTES

Tier 7 — Vacant placeholders (Unknown, either populate or prune — not this phase's decision)
    DECISIONS.md, INBOX.md, prompts/*.md, templates/*.md, projects/{BANK_STRATEGY,BUSINESS,LEGAL,PERSONAL}.md
```

A `MASTER_PRODUCT_ROADMAP.md` built in a later phase should draw **only** from Tiers 0–3 for "what
exists / what's approved," and from Tier 5 (post-triage) for "what's next" — Tiers 4 and 6 are
context, not roadmap inputs, and Tier 7 contributes nothing until populated.

---

## 6. Final report

| Metric | Count | Detail |
|---|---|---|
| **Source of Truth documents (SoT-Current + SoT-Target combined)** | **20** | SoT-Current: `README.md` (setup portion), `ARCHITECTURE.md` root, `WORKSPACE_HOME_ARCHITECTURE.md`, `CURRENT_STATE.md` (operating-rules portion), ADR 0001/0002/0003 (decision sections), `FOUNDER_FUNCTIONAL_AUDIT_9761459_STATUS.md` = 8. SoT-Target: `docs/desktop/README.md`, `PRODUCT_VISION.md`, `ARCHITECTURE.md`, `INFORMATION_ARCHITECTURE.md`, `DESIGN_DIRECTIONS.md`, `DESIGN_SYSTEM.md`, `WORKSPACE_HOME_SPEC.md`, `PLATFORM_BEHAVIOR.md`, `DESKTOP_INCREMENT_1.md`, `IMPLEMENTATION_ROADMAP.md`, ADR 0001 Decision section (counted once, not twice) = 12 |
| **Historical documents** | **9** | `FOUNDER_FUNCTIONAL_AUDIT_9761459.md`, `CURRENT_STATE.md` (per-project portion), `RELEASE_NOTES_v1.1.md`, `RELEASE_NOTES_v1.2.md`, `README.md` (feature-list portion), `WORKSPACE_HOME_ARCHITECTURE.md` (review-narrative portion), `projects/AIOS.md`, `context/AIOS_CONTEXT.md`, `context/BANK_CONTEXT.md` + `context/LEGAL_CONTEXT.md` (counted as one AIOS/sensitive-context group in the 9, see §1.5) |
| **Deprecated documents** | **0** | Nothing in this repository is explicitly marked superseded; note this as a gap in itself — the audit's own `KEEP`/`IMPROVE`/`REPLACE`/`REMOVE` code-classification table (§12 of the audit) is the closest thing to a deprecation signal, and it targets *code modules*, not documents |
| **Draft / Unknown-provenance documents** | **7** | `FOUNDER_FUNCTIONAL_AUDIT_TASKS_9761459.json`, ADR 0001's Tier B section, `roadmap/program/README.md`, `PROGRAM_ROADMAP.md`, `program_roadmap.json` (Draft + Unknown provenance), `import_program_roadmap.py`, `ready_tasks.py` (tooling, Deprecated-on-arrival relative to the canonical pipeline) |
| **Conflicts identified** | **6** | Ranked HIGH → MEDIUM in §2; two (Conflict 1, Conflict 2) are HIGH severity and Conflict 2 is an **active, present-tense** data-integrity issue, not merely a documentation mismatch |
| **Documents requiring update** | **7** | `README.md`, `CHANGELOG.md`, `docs/desktop/PRODUCT_VISION.md`, `docs/desktop/WORKSPACE_HOME_SPEC.md` (registry language), `FOUNDER_FUNCTIONAL_AUDIT_9761459_STATUS.md` (finding-by-finding PR #9 mapping), plus **one new document recommended** (ADR 0004) — not an update to an existing one, but required to close Conflict 1/3 at the architecture-record layer |
| **Documents recommended for archival, once the above updates land** | **0 immediately** | No document is safe to archive yet — even `FOUNDER_FUNCTIONAL_AUDIT_9761459.md` must stay live until its STATUS doc's triage (Conflict 4) completes; premature archival would delete evidence the triage needs |

### Recommended canonical input set for `MASTER_PRODUCT_ROADMAP.md` (Phase 3)

1. `command_center/models.py` and adjacent code (ground truth for current registry/module surface)
2. `ARCHITECTURE.md` (root) + ADR 0001/0002/0003 (+ the recommended new ADR 0004, once written)
3. `WORKSPACE_HOME_ARCHITECTURE.md`
4. `CHANGELOG.md`, once refreshed per recommendation 2
5. `docs/desktop/*` (all 10 files) — the approved, unbuilt desktop track
6. `FOUNDER_FUNCTIONAL_AUDIT_9761459.md` + `_STATUS.md`, read together, never the audit alone
7. `FOUNDER_FUNCTIONAL_AUDIT_TASKS_9761459.json`, **after** triage (Done/Still Open/Superseded/Duplicate)
8. ADR 0001's Tier B list, folded into the same triage pass as #7
9. `roadmap/program/program_roadmap.json`, **only if and after** the founder confirms its provenance
   and its project-naming scheme is reconciled against the canonical registry (recommendation 6) —
   conditional inclusion, not automatic

`CURRENT_STATE.md`'s per-project section, `context/*`, and `projects/AIOS.md` are recommended
**exclusions** from the Master Product Roadmap input set — they describe a different project's
roadmap (AIOS) or are stale snapshots superseded by #6 above.
