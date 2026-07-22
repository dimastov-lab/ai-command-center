# Task-package import — integration contract

Backend module: `command_center/task_import.py` (zero Streamlit coupling).
CLI: `scripts/import_tasks.py`. Example package: `data/tasks_import.example.json`.

This document is the contract for wiring the import backend into a UI (the
Streamlit Create-Task page in `app.py`) **without any change to `app.py` being
made by the backend task** — `app.py` is owned by another workstream and is a
protected file here. A UI task picks this up and applies the small diff at the
end.

## Pipeline (four pure functions, disk only where noted)

```
parse_task_package(raw, *, filename=None, fmt=None, max_bytes=MAX_PACKAGE_BYTES)
    bytes|str -> ParsedPackage         # never touches disk; raises TaskImportError
validate_task_package(parsed)
    ParsedPackage -> ValidationResult  # never touches disk
build_import_preview(root, parsed, validation, *, allow_unresolved_dependencies=False)
    -> ImportPreview                   # ONE read-only load of the store; never writes
apply_task_package(root, parsed, validation, *, lock_timeout=..., allow_unresolved_dependencies=False)
    -> ImportResult                    # load->merge->save inside the shared tasks lock
```

`root` is the repository root `Path` (the same one every other call site uses).
Every write goes through `tasks_repository.mutate_tasks` under the shared
`tasks_repository.tasks_lock` — the importer is **not** a second task store.

## Guarantees

- **Formats:** JSON, YAML, Markdown (YAML front-matter or a fenced
  `json`/`yaml` block), and plain text (sniffed JSON-then-YAML). Format is
  chosen by explicit `fmt` → file extension → content sniff. All formats
  converge on the same `ParsedPackage`, so validation/preview/apply are
  format-agnostic. YAML uses `yaml.safe_load` only.
- **Shapes:** a bare list of task objects **or** an envelope
  `{"schema_version", "package_id", "tasks": [...]}`. A bare list gets a
  content-hash-derived `package_id`.
- **Single or many tasks** — a one-element list/array is valid.
- **Strict validation** with per-item references (`id`, else `#<n>`): required
  fields, id uniqueness inside the package, `status`/`priority`/`task_type`
  vocabularies, `depends_on` shape, and project normalization.
- **Dedup vs. store:** a task whose `id` already exists is skipped, never
  duplicated or overwritten. Re-importing a package is idempotent.
- **Dependencies:** an unresolved `depends_on` (absent from both package and
  store) is a **blocking error** by default; opt in with
  `allow_unresolved_dependencies=True` to downgrade to a warning.
- **Dry-run:** `build_import_preview` writes nothing; the UI shows counts +
  per-task rows and only offers "Import" when `preview.has_blocking_errors` is
  `False`.
- **Atomic apply / rollback:** all new tasks are written in one `save_tasks`
  call (tempfile + `os.replace`) inside the shared lock. On any error nothing
  is written and existing tasks are untouched — no partial batch.
- **Unknown fields** (`source`, `parallel_group`, `target_version`, …) are
  preserved under `record["metadata"]`, never silently dropped.
- **Oversized input** (> `MAX_PACKAGE_BYTES`, default 5 MiB) is rejected before
  parsing. Corrupt JSON/YAML and a non-list/non-dict root raise
  `TaskImportError`.
- **Result:** `ImportResult(imported_ids, skipped_duplicate_ids, warnings)`;
  `ImportPreview(new_items, duplicate_ids, errors, warnings, rows, ...)`.

## Data classes (import from `command_center.task_import`)

`TaskImportError`, `ParsedPackage`, `ValidationResult`, `ImportPreview`,
`PackageTaskRow`, `ImportResult`, `ImportIssue`. Public constants:
`SCHEMA_VERSION`, `SUPPORTED_IMPORT_SUFFIXES` (for `st.file_uploader(type=...)`),
`MAX_PACKAGE_BYTES`.

## CLI (already wired, no protected files)

```
python scripts/import_tasks.py data/tasks_import.example.json --dry-run
python scripts/import_tasks.py PACKAGE.(json|yaml|md|txt) --apply [--format yaml] \
    [--allow-unresolved-dependencies]
```

Exit code `2` on parse failure or blocking validation errors; `0` on success.

## UI wiring for the next task (apply to `app.py` — NOT done here)

The Create-Task page already calls `task_import.parse_task_package(...)`. To
enable multi-format upload, the owning task applies exactly this change to the
"Импорт пакета задач" block (kept out of this branch on purpose):

```python
# st.file_uploader
uploaded_package = st.file_uploader(
    "Пакет задач (JSON / YAML / Markdown / текст)",
    type=list(task_import.SUPPORTED_IMPORT_SUFFIXES),
    key="import_task_package_uploader",
)
# parse call — pass the filename so the extension selects the parser
parsed_package = task_import.parse_task_package(
    uploaded_package.getvalue(), filename=uploaded_package.name
)
```

No other change is required: `preview`/`apply` are already format-agnostic, and
`parse_task_package` with no `filename` stays byte-for-byte backward compatible
with the current JSON-only call site.
