"""The Streamlit console's own refusal to serve on a reachable interface.

Why this exists at all
----------------------

Four launch artifacts already keep the console off-host — `.streamlit/config.toml`,
`scripts/start-ui.sh`, `scripts/aml-entrypoint.sh` and `docker-compose.aml.yml`,
each gated by `tests/test_deployment_exposure.py`. All four are *launch-path*
controls, and every one of them documented the same escape hatch: pass an
explicit `--server.address`, or set `AML_BIND_HOST`, and you have "an
intentional, reviewed exposure". Nothing reviewed it. An operator following
those comments put an unauthenticated console that runs `git`, `gh` and
arbitrary agent subprocesses on a reachable interface, and no gate could see it
because the exposure lived in a command line, not in a file.

[ADR 0010](../docs/adr/0010-streamlit-console-stays-local.md) withdrew that
escape hatch: the Streamlit console is a local-only, single-operator surface and
will not be given an authentication layer. Remote and multi-operator access is
served by the authenticated HTTP API (`command_center.api`,
`command_center.webapi`, `VOYN-W0-AICC-AUTH-HTTP-01`) and the web/desktop
clients in front of it.

This module is where that decision stops being prose. It runs inside the process
that actually holds the privilege, so it sees the effective bind address however
it was chosen — config file, environment variable, or a CLI flag that overrode
both.

What it can and cannot see
--------------------------

It sees one thing: the address this process is listening on. It deliberately
does not try to infer anything else, because a process cannot:

* It cannot see the host's port-publishing rules. Inside a container the
  correct bind address is `0.0.0.0` — the network namespace is private and a
  published port cannot reach a service listening only on the container's own
  loopback. Whether that published port is loopback-qualified is a fact about
  the *host*, not about this process. That single case is the reason
  :data:`PRIVATE_NAMESPACE_ENV` exists, and it is an operator assertion this
  module cannot verify. It is set in exactly one place in the repository —
  `docker-compose.aml.yml`, the same file whose publish line
  `tests/test_deployment_exposure.py` pins to a literal loopback address — and
  a test refuses any other artifact setting it truthy. That pairing is what
  makes the assertion checkable by review even though it is not checkable at
  runtime.
* It cannot make the console authenticated. Refusing to serve is the whole
  mechanism; there is no degraded read-only mode, because "read-only" is not a
  state `app.py` has.

Timing: `app.py` is a linear script re-executed per session, so this check runs
once per session, before any page renders — not once at server boot. A refused
deployment therefore still *listens*: the socket accepts, Streamlit serves its
own static shell and health endpoint, and every session that reaches the
application script is refused with no widget rendered and no callback
registered. That is the earliest point in-process code can act, and it is
strictly more than the launch-path guards could do; it is not a claim that the
port is closed.

Fail-closed in both directions
------------------------------

An unset address is a refusal, not a pass: Streamlit's own default for
`server.address` is `None`, which binds every interface. "I do not know which
interface I am on" and "I am on a safe one" are the two readings, and only one
of them is survivable — the same reasoning `scripts/aml-entrypoint.sh` already
applies by exiting `78` rather than choosing a default.

:func:`is_serving` is the other direction. It answers "is this interpreter
answering the console over a socket at all", which is false under
`streamlit.testing.v1.AppTest` and under a plain `import app`. If that detection
silently broke, the guard would silently stop guarding — a fail-*open*
regression. `tests/test_console_boundary.py` pins the marker it reads to the
Streamlit CLI's own import of it, so a rename upstream fails the gate instead of
disabling the control.
"""

from __future__ import annotations

import ipaddress
import os
import sys
from collections.abc import Mapping

#: Environment variable by which a deployment asserts the one fact this process
#: cannot observe: that its non-loopback bind address is inside a private
#: container network namespace whose published port is loopback-qualified.
#:
#: Deliberately named for the *fact it asserts* rather than for its effect. A
#: general `AICC_CONSOLE_EXPOSURE=public` knob would be a supported way to say
#: "I reviewed this and it is fine", which is exactly the sentence ADR 0010
#: withdrew. This variable is false — not merely unwise — if the port is
#: published anywhere but loopback.
PRIVATE_NAMESPACE_ENV = "AICC_CONSOLE_PRIVATE_NAMESPACE"

#: The module the Streamlit CLI imports to run a script over a real server
#: (`streamlit/web/cli.py`: `from streamlit.web import bootstrap`). Its presence
#: in `sys.modules` distinguishes `streamlit run app.py` from `AppTest`, which
#: builds a Runtime with no web server, and from a bare `import app`.
SERVING_MARKER_MODULE = "streamlit.web.bootstrap"

_TRUTHY = frozenset({"1", "true", "yes", "on"})

_ADR = "docs/adr/0010-streamlit-console-stays-local.md"


class ConsoleExposureRefused(RuntimeError):
    """This process is serving the privileged console on a reachable interface.

    Raised instead of handled here so the caller decides how to refuse: `app.py`
    writes the reason to stderr — where the operator who chose the address is
    watching — *and* renders it, then stops the script before any page is built.
    """


def is_loopback(address: str) -> bool:
    """True when `address` can only be reached from the host itself.

    Accepts the hostname spellings Streamlit takes verbatim as well as literal
    addresses. Anything unparseable is not loopback: an address this module
    cannot resolve to a known-local interface is one it must refuse.

    Deliberately no DNS: a name that resolves to a loopback address today is a
    name whose owner can repoint it, and a security decision must not depend on
    a lookup made at boot in a resolver this process does not control.
    """
    if address in {"localhost", "localhost4", "localhost6"}:
        return True
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def private_namespace_declared(env: Mapping[str, str] | None = None) -> bool:
    """True when the deployment asserts a private container network namespace.

    Only the explicit affirmative spellings count. A variable set to an empty
    string — which is what an unfilled `.env` template produces — is not an
    assertion, and neither is a value this module does not recognise.
    """
    source = os.environ if env is None else env
    return source.get(PRIVATE_NAMESPACE_ENV, "").strip().lower() in _TRUTHY


def is_serving(modules: Mapping[str, object] | None = None) -> bool:
    """True when this interpreter is answering the console over a socket."""
    return SERVING_MARKER_MODULE in (sys.modules if modules is None else modules)


def enforce(
    *,
    address: str | None,
    serving: bool | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    """Raise :class:`ConsoleExposureRefused` unless this console stays local.

    `address` is the *effective* `server.address` — Streamlit's resolved
    option, so a CLI flag, an environment variable and the config file all
    arrive here as the same value. `None` means Streamlit's own default, which
    binds every interface.
    """
    if serving is None:
        serving = is_serving()
    if not serving:
        return
    if address is not None and is_loopback(address):
        return
    if private_namespace_declared(env):
        return

    bound = "every interface (Streamlit's default)" if address is None else repr(address)
    raise ConsoleExposureRefused(
        f"Refusing to serve: this console is bound to {bound}, and it has no "
        "authentication layer while it runs git, gh and agent subprocesses on "
        f"the host.\n\n{_ADR} records the decision that the Streamlit console "
        "stays local: remote and multi-operator access is served by the "
        "authenticated HTTP API and the web/desktop clients, not by exposing "
        "this one.\n\n"
        "Bind a loopback address (`--server.address 127.0.0.1`) to start. If "
        "this is a container whose network namespace is private and whose "
        "published port is loopback-qualified, that is the one sanctioned "
        f"non-loopback bind: set {PRIVATE_NAMESPACE_ENV}=1 in the same "
        "deployment artifact that publishes the port."
    )
