import pytest

import ibkr_bot.gateway as gateway

BASE = "127.0.0.1:4002"


class _FakeIB:
    """Faux ib_async.IB — enregistre les appels, ne fait jamais de reseau.
    Meme esprit que _FakeIbkrResponse dans l'ancienne suite de tests."""

    def __init__(self):
        self.connected = False
        self.connect_calls = []
        self.disconnect_calls = 0
        self.managed_accounts_result = ["U28849893"]
        self.sleep_calls = []
        self.account_values_result = []
        self.positions_result = []
        self.contract_details_result = []
        self.mkt_data_result = None
        self.cancel_mkt_data_calls = []
        self.req_mkt_data_calls = []

    def connect(self, host, port, clientId=1, timeout=4, readonly=False,
                account="", raiseSyncErrors=False, **kwargs):
        self.connect_calls.append({
            "host": host, "port": port, "clientId": clientId,
            "timeout": timeout, "account": account,
            "raiseSyncErrors": raiseSyncErrors,
        })
        self.connected = True

    def isConnected(self):
        return self.connected

    def disconnect(self):
        self.disconnect_calls += 1
        self.connected = False

    def managedAccounts(self):
        return self.managed_accounts_result

    def sleep(self, seconds):
        self.sleep_calls.append(seconds)

    def accountValues(self, account=""):
        return self.account_values_result

    def positions(self, account=""):
        return self.positions_result

    def reqContractDetails(self, contract):
        return self.contract_details_result

    def reqMktData(self, contract, *a, **k):
        self.req_mkt_data_calls.append(contract)
        return self.mkt_data_result

    def cancelMktData(self, contract):
        self.cancel_mkt_data_calls.append(contract)


class _FakeContract:
    def __init__(self, conId):
        self.conId = conId


class _FakePosition:
    def __init__(self, account, conId, position, avgCost=0.0):
        self.account = account
        self.contract = _FakeContract(conId)
        self.position = position
        self.avgCost = avgCost


class _FakeAccountValue:
    def __init__(self, account, tag, value, currency, modelCode=""):
        self.account = account
        self.tag = tag
        self.value = value
        self.currency = currency
        self.modelCode = modelCode


class _FakeContractFull:
    def __init__(self, symbol, conId, currency, primaryExchange, secType="STK"):
        self.symbol = symbol
        self.conId = conId
        self.currency = currency
        self.primaryExchange = primaryExchange
        self.secType = secType


class _FakeContractDetails:
    def __init__(self, contract):
        self.contract = contract


class _FakeTicker:
    def __init__(self, price):
        self._price = price

    def marketPrice(self):
        return self._price


@pytest.fixture
def fake_ib(monkeypatch):
    instance = _FakeIB()
    monkeypatch.setattr(gateway, "IB", lambda: instance)
    # gateway.py garde un singleton module-level ; le reinitialiser entre
    # deux tests evite qu'un test reutilise la connexion du precedent.
    monkeypatch.setattr(gateway, "_ib", None)
    return instance


def test_connect_parses_host_and_port_from_base_url(fake_ib):
    ok = gateway.connect(BASE, account_id="U28849893")
    assert ok is True
    assert fake_ib.connect_calls == [{
        "host": "127.0.0.1", "port": 4002, "clientId": gateway.DEFAULT_CLIENT_ID,
        "timeout": gateway.CONNECT_TIMEOUT, "account": "U28849893",
        "raiseSyncErrors": True,
    }]


def test_connect_tolerates_an_http_prefix(fake_ib):
    gateway.connect("https://127.0.0.1:4001")
    assert fake_ib.connect_calls[0]["host"] == "127.0.0.1"
    assert fake_ib.connect_calls[0]["port"] == 4001


def test_connect_accepts_a_custom_client_id(fake_ib):
    gateway.connect(BASE, client_id=42)
    assert fake_ib.connect_calls[0]["clientId"] == 42


def test_connect_returns_false_instead_of_raising(monkeypatch, fake_ib):
    def boom(*a, **k):
        raise ConnectionRefusedError("gateway pas encore pret")

    monkeypatch.setattr(fake_ib, "connect", boom)
    assert gateway.connect(BASE) is False


def test_connect_is_idempotent_if_already_connected(fake_ib):
    gateway.connect(BASE)
    gateway.connect(BASE)
    assert len(fake_ib.connect_calls) == 1


def test_disconnect_is_safe_even_if_never_connected(fake_ib):
    gateway.disconnect()  # ne doit jamais lever
    assert fake_ib.disconnect_calls == 0


def test_disconnect_calls_ib_disconnect_when_connected(fake_ib):
    gateway.connect(BASE)
    gateway.disconnect()
    assert fake_ib.disconnect_calls == 1
    # Un flush delay est recommande par ib_async avant de couper une
    # connexion de courte duree (voir doc du projet) : on l'exerce ici.
    assert fake_ib.sleep_calls == [gateway.DISCONNECT_FLUSH_SECONDS]


def test_is_authenticated_true_when_connected_and_accounts_present(fake_ib):
    gateway.connect(BASE)
    assert gateway.is_authenticated(BASE) is True


def test_is_authenticated_false_when_not_connected(fake_ib):
    assert gateway.is_authenticated(BASE) is False


def test_is_authenticated_false_when_connected_but_no_managed_accounts(fake_ib):
    gateway.connect(BASE)
    fake_ib.managed_accounts_result = []
    assert gateway.is_authenticated(BASE) is False


