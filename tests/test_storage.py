import json

from command_center import storage


def test_atomic_write_and_read_json_roundtrip(tmp_path):
    path = tmp_path / "sub" / "file.json"
    storage.atomic_write_json(path, {"a": 1})
    assert storage.read_json(path, None) == {"a": 1}


def test_read_json_missing_file_returns_default(tmp_path):
    assert storage.read_json(tmp_path / "missing.json", "default") == "default"


def test_read_json_corrupt_file_returns_default(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert storage.read_json(path, []) == []


def test_atomic_write_leaves_no_tmp_file_behind(tmp_path):
    storage.atomic_write_json(tmp_path / "file.json", [1, 2, 3])
    assert list(tmp_path.glob(".*tmp")) == []


def test_ensure_seeded_never_reads_sibling_example_file(tmp_path):
    """Regression test: seeding must never copy an illustrative `.example.*` file's
    fake content into the real runtime file — that would present fabricated data as
    real (caught during manual smoke testing before this suite existed)."""
    path = tmp_path / "runtime.json"
    example = tmp_path / "runtime.example.json"
    example.write_text(json.dumps({"should": "not be copied"}), encoding="utf-8")

    storage.ensure_seeded(path, [])

    assert storage.read_json(path, None) == []


def test_ensure_seeded_is_a_noop_if_file_already_exists(tmp_path):
    path = tmp_path / "runtime.json"
    storage.atomic_write_json(path, {"real": "data"})
    storage.ensure_seeded(path, {"default": "value"})
    assert storage.read_json(path, None) == {"real": "data"}


def test_append_jsonl_and_read_jsonl_preserve_order(tmp_path):
    path = tmp_path / "log.jsonl"
    storage.append_jsonl(path, {"id": "a", "v": 1})
    storage.append_jsonl(path, {"id": "a", "v": 2})
    storage.append_jsonl(path, {"id": "b", "v": 1})
    records = storage.read_jsonl(path)
    assert [record["id"] for record in records] == ["a", "a", "b"]


def test_read_jsonl_skips_corrupt_lines(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text('{"id": "a"}\nnot json\n{"id": "b"}\n', encoding="utf-8")
    records = storage.read_jsonl(path)
    assert [record["id"] for record in records] == ["a", "b"]


def test_read_jsonl_missing_file_returns_empty_list(tmp_path):
    assert storage.read_jsonl(tmp_path / "missing.jsonl") == []


def test_ensure_seeded_jsonl_creates_empty_file(tmp_path):
    path = tmp_path / "log.jsonl"
    storage.ensure_seeded_jsonl(path)
    assert path.exists()
    assert storage.read_jsonl(path) == []


def test_fold_latest_by_id_last_write_wins():
    records = [
        {"id": "a", "status": "queued"},
        {"id": "a", "status": "running"},
        {"id": "b", "status": "queued"},
        {"id": "a", "status": "completed"},
    ]
    folded = storage.fold_latest_by_id(records)
    assert folded["a"]["status"] == "completed"
    assert folded["b"]["status"] == "queued"


def test_fold_latest_by_id_ignores_records_without_id():
    records = [{"status": "queued"}, {"id": "a", "status": "completed"}]
    folded = storage.fold_latest_by_id(records)
    assert list(folded.keys()) == ["a"]


def test_resolve_data_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("AICC_DATA_DIR", str(tmp_path / "custom"))
    assert storage.resolve_data_dir(tmp_path) == tmp_path / "custom"


def test_resolve_data_dir_default_when_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("AICC_DATA_DIR", raising=False)
    assert storage.resolve_data_dir(tmp_path) == tmp_path / "data"
