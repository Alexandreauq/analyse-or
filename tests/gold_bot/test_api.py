import pytest
from fastapi.testclient import TestClient

import gold_bot.api as api
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
