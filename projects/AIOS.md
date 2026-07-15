AIOS — Project Control Card

Updated: 2026-07-15
Status: Active
Priority: Critical

1. Project Objective

Build AIOS as an open, production-grade AI operating platform with enterprise capabilities and a commercial product layer.

The platform must support:

* reliable memory and state management;
* API and SDK access;
* authentication and authorization;
* developer tooling;
* enterprise security and operations;
* commercial packaging;
* AML and compliance-oriented product scenarios.

⸻

2. Operating Model

AIOS work is divided into separate streams:

1. Core platform development
2. Architecture and specifications
3. Product and commercial model
4. Security and enterprise readiness
5. Documentation and developer experience
6. AML and compliance product scenarios

Streams may progress in parallel only when they do not modify overlapping files or unresolved shared contracts.

⸻

3. Mandatory Working Rules

1. One agent = one task = one worktree = one branch.
2. An agent may modify only explicitly permitted files.
3. No push, merge or release without explicit approval.
4. Implementation and review must be performed by different agents.
5. Every task must define measurable acceptance criteria.
6. Tests must not be weakened to make implementation pass.
7. Architectural decisions already accepted must not be reopened without new evidence.
8. Generated artifacts must be reproducible and checked for drift.
9. Documentation must reflect actual runtime behavior.
10. Agent reports must include exact commands and results.
11. API and UI must not be started unless the current phase explicitly permits it.
12. Confidential employer information must not be used in product development.

⸻

4. Current Program Status

P0 — Reliability and Memory Foundation

Status: COMPLETE

Completed areas:

* memory write transaction model;
* crash-recoverable JSON writes;
* path traversal remediation;
* atomic and idempotent object creation;
* object/event atomicity;
* memory reconciliation;
* reconciliation operator CLI;
* reconciliation report schema;
* legacy transaction recovery;
* guarded repair behavior;
* documentation and release records.

Relevant completed architecture:

* RFC-0005
* ADR-0011
* RFC-0006
* ADR-0012

Quality status previously reported:

* full test suite passed;
* Ruff passed;
* mypy passed;
* reconciliation safety tests passed.

P0 must not be revisited unless a confirmed regression or architectural defect is found.

⸻

P1 — API, Authentication and SDK

Status: IN PROGRESS

Completed or substantially completed:

* API shell;
* authentication integration;
* OpenAPI contract foundation;
* independent runtime Pydantic schemas;
* removal of canonical-schema injection into runtime OpenAPI;
* runtime OpenAPI aligned with actual FastAPI and Pydantic models.

Known approved work:

* correction of runtime OpenAPI architecture was approved for commit.

Current uncertainty:

* exact repository HEAD and branch state must be verified before selecting the next task;
* uncommitted approved changes may still exist;
* SDK architecture review findings may remain unresolved.

Next program action:

1. Inspect the relevant P1 repositories.
2. Identify uncommitted or unmerged approved work.
3. Establish the exact current baseline.
4. Select only one next critical-path task.
5. Issue a bounded implementation or review prompt.

⸻

5. Specifications Program

Repository: aios-specs

Status: Active

Completed candidate waves:

* Wave 2 — Platform Architecture
* Wave 3 — Memory and Intelligence
* Wave 4 — Developer Platform
* Wave 5 — Enterprise Security and Operations

Known candidate tag:

* specs-v0.6.0-candidate

Wave 6 work includes:

* COM-002 licensing model;
* COM-003 commercial feature model;
* related ADRs, requirements, traceability matrices and validation tooling.

Functional architecture work includes:

* onboarding;
* KYC;
* CDD;
* EDD;
* customer risk;
* user journeys and related functional specifications.

Operating requirement:

Specifications must remain traceable to runtime architecture, product requirements and commercial capabilities.

⸻

6. Product and Commercial Stream

Status: Active

Product ambition:

