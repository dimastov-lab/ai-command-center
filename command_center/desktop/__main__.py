"""Desktop entry point: ``python -m command_center.desktop`` (`docs/desktop/ARCHITECTURE.md` §16)."""

from __future__ import annotations

import sys

from .app import run

if __name__ == "__main__":
    sys.exit(run(sys.argv))
