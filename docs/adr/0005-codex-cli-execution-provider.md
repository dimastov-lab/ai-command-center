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
environment construction, prompt transport, output parsing, failure
classification, readiness/cancellation descriptions, and redacted audit
metadata. `Supervisor` remains the only process/state owner.

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
checkout, and is on the explicit expected task branch. Existing atomic runtime
workspace locking prevents duplicate active launches.

Migration 6 adds `run.provider_id` and `run.provider_metadata_json`. Codex run
rows persist a redacted prompt marker; deterministic metadata stores only the
provider/version, sandbox/readiness/cancellation modes, stdin transport,
prompt byte count and SHA-256, and environment key names. Environment values,
credentials, and full prompts are excluded. Codex stderr is token-redacted
before persistence.

Provider failures retain the existing `FAILED` state and use actionable
`failure_reason` codes: `executable_missing`, `provider_launch_failed`,
`provider_exit_nonzero`, `authentication_failed`, `quota_limit`, and the
existing `timeout`/blocked/incomplete reasons. No competing state vocabulary
is introduced.

Codex cancellation verifies the live process against its recorded launch identity
immediately before signaling the process group. Unverifiable identity fails
closed, preventing a reused PID from targeting an unrelated process.

## Consequences

- Claude continues through its historical command builder and stream parser.
- Codex participates in the same handshake, streaming, cancellation, timeout,
  report, duplicate-lock, and reconciliation paths.
- The existing UI selector exposes Claude Code and Codex CLI with availability;
  the ad-hoc canonical-checkout form refuses Codex and directs users to a task
  worktree launch.
- Tests use only `tests/fixtures/fake_codex.py`; availability probes and runs
  are pointed at that executable, so tests never invoke real Codex or consume
  credits.

## Limitations

- Codex session resume is intentionally unsupported.
- Codex can launch only from a task-associated dedicated worktree with an
  explicit expected branch; canonical/ad-hoc execution is intentionally
  unavailable.
- Authentication is not proactively tested because doing so could contact a
  provider. Authentication/quota failures are classified from launch output.
