import pytest
from fastapi.testclient import TestClient

import gold_bot.api as api
import gold_bot.loop as loop
import gold_bot.state as state


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("BOT_API_TOKEN", "secret-token")
    return TestClient(api.app)


def test_status_returns_current_state(client):
    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()
    assert body["kill_switch"] is False
    assert body["dry_run"] is True


def test_kill_requires_valid_token(client):
    response = client.post("/kill", headers={"X-Bot-Token": "wrong-token"})
    assert response.status_code == 401


def test_kill_without_token_header_is_rejected(client):
    response = client.post("/kill")
    assert response.status_code == 401


def test_kill_sets_kill_switch_true(client):
    response = client.post("/kill", headers={"X-Bot-Token": "secret-token"})
    assert response.status_code == 200
    assert response.json()["kill_switch"] is True
    assert client.get("/status").json()["kill_switch"] is True


def test_resume_sets_kill_switch_false(client):
    client.post("/kill", headers={"X-Bot-Token": "secret-token"})
    response = client.post("/resume", headers={"X-Bot-Token": "secret-token"})
    assert response.status_code == 200
    assert response.json()["kill_switch"] is False


def test_resume_requires_valid_token(client):
    response = client.post("/resume", headers={"X-Bot-Token": "wrong-token"})
    assert response.status_code == 401


def test_status_reports_circuit_breaker_fields_from_separate_file(client, monkeypatch, tmp_path):
    cb_path = str(tmp_path / "circuit_breaker_state.json")
    monkeypatch.setattr(api, "CIRCUIT_BREAKER_STATE_PATH", cb_path)
    api.state.save_state(
        {"circuit_breaker_day": "2026-09-10", "circuit_breaker_starting_balance": 10000.0},
        cb_path,
    )
    response = client.get("/status")
    body = response.json()
    assert body["circuit_breaker_day"] == "2026-09-10"


def test_kill_rejected_when_bot_api_token_not_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.delenv("BOT_API_TOKEN", raising=False)
    test_client = TestClient(api.app)
    response = test_client.post("/kill", headers={"X-Bot-Token": "anything"})
    assert response.status_code == 401


def test_rejected_kill_does_not_mutate_state(client):
    client.post("/kill", headers={"X-Bot-Token": "secret-token"})
    client.post("/resume", headers={"X-Bot-Token": "wrong-token"})
    status = client.get("/status").json()
    assert status["kill_switch"] is True  # inchangé malgré la tentative rejetée


def test_broker_module_is_never_referenced_in_api_source():
    import inspect
    source = inspect.getsource(api)
    assert "broker" not in source


def test_kill_rejected_not_crashed_on_non_ascii_token(client):
    # Header envoyé comme octets latin-1 bruts (ce que le serveur reçoit
    # réellement sur le fil) plutôt qu'un `str` Python — httpx (client de
    # test) refuse lui-même d'encoder un `str` non-ASCII en en-tête HTTP
    # avant même que la requête ne parte, ce qui ne reproduirait pas le
    # scénario visé (un octet non-ASCII arrivant côté serveur).
    response = client.post("/kill", headers={"X-Bot-Token": "tokén".encode("latin-1")})
    assert response.status_code == 401


def test_circuit_breaker_state_path_matches_loop_module():
    assert api.CIRCUIT_BREAKER_STATE_PATH == loop.CIRCUIT_BREAKER_STATE_PATH
