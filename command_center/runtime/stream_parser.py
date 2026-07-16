"""Turns one line of `claude -p --output-format stream-json --include-partial-messages
--verbose` output into a typed event dict, for incremental persistence as a
`RunEvent`.

This module never raises on malformed input. A line that isn't valid JSON, or
is valid JSON but not the shape expected, is preserved as an `event_type` of
`"malformed"` carrying the raw text — the supervisor persists it like any other
event rather than crashing the run or silently dropping data. This is
deliberately conservative about the *shape* of a well-formed line too: the
`type` field drives classification, but an unrecognized `type` value is still
persisted (as `"unknown_type"`), not treated as an error, since the CLI's
stream-json schema is not a contract this project controls.
"""

from __future__ import annotations

import json

# `claude --output-format stream-json` message `type` values this project
# knows how to label distinctly. Anything else still gets persisted, just
# under the generic `"unknown_type"` bucket rather than one of these.
_TYPE_TO_EVENT_TYPE: dict[str, str] = {
    "system": "lifecycle",
    "stream_event": "assistant_partial",
    "assistant": "assistant_message",
    "user": "tool_result",
    "result": "result",
}


def parse_stream_line(raw_line: str) -> dict | None:
    """Classify one raw line of subprocess stdout.

    Returns None for a blank line (nothing to persist). Otherwise returns
    `{"event_type": ..., "payload": {...}}`, always JSON-serializable.
    """
    line = raw_line.rstrip("\n").rstrip("\r")
    if not line.strip():
        return None

    try:
        data = json.loads(line)
    except (json.JSONDecodeError, ValueError) as exc:
        return {"event_type": "malformed", "payload": {"raw": line, "error": str(exc)}}

    if not isinstance(data, dict):
        return {"event_type": "malformed", "payload": {"raw": line, "error": "top-level JSON value is not an object"}}

    msg_type = data.get("type")
    event_type = _TYPE_TO_EVENT_TYPE.get(msg_type, "unknown_type") if isinstance(msg_type, str) else "unknown_type"
    return {"event_type": event_type, "payload": data}


def stderr_event(line: str) -> dict:
    return {"event_type": "stderr_line", "payload": {"line": line.rstrip("\n").rstrip("\r")}}


def lifecycle_event(name: str, **details) -> dict:
    return {"event_type": "lifecycle", "payload": {"lifecycle": name, **details}}
