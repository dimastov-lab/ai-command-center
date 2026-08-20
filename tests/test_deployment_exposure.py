"""Network-exposure invariants for every launch path (VOYN-W0-AICC-STREAMLIT-EXPOSED-NO-AUTH).

The application has no authentication layer yet performs privileged git/gh and
subprocess operations, so *no* launch artifact may put it on a reachable
interface without the operator explicitly asking for that.

Four launch paths exist and each needs its own guard, because a fix applied to
one of them has already failed to protect the others:

  1. bare `streamlit run app.py`   -> `.streamlit/config.toml` pins the address
  2. `scripts/start-ui.sh`         -> injects a loopback default
  3. container entrypoint          -> `scripts/aml-entrypoint.sh`
  4. `docker compose`              -> the *published* port in the compose file

Paths 1 and 2 were hardened by an earlier audit (BLOCKER-1) but were never
covered by a test; paths 3 and 4 then reintroduced the exact same exposure.
This module owns the invariant for all four so that a regression in any one of
them fails the gate.

Scope note: these tests assert only that the surface is not *exposed*. They do
not — and cannot — assert that it is *authenticated*, because it is not, and
under [ADR 0010](../docs/adr/0010-streamlit-console-stays-local.md) it never
will be: the console is a local-only surface and remote access is the
authenticated HTTP API's job (AUTH-HTTP-01).

That ADR also closes the gap this module structurally cannot cover. Every
assertion here reads a *file*, so none of them can see a `--server.address`
typed on a command line — which is exactly what all four artifacts used to
advertise as "an intentional, reviewed exposure". The in-process counterpart is
`command_center/console_boundary.py`, gated by `tests/test_console_boundary.py`.
The last three tests below are the seam between the two: they pin the compose
publish to a *literal* loopback address (so widening it is an edit to a gated
file rather than a shell variable), and they pin the one operator assertion the
in-process guard has to take on trust to the single artifact allowed to make it.
"""

from __future__ import annotations

import ipaddress
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent

ENTRYPOINT = ROOT / "scripts" / "aml-entrypoint.sh"
COMPOSE = ROOT / "docker-compose.aml.yml"

# `${NAME}`, `${NAME:-default}` and `${NAME-default}` as used by compose interpolation.
_INTERPOLATION = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::?-([^}]*))?\}")


def _is_loopback(address: str) -> bool:
    """True when `address` can only be reached from the host itself."""
    if address in {"localhost", "localhost4", "localhost6"}:
        return True
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def _resolve_defaults(value: str) -> str:
    """Interpolate a compose value the way an operator who set nothing would see it.

    An unset variable with no default renders as the empty string, which is
    exactly the fail-open case the port assertions must catch.
    """
    return _INTERPOLATION.sub(lambda m: m.group(2) or "", value)


def _published_host_address(port_spec: str) -> str | None:
    """Return the host interface a compose short-syntax port publishes on.

    `None` means the spec is unqualified — Docker then binds every interface,
    and does so by writing its own rules, bypassing a host firewall.
    """
    spec = port_spec.rsplit("/", 1)[0]  # drop an optional /tcp|/udp suffix
    if spec.startswith("["):  # [::1]:8501:8501 — bracketed IPv6 host
        host, _, _rest = spec[1:].partition("]")
        return host or None
    parts = spec.split(":")
    if len(parts) < 3:  # "8501" or "8501:8501" — no host address at all
        return None
    return parts[0] or None


