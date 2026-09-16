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
        self.place_order_result = None
        self.place_order_calls = []

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

    def placeOrder(self, contract, order):
        self.place_order_calls.append((contract, order))
        return self.place_order_result


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


class _FakeOrderStatus:
    def __init__(self, status="Filled", avgFillPrice=0.0, orderId=0):
        self.status = status
        self.avgFillPrice = avgFillPrice
        self.orderId = orderId


class _FakeOrder:
    def __init__(self, orderId):
        self.orderId = orderId


class _FakeCommissionReport:
    def __init__(self, commission):
        self.commission = commission


class _FakeFill:
    def __init__(self, commission=None):
        # commissionReport est None tant que le rapport n'est pas encore
        # arrive (ordre juste rempli, ib.placeOrder etant asynchrone) —
        # voir docstring de order_status().
        self.commissionReport = _FakeCommissionReport(commission) if commission is not None else None


class _FakeTrade:
    def __init__(self, orderId, avgFillPrice=0.0, status="Filled", fills=None, log=None):
        self.order = _FakeOrder(orderId)
        self.orderStatus = _FakeOrderStatus(status=status, avgFillPrice=avgFillPrice, orderId=orderId)
        self.fills = fills if fills is not None else []
        self.log = log if log is not None else []


@pytest.fixture
def fake_ib(monkeypatch):
    instance = _FakeIB()
    monkeypatch.setattr(gateway, "IB", lambda: instance)
    # gateway.py garde un singleton module-level ; le reinitialiser entre
    # deux tests evite qu'un test reutilise la connexion du precedent.
    monkeypatch.setattr(gateway, "_ib", None)
    # Meme raisonnement pour le cache d'ordres passes (module-level lui
    # aussi) : sans ce reset, un order_id reutilise d'un test a l'autre
    # (555 ci-dessous) pourrait lire le Trade d'un test precedent.
    monkeypatch.setattr(gateway, "_trades_by_order_id", {})
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


def test_place_market_order_returns_a_single_confirmation(fake_ib):
    gateway.connect(BASE)
    fake_ib.place_order_result = _FakeTrade(orderId=555, avgFillPrice=0.0, status="Submitted")
    result = gateway.place_market_order(BASE, "U28849893", 265598, "BUY", 10)
    assert result == [{"order_id": "555"}]
    contract, order = fake_ib.place_order_calls[0]
    assert contract.conId == 265598
    # Fix 2 (revue post-implementation) : sans exchange="SMART", TWS
    # rejette tout ordre reel avec l'erreur 321 "Please enter exchange"
    # — un Contract(conId=...) seul suffit pour reqContractDetails mais
    # pas pour placeOrder.
    assert contract.exchange == "SMART"
    assert order.action == "BUY"
    assert order.totalQuantity == 10
    assert order.tif == "DAY"
    # Fix 1 : place_market_order laisse le temps a la reponse initiale de
    # TWS d'arriver avant de considerer l'ordre confirme (placeOrder est
    # non bloquant et ne leve jamais sur un rejet).
    assert fake_ib.sleep_calls == [2]


def test_place_market_order_sell_side_is_forwarded_unchanged(fake_ib):
    gateway.connect(BASE)
    fake_ib.place_order_result = _FakeTrade(orderId=556, avgFillPrice=0.0, status="Submitted")
    result = gateway.place_market_order(BASE, "U28849893", 265598, "SELL", 4)
    assert result == [{"order_id": "556"}]
    contract, order = fake_ib.place_order_calls[0]
    assert contract.exchange == "SMART"
    assert order.action == "SELL"
    assert order.totalQuantity == 4


def test_place_market_order_rejects_invalid_side():
    with pytest.raises(ValueError, match="BUY.*SELL"):
        gateway.place_market_order(BASE, "U28849893", 265598, "HOLD", 10)


def test_place_market_order_rejects_invalid_quantity():
    with pytest.raises(ValueError, match="entier"):
        gateway.place_market_order(BASE, "U28849893", 265598, "BUY", 0)


