"""The console's in-process refusal to serve off-host (VOYN-W0-AICC-CONSOLE-NO-AUTH).

`tests/test_deployment_exposure.py` gates the four *launch artifacts*. This
module gates the control that exists because those artifacts cannot see
everything: a `--server.address 0.0.0.0` typed on a command line leaves no trace
in any file, so the only place it can be caught is inside the process that then
holds the privilege.

The decision this enforces is
[ADR 0010](../docs/adr/0010-streamlit-console-stays-local.md): the Streamlit
console is a local-only, single-operator surface, remote access is the
authenticated HTTP API's job, and "reviewed exposure" is no longer a supported
configuration.

Three things need gating, and the third is the one that is easy to forget:

1. the refusal logic itself, including that an *unset* address refuses;
2. that `app.py` is actually wired to it, and refuses before building a page;
3. that :func:`console_boundary.is_serving` still detects a real server. If that
   detection broke, every test above would still pass while the guard silently
   stopped guarding — the one failure mode of this design that is fail-*open*.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
import streamlit.web
from streamlit.testing.v1 import AppTest

from command_center import console_boundary

ROOT = Path(__file__).resolve().parent.parent
APP_PATH = str(ROOT / "app.py")


# --- the refusal logic --------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    ["localhost", "localhost4", "localhost6", "127.0.0.1", "127.0.0.53", "::1"],
)
def test_a_loopback_bind_serves(address: str) -> None:
    console_boundary.enforce(address=address, serving=True, env={})


@pytest.mark.parametrize(
    "address",
    [
        "0.0.0.0",
        "::",
        "192.168.1.10",
        "10.0.0.4",
        "49.12.98.212",
        # A name is not evidence: this module never resolves DNS, because a name
        # that points at loopback today is one its owner can repoint tomorrow.
        "console.internal",
    ],
)
def test_a_reachable_bind_is_refused(address: str) -> None:
    with pytest.raises(console_boundary.ConsoleExposureRefused):
        console_boundary.enforce(address=address, serving=True, env={})


def test_an_unset_address_is_refused_rather_than_assumed_safe() -> None:
    """Streamlit's own default for `server.address` is `None` — every interface.

    "I do not know which interface I am on" must resolve the same way
    `scripts/aml-entrypoint.sh` already resolves it: refuse, loudly, at a cost
    of one restart. Reading `None` as loopback would make the guard hardest to
    trigger exactly where the config file failed to load.
    """
    with pytest.raises(console_boundary.ConsoleExposureRefused) as refusal:
        console_boundary.enforce(address=None, serving=True, env={})
    assert "every interface" in str(refusal.value)


def test_nothing_is_checked_when_this_process_serves_nothing() -> None:
    """`AppTest` and a bare `import app` reach no socket; there is nothing to expose."""
    console_boundary.enforce(address="0.0.0.0", serving=False, env={})


def test_the_private_namespace_assertion_permits_the_container_bind() -> None:
    console_boundary.enforce(
        address="0.0.0.0",
        serving=True,
        env={console_boundary.PRIVATE_NAMESPACE_ENV: "1"},
    )


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "  yes  ", "on"])
def test_affirmative_spellings_of_the_assertion_are_accepted(value: str) -> None:
    assert console_boundary.private_namespace_declared(
        {console_boundary.PRIVATE_NAMESPACE_ENV: value}
    )


@pytest.mark.parametrize("value", ["", "   ", "0", "false", "no", "off", "maybe", "container"])
def test_anything_that_is_not_an_affirmation_is_not_one(value: str) -> None:
    """An unfilled `.env` template renders as an empty value; that is not a claim.

    Neither is a spelling this module does not recognise. Treating an
    unrecognised value as truthy is how a typo turns into an exposure.
    """
    assert not console_boundary.private_namespace_declared(
        {console_boundary.PRIVATE_NAMESPACE_ENV: value}
    )
    with pytest.raises(console_boundary.ConsoleExposureRefused):
        console_boundary.enforce(
            address="0.0.0.0",
            serving=True,
            env={console_boundary.PRIVATE_NAMESPACE_ENV: value},
        )


def test_the_refusal_tells_the_operator_what_to_do_about_it() -> None:
    """A security refusal that does not say why is one an operator routes around.

    The message has to carry three things the reader does not have: the address
    it actually resolved (which may not be the one they think they set), the
    record that says this is a decision rather than a bug, and the single
    sanctioned exception, so the container case is not rediscovered by
    guesswork.
    """
    with pytest.raises(console_boundary.ConsoleExposureRefused) as refusal:
        console_boundary.enforce(address="0.0.0.0", serving=True, env={})
    message = str(refusal.value)

    assert "0.0.0.0" in message
    assert "0010-streamlit-console-stays-local.md" in message
    assert console_boundary.PRIVATE_NAMESPACE_ENV in message
    assert "--server.address 127.0.0.1" in message
    assert (ROOT / "docs" / "adr" / "0010-streamlit-console-stays-local.md").is_file()


# --- the `app.py` wiring ------------------------------------------------------


def _run_app_bound_to(monkeypatch: pytest.MonkeyPatch, address: str | None) -> AppTest:
    """Run the real `app.py` as if Streamlit had resolved `address` and were serving.

    Both inputs are faked at their source rather than by calling
    `console_boundary` directly, so what this exercises is `app.py`'s own call:
    delete the guard from `app.py` and these tests fail.
    """
    from streamlit import config as streamlit_config

    real_get_option = streamlit_config.get_option

    def fake_get_option(key: str):
        return address if key == "server.address" else real_get_option(key)

    monkeypatch.setattr(streamlit_config, "get_option", fake_get_option)
    monkeypatch.setattr(console_boundary, "is_serving", lambda: True)
    monkeypatch.delenv(console_boundary.PRIVATE_NAMESPACE_ENV, raising=False)

    return AppTest.from_file(APP_PATH, default_timeout=60).run()


def test_app_refuses_the_session_and_renders_no_privileged_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = _run_app_bound_to(monkeypatch, "0.0.0.0")

    assert not at.exception, f"the refusal must be a rendered error, not a crash: {at.exception}"
    assert [e for e in at.error if "Refusing to serve" in e.value], (
        f"app.py rendered no refusal while bound to 0.0.0.0; errors: {[e.value for e in at.error]}"
    )
    # `st.stop()` ran before the page was built, so nothing actionable exists.
    assert not at.button, "a refused session must not be handed any control to press"
    assert not at.selectbox
    assert not at.text_input


def test_app_serves_normally_on_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """The negative control: the guard must not be refusing everything."""
    at = _run_app_bound_to(monkeypatch, "127.0.0.1")

    assert not at.exception, f"the guard broke the normal launch path: {at.exception}"
    assert not [e for e in at.error if "Refusing to serve" in e.value]
    assert at.button or at.selectbox or at.markdown, "the app rendered nothing at all"


# --- the fail-open failure mode ----------------------------------------------


def test_the_serving_marker_is_the_module_the_streamlit_cli_imports() -> None:
    """Pin `is_serving`'s marker to the CLI's own import of it.

    `is_serving()` reads one key out of `sys.modules`. If a future Streamlit
    renames or stops importing that module, the key is never present, the guard
    concludes "not serving", and it stops guarding — with every other test in
    this file still green, because they all pass `serving=` explicitly.

    So assert the linkage directly: the module exists, and `streamlit/web/cli.py`
    — the entry point `streamlit run` goes through — imports it. Read from
    source rather than by importing the CLI: importing it would put the marker
    into this interpreter's `sys.modules` and make `is_serving()` true for every
    later test sharing the process.
    """
    assert console_boundary.SERVING_MARKER_MODULE == "streamlit.web.bootstrap"
    assert importlib.util.find_spec(console_boundary.SERVING_MARKER_MODULE) is not None, (
        "streamlit no longer ships the module is_serving() looks for"
    )

    cli_source = (Path(streamlit.web.__file__).parent / "cli.py").read_text()
    assert re.search(r"^from streamlit\.web import .*\bbootstrap\b", cli_source, re.MULTILINE), (
        "streamlit/web/cli.py no longer imports `bootstrap`; is_serving() would "
        "report 'not serving' inside a real `streamlit run` and the console "
        "boundary would silently stop being enforced"
    )
