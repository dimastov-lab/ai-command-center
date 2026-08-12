# Local developer shortcuts. CI does not use this file.
# Uses .venv/bin/python directly (not `uv run`) so uv.lock is never touched
# by a plain test run.

PY := .venv/bin/python

.PHONY: preflight test test-fast

## Fast pre-push checks: whitespace, ruff, byte-compile (mirrors the fast part
## of the CI Quality gates job).
preflight:
	./scripts/preflight.sh

## Full test suite, serial.
test:
	$(PY) -m pytest -q

## Full test suite in parallel with pytest-xdist (local speedup only; CI is
## unchanged). Tests that cannot run concurrently are marked `serial` and run
## in a second, single-process phase.
test-fast:
	$(PY) -m pytest -q -n 8 -m "not serial"
	$(PY) -m pytest -q -m serial
