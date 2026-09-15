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


def test_brokerage_accounts_uses_iserver_accounts_and_disables_cert_check(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None, verify=None):
        captured["url"] = url
        captured["timeout"] = timeout
        captured["verify"] = verify
        return _FakeIbkrResponse({"accounts": ["U1234567"], "selectedAccount": "U1234567"})

    monkeypatch.setattr(gateway.requests, "get", fake_get)
    result = gateway.brokerage_accounts(BASE)

    assert result["accounts"] == ["U1234567"]
    assert captured["url"] == "https://127.0.0.1:5000/v1/api/iserver/accounts"
    assert captured["timeout"] == gateway.TIMEOUT
    assert captured["verify"] is False


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
    assert captured["params"] == {
        "conid": "265598", "secType": "STK", "sectype": "STK",
    }


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


# --- portefeuille ----------------------------------------------------

def test_portfolio_accounts_uses_the_portfolio_accounts_path(monkeypatch):
    """Pendant cote portefeuille du prealable brokerage_accounts()/
    /iserver/accounts : le CPAPI exige un appel a /portfolio/accounts
    avant que ledger()/positions() renvoient des donnees reelles."""
    captured = {}

    def fake_get(url, params=None, timeout=None, verify=None):
        captured["url"] = url
        return _FakeIbkrResponse([{"id": "U1234567", "accountId": "U1234567"}])

    monkeypatch.setattr(gateway.requests, "get", fake_get)
    result = gateway.portfolio_accounts(BASE)

    assert result == [{"id": "U1234567", "accountId": "U1234567"}]
    assert captured["url"] == "https://127.0.0.1:5000/v1/api/portfolio/accounts"


def test_portfolio_accounts_returns_empty_list_when_api_returns_a_dict(monkeypatch):
    monkeypatch.setattr(
        gateway.requests, "get",
        lambda *a, **k: _FakeIbkrResponse({"error": "not ready"}))
    assert gateway.portfolio_accounts(BASE) == []


def test_ledger_uses_the_portfolio_ledger_path(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None, verify=None):
        captured["url"] = url
        return _FakeIbkrResponse({
            "EUR": {"currency": "EUR", "cashbalance": 3120.5, "settledcash": 3120.5},
            "BASE": {"currency": "BASE", "cashbalance": 3400.0},
        })

    monkeypatch.setattr(gateway.requests, "get", fake_get)
    result = gateway.ledger(BASE, "U1234567")

    assert result["EUR"]["cashbalance"] == 3120.5
    assert captured["url"] == "https://127.0.0.1:5000/v1/api/portfolio/U1234567/ledger"


def test_cash_by_currency_keeps_real_currencies_and_drops_base(monkeypatch):
    monkeypatch.setattr(gateway.requests, "get", lambda *a, **k: _FakeIbkrResponse({
        "EUR": {"currency": "EUR", "cashbalance": 3120.5},
        "GBP": {"currency": "GBP", "cashbalance": 430.0},
        "USD": {"currency": "USD", "cashbalance": 0.0},
        "BASE": {"currency": "BASE", "cashbalance": 3900.0},
    }))
    result = gateway.cash_by_currency(BASE, "U1234567")

    assert result == {"EUR": 3120.5, "GBP": 430.0, "USD": 0.0}
    assert "BASE" not in result


def test_cash_by_currency_ignores_entries_without_a_cash_balance(monkeypatch):
    monkeypatch.setattr(gateway.requests, "get", lambda *a, **k: _FakeIbkrResponse({
        "EUR": {"currency": "EUR", "cashbalance": 100.0},
        "CHF": {"currency": "CHF"},
        "JPY": "pas un dict",
    }))
    assert gateway.cash_by_currency(BASE, "U1234567") == {"EUR": 100.0}


def test_base_currency_cash_reads_the_base_ledger_entry(monkeypatch):
    monkeypatch.setattr(gateway.requests, "get", lambda *a, **k: _FakeIbkrResponse({
        "EUR": {"currency": "EUR", "cashbalance": 3120.5},
        "BASE": {"currency": "BASE", "cashbalance": 8000.0},
    }))
    assert gateway.base_currency_cash(BASE, "U1234567") == 8000.0


def test_base_currency_cash_is_zero_when_the_base_entry_is_missing(monkeypatch):
    monkeypatch.setattr(gateway.requests, "get", lambda *a, **k: _FakeIbkrResponse({
        "EUR": {"currency": "EUR", "cashbalance": 3120.5},
    }))
    assert gateway.base_currency_cash(BASE, "U1234567") == 0.0


