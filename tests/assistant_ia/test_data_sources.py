# tests/assistant_ia/test_data_sources.py
from datetime import datetime, timezone

import pytest

import assistant_ia.data_sources as data_sources


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


def test_fetch_indices_data_calls_the_public_url():
    appels = []

    def fake_get(url, timeout=None):
        appels.append(url)
        return _FakeResponse(json_data={"companies": []})

    data_sources.fetch_indices_data(http_get=fake_get, cache={})

    assert appels == ["https://alexandreauq.github.io/analyse-or/indices.json"]


def test_fetch_indices_data_reuses_a_fresh_cache_entry():
    appels = []

    def fake_get(url, timeout=None):
        appels.append(url)
        return _FakeResponse(json_data={"companies": ["premier_appel"]})

    cache = {}
    horloge = {"t": 1000.0}
    premier = data_sources.fetch_indices_data(http_get=fake_get, now_fn=lambda: horloge["t"], cache=cache)
    horloge["t"] += 60.0  # bien avant les 600s de TTL
    second = data_sources.fetch_indices_data(http_get=fake_get, now_fn=lambda: horloge["t"], cache=cache)

    assert len(appels) == 1  # un seul appel reseau, le second est servi par le cache
    assert premier == second == {"companies": ["premier_appel"]}


def test_fetch_indices_data_refetches_after_the_cache_ttl_expires():
    appels = []

    def fake_get(url, timeout=None):
        appels.append(url)
        return _FakeResponse(json_data={"companies": [f"appel_{len(appels)}"]})

    cache = {}
    horloge = {"t": 1000.0}
    data_sources.fetch_indices_data(http_get=fake_get, now_fn=lambda: horloge["t"], cache=cache)
    horloge["t"] += 700.0  # au-dela des 600s de TTL
    data_sources.fetch_indices_data(http_get=fake_get, now_fn=lambda: horloge["t"], cache=cache)

    assert len(appels) == 2


def test_fetch_bot_dashboard_or_uses_the_gold_bot_url_and_token(monkeypatch):
    monkeypatch.setenv("BOT_API_TOKEN", "secret-or")
    appels = []

    def fake_get(url, headers=None, timeout=None):
        appels.append((url, headers))
        return _FakeResponse(json_data={"balance": 981.45})

    resultat = data_sources.fetch_bot_dashboard("or", http_get=fake_get)

    assert appels == [("https://goldbot.fr:8443/dashboard", {"X-Bot-Token": "secret-or"})]
    assert resultat == {"balance": 981.45}


def test_fetch_bot_dashboard_actions_uses_the_ibkr_bot_url_and_token(monkeypatch):
    monkeypatch.setenv("IBKR_BOT_API_TOKEN", "secret-actions")
    appels = []

    def fake_get(url, headers=None, timeout=None):
        appels.append((url, headers))
        return _FakeResponse(json_data={"balance": 20.0})

    data_sources.fetch_bot_dashboard("actions", http_get=fake_get)

    assert appels == [("https://goldbot.fr:8444/dashboard", {"X-Bot-Token": "secret-actions"})]


def test_fetch_bot_dashboard_degrades_on_network_failure(monkeypatch):
    monkeypatch.setenv("BOT_API_TOKEN", "secret-or")

    def fake_get(url, headers=None, timeout=None):
        raise RuntimeError("timeout reseau")

    resultat = data_sources.fetch_bot_dashboard("or", http_get=fake_get)

    assert "erreur" in resultat


def test_fetch_bot_dashboard_rejects_an_unknown_bot_name():
    with pytest.raises(ValueError):
        data_sources.fetch_bot_dashboard("inconnu", http_get=lambda *a, **k: None)


def test_fetch_finnhub_sends_the_token_in_a_header_never_in_the_url(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_TOKEN", "secret-finnhub")
    appels = []

    def fake_get(url, params=None, headers=None, timeout=None):
        appels.append((url, params, headers))
        return _FakeResponse(json_data=[])

    data_sources.fetch_finnhub_company_news(
        "AAPL", "2026-10-01", "2026-10-03", http_get=fake_get, cache={})

    url, params, headers = appels[0]
    assert headers == {"X-Finnhub-Token": "secret-finnhub"}
    assert "secret-finnhub" not in url
    assert params == {"symbol": "AAPL", "from": "2026-10-01", "to": "2026-10-03"}


def test_fetch_finnhub_refuses_without_a_token(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_TOKEN", raising=False)

    resultat = data_sources.fetch_finnhub_company_news(
        "AAPL", "2026-10-01", "2026-10-03", http_get=lambda *a, **k: None, cache={})

    assert "FINNHUB_API_TOKEN" in resultat["erreur"]


def test_fetch_gdelt_sends_the_window_and_needs_no_key(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_TOKEN", raising=False)
    appels = []

    def fake_get(url, params=None, headers=None, timeout=None):
        appels.append(params)
        return _FakeResponse(json_data={"articles": []})

    debut = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    data_sources.fetch_gdelt_articles("\"Safran\"", debut, 7200, "k", http_get=fake_get, cache={})

    assert appels[0]["query"] == '"Safran"'
    assert appels[0]["startdatetime"] == "20261001120000"
    assert appels[0]["format"] == "json"


def test_fetch_gdelt_reuses_a_fresh_cache_entry():
    appels = []

    def fake_get(url, params=None, headers=None, timeout=None):
        appels.append(1)
        return _FakeResponse(json_data={"articles": []})

    cache = {}
    debut = datetime(2026, 10, 1, tzinfo=timezone.utc)
    data_sources.fetch_gdelt_articles("q", debut, 3600, "marche", http_get=fake_get,
                                      now_fn=lambda: 1000.0, cache=cache)
    data_sources.fetch_gdelt_articles("q", debut, 3600, "marche", http_get=fake_get,
                                      now_fn=lambda: 1100.0, cache=cache)

    assert len(appels) == 1


def test_fetch_gdelt_treats_the_rate_limit_text_as_an_error_and_never_caches_it():
    class _TexteBrut(_FakeResponse):
        def json(self):
            raise ValueError("Please limit requests to one every 5 seconds")

    cache = {}
    resultat = data_sources.fetch_gdelt_articles(
        "q", datetime(2026, 10, 1, tzinfo=timezone.utc), 3600, "marche",
        http_get=lambda *a, **k: _TexteBrut(), cache=cache)

    assert resultat["erreur"].startswith("actualites indisponibles (")
    assert cache == {}


def test_fetch_news_error_never_leaks_the_finnhub_token(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_TOKEN", "secret-finnhub")

    def fake_get(url, params=None, headers=None, timeout=None):
        raise RuntimeError("HTTP 429 pour https://finnhub.io/api/v1/company-news?token=secret-finnhub")

    resultat = data_sources.fetch_finnhub_company_news(
        "AAPL", "2026-10-01", "2026-10-03", http_get=fake_get, cache={})

    assert "secret-finnhub" not in resultat["erreur"]
    assert resultat["erreur"] == "actualites indisponibles (RuntimeError)"
