# AIOS — Context Pack

Updated: 2026-07-15
Status: Active
Current phase: P1

## Project

AIOS is an open, production-grade AI operating platform with an enterprise and commercial product layer.

Primary product direction:

- open-source core;
- enterprise capabilities;
- developer platform;
- banking, AML and compliance scenarios;
- commercial packaging.

## Completed

### P0

P0 is complete.

Completed capabilities include:

- memory write transaction model;
- crash-recoverable JSON writes;
- atomic and idempotent object creation;
- object/event atomicity;
- reconciliation operator CLI;
- guarded repair;
- legacy transaction recovery;
- reconciliation documentation and release artifacts.

P0 must not be reopened unless a confirmed regression or architectural defect is found.

### P1

Completed or substantially completed:

- API shell;
- authentication integration;
- OpenAPI contract foundation;
- independent runtime Pydantic models;
- removal of canonical-schema injection into runtime OpenAPI;
- alignment of runtime OpenAPI with FastAPI and Pydantic behavior.

## Current State

The exact current repository, branch and working-tree state must be verified before starting new implementation.

Possible pending items:

- approved but uncommitted P1 changes;
- SDK review findings;
- incomplete build or packaging regression coverage;
- branches or worktrees not yet merged.

Repository state is more authoritative than conversation history.

## Current Objective

Establish the exact P1 baseline and complete the next critical-path task without reopening completed architecture.

## Mandatory Rules

1. One agent = one task = one branch = one worktree.
2. Modify only explicitly permitted files.
3. Do not push, merge, reset, stash or rebase unless explicitly authorized.
4. Implementation and independent review must be performed separately.
5. Do not weaken tests.
6. Report exact commands and exact results.
7. Do not claim tests passed unless they were executed.
8. Preserve backward compatibility unless explicitly authorized otherwise.
9. Runtime behavior is the source of truth for runtime contracts.
10. Generated artifacts must be reproducible.
11. Do not expose credentials, raw transport objects or unsafe exception payloads.
12. Do not modify this Context Pack unless explicitly instructed.

## Default Technical Quality Gate

Where applicable, verify:

- targeted tests;
- full pytest suite;
- Ruff;
- mypy;
- schema validation;
- generated-artifact drift;
- package build;
- wheel and sdist behavior;
- git diff;
- git status.

## Required Agent Output

Return:

1. Result
2. Root cause
3. Files changed
4. Behavioral changes
5. Tests added or changed
6. Exact commands executed
7. Exact results
8. Remaining risks
9. Git diff summary
10. Git status
11. Recommendation: APPROVE or NOT APPROVED

## Next Action

Run a read-only repository inventory before selecting the next implementation task.

The inventory must identify:

- repository path;
- current branch;
- HEAD;
- working-tree status;
- untracked files;
- active worktrees;
- branches ahead or behind main;
- approved work not yet committed;
- likely next critical-path task.
