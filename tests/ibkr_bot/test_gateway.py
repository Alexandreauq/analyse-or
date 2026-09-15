import pytest

import ibkr_bot.gateway as gateway

BASE = "https://127.0.0.1:5000"


class _FakeIbkrResponse:
    """Faux objet reponse HTTP — meme convention que
    tests/gold_bot/test_broker.py."""

    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise gateway.requests.exceptions.HTTPError(
                f"{self.status_code} Client Error", response=self
            )

    def json(self):
        return self._json_data


def test_build_url_inserts_the_v1_api_prefix():
    assert gateway.build_url(BASE, "/iserver/auth/status") == (
        "https://127.0.0.1:5000/v1/api/iserver/auth/status"
    )
    assert gateway.build_url(BASE + "/", "iserver/auth/status") == (
        "https://127.0.0.1:5000/v1/api/iserver/auth/status"
    )


def test_auth_status_posts_to_the_right_url_and_disables_cert_check(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None, verify=None):
        captured["url"] = url
        captured["timeout"] = timeout
        captured["verify"] = verify
        return _FakeIbkrResponse({"authenticated": True, "connected": True, "competing": False})

    monkeypatch.setattr(gateway.requests, "post", fake_post)
    result = gateway.auth_status(BASE)

    assert result["authenticated"] is True
    assert captured["url"] == "https://127.0.0.1:5000/v1/api/iserver/auth/status"
    assert captured["timeout"] == gateway.TIMEOUT
    assert captured["verify"] is False


def test_is_authenticated_true_only_when_authenticated_and_connected(monkeypatch):
    monkeypatch.setattr(
        gateway.requests, "post",
        lambda *a, **k: _FakeIbkrResponse({"authenticated": True, "connected": True}))
    assert gateway.is_authenticated(BASE) is True

    monkeypatch.setattr(
        gateway.requests, "post",
        lambda *a, **k: _FakeIbkrResponse({"authenticated": True, "connected": False}))
    assert gateway.is_authenticated(BASE) is False


def test_is_authenticated_returns_false_instead_of_raising(monkeypatch):
    """Le preflight du batch (Plan B) appelle cette fonction : un Gateway
    eteint doit donner False, pas une exception."""
    def boom(*args, **kwargs):
        raise gateway.requests.exceptions.ConnectionError("gateway eteint")

    monkeypatch.setattr(gateway.requests, "post", boom)
    assert gateway.is_authenticated(BASE) is False


def test_tickle_and_reauthenticate_use_their_documented_paths(monkeypatch):
    captured = []

    def fake_post(url, json=None, timeout=None, verify=None):
        captured.append(url)
        return _FakeIbkrResponse({"session": "abc"})

    monkeypatch.setattr(gateway.requests, "post", fake_post)
    gateway.tickle(BASE)
    gateway.reauthenticate(BASE)

    assert captured == [
        "https://127.0.0.1:5000/v1/api/tickle",
        "https://127.0.0.1:5000/v1/api/iserver/reauthenticate",
    ]


def test_brokerage_accounts_uses_iserver_accounts(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None, verify=None):
        captured["url"] = url
        return _FakeIbkrResponse({"accounts": ["U1234567"], "selectedAccount": "U1234567"})

    monkeypatch.setattr(gateway.requests, "get", fake_get)
    result = gateway.brokerage_accounts(BASE)

    assert result["accounts"] == ["U1234567"]
    assert captured["url"] == "https://127.0.0.1:5000/v1/api/iserver/accounts"


def test_search_contract_sends_symbol_and_stk_sectype(monkeypatch):
    captured = {}
    raw = [{
        "conid": "265598", "companyHeader": "APPLE INC - NASDAQ",
        "companyName": "APPLE INC", "symbol": "AAPL", "description": "NASDAQ",
        "sections": [{"secType": "STK"}, {"secType": "OPT"}],
    }]

    def fake_get(url, params=None, timeout=None, verify=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeIbkrResponse(raw)

    monkeypatch.setattr(gateway.requests, "get", fake_get)
    result = gateway.search_contract(BASE, "AAPL")

    assert result == raw
    assert captured["url"] == "https://127.0.0.1:5000/v1/api/iserver/secdef/search"
    assert captured["params"] == {"symbol": "AAPL", "secType": "STK"}


def test_search_contract_returns_empty_list_when_api_returns_a_dict(monkeypatch):
    """Le Gateway renvoie parfois un objet d'erreur la ou la doc annonce
    une liste — ne pas laisser cette forme remonter aux appelants."""
    monkeypatch.setattr(
        gateway.requests, "get",
        lambda *a, **k: _FakeIbkrResponse({"error": "no contracts"}))
    assert gateway.search_contract(BASE, "INCONNU") == []


def test_contract_info_sends_conid_and_stk_sectype(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None, verify=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeIbkrResponse({
            "conid": 265598, "symbol": "AAPL", "currency": "USD",
            "exchange": "NASDAQ", "listingExchange": "NASDAQ",
        })

    monkeypatch.setattr(gateway.requests, "get", fake_get)
    result = gateway.contract_info(BASE, 265598)

    assert result["currency"] == "USD"
    assert captured["url"] == "https://127.0.0.1:5000/v1/api/iserver/secdef/info"
    assert captured["params"] == {"conid": "265598", "secType": "STK"}


def test_exchange_rate_reads_the_rate_field(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None, verify=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeIbkrResponse({"rate": 0.8612})

    monkeypatch.setattr(gateway.requests, "get", fake_get)
    result = gateway.exchange_rate(BASE, "EUR", "GBP")

    assert result == pytest.approx(0.8612)
    assert captured["url"] == "https://127.0.0.1:5000/v1/api/iserver/exchangerate"
    assert captured["params"] == {"source": "EUR", "target": "GBP"}


def test_exchange_rate_short_circuits_identical_currencies(monkeypatch):
    """EUR -> EUR ne doit declencher aucun appel reseau : le Gateway
    refuse la paire degeneree, et le taux est trivialement 1."""
    def boom(*args, **kwargs):
        raise AssertionError("aucun appel reseau attendu pour EUR->EUR")

    monkeypatch.setattr(gateway.requests, "get", boom)
    assert gateway.exchange_rate(BASE, "EUR", "EUR") == 1.0


def test_exchange_rate_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(
        gateway.requests, "get",
        lambda *a, **k: _FakeIbkrResponse({}, status_code=503))
    with pytest.raises(gateway.requests.exceptions.HTTPError):
        gateway.exchange_rate(BASE, "EUR", "CHF")
