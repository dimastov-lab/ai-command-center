"""Reveal filesystem objects in the target platform's native file manager."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .paths import platform_name


def reveal_in_file_manager(path: Path) -> None:
    target = Path(path).expanduser().resolve(strict=False)
    if platform_name() == "macos":
        command = ["open", "-R", str(target)]
    else:
        command = ["explorer.exe", f"/select,{target}"]
    subprocess.run(command, check=True)
