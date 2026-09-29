from datetime import datetime, timedelta, timezone

import pytest

import gold_bot.session_breakout as session_breakout


def _candle(dt, open_, high, low, close, tick_volume=100, spread=1):
    return {"time": dt.strftime("%Y-%m-%d %H:%M:%S"), "open": open_, "high": high, "low": low,
            "close": close, "tick_volume": tick_volume, "spread": spread}


def _asian_session_candles(day_start, high=2010.0, low=2000.0, count=48, tick_volume=100):
    """`count` bougies de 5min à partir de `day_start` (00:00 UTC), toutes
    au milieu du range sauf 2 qui portent exactement `high`/`low` -- range
    asiatique contrôlé pour les tests."""
    mid = (high + low) / 2
    candles = [_candle(day_start + timedelta(minutes=5 * i), mid, mid, mid, mid, tick_volume) for i in range(count)]
    candles[0]["high"] = high
    candles[1]["low"] = low
    return candles


def _lookback_candles(start, count, price=2005.0, tick_volume=100):
    """`count` bougies neutres de 5min à partir de `start`, prix constant
    -- sert à remplir VOLUME_CONFIRMATION_LOOKBACK avant une bougie de
    cassure."""
    return [_candle(start + timedelta(minutes=5 * i), price, price + 1, price - 1, price, tick_volume)
            for i in range(count)]


DAY = datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc)  # lundi, loin de tout évènement macro
TRADING_START = datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc)


def test_compute_asian_range_returns_high_low_when_enough_candles():
    candles = _asian_session_candles(DAY, high=2010.0, low=2000.0, count=48)
    result = session_breakout.compute_asian_range(candles, TRADING_START)
    assert result == {"high": 2010.0, "low": 2000.0}


def test_compute_asian_range_none_when_too_few_candles():
    candles = _asian_session_candles(DAY, count=20)  # < MIN_ASIAN_SESSION_CANDLES (48)
    assert session_breakout.compute_asian_range(candles, TRADING_START) is None


def test_compute_asian_range_none_when_window_does_not_reach_session_start():
    # 48+ bougies, toutes dans la fenêtre 00h-08h, mais la plus ancienne
    # commence à 02h -- la fenêtre fournie ne remonte pas jusqu'au début
    # réel de la session (00h). Avant le correctif, le nombre de bougies
    # suffisait à produire un range tronqué et à le faire passer pour le
    # vrai range asiatique complet.
    late_session_start = DAY + timedelta(hours=2)
    candles = _asian_session_candles(late_session_start, high=2010.0, low=2000.0, count=48)
    assert session_breakout.compute_asian_range(candles, TRADING_START) is None


def test_compute_asian_range_ignores_candles_outside_asian_window():
    candles = _asian_session_candles(DAY, high=2010.0, low=2000.0, count=48)
    # Bougie de la fenêtre de trading (08h) avec un extrême bien plus large --
    # ne doit pas influencer le range asiatique.
    candles.append(_candle(TRADING_START, 1900.0, 2500.0, 1900.0, 2000.0))
    result = session_breakout.compute_asian_range(candles, TRADING_START)
    assert result == {"high": 2010.0, "low": 2000.0}


def test_compute_signal_neutre_outside_trading_window():
    candles = _asian_session_candles(DAY, count=48)
    late = datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc)  # hors 08h-16h UTC
    candles.append(_candle(late - timedelta(minutes=5), 2005, 2006, 2004, 2005))
    candles.append(_candle(late, 2011, 2015, 2010, 2013, tick_volume=1000))
    result = session_breakout.compute_signal(candles)
    assert result["status"] == "neutre"
    assert result["entry"] is None


def test_compute_signal_neutre_when_asian_range_invalid():
    candles = _asian_session_candles(DAY, count=20)  # < MIN_ASIAN_SESSION_CANDLES
    candles.append(_candle(TRADING_START, 2011, 2015, 2010, 2013, tick_volume=1000))
    result = session_breakout.compute_signal(candles)
    assert result["status"] == "neutre"


def test_compute_signal_achat_on_fresh_upward_crossing_with_volume_confirmation():
    candles = _asian_session_candles(DAY, high=2010.0, low=2000.0, count=48, tick_volume=100)
    candles += _lookback_candles(TRADING_START, 21, price=2005.0, tick_volume=100)
    # dernière bougie du lookback (index -1 avant la cassure) reste sous range_high (2010)
    breakout_time = TRADING_START + timedelta(minutes=5 * 21)
    candles.append(_candle(breakout_time, 2009, 2013, 2008, 2012, tick_volume=200))  # 200 > 1.5*100

    result = session_breakout.compute_signal(candles)

    assert result["status"] == "achat"
    assert result["entry"] == 2012
    assert result["stop_loss"] == pytest.approx(2010.0 - 1.0)  # range_high - CHARTPATTERN_STOP_BUFFER
    assert result["take_profit"] == pytest.approx(2012.0 + 10.0)  # breakout + (range_high-range_low)
    assert result["trend"] == "haussier"
    assert result["pattern"] is None


