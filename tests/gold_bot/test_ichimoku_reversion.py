# tests/gold_bot/test_ichimoku_reversion.py
from datetime import datetime, timedelta, timezone

import gold_bot.ichimoku_reversion as ichimoku_reversion


def _realistic_fill_time(candle_time_str):
    """Horodatage de remplissage realiste pour un `entry_time_iso` de
    test : cloture H1 de la bougie (ouverture + 1h) + 30s, jamais
    l'heure d'ouverture brute -- meme principe que
    fibonacci_reversion.compute_trailing_stop (le remplissage reel
    arrive toujours apres la cloture de la bougie d'entree)."""
    return (ichimoku_reversion._parse(candle_time_str) + timedelta(hours=1, seconds=30)).isoformat()


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


def _build_bullish_cross_history():
    """78 bougies H1 (MIN_H1_CANDLES) avec un prix stable puis un
    croisement Tenkan/Kijun haussier confirme par un prix au-dessus du
    nuage sur la derniere bougie."""
    from datetime import timedelta
    candles = []
    base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    price = 100.0
    for h in range(80):
        ts = (base + timedelta(hours=h)).strftime("%Y-%m-%d %H:00:00")
        if h < 70:
            o, hh, l, c = price, price + 0.5, price - 0.5, price
        else:
            # pousse le prix a la hausse sur les 10 dernieres bougies pour provoquer un croisement
            price += 2.0
            o, hh, l, c = price - 2.0, price + 0.5, price - 2.5, price
        candles.append(_candle(ts, o, hh, l, c))
    return candles


def _h1_to_5min(candles_h1):
    """Convertit une liste de bougies H1 en bougies 5min equivalentes
    (une seule bougie 5min par heure, au prorata de l'OHLC) -- suffisant
    pour que resample_h1() reconstruise exactement les memes buckets."""
    out = []
    for c in candles_h1:
        t = ichimoku_reversion._parse(c["time"])
        out.append(_candle(t.strftime("%Y-%m-%d %H:00:00"), c["open"], c["high"], c["low"], c["close"]))
    return out


def test_latest_state_returns_none_when_history_too_short():
    candles_5min = _h1_to_5min(_build_bullish_cross_history())[:50]  # < MIN_H1_CANDLES
    assert ichimoku_reversion.latest_state(candles_5min) is None


def test_latest_state_returns_full_state_with_enough_history():
    candles_5min = _h1_to_5min(_build_bullish_cross_history())
    state = ichimoku_reversion.latest_state(candles_5min)
    assert state is not None
    for key in ("close", "high", "low", "atr", "tenkan", "tenkan_prev", "kijun", "kijun_prev", "senkou_a", "senkou_b", "hour_utc"):
        assert key in state
    assert state["atr"] > 0


def test_signal_returns_achat_on_bullish_tk_cross_above_cloud():
    state = {
        "tenkan_prev": 99.0, "kijun_prev": 100.0, "tenkan": 101.0, "kijun": 100.0,
        "close": 105.0, "senkou_a": 102.0, "senkou_b": 101.0,
    }
    assert ichimoku_reversion.signal(state) == "achat"


def test_signal_returns_none_when_cross_not_confirmed_by_cloud():
    state = {
        "tenkan_prev": 99.0, "kijun_prev": 100.0, "tenkan": 101.0, "kijun": 100.0,
        "close": 95.0, "senkou_a": 102.0, "senkou_b": 101.0,  # close sous le nuage malgre le croisement haussier
    }
    assert ichimoku_reversion.signal(state) is None


def test_signal_returns_vente_on_bearish_tk_cross_below_cloud():
    state = {
        "tenkan_prev": 101.0, "kijun_prev": 100.0, "tenkan": 99.0, "kijun": 100.0,
        "close": 95.0, "senkou_a": 98.0, "senkou_b": 97.0,
    }
    assert ichimoku_reversion.signal(state) == "vente"


def _build_bearish_cross_history():
    """Symetrique de _build_bullish_cross_history : prix stable puis
    croisement Tenkan/Kijun baissier (descente de 2.0 par bougie H1 sur
    les 10 dernieres bougies)."""
    from datetime import timedelta
    candles = []
    base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    price = 100.0
    for h in range(80):
        ts = (base + timedelta(hours=h)).strftime("%Y-%m-%d %H:00:00")
        if h < 70:
            o, hh, l, c = price, price + 0.5, price - 0.5, price
        else:
            price -= 2.0
            o, hh, l, c = price + 2.0, price + 2.5, price - 0.5, price
        candles.append(_candle(ts, o, hh, l, c))
    return candles


def test_compute_trailing_stop_returns_none_before_1r_excursion():
    # Entree a l'indice H1 78 (candle_5min index 78, prix encore en pleine
    # montee), PAS la derniere bougie -- sinon since_entry serait vide et
    # le test ne verifierait plus rien sur le calcul d'excursion.
    # A l'indice H1 78 : kijun_entry=109.0, atr_entry=1.9734789835322215
    # (verifie via script jetable, meme fonctions que la production).
    # initial_stop (achat) = 109.0 - 0.5*1.9734789835322215 = 108.01326050823389
    # r_distance = |118.0 - 108.01326050823389| = 9.98673949176611
    # since_entry = [candle idx 79] seul, high=120.5
    # excursion_r = (120.5 - 118.0) / 9.98673949176611 = 0.2503... < 1R => None
    candles_5min = _h1_to_5min(_build_bullish_cross_history())
    entry_time_iso = _realistic_fill_time(candles_5min[78]["time"])
    result = ichimoku_reversion.compute_trailing_stop(
        candles_5min, "achat", entry_time_iso, entry_price=118.0, current_stop_loss=105.0)
    assert result is None


