import subprocess
import time

from command_center.runtime import identity


def test_capture_identity_for_running_process():
    proc = subprocess.Popen(["sleep", "2"])
    try:
        ident = identity.capture_identity(proc.pid)
        assert ident is not None
        assert ident.pid == proc.pid
        assert "sleep" in ident.command
        assert ident.start_time
    finally:
        proc.terminate()
        proc.wait()


def test_capture_identity_returns_none_for_dead_process():
    proc = subprocess.Popen(["true"])
    proc.wait()
    time.sleep(0.2)
    assert identity.capture_identity(proc.pid) is None


def test_process_exists_true_while_running_false_after_exit():
    proc = subprocess.Popen(["sleep", "1"])
    try:
        assert identity.process_exists(proc.pid) is True
    finally:
        proc.terminate()
        proc.wait()
    time.sleep(0.2)
    assert identity.process_exists(proc.pid) is False


def test_identity_matches_true_for_same_running_process():
    proc = subprocess.Popen(["sleep", "2"])
    try:
        recorded = identity.capture_identity(proc.pid).as_string()
        assert identity.identity_matches(proc.pid, recorded) is True
    finally:
        proc.terminate()
        proc.wait()


def test_identity_matches_false_when_recorded_identity_is_stale_text():
    proc = subprocess.Popen(["sleep", "2"])
    try:
        assert identity.identity_matches(proc.pid, "bogus start time|bogus command") is False
    finally:
        proc.terminate()
        proc.wait()


def test_identity_matches_false_when_process_gone():
    proc = subprocess.Popen(["true"])
    proc.wait()
    time.sleep(0.2)
    assert identity.identity_matches(proc.pid, "anything") is False


def test_identity_matches_false_when_recorded_identity_missing():
    proc = subprocess.Popen(["sleep", "2"])
    try:
        assert identity.identity_matches(proc.pid, None) is False
        assert identity.identity_matches(proc.pid, "") is False
    finally:
        proc.terminate()
        proc.wait()


def test_capture_identity_handles_invalid_pid_gracefully():
    assert identity.capture_identity(-1) is None