* global open-source platform;
* commercial enterprise offering;
* strong developer experience;
* banking, AML and compliance specialization;
* clear separation between open-core and commercial capabilities.

Existing or planned artifacts:

* Product Vision
* Product Positioning
* commercial feature model
* licensing model
* pricing model
* packaging
* website
* documentation
* sales kit
* launch kit
* design-partner program

Product work must not invent capabilities that do not exist in architecture or code.

⸻

7. Current Priorities

Priority 1:

Establish the exact current state of all active AIOS repositories and branches.

Priority 2:

Close approved but uncommitted P1 work.

Priority 3:

Resolve any blocking architecture-review findings before continuing implementation.

Priority 4:

Define the next P1 critical-path sequence.

Priority 5:

Keep specifications, runtime implementation and product claims synchronized.

⸻

8. Critical Path

The default critical path is:

1. Verify repository state.
2. Close pending approved changes.
3. Perform independent review.
4. Remediate confirmed blockers.
5. Run full quality gate.
6. Commit.
7. Merge only after explicit approval.
8. Update this project card.
9. Select the next task.

No new major AIOS phase should begin while the current critical-path item remains unresolved.

⸻

9. Definition of Done for Technical Tasks

A technical task is complete only when:

* objective behavior is implemented;
* only permitted files were modified;
* required tests were added;
* existing tests still pass;
* Ruff passes;
* mypy passes;
* schema or contract validation passes where applicable;
* generated-artifact drift checks pass;
* documentation matches runtime behavior;
* git diff was reviewed;
* git status is reported;
* an independent reviewer returns APPROVED;
* remaining risks are explicitly documented.

⸻

10. Required Agent Report Format

Every implementation or remediation agent must return:

1. Result
2. Root cause
3. Files changed
4. Behavioral changes
5. Architectural decisions
6. Tests added or changed
7. Exact validation commands
8. Exact validation results
9. Remaining risks
10. Git diff summary
11. Git status
12. Recommendation

Every review agent must return:

1. Verdict: APPROVED or NOT APPROVED
2. Scope reviewed
3. Confirmed findings
4. File and line references
5. Test coverage assessment
6. Security assessment
7. Contract and compatibility assessment
8. Required remediation
9. Residual risks

⸻

11. Prohibited Actions

Unless explicitly authorized, an agent must not:

* push;
* merge;
* rebase shared branches;
* delete branches or worktrees;
* modify unrelated files;
* redesign accepted architecture;
* weaken tests;
* suppress errors without justification;
* introduce backward incompatibility;
* expose credentials or raw sensitive payloads;
* claim tests passed without executing them;
* edit release documentation before implementation is verified;
* launch API or UI services.

⸻

12. Current Risks

1. Repository status may differ from the status recorded in conversations.
2. Approved changes may remain uncommitted.
3. Different AIOS repositories may contain overlapping or inconsistent contracts.
4. Product and specification work may advance ahead of runtime implementation.
5. Long agent sessions may accidentally reopen resolved architecture.
6. SDK generation may depend on unstable source ordering.
7. Exception chaining may expose credentials or untrusted response data.
8. Documentation may describe intended rather than actual behavior.

⸻

13. Decisions Required From the Owner

Current owner decisions should be limited to:

* approval or rejection of architectural choices;
* priority ordering;
* acceptance of residual risks;
* authorization to commit;
* authorization to merge;
* release readiness;
* product positioning;
* commercial model;
* use of banking and AML scenarios.

Agents should resolve routine implementation details without repeatedly escalating them.

⸻

14. Next Action

Run a read-only AIOS repository inventory.

The inventory must identify:

* repository path;
* current branch;
* HEAD commit;
* clean or dirty working tree;
* untracked files;
* uncommitted changes;
* active worktrees;
* branches ahead or behind main;
* approved work not yet committed;
* likely next critical-path task.

The inventory must not modify files, commit, push, merge, stash or reset anything.