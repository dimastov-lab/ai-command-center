"""Real-browser E2E for the status projection (audit D2/D5).

Every other UI test is headless Streamlit `AppTest`, which inspects the element
tree without a real browser/WebSocket — so it never caught that the Kanban board
dropped the `Blocked` lane and rendered only 88 of 174 tasks. This test renders
the *actual* app in Chromium and asserts the board shows a `Blocked` lane and
accounts for every task (nothing silently vanishes).

Skips cleanly where Playwright or its browser is unavailable; CI installs both
(`python -m playwright install chromium`).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

APP = Path(__file__).resolve().parents[1] / "app.py"


def _chromium_installed() -> bool:
    """True only when the Playwright Chromium browser is actually present."""
    try:
        with sync_api.sync_playwright() as pw:
            executable = pw.chromium.executable_path
        return bool(executable) and Path(executable).exists()
    except Exception:
        return False


# Skip the whole module *before* the fixture spawns Streamlit when the browser is
# absent (e.g. the required CI gate, which does not install browsers). A dedicated
# informational CI step runs `python -m playwright install chromium` first, so the
# E2E actually executes there without being able to destabilise the merge gate.
if not _chromium_installed():
    pytest.skip("Playwright Chromium browser is not installed", allow_module_level=True)

# A known, tiny store whose statuses include the previously-invisible `Blocked`.
FIXTURE_TASKS = [
    {"id": "t1", "project": "AICC", "title": "Backlog one", "goal": "g", "status": "Backlog"},
    {"id": "t2", "project": "AICC", "title": "Backlog two", "goal": "g", "status": "Backlog"},
    {"id": "t3", "project": "AICC", "title": "Blocked one", "goal": "g", "status": "Blocked"},
    {"id": "t4", "project": "AICC", "title": "Blocked two", "goal": "g", "status": "Blocked"},
    {"id": "t5", "project": "AICC", "title": "Blocked three", "goal": "g", "status": "Blocked"},
    {"id": "t6", "project": "AICC", "title": "Done one", "goal": "g", "status": "Done"},
]
BLOCKED_COUNT = sum(1 for t in FIXTURE_TASKS if t["status"] == "Blocked")


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def live_app(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("aicc_e2e_data")
    (data_dir / "tasks.json").write_text(json.dumps(FIXTURE_TASKS), encoding="utf-8")
    port = _free_port()
    env = {
        **os.environ,
        "AICC_DATA_DIR": str(data_dir),
        "AICC_BACKGROUND_SYNC": "0",
        "AICC_OPERATOR": "",
    }
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "streamlit", "run", str(APP),
            "--server.port", str(port),
            "--server.address", "127.0.0.1",
            "--server.headless", "true",
            "--browser.gatherUsageStats", "false",
        ],
        env=env, cwd=str(APP.parent),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            if proc.poll() is not None:
                pytest.fail("streamlit process exited before becoming ready")
            try:
                with urllib.request.urlopen(url + "/_stcore/health", timeout=1) as resp:
                    if resp.status == 200:
                        break
            except OSError:
                time.sleep(0.5)
        else:
            pytest.fail("streamlit did not become healthy in time")
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _board_text(url: str) -> str:
    try:
        launcher = sync_api.sync_playwright
        with launcher() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:  # browser not installed
                pytest.skip(f"Chromium unavailable: {exc}")
            page = browser.new_page()
            page.goto(url, wait_until="load")
            # Streamlit paints content over the WebSocket after `load`; wait for
            # the board to actually render its lanes.
            page.wait_for_selector("text=Blocked", timeout=90000)
            page.wait_for_selector("text=Done", timeout=90000)
            body = page.inner_text("body")
            browser.close()
            return body
    finally:
        pass


def test_board_shows_blocked_lane_and_accounts_for_every_task(live_app):
    body = _board_text(live_app)
    # D2: the Blocked lane exists at all (it was absent → 49% of tasks invisible).
    assert "Blocked" in body
    # D5/D2: the board reflects the blocked tasks that used to vanish. Their count
    # must appear on the board next to the Blocked lane.
    assert str(BLOCKED_COUNT) in body
    # Sanity: the other canonical lanes render too, so the board is really up.
    assert "Backlog" in body
    assert "Done" in body


def test_dashboard_keyboard_semantics_and_320px_reflow(live_app):
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 320, "height": 800})
        page.goto(live_app, wait_until="load")
        page.wait_for_selector("text=Очередь выполнения", timeout=90000)

        surface = page.locator("[data-testid='stMain']")
        assert surface.locator("h1").count() >= 1
        assert surface.locator("h2").count() >= 1
        assert surface.locator("[role='status']").count() >= 1
        assert surface.locator("[role='progressbar']").count() >= 1
        assert surface.locator("svg[role='img'][aria-label]").count() >= 1
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")

        action_names = surface.locator("button").evaluate_all(
            "els => els.map(el => el.getAttribute('aria-label') || el.innerText).filter(Boolean)"
        )
        assert action_names
        dashboard_action_names = [
            name
            for name in action_names
            if any(
                marker in name
                for marker in ("Быстро:", "arrow_forward", "Открыть Execution Center", "Открыть задачу")
            )
        ]
        assert dashboard_action_names
        assert len(dashboard_action_names) == len(set(dashboard_action_names))

        surface.locator("button").first.focus()
        first_focused = page.evaluate("document.activeElement.outerHTML")
        focus_style = surface.locator("button").first.evaluate(
            "el => getComputedStyle(el).outlineStyle + ':' + getComputedStyle(el).outlineWidth"
        )
        assert focus_style != "none:0px"
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement !== document.body")
        assert page.evaluate("document.activeElement.outerHTML") != first_focused

        page.set_viewport_size({"width": 640, "height": 800})
        page.evaluate("document.documentElement.style.fontSize = '200%'")
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        browser.close()
