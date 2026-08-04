# ADR 0009 — The canonical project registry and the validating task-import pipeline

Status: **Accepted, implemented.** Both halves of this record describe code that is already on
`main`: the nine-entry `models.PROJECT_IDS` registry (expanded in PR #9, commit `98d7714`) and the
`command_center/task_import.py` / `scripts/import_tasks.py` import pipeline that validates every
project value against it. This ADR is written **after** the fact, to give the registry an
architecture-tier record it has never had.

## Context

The project registry is the most load-bearing vocabulary in this application. Every Kanban lane,
per-project metric, recommendation input, project-config entry, redaction decision, and imported
task is keyed on it. It has nevertheless been governed only by a hardcoded list in `models.py` and
by whatever a given document happened to say about it, and those two drifted apart.

Three concrete failures followed from having no architecture record:

1. **A stale ADR became a competing source of truth.** ADR 0002's "Known limitation" section states
   that `models.PROJECT_IDS` is a fixed list of six ids (`AIOS`, `AICOS`, `BANK`, `LEGAL`,
   `BUSINESS`, `PERSONAL`). That was true when it was written and has been false since `98d7714`
   added `AICC`, `PRODUCT`, and `ECOSYSTEM`. Under the authority hierarchy the ADR tier is immutable
   once written, so the sentence cannot be edited — it can only be **superseded by a newer ADR**,
   which did not exist.
2. **Registry facts were only recorded in tiers below the architecture record.** The nine-id
   registry is stated correctly in `DR-ROADMAP-AUTHORITY-001` §4 (a decision record, tier 3) and
   analysed in `ROADMAP_RECONCILIATION.md` Conflict 1, but nothing in tier 2 carried it. That is
   backwards: the registry is an architectural constant, not a program-governance choice, and a
   decision record is the wrong place for it to live permanently.
3. **The alias table's folding rule was undocumented, and the pressure to widen it was real.** The
   `roadmap/program/` package uses `AI_COMMAND_CENTER` and `AIOS_PRODUCT`. Neither resolves —
   `normalize_project_id` folds case and whitespace but not underscores — and 26 of the live tasks
   in `data/tasks.json` carry project values that do not resolve against the registry as a result.
   The cheap fix (add two underscore aliases and re-run the importer) was available to anyone who
   did not know why the folding rule is narrow.

`ROADMAP_RECONCILIATION.md` §4.1 recommended this ADR as the *first* unification step. ADR 0004 and
0005 were then written for other topics (autonomy), so the recommended registry ADR was never
written; `DR-ROADMAP-AUTHORITY-001` §8 F3 records that gap, and its §2.4 documentation-truth gate
cannot close until this document exists. This is that document.

## Decision

### 1. `models.PROJECT_IDS` is the single canonical registry

`command_center/models.py` holds the registry, and it is runtime ground truth. Any document that
disagrees with it is stale and must be corrected to match the code — never the reverse.

| Canonical id | Display name (`project_config.DISPLAY_NAMES`) | Sensitive |
|---|---|---|
| `AICC` | AI Command Center | |
| `AIOS` | AIOS | |
| `AICOS` | AICOS | |
| `PRODUCT` | AIOS Product | |
| `ECOSYSTEM` | Ecosystem | |
| `BANK` | Bank Strategy | **yes** |
| `LEGAL` | Legal | **yes** |
| `BUSINESS` | Business | |
| `PERSONAL` | Personal | |

Nine ids. `SENSITIVE_PROJECT_IDS = {BANK, LEGAL}` is the redaction subset, read through
`project_config.is_sensitive`; it is a property *of the registry*, not a separate list to be
maintained alongside it, and every surface that redacts (Workspace Home, reports, artifacts) must
derive from it rather than hardcoding two ids.

`AICC`, `PRODUCT`, and `ECOSYSTEM` are the three ids added in `98d7714`, and they are the reason the
six-id language in ADR 0002's "Known limitation", `docs/desktop/PRODUCT_VISION.md`, and
`WORKSPACE_HOME_SPEC.md` is wrong. **This ADR supersedes that language wherever it appears.** Per
the authority hierarchy, ADR 0002 itself is not edited.

Three ids are worth spelling out because they are the ones people conflate:

- `AICC` is *this* product — the control plane in this repository.
- `AICOS` is a **separate product** that AI Command Center tracks. AI Command Center is not AICOS.
- `PRODUCT` ("AIOS Product") is the product track for AIOS, distinct from `AIOS` itself.

Collapsing any of these into another is the exact failure the alias invariant below prevents.

### 2. `PROJECT_NAME_ALIASES` folds case and whitespace only — not underscores

`project_config.PROJECT_NAME_ALIASES` maps the free-text names a task package is allowed to use onto
canonical ids. Two properties are decided here.

**It is derived, not hand-maintained.** `_build_project_name_aliases` registers, for every entry in
`PROJECT_IDS`, both its `DISPLAY_NAMES` value and its bare id, then merges the (currently empty)
`_EXPLICIT_PROJECT_ALIASES` dict. Deriving it means the table cannot drift out of sync with the
display names the UI renders, and — the invariant Founder Review established during AICC-AUDIT-001
remediation — **no two distinct entities can fold into one id**, by construction: each display name
maps to its own id and to nothing else. `"AI Command Center"` cannot collapse into `AICOS`;
`"AIOS Product"` cannot collapse into `AIOS`.

**The folding rule is exactly one normalization.** `_alias_key` lowercases, strips, and collapses
internal whitespace runs — and does nothing else. The same function is applied to both the table's
keys and every lookup, so the two can never disagree about how a name is matched. Underscores,
hyphens, and punctuation are **not** folded. Concretely:

| Input | Resolves to |
|---|---|
| `"AICC"`, `"aicc"` | `AICC` |
| `"AI Command Center"`, `"ai   command   center  "` | `AICC` |
| `"AIOS Product"` | `PRODUCT` |
| `"AI_COMMAND_CENTER"` | `None` |
| `"AIOS_PRODUCT"` | `None` |
| `"PORTFOLIO"`, `"PLATFORM"` | `None` — no canonical home at all |

The narrowness is the point. Case and whitespace differences are *typography* — the same name typed
differently. An underscore-delimited token is a **different naming scheme**, and folding it would
mean silently accepting an unreviewed external taxonomy into the canonical vocabulary. That is what
`roadmap/program/` proposes, and it is declined.

Note that `models.PROJECT_ALIASES` is a **different table with a different job**: it maps
non-canonical labels that historically reached `data/tasks.json` from outside the app onto canonical
ids, so `tasks_repository.validate_tasks` can say "did you mean…?" and
`reconcile_project_aliases` can migrate the live store. It is a *repair* table for existing bad
data. `PROJECT_NAME_ALIASES` is an *admission* table for new input. Neither is a fallback for the
other.

### 3. `normalize_project_id` fails to `None` — it never guesses

```
normalize_project_id(raw) -> canonical id | None
```

Empty/blank input returns `None`. An already-canonical id passes through unchanged. Everything else
is looked up in `PROJECT_NAME_ALIASES` under `_alias_key`, and **a miss returns `None`**.

There is deliberately no default project. A guessed project is worse than a rejected import: it
produces a task that looks correctly filed, sits in the wrong lane, distorts that project's counts
and recommendations, and — if the guess landed on `BANK` or `LEGAL` — silently changes which
redaction rules apply to it. A `None` is loud and stops at the boundary;
`task_import.validate_task_package` turns it into a per-task error (`Неизвестный проект: …`), and
because `apply_task_package` refuses to run at all while `validation.errors` is non-empty, one
unresolvable project value rejects the **whole package** rather than importing it partially.

The one intentional relaxation is `canonical_project_id`, which is `normalize_project_id` with a
raw-value fallback (`normalize_project_id(value) or value`). It exists for *comparison* sites — a
task whose stored project is `"AI Command Center"` must still match the selector's `"AICC"` — and an
unknown value falling back to itself still matches only itself exactly, so it is never broadened
onto a real lane. Fail-to-`None` governs the **write** path; fallback-to-self governs the **read**
path. These are different problems and correctly have different answers.

### 4. The task-import pipeline is the only validating write path for external tasks

`command_center/task_import.py` is a four-stage pipeline — parse → validate → preview → apply —
where every stage before the last is pure and touches no disk:

- `parse_task_package` accepts JSON, YAML, Markdown (front-matter or first fenced block), and
  sniffed plain text, all converging on the same `ParsedPackage`, so validation and apply are
  format-agnostic. Input is capped at `MAX_PACKAGE_BYTES` (5 MiB) *before* any parser sees it, and
  YAML goes through `yaml.safe_load` only.
- `validate_task_package` checks required fields and vocabularies (`KANBAN_STATUSES`,
  `TASK_PRIORITIES`) and normalizes every `project` through `normalize_project_id` per §3.
- `build_import_preview` reads the store once, read-only, to classify each task as new or duplicate.
- `apply_task_package` runs the entire load → re-derive → merge → save cycle inside
  `tasks_repository.mutate_tasks`, holding **the same shared `tasks.lock`** every other task-writing
  path in the application holds. It never trusts a caller-held preview; a store mutated in between
  is seen fresh.

Two consequences are load-bearing and are decided here rather than left as implementation detail:

**One store, one lock.** The importer is not a second task store and must never become one. It
writes through `tasks_repository` exactly as the Kanban board and the Create Task page do, so a
package import racing a manual edit cannot lose an update.

**All-or-nothing.** A package with any blocking error is rejected in full. There is no partial
import, which is what makes "an unresolvable project value" a safe failure rather than a
half-migrated store.

`app.py`'s Create Task page and `scripts/import_tasks.py` are thin callers of these four functions;
the CLI requires exactly one of `--dry-run` / `--apply`, so a bare invocation can never write.

### 5. Changing the registry requires an ADR, never a script workaround

Adding, removing, or renaming an entry in `PROJECT_IDS`, changing `SENSITIVE_PROJECT_IDS`, or
widening `PROJECT_NAME_ALIASES` (including adding `_EXPLICIT_PROJECT_ALIASES` entries or loosening
`_alias_key`) is a **founder decision recorded in a new ADR**.

Specifically rejected: adding underscore aliases, or any other alias, for the purpose of making a
particular unreviewed package import cleanly. That inverts the direction of authority — it lets an
external document edit the canonical vocabulary by being inconvenient. The 26 live tasks in
`data/tasks.json` whose project values do not resolve are a **data** problem, to be fixed by
remapping and re-importing through `scripts/import_tasks.py --dry-run` first, not a signal that the
registry is too narrow.

This mirrors `DR-ROADMAP-AUTHORITY-001` §4 and is restated here so the rule lives in the
architecture tier, where the registry itself lives, rather than only in a decision record.

## Consequences

- The registry now has a tier-2 record. ADR 0002's six-id "Known limitation" is superseded by this
  document; ADR 0002 is not edited, per the authority hierarchy.
- `DR-ROADMAP-AUTHORITY-001` §8 **F3 is closed** by this ADR. §2.4's documentation-truth gate still
  needs F1 (`README.md` / `CHANGELOG.md` refresh) to close fully.
- Reconciliation Conflict 1 ("the canonical project registry has three incompatible versions in
  play") is resolved at the architecture-record layer. The remaining versions are stale documents,
  each now correctable against a single citable record.
- `docs/desktop/PRODUCT_VISION.md` §1/§5 and `WORKSPACE_HOME_SPEC.md` still say "six projects"
  (`DR-ROADMAP-AUTHORITY-001` §8 F5). They are tier-5 documents obligated to track tier 2; this ADR
  is what they must now be corrected against.
- The `roadmap/program/` package's `AI_COMMAND_CENTER` / `AIOS_PRODUCT` / `PORTFOLIO` / `PLATFORM`
  taxonomy remains non-importable. `PORTFOLIO` and `PLATFORM` have no canonical home at all; the
  other two must be remapped to `AICC` / `PRODUCT` before any re-import.
- Registry changes get slower on purpose. Nine ids for a single-user control plane is a vocabulary,
  not a database table, and the cost of a wrong entry (mis-filed tasks, wrong redaction) is paid
  continuously by every project-scoped surface in the app.

## Non-goals

- **Making the registry user-editable / persisted.** ADR 0002 identified this as a materially larger
  change touching every module that iterates `PROJECT_IDS`. This ADR records the registry as it is;
  it does not add a project-creation path, and does not decide whether one should ever exist.
- **Resolving the 26 non-resolving live task records.** That is `DR-ROADMAP-AUTHORITY-001` §8 F2 — a
  data decision requiring founder input on each record, not a documentation change.
- **Approving or rejecting `roadmap/program/`'s content.** Only its project taxonomy is ruled on
  here; its disposition as roadmap material is settled in `DR-ROADMAP-AUTHORITY-001` §7.
- **Adding a UI for sensitivity.** `SENSITIVE_PROJECT_IDS` is recorded as a registry property; how
  each surface renders redaction is owned by those surfaces and by the Desktop `PRODUCT_VISION.md`
  §8 guarantees.

## References

- `command_center/models.py` — `PROJECT_IDS`, `SENSITIVE_PROJECT_IDS`, `PROJECT_ALIASES`
- `command_center/project_config.py` — `DISPLAY_NAMES`, `_alias_key`, `_build_project_name_aliases`,
  `PROJECT_NAME_ALIASES`, `normalize_project_id`, `canonical_project_id`, `project_matches`,
  `is_sensitive`
- `command_center/task_import.py`, `scripts/import_tasks.py` — the validating import pipeline
- `tests/test_project_canonicalization.py`, `tests/test_project_config.py` — the executable form of
  the rules above
- [`DECISIONS.md`](../../DECISIONS.md) → `DR-ROADMAP-AUTHORITY-001` §4 (canonical project-id
  mapping), §5 (authority hierarchy), §8 F3
- `docs/roadmap/ROADMAP_RECONCILIATION.md` — Conflict 1, recommendation §4.1
- ADR 0002 — superseded on registry size only (see Decision §1)
