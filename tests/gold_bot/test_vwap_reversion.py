from datetime import datetime, timedelta, timezone

import pytest

import gold_bot.vwap_reversion as vwap_reversion


def _candle(time_str, open_, high, low, close, tick_volume=1000, spread=20):
    return {"time": time_str, "open": open_, "high": high, "low": low, "close": close,
            "tick_volume": tick_volume, "spread": spread}


def test_resample_15min_groups_three_5min_candles_into_one():
    candles = [
        _candle("2026-09-29 13:00:00", 2000, 2005, 1998, 2002, tick_volume=100),
        _candle("2026-09-29 13:05:00", 2002, 2008, 2001, 2006, tick_volume=200),
        _candle("2026-09-29 13:10:00", 2006, 2010, 2004, 2003, tick_volume=150),
    ]
    result = vwap_reversion.resample_15min(candles)
    assert len(result) == 1
    bucket = result[0]
    assert bucket["time"] == "2026-09-29T13:00:00+00:00"
    assert bucket["open"] == 2000
    assert bucket["high"] == 2010
    assert bucket["low"] == 1998
    assert bucket["close"] == 2003
    assert bucket["tick_volume"] == 450


def test_resample_15min_splits_across_bucket_boundary():
    candles = [
        _candle("2026-09-29 13:10:00", 2000, 2005, 1998, 2002),
        _candle("2026-09-29 13:15:00", 2002, 2008, 2001, 2006),
    ]
    result = vwap_reversion.resample_15min(candles)
    assert len(result) == 2
    assert result[0]["time"] == "2026-09-29T13:00:00+00:00"
    assert result[1]["time"] == "2026-09-29T13:15:00+00:00"


def _build_15min_series(n, start_price=2000.0, start_dt=None):
    """Genere n bougies 15min consecutives, prix constant (variations
    minimes pour eviter un ecart-type strictement nul), utile pour les
    tests d'echauffement EMA200/session."""
    start_dt = start_dt or datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    out = []
    price = start_price
    for i in range(n):
        t = start_dt + timedelta(minutes=15 * i)
        price += 0.01 if i % 2 == 0 else -0.01
        out.append({
            "time": t.isoformat(), "open": price, "high": price + 0.5, "low": price - 0.5,
            "close": price, "tick_volume": 1000, "spread": 20,
        })
    return out


def test_compute_indicators_resets_vwap_daily_but_not_ema():
    candles = _build_15min_series(vwap_reversion.EMA200_PERIOD + 10)
    vwap_reversion.compute_indicators(candles)
    # bars_into_session doit repartir a 1 au tout premier candle de chaque
    # jour calendaire UTC distinct.
    first_day = datetime.fromisoformat(candles[0]["time"]).date()
    seen_days = {first_day}
    for c in candles[1:]:
        day = datetime.fromisoformat(c["time"]).date()
        if day not in seen_days:
            assert c["bars_into_session"] == 1
            seen_days.add(day)
    # EMA200 continue de bouger meme apres un changement de jour (jamais
    # remise a la valeur du prix courant comme la VWAP).
    assert candles[-1]["ema200"] != candles[-1]["close"]


