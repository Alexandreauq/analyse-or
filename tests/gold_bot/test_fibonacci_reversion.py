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


def _daily_history(num_days, start_price, daily_delta, start_day):
    """Un candle par jour calendaire, les `num_days` jours precedant
    `start_day` (jamais `start_day` lui-meme) -- assez pour
    daily_trend_strength, qui ne regarde que la cloture du dernier
    candle de chaque jour."""
    out = []
    price = start_price
    for d in range(num_days):
        day = start_day - timedelta(days=num_days - d)
        price += daily_delta
        out.append(_candle(day.strftime("%Y-%m-%d 23:55:00"), price - 0.1, price + 0.1, price - 0.1, price))
    return out


def test_latest_state_trend_strength_none_without_enough_merged_history():
    # `candles` seul (fenetre recente de quelques heures) ne couvre
    # jamais TREND_WINDOW_DAYS jours complets -- sans historical_candles,
    # un signal ne doit jamais etre laisse passer sans filtre fiable.
    candles = _series(fib.ATR_PERIOD + fib.FIB_WINDOW + 5,
                       start_dt=datetime(2026, 10, 1, tzinfo=timezone.utc))
    state = fib.latest_state(candles)
    assert state["trend_strength"] is None


def test_latest_state_trend_strength_none_at_exactly_window_days_of_history():
    # A exactement TREND_WINDOW_DAYS(20) jours avant aujourd'hui, le
    # jour retenu serait lui-meme le premier jour "mesurable" de
    # daily_trend_strength -- qui renvoie encore son 0.0 par DEFAUT
    # (historique insuffisant) a cet indice, pas une vraie mesure.
    # Trouve en contre-revue du correctif, 2026-10-28 (l'ancienne
    # condition "< TREND_WINDOW_DAYS" laissait passer ce cas-limite).
    today = datetime(2026, 10, 1, tzinfo=timezone.utc)
    candles = _series(fib.ATR_PERIOD + fib.FIB_WINDOW + 5, start_price=1960.0, start_dt=today)
    historical_exactly_20 = _daily_history(fib.TREND_WINDOW_DAYS, start_price=1900.0, daily_delta=2.0, start_day=today)
    assert fib.latest_state(candles, historical_exactly_20)["trend_strength"] is None

    historical_21 = _daily_history(fib.TREND_WINDOW_DAYS + 1, start_price=1900.0, daily_delta=2.0, start_day=today)
    assert fib.latest_state(candles, historical_21)["trend_strength"] is not None


def test_latest_state_trend_strength_uses_merged_historical_candles():
    today = datetime(2026, 10, 1, tzinfo=timezone.utc)
    historical = _daily_history(25, start_price=1900.0, daily_delta=2.0, start_day=today)
    candles = _series(fib.ATR_PERIOD + fib.FIB_WINDOW + 5, start_price=1960.0, start_dt=today)
    state = fib.latest_state(candles, historical)
    assert state["trend_strength"] is not None
    assert state["trend_strength"] > 0  # tendance haussiere reguliere dans l'historique


def test_latest_state_trend_strength_ignores_todays_own_partial_close():
    # Jamais la cloture (encore partielle en production) du jour
    # courant -- toujours celle du dernier jour calendaire COMPLET.
    # Modifier la derniere bougie d'aujourd'hui ne doit RIEN changer,
    # contrairement a la recherche de validation qui taguait (a tort)
    # chaque signal avec la cloture FINALE de son propre jour.
    today = datetime(2026, 10, 1, tzinfo=timezone.utc)
    historical = _daily_history(25, start_price=1900.0, daily_delta=2.0, start_day=today)
    candles = _series(fib.ATR_PERIOD + fib.FIB_WINDOW + 5, start_price=1960.0, start_dt=today)
    baseline = fib.latest_state(candles, historical)["trend_strength"]

    candles_modified = [dict(c) for c in candles]
    candles_modified[-1] = dict(candles_modified[-1])
    candles_modified[-1]["close"] = 5000.0
    modified = fib.latest_state(candles_modified, historical)["trend_strength"]

    assert modified == baseline


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


