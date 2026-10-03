# tests/assistant_ia/test_data_sources.py
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


def test_fetch_marketaux_news_sends_the_token_in_the_query_string(monkeypatch):
    monkeypatch.setenv("MARKETAUX_API_TOKEN", "secret-marketaux")
    appels = []

    def fake_get(url, params=None, timeout=None):
        appels.append((url, params))
        return _FakeResponse(json_data={"data": []})

    data_sources.fetch_marketaux_news(
        {"symbols": "MC.PA"}, 7200, "entreprise:MC.PA", http_get=fake_get, cache={})

    url, params = appels[0]
    assert url == "https://api.marketaux.com/v1/news/all"
    assert params["api_token"] == "secret-marketaux"
    assert params["symbols"] == "MC.PA"


def test_fetch_marketaux_news_refuses_without_a_token(monkeypatch):
    monkeypatch.delenv("MARKETAUX_API_TOKEN", raising=False)
    appels = []

    resultat = data_sources.fetch_marketaux_news(
        {"symbols": "MC.PA"}, 7200, "k", http_get=lambda *a, **k: appels.append(1), cache={})

    assert "erreur" in resultat
    assert appels == []  # aucun appel réseau sans token


def test_fetch_marketaux_news_reuses_a_fresh_cache_entry(monkeypatch):
    monkeypatch.setenv("MARKETAUX_API_TOKEN", "t")
    appels = []

    def fake_get(url, params=None, timeout=None):
        appels.append(1)
        return _FakeResponse(json_data={"data": [{"title": "x"}]})

    cache = {}
    horloge = {"t": 1000.0}
    data_sources.fetch_marketaux_news({"symbols": "MC.PA"}, 7200, "k",
                                      http_get=fake_get, now_fn=lambda: horloge["t"], cache=cache)
    horloge["t"] += 600.0
    second = data_sources.fetch_marketaux_news({"symbols": "MC.PA"}, 7200, "k",
                                               http_get=fake_get, now_fn=lambda: horloge["t"], cache=cache)

    assert len(appels) == 1
    assert second == {"data": [{"title": "x"}]}


def test_fetch_marketaux_news_refetches_after_ttl(monkeypatch):
    monkeypatch.setenv("MARKETAUX_API_TOKEN", "t")
    appels = []

    def fake_get(url, params=None, timeout=None):
        appels.append(1)
        return _FakeResponse(json_data={"data": []})

    cache = {}
    horloge = {"t": 1000.0}
    data_sources.fetch_marketaux_news({"symbols": "MC.PA"}, 3600, "k",
                                      http_get=fake_get, now_fn=lambda: horloge["t"], cache=cache)
    horloge["t"] += 3700.0
    data_sources.fetch_marketaux_news({"symbols": "MC.PA"}, 3600, "k",
                                      http_get=fake_get, now_fn=lambda: horloge["t"], cache=cache)

    assert len(appels) == 2


def test_fetch_marketaux_news_never_caches_an_error(monkeypatch):
    monkeypatch.setenv("MARKETAUX_API_TOKEN", "t")

    def fake_get(url, params=None, timeout=None):
        raise RuntimeError("quota")

    cache = {}
    resultat = data_sources.fetch_marketaux_news({"symbols": "MC.PA"}, 7200, "k",
                                                 http_get=fake_get, cache=cache)

    assert "erreur" in resultat
    assert "k" not in cache


def test_fetch_marketaux_news_error_never_leaks_the_token(monkeypatch):
    monkeypatch.setenv("MARKETAUX_API_TOKEN", "secret-marketaux")

    def fake_get(url, params=None, timeout=None):
        raise RuntimeError(f"HTTP 429 pour https://api.marketaux.com/v1/news/all?api_token=secret-marketaux")

    resultat = data_sources.fetch_marketaux_news({"symbols": "MC.PA"}, 7200, "k",
                                                 http_get=fake_get, cache={})

    assert "secret-marketaux" not in resultat["erreur"]
    assert "RuntimeError" in resultat["erreur"]  # seul le nom de la classe est transmis
