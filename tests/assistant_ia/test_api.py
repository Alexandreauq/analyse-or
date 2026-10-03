import pytest
from fastapi.testclient import TestClient

import assistant_ia.api as api


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AI_ASSISTANT_API_TOKEN", "secret-token")
    return TestClient(api.app)


def test_ask_rejects_a_missing_token(client):
    response = client.post("/ask", json={"question": "Bonjour", "history": []})

    assert response.status_code == 401


def test_ask_rejects_a_wrong_token(client):
    response = client.post(
        "/ask", json={"question": "Bonjour", "history": []},
        headers={"X-Bot-Token": "mauvais"})

    assert response.status_code == 401


def test_ask_streams_events_from_the_assistant_loop(client, monkeypatch):
    def fake_loop(question, history, manual_positions=None, **kwargs):
        yield {"type": "texte", "texte": "Bonjour !"}
        yield {"type": "fin"}

    monkeypatch.setattr(api.assistant, "run_assistant_loop", fake_loop)
    monkeypatch.setattr(api.usage, "budget_exceeded", lambda **kwargs: False)

    response = client.post(
        "/ask", json={"question": "Bonjour", "history": []},
        headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 200
    assert "Bonjour !" in response.text
    assert "\"type\": \"fin\"" in response.text or '"type":"fin"' in response.text.replace(" ", "")


def test_ask_refuses_when_the_daily_budget_is_exceeded(client, monkeypatch):
    monkeypatch.setattr(api.usage, "budget_exceeded", lambda **kwargs: True)

    response = client.post(
        "/ask", json={"question": "Bonjour", "history": []},
        headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 429
