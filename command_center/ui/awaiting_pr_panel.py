"""Панель «Готово — ожидает слияния».

Сюда попадают все выполненные задачи у которых нет открытого пулреквеста
(агент завершился успешно, но ветка совпадает с основной — reason NO_PR_NOT_IN_TARGET).

Три кнопки на задачу:
  1. Создать ПР   — создаёт ветку (если нужно) и открывает ПР в GitHub
  2. Тесты        — запускает команды валидации, показывает светофор 🔴🟡🟢,
                    при красном — автоматически запускает агента-исправителя
  3. Влить        — проверяет актуальность основной ветки, затем сливает;
                    если основная ветка обновилась — предупреждает и блокирует
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable

import streamlit as st

from command_center import models, project_config, tasks_repository
from command_center.runtime import db as runtime_db, validation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tasks_awaiting_pr(tasks: list[dict], *, project: str | None = None) -> list[dict]:
    result = [
        t for t in tasks
        if t.get("launch_status") == "Awaiting PR"
        and (project is None or t.get("project") == project)
    ]
    result.sort(key=lambda t: t.get("updated_at") or "", reverse=True)
    return result


def _run(cmd: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)


def _current_head(repo: str) -> str:
    r = _run(["git", "rev-parse", "HEAD"], cwd=repo)
    return r.stdout.strip() if r.returncode == 0 else ""


def _remote_main_head(repo: str, remote: str = "origin", base: str = "main") -> str:
    _run(["git", "fetch", remote, base], cwd=repo)
    r = _run(["git", "rev-parse", f"{remote}/{base}"], cwd=repo)
    return r.stdout.strip() if r.returncode == 0 else ""


def _branch_exists_remote(repo: str, branch: str, remote: str = "origin") -> bool:
    r = _run(["git", "ls-remote", "--heads", remote, branch], cwd=repo)
    return bool(r.stdout.strip())


def _pr_url_for_branch(repo: str, branch: str) -> str | None:
    r = _run(["gh", "pr", "view", branch, "--json", "url", "--jq", ".url"], cwd=repo)
    url = r.stdout.strip()
    return url if r.returncode == 0 and url.startswith("http") else None


def _create_feature_branch(repo: str, task_id: str, head_commit: str) -> str:
    """Create a feature branch at `head_commit` and return its name.
    If it already exists locally, reuse it."""
    branch = f"aicc/{task_id.lower().replace('_', '-')}"
    r = _run(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repo)
    if r.returncode != 0:
        _run(["git", "branch", branch, head_commit or "HEAD"], cwd=repo)
    return branch


# ---------------------------------------------------------------------------
# Session-state keys (per-task)
# ---------------------------------------------------------------------------

def _sk(task_id: str, suffix: str) -> str:
    return f"awaiting_pr_{task_id}_{suffix}"


def _get_pr_url(task: dict) -> str | None:
    return task.get("pull_request_url") or st.session_state.get(_sk(task["id"], "pr_url"))


def _set_pr_url(task_id: str, url: str) -> None:
    st.session_state[_sk(task_id, "pr_url")] = url


def _get_test_result(task_id: str) -> str | None:
    """'green' | 'red' | 'running' | None"""
    return st.session_state.get(_sk(task_id, "test_result"))


def _set_test_result(task_id: str, result: str) -> None:
    st.session_state[_sk(task_id, "test_result")] = result


def _get_test_detail(task_id: str) -> str:
    return st.session_state.get(_sk(task_id, "test_detail"), "")


def _set_test_detail(task_id: str, detail: str) -> None:
    st.session_state[_sk(task_id, "test_detail")] = detail


def _get_autofix_count(task_id: str) -> int:
    return int(st.session_state.get(_sk(task_id, "autofix_count"), 0))


def _set_autofix_count(task_id: str, n: int) -> None:
    st.session_state[_sk(task_id, "autofix_count")] = n


MAX_AUTOFIX_ATTEMPTS = 3


def _create_autofix_task(
    task: dict,
    root: Path,
    failure_summary: str,
    save_tasks_fn: Callable[[list[dict]], None],
) -> str:
    """Create a new implementation task to fix failing tests and return its id."""
    import uuid
    parent_id = task["id"]
    attempt = _get_autofix_count(parent_id) + 1
    new_id = f"{parent_id}-FIX-{attempt:02d}"
    parent_title = task.get("title") or parent_id
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    fix_task: dict = {
        "id": new_id,
        "title": f"Auto-fix: {parent_title[:60]} — fix failing tests (attempt {attempt})",
        "description": (
            f"Auto-generated fix task for {parent_id} (attempt {attempt}/{MAX_AUTOFIX_ATTEMPTS}).\n\n"
            "The following tests failed — fix them without breaking passing tests:\n\n"
            f"```\n{failure_summary[:2000]}\n```\n\n"
            f"Repository: {task.get('repository_path') or 'see parent task'}"
        ),
        "task_type": "implementation",
        "status": "Next",
        "launch_status": "Ready",
        "project": task.get("project"),
        "branch": task.get("branch"),
        "repository_path": task.get("repository_path"),
        "executor": task.get("executor"),
        "model": task.get("model"),
        "parent_task_id": parent_id,
        "created_at": now,
        "updated_at": now,
        "timeline": [],
        "tags": ["auto-fix"],
    }

    def _add(tasks: list[dict]) -> list[dict]:
        if not any(t["id"] == new_id for t in tasks):
            tasks.append(fix_task)
        return tasks

    tasks_repository.mutate_tasks(root, _add)
    return new_id


# ---------------------------------------------------------------------------
# Action: Create PR
# ---------------------------------------------------------------------------

def _action_create_pr(
    task: dict,
    root: Path,
    save_fn: Callable[[list[dict]], None],
    completion: dict | None,
) -> None:
    repo = task.get("repository_path") or ""
    if not repo:
        st.error("Нет repository_path в задаче.")
        return

    task_id = task["id"]
    base_branch = (
        (completion or {}).get("base_branch")
        or task.get("base_branch")
        or "main"
    )
    run_branch = (completion or {}).get("branch") or task.get("branch") or ""
    head_commit = (completion or {}).get("head_commit") or _current_head(repo)

    # If the run was on the base branch directly, create a feature branch.
    if not run_branch or run_branch == base_branch:
        branch = _create_feature_branch(repo, task_id, head_commit)
    else:
        branch = run_branch

    with st.spinner(f"Пушу ветку `{branch}`…"):
        push_r = _run(["git", "push", "origin", f"{branch}:{branch}"], cwd=repo)
        if push_r.returncode != 0:
            st.error(f"git push failed:\n```\n{push_r.stderr[:800]}\n```")
            return

    # Check if PR already exists.
    existing_url = _pr_url_for_branch(repo, branch)
    if existing_url:
        _set_pr_url(task_id, existing_url)
        tasks_repository.set_manual_launch_status(
            root, task_id, "Awaiting PR",
            f"PR уже существует: {existing_url}"
        )
        st.success(f"PR уже открыт: {existing_url}")
        return

    title = task.get("title") or task_id
    body = (
        f"Автоматически создан через AICC панель Awaiting PR.\n\n"
        f"**Задача:** `{task_id}`\n"
        f"**Тип:** {task.get('task_type') or task.get('type') or 'implementation'}\n"
    )
    with st.spinner("Создаю PR…"):
        pr_r = _run(
            ["gh", "pr", "create",
             "--base", base_branch,
             "--head", branch,
             "--title", title,
             "--body", body],
            cwd=repo,
        )
    if pr_r.returncode != 0:
        st.error(f"gh pr create failed:\n```\n{pr_r.stderr[:800]}\n```")
        return

    url = pr_r.stdout.strip()
    _set_pr_url(task_id, url)
    tasks_repository.set_manual_launch_status(
        root, task_id, "Awaiting PR",
        f"PR создан: {url}"
    )
    st.success(f"PR создан: {url}")
    st.rerun()


# ---------------------------------------------------------------------------
# Action: Run Tests
# ---------------------------------------------------------------------------

def _action_run_tests(
    task: dict,
    root: Path,
    project_configs: dict,
    save_tasks_fn: Callable[[list[dict]], None] | None = None,
) -> None:
    task_id = task["id"]
    repo = task.get("repository_path") or ""
    proj = task.get("project") or ""
    cfg = project_configs.get(proj) or {}

    plan = validation.resolve_validation_plan(task=task, project_cfg=cfg)
    if not plan:
        st.info("Команды валидации не настроены для этого проекта. Тесты считаются пройденными.")
        _set_test_result(task_id, "green")
        _set_test_detail(task_id, "Нет validation_commands — авто-зелёный.")
        return

    _set_test_result(task_id, "running")
    with st.spinner("Запускаю тесты…"):
        outcome = validation.run_validation(repo, plan)

    if outcome.passed:
        _set_test_result(task_id, "green")
        _set_test_detail(task_id, outcome.summary())
        _set_autofix_count(task_id, 0)  # reset counter on success
    else:
        _set_test_result(task_id, "red")
        _set_test_detail(task_id, outcome.summary())

        attempt = _get_autofix_count(task_id)
        if attempt >= MAX_AUTOFIX_ATTEMPTS:
            st.error(
                f"🔴 Тесты красные — автоисправление исчерпано ({MAX_AUTOFIX_ATTEMPTS}/{MAX_AUTOFIX_ATTEMPTS} попыток). "
                "Проверьте задачи вручную."
            )
        elif save_tasks_fn is not None:
            fix_id = _create_autofix_task(task, root, outcome.summary(), save_tasks_fn)
            _set_autofix_count(task_id, attempt + 1)
            st.warning(
                f"🔴 Тесты красные — создана задача **{fix_id}** для автоисправления "
                f"(попытка {attempt + 1}/{MAX_AUTOFIX_ATTEMPTS}). "
                "После её завершения нажмите **Тесты** ещё раз."
            )
        else:
            st.warning(
                "🔴 Тесты красные. Исправьте ошибки и нажмите **Тесты** ещё раз.\n\n"
                f"```\n{outcome.summary()[:800]}\n```"
            )


# ---------------------------------------------------------------------------
# Action: Merge
# ---------------------------------------------------------------------------

def _action_merge(
    task: dict,
    root: Path,
    save_fn: Callable[[list[dict]], None],
) -> None:
    task_id = task["id"]
    repo = task.get("repository_path") or ""
    pr_url = _get_pr_url(task)
    if not pr_url:
        st.error("Нет PR для мержа.")
        return

    # Check main currency: fetch and compare.
    with st.spinner("Проверяю актуальность main…"):
        remote_head = _remote_main_head(repo)
        local_merge_base = ""
        if remote_head:
            r = _run(["git", "merge-base", "HEAD", remote_head], cwd=repo)
            local_merge_base = r.stdout.strip() if r.returncode == 0 else ""

    if remote_head and local_merge_base and local_merge_base != remote_head:
        st.warning(
            "⚠️ Main обновился с момента последнего прогона тестов. "
            "**Запусти тесты ещё раз** перед мержем чтобы убедиться "
            "что изменения совместимы."
        )
        _set_test_result(task_id, None)
        return

    with st.spinner("Мержу PR…"):
        merge_r = _run(
            ["gh", "pr", "merge", pr_url, "--squash", "--delete-branch"],
            cwd=repo,
        )
    if merge_r.returncode != 0:
        st.error(f"gh pr merge failed:\n```\n{merge_r.stderr[:800]}\n```")
        return

    tasks_repository.set_manual_launch_status(
        root, task_id, "Done",
        f"PR смержен: {pr_url}"
    )
    st.session_state.pop(_sk(task_id, "pr_url"), None)
    st.session_state.pop(_sk(task_id, "test_result"), None)
    st.success(f"Смержено! Задача переведена в Done.")
    save_fn(tasks_repository.load_tasks(root))
    st.rerun()


# ---------------------------------------------------------------------------
# Traffic-light widget
# ---------------------------------------------------------------------------

_LIGHT = {"green": "🟢", "red": "🔴", "running": "🟡", None: "⬜"}


def _render_traffic_light(task_id: str) -> None:
    result = _get_test_result(task_id)
    detail = _get_test_detail(task_id)
    emoji = _LIGHT.get(result, "⬜")
    label = {
        "green": "Тесты проходят",
        "red": "Тесты падают",
        "running": "Запускаю…",
        None: "Тесты не запускались",
    }.get(result, "")
    st.caption(f"{emoji} {label}" + (f" — {detail[:120]}" if detail else ""))


# ---------------------------------------------------------------------------
# Run metadata line (когда завершено / из какой ветки / какой коммит)
# ---------------------------------------------------------------------------

def _format_completed_at(raw: str | None) -> str:
    """ISO timestamp → `07.08.2026 09:46`. Нераспознанное значение возвращаем
    как есть — лучше показать сырую строку, чем потерять её."""
    if not raw:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return dt.strftime("%d.%m.%Y %H:%M")


def _render_run_meta(task: dict, completion: dict | None) -> None:
    comp = completion or {}
    when = _format_completed_at(task.get("last_run_at") or comp.get("updated_at"))
    branch = task.get("branch") or comp.get("branch") or ""
    commit = task.get("commit_hash") or comp.get("head_commit") or ""

    parts: list[str] = []
    if when:
        parts.append(f"Завершено: {when}")
    if branch:
        parts.append(f"ветка: {branch}")
    if commit:
        parts.append(f"коммит: {str(commit)[:7]}")
    if parts:
        st.caption(" • ".join(parts))


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render_awaiting_pr_panel(
    tasks: list[dict],
    root: Path,
    save_tasks_fn: Callable[[list[dict]], None],
    db_path: Path | None = None,
    *,
    project: str | None = None,
    key_prefix: str = "awaiting_pr",
) -> None:
    pending = _tasks_awaiting_pr(tasks, project=project)
    if not pending:
        return

    project_configs = project_config.load_project_configs()

    st.markdown("#### Готово — ожидает слияния")
    st.caption(
        f"{len(pending)} задач выполнены агентом — нужен пулреквест для доставки в основную ветку."
    )

    # ---- bulk "select all" + bulk Done ----
    col_all, col_bulk_done, _ = st.columns([1, 2.5, 5.5])
    if col_all.button("Все", key=f"{key_prefix}_select_all"):
        for t in pending:
            st.session_state[f"{key_prefix}_sel_{t['id']}"] = True

    selected = [
        t["id"] for t in pending
        if st.session_state.get(f"{key_prefix}_sel_{t['id']}")
    ]
    if col_bulk_done.button(
        f"✓ Выполнено ({len(selected)})" if selected else "✓ Выполнено",
        key=f"{key_prefix}_bulk_done",
        disabled=not selected,
    ):
        for tid in selected:
            tasks_repository.set_manual_launch_status(
                root, tid, "Done",
                "Bulk Done из панели Awaiting PR."
            )
            st.session_state.pop(f"{key_prefix}_sel_{tid}", None)
        st.rerun()

    st.divider()

    for task in pending:
        tid = task["id"]
        repo = task.get("repository_path") or ""

        # Fetch completion record if we have a db_path.
        completion: dict | None = None
        if db_path:
            run_id = task.get("current_run_id")
            if run_id:
                completion = runtime_db.get_completion(db_path, run_id)

        pr_url = _get_pr_url(task)
        test_result = _get_test_result(tid)

        # ---- row header ----
        sel_col, info_col = st.columns([0.5, 8])
        with sel_col:
            st.checkbox("", key=f"{key_prefix}_sel_{tid}", label_visibility="collapsed")
        with info_col:
            proj = task.get("project") or ""
            ttype = task.get("task_type") or task.get("type") or ""
            st.markdown(f"**{task.get('title') or tid}**  `{proj}`  _{ttype}_")

        # ---- run metadata + traffic light ----
        _render_run_meta(task, completion)
        _render_traffic_light(tid)

        # ---- three action buttons ----
        btn1, btn2, btn3, _ = st.columns([2, 2.5, 2, 2.5])

        # Кнопка 1: Создать ПР
        with btn1:
            if pr_url:
                st.link_button("🔗 ПР открыт", pr_url, use_container_width=True)
            else:
                if st.button(
                    "📤 Создать ПР",
                    key=f"{key_prefix}_create_pr_{tid}",
                    use_container_width=True,
                    help="Создать ветку и открыть пулреквест в GitHub",
                ):
                    _action_create_pr(task, root, save_tasks_fn, completion)

        # Кнопка 2: Тесты (доступна только после создания ПР)
        with btn2:
            tests_disabled = not pr_url
            if st.button(
                "🧪 Тесты",
                key=f"{key_prefix}_run_tests_{tid}",
                use_container_width=True,
                disabled=tests_disabled,
                help="Запустить тесты и показать результат" if not tests_disabled
                     else "Сначала создайте ПР",
            ):
                _action_run_tests(task, root, project_configs, save_tasks_fn=save_tasks_fn)
                st.rerun()

        # Кнопка 3: Влить (доступна только при зелёных тестах)
        with btn3:
            merge_disabled = test_result != "green" or not pr_url
            merge_help = (
                "Проверит актуальность основной ветки и выполнит слияние"
                if not merge_disabled
                else ("Сначала запустите тесты (нужны 🟢)"
                      if pr_url else "Сначала создайте ПР")
            )
            if st.button(
                "🚀 Влить",
                key=f"{key_prefix}_merge_{tid}",
                use_container_width=True,
                disabled=merge_disabled,
                type="primary" if not merge_disabled else "secondary",
                help=merge_help,
            ):
                _action_merge(task, root, save_tasks_fn)

        st.divider()
