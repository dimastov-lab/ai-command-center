# AI Command Center Program Roadmap Package

Files:

- `program_roadmap.json` — canonical dependency graph and full planning horizon.
- `PROGRAM_ROADMAP.md` — human-readable roadmap.
- `import_program_roadmap.py` — safe, duplicate-aware import into `data/tasks.json`.
- `ready_tasks.py` — computes tasks whose dependencies are completed and can optionally prepare worktrees.

## Install

```bash
cd ~/Projects/ai-command-center
python3 /path/to/import_program_roadmap.py --repo "$PWD" --dry-run
python3 /path/to/import_program_roadmap.py --repo "$PWD"
```

## See tasks ready now

```bash
python3 /path/to/ready_tasks.py
```

## Prepare worktrees for ready tasks

```bash
python3 /path/to/ready_tasks.py --prepare-worktrees
```

The worktree command deliberately skips repository paths that do not yet exist.
Before launching agents, review those paths and update the roadmap if your local repository layout differs.
