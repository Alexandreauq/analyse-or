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


class _FakeAccountValue:
    def __init__(self, account, tag, value, currency, modelCode=""):
        self.account = account
        self.tag = tag
        self.value = value
        self.currency = currency
        self.modelCode = modelCode


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
