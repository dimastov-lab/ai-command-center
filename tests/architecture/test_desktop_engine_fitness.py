"""Architecture fitness gate: the desktop client has no duplicate execution engine.

Enforces the Desktop Increment 1 architecture contract (`DESKTOP_INCREMENT_1.md`
§6.2, `IMPLEMENTATION_ROADMAP.md` D2A forbidden scope, issue #196 AC): the native
client consumes `command_center.runtime` exclusively through the narrow
`command_center.application` ports and never grows its own execution machinery.

Static AST rules over ``command_center/desktop`` + ``command_center/application``:

1. no process-execution primitives (``subprocess``, ``multiprocessing``, ``pty``,
   ``os.system``/``os.popen``/``os.exec*``/``os.spawn*``);
2. no second UI/server engine (``streamlit``, ``flask``, ``fastapi``, ``uvicorn``,
   ``http.server``, ``socketserver``);
3. the desktop (Qt) layer never imports ``command_center.runtime`` or
   ``command_center.webapi`` directly — data flows only through application ports;
4. exactly one ``ExecutionCenterAPI(...)`` construction site in the whole
   perimeter, owned by ``application/workspace_home_adapter.py``;
5. the application layer stays Qt-free (importable from plain ``pytest``).
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DESKTOP = REPO_ROOT / "command_center" / "desktop"
APPLICATION = REPO_ROOT / "command_center" / "application"

FORBIDDEN_PROCESS_MODULES = {"subprocess", "multiprocessing", "pty"}
FORBIDDEN_ENGINE_MODULES = {
    "streamlit",
    "flask",
    "fastapi",
    "uvicorn",
    "http.server",
    "socketserver",
}
FORBIDDEN_OS_CALLS = ("system", "popen", "execl", "execle", "execlp", "execv",
                      "execve", "execvp", "spawnl", "spawnv", "startfile")


def _python_files(*roots: Path) -> list[Path]:
    files = [p for root in roots for p in sorted(root.rglob("*.py"))]
    assert files, f"no python files under {roots} — perimeter moved?"
    return files


def _imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module)
    return roots


def _matches(imported: set[str], forbidden: set[str]) -> set[str]:
    return {
        mod
        for mod in imported
        for bad in forbidden
        if mod == bad or mod.startswith(bad + ".")
    }


def test_no_process_execution_primitives_in_desktop_or_application():
    offenders: dict[str, set[str]] = {}
    for path in _python_files(DESKTOP, APPLICATION):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        hits = _matches(_imported_roots(tree), FORBIDDEN_PROCESS_MODULES)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
                and node.func.attr in FORBIDDEN_OS_CALLS
            ):
                hits.add(f"os.{node.func.attr}")
        if hits:
            offenders[str(path.relative_to(REPO_ROOT))] = hits
    assert not offenders, f"process-execution primitives found: {offenders}"


def test_no_second_ui_or_server_engine():
    offenders = {
        str(path.relative_to(REPO_ROOT)): hits
        for path in _python_files(DESKTOP, APPLICATION)
        if (
            hits := _matches(
                _imported_roots(ast.parse(path.read_text(encoding="utf-8"))),
                FORBIDDEN_ENGINE_MODULES,
            )
        )
    }
    assert not offenders, f"second engine imports found: {offenders}"


def test_desktop_layer_reaches_runtime_only_through_application_ports():
    forbidden = {"command_center.runtime", "command_center.webapi"}
    offenders = {
        str(path.relative_to(REPO_ROOT)): hits
        for path in _python_files(DESKTOP)
        if (
            hits := _matches(
                _imported_roots(ast.parse(path.read_text(encoding="utf-8"))),
                forbidden,
            )
        )
    }
    assert not offenders, f"desktop imports runtime/webapi directly: {offenders}"


def test_exactly_one_execution_center_api_construction_site():
    sites: list[str] = []
    for path in _python_files(DESKTOP, APPLICATION):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ExecutionCenterAPI"
            ):
                sites.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert len(sites) == 1, f"expected one construction site, got {sites}"
    assert sites[0].startswith("command_center/application/workspace_home_adapter.py")


def test_application_layer_is_qt_free():
    offenders = {
        str(path.relative_to(REPO_ROOT)): hits
        for path in _python_files(APPLICATION)
        if (
            hits := _matches(
                _imported_roots(ast.parse(path.read_text(encoding="utf-8"))),
                {"PySide6", "PyQt5", "PyQt6", "shiboken6"},
            )
        )
    }
    assert not offenders, f"Qt imports in application layer: {offenders}"
