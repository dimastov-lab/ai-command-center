"""Run the debug CLI with the finalization race widened to a certainty.

The defect this guards against — a CLI that returns the moment a run row turns
terminal, truncating the `process_exited` event, the auto-commit of the agent's
work and the run report — reproduces about once in a hundred runs. A test that
merely launches the CLI therefore passes with the fix reverted, which is
exactly what independent review demonstrated about the test that shipped
alongside the fix.

So the window is widened rather than raced for. This wrapper holds the
supervisor's daemon thread for `AICC_TEST_WIDEN_FINALIZATION_SECONDS`
immediately after the *terminal* run row is committed and before that thread
appends `process_exited`. It stretches an interval that already exists —
measured at ~2.5 ms median, 41 ms max, against the CLI's 200 ms poll interval —
until no poll tick can land outside it. Ordering and product logic are
untouched; only the gap between two steps that were already in this order.

It is a wrapper script rather than a `sitecustomize` module because the first
attempt was one, and it **failed silently**: at `sitecustomize` time
`command_center` is not importable yet, so the patch never applied, Python
printed a line nobody reads, and the guard went green while measuring nothing.
That is the same class of false gate this whole series exists to remove, so
this version refuses to run unless it can prove it patched what it meant to.
"""

from __future__ import annotations

import os
import runpy
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts" / "execution_center_debug.py"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_WIDEN = float(os.environ.get("AICC_TEST_WIDEN_FINALIZATION_SECONDS", "0") or 0)
if _WIDEN <= 0:
    raise SystemExit(
        "AICC_TEST_WIDEN_FINALIZATION_SECONDS must be set to a positive number; "
        "this wrapper exists only to widen the finalization window, and running "
        "it without one would quietly measure nothing"
    )

from command_center.runtime import db as _db  # noqa: E402  (after sys.path setup)

_TERMINAL = set(_db.TERMINAL_STATES)
_original_update_run_state = _db.update_run_state


def _update_run_state(*args, **kwargs):
    result = _original_update_run_state(*args, **kwargs)
    if kwargs.get("new_state") in _TERMINAL:
        time.sleep(_WIDEN)
    return result


_db.update_run_state = _update_run_state

# Proof, not hope. The supervisor resolves `db.update_run_state` at call time,
# so this attribute is the one it will reach — and if some future refactor
# moves the write elsewhere, this wrapper must fail loudly rather than keep
# reporting a guarded run.
if _db.update_run_state is not _update_run_state:  # pragma: no cover - defensive
    raise SystemExit("failed to install the finalization widener")
if not _TERMINAL:
    raise SystemExit("db.TERMINAL_STATES is empty; the widener would never fire")

sys.argv = [str(CLI), *sys.argv[1:]]
runpy.run_path(str(CLI), run_name="__main__")