def _realistic_entry_time_iso(candles, entry_idx, delay_seconds=3):
    """Horodatage de remplissage REALISTE : quelques secondes apres la
    CLOTURE de la bougie signal (le remplissage a lieu pendant la
    bougie suivante), contrairement a l'horodatage a l'OUVERTURE de la
    bougie signal elle-meme utilise par convenance dans les tests
    ci-dessus (qui reste un entree valide, juste differente -- avant
    la cloture de cette bougie, donc resolue sur la bougie PRECEDENTE)."""
    close_dt = (datetime.strptime(candles[entry_idx]["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                + timedelta(minutes=fib.CANDLE_INTERVAL_MINUTES))
    return (close_dt + timedelta(seconds=delay_seconds)).isoformat()


def test_compute_trailing_stop_uses_signal_candle_atr_for_realistic_entry_time():
    # Avant le correctif du 2026-10-28 (revue finale de branche),
    # l'horodatage REALISTE (post-cloture) resolvait entry_idx sur la
    # bougie SUIVANTE, pas sur la bougie signal elle-meme -- donc sur
    # le mauvais ATR/niveau pour calculer R. Fond de prix (1988) proche
    # du niveau attendu pour que les bougies "normales" ne dominent pas
    # le plus haut depuis l'entree (contrairement a _series_with_level,
    # dont le fond a 2000 est loin au-dessus de cette zone).
    candles = [dict(c, high=1988.1, low=1987.9, open=1988.0, close=1988.0) if i not in (21, 55) else c
               for i, c in enumerate(_series_with_level(fib.ATR_PERIOD + fib.FIB_WINDOW + 20))]
    entry_idx = fib.ATR_PERIOD + fib.FIB_WINDOW + 5
    atr = fib.compute_atr(candles)
    level, _bias = fib.levels_fibonacci(candles)
    entry_price = level[entry_idx] + 1.0
    r_signal = entry_price - (level[entry_idx] - fib.STOP_BUFFER_ATR_MULT * atr[entry_idx])
    r_next = entry_price - (level[entry_idx + 1] - fib.STOP_BUFFER_ATR_MULT * atr[entry_idx + 1])
    assert r_next != r_signal  # sinon ce fixture ne distingue rien
    entry_time_iso = _realistic_entry_time_iso(candles, entry_idx)
    current_stop_loss = entry_price - 5.0

    # Excursion ENTRE r_next et r_signal : n'active le suiveur QUE si
    # le mauvais R (bougie suivante, bug) est utilise -- donc None ici
    # prouve que le code utilise bien R de la bougie SIGNAL.
    between = (r_signal + r_next) / 2
    candles_between = [dict(c) for c in candles]
    candles_between[entry_idx + 2]["high"] = entry_price + between
    result_between = fib.compute_trailing_stop(candles_between, "achat", entry_time_iso, entry_price, current_stop_loss)
    assert result_between is None

    candles_above = [dict(c) for c in candles]
    candles_above[entry_idx + 2]["high"] = entry_price + max(r_signal, r_next) * 1.5
    result_above = fib.compute_trailing_stop(candles_above, "achat", entry_time_iso, entry_price, current_stop_loss)
    assert result_above is not None  # excursion au-dessus des deux R -> active quoi qu'il arrive


def test_compute_trailing_stop_none_when_entry_bias_mismatches_direction():
    # Garde-fou : si le biais Fibonacci a la bougie d'entree retrouvee
    # ne correspond pas a la direction reelle de la position, on
    # refuse plutot que de calculer R avec le mauvais niveau --
    # excursion favorable large injectee pour que la SEULE raison du
    # None possible soit ce garde-fou (sans excursion, le None serait
    # trivial -- pas d'activation -- et ne prouverait rien sur le
    # garde-fou lui-meme, trouve en contre-revue du correctif).
    candles = _series_with_level(fib.ATR_PERIOD + fib.FIB_WINDOW + 20)
    entry_idx = fib.ATR_PERIOD + fib.FIB_WINDOW + 5
    entry_price = candles[entry_idx]["close"]
    entry_time_iso = candles[entry_idx]["time"].replace(" ", "T") + "+00:00"
    candles[entry_idx + 2]["low"] = entry_price - 200.0  # excursion favorable a une VENTE
    result = fib.compute_trailing_stop(candles, "vente", entry_time_iso, entry_price, entry_price + 5.0)
    assert result is None  # bias reel a entry_idx = "achat", pas "vente"


def test_compute_trailing_stop_never_tightens_beyond_entry_price():
    # Le plancher max(entry_price, candidat) doit ramener le stop
    # exactement a l'entree, jamais plus loin, meme quand le candidat
    # naturel (meilleur prix - TRAIL_BUFFER_ATR_MULT*atr) tombe en
    # dessous. Fond de prix proche du niveau (meme raison que le test
    # ci-dessus) pour isoler l'excursion au candle injecte.
    candles = [dict(c, high=1988.1, low=1987.9, open=1988.0, close=1988.0) if i not in (21, 55) else c
               for i, c in enumerate(_series_with_level(fib.ATR_PERIOD + fib.FIB_WINDOW + 20))]
    entry_idx = fib.ATR_PERIOD + fib.FIB_WINDOW + 5
    atr = fib.compute_atr(candles)
    level, _bias = fib.levels_fibonacci(candles)
    atr_entry = atr[entry_idx]
    # entre initial_stop et level -> r < TRAIL_BUFFER_ATR_MULT*atr_entry,
    # rendant la zone "active mais sous l'entree" atteignable.
    entry_price = level[entry_idx] - 0.1 * atr_entry
    initial_stop = level[entry_idx] - fib.STOP_BUFFER_ATR_MULT * atr_entry
    r = abs(entry_price - initial_stop)
    best_price = entry_price + r * 1.05
    candles[entry_idx + 2]["high"] = best_price
    entry_time_iso = _realistic_entry_time_iso(candles, entry_idx)
    current_stop_loss = entry_price - 5.0

    result = fib.compute_trailing_stop(candles, "achat", entry_time_iso, entry_price, current_stop_loss)
    assert result == entry_price


def test_compute_trailing_stop_moves_stop_down_once_activated_vente():
    # Symetrique du test achat -- haut ancien (21), bas recent (55).
    candles = _series_with_level(fib.ATR_PERIOD + fib.FIB_WINDOW + 20, start_price=2000.0)
    for i, c in enumerate(candles):
        if i == 21:
            c["open"] = c["high"] = c["low"] = c["close"] = 2050.0
        elif i == 55:
            c["open"] = c["high"] = c["low"] = c["close"] = 1950.0
    entry_idx = fib.ATR_PERIOD + fib.FIB_WINDOW + 5
    entry_price = candles[entry_idx]["close"]
    entry_time_iso = candles[entry_idx]["time"].replace(" ", "T") + "+00:00"
    candles[entry_idx + 2]["low"] = entry_price - 200.0
    current_stop_loss = entry_price + 5.0
    result = fib.compute_trailing_stop(candles, "vente", entry_time_iso, entry_price, current_stop_loss)
    assert result is not None
    assert result < current_stop_loss
