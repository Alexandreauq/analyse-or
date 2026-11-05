# tests/gold_bot/test_ichimoku_reversion.py
from datetime import datetime, timezone

import gold_bot.ichimoku_reversion as ichimoku_reversion


def _candle(time_str, o, h, l, c, vol=100):
    return {"time": time_str, "open": o, "high": h, "low": l, "close": c, "tick_volume": vol, "spread": 1}


def test_resample_h1_aggregates_5min_bars_into_hourly_buckets():
    candles = [
        _candle("2026-01-05 10:00:00", 100, 101, 99, 100.5),
        _candle("2026-01-05 10:05:00", 100.5, 102, 100, 101.5),
        _candle("2026-01-05 10:55:00", 101.5, 103, 101, 102.0),
        _candle("2026-01-05 11:00:00", 102.0, 102.5, 101.8, 102.2),
    ]
    h1 = ichimoku_reversion.resample_h1(candles)
    assert len(h1) == 2
    assert h1[0]["time"] == "2026-01-05T10:00:00"
    assert h1[0]["open"] == 100
    assert h1[0]["high"] == 103
    assert h1[0]["low"] == 99
    assert h1[0]["close"] == 102.0
    assert h1[1]["time"] == "2026-01-05T11:00:00"


def test_compute_atr_h1_is_none_before_warmup_then_positive():
    candles_h1 = [_candle(f"2026-01-{d:02d} 00:00:00", 100 + d, 101 + d, 99 + d, 100.5 + d) for d in range(1, 20)]
    atr = ichimoku_reversion.compute_atr_h1(candles_h1, period=14)
    assert atr[12] is None
    assert atr[13] is not None
    assert atr[13] > 0


def test_compute_ichimoku_tenkan_kijun_are_midpoints_of_rolling_high_low():
    candles_h1 = [_candle(f"2026-01-01 {h:02d}:00:00", 100, 100 + h, 100 - h, 100) for h in range(10)]
    ind = ichimoku_reversion.compute_ichimoku(candles_h1, tenkan_period=3, kijun_period=5, senkou_b_period=5, shift=2)
    # a l'indice 9 (10e bougie), plus haut/plus bas sur les 3 dernieres bougies (indices 7,8,9) = high max 109 / low min 91
    assert ind["tenkan"][9] == (109 + 91) / 2.0
    # senkou_a[9] est decale : il reprend la valeur brute calculee a l'indice 9-2=7
    assert ind["senkou_a"][9] == ind["tenkan"][7] and ind["senkou_a"][9] is not None


def test_compute_ichimoku_senkou_is_none_before_shift():
    candles_h1 = [_candle(f"2026-01-01 {h:02d}:00:00", 100, 101, 99, 100) for h in range(5)]
    ind = ichimoku_reversion.compute_ichimoku(candles_h1, tenkan_period=3, kijun_period=3, senkou_b_period=3, shift=10)
    assert all(v is None for v in ind["senkou_a"])
    assert all(v is None for v in ind["senkou_b"])