def test_place_market_order_rejects_a_bool_quantity():
    # bool est une sous-classe d'int en Python : True/False ne doivent
    # jamais devenir silencieusement une quantite de 1/0.
    with pytest.raises(ValueError, match="entier"):
        gateway.place_market_order(BASE, "U28849893", 265598, "BUY", True)


def test_place_market_order_raises_when_ibkr_rejects_or_cancels_the_order(fake_ib):
    # Fix 1 (Critical, revue post-implementation) : ib.placeOrder() ne
    # leve jamais sur un rejet — TWS le signale plus tard via un statut
    # asynchrone. Sans cette verification, un ordre rejete serait
    # journalise comme un succes par daily.py::_place_order.
    gateway.connect(BASE)
    fake_ib.place_order_result = _FakeTrade(
        orderId=557, status="Cancelled",
        log=["rejected: no such contract"])
    with pytest.raises(RuntimeError, match="rejete ou annule"):
        gateway.place_market_order(BASE, "U28849893", 265598, "BUY", 10)


def test_place_market_order_propagates_placeorder_exceptions(fake_ib, monkeypatch):
    # Mirroir de l'ancien test_place_market_order_propagates_http_errors
    # (CPAPI) : une exception levee pendant l'envoi de l'ordre (connexion
    # coupee, etc.) doit remonter telle quelle, jamais etre avalee.
    gateway.connect(BASE)

    def boom(contract, order):
        raise ConnectionError("connexion TWS perdue pendant l'envoi de l'ordre")

    monkeypatch.setattr(fake_ib, "placeOrder", boom)
    with pytest.raises(ConnectionError, match="connexion TWS perdue"):
        gateway.place_market_order(BASE, "U28849893", 265598, "BUY", 10)


def test_confirm_reply_raises_loudly_instead_of_pretending_to_answer(fake_ib):
    gateway.connect(BASE)
    with pytest.raises(NotImplementedError):
        gateway.confirm_reply(BASE, "some-reply-id")


def test_order_status_reads_the_cached_trade_for_this_order_id(fake_ib):
    gateway.connect(BASE)
    fake_ib.place_order_result = _FakeTrade(orderId=555, avgFillPrice=123.45, status="Filled")
    gateway.place_market_order(BASE, "U28849893", 265598, "BUY", 10)
    status = gateway.order_status(BASE, "555")
    assert status == {"order_status": "Filled", "avgPrice": 123.45, "commission": None}


def test_order_status_empty_dict_when_order_id_unknown(fake_ib):
    gateway.connect(BASE)
    assert gateway.order_status(BASE, "unknown-id") == {}


def test_order_status_reads_real_commission_when_a_fill_reports_one(fake_ib):
    # Fix 3 (Important, revue post-implementation) : commission etait
    # hardcodee a None, perdant definitivement la donnee reelle exposee
    # par ib_async via trade.fills[i].commissionReport.commission.
    gateway.connect(BASE)
    fake_ib.place_order_result = _FakeTrade(
        orderId=558, avgFillPrice=99.5, status="Filled",
        fills=[_FakeFill(commission=None), _FakeFill(commission=1.23)])
    gateway.place_market_order(BASE, "U28849893", 265598, "BUY", 10)
    status = gateway.order_status(BASE, "558")
    assert status == {"order_status": "Filled", "avgPrice": 99.5, "commission": 1.23}


def test_order_status_commission_falls_back_to_none_without_a_fill_yet(fake_ib):
    # Best-effort (comme le prix estime ailleurs dans ce fichier) : les
    # fills peuvent arriver apres l'appel a order_status(), pas de
    # tentative de forcer leur presence.
    gateway.connect(BASE)
    fake_ib.place_order_result = _FakeTrade(
        orderId=559, avgFillPrice=0.0, status="Submitted", fills=[])
    gateway.place_market_order(BASE, "U28849893", 265598, "BUY", 10)
    status = gateway.order_status(BASE, "559")
    assert status == {"order_status": "Submitted", "avgPrice": None, "commission": None}
