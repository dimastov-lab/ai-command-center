# Daily self-audit

The headless daily self-audit is an opt-in product and engineering campaign.
It is not a timer around `pytest`: every campaign must exercise the user path
from task creation/import through execution, remediation, review, CI, merge,
target-branch verification and final task projection.

## Mandatory coverage

- implementation, architecture, security, reliability and concurrency;
- real UI/user journey, usability, accessibility and recovery feedback;
- failure paths including invalid/dirty workspaces, concurrency, timeout,
  malformed output, failed checks, network errors, review rejection, conflicts
  and process restart;
- automated remediation with regression coverage and independent re-review;
- queue waves, dependencies, capacity, fairness and isolated failures;
- evidence-based task reprioritization;
- local quality gates, GitHub checks/review/mergeability and verified target
  branch state.

A campaign is never successful merely because an agent process exits zero or a
PR is merged. The persisted result is `completed` only after the target branch
is verified. Failures remain visible as `failed` or `requires_attention`.

## Enabling

The service is off by default. A persistent host must set:

```text
AICC_DAILY_AUDIT_ENABLED=1
AICC_COMPLETION_AUTOPILOT=1
AICC_DATA_DIR=/absolute/path/to/the/app/data
```

Run one scheduler tick:

```text
python scripts/daily_audit_daemon.py --once
```

Inspect persisted scheduling state:

```text
python scripts/daily_audit_daemon.py --status
```

`deploy/com.ai-command-center.daily-audit.plist` is a launchd template. Replace
`__ROOT__`, `__PYTHON__`, `__PATH__` and `__DATA_DIR__` with absolute paths
before installing it. `AICC_DATA_DIR` must be the same directory used by the
Streamlit application; otherwise campaigns run correctly but cannot appear in
the application UI. The
process is kept alive, while the SQLite due time and lease ensure that only one
campaign is dispatched per day and that another host cannot duplicate it.
