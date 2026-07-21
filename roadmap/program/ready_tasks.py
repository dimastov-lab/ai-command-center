#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


TERMINAL_STATES = {"Completed", "Done", "Merged", "Accepted"}


def run(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"$ {' '.join(args)}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Show or prepare all currently-ready roadmap tasks.")
    parser.add_argument("--roadmap", default=str(Path(__file__).with_name("program_roadmap.json")))
    parser.add_argument("--prepare-worktrees", action="store_true")
    args = parser.parse_args()

    roadmap = json.loads(Path(args.roadmap).read_text(encoding="utf-8"))
    tasks = roadmap["tasks"]
    status = {task["id"]: task.get("status", "Backlog") for task in tasks}

    ready = [
        task for task in tasks
        if task.get("status") not in TERMINAL_STATES
        and all(status.get(dep) in TERMINAL_STATES for dep in task.get("depends_on", []))
    ]

    print(f"Ready tasks: {len(ready)}")
    for task in ready:
        print(f"- {task['id']}: {task['title']} [{task['parallel_group']}]")
        print(f"  repo: {task['repository_path']}")
        print(f"  worktree: {task['workspace_path']}")
        print(f"  branch: {task['branch']}")

    if not args.prepare_worktrees:
        return

    for task in ready:
        repo = Path(task["repository_path"])
        worktree = Path(task["workspace_path"])
        if not repo.exists():
            print(f"SKIP {task['id']}: repository does not exist: {repo}")
            continue
        if worktree.exists():
            print(f"OK {task['id']}: worktree already exists")
            continue

        run("git", "fetch", "origin", cwd=repo)
        run(
            "git", "worktree", "add",
            "-b", task["branch"],
            str(worktree),
            "origin/main",
            cwd=repo,
        )
        print(f"CREATED {task['id']}: {worktree}")


if __name__ == "__main__":
    main()
