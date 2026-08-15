"""Fail closed unless an independent verdict accepted *this exact* head commit.

Why this exists
---------------
The delivery rules require every pull request to be accepted by a reviewer who
is not its author, on the exact commit that will be merged. GitHub cannot
enforce that here, and both built-in routes were tried and closed:

* `required_approving_review_count` with `reviewDecision == APPROVED`. The
  acceptance identity is a GitHub App installation with `contents: read`; its
  approval carries `authorAssociation: NONE` and GitHub only counts approvals
  from accounts with write access. Granting write access to the reviewer would
  dissolve the separation the rule exists to create.
* Self-approval. Every agent runs under the account that opens the pull
  request, and GitHub refuses an approval from the author at the API level.

So the verdict is published as a review whose **first line** is exactly::

    ACCEPTANCE: ACCEPT <40-character head sha>
    ACCEPTANCE: REJECT <40-character head sha>

and this gate is what makes it binding. Independence is checkable here even
though it is invisible to branch protection: the app reviews under its own
`login` (`voyn-acceptance[bot]`), so the comparison is `login` against the pull
request's author `login` — not `authorAssociation`, which is exactly the field
that made the built-in route unusable.

Everything it cannot establish is a refusal: no review, no marker, a marker for
a different commit, an unparseable sha, a missing token, an API error. A gate
that guesses accepts nothing in particular.
"""

from __future__ import annotations

import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# The verdict line. Matched with `fullmatch` against the body's *first line*
# only: a marker in the middle of a review does not count, because the
# reviewer's prose may quote, discuss or recommend a verdict, and only a body
# that opens with one is the reviewer rendering it.
MARKER = re.compile(r"ACCEPTANCE: (ACCEPT|REJECT) ([0-9a-fA-F]{40})[ \t]*")

# A review that was submitted but later dismissed no longer represents its
# author's position, so it cannot supply an acceptance. A rejection still
# blocks: withdrawing an objection is not the same as accepting.
DISMISSED = "DISMISSED"
# Not yet submitted; it is a draft visible only to its author.
PENDING = "PENDING"

_PER_PAGE = 100
_MAX_PAGES = 50
_MAX_BYTES = 8 * 1024 * 1024


class AcceptanceError(RuntimeError):
    """The pull request may not merge on the evidence available."""


class Verdict:
    """One parsed `ACCEPTANCE:` line together with who published it."""

    def __init__(self, decision: str, sha: str, author: str, state: str) -> None:
        self.decision = decision
        self.sha = sha
        self.author = author
        self.state = state


def parse_marker(body: object) -> tuple[str, str] | None:
    """Return `(decision, sha)` if the body's first line is a verdict."""
    if not isinstance(body, str) or not body:
        return None
    first_line = body.replace("\r\n", "\n").split("\n", 1)[0]
    match = MARKER.fullmatch(first_line)
    if match is None:
        return None
    return match.group(1), match.group(2).lower()


def verdicts_from(reviews: object) -> list[Verdict]:
    """Every parseable verdict in an API review list."""
    if not isinstance(reviews, list):
        raise AcceptanceError("review list is not an array")
    found = []
    for review in reviews:
        if not isinstance(review, dict):
            raise AcceptanceError("review entry is not an object")
        parsed = parse_marker(review.get("body"))
        if parsed is None:
            continue
        user = review.get("user")
        author = user.get("login") if isinstance(user, dict) else None
        state = review.get("state")
        found.append(
            Verdict(
                decision=parsed[0],
                sha=parsed[1],
                # An unattributable review can never establish independence.
                author=author if isinstance(author, str) and author else "",
                state=state if isinstance(state, str) else "",
            )
        )
    return found