def _run_entrypoint(tmp_path: Path, address: str | None) -> tuple[int, str, str]:
    """Execute the real entrypoint with `python`/`streamlit` stubbed out.

    Returns the exit code, whatever argv the stub `streamlit` was handed (empty
    when it was never reached) and stderr, so the assertions below are about
    what the script *does*, not about what it contains.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launched = tmp_path / "streamlit-argv.txt"

    (bin_dir / "streamlit").write_text(f'#!/usr/bin/env bash\necho "$@" > "{launched}"\n')
    (bin_dir / "python").write_text("#!/usr/bin/env bash\nexit 0\n")
    for stub in ("streamlit", "python"):
        (bin_dir / stub).chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "AICC_DATA_DIR": str(tmp_path / "data"),
    }
    env.pop("STREAMLIT_SERVER_ADDRESS", None)
    if address is not None:
        env["STREAMLIT_SERVER_ADDRESS"] = address

    completed = subprocess.run(
        ["bash", str(ENTRYPOINT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    argv = launched.read_text() if launched.exists() else ""
    return completed.returncode, argv, completed.stderr


# --- Path 1: a bare `streamlit run app.py` -----------------------------------


def test_streamlit_config_pins_a_loopback_address() -> None:
    config = (ROOT / ".streamlit" / "config.toml").read_text()
    match = re.search(r"^\s*address\s*=\s*\"([^\"]+)\"", config, re.MULTILINE)
    assert match is not None, ".streamlit/config.toml must pin [server] address"
    assert _is_loopback(match.group(1)), (
        f".streamlit/config.toml binds {match.group(1)!r}; a bare `streamlit run app.py` "
        "would then expose an unauthenticated privileged console off-host"
    )


# --- Path 2: scripts/start-ui.sh ---------------------------------------------


def test_start_ui_defaults_to_a_loopback_address() -> None:
    # Comment lines mention the flag while documenting the override, so read
    # only the executable ones.
    code = "\n".join(
        line
        for line in (ROOT / "scripts" / "start-ui.sh").read_text().splitlines()
        if not line.lstrip().startswith("#")
    )
    match = re.search(r"--server\.address\s+(\S+)", code)
    assert match is not None, "start-ui.sh must inject a default --server.address"
    address = match.group(1).strip("\"'")
    assert _is_loopback(address), (
        f"start-ui.sh defaults to {address!r} instead of a loopback address"
    )


# --- Path 3: the container entrypoint ----------------------------------------


def test_entrypoint_refuses_to_start_without_an_explicit_address(tmp_path: Path) -> None:
    """No default at all: an operator cannot *forget* to choose the bind address.

    A safe default would still be a default — reachable by silently inheriting
    it. Refusing to start makes the omission impossible rather than unlikely,
    and the failure is loud, immediate and costs nothing but a restart.

    The exit code is asserted exactly, and the message with it, because "exited
    non-zero" is too weak to prove the guard exists: with `set -u` any stray
    reference to the unset variable also aborts the script. That accident would
    satisfy a `!= 0` assertion while leaving the deployment's fail-closed
    behaviour resting on an `echo` that a later edit could remove without
    noticing. `78` is EX_CONFIG and is only produced deliberately.
    """
    returncode, argv, stderr = _run_entrypoint(tmp_path, address=None)
    assert returncode == 78, (
        "entrypoint must refuse to start deliberately (exit 78) when "
        f"STREAMLIT_SERVER_ADDRESS is unset; it exited {returncode} with: {stderr!r}"
    )
    assert "STREAMLIT_SERVER_ADDRESS" in stderr, (
        f"the refusal must name the variable the operator has to set; got: {stderr!r}"
    )
    assert argv == "", f"entrypoint launched streamlit anyway, with: {argv!r}"


@pytest.mark.parametrize("address", ["127.0.0.1", "0.0.0.0"])
def test_entrypoint_binds_exactly_the_requested_address(tmp_path: Path, address: str) -> None:
    """The operator's explicit choice is passed through verbatim, never widened."""
    returncode, argv, _stderr = _run_entrypoint(tmp_path, address=address)
    assert returncode == 0, f"entrypoint failed for an explicit address: {address}"
    assert f"--server.address {address}" in argv, (
        f"entrypoint did not bind the requested {address!r}; it ran: {argv!r}"
    )


# --- Path 4: the published port in docker compose ----------------------------


def _compose_service() -> dict:
    return yaml.safe_load(COMPOSE.read_text())["services"]["aml"]


def test_compose_publishes_ports_only_on_a_loopback_default() -> None:
    """Every published port must name a host interface, defaulting to loopback.

    An unqualified `"8501:8501"` binds every interface on the host, and Docker
    installs the rule below a host firewall, so the exposure is not visible in
    the firewall's own configuration.
    """
    ports = _compose_service().get("ports", [])
    assert ports, "the compose service must declare its published ports explicitly"

    for entry in ports:
        assert isinstance(entry, str), f"unsupported long-syntax port entry: {entry!r}"
        host_address = _published_host_address(_resolve_defaults(entry))
        assert host_address is not None, (
            f"port {entry!r} is published without a host address, which binds every "
            "interface on the host"
        )
        assert _is_loopback(host_address), (
            f"port {entry!r} defaults to publishing on {host_address!r}; the default "
            "must be loopback and any wider exposure must be an explicit operator choice"
        )


def test_compose_sets_the_container_bind_address_explicitly() -> None:
    """The counterpart to the fail-closed entrypoint.

    Inside the container's own network namespace the service must listen on all
    interfaces or the published port cannot reach it. That is safe *because*
    the namespace is private and the publish above is loopback-qualified — but
    it only holds while the value is stated here, so assert it stays stated.
    """
    environment = _compose_service().get("environment", {})
    assert environment.get("STREAMLIT_SERVER_ADDRESS"), (
        "compose must set STREAMLIT_SERVER_ADDRESS explicitly; the entrypoint has no "
        "default and the service would otherwise refuse to start"
    )


# --- Path 4b: the decision ADR 0010 makes non-negotiable ---------------------

#: Set by the container deployment to assert the one fact the console process
#: cannot observe about itself: that its non-loopback bind is inside a private
#: network namespace. Kept as a literal here rather than imported from
#: `command_center.console_boundary` on purpose — this is the deployment side of
#: the contract, and a rename in the module should surface as a failing test
#: naming both halves, not as both halves quietly agreeing on a new name while
#: the shipped compose file still sets the old one.
PRIVATE_NAMESPACE_ENV = "AICC_CONSOLE_PRIVATE_NAMESPACE"