def test_compute_signal_vente_on_fresh_downward_crossing_with_volume_confirmation():
    candles = _asian_session_candles(DAY, high=2010.0, low=2000.0, count=48, tick_volume=100)
    candles += _lookback_candles(TRADING_START, 21, price=2005.0, tick_volume=100)
    breakout_time = TRADING_START + timedelta(minutes=5 * 21)
    candles.append(_candle(breakout_time, 2001, 1997, 1996, 1998, tick_volume=200))  # close 1998 < range_low 2000

    result = session_breakout.compute_signal(candles)

    assert result["status"] == "vente"
    assert result["entry"] == 1998
    assert result["stop_loss"] == pytest.approx(2000.0 + 1.0)
    assert result["take_profit"] == pytest.approx(1998.0 - 10.0)
    assert result["trend"] == "baissier"


def test_compute_signal_neutre_when_crossing_not_fresh():
    candles = _asian_session_candles(DAY, high=2010.0, low=2000.0, count=48, tick_volume=100)
    candles += _lookback_candles(TRADING_START, 20, price=2005.0, tick_volume=100)
    t1 = TRADING_START + timedelta(minutes=5 * 20)
    candles.append(_candle(t1, 2011, 2012, 2010.5, 2011, tick_volume=100))  # déjà au-dessus du range
    t2 = t1 + timedelta(minutes=5)
    candles.append(_candle(t2, 2011, 2015, 2010.5, 2013, tick_volume=300))  # toujours au-dessus, volume fort

    result = session_breakout.compute_signal(candles)
    assert result["status"] == "neutre"


def test_compute_signal_neutre_when_volume_not_confirmed():
    candles = _asian_session_candles(DAY, high=2010.0, low=2000.0, count=48, tick_volume=100)
    candles += _lookback_candles(TRADING_START, 21, price=2005.0, tick_volume=100)
    breakout_time = TRADING_START + timedelta(minutes=5 * 21)
    candles.append(_candle(breakout_time, 2009, 2013, 2008, 2012, tick_volume=120))  # 120 < 1.5*100=150

    result = session_breakout.compute_signal(candles)
    assert result["status"] == "neutre"


def test_compute_signal_neutre_when_risk_reward_insufficient():
    # Range très étroit (1.0) : risque (~buffer=1.0) proche de la récompense
    # (range_height=1.0) -> ratio < 1.5, rejeté par meets_minimum_risk_reward.
    candles = _asian_session_candles(DAY, high=2001.0, low=2000.0, count=48, tick_volume=100)
    candles += _lookback_candles(TRADING_START, 21, price=2000.5, tick_volume=100)
    breakout_time = TRADING_START + timedelta(minutes=5 * 21)
    candles.append(_candle(breakout_time, 2000.8, 2002, 2000.5, 2001.5, tick_volume=1000))

    result = session_breakout.compute_signal(candles)
    assert result["status"] == "neutre"


def test_compute_signal_neutre_during_news_blackout():
    # CPI du 11/09/2026, 12h30 UTC (voir confluence.SCALP_HIGH_IMPACT_EVENTS_UTC).
    day = datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc)
    candles = _asian_session_candles(day, high=2010.0, low=2000.0, count=48, tick_volume=100)
    trading_start = datetime(2026, 9, 11, 8, 0, tzinfo=timezone.utc)
    candles += _lookback_candles(trading_start, 20, price=2005.0, tick_volume=100)
    t1 = datetime(2026, 9, 11, 12, 25, tzinfo=timezone.utc)
    candles.append(_candle(t1, 2005, 2006, 2004, 2005, tick_volume=100))
    t2 = datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc)  # exactement le CPI
    candles.append(_candle(t2, 2009, 2013, 2008, 2012, tick_volume=1000))

    result = session_breakout.compute_signal(candles)
    assert result["status"] == "neutre"


def test_compute_signal_neutre_when_no_candles():
    assert session_breakout.compute_signal([])["status"] == "neutre"


def test_min_window_size_for_backtest_covers_full_asian_and_trading_window():
    # 16h de bougies 5min (00h-16h UTC) + 1 pour la bougie courante --
    # sinon backtest.simulate_trades(..., signal_fn=compute_signal) avec
    # son window_size par defaut (SIGNAL_WINDOW_SIZE=90, dimensionne pour
    # l'ancien moteur) tronque ou perd silencieusement le range asiatique
    # des le milieu de matinee.
    assert session_breakout.MIN_WINDOW_SIZE_FOR_BACKTEST == 193