def evaluate(reviews: object, head_sha: object, pull_request_author: object) -> str:
    """Return the accepting reviewer's login, or raise with cause and remedy."""
    if not isinstance(head_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", head_sha):
        raise AcceptanceError(f"head sha is not a 40-character commit id: {head_sha!r}")
    head = head_sha.lower()
    if not isinstance(pull_request_author, str) or not pull_request_author:
        raise AcceptanceError("the pull request has no resolvable author, so independence is unprovable")
    author = pull_request_author.casefold()

    verdicts = verdicts_from(reviews)
    on_head = [verdict for verdict in verdicts if verdict.sha == head and verdict.state != PENDING]

    rejections = [verdict for verdict in on_head if verdict.decision == "REJECT"]
    if rejections:
        # Checked before any acceptance: a rejection standing on the current
        # head is the reviewer's live position, and an earlier ACCEPT on the
        # same commit does not overturn it.
        who = ", ".join(sorted({verdict.author or "<unknown>" for verdict in rejections}))
        raise AcceptanceError(
            f"acceptance was REJECTED for {head} by {who}. "
            "Address the rejection, push the fix, and obtain a new verdict on the new head commit"
        )

    accepting = [
        verdict
        for verdict in on_head
        if verdict.decision == "ACCEPT"
        and verdict.state != DISMISSED
        and verdict.author
        and verdict.author.casefold() != author
    ]
    if accepting:
        return accepting[0].author

    # Nothing accepted this commit. Say which of the ways it failed, because a
    # gate whose refusal is unreadable gets routed around instead of fixed.
    self_issued = [
        verdict
        for verdict in on_head
        if verdict.decision == "ACCEPT" and verdict.author.casefold() == author
    ]
    if self_issued:
        raise AcceptanceError(
            f"the only ACCEPT for {head} was published by {self_issued[0].author}, who authored this "
            "pull request. Acceptance must come from an identity that is not the author; have the "
            "acceptance reviewer publish the verdict"
        )
    dismissed = [
        verdict
        for verdict in on_head
        if verdict.decision == "ACCEPT" and verdict.state == DISMISSED
    ]
    if dismissed:
        raise AcceptanceError(
            f"the ACCEPT for {head} was dismissed and no longer stands. Obtain a fresh verdict"
        )
    stale = sorted({verdict.sha for verdict in verdicts if verdict.sha != head})
    if stale:
        raise AcceptanceError(
            f"no verdict names the current head {head}; the newest verdict on record is for "
            f"{', '.join(stale)}. Acceptance is per commit — re-run acceptance against the current "
            "head and publish a verdict naming it"
        )
    raise AcceptanceError(
        f"no acceptance verdict for {head}. A reviewer other than the author must publish a review "
        f"whose first line is exactly `ACCEPTANCE: ACCEPT {head}`"
    )


def _api(path: str, env: dict[str, str]) -> object:
    token = env.get("GITHUB_TOKEN") or env.get("GH_TOKEN")
    if not token:
        # Without a token the verdict is unreadable, which is not the same as
        # absent, which is not the same as favourable.
        raise AcceptanceError("GITHUB_TOKEN is required to read the pull request's reviews")
    base = env.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    request = Request(  # noqa: S310 - fixed https API host from the runner environment
        f"{base}{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "aicc-acceptance-gate",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - see above
            body = response.read(_MAX_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise AcceptanceError(f"cannot read {path} from the GitHub API") from error
    if len(body) > _MAX_BYTES:
        raise AcceptanceError(f"the response for {path} is implausibly large")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise AcceptanceError(f"the response for {path} is not JSON") from error


def _pull_request_number(env: dict[str, str]) -> int:
    """The pull request under judgement, from the event payload."""
    path = env.get("GITHUB_EVENT_PATH")
    if not path:
        raise AcceptanceError("GITHUB_EVENT_PATH is not set")
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AcceptanceError("event payload is unreadable") from error
    pull_request = payload.get("pull_request") if isinstance(payload, dict) else None
    number = pull_request.get("number") if isinstance(pull_request, dict) else None
    if not isinstance(number, int):
        raise AcceptanceError(
            f"event {env.get('GITHUB_EVENT_NAME')!r} carries no pull request; this gate only "
            "judges pull requests"
        )
    return number


def _reviews(repository: str, number: int, env: dict[str, str]) -> list:
    """Every submitted review, following pagination to the end."""
    collected: list = []
    for page in range(1, _MAX_PAGES + 1):
        batch = _api(
            f"/repos/{repository}/pulls/{number}/reviews?per_page={_PER_PAGE}&page={page}",
            env,
        )
        if not isinstance(batch, list):
            raise AcceptanceError("the reviews response is not an array")
        collected.extend(batch)
        if len(batch) < _PER_PAGE:
            return collected
    raise AcceptanceError("the review list did not end within the page limit")


def assert_accepted(env: dict[str, str]) -> str:
    """Return the accepting reviewer's login, or raise `AcceptanceError`."""
    repository = env.get("GITHUB_REPOSITORY")
    if not repository:
        raise AcceptanceError("GITHUB_REPOSITORY is not set")
    number = _pull_request_number(env)

    # Re-read the pull request rather than trusting the event payload: on a
    # `pull_request_review` event the payload was assembled for the review, and
    # a head that moved between the push and this run must not be judged by a
    # verdict for the commit it replaced.
    pull_request = _api(f"/repos/{repository}/pulls/{number}", env)
    if not isinstance(pull_request, dict):
        raise AcceptanceError("the pull request response is not an object")
    head = pull_request.get("head")
    head_sha = head.get("sha") if isinstance(head, dict) else None
    user = pull_request.get("user")
    pull_request_author = user.get("login") if isinstance(user, dict) else None

    return evaluate(_reviews(repository, number, env), head_sha, pull_request_author)


def main(argv: list[str] | None = None) -> int:
    try:
        reviewer = assert_accepted(dict(os.environ))
    except AcceptanceError as error:
        print(f"acceptance gate refused: {error}", file=sys.stderr)
        return 1
    print(f"independent acceptance confirmed, published by {reviewer}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
