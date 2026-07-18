import subprocess

from command_center import os_actions


def test_platform_name_darwin(monkeypatch):
    monkeypatch.setattr(os_actions.sys, "platform", "darwin")
    assert os_actions.platform_name() == "macos"


def test_platform_name_windows(monkeypatch):
    monkeypatch.setattr(os_actions.sys, "platform", "win32")
    assert os_actions.platform_name() == "windows"


def test_platform_name_other(monkeypatch):
    monkeypatch.setattr(os_actions.sys, "platform", "linux")
    assert os_actions.platform_name() == "linux"


def test_reveal_in_file_manager_missing_path(tmp_path):
    ok, message = os_actions.reveal_in_file_manager(tmp_path / "nope")
    assert ok is False
    assert message


def test_open_terminal_at_missing_path(tmp_path):
    ok, message = os_actions.open_terminal_at(tmp_path / "nope")
    assert ok is False
    assert message


def test_non_darwin_platform_fails_closed_without_touching_subprocess(monkeypatch, tmp_path):
    """Never even attempts a subprocess call on an unsupported platform —
    the guard must come before any `subprocess.run`, not after a failure."""
    monkeypatch.setattr(os_actions.sys, "platform", "linux")
    calls = []
    monkeypatch.setattr(os_actions.subprocess, "run", lambda *a, **k: calls.append((a, k)))

    ok, message = os_actions.reveal_in_file_manager(tmp_path)
    assert ok is False and "macOS" in message

    ok, message = os_actions.open_terminal_at(tmp_path)
    assert ok is False and "macOS" in message

    ok, message = os_actions.copy_to_clipboard("x")
    assert ok is False and "macOS" in message

    assert calls == []


def test_reveal_in_file_manager_success_never_opens_real_finder(monkeypatch, tmp_path):
    monkeypatch.setattr(os_actions.sys, "platform", "darwin")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(os_actions.subprocess, "run", fake_run)
    ok, message = os_actions.reveal_in_file_manager(tmp_path)
    assert ok is True
    assert captured["command"] == ["open", str(tmp_path)]


def test_open_terminal_at_success_never_opens_real_terminal(monkeypatch, tmp_path):
    monkeypatch.setattr(os_actions.sys, "platform", "darwin")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(os_actions.subprocess, "run", fake_run)
    ok, message = os_actions.open_terminal_at(tmp_path)
    assert ok is True
    assert captured["command"] == ["open", "-a", "Terminal", str(tmp_path)]


def test_copy_to_clipboard_success_never_touches_real_clipboard(monkeypatch):
    monkeypatch.setattr(os_actions.sys, "platform", "darwin")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(os_actions.subprocess, "run", fake_run)
    ok, message = os_actions.copy_to_clipboard("hello")
    assert ok is True
    assert captured["command"] == ["pbcopy"]
    assert captured["input"] == "hello"


def test_reveal_in_file_manager_subprocess_error_fails_cleanly(monkeypatch, tmp_path):
    monkeypatch.setattr(os_actions.sys, "platform", "darwin")

    def fake_run(command, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr(os_actions.subprocess, "run", fake_run)
    ok, message = os_actions.reveal_in_file_manager(tmp_path)
    assert ok is False
    assert "boom" in message