def test_latest_state_none_when_not_enough_candles_after_resampling():
    candles_5min = []
    t = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for i in range(vwap_reversion.EMA200_PERIOD):  # bien moins que necessaire une fois resample en 15min
        candles_5min.append(_candle((t + timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M:%S"),
                                     2000, 2001, 1999, 2000))
    assert vwap_reversion.latest_state(candles_5min) is None


def test_latest_state_returns_dict_with_expected_keys_when_enough_history():
    candles_15min = _build_15min_series(vwap_reversion.EMA200_PERIOD + 5)
    # reconstruit des bougies 5min equivalentes (3 par bougie 15min) pour
    # passer par le vrai chemin resample_15min + compute_indicators.
    candles_5min = []
    for c in candles_15min:
        base_t = datetime.fromisoformat(c["time"])
        for j in range(3):
            candles_5min.append(_candle((base_t + timedelta(minutes=5 * j)).strftime("%Y-%m-%d %H:%M:%S"),
                                         c["open"], c["high"], c["low"], c["close"], c["tick_volume"] // 3))

    state = vwap_reversion.latest_state(candles_5min)
    assert state is not None
    for key in ("close", "vwap", "vwap_std", "ema200", "ema50", "hour_utc", "bars_into_session"):
        assert key in state


def test_latest_state_vwap_std_is_zero_or_positive():
    candles_15min = _build_15min_series(vwap_reversion.EMA200_PERIOD + 5)
    candles_5min = []
    for c in candles_15min:
        base_t = datetime.fromisoformat(c["time"])
        for j in range(3):
            candles_5min.append(_candle((base_t + timedelta(minutes=5 * j)).strftime("%Y-%m-%d %H:%M:%S"),
                                         c["open"], c["high"], c["low"], c["close"]))
    state = vwap_reversion.latest_state(candles_5min)
    assert state["vwap_std"] >= 0


def _to_5min(candles_15min):
    """Reconstruit des bougies 5min equivalentes (3 par bougie 15min) pour
    passer par le vrai chemin resample_15min + compute_indicators."""
    out = []
    for c in candles_15min:
        base_t = datetime.fromisoformat(c["time"])
        for j in range(3):
            out.append(_candle((base_t + timedelta(minutes=5 * j)).strftime("%Y-%m-%d %H:%M:%S"),
                                c["open"], c["high"], c["low"], c["close"], c["tick_volume"]))
    return out


def test_compute_trailing_stop_none_when_entry_time_before_all_candles():
    candles_15min = _build_15min_series(vwap_reversion.EMA200_PERIOD + 20)
    result = vwap_reversion.compute_trailing_stop(
        _to_5min(candles_15min), "achat", "2020-01-01T00:00:00+00:00", 2000.0, 1990.0,
    )
    assert result is None


def test_compute_trailing_stop_none_when_breakeven_not_reached_achat():
    candles_15min = _build_15min_series(vwap_reversion.EMA200_PERIOD + 20)
    entry_idx = vwap_reversion.EMA200_PERIOD + 5
    entry_price = candles_15min[entry_idx]["close"]
    entry_time_iso = candles_15min[entry_idx]["time"]
    # aucune excursion favorable apres l'entree -- prix strictement plat.
    for c in candles_15min[entry_idx + 1:]:
        c["open"] = c["high"] = c["low"] = c["close"] = entry_price

    result = vwap_reversion.compute_trailing_stop(
        _to_5min(candles_15min), "achat", entry_time_iso, entry_price, entry_price - 5.0,
    )
    assert result is None


def test_compute_trailing_stop_moves_stop_up_when_breakeven_reached_achat():
    candles_15min = _build_15min_series(vwap_reversion.EMA200_PERIOD + 20)
    entry_idx = vwap_reversion.EMA200_PERIOD + 5
    entry_price = candles_15min[entry_idx]["close"]
    entry_time_iso = candles_15min[entry_idx]["time"]
    # grosse excursion favorable, tres au-dela de R (distance minime sur
    # cette serie quasi plate) -- declenche a coup sur le seuil.
    favorable_idx = entry_idx + 2
    candles_15min[favorable_idx]["high"] = entry_price + 10.0

    current_stop_loss = entry_price - 5.0  # nettement moins protecteur que ce que l'EMA50 suggererait
    result = vwap_reversion.compute_trailing_stop(
        _to_5min(candles_15min), "achat", entry_time_iso, entry_price, current_stop_loss,
    )
    assert result is not None
    assert result > current_stop_loss


def test_compute_trailing_stop_none_when_candidate_would_loosen_the_stop():
    candles_15min = _build_15min_series(vwap_reversion.EMA200_PERIOD + 20)
    entry_idx = vwap_reversion.EMA200_PERIOD + 5
    entry_price = candles_15min[entry_idx]["close"]
    entry_time_iso = candles_15min[entry_idx]["time"]
    favorable_idx = entry_idx + 2
    candles_15min[favorable_idx]["high"] = entry_price + 10.0

    # stop deja bien au-dessus de ce que le suiveur calculerait (candidat
    # toujours <= ema50 + une petite marge, proche de entry_price sur
    # cette serie quasi plate) -- ne doit jamais reculer.
    current_stop_loss = entry_price + 50.0
    result = vwap_reversion.compute_trailing_stop(
        _to_5min(candles_15min), "achat", entry_time_iso, entry_price, current_stop_loss,
    )
    assert result is None


def test_compute_trailing_stop_moves_stop_down_when_breakeven_reached_vente():
    candles_15min = _build_15min_series(vwap_reversion.EMA200_PERIOD + 20)
    entry_idx = vwap_reversion.EMA200_PERIOD + 5
    entry_price = candles_15min[entry_idx]["close"]
    entry_time_iso = candles_15min[entry_idx]["time"]
    favorable_idx = entry_idx + 2
    candles_15min[favorable_idx]["low"] = entry_price - 10.0

    current_stop_loss = entry_price + 5.0
    result = vwap_reversion.compute_trailing_stop(
        _to_5min(candles_15min), "vente", entry_time_iso, entry_price, current_stop_loss,
    )
    assert result is not None
    assert result < current_stop_loss
