"""Persistence for the config-driven `DispatchPolicy`.

This module is the **single writer** of `data/dispatch_policy.json` (see
`docs/AUTHORITY_MAP.md`). It mirrors `pipeline_settings.py`'s primitives
exactly — atomic-replace writes guarded by a cross-process advisory file lock
via `command_center.storage` — so a read-modify-write from two sessions cannot
tear the file. No SQL, no business logic: just load/save of the policy value
object, which keeps the layering honest (Service calls the config store, the
store owns the file). Named like `pipeline_settings` — a config store, not an
engine — so it does not read as new persistence-engine growth.
"""

from __future__ import annotations

import contextlib

from pathlib import Path

from command_center import models, storage
from command_center.dispatch.models import DispatchPolicy

POLICY_FILE_NAME = "dispatch_policy.json"
POLICY_LOCK_FILE_NAME = "dispatch_policy.lock"

_LOCK_TIMEOUT_SECONDS = 30.0
_LOCK_POLL_SECONDS = 0.05


def policy_file_path(root: Path) -> Path:
    return storage.resolve_data_dir(root) / POLICY_FILE_NAME


def policy_lock_path(root: Path) -> Path:
    return storage.resolve_data_dir(root) / POLICY_LOCK_FILE_NAME


@contextlib.contextmanager
def policy_lock(root: Path, *, timeout: float = _LOCK_TIMEOUT_SECONDS):
    """Cross-process mutual exclusion for the policy read-modify-write cycle —
    the same OS advisory-lock primitive as `pipeline_settings.settings_lock`."""
    with storage.file_lock(
        policy_lock_path(root), timeout=timeout, poll_seconds=_LOCK_POLL_SECONDS
    ):
        yield


def load_policy(root: Path) -> DispatchPolicy:
    """Read the persisted policy, or the safe defaults if nothing is saved yet.
    Unlocked by design (a plain read of an atomically-written file); use
    `save_policy` / `update_policy` for anything that writes."""
    return DispatchPolicy.from_dict(storage.read_json(policy_file_path(root), {}))


def save_policy(
    root: Path, policy: DispatchPolicy, *, actor: str | None = None
) -> DispatchPolicy:
    """Persist `policy` wholesale under `policy_lock`, stamping provenance."""
    stamped = _stamp(policy, actor)
    with policy_lock(root):
        storage.atomic_write_json(policy_file_path(root), stamped.as_dict())
    return stamped


def update_policy(
    root: Path, changes: dict, *, actor: str | None = None
) -> DispatchPolicy:
    """Lost-update-safe partial update: re-read the current policy under the
    lock, overlay `changes` (validated through `DispatchPolicy.from_dict`) and
    write back — so two concurrent edits of different fields don't clobber."""
    with policy_lock(root):
        current = DispatchPolicy.from_dict(
            storage.read_json(policy_file_path(root), {})
        )
        merged = dict(current.as_dict())
        merged.update(changes or {})
        stamped = _stamp(DispatchPolicy.from_dict(merged), actor)
        storage.atomic_write_json(policy_file_path(root), stamped.as_dict())
    return stamped


def _stamp(policy: DispatchPolicy, actor: str | None) -> DispatchPolicy:
    import dataclasses

    return dataclasses.replace(
        policy, updated_at=models.iso_now(), updated_by=actor or policy.updated_by
    )
