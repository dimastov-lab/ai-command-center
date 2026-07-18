from command_center.runtime import db, log_tail


def _make_run(path):
    task = db.create_task(path, project="AIOS", title="t", task_type="implementation")
    session = db.create_session(path, task_id=task["id"], project="AIOS", repository_path="/tmp/x")
    return db.create_run(
        path,
        session_id=session["id"],
        task_id=task["id"],
        project="AIOS",
        task_type="implementation",
        repository_path="/tmp/x",
        prompt="p",
        is_resume=False,
    )


def test_tail_events_bounded_to_max_lines_and_in_order(tmp_path):
    path = tmp_path / "runtime.db"
    db.migrate(path)
    run = _make_run(path)
    for i in range(250):
        db.append_run_event(path, run["id"], "stdout_line", {"line": f"line {i}"})

    events = log_tail.tail_events(path, run["id"], max_lines=200)
    assert len(events) == 200
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)
    # The last 200 of 250 lines — i.e. lines 50..249 — never the first 200.
    assert events[0]["payload"]["line"] == "line 50"
    assert events[-1]["payload"]["line"] == "line 249"


def test_tail_events_empty_run_returns_empty_list(tmp_path):
    path = tmp_path / "runtime.db"
    db.migrate(path)
    run = _make_run(path)
    assert log_tail.tail_events(path, run["id"]) == []


def test_render_log_lines_matches_seq_event_type_and_payload_preview(tmp_path):
    path = tmp_path / "runtime.db"
    db.migrate(path)
    run = _make_run(path)
    db.append_run_event(path, run["id"], "stderr_line", {"line": "boom"})
    events = log_tail.tail_events(path, run["id"])
    lines = log_tail.render_log_lines(events)
    assert len(lines) == 1
    assert "stderr_line" in lines[0]
    assert "boom" in lines[0]


def test_latest_event_returns_single_most_recent_row(tmp_path):
    path = tmp_path / "runtime.db"
    db.migrate(path)
    run = _make_run(path)
    db.append_run_event(path, run["id"], "stdout_line", {"line": "first"})
    db.append_run_event(path, run["id"], "stdout_line", {"line": "second"})
    latest = log_tail.latest_event(path, run["id"])
    assert latest["payload"]["line"] == "second"


def test_latest_event_none_when_no_events(tmp_path):
    path = tmp_path / "runtime.db"
    db.migrate(path)
    run = _make_run(path)
    assert log_tail.latest_event(path, run["id"]) is None


def test_session_timeline_returns_only_lifecycle_events_in_order(tmp_path):
    path = tmp_path / "runtime.db"
    db.migrate(path)
    run = _make_run(path)
    db.append_run_event(path, run["id"], "lifecycle", {"lifecycle": "process_started"})
    db.append_run_event(path, run["id"], "stdout_line", {"line": "noise"})
    db.append_run_event(path, run["id"], "stderr_line", {"line": "more noise"})
    db.append_run_event(path, run["id"], "lifecycle", {"lifecycle": "process_exited"})

    timeline = log_tail.session_timeline(path, run["id"])
    assert [e["payload"]["lifecycle"] for e in timeline] == ["process_started", "process_exited"]


def test_session_timeline_empty_when_no_lifecycle_events(tmp_path):
    path = tmp_path / "runtime.db"
    db.migrate(path)
    run = _make_run(path)
    db.append_run_event(path, run["id"], "stdout_line", {"line": "just noise"})
    assert log_tail.session_timeline(path, run["id"]) == []
