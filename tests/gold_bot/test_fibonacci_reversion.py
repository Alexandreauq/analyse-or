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


def test_latest_state_none_when_empty():
    assert fib.latest_state([]) is None


def test_latest_state_none_before_atr_period():
    candles = _series(fib.ATR_PERIOD - 1)
    assert fib.latest_state(candles) is None


def test_latest_state_returns_expected_keys():
    candles = _series(fib.ATR_PERIOD + fib.FIB_WINDOW + 5)
    state = fib.latest_state(candles)
    assert state is not None
    for key in ("close", "high", "low", "level", "bias", "atr", "trend_strength", "hour_utc"):
        assert key in state


def test_latest_state_hour_utc_matches_last_candle():
    candles = _series(fib.ATR_PERIOD + fib.FIB_WINDOW + 5,
                       start_dt=datetime(2026, 9, 1, 3, 0, tzinfo=timezone.utc))
    state = fib.latest_state(candles)
    last_hour = datetime.strptime(candles[-1]["time"], "%Y-%m-%d %H:%M:%S").hour
    assert state["hour_utc"] == last_hour


def _series_with_level(n, start_price=2000.0, start_dt=None):
    """Série causant un retracement ACHAT bien défini (bas ancien à
    l'indice 21, haut récent à l'indice 55 -> hi_idx > lo_idx -> niveau
    achat), avec un bruit de fond MINUSCULE (+-0.1) partout ailleurs --
    la cible (entry_idx = ATR_PERIOD+FIB_WINDOW+5) tombe après les deux
    anomalies, qui restent dans la fenêtre de 40 bougies de
    levels_fibonacci (40 bougies avant l'indice 59 = indices 19..58,
    qui couvre bien 21 et 55) mais sont assez ANCIENNES par rapport à
    `compute_atr` (EMA causale, k=1/14) pour que leur contribution à
    atr[59] soit fortement amortie (décroissance exponentielle sur
    ~38-40 bougies) -- jamais annulée exactement, d'où les marges
    volontairement larges dans les tests ci-dessous (ne jamais
    comparer `result` à une valeur numérique exacte ici, seulement à
    `current_stop_loss` ou à `None`)."""
    start_dt = start_dt or datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    out = []
    for i in range(n):
        t = start_dt + timedelta(minutes=5 * i)
        if i == 21:
            price = 1950.0  # bas ancien
        elif i == 55:
            price = 2050.0  # haut recent
        else:
            price = 2000.0
        out.append(_candle(t.strftime("%Y-%m-%d %H:%M:%S"), price, price + 0.1, price - 0.1, price))
    return out


def test_compute_trailing_stop_none_when_entry_time_before_all_candles():
    candles = _series_with_level(fib.ATR_PERIOD + fib.FIB_WINDOW + 20)
    result = fib.compute_trailing_stop(candles, "achat", "2020-01-01T00:00:00+00:00", 2000.0, 1990.0)
    assert result is None


def test_compute_trailing_stop_none_when_excursion_insufficient():
    candles = _series_with_level(fib.ATR_PERIOD + fib.FIB_WINDOW + 20)
    entry_idx = fib.ATR_PERIOD + fib.FIB_WINDOW + 5  # 59 -- apres les deux anomalies (21, 55)
    entry_price = candles[entry_idx]["close"]
    entry_time_iso = candles[entry_idx]["time"].replace(" ", "T") + "+00:00"
    for c in candles[entry_idx + 1:]:
        c["open"] = c["high"] = c["low"] = c["close"] = entry_price  # aucune excursion apres l'entree
    result = fib.compute_trailing_stop(candles, "achat", entry_time_iso, entry_price, entry_price - 5.0)
    assert result is None


def test_compute_trailing_stop_moves_stop_up_once_activated_achat():
    candles = _series_with_level(fib.ATR_PERIOD + fib.FIB_WINDOW + 20)
    entry_idx = fib.ATR_PERIOD + fib.FIB_WINDOW + 5
    entry_price = candles[entry_idx]["close"]
    entry_time_iso = candles[entry_idx]["time"].replace(" ", "T") + "+00:00"
    # Excursion tres large (+200) pour depasser R quelle que soit la
    # contamination residuelle d'ATR par les anomalies 21/55 (R reste
    # necessairement d'un ordre de grandeur bien inferieur, vu
    # l'amortissement exponentiel sur ~38-40 bougies -- voir docstring
    # de _series_with_level).
    candles[entry_idx + 2]["high"] = entry_price + 200.0
    current_stop_loss = entry_price - 5.0
    result = fib.compute_trailing_stop(candles, "achat", entry_time_iso, entry_price, current_stop_loss)
    assert result is not None
    assert result > current_stop_loss


def test_compute_trailing_stop_never_loosens():
    candles = _series_with_level(fib.ATR_PERIOD + fib.FIB_WINDOW + 20)
    entry_idx = fib.ATR_PERIOD + fib.FIB_WINDOW + 5
    entry_price = candles[entry_idx]["close"]
    entry_time_iso = candles[entry_idx]["time"].replace(" ", "T") + "+00:00"
    candles[entry_idx + 2]["high"] = entry_price + 200.0
    # Deja tres au-dessus de tout candidat possible (candidat <= meilleur
    # prix atteint depuis l'entree, lui-meme <= entry_price + 200).
    current_stop_loss = entry_price + 500.0
    result = fib.compute_trailing_stop(candles, "achat", entry_time_iso, entry_price, current_stop_loss)
    assert result is None
