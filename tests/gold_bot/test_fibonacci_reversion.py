# tests/gold_bot/test_fibonacci_reversion.py
from datetime import datetime, timedelta, timezone

import gold_bot.fibonacci_reversion as fib


def _candle(time_str, open_, high, low, close, tick_volume=1000, spread=20):
    return {"time": time_str, "open": open_, "high": high, "low": low, "close": close,
            "tick_volume": tick_volume, "spread": spread}


def _series(n, start_price=2000.0, start_dt=None, step_minutes=5):
    start_dt = start_dt or datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    out = []
    price = start_price
    for i in range(n):
        t = start_dt + timedelta(minutes=step_minutes * i)
        price += 0.5 if i % 2 == 0 else -0.5
        out.append(_candle(t.strftime("%Y-%m-%d %H:%M:%S"), price, price + 1.0, price - 1.0, price))
    return out


def test_compute_atr_none_before_period_bars():
    candles = _series(fib.ATR_PERIOD - 1)
    atr = fib.compute_atr(candles)
    assert all(v is None for v in atr)


def test_compute_atr_positive_once_period_reached():
    candles = _series(fib.ATR_PERIOD + 5)
    atr = fib.compute_atr(candles)
    assert atr[fib.ATR_PERIOD - 1] is not None
    assert atr[fib.ATR_PERIOD - 1] > 0
    assert atr[-1] is not None


def test_compute_atr_is_causal_future_bars_dont_affect_past_value():
    candles = _series(fib.ATR_PERIOD + 5)
    atr_full = fib.compute_atr(candles)
    atr_truncated = fib.compute_atr(candles[: fib.ATR_PERIOD + 2])
    assert atr_full[fib.ATR_PERIOD + 1] == atr_truncated[fib.ATR_PERIOD + 1]


def test_levels_fibonacci_none_before_any_range():
    candles = _series(5)
    level, bias = fib.levels_fibonacci(candles, window=40)
    assert level[0] is None and bias[0] is None


def test_levels_fibonacci_vente_bias_when_low_is_most_recent_extreme():
    # Mouvement haut -> bas : le plus bas le plus recent -> retracement
    # HAUT attendu, biais VENTE (voir docstring de la fonction portee).
    candles = _series(60, start_price=2000.0)
    candles[10]["high"] = 2050.0  # haut ancien
    candles[50]["low"] = 1950.0   # bas recent (apres le haut)
    level, bias = fib.levels_fibonacci(candles, window=60, fib_pct=0.618)
    assert bias[59] == "vente"
    expected = 1950.0 + 0.618 * (2050.0 - 1950.0)
    assert level[59] == expected


def test_levels_fibonacci_achat_bias_when_high_is_most_recent_extreme():
    candles = _series(60, start_price=2000.0)
    candles[10]["low"] = 1950.0   # bas ancien
    candles[50]["high"] = 2050.0  # haut recent (apres le bas)
    level, bias = fib.levels_fibonacci(candles, window=60, fib_pct=0.618)
    assert bias[59] == "achat"
    expected = 2050.0 - 0.618 * (2050.0 - 1950.0)
    assert level[59] == expected


def test_daily_trend_strength_zero_before_window_days():
    candles = []
    for d in range(5):
        t = datetime(2026, 9, 1 + d, 10, 0, tzinfo=timezone.utc)
        candles.append(_candle(t.strftime("%Y-%m-%d %H:%M:%S"), 2000, 2001, 1999, 2000 + d))
    strength = fib.daily_trend_strength(candles, window_days=20)
    days = sorted({c["time"][:10] for c in candles})
    assert all(strength[d] == 0.0 for d in days)


def test_daily_trend_strength_positive_in_an_uptrend():
    candles = []
    for d in range(25):
        t = datetime(2026, 9, 1 + d, 10, 0, tzinfo=timezone.utc)
        price = 2000.0 + d * 2.0  # hausse reguliere
        candles.append(_candle(t.strftime("%Y-%m-%d %H:%M:%S"), price, price + 1, price - 1, price))
    strength = fib.daily_trend_strength(candles, window_days=20)
    last_day = sorted({c["time"][:10] for c in candles})[-1]
    assert strength[last_day] > 0