def test_is_authenticated_returns_false_instead_of_raising(fake_ib, monkeypatch):
    gateway.connect(BASE)
    monkeypatch.setattr(fake_ib, "managedAccounts",
                         lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert gateway.is_authenticated(BASE) is False


def test_auth_status_reflects_is_authenticated(fake_ib):
    gateway.connect(BASE)
    status = gateway.auth_status(BASE)
    assert status == {"authenticated": True, "connected": True, "competing": False}


def test_tickle_is_a_plain_isconnected_check(fake_ib):
    gateway.connect(BASE)
    assert gateway.tickle(BASE) == {"session": "ib_async", "connected": True}


def test_reauthenticate_is_a_documented_no_op(fake_ib):
    gateway.connect(BASE)
    result = gateway.reauthenticate(BASE)
    assert result == {
        "authenticated": True,
        "detail": "no-op : la reconnexion TWS API passe par IBC/systemd, pas par un appel applicatif",
    }


def test_brokerage_accounts_reflects_managed_accounts(fake_ib):
    gateway.connect(BASE)
    assert gateway.brokerage_accounts(BASE) == {"accounts": ["U28849893"]}


def test_portfolio_accounts_reflects_managed_accounts(fake_ib):
    gateway.connect(BASE)
    assert gateway.portfolio_accounts(BASE) == [{"accountId": "U28849893"}]


def test_ledger_indexes_cash_balances_by_currency_plus_base(fake_ib):
    gateway.connect(BASE)
    fake_ib.account_values_result = [
        _FakeAccountValue("U28849893", "CashBalance", "1234.56", "EUR"),
        _FakeAccountValue("U28849893", "CashBalance", "500.00", "GBP"),
        _FakeAccountValue("U28849893", "TotalCashValue", "1800.00", "BASE"),
        _FakeAccountValue("U28849893", "NetLiquidation", "9999.00", "BASE"),  # doit etre ignore
    ]
    result = gateway.ledger(BASE, "U28849893")
    assert result == {
        "EUR": {"cashbalance": 1234.56},
        "GBP": {"cashbalance": 500.00},
        "BASE": {"cashbalance": 1800.00},
    }


def test_cash_by_currency_excludes_the_base_aggregate(fake_ib):
    gateway.connect(BASE)
    fake_ib.account_values_result = [
        _FakeAccountValue("U28849893", "CashBalance", "1234.56", "EUR"),
        _FakeAccountValue("U28849893", "TotalCashValue", "1800.00", "BASE"),
    ]
    assert gateway.cash_by_currency(BASE, "U28849893") == {"EUR": 1234.56}


def test_base_currency_cash_reads_the_base_aggregate(fake_ib):
    gateway.connect(BASE)
    fake_ib.account_values_result = [
        _FakeAccountValue("U28849893", "TotalCashValue", "1800.00", "BASE"),
    ]
    assert gateway.base_currency_cash(BASE, "U28849893") == 1800.00


def test_base_currency_cash_defaults_to_zero_when_absent(fake_ib):
    gateway.connect(BASE)
    fake_ib.account_values_result = []
    assert gateway.base_currency_cash(BASE, "U28849893") == 0.0


def test_positions_translates_ib_async_position_objects(fake_ib):
    gateway.connect(BASE)
    fake_ib.positions_result = [
        _FakePosition("U28849893", 265598, 10, avgCost=150.25),
        _FakePosition("U28849893", 999999, 0, avgCost=0.0),
    ]
    result = gateway.positions(BASE, "U28849893")
    assert result == [
        {"conid": 265598, "position": 10, "avgCost": 150.25, "account": "U28849893"},
        {"conid": 999999, "position": 0, "avgCost": 0.0, "account": "U28849893"},
    ]


def test_positions_empty_when_no_positions_held(fake_ib):
    gateway.connect(BASE)
    fake_ib.positions_result = []
    assert gateway.positions(BASE, "U28849893") == []


def test_search_contract_translates_contract_details_list(fake_ib):
    gateway.connect(BASE)
    fake_ib.contract_details_result = [
        _FakeContractDetails(_FakeContractFull("SAP", 12345, "EUR", "IBIS")),
        _FakeContractDetails(_FakeContractFull("SAP", 67890, "USD", "NYSE")),
    ]
    result = gateway.search_contract(BASE, "SAP")
    assert result == [
        {"symbol": "SAP", "conid": 12345, "description": "IBIS",
         "sections": [{"secType": "STK"}]},
        {"symbol": "SAP", "conid": 67890, "description": "NYSE",
         "sections": [{"secType": "STK"}]},
    ]


def test_search_contract_returns_empty_list_on_no_match(fake_ib):
    gateway.connect(BASE)
    fake_ib.contract_details_result = []
    assert gateway.search_contract(BASE, "INCONNU") == []


def test_contract_info_reads_currency_and_primary_exchange(fake_ib):
    gateway.connect(BASE)
    fake_ib.contract_details_result = [
        _FakeContractDetails(_FakeContractFull("SAP", 12345, "EUR", "IBIS")),
    ]
    assert gateway.contract_info(BASE, 12345) == {
        "currency": "EUR", "listingExchange": "IBIS",
    }


def test_contract_info_empty_dict_when_not_found(fake_ib):
    gateway.connect(BASE)
    fake_ib.contract_details_result = []
    assert gateway.contract_info(BASE, 999) == {}


def test_exchange_rate_reads_forex_market_price(fake_ib):
    gateway.connect(BASE)
    fake_ib.mkt_data_result = _FakeTicker(0.86)
    rate = gateway.exchange_rate(BASE, "EUR", "GBP")
    assert rate == 0.86
    assert fake_ib.cancel_mkt_data_calls  # nettoyage de l'abonnement


def test_exchange_rate_same_currency_is_one_without_network_call(fake_ib):
    gateway.connect(BASE)
    assert gateway.exchange_rate(BASE, "EUR", "EUR") == 1.0
    assert fake_ib.req_mkt_data_calls == []  # aucun appel reqMktData necessaire
