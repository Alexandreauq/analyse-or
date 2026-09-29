import json
import os
import threading
import time

import gold_bot.state as state


def test_load_state_returns_fallback_when_file_absent(tmp_path):
    path = str(tmp_path / "does_not_exist" / "state.json")
    result = state.load_state(path)
    assert result == {"kill_switch": False, "dry_run": True, "risk_profile": 3}


def test_load_state_returns_fallback_on_corrupted_json(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not valid json", encoding="utf-8")
    result = state.load_state(str(path))
    assert result == {"kill_switch": False, "dry_run": True, "risk_profile": 3}


def test_save_state_then_load_state_round_trips(tmp_path):
    path = str(tmp_path / "nested" / "state.json")
    state.save_state({"kill_switch": True, "dry_run": False, "risk_profile": 3}, path)
    result = state.load_state(path)
    assert result == {"kill_switch": True, "dry_run": False, "risk_profile": 3}


def test_load_state_merges_partial_data_with_defaults(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"dry_run": False}), encoding="utf-8")
    result = state.load_state(str(path))
    assert result == {"kill_switch": False, "dry_run": False, "risk_profile": 3}


def test_load_state_returns_fallback_when_json_is_not_a_dict(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    result = state.load_state(str(path))
    assert result == {"kill_switch": False, "dry_run": True, "risk_profile": 3}


def test_save_state_is_atomic_no_tmp_file_left_behind(tmp_path):
    path = str(tmp_path / "state.json")
    state.save_state({"kill_switch": True, "dry_run": False, "risk_profile": 3}, path)
    assert not os.path.exists(path + ".tmp")
    assert state.load_state(path) == {"kill_switch": True, "dry_run": False, "risk_profile": 3}


def test_save_state_with_bare_relative_filename_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state.save_state({"kill_switch": False, "dry_run": True, "risk_profile": 3}, "bare_state.json")
    assert state.load_state("bare_state.json") == {"kill_switch": False, "dry_run": True, "risk_profile": 3}


def test_load_state_merges_custom_risk_profile(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"risk_profile": 5}), encoding="utf-8")
    result = state.load_state(str(path))
    assert result == {"kill_switch": False, "dry_run": True, "risk_profile": 5}


def test_update_state_merges_field_without_clobbering_others(tmp_path):
    path = str(tmp_path / "state.json")
    state.save_state({"kill_switch": False, "dry_run": True, "risk_profile": 3}, path)

    result = state.update_state({"kill_switch": True}, path)

    assert result == {"kill_switch": True, "dry_run": True, "risk_profile": 3}
    assert state.load_state(path) == {"kill_switch": True, "dry_run": True, "risk_profile": 3}


def test_update_state_removes_lock_file_after_completing(tmp_path):
    path = str(tmp_path / "state.json")
    state.update_state({"kill_switch": True}, path)
    assert not os.path.exists(path + ".lock")


def test_update_state_recovers_from_a_stale_lock_file(tmp_path):
    path = str(tmp_path / "state.json")
    state.save_state({"kill_switch": False, "dry_run": True, "risk_profile": 3}, path)
    lock_path = path + ".lock"
    # Simule un verrou abandonné par un processus mort en le tenant :
    # fichier présent, horodatage bien plus vieux que le seuil de péremption.
    with open(lock_path, "w"):
        pass
    old_time = time.time() - state._LOCK_STALE_AFTER_SECONDS - 5
    os.utime(lock_path, (old_time, old_time))

    result = state.update_state({"kill_switch": True}, path)

    assert result["kill_switch"] is True
    assert not os.path.exists(lock_path)


def test_update_state_serializes_concurrent_writers_so_no_field_is_lost(tmp_path, monkeypatch):
    """Reproduit la course trouvée dans gold_bot.api le 2026-09-29 : deux
    requêtes admin quasi simultanées (ex: /kill puis /profile) faisaient
    chacune un load -> modifie -> save complet sans verrou -- la seconde
    écriture, partie d'un état lu avant la modification de la première,
    pouvait l'écraser silencieusement. On force le recouvrement en
    ralentissant load_state, pour que ce test échoue de façon fiable en
    l'absence du verrou plutôt que par chance selon l'ordonnancement des
    threads."""
    path = str(tmp_path / "state.json")
    state.save_state({"kill_switch": False, "dry_run": True, "risk_profile": 3}, path)
    original_load = state.load_state

    def slow_load(p=path):
        time.sleep(0.2)
        return original_load(p)

    monkeypatch.setattr(state, "load_state", slow_load)

    def writer(field, value):
        state.update_state({field: value}, path)

    t1 = threading.Thread(target=writer, args=("kill_switch", True))
    t2 = threading.Thread(target=writer, args=("dry_run", False))
    t1.start()
    time.sleep(0.05)  # laisse t1 entrer dans sa section critique en premier
    t2.start()
    t1.join()
    t2.join()

    result = original_load(path)
    assert result["kill_switch"] is True
    assert result["dry_run"] is False
