# ADR 0010 — The Streamlit console stays local; remote operation moves to the authenticated API

Status: **Accepted.** Task `VOYN-W0-AICC-CONSOLE-NO-AUTH` (Wave 0, P0) requires this decision to be
recorded before any external deployment of the console. This is that record, and the mechanism
described under [Enforcement](#enforcement) is on `main` with it.

## Context

`app.py` serves HTTP and WebSocket traffic and performs privileged work on the host: it invokes
`git` and `gh`, launches provider CLIs (`claude`, `codex`, …) as subprocesses, writes the runtime
database, and mutates repositories and worktrees. It has **no authentication layer**. Any session
that reaches the script gets every one of those capabilities; there is no principal, no per-action
authorization, and nothing in the audit trail that names who acted.

The only thing standing between that surface and the network is where it binds. Four launch
artifacts pin it to loopback, and `tests/test_deployment_exposure.py` gates all four:

| Launch path | Control |
|---|---|
| `streamlit run app.py` | `.streamlit/config.toml` pins `[server] address = "localhost"` |
| `scripts/start-ui.sh` | injects `--server.address localhost` unless one is passed |
| container entrypoint | `scripts/aml-entrypoint.sh` has no default and exits `78` (`EX_CONFIG`) unless `STREAMLIT_SERVER_ADDRESS` is set |
| `docker compose` | the published port names a host interface |

That is a compensating control, not security architecture, and the audit finding behind this task
says exactly that. Two properties make the distinction concrete rather than rhetorical:

1. **Every one of those controls documented its own bypass.** `.streamlit/config.toml`: "an explicit
   `--server.address` CLI flag still overrides this for an intentional, reviewed exposure".
   `start-ui.sh`: "Pass an explicit `--server.address` to override". `docker-compose.aml.yml`: "set
   `AML_BIND_HOST` to widen it, in front of a reviewed authenticating proxy". `README.md` printed
   the widening command. Nothing performed the review those sentences assumed, and no gate could
   see the bypass, because it lives in a command line or a shell variable rather than in a file.
2. **They are launch-path controls, and launch paths keep being added.** The container entrypoint
   and the compose file were added after the first two were hardened, and both reintroduced the
   same exposure — that is the whole history of `VOYN-W0-AICC-STREAMLIT-EXPOSED-NO-AUTH`. A control
   that has to be re-derived for each new way of starting the process is one that will be missed
   again.

Meanwhile the repository already has an authenticated HTTP surface. `VOYN-W0-AICC-AUTH-HTTP-01`
built `command_center/http_auth`: authentication delegated to the platform's
`GET /api/v1/whoami`, authorization owned locally as a closed deny-by-default operation map, and
route coverage checked against the router tree at boot. Every mutating route across `command_center.api`
and `command_center.webapi` sits behind it. The `web/` SPA and the PySide6
desktop client are the clients in front of it, and `MASTER_PRODUCT_ROADMAP.md` commits to
native-desktop and web as the product direction with Streamlit as the interim feature-complete
console.

So the question this ADR settles is not "does the console need an identity layer" — it is "which
of the three available answers do we commit to, so that the compensating control stops being
load-bearing".

## Options considered

### Option 1 — put an identity-aware reverse proxy in front of it

An authenticating proxy (oauth2-proxy, an IAP, an SSO gateway) terminates TLS, authenticates the
user, and forwards to the console.

Rejected. It buys authentication and nothing else, and this surface's problem is not only
authentication:

- **No authorization.** Streamlit renders one application for every session. An authenticated
  operator gets `git push --force`, `gh pr merge`, agent launches on any project, and the AML
  compliance panels, because there is no seam at which a capability could be withheld. The proxy
  can only answer "may this person in"; the interesting question here is "may this person do
  *this*", and `command_center/http_auth/authz.py` already answers that for the API while the
  console has no place to ask it.
- **No attributable audit.** The privileged calls take no actor. Adding one means threading a
  principal through `agent_runner`, `execution_queue`, the runtime supervisor and the git helpers —
  the same work as Option 2, so the proxy does not avoid it.
- **The bypass stays.** A proxy protects the port it fronts. The console keeps listening, and one
  `--server.address 0.0.0.0` on a different host — or a container publish widened past the proxy —
  reproduces today's exposure exactly. The control that failed is not replaced, only shadowed.
- **It is infrastructure this repository does not own or test.** The correctness of the deployment
  would live in someone else's config, outside every gate in this tree.

### Option 2 — hold the same AIOS identity boundary inside the console

Reuse `command_center/http_auth`: read the caller's bearer credential from the request headers
Streamlit exposes (`st.context.headers`), resolve it through the platform's `whoami`, and check the
resulting principal against the operation map before each privileged action.

Rejected — as a *near-term* option; it is the only credible way to make the console remotely usable
and is the fallback recorded below if the premise of Option 3 fails.

- **Coverage cannot be proven.** The HTTP boundary is safe because coverage is structural: a table
  of routes checked against the router tree at boot, so a new route without an entry fails startup.
  `app.py` has no router. Its privileged calls are ordinary function calls scattered through 3 300+
  lines of script and the `command_center/ui/` panels behind it, reached through a flat `if/elif`
  page route. "Every privileged action is behind a check" would be a claim maintained by reading,
  which is the property `routing.validate_routing` exists specifically not to rely on.
- **The session model works against the check.** Streamlit re-executes the script per interaction
  and keeps state in `st.session_state` across reruns. A credential validated when the session
  opened is not revalidated when a button is pressed twenty minutes later, and
  `command_center/http_auth/identity.py` deliberately refuses to cache precisely so that revocation
  is immediate. Preserving that property means a `whoami` round trip per rerun — on a UI that
  reruns on every keystroke in a text input.
- **It funds the wrong surface.** The cost is a second authorization implementation, with its own
  coverage argument, for the client the roadmap is replacing.

### Option 3 — no remote Streamlit; remote operation is the authenticated API plus the web/desktop clients

The console is declared a local-only, single-operator surface for the remainder of its life. It is
not given an authentication layer, and it is not deployed anywhere it could be reached off-host.
Anything that needs to be reachable is built on `command_center.api` / `command_center.webapi`
behind `command_center/http_auth`, and consumed by `web/` or the desktop client.

**Chosen.** It is the only option that removes the surface rather than wrapping it, it needs no
infrastructure this repository does not own, and it is where the product is already going: the
authenticated API exists, its clients exist, and Streamlit is documented as the interim console.
It also costs nothing today — no deployment currently exposes the console; the exposure was a
documented *invitation*, and this decision withdraws the invitation.

## Decision

1. **The Streamlit console (`app.py`) is a local-only, single-operator surface.** It will not
   receive an authentication or authorization layer, and it will not be deployed on an interface
   reachable from another host.
2. **Remote, multi-operator, and third-party access is served exclusively by the authenticated HTTP
   API** (`command_center.api`, `command_center.webapi`, behind `command_center/http_auth`) and the
   `web/` and desktop clients in front of it. A capability that needs to be remote is a reason to
   add an authenticated route, never a reason to expose the console.
3. **"Reviewed exposure" is no longer a supported configuration.** The `AML_BIND_HOST` knob is
   removed, and the documentation that advertised widening is withdrawn. Loopback is not a default
   to be overridden; it is the contract.
4. **One non-loopback bind remains sanctioned: a private container network namespace.** Inside a
   container the service must listen on `0.0.0.0` or the published port cannot reach it; the
   exposure boundary is the *publish*, which is loopback-qualified and gated. This is declared with
   `AICC_CONSOLE_PRIVATE_NAMESPACE=1` in the same artifact that publishes the port.

## Enforcement

The decision is mechanical in three places, chosen so that each covers what the others cannot see.

**In the process — `command_center/console_boundary.py`, called from `app.py` before any page is
built.** It reads the `server.address` Streamlit actually resolved, so a config file, an environment
variable and a CLI flag all arrive as the same value, and refuses to serve the session on anything
that is not loopback. This is what closes the `--server.address 0.0.0.0` hole that no file-based
gate can observe. An unset address is a refusal, not a pass: Streamlit's own default is `None`,
which binds every interface.

**In the artifacts — `docker-compose.aml.yml`.** The published port is now the literal
`127.0.0.1:${AML_PORT:-8501}:8501`, with no variable in front of the host address. Widening it is
an edit to a file that `tests/test_deployment_exposure.py` checks, so it fails in review instead of
succeeding silently in an operator's shell.

**In the gates — `tests/test_console_boundary.py` and `tests/test_deployment_exposure.py`.** The
former covers the refusal logic, the `app.py` wiring end-to-end through `AppTest`, and pins the
marker `is_serving()` reads to the Streamlit CLI's own import of it, so an upstream rename fails the
gate rather than silently disabling the control. The latter keeps the four launch paths loopback and
additionally asserts that the compose publish host is a literal, that the namespace assertion
accompanies the container's `0.0.0.0`, and that no other artifact in the tree sets that assertion
truthy.

## What this does not claim

- **The port is not closed.** `app.py` is a linear script executed per session, so the refusal is
  per session: a misconfigured deployment still accepts connections and still serves Streamlit's
  own static shell and `/_stcore/health`. What no session gets is the application. This is strictly
  more than the launch-path guards achieved and strictly less than a closed socket.
- **`AICC_CONSOLE_PRIVATE_NAMESPACE` is an operator assertion, not a verified fact.** A process
  cannot observe its host's port-publishing rules. What makes it reviewable is that it is set in
  exactly one file, that file's publish line is pinned to a literal loopback address by a test, and
  a test refuses a truthy value anywhere else. Setting it by hand alongside a widened publish
  defeats the control — deliberately visibly, in a shell command an audit can find, rather than
  invisibly through a supported knob.
- **The console remains unauthenticated.** Nothing here authenticates anyone. The decision is that
  it never will, because the surface that needs authentication is the API, which already has it.
- **This does not deprecate Streamlit.** It remains the feature-complete local console. Only its
  *remote* deployment is ruled out.

## Consequences

- Every existing deployment keeps working: none binds off-host today.
- A future requirement to operate the console remotely is a requirement to build the capability as
  an authenticated API route with a `web/`-or-desktop client, and that cost is now explicit at the
  point the requirement appears rather than absorbed by a bind-address change.
- `README.md`, `ARCHITECTURE.md` and `docs/aml/ACCEPTANCE_PACKAGE.md` no longer describe widening as
  an operator option.
- The AML container deployment is unchanged in behaviour and loses one variable (`AML_BIND_HOST`).

## Revisiting

This decision is superseded, by a new ADR, if either premise fails:

- **The API stops being able to absorb the requirement.** If a capability genuinely cannot be
  expressed as an authenticated route — and the reason is architectural, not schedule pressure —
  then Option 2 is the fallback: the same AIOS identity boundary inside the console, and it must
  arrive with a *structural* coverage argument for privileged actions, not a reviewed list. Option
  1 is not a fallback; it does not solve authorization or attribution and leaves the bypass intact.
- **Streamlit outlives the web and desktop clients as the primary interface.** The decision assumes
  Streamlit is interim. If that assumption expires, the surface deserves a real boundary rather
  than a permanent locality constraint.

## References

- Task `VOYN-W0-AICC-CONSOLE-NO-AUTH` (Wave 0, P0) — the finding this record answers.
- Task `VOYN-W0-AICC-STREAMLIT-EXPOSED-NO-AUTH` — the exposure hardening this builds on
  (`CHANGELOG.md`, "container deployment no longer exposes the console").
- Task `VOYN-W0-AICC-AUTH-HTTP-01` — the authenticated HTTP boundary this decision routes remote
  access to (`command_center/http_auth/`).
- [ADR 0008](0008-aios-first-control-plane-boundary.md) — AICC as the operator-facing control plane
  over AIOS contracts.