def test_base_currency_cash_is_zero_when_the_cashbalance_field_is_missing_or_invalid(monkeypatch):
    monkeypatch.setattr(gateway.requests, "get", lambda *a, **k: _FakeIbkrResponse({
        "BASE": {"currency": "BASE"},
    }))
    assert gateway.base_currency_cash(BASE, "U1234567") == 0.0

    monkeypatch.setattr(gateway.requests, "get", lambda *a, **k: _FakeIbkrResponse({
        "BASE": "pas un dict",
    }))
    assert gateway.base_currency_cash(BASE, "U1234567") == 0.0

    monkeypatch.setattr(gateway.requests, "get", lambda *a, **k: _FakeIbkrResponse({
        "BASE": {"currency": "BASE", "cashbalance": "8000"},
    }))
    assert gateway.base_currency_cash(BASE, "U1234567") == 0.0


def test_positions_fetches_the_first_page(monkeypatch):
    captured = []
    raw = [
        {"conid": 265598, "contractDesc": "AAPL", "position": 10.0,
         "currency": "USD", "mktPrice": 190.0, "assetClass": "STK"},
        {"conid": 4901, "contractDesc": "LVMH", "position": 1.0,
         "currency": "EUR", "mktPrice": 415.0, "assetClass": "STK"},
    ]

    def fake_get(url, params=None, timeout=None, verify=None):
        captured.append(url)
        return _FakeIbkrResponse(raw)

    monkeypatch.setattr(gateway.requests, "get", fake_get)
    result = gateway.positions(BASE, "U1234567")

    assert result == raw
    assert captured == ["https://127.0.0.1:5000/v1/api/portfolio/U1234567/positions/0"]


def test_positions_follows_pagination_until_a_short_page(monkeypatch):
    page_0 = [{"conid": i, "position": 1.0, "currency": "EUR"} for i in range(100)]
    page_1 = [{"conid": 1000, "position": 2.0, "currency": "EUR"}]
    pages = {"0": page_0, "1": page_1}
    captured = []

    def fake_get(url, params=None, timeout=None, verify=None):
        captured.append(url)
        return _FakeIbkrResponse(pages[url.rsplit("/", 1)[-1]])

    monkeypatch.setattr(gateway.requests, "get", fake_get)
    result = gateway.positions(BASE, "U1234567")

    assert len(result) == 101
    assert result[-1]["conid"] == 1000
    assert captured == [
        "https://127.0.0.1:5000/v1/api/portfolio/U1234567/positions/0",
        "https://127.0.0.1:5000/v1/api/portfolio/U1234567/positions/1",
    ]


def test_positions_returns_empty_list_when_api_returns_a_dict(monkeypatch):
    monkeypatch.setattr(
        gateway.requests, "get",
        lambda *a, **k: _FakeIbkrResponse({"error": "not ready"}))
    assert gateway.positions(BASE, "U1234567") == []


# --- ordres (mockes, jamais appeles hors tests dans ce Plan A) --------

