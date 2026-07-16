import json

from command_center.runtime import stream_parser


def test_blank_line_returns_none():
    assert stream_parser.parse_stream_line("") is None
    assert stream_parser.parse_stream_line("   \n") is None
    assert stream_parser.parse_stream_line("\n") is None


def test_malformed_json_preserved_as_diagnostic_event_not_raised():
    event = stream_parser.parse_stream_line("{not valid json at all")
    assert event["event_type"] == "malformed"
    assert "raw" in event["payload"]
    assert event["payload"]["raw"] == "{not valid json at all"
    assert "error" in event["payload"]


def test_non_object_json_is_malformed():
    for raw in ("[1, 2, 3]", '"just a string"', "42", "null"):
        event = stream_parser.parse_stream_line(raw)
        assert event["event_type"] == "malformed"
        assert event["payload"]["raw"] == raw


def test_system_type_classified_as_lifecycle():
    line = json.dumps({"type": "system", "subtype": "init"})
    event = stream_parser.parse_stream_line(line)
    assert event["event_type"] == "lifecycle"
    assert event["payload"]["subtype"] == "init"


def test_stream_event_type_classified_as_assistant_partial():
    line = json.dumps({"type": "stream_event", "event": {"delta": "hi"}})
    event = stream_parser.parse_stream_line(line)
    assert event["event_type"] == "assistant_partial"


def test_assistant_type_classified_as_assistant_message():
    line = json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}})
    event = stream_parser.parse_stream_line(line)
    assert event["event_type"] == "assistant_message"


def test_user_type_classified_as_tool_result():
    line = json.dumps({"type": "user", "message": {"content": []}})
    event = stream_parser.parse_stream_line(line)
    assert event["event_type"] == "tool_result"


def test_result_type_classified_as_result():
    line = json.dumps({"type": "result", "result": "done"})
    event = stream_parser.parse_stream_line(line)
    assert event["event_type"] == "result"
    assert event["payload"]["result"] == "done"


def test_unknown_type_is_not_treated_as_malformed():
    line = json.dumps({"type": "some_future_type", "data": 1})
    event = stream_parser.parse_stream_line(line)
    assert event["event_type"] == "unknown_type"
    assert event["payload"]["data"] == 1


def test_missing_type_field_is_unknown_type_not_malformed():
    line = json.dumps({"no_type_here": True})
    event = stream_parser.parse_stream_line(line)
    assert event["event_type"] == "unknown_type"


def test_payload_always_preserves_full_original_data():
    original = {"type": "result", "result": "x" * 5000, "extra_field": {"nested": [1, 2, 3]}}
    event = stream_parser.parse_stream_line(json.dumps(original))
    assert event["payload"] == original
    assert len(event["payload"]["result"]) == 5000


def test_stderr_event_shape():
    event = stream_parser.stderr_event("some error text\n")
    assert event == {"event_type": "stderr_line", "payload": {"line": "some error text"}}


def test_lifecycle_event_shape():
    event = stream_parser.lifecycle_event("process_started", pid=123)
    assert event == {"event_type": "lifecycle", "payload": {"lifecycle": "process_started", "pid": 123}}


def test_a_run_with_mixed_malformed_and_valid_lines_preserves_every_line():
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        "not json",
        json.dumps({"type": "result", "result": "ok"}),
        "",
        "{also not json",
    ]
    events = [stream_parser.parse_stream_line(line) for line in lines]
    non_blank = [e for e in events if e is not None]
    assert len(non_blank) == 4
    assert [e["event_type"] for e in non_blank] == ["lifecycle", "malformed", "result", "malformed"]
