"""GitHub adapter for the autonomous completion pipeline — pull-request
discovery, creation, and merge via the `gh` CLI.

This is the first `gh` integration in the codebase; the pre-existing surface
only ever *parsed* a PR URL an agent wrote into its report. Everything here
runs as a fixed argv list with `shell=False` and an explicit timeout. PR bodies
are written to a temporary file and passed via `--body-file`, never
interpolated into a command line.

The pipeline's single most important correctness rule lives in
`PullRequestState`: **a closed PR is never treated as merged just because its
state is not OPEN.** `is_merged` requires an actual merge signal (`state ==
MERGED`, or a `merged_at`/`merge_commit`); `is_closed_unmerged` is the distinct,
recoverable case (`CLOSED`, no merge signal).

`GitHubClient` is the real adapter; `FakeGitHubClient` is an in-memory double
with the identical interface, used by the tests and the deterministic
demonstration scenarios so neither touches real GitHub.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_DEFAULT_TIMEOUT = 60

STATE_OPEN = "OPEN"
STATE_CLOSED = "CLOSED"
STATE_MERGED = "MERGED"

CHECKS_PASSING = "PASSING"
CHECKS_FAILING = "FAILING"
CHECKS_PENDING = "PENDING"
CHECKS_NONE = "NONE"

MERGE_METHODS = ("squash", "merge", "rebase")

_PR_JSON_FIELDS = (
    "number,url,state,mergedAt,mergeCommit,mergeable,"
    "statusCheckRollup,reviewDecision,headRefName,headRefOid,baseRefName,title"
)


class GitHubError(Exception):
    """A `gh` invocation failed. Carries the argv and captured stderr for the
    audit trail — never tokens or environment."""

    def __init__(self, args: list[str], returncode: int | None, stderr: str) -> None:
        self.args = args
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"gh {' '.join(args)} failed (rc={returncode}): {stderr.strip()[:400]}")


@dataclass
class PullRequestState:
    """Normalized view of a pull request. Distinguishes merged from
    closed-unmerged unambiguously (see module docstring)."""

    number: int | None = None
    url: str | None = None
    state: str | None = None  # OPEN / CLOSED / MERGED
    merged_at: str | None = None
    merge_commit: str | None = None
    mergeable: str | None = None  # MERGEABLE / CONFLICTING / UNKNOWN
    checks_state: str = CHECKS_NONE  # PASSING / FAILING / PENDING / NONE
    review_decision: str | None = None  # APPROVED / REVIEW_REQUIRED / CHANGES_REQUESTED / None
    head_ref: str | None = None
    head_oid: str | None = None  # exact head commit the checks/review belong to (audit D6)
    base_ref: str | None = None
    title: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def is_merged(self) -> bool:
        return self.state == STATE_MERGED or bool(self.merged_at) or bool(self.merge_commit)

    @property
    def is_open(self) -> bool:
        return self.state == STATE_OPEN

    @property
    def is_closed_unmerged(self) -> bool:
        """CLOSED with no merge signal whatsoever — the recoverable case."""
        return self.state == STATE_CLOSED and not self.merged_at and not self.merge_commit

    @property
    def checks_passing(self) -> bool:
        return self.checks_state == CHECKS_PASSING

    @property
    def checks_failing(self) -> bool:
        return self.checks_state == CHECKS_FAILING

    @property
    def checks_pending(self) -> bool:
        return self.checks_state == CHECKS_PENDING

    @property
    def review_satisfied(self) -> bool:
        """No review is outstanding: either the repo requires none
        (`review_decision` empty) or it is APPROVED. CHANGES_REQUESTED and
        REVIEW_REQUIRED are both unsatisfied."""
        return self.review_decision in (None, "", "APPROVED")


def _classify_checks(rollup: Any) -> str:
    """Reduce a `statusCheckRollup` list to one of PASSING/FAILING/PENDING/NONE.

    Handles both CheckRun items (`status`/`conclusion`) and StatusContext items
    (`state`). Any failure dominates; else any pending dominates; else if there
    were checks at all they passed."""
    if not isinstance(rollup, list) or not rollup:
        return CHECKS_NONE
    failing = False
    pending = False
    for item in rollup:
        if not isinstance(item, dict):
            continue
        conclusion = (item.get("conclusion") or "").upper()
        status = (item.get("status") or "").upper()
        state = (item.get("state") or "").upper()
        if conclusion in ("FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE") or state in (
            "FAILURE",
            "ERROR",
        ):
            failing = True
        elif status in ("QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED") or state == "PENDING":
            pending = True
        elif not conclusion and not state and status and status != "COMPLETED":
            pending = True
    if failing:
        return CHECKS_FAILING
    if pending:
        return CHECKS_PENDING
    return CHECKS_PASSING


def pull_request_from_json(data: dict) -> PullRequestState:
    """Build a `PullRequestState` from one `gh pr view/list --json` object."""
    merge_commit = data.get("mergeCommit")
    if isinstance(merge_commit, dict):
        merge_commit = merge_commit.get("oid") or merge_commit.get("abbreviatedOid")
    return PullRequestState(
        number=data.get("number"),
        url=data.get("url"),
        state=(data.get("state") or None),
        merged_at=data.get("mergedAt") or None,
        merge_commit=merge_commit or None,
        mergeable=data.get("mergeable") or None,
        checks_state=_classify_checks(data.get("statusCheckRollup")),
        review_decision=data.get("reviewDecision") or None,
        head_ref=data.get("headRefName") or None,
        head_oid=data.get("headRefOid") or None,
        base_ref=data.get("baseRefName") or None,
        title=data.get("title") or None,
        raw=data,
    )


def _select_pull_request(prs: list[dict]) -> dict | None:
    """From candidate PRs for a branch, pick the most decision-relevant one:
    an OPEN PR first, then a MERGED one, else the most recent CLOSED one."""
    if not prs:
        return None
    for pr in prs:
        if (pr.get("state") or "").upper() == STATE_OPEN:
            return pr
    for pr in prs:
        if (pr.get("state") or "").upper() == STATE_MERGED or pr.get("mergedAt"):
            return pr
    return prs[0]


class GitHubClient:
    """Real `gh`-CLI-backed adapter. All methods operate in the given repo
    directory (`cwd`), so the correct GitHub repo is inferred from its
    `origin` remote — no repo slug is ever hardcoded."""

    def __init__(self, *, gh_binary: str = "gh", timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._gh = gh_binary
        self._timeout = timeout

    def _run(self, args: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                [self._gh, *args],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise GitHubError(args, None, str(exc)) from exc

    def is_available(self) -> bool:
        """Whether the `gh` CLI is installed and authenticated."""
        try:
            version = self._run(["--version"], cwd=Path.cwd())
        except GitHubError:
            return False
        if version.returncode != 0:
            return False
        auth = self._run(["auth", "status"], cwd=Path.cwd())
        return auth.returncode == 0

    def discover_pull_request(
        self, repo: Path, *, branch: str, base: str | None = None
    ) -> PullRequestState | None:
        """Find the most relevant PR whose head is `branch`, across all states.
        Returns None when no PR exists (a first-class outcome, not an error)."""
        args = [
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            "all",
            "--limit",
            "20",
            "--json",
            _PR_JSON_FIELDS,
        ]
        if base:
            args.extend(["--base", base])
        proc = self._run(args, cwd=repo)
        if proc.returncode != 0:
            raise GitHubError(args, proc.returncode, proc.stderr or "")
        try:
            prs = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise GitHubError(args, proc.returncode, f"unparseable json: {exc}") from exc
        selected = _select_pull_request(prs if isinstance(prs, list) else [])
        return pull_request_from_json(selected) if selected else None

    def create_pull_request(
        self, repo: Path, *, base: str, head: str, title: str, body: str
    ) -> PullRequestState:
        """Create a PR from `head` into `base`. The body is written to a temp
        file and passed with `--body-file` — never interpolated into argv."""
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
            handle.write(body)
            body_path = handle.name
        try:
            args = [
                "pr",
                "create",
                "--base",
                base,
                "--head",
                head,
                "--title",
                title,
                "--body-file",
                body_path,
            ]
            proc = self._run(args, cwd=repo)
            if proc.returncode != 0:
                raise GitHubError(args, proc.returncode, proc.stderr or "")
        finally:
            try:
                Path(body_path).unlink()
            except OSError:  # pragma: no cover
                pass
        # `gh pr create` prints the new PR URL; re-discover for full metadata.
        discovered = self.discover_pull_request(repo, base=base, branch=head)
        if discovered is not None:
            return discovered
        url = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else None
        return PullRequestState(url=url, state=STATE_OPEN, head_ref=head, base_ref=base)

    def merge_pull_request(
        self, repo: Path, *, number: int, method: str = "squash", match_head_oid: str | None = None
    ) -> PullRequestState:
        """Merge PR `number` using `method` (squash/merge/rebase). Never passes
        `--delete-branch` (branches are only removed after target-branch
        verification, which this pipeline never does automatically).

        When `match_head_oid` is given, `--match-head-commit <oid>` makes GitHub
        refuse the merge unless the PR head is still exactly that commit — the
        one whose required checks/review were evaluated (audit D6). This closes
        the stale-rollup race: if a new commit was pushed after the checks were
        read, the merge fails instead of landing an unverified head."""
        if method not in MERGE_METHODS:
            raise ValueError(f"Unknown merge method: {method!r}")
        args = ["pr", "merge", str(number), f"--{method}"]
        if match_head_oid:
            args += ["--match-head-commit", match_head_oid]
        proc = self._run(args, cwd=repo)
        if proc.returncode != 0:
            raise GitHubError(args, proc.returncode, proc.stderr or "")
        # Re-read the PR to capture the merge commit.
        args_view = ["pr", "view", str(number), "--json", _PR_JSON_FIELDS]
        view = self._run(args_view, cwd=repo)
        if view.returncode == 0 and view.stdout:
            try:
                return pull_request_from_json(json.loads(view.stdout))
            except json.JSONDecodeError:  # pragma: no cover
                pass
        return PullRequestState(number=number, state=STATE_MERGED)


class FakeGitHubClient:
    """In-memory GitHub double with the same interface as `GitHubClient`, for
    tests and the deterministic demonstration scenarios.

    PRs are keyed by head branch. `on_merge(pr)` is an optional hook invoked
    when a PR is merged, so a test can perform the *real* git merge into a
    local remote — making target-branch verification exercise real git rather
    than a stub. This keeps the demonstrations honest: only the GitHub API is
    faked, the repository state is real."""

    def __init__(self, *, available: bool = True, on_merge: Callable[[PullRequestState], str | None] | None = None):
        self._available = available
        self._on_merge = on_merge
        self._by_branch: dict[str, PullRequestState] = {}
        self._next_number = 1
        self.created: list[PullRequestState] = []
        self.merged: list[int] = []

    def is_available(self) -> bool:
        return self._available

    def seed(self, pr: PullRequestState) -> PullRequestState:
        """Test helper: register a PR (e.g. a pre-existing closed-unmerged one)."""
        if pr.number is None:
            pr.number = self._next_number
            self._next_number += 1
        if pr.head_ref:
            self._by_branch[pr.head_ref] = pr
        return pr

    def discover_pull_request(
        self, repo: Path, *, branch: str, base: str | None = None
    ) -> PullRequestState | None:
        return self._by_branch.get(branch)

    def create_pull_request(
        self, repo: Path, *, base: str, head: str, title: str, body: str
    ) -> PullRequestState:
        pr = PullRequestState(
            number=self._next_number,
            url=f"https://github.com/local/repo/pull/{self._next_number}",
            state=STATE_OPEN,
            mergeable="MERGEABLE",
            checks_state=CHECKS_PASSING,
            head_ref=head,
            base_ref=base,
            title=title,
        )
        self._next_number += 1
        self._by_branch[head] = pr
        self.created.append(pr)
        return pr

    def merge_pull_request(
        self, repo: Path, *, number: int, method: str = "squash", match_head_oid: str | None = None
    ) -> PullRequestState:
        if method not in MERGE_METHODS:
            raise ValueError(f"Unknown merge method: {method!r}")
        pr = next((p for p in self._by_branch.values() if p.number == number), None)
        if pr is None:
            raise GitHubError(["pr", "merge", str(number)], 1, "no such PR")
        # Mirror GitHub's `--match-head-commit`: refuse if the head moved since
        # the checks/review were evaluated on `match_head_oid` (audit D6).
        if match_head_oid is not None and pr.head_oid is not None and match_head_oid != pr.head_oid:
            raise GitHubError(
                ["pr", "merge", str(number), "--match-head-commit", match_head_oid],
                1,
                "head commit moved since checks were evaluated",
            )
        merge_commit = None
        if self._on_merge is not None:
            merge_commit = self._on_merge(pr)
        pr.state = STATE_MERGED
        pr.merged_at = "2026-07-22T12:00:00"
        pr.merge_commit = merge_commit or pr.merge_commit or f"mergecommit{number}"
        self.merged.append(number)
        return pr