def test_place_market_order_sends_the_documented_body(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None, verify=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeIbkrResponse([{"order_id": "1234", "order_status": "Submitted"}])

    monkeypatch.setattr(gateway.requests, "post", fake_post)
    result = gateway.place_market_order(BASE, "U1234567", 4901, "BUY", 6)

    assert result == [{"order_id": "1234", "order_status": "Submitted"}]
    assert captured["url"] == "https://127.0.0.1:5000/v1/api/iserver/account/U1234567/orders"
    sent_order = captured["json"]["orders"][0]
    coid = sent_order.pop("cOID", None)
    assert sent_order == {
        "conid": 4901,
        "orderType": "MKT",
        "side": "BUY",
        "quantity": 6,
        "tif": "DAY",
        "acctId": "U1234567",
    }
    assert isinstance(coid, str) and coid


def test_place_market_order_sends_a_distinct_coid_on_each_call(monkeypatch):
    """cOID (client order id) doit permettre a un futur retry (Plan B)
    de distinguer un ordre deja envoye d'un ordre jamais parti apres un
    timeout HTTP — deux appels ne doivent donc jamais partager le meme
    identifiant."""
    captured = []

    def fake_post(url, json=None, timeout=None, verify=None):
        captured.append(json["orders"][0]["cOID"])
        return _FakeIbkrResponse([{"order_id": "1234"}])

    monkeypatch.setattr(gateway.requests, "post", fake_post)
    gateway.place_market_order(BASE, "U1234567", 4901, "BUY", 6)
    gateway.place_market_order(BASE, "U1234567", 4901, "BUY", 6)

    assert len(captured) == 2
    assert captured[0] != captured[1]
    assert all(coid for coid in captured)


def test_place_market_order_accepts_sell_side(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None, verify=None):
        captured["json"] = json
        return _FakeIbkrResponse([{"order_id": "1235"}])

    monkeypatch.setattr(gateway.requests, "post", fake_post)
    gateway.place_market_order(BASE, "U1234567", 4901, "SELL", 6)

    assert captured["json"]["orders"][0]["side"] == "SELL"
    assert captured["json"]["orders"][0]["orderType"] == "MKT"


def test_place_market_order_rejects_an_unknown_side():
    with pytest.raises(ValueError, match="BUY.*SELL"):
        gateway.place_market_order(BASE, "U1234567", 4901, "achat", 6)


def test_place_market_order_rejects_a_non_positive_quantity():
    with pytest.raises(ValueError, match="quantite"):
        gateway.place_market_order(BASE, "U1234567", 4901, "BUY", 0)


def test_place_market_order_propagates_http_errors(monkeypatch):
    monkeypatch.setattr(
        gateway.requests, "post",
        lambda *a, **k: _FakeIbkrResponse({}, status_code=400))
    with pytest.raises(gateway.requests.exceptions.HTTPError):
        gateway.place_market_order(BASE, "U1234567", 4901, "BUY", 6)


def test_confirm_reply_posts_confirmed_true(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None, verify=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeIbkrResponse([{"order_id": "1234", "order_status": "Submitted"}])

    monkeypatch.setattr(gateway.requests, "post", fake_post)
    gateway.confirm_reply(BASE, "e1f2a3b4-0000")

    assert captured["url"] == "https://127.0.0.1:5000/v1/api/iserver/reply/e1f2a3b4-0000"
    assert captured["json"] == {"confirmed": True}


def test_order_status_uses_the_documented_path(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None, verify=None):
        captured["url"] = url
        return _FakeIbkrResponse({"order_status": "Filled", "avgPrice": "415.20"})

    monkeypatch.setattr(gateway.requests, "get", fake_get)
    result = gateway.order_status(BASE, "1234")

    assert result["order_status"] == "Filled"
    assert captured["url"] == (
        "https://127.0.0.1:5000/v1/api/iserver/account/order/status/1234"
    )


# --- garde structurel du Plan A --------------------------------------

def test_only_gateway_and_daily_may_reference_the_order_functions():
    """Garantie structurelle, elargie AU PLUS ETROIT pour le Plan B.

    - Les NOMS de fonction (place_market_order, confirm_reply) : definis
      par gateway.py, et appeles par daily.py — le seul appelant legitime
      (spec 4.3, point 2 : "daily.py est le seul endroit ou un ordre reel
      part"). Tout AUTRE module du paquet qui les mentionne est un
      contournement du garde.
    - Les ROUTES HTTP : interdites PARTOUT hors gateway.py, DAILY.PY
      COMPRISE. daily.py appelle gateway.py, elle ne reimplemente jamais
      l'appel HTTP — sinon la garantie "un seul module parle a IBKR"
      (spec 4.3, point 1) ne vaudrait plus rien.

    rglob (recursif) plutot que glob : un futur sous-paquet (ex.
    ibkr_bot/steps/) doit etre scanne lui aussi."""
    import pathlib

    package_dir = pathlib.Path(gateway.__file__).parent
    noms_de_fonction = ("place_market_order", "confirm_reply")
    routes = ("/iserver/account/", "/iserver/reply/")
    appelants_autorises = {"gateway.py", "daily.py"}

    fautifs = []
    for source in sorted(package_dir.rglob("*.py")):
        texte = source.read_text(encoding="utf-8")
        if source.name != "gateway.py":
            for route in routes:
                if route in texte:
                    fautifs.append(f"{source.name} construit la route {route!r}")
        if source.name not in appelants_autorises:
            for nom in noms_de_fonction:
                if nom in texte:
                    fautifs.append(f"{source.name} mentionne {nom!r}")

    assert fautifs == [], (
        "Seuls gateway.py (definition) et daily.py (appel) peuvent "
        "reference les fonctions de passage d'ordre, et seul gateway.py "
        "peut construire leurs routes HTTP : " + "; ".join(fautifs)
    )


def test_ibkr_bot_package_does_not_reexport_the_order_routes():
    """Ceinture et bretelles par rapport au scan textuel ci-dessus : meme
    si un futur `ibkr_bot/__init__.py` se mettait a faire
    `from .gateway import *`, les fonctions de passage d'ordre ne
    doivent pas devenir accessibles comme `ibkr_bot.place_market_order`.
    Aujourd'hui __init__.py est vide (0 octet), donc ce test passe
    trivialement — il sert de garde-fou si ca change."""
    import ibkr_bot

    assert not hasattr(ibkr_bot, "place_market_order")
    assert not hasattr(ibkr_bot, "confirm_reply")
