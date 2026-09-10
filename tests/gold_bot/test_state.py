import json

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
