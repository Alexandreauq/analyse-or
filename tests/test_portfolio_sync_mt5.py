import json

import pytest
import portfolio_sync_mt5


class _FakeMT5Response:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise portfolio_sync_mt5.requests.exceptions.HTTPError(
                f"{self.status_code} Client Error", response=self
            )

    def json(self):
        return self._json_data


def test_fetch_positions_returns_parsed_json_on_success(monkeypatch):
    raw = [{"symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "profit": 12.5}]
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return _FakeMT5Response(raw)

    monkeypatch.setattr(portfolio_sync_mt5.requests, "get", fake_get)
    result = portfolio_sync_mt5.fetch_positions("tok", "acc123", region="london")

    assert result == raw
    assert captured["url"] == (
        "https://mt-client-api-v1.london.agiliumtrade.ai"
        "/users/current/accounts/acc123/positions"
    )
    assert captured["headers"] == {"auth-token": "tok"}


def test_fetch_positions_uses_default_region_when_not_specified(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        return _FakeMT5Response([])

    monkeypatch.setattr(portfolio_sync_mt5.requests, "get", fake_get)
    portfolio_sync_mt5.fetch_positions("tok", "acc123")

    assert "mt-client-api-v1.london.agiliumtrade.ai" in captured["url"]


def test_fetch_positions_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(
        portfolio_sync_mt5.requests, "get",
        lambda *a, **k: _FakeMT5Response([], status_code=401),
    )
    with pytest.raises(portfolio_sync_mt5.requests.exceptions.HTTPError):
        portfolio_sync_mt5.fetch_positions("bad-token", "acc123")


def test_to_public_positions_maps_buy_and_sell_types():
    raw = [
        {"symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "profit": 12.5},
        {"symbol": "EURUSD", "type": "POSITION_TYPE_SELL", "profit": -3.0},
    ]
    result = portfolio_sync_mt5.to_public_positions(raw)
    assert result[0]["symbol"] == "XAUUSD"
    assert result[0]["type"] == "achat"
    assert result[1]["symbol"] == "EURUSD"
    assert result[1]["type"] == "vente"


def test_to_public_positions_maps_profit_sign():
    raw = [
        {"symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "profit": 12.5},
        {"symbol": "EURUSD", "type": "POSITION_TYPE_SELL", "profit": -3.0},
        {"symbol": "US30", "type": "POSITION_TYPE_BUY", "profit": 0},
    ]
    result = portfolio_sync_mt5.to_public_positions(raw)
    assert result[0]["pnl_sign"] == "positif"
    assert result[1]["pnl_sign"] == "négatif"
    assert result[2]["pnl_sign"] == "positif"  # profit == 0 compte comme positif


def test_to_public_positions_defaults_missing_profit_to_positive():
    raw = [{"symbol": "XAUUSD", "type": "POSITION_TYPE_BUY"}]
    result = portfolio_sync_mt5.to_public_positions(raw)
    assert result[0]["pnl_sign"] == "positif"


def test_to_public_positions_never_includes_numeric_fields():
    raw = [{
        "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "profit": 12.5,
        "volume": 0.1, "openPrice": 2000.0, "currentPrice": 2010.0,
        "swap": -0.5, "commission": -0.5,
    }]
    result = portfolio_sync_mt5.to_public_positions(raw)
    forbidden = {"profit", "volume", "openPrice", "currentPrice", "swap", "commission"}
    for position in result:
        assert forbidden.isdisjoint(position.keys())
        assert set(position.keys()) == {"symbol", "type", "pnl_sign"}


def test_to_public_positions_empty_list_returns_empty_list():
    assert portfolio_sync_mt5.to_public_positions([]) == []


def test_main_writes_not_configured_status_when_credentials_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("METAAPI_TOKEN", raising=False)
    monkeypatch.delenv("METAAPI_ACCOUNT_ID", raising=False)
    output_path = tmp_path / "real_portfolio_mt5.json"
    monkeypatch.setattr(portfolio_sync_mt5, "REAL_PORTFOLIO_JSON_PATH", str(output_path))

    portfolio_sync_mt5.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["sync_status"] == "not_configured"
    assert written["sync_error"] is None
    assert written["positions"] == []


def test_main_writes_ok_status_and_positions_on_success(monkeypatch, tmp_path):
    monkeypatch.setenv("METAAPI_TOKEN", "tok")
    monkeypatch.setenv("METAAPI_ACCOUNT_ID", "acc123")
    output_path = tmp_path / "real_portfolio_mt5.json"
    monkeypatch.setattr(portfolio_sync_mt5, "REAL_PORTFOLIO_JSON_PATH", str(output_path))
    monkeypatch.setattr(
        portfolio_sync_mt5, "fetch_positions",
        lambda token, account_id, region=portfolio_sync_mt5.DEFAULT_MT5_REGION: [
            {"symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "profit": 12.5},
        ],
    )

    portfolio_sync_mt5.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["sync_status"] == "ok"
    assert written["sync_error"] is None
    assert written["positions"] == [{"symbol": "XAUUSD", "type": "achat", "pnl_sign": "positif"}]


def test_main_keeps_previous_positions_and_sets_error_on_fetch_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("METAAPI_TOKEN", "tok")
    monkeypatch.setenv("METAAPI_ACCOUNT_ID", "acc123")
    output_path = tmp_path / "real_portfolio_mt5.json"
    output_path.write_text(
        json.dumps({
            "updated": "2026-09-09T07:00:00Z", "sync_status": "ok", "sync_error": None,
            "positions": [{"symbol": "XAUUSD", "type": "achat", "pnl_sign": "positif"}],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(portfolio_sync_mt5, "REAL_PORTFOLIO_JSON_PATH", str(output_path))

    def _raise(token, account_id, region=portfolio_sync_mt5.DEFAULT_MT5_REGION):
        raise portfolio_sync_mt5.requests.exceptions.HTTPError("401 Client Error")

    monkeypatch.setattr(portfolio_sync_mt5, "fetch_positions", _raise)

    portfolio_sync_mt5.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["sync_status"] == "error"
    assert written["positions"] == [{"symbol": "XAUUSD", "type": "achat", "pnl_sign": "positif"}]


def test_main_starts_from_empty_state_when_output_file_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("METAAPI_TOKEN", raising=False)
    output_path = tmp_path / "does_not_exist" / "real_portfolio_mt5.json"
    monkeypatch.setattr(portfolio_sync_mt5, "REAL_PORTFOLIO_JSON_PATH", str(output_path))

    portfolio_sync_mt5.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["positions"] == []


def test_write_real_portfolio_never_emits_invalid_json_nan(monkeypatch, tmp_path):
    output_path = tmp_path / "real_portfolio_mt5.json"
    monkeypatch.setattr(portfolio_sync_mt5, "REAL_PORTFOLIO_JSON_PATH", str(output_path))

    with pytest.raises(ValueError):
        portfolio_sync_mt5._write_real_portfolio({"positions": [{"x": float("nan")}]})
