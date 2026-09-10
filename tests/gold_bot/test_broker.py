import pytest
import gold_bot.broker as broker


class _FakeMT5Response:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise broker.requests.exceptions.HTTPError(
                f"{self.status_code} Client Error", response=self
            )

    def json(self):
        return self._json_data


def test_get_account_balance_returns_balance(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return _FakeMT5Response({"balance": 10234.5, "currency": "USD"})

    monkeypatch.setattr(broker.requests, "get", fake_get)
    result = broker.get_account_balance("tok", "acc123", region="london")

    assert result == 10234.5
    assert captured["url"] == (
        "https://mt-client-api-v1.london.agiliumtrade.ai"
        "/users/current/accounts/acc123/account-information"
    )
    assert captured["headers"] == {"auth-token": "tok"}


def test_get_account_balance_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(broker.requests, "get", lambda *a, **k: _FakeMT5Response({}, status_code=401))
    with pytest.raises(broker.requests.exceptions.HTTPError):
        broker.get_account_balance("bad-tok", "acc123")


def test_get_open_positions_returns_list(monkeypatch):
    raw = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY"}]
    monkeypatch.setattr(broker.requests, "get", lambda *a, **k: _FakeMT5Response(raw))
    result = broker.get_open_positions("tok", "acc123")
    assert result == raw


def test_get_symbol_specification_returns_contract_size(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        return _FakeMT5Response({"contractSize": 100, "tickSize": 0.01})

    monkeypatch.setattr(broker.requests, "get", fake_get)
    result = broker.get_symbol_specification("tok", "acc123", "XAUUSD")

    assert result["contractSize"] == 100
    assert captured["url"].endswith("/users/current/accounts/acc123/symbols/XAUUSD/specification")


def test_place_market_order_sends_correct_body_for_achat(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeMT5Response({"orderId": "999"})

    monkeypatch.setattr(broker.requests, "post", fake_post)
    result = broker.place_market_order("tok", "acc123", "XAUUSD", "achat", 1.0, 2095, 2110)

    assert result == {"orderId": "999"}
    assert captured["json"] == {
        "actionType": "ORDER_TYPE_BUY",
        "symbol": "XAUUSD",
        "volume": 1.0,
        "stopLoss": 2095,
        "takeProfit": 2110,
    }
    assert captured["url"].endswith("/users/current/accounts/acc123/trade")


def test_place_market_order_sends_correct_body_for_vente(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _FakeMT5Response({"orderId": "998"})

    monkeypatch.setattr(broker.requests, "post", fake_post)
    broker.place_market_order("tok", "acc123", "XAUUSD", "vente", 1.0, 2110, 2095)

    assert captured["json"]["actionType"] == "ORDER_TYPE_SELL"


def test_close_position_sends_correct_body(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _FakeMT5Response({"orderId": "997", "positionId": "46648037"})

    monkeypatch.setattr(broker.requests, "post", fake_post)
    result = broker.close_position("tok", "acc123", "46648037")

    assert result == {"orderId": "997", "positionId": "46648037"}
    assert captured["json"] == {"actionType": "POSITION_CLOSE_ID", "positionId": "46648037"}
