"""CI must be evaluated against the exact PR head commit, and the merge must
refuse if that commit has moved since (audit D6).

Before this, `gh pr merge` was invoked plainly and `headRefOid` was never even
fetched, so an auto-merge could land a commit whose required checks were never
green — the classic stale-rollup race: read PASSING for commit A, someone pushes
B, merge B unverified. `--match-head-commit <oid>` makes GitHub refuse exactly
that.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from command_center.runtime import github


def test_pr_json_fields_request_head_oid():
    assert "headRefOid" in github._PR_JSON_FIELDS


def test_pull_request_from_json_parses_head_oid():
    pr = github.pull_request_from_json({"number": 1, "headRefOid": "9a1f3c7deadbeef"})
    assert pr.head_oid == "9a1f3c7deadbeef"


class _CapturingClient(github.GitHubClient):
    """Real client with the subprocess boundary stubbed, so we can assert the
    exact `gh` argv without invoking `gh`."""

    def __init__(self):
        super().__init__()
        self.calls: list[list[str]] = []

    def _run(self, args, *, cwd):
        self.calls.append(list(args))
        if args[:2] == ["pr", "merge"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        # the post-merge `pr view`
        return subprocess.CompletedProcess(
            args, 0, stdout=json.dumps({"number": 1, "state": "MERGED", "mergeCommit": {"oid": "m1"}}), stderr=""
        )

    def _merge_argv(self) -> list[str]:
        return next(a for a in self.calls if a[:2] == ["pr", "merge"])


def test_merge_passes_match_head_commit_when_head_oid_given(tmp_path):
    client = _CapturingClient()
    client.merge_pull_request(tmp_path, number=1, method="squash", match_head_oid="9a1f3c7")
    argv = client._merge_argv()
    assert "--match-head-commit" in argv
    assert argv[argv.index("--match-head-commit") + 1] == "9a1f3c7"


def test_merge_omits_match_head_commit_without_head_oid(tmp_path):
    client = _CapturingClient()
    client.merge_pull_request(tmp_path, number=1, method="squash")
    assert "--match-head-commit" not in client._merge_argv()


def test_fake_client_refuses_merge_when_head_moved():
    client = github.FakeGitHubClient()
    pr = client.create_pull_request(Path("/x"), base="main", head="feat/x", title="t", body="b")
    pr.head_oid = "A"
    # Head advanced to B after checks were read on A → merge must be refused.
    with pytest.raises(github.GitHubError):
        client.merge_pull_request(Path("/x"), number=pr.number, method="squash", match_head_oid="B")
    # A merge that matches the evaluated head succeeds.
    merged = client.merge_pull_request(Path("/x"), number=pr.number, method="squash", match_head_oid="A")
    assert merged.is_merged


def test_fake_client_merges_without_match_head_oid_for_backwards_compat():
    client = github.FakeGitHubClient()
    pr = client.create_pull_request(Path("/x"), base="main", head="feat/y", title="t", body="b")
    merged = client.merge_pull_request(Path("/x"), number=pr.number, method="squash")
    assert merged.is_merged