def test_compute_trailing_stop_tightens_stop_on_achat_happy_path():
    # Entree a l'indice H1 70 (premiere bougie de la montee) :
    # kijun_entry=101.0, atr_entry=1.1428571428571428 (8/7).
    # initial_stop (achat) = 101.0 - 0.5*1.1428571428571428 = 100.42857142857143
    # r_distance = |102.0 - 100.42857142857143| = 1.5714285714285714
    # since_entry = indices 71..79, best high = 120.5 (idx 79)
    # excursion_r = (120.5 - 102.0) / 1.5714285714285714 = 11.77 >= 1R => trailing actif
    # kijun_i (derniere bougie H1, idx 79) = 110.0, ATR TOUJOURS celui FIGE
    # a l'entree (1.1428571428571428, jamais l'ATR courant de idx 79)
    # candidate = max(102.0, 110.0 - 0.5*1.1428571428571428) = 109.42857142857143
    # current_stop_loss = 100.42857142857143 (le stop initial, pas encore
    # resserre) => new_stop = max(100.42857142857143, 109.42857142857143)
    #            = 109.42857142857143 (!= current_stop_loss)
    candles_5min = _h1_to_5min(_build_bullish_cross_history())
    entry_time_iso = _realistic_fill_time(candles_5min[70]["time"])
    result = ichimoku_reversion.compute_trailing_stop(
        candles_5min, "achat", entry_time_iso, entry_price=102.0, current_stop_loss=100.42857142857143)
    assert result == 109.42857142857143


def test_compute_trailing_stop_tightens_stop_on_vente_happy_path():
    # Symetrique du cas achat ci-dessus, sur _build_bearish_cross_history.
    # Entree a l'indice H1 70 : kijun_entry=99.0, atr_entry=1.1428571428571428.
    # initial_stop (vente) = 99.0 + 0.5*1.1428571428571428 = 99.57142857142857
    # r_distance = |98.0 - 99.57142857142857| = 1.5714285714285714
    # since_entry = indices 71..79, best low = 79.5 (idx 79)
    # excursion_r = (98.0 - 79.5) / 1.5714285714285714 = 11.77 >= 1R
    # kijun_i (idx 79) = 90.0, atr_entry fige = 1.1428571428571428
    # candidate = min(98.0, 90.0 + 0.5*1.1428571428571428) = 90.57142857142857
    # current_stop_loss = 99.57142857142857 (stop initial) => new_stop
    #            = min(99.57142857142857, 90.57142857142857) = 90.57142857142857
    candles_5min = _h1_to_5min(_build_bearish_cross_history())
    entry_time_iso = _realistic_fill_time(candles_5min[70]["time"])
    result = ichimoku_reversion.compute_trailing_stop(
        candles_5min, "vente", entry_time_iso, entry_price=98.0, current_stop_loss=99.57142857142857)
    assert result == 90.57142857142857


def test_compute_trailing_stop_excludes_entry_candles_own_extreme():
    # Verrouille la regression du defaut 3 (since_entry incluait avant la
    # bougie d'entree elle-meme) : construit une bougie H1 d'entree avec
    # un high extreme (150.0) PLUS GRAND que tout ce qui suit, sur un
    # historique sinon totalement plat (100.0 partout). Verifie
    # independamment (script jetable, memes fonctions que la production) :
    # kijun_entry=124.75, atr_entry=4.535714285714286 (la bougie spike
    # elle-meme entre dans la fenetre glissante kijun/ATR, incluse par
    # definition -- cf. _rolling_high_low).
    # initial_stop (achat) = 124.75 - 0.5*4.535714285714286 = 122.48214285714286
    # r_distance = |100.0 - 122.48214285714286| = 22.48214285714286
    # since_entry STRICTEMENT APRES l'entree (code corrige) : best high
    # = 100.5 (bougies plates) => excursion_r = 0.5/22.48 = 0.022 < 1R => None.
    # Si since_entry incluait encore la bougie d'entree (ancien bug,
    # `>=` au lieu du slice `entry_5min_idx+1:`), best high serait 150.0
    # (la bougie d'entree elle-meme) => excursion_r = 50/22.48 = 2.22 >= 1R,
    # et la fonction renverrait un nouveau stop au lieu de None -- ce test
    # echouerait si le defaut 3 etait reintroduit.
    from datetime import timedelta
    base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    candles_5min = []
    for h in range(80):
        ts = (base + timedelta(hours=h)).strftime("%Y-%m-%d %H:00:00")
        candles_5min.append(_candle(ts, 100.0, 100.5, 99.5, 100.0))
    entry_idx = 70
    candles_5min[entry_idx] = _candle(candles_5min[entry_idx]["time"], 100.0, 150.0, 99.5, 100.0)
    entry_time_iso = _realistic_fill_time(candles_5min[entry_idx]["time"])
    result = ichimoku_reversion.compute_trailing_stop(
        candles_5min, "achat", entry_time_iso, entry_price=100.0, current_stop_loss=122.48214285714286)
    assert result is None


def test_compute_trailing_stop_never_loosens_an_already_tighter_stop():
    # Meme scenario que le chemin heureux achat (candidate=109.42857142857143
    # au lieu du candidat calcule), mais current_stop_loss=115.0 est DEJA
    # plus resserre (plus proche du prix) que le candidat calcule :
    # new_stop = max(115.0, 109.42857142857143) = 115.0 == current_stop_loss
    # => aucun changement, la fonction ne doit jamais reculer le stop.
    candles_5min = _h1_to_5min(_build_bullish_cross_history())
    entry_time_iso = _realistic_fill_time(candles_5min[70]["time"])
    result = ichimoku_reversion.compute_trailing_stop(
        candles_5min, "achat", entry_time_iso, entry_price=102.0, current_stop_loss=115.0)
    assert result is None
