import json
import os

import gold_bot.state as state


def test_load_state_returns_fallback_when_file_absent(tmp_path):
    path = str(tmp_path / "does_not_exist" / "state.json")
    result = state.load_state(path)
    assert result == {"kill_switch": False, "dry_run": True}


def test_load_state_returns_fallback_on_corrupted_json(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not valid json", encoding="utf-8")
    result = state.load_state(str(path))
    assert result == {"kill_switch": False, "dry_run": True}


def test_save_state_then_load_state_round_trips(tmp_path):
    path = str(tmp_path / "nested" / "state.json")
    state.save_state({"kill_switch": True, "dry_run": False}, path)
    result = state.load_state(path)
    assert result == {"kill_switch": True, "dry_run": False}


def test_load_state_merges_partial_data_with_defaults(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"dry_run": False}), encoding="utf-8")
    result = state.load_state(str(path))
    assert result == {"kill_switch": False, "dry_run": False}


def test_load_state_returns_fallback_when_json_is_not_a_dict(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    result = state.load_state(str(path))
    assert result == {"kill_switch": False, "dry_run": True}


def test_save_state_is_atomic_no_tmp_file_left_behind(tmp_path):
    path = str(tmp_path / "state.json")
    state.save_state({"kill_switch": True, "dry_run": False}, path)
    assert not os.path.exists(path + ".tmp")
    assert state.load_state(path) == {"kill_switch": True, "dry_run": False}


def test_save_state_with_bare_relative_filename_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state.save_state({"kill_switch": False, "dry_run": True}, "bare_state.json")
    assert state.load_state("bare_state.json") == {"kill_switch": False, "dry_run": True}
