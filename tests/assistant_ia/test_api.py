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


def test_ask_passes_the_requested_level_to_the_assistant_loop(client, monkeypatch):
    niveaux_recus = []

    def fake_loop(question, history, manual_positions=None, niveau=None, **kwargs):
        niveaux_recus.append(niveau)
        yield {"type": "fin"}

    monkeypatch.setattr(api.assistant, "run_assistant_loop", fake_loop)
    monkeypatch.setattr(api.usage, "budget_exceeded", lambda **kwargs: False)

    client.post(
        "/ask", json={"question": "Bonjour", "history": [], "niveau": "grand_public"},
        headers={"X-Bot-Token": "secret-token"})

    assert niveaux_recus == ["grand_public"]


def test_ask_refuses_a_question_that_is_too_long(client, monkeypatch):
    monkeypatch.setattr(api.usage, "budget_exceeded", lambda **kwargs: False)
    monkeypatch.setattr(api, "_frequence", api.limits.RateLimiter(100, 60))

    response = client.post(
        "/ask", json={"question": "x" * (api.MAX_QUESTION_CHARS + 1), "history": []},
        headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 400


def test_ask_refuses_an_empty_question(client, monkeypatch):
    monkeypatch.setattr(api.usage, "budget_exceeded", lambda **kwargs: False)
    monkeypatch.setattr(api, "_frequence", api.limits.RateLimiter(100, 60))

    response = client.post("/ask", json={"question": "   ", "history": []},
                           headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 400


def test_ask_rate_limits_each_visitor(client, monkeypatch):
    monkeypatch.setattr(api.usage, "budget_exceeded", lambda **kwargs: False)
    monkeypatch.setattr(api, "_frequence", api.limits.RateLimiter(2, 60))

    def fake_loop(question, history, manual_positions=None, **kwargs):
        yield {"type": "fin"}

    monkeypatch.setattr(api.assistant, "run_assistant_loop", fake_loop)
    entetes = {"X-Bot-Token": "secret-token"}
    codes = [client.post("/ask", json={"question": "Q", "history": []}, headers=entetes).status_code
             for _ in range(3)]

    assert codes == [200, 200, 429]


def test_ask_answers_503_when_too_many_requests_are_running(client, monkeypatch):
    monkeypatch.setattr(api.usage, "budget_exceeded", lambda **kwargs: False)
    monkeypatch.setattr(api, "_frequence", api.limits.RateLimiter(100, 60))
    monkeypatch.setattr(api, "_concurrence", api.limits.ConcurrencyLimiter(1))
    api._concurrence.tente()  # une requête déjà en cours occupe la seule place

    response = client.post("/ask", json={"question": "Q", "history": []},
                           headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 503


def test_ask_truncates_the_history_sent_to_the_assistant(client, monkeypatch):
    monkeypatch.setattr(api.usage, "budget_exceeded", lambda **kwargs: False)
    monkeypatch.setattr(api, "_frequence", api.limits.RateLimiter(100, 60))
    recus = []

    def fake_loop(question, history, manual_positions=None, **kwargs):
        recus.append(list(history))
        yield {"type": "fin"}

    monkeypatch.setattr(api.assistant, "run_assistant_loop", fake_loop)
    historique = [{"role": "user", "content": str(i)} for i in range(40)]
    client.post("/ask", json={"question": "Q", "history": historique}, headers={"X-Bot-Token": "secret-token"})

    assert len(recus[0]) == api.MAX_HISTORY_MESSAGES
    assert recus[0][-1]["content"] == "39"


def test_financial_analysis_returns_the_subprocess_result(client, monkeypatch):
    monkeypatch.setattr(api, "_generate_one_subprocess",
                         lambda ticker: {"html": "<p>Analyse</p>", "genere": True})

    response = client.get("/financial-analysis/AAA")

    assert response.status_code == 200
    assert response.json() == {"html": "<p>Analyse</p>", "disponible": True}


def test_financial_analysis_reports_unavailable_when_nothing_was_generated(client, monkeypatch):
    monkeypatch.setattr(api, "_generate_one_subprocess",
                         lambda ticker: {"html": None, "genere": False})

    response = client.get("/financial-analysis/AAA")

    assert response.status_code == 200
    assert response.json() == {"html": None, "disponible": False}


def test_financial_analysis_reports_unavailable_on_subprocess_timeout(client, monkeypatch):
    import subprocess as sp

    def _leve_timeout(ticker):
        raise sp.TimeoutExpired(cmd="indices_score.py", timeout=150)

    monkeypatch.setattr(api, "_generate_one_subprocess", _leve_timeout)

    response = client.get("/financial-analysis/AAA")

    assert response.status_code == 200
    assert response.json() == {"html": None, "disponible": False}


def test_financial_analysis_reports_unavailable_on_malformed_subprocess_output(client, monkeypatch):
    def _sortie_invalide(ticker):
        raise ValueError("JSON invalide")

    monkeypatch.setattr(api, "_generate_one_subprocess", _sortie_invalide)

    response = client.get("/financial-analysis/AAA")

    assert response.status_code == 200
    assert response.json() == {"html": None, "disponible": False}


def test_generate_one_subprocess_uses_the_current_interpreter(monkeypatch):
    appels = []

    class _FakeResultat:
        stdout = '{"html": null, "genere": false}'

    def _fake_run(cmd, **kwargs):
        appels.append(cmd)
        return _FakeResultat()

    monkeypatch.setattr(api.subprocess, "run", _fake_run)

    api._generate_one_subprocess("AAA")

    assert appels[0][0] == api.sys.executable


def test_financial_analysis_rate_limits_each_visitor(client, monkeypatch):
    monkeypatch.setattr(api, "_generate_one_subprocess",
                         lambda ticker: {"html": "<p>x</p>", "genere": False})
    monkeypatch.setattr(api, "_frequence_analyse", api.limits.RateLimiter(2, 60))

    codes = [client.get("/financial-analysis/AAA").status_code for _ in range(3)]

    assert codes == [200, 200, 429]


def test_financial_analysis_rate_limits_by_visitor_not_by_ticker(client, monkeypatch):
    monkeypatch.setattr(api, "_generate_one_subprocess",
                         lambda ticker: {"html": "<p>x</p>", "genere": False})
    monkeypatch.setattr(api, "_frequence_analyse", api.limits.RateLimiter(2, 60))

    tickers = ["AAA", "BBB", "CCC"]
    codes = [client.get(f"/financial-analysis/{t}").status_code for t in tickers]

    assert codes == [200, 200, 429]


def test_financial_analysis_answers_503_when_a_generation_is_already_running(client, monkeypatch):
    monkeypatch.setattr(api, "_frequence_analyse", api.limits.RateLimiter(100, 60))
    monkeypatch.setattr(api, "_concurrence_analyse", api.limits.ConcurrencyLimiter(1))
    api._concurrence_analyse.tente()  # une génération déjà en cours occupe la seule place

    response = client.get("/financial-analysis/AAA")

    assert response.status_code == 503
