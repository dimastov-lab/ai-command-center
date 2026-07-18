"""The one place OS-specific branching lives for the Streamlit application.

`docs/desktop/ARCHITECTURE.md` §6/§8.1 and `docs/desktop/PLATFORM_BEHAVIOR.md`
§3-4 define a target `command_center.platform` package (D3 of the separate,
not-yet-started desktop migration track — see `docs/adr/
0001-engineering-control-center-v2-increment-1.md`) as the *only* package
permitted to contain `sys.platform`/`platform.system()` branching, with
`reveal_in_file_manager(path)` as part of its documented contract. This
module is the interim, Streamlit-app-scoped equivalent: every `sys.platform`
check in the app lives here and nowhere else, so that when D3 actually
happens, callers change an import path, not their calling convention (this
module already returns `(ok: bool, message: str)` tuples rather than raising,
which is what a Streamlit page wants — D3's actual signatures may differ to
fit Qt's signal/slot error-propagation model, see `ARCHITECTURE.md` §12).

macOS only today (this repository's only target platform for the Streamlit
app). Every function fails closed with a clear message on any other OS
rather than silently no-op-ing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def reveal_in_file_manager(path: str | Path) -> tuple[bool, str]:
    """Opens Finder at `path` (macOS). Named to match the target
    `command_center.platform.reveal_in_file_manager` contract."""
    if sys.platform != "darwin":
        return False, "Открытие Finder поддерживается только на macOS."
    resolved = Path(path)
    if not resolved.exists():
        return False, f"Путь не найден: {path}"
    try:
        subprocess.run(["open", str(resolved)], check=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Не удалось открыть Finder: {exc}"
    return True, f"Открыто в Finder: {resolved}"


def open_terminal_at(path: str | Path) -> tuple[bool, str]:
    """Opens macOS Terminal.app at `path`. Never types or runs a command
    inside it — only changes the app's working directory, equivalent to
    Finder's "New Terminal at Folder". Not part of the D3 platform contract
    yet (Desktop Increment 1 is read-only and has no terminal launching, per
    `docs/desktop/DESKTOP_INCREMENT_1.md` §1) — added here because the
    Streamlit Launch System needs it now."""
    if sys.platform != "darwin":
        return False, "Открытие терминала поддерживается только на macOS."
    resolved = Path(path)
    if not resolved.exists():
        return False, f"Путь не найден: {path}"
    try:
        subprocess.run(["open", "-a", "Terminal", str(resolved)], check=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Не удалось открыть терминал: {exc}"
    return True, f"Терминал открыт: {resolved}"


def copy_to_clipboard(text: str) -> tuple[bool, str]:
    """Copies `text` to the system clipboard (macOS `pbcopy`). Also not yet
    part of the D3 platform contract — same rationale as `open_terminal_at`."""
    if sys.platform != "darwin":
        return False, "Копирование в буфер обмена поддерживается только на macOS."
    try:
        subprocess.run(["pbcopy"], input=text, text=True, check=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Не удалось скопировать в буфер обмена: {exc}"
    return True, "Промпт скопирован в буфер обмена."


def platform_name() -> str:
    """Matches the target `command_center.platform.platform_name()` contract
    shape (`PLATFORM_BEHAVIOR.md` §3): a stable identifier for the very few
    call sites above this layer that legitimately need one."""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return sys.platform
