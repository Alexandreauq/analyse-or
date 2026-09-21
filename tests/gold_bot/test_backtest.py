from datetime import datetime, timedelta, timezone

import pytest

import gold_bot.backtest as backtest
import gold_bot.confluence as confluence


class _FakeTDResponse:
    def __init__(self, json_data, ok=True, status_code=200):
        self._json_data = json_data
        self.ok = ok
        self.status_code = status_code

    def json(self):
        return self._json_data


def _candle(time_str, open_=10.0, high=11.0, low=9.0, close=10.5):
    return {"time": time_str, "open": open_, "high": high, "low": low, "close": close}


# --- fetch_gold_candles_range ------------------------------------------------

def test_fetch_gold_candles_range_single_page(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["params"] = params
        values = [
            {"datetime": "2026-01-01 00:04:00", "open": "10", "high": "11", "low": "9", "close": "10.5"},
            {"datetime": "2026-01-01 00:03:00", "open": "10", "high": "11", "low": "9", "close": "10.4"},
            {"datetime": "2026-01-01 00:02:00", "open": "10", "high": "11", "low": "9", "close": "10.3"},
            {"datetime": "2026-01-01 00:01:00", "open": "10", "high": "11", "low": "9", "close": "10.2"},
            {"datetime": "2026-01-01 00:00:00", "open": "10", "high": "11", "low": "9", "close": "10.1"},
        ]
        return _FakeTDResponse({"status": "ok", "values": values})

    monkeypatch.setattr(backtest.requests, "get", fake_get)
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, 0, 4, tzinfo=timezone.utc)

    result = backtest.fetch_gold_candles_range("fake-key", start, end)

    assert captured["params"]["symbol"] == "XAU/USD"
    assert captured["params"]["interval"] == "1min"
    assert [c["time"] for c in result] == [
        "2026-01-01 00:00:00", "2026-01-01 00:01:00", "2026-01-01 00:02:00",
        "2026-01-01 00:03:00", "2026-01-01 00:04:00",
    ]
    assert result[0]["close"] == 10.1


def test_fetch_gold_candles_range_paginates_when_more_than_one_page_needed(monkeypatch):
    monkeypatch.setattr(backtest, "MAX_OUTPUT_SIZE", 3)
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params["end_date"])
        end_date = datetime.strptime(params["end_date"], "%Y-%m-%d %H:%M:%S")
        values = [
            {"datetime": (end_date - timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"),
             "open": "10", "high": "11", "low": "9", "close": "10.5"}
            for i in range(3)
        ]
        return _FakeTDResponse({"status": "ok", "values": values})

    monkeypatch.setattr(backtest.requests, "get", fake_get)
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, 0, 6, tzinfo=timezone.utc)

    result = backtest.fetch_gold_candles_range("fake-key", start, end)

    assert len(calls) >= 3
    times = [c["time"] for c in result]
    assert times == sorted(times)
    assert len(times) == len(set(times))
    assert times[0] == "2026-01-01 00:00:00"
    assert times[-1] == "2026-01-01 00:06:00"


def test_fetch_gold_candles_range_rejects_on_error_status(monkeypatch):
    monkeypatch.setattr(
        backtest.requests, "get",
        lambda *a, **k: _FakeTDResponse({"status": "error", "message": "quota dépassé"}),
    )
    with pytest.raises(RuntimeError, match="quota dépassé"):
        backtest.fetch_gold_candles_range(
            "fake-key",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 2, tzinfo=timezone.utc),
        )


def test_fetch_gold_candles_range_rejects_on_http_error(monkeypatch):
    monkeypatch.setattr(
        backtest.requests, "get",
        lambda *a, **k: _FakeTDResponse({}, ok=False, status_code=429),
    )
    with pytest.raises(RuntimeError, match="429"):
        backtest.fetch_gold_candles_range(
            "fake-key",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 2, tzinfo=timezone.utc),
        )


