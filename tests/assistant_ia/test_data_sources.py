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
