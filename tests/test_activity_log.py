from command_center import activity_log, storage


def test_log_event_and_load_activity_newest_first(tmp_path, monkeypatch):
    """`ts` has only second precision, so this appends explicit distinct timestamps
    rather than relying on two real-time `log_event` calls landing in different
    seconds (which would make the test flaky)."""
    monkeypatch.setattr(activity_log, "ACTIVITY_FILE", tmp_path / "activity.jsonl")
    storage.append_jsonl(activity_log.ACTIVITY_FILE, {"id": "e1", "ts": "2026-01-01T09:00:00", "type": "run_queued"})
    storage.append_jsonl(activity_log.ACTIVITY_FILE, {"id": "e2", "ts": "2026-01-01T09:00:05", "type": "run_completed"})

    events = activity_log.load_activity()
    assert len(events) == 2
    assert events[0]["type"] == "run_completed"
    assert events[1]["type"] == "run_queued"


def test_log_event_truncates_long_messages(tmp_path, monkeypatch):
    monkeypatch.setattr(activity_log, "ACTIVITY_FILE", tmp_path / "activity.jsonl")
    event = activity_log.log_event("message_added", message="x" * 5000)
    assert len(event["message"]) == 500


def test_load_activity_respects_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(activity_log, "ACTIVITY_FILE", tmp_path / "activity.jsonl")
    for i in range(10):
        activity_log.log_event("message_added", message=f"event {i}")
    events = activity_log.load_activity(limit=3)
    assert len(events) == 3
