# ADR 0005 — Codex CLI Execution Provider Foundation

Status: **Accepted, implemented.**

## Context

The v2 Session Supervisor already owns the authoritative subprocess lifecycle:
workspace locking, PREPARED/QUEUED/RUNNING/terminal transitions, startup
handshake, PID identity, streaming persistence, cancellation, timeout, and
restart reconciliation. Adding Codex through another launcher would duplicate
and weaken those guarantees. Codex also differs materially from Claude: its
non-interactive interface is `codex exec`, it accepts the prompt on stdin, and
its JSONL event vocabulary is provider-specific.

## Decision

Introduce `command_center.runtime.providers`, whose contract owns only
provider-specific executable discovery/version/interface probing, fixed argv,
environment construction, prompt transport, a per-run stream sanitizer and
event normalizer, bounded provider-error classification, explicit
readiness/result semantics, cancellation requirements, and audit metadata.
`Supervisor` remains the only process/state owner.

Codex uses the locally verified interface:

```
codex exec --json --color never --sandbox <mode> --cd <worktree> -
```

The prompt is written to stdin and never appears in argv. Implementation tasks
use `workspace-write`; review/final-gate/architecture-review use `read-only`.
The provider does not use a shell, cloud delegation, automatic fallback, or
resume in this increment.

Codex launch is fail-closed unless the target is a registered worktree of the
project's configured canonical repository, differs from the canonical
checkout, is supplied through a non-symlinked path, is clean, and is on the
explicit expected task branch. Existing atomic runtime workspace locking
prevents duplicate active launches. A project must also explicitly include
`codex` in `allowed_agents`; missing policy retains the legacy Claude-only
default and malformed policy disables execution.

Migration 6 adds `run.provider_id` and `run.provider_metadata_json`. Codex run
rows persist a redacted prompt marker; deterministic metadata stores only the
provider/version, sandbox/readiness/result/cancellation modes, stdin transport,
prompt byte count and SHA-256. It records neither environment keys nor values.
The per-run pre-persistence boundary retains bounded stream carry-over and
redacts prompt echoes, prompt fragments of eight or more characters,
JSON-escaped prompt text, and common credential forms from structured stdout,
malformed stdout, unknown events, and stderr. Codex prompts over 100,000
characters fail before task/session/run persistence so redaction state remains
bounded. Reports consume only the already-sanitized event stream.

Codex readiness requires a recognized `thread.started` or `turn.started`
lifecycle event; stderr, malformed JSON, warnings, and unknown events do not
set the handshake timestamp. Successful completion additionally requires a
normalized non-empty agent message followed by `turn.completed`. Exit code
zero without both forms of evidence is recorded as incomplete/failed, never
as success. Recognized agent messages and completion events are normalized to
the provider-neutral assistant/result event shapes already consumed by reports
and task projection.

Provider failures retain the existing `FAILED` state and use actionable
`failure_reason` codes: `executable_missing`, `provider_launch_failed`,
`provider_exit_nonzero`, `authentication_failed`, `quota_limit`, and the
existing `timeout`/blocked/incomplete reasons. Authentication/quota detection
uses only sanitized stderr and normalized structured provider-error events,
with explicit event-count and byte bounds; ordinary assistant/result/task text
is excluded. No competing state vocabulary is introduced.

Codex must obtain a stable start-time/command identity before transitioning to
`RUNNING`; failure terminates the owned spawn and records a terminal launch
failure. Cancellation and timeout verify the live process against that
recorded identity immediately before signaling its process group. A mismatch
is never signaled, preventing a reused PID from targeting an unrelated
process.

## Consequences

- Claude continues through its historical command builder and stream parser.
- Codex participates in the same Supervisor-owned streaming, cancellation,
  timeout, report, duplicate-lock, and reconciliation paths, while the
  provider runtime supplies its stricter handshake/result semantics.
- The existing UI selector derives choices from the same project authorization
  resolver as the service boundary. It exposes Codex only after explicit
  project opt-in and shows availability; the ad-hoc canonical-checkout form
  refuses Codex and directs users to a task worktree launch.
- Tests use only `tests/fixtures/fake_codex.py`; availability probes and runs
  are pointed at that executable, so tests never invoke real Codex or consume
  credits.

## Limitations

- Codex session resume is intentionally unsupported.
- Prompts longer than 100,000 characters are refused for this provider
  increment.
- Codex can launch only from a task-associated dedicated worktree with an
  explicit expected branch; canonical/ad-hoc execution is intentionally
  unavailable.
- Authentication is not proactively tested because doing so could contact a
  provider. Authentication/quota failures are classified from bounded,
  sanitized provider-authored error evidence after launch.
- After a Supervisor restart, live children can be identity-reconciled but
  their inherited pipes and waitable-child handles cannot be reattached.
  Reconciliation therefore never fabricates completion; unverifiable or ended
  orphans become the existing conservative recovery states.