def test_fetch_gold_candles_range_rejects_start_after_end():
    with pytest.raises(ValueError):
        backtest.fetch_gold_candles_range(
            "fake-key",
            datetime(2026, 1, 2, tzinfo=timezone.utc),
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


# --- simulate_trades ----------------------------------------------------------

def _warmup_candles(n, start_price=100.0):
    """`n` bougies neutres, une par minute, pour atteindre SCALP_MIN_CANDLES
    sans provoquer de signal (compute_signal réel les traiterait aussi, mais
    ces tests injectent leur propre signal_fn donc leur contenu importe peu)."""
    base = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)  # même date que _candle des tests confluence : loin de tout blackout macro
    return [
        _candle((base + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"),
                start_price, start_price + 1, start_price - 1, start_price)
        for i in range(n)
    ]


def _fire_once_then_neutral(direction, entry_price, stop_loss, take_profit):
    """Fabrique un signal_fn qui renvoie achat/vente une seule fois (au
    premier appel), puis neutre indéfiniment ensuite — imite un signal qui
    ne se redéclenche jamais tant qu'une position est ouverte (comme la
    vraie boucle, qui n'appelle plus compute_signal tant qu'une position
    est ouverte) et ne se redéclenche pas non plus après clôture dans ces
    tests, pour isoler le comportement testé."""
    state = {"fired": False}

    def signal_fn(window):
        if not state["fired"]:
            state["fired"] = True
            return {"status": direction, "price": entry_price, "entry": entry_price,
                    "stop_loss": stop_loss, "take_profit": take_profit,
                    "trend": "baissier" if direction == "achat" else "haussier",
                    "pattern": {"name": "Marteau", "direction": "haussier"}}
        return {"status": "neutre", "price": entry_price, "entry": None,
                "stop_loss": None, "take_profit": None, "trend": "neutre", "pattern": None}

    return signal_fn


def test_simulate_trades_closes_on_stop_loss_hit():
    candles = _warmup_candles(confluence.SCALP_MIN_CANDLES)
    entry_time = candles[-1]["time"]
    entry_price = 100.0
    stop_loss = 95.0
    take_profit = 110.0
    # Bougie suivante : touche le stop (low <= 95) sans toucher le TP.
    next_time = datetime.strptime(entry_time, "%Y-%m-%d %H:%M:%S") + timedelta(minutes=1)
    candles.append(_candle(next_time.strftime("%Y-%m-%d %H:%M:%S"), 100, 101, 94, 96))

    trades = backtest.simulate_trades(
        candles, signal_fn=_fire_once_then_neutral("achat", entry_price, stop_loss, take_profit))

    assert len(trades) == 1
    trade = trades[0]
    assert trade["direction"] == "achat"
    assert trade["entry_price"] == entry_price
    assert trade["close_reason"] == "sl_hit"
    assert trade["close_price"] == stop_loss
    assert trade["return_usd"] == pytest.approx(stop_loss - entry_price)
    assert trade["return_usd"] < 0


def test_simulate_trades_closes_on_take_profit_hit():
    candles = _warmup_candles(confluence.SCALP_MIN_CANDLES)
    entry_price = 100.0
    stop_loss = 95.0
    take_profit = 110.0
    entry_time = datetime.strptime(candles[-1]["time"], "%Y-%m-%d %H:%M:%S")
    next_time = entry_time + timedelta(minutes=1)
    candles.append(_candle(next_time.strftime("%Y-%m-%d %H:%M:%S"), 100, 111, 99, 109))

    trades = backtest.simulate_trades(
        candles, signal_fn=_fire_once_then_neutral("achat", entry_price, stop_loss, take_profit))

    assert len(trades) == 1
    trade = trades[0]
    assert trade["close_reason"] == "tp_hit"
    assert trade["close_price"] == take_profit
    assert trade["return_usd"] == pytest.approx(take_profit - entry_price)
    assert trade["return_usd"] > 0


def test_simulate_trades_prioritizes_stop_loss_when_both_touched_same_candle():
    candles = _warmup_candles(confluence.SCALP_MIN_CANDLES)
    entry_price = 100.0
    stop_loss = 95.0
    take_profit = 110.0
    entry_time = datetime.strptime(candles[-1]["time"], "%Y-%m-%d %H:%M:%S")
    next_time = entry_time + timedelta(minutes=1)
    # Une seule bougie touche à la fois le SL (low<=95) et le TP (high>=110).
    candles.append(_candle(next_time.strftime("%Y-%m-%d %H:%M:%S"), 100, 111, 94, 105))

    trades = backtest.simulate_trades(
        candles, signal_fn=_fire_once_then_neutral("achat", entry_price, stop_loss, take_profit))

    assert trades[0]["close_reason"] == "sl_hit"


def test_simulate_trades_closes_still_open_position_at_end_of_data():
    candles = _warmup_candles(confluence.SCALP_MIN_CANDLES)
    entry_price = 100.0
    entry_time = datetime.strptime(candles[-1]["time"], "%Y-%m-%d %H:%M:%S")
    for i in range(3):
        t = entry_time + timedelta(minutes=i + 1)
        candles.append(_candle(t.strftime("%Y-%m-%d %H:%M:%S"), 100, 102, 98, 101))

    trades = backtest.simulate_trades(
        candles, signal_fn=_fire_once_then_neutral("achat", entry_price, 90.0, 200.0))

    assert len(trades) == 1
    assert trades[0]["close_reason"] == "fin_backtest"
    assert trades[0]["close_price"] == candles[-1]["close"]


def test_simulate_trades_does_not_call_signal_fn_while_position_open():
    candles = _warmup_candles(confluence.SCALP_MIN_CANDLES)
    entry_time = datetime.strptime(candles[-1]["time"], "%Y-%m-%d %H:%M:%S")
    for i in range(5):
        t = entry_time + timedelta(minutes=i + 1)
        candles.append(_candle(t.strftime("%Y-%m-%d %H:%M:%S"), 100, 102, 98, 101))

    call_count = {"n": 0}

    def counting_signal_fn(window):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {"status": "achat", "price": 100.0, "entry": 100.0, "stop_loss": 90.0,
                     "take_profit": 200.0, "trend": "baissier", "pattern": {"name": "Marteau", "direction": "haussier"}}
        return {"status": "neutre", "price": 100.0, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": "neutre", "pattern": None}

    backtest.simulate_trades(candles, signal_fn=counting_signal_fn)

    # Une seule position ouverte sur toute la série -> un seul appel au
    # signal tant qu'elle reste ouverte (elle ne se ferme jamais ici,
    # SL/TP très larges), jamais un deuxième pendant qu'elle est ouverte.
    assert call_count["n"] == 1


def test_simulate_trades_uses_a_rolling_window_not_the_full_history():
    n = confluence.SCALP_MIN_CANDLES + 20
    candles = _warmup_candles(n)
    captured_windows = []

    def spy_signal_fn(window):
        captured_windows.append(len(window))
        return {"status": "neutre", "price": 100.0, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": "neutre", "pattern": None}

    backtest.simulate_trades(candles, window_size=10, signal_fn=spy_signal_fn)

    # Une fois assez d'historique accumulé, la fenêtre plafonne à
    # window_size plutôt que de grossir indéfiniment avec tout l'historique.
    assert max(captured_windows) == 10


# --- summarize_trades ----------------------------------------------------------

def test_summarize_trades_computes_aggregate_stats():
    trades = [
        {"entry_price": 100.0, "stop_loss": 95.0, "return_usd": 10.0, "return_pct": 10.0},
        {"entry_price": 100.0, "stop_loss": 95.0, "return_usd": -5.0, "return_pct": -5.0},
        {"entry_price": 100.0, "stop_loss": 90.0, "return_usd": -10.0, "return_pct": -10.0},
    ]
    summary = backtest.summarize_trades(trades)

    assert summary["trade_count"] == 3
    assert summary["win_count"] == 1
    assert summary["loss_count"] == 2
    assert summary["win_rate_pct"] == pytest.approx(100 / 3)
    assert summary["total_return_usd"] == pytest.approx(-5.0)
    assert summary["avg_win_usd"] == pytest.approx(10.0)
    assert summary["avg_loss_usd"] == pytest.approx(-7.5)
    # R du 1er trade = 10/5 = 2.0 ; 2e = -5/5 = -1.0 ; 3e = -10/10 = -1.0 -> moyenne 0.0
    assert summary["avg_r_multiple"] == pytest.approx(0.0)


def test_summarize_trades_handles_empty_list():
    summary = backtest.summarize_trades([])
    assert summary["trade_count"] == 0
    assert summary["win_rate_pct"] is None
    assert summary["avg_win_usd"] is None
    assert summary["avg_loss_usd"] is None
    assert summary["avg_r_multiple"] is None


# --- run_backtest (orchestration) ----------------------------------------------

def test_run_backtest_wires_fetch_and_simulation_together(monkeypatch):
    fake_candles = _warmup_candles(confluence.SCALP_MIN_CANDLES)
    captured = {}

    def fake_fetch(api_key, start, end):
        captured["api_key"] = api_key
        captured["start"] = start
        captured["end"] = end
        return fake_candles

    monkeypatch.setattr(backtest, "fetch_gold_candles_range", fake_fetch)
    monkeypatch.setattr(backtest, "simulate_trades", lambda candles, **k: [])

    end = datetime(2026, 2, 1, tzinfo=timezone.utc)
    result = backtest.run_backtest("fake-key", days=7, end=end)

    assert captured["api_key"] == "fake-key"
    assert captured["start"] == end - timedelta(days=7)
    assert captured["end"] == end
    assert result["candle_count"] == len(fake_candles)
    assert result["summary"]["trade_count"] == 0