#: Where an operator-facing bind address could plausibly be introduced. Test
#: sources are excluded deliberately: they must be free to construct the
#: assertion as data in order to test it.
DEPLOYMENT_ARTIFACT_GLOBS = (
    "scripts/**/*",
    "deploy/**/*",
    "packaging/**/*",
    ".github/**/*",
    "Dockerfile",
    "Makefile",
    ".env.example",
    "docker-compose*.yml",
)

_ASSERTION_ASSIGNMENT = re.compile(
    rf"{PRIVATE_NAMESPACE_ENV}\s*[:=]\s*[\"\']?([^\"\'\s#]*)",
)

_TRUTHY = {"1", "true", "yes", "on"}


def _raw_published_host(port_spec: str) -> str | None:
    """The host segment of a compose port spec, *before* any interpolation.

    Separate from `_published_host_address` above, which resolves defaults
    first. Here the point is precisely to see the `${...}` if there is one.
    """
    spec = port_spec.rsplit("/", 1)[0]
    if spec.startswith("["):
        host, _, _rest = spec[1:].partition("]")
        return host or None
    head, separator, rest = spec.partition(":")
    if not separator or ":" not in rest:  # "8501" or "8501:8501"
        return None
    return head or None


def test_compose_publish_host_is_a_literal_an_operator_cannot_widen() -> None:
    """The host address must not be an interpolated variable.

    `test_compose_publishes_ports_only_on_a_loopback_default` above accepts a
    safe *default*, which is what `${AML_BIND_HOST:-127.0.0.1}` was. ADR 0010
    withdrew that: a default is a knob, and the comment beside this one used to
    invite operators to turn it ("set AML_BIND_HOST to widen it, in front of a
    reviewed authenticating proxy"). Nothing performed that review, and turning
    the knob left no trace in the repository at all.

    With a literal, widening the publish means editing a file this module reads,
    so the change arrives in a diff and fails here — which is the only place the
    decision can be re-argued.
    """
    for entry in _compose_service().get("ports", []):
        host = _raw_published_host(entry)
        assert host is not None, f"port {entry!r} publishes without a host address"
        assert "${" not in host, (
            f"port {entry!r} takes its host interface from a variable; an operator "
            "can then expose an unauthenticated privileged console without changing "
            "a tracked file (ADR 0010)"
        )
        assert _is_loopback(host), f"port {entry!r} publishes on {host!r}, not loopback"


def test_compose_declares_the_private_namespace_for_its_container_bind() -> None:
    """The container's `0.0.0.0` is the one sanctioned non-loopback bind — and it
    must say so, because the console now refuses that bind by default.

    The two settings are a pair: `STREAMLIT_SERVER_ADDRESS: 0.0.0.0` is only
    safe *because* the publish above is loopback-qualified, and the assertion
    variable is the deployment stating that. Shipping the first without the
    second means the container starts and then refuses every session; shipping
    the second without the first means an assertion is standing where it is not
    needed and would silently cover a later widening.
    """
    environment = _compose_service().get("environment", {})
    address = environment.get("STREAMLIT_SERVER_ADDRESS")
    assert address, "compose must set STREAMLIT_SERVER_ADDRESS explicitly"

    declared = str(environment.get(PRIVATE_NAMESPACE_ENV, "")).strip().lower() in _TRUTHY
    if _is_loopback(str(address)):
        assert not declared, (
            f"compose binds the loopback address {address!r} and does not need "
            f"{PRIVATE_NAMESPACE_ENV}; an assertion that is not needed is one that "
            "will still be here covering a later change that does need reviewing"
        )
    else:
        assert declared, (
            f"compose binds {address!r} inside the container but does not set "
            f"{PRIVATE_NAMESPACE_ENV}; command_center/console_boundary.py refuses "
            "every session on a non-loopback bind without it (ADR 0010)"
        )


def test_no_other_deployment_artifact_asserts_a_private_namespace() -> None:
    """The assertion is unverifiable at runtime, so its blast radius is bounded here.

    A process cannot see its host's port-publishing rules, so
    `AICC_CONSOLE_PRIVATE_NAMESPACE` is something the deployment claims rather
    than something the console checks. What makes the claim reviewable is that
    it appears in exactly one file — the same file whose publish line the test
    above pins to a literal loopback address, which is what makes the claim
    true. Set anywhere else it is an unpaired assertion: a way to switch the
    guard off with no accompanying evidence, which is the "reviewed exposure"
    ADR 0010 removed, restored under a new name.
    """
    permitted = COMPOSE.resolve()
    offenders: list[str] = []

    for pattern in DEPLOYMENT_ARTIFACT_GLOBS:
        for path in ROOT.glob(pattern):
            if not path.is_file() or path.resolve() == permitted:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # a binary or unreadable artifact cannot set an env var
            for match in _ASSERTION_ASSIGNMENT.finditer(text):
                if match.group(1).strip().lower() in _TRUTHY:
                    offenders.append(f"{path.relative_to(ROOT)}: {match.group(0)!r}")

    assert not offenders, (
        f"{PRIVATE_NAMESPACE_ENV} is asserted outside {COMPOSE.name}, where nothing "
        "pins the loopback publish that makes it true: " + "; ".join(offenders)
    )
