from datetime import datetime, timedelta, timezone

import pytest

import gold_bot.odds_check as odds_check
import gold_bot.vwap_reversion as vwap_reversion


def _make_5min_candles(n, start, price_fn):
    """Génère n bougies 5min consécutives à partir de `start` (UTC),
    close=price_fn(i), open=high=low=close (bougies plates, suffisant
    pour tester la détection d'entrée qui ne regarde que close/vwap/ema)."""
    out = []
    for i in range(n):
        t = start + timedelta(minutes=5 * i)
        price = price_fn(i)
        out.append({
            "time": t.strftime("%Y-%m-%d %H:%M:%S"), "open": price, "high": price,
            "low": price, "close": price, "tick_volume": 10, "spread": 2,
        })
    return out


def test_find_historical_entries_returns_empty_list_on_short_history():
    candles = _make_5min_candles(10, datetime(2026, 1, 1, tzinfo=timezone.utc), lambda i: 2000.0)
    assert odds_check._find_historical_entries(candles, "achat") == []


def test_find_historical_entries_finds_a_known_achat_signal(monkeypatch):
    # Même recette que test_decide_and_act_vwap_opens_achat_with_partial_and_runner_legs
    # (tests/gold_bot/test_bot.py) mais appliquée a un etat vwap_reversion
    # fabrique directement, plutôt qu'a de vraies bougies generees — on
    # monkeypatch resample_15min/compute_indicators pour renvoyer une
    # liste de bougies 15min dont la DERNIERE a l'etat voulu, les
    # precedentes a un etat neutre (aucun signal), afin d'isoler
    # precisement la detection sans dependre du calcul reel de VWAP/EMA.
    neutral = {"time": "2026-01-01T00:00:00", "close": 2000.0, "vwap": 2000.0, "vwap_std": 5.0,
               "ema200": 2000.0, "ema50": 2000.0, "hour_utc": 10, "bars_into_session": 20}
    signal_bar = {"time": "2026-01-01T05:00:00", "close": 1975.0, "vwap": 2010.0, "vwap_std": 20.0,
                  "ema200": 1900.0, "ema50": 2005.0, "hour_utc": 10, "bars_into_session": 20}
    fifteen_min_candles = [dict(neutral) for _ in range(199)] + [signal_bar]
    monkeypatch.setattr(odds_check.vwap_reversion, "resample_15min", lambda c: fifteen_min_candles)
    monkeypatch.setattr(odds_check.vwap_reversion, "compute_indicators", lambda c: c)

    candles_5min = _make_5min_candles(1, datetime(2026, 1, 1, tzinfo=timezone.utc), lambda i: 2000.0)
    entries = odds_check._find_historical_entries(candles_5min, "achat")

    assert len(entries) == 1
    assert entries[0]["entry_price"] == 1975.0
    assert entries[0]["stop_loss"] == 2010.0 - 2.5 * 20.0  # vwap - stop_sigma*std, même formule que decide_and_act_vwap


def test_find_historical_entries_ignores_wrong_direction(monkeypatch):
    signal_bar = {"time": "2026-01-01T05:00:00", "close": 1975.0, "vwap": 2010.0, "vwap_std": 20.0,
                  "ema200": 1900.0, "ema50": 2005.0, "hour_utc": 10, "bars_into_session": 20}
    fifteen_min_candles = [signal_bar] * 200
    monkeypatch.setattr(odds_check.vwap_reversion, "resample_15min", lambda c: fifteen_min_candles)
    monkeypatch.setattr(odds_check.vwap_reversion, "compute_indicators", lambda c: c)
    candles_5min = _make_5min_candles(1, datetime(2026, 1, 1, tzinfo=timezone.utc), lambda i: 2000.0)
    # signal_bar correspond a un signal "achat" (voir test precedent) --
    # chercher "vente" ne doit rien trouver.
    assert odds_check._find_historical_entries(candles_5min, "vente") == []


def test_find_historical_entries_ignores_bars_before_min_bars_into_session(monkeypatch):
    signal_bar = {"time": "2026-01-01T05:00:00", "close": 1975.0, "vwap": 2010.0, "vwap_std": 20.0,
                  "ema200": 1900.0, "ema50": 2005.0, "hour_utc": 10,
                  "bars_into_session": vwap_reversion.MIN_BARS_INTO_SESSION - 1}
    fifteen_min_candles = [signal_bar] * 200
    monkeypatch.setattr(odds_check.vwap_reversion, "resample_15min", lambda c: fifteen_min_candles)
    monkeypatch.setattr(odds_check.vwap_reversion, "compute_indicators", lambda c: c)
    candles_5min = _make_5min_candles(1, datetime(2026, 1, 1, tzinfo=timezone.utc), lambda i: 2000.0)
    assert odds_check._find_historical_entries(candles_5min, "achat") == []


def test_find_historical_entries_ignores_bars_at_or_after_session_end(monkeypatch):
    signal_bar = {"time": "2026-01-01T22:00:00", "close": 1975.0, "vwap": 2010.0, "vwap_std": 20.0,
                  "ema200": 1900.0, "ema50": 2005.0, "hour_utc": vwap_reversion.SESSION_END_HOUR_UTC,
                  "bars_into_session": 20}
    fifteen_min_candles = [signal_bar] * 200
    monkeypatch.setattr(odds_check.vwap_reversion, "resample_15min", lambda c: fifteen_min_candles)
    monkeypatch.setattr(odds_check.vwap_reversion, "compute_indicators", lambda c: c)
    candles_5min = _make_5min_candles(1, datetime(2026, 1, 1, tzinfo=timezone.utc), lambda i: 2000.0)
    assert odds_check._find_historical_entries(candles_5min, "achat") == []


def test_simulate_exit_closes_on_stop_touch():
    # achat a 1975, stop a 1960 -- une bougie qui touche 1960 en low doit
    # clore au stop, retour negatif.
    candles_after_entry = [
        {"time": "2026-01-01 05:05:00", "open": 1975.0, "high": 1976.0, "low": 1959.0, "close": 1960.0,
         "tick_volume": 10, "spread": 2},
    ]
    result = odds_check._simulate_exit(
        candles_after_entry, direction="achat", entry_price=1975.0, initial_stop=1960.0,
        entry_time="2026-01-01 05:00:00", symbol="XAUUSD",
    )
    assert result["close_reason"] == "stop_touche"
    assert result["return_pct"] < 0
    assert result["close_time"] == "2026-01-01 05:05:00"


def test_simulate_exit_closes_at_session_end():
    candles_after_entry = [
        {"time": "2026-01-01 22:00:00", "open": 1980.0, "high": 1982.0, "low": 1978.0, "close": 1981.0,
         "tick_volume": 10, "spread": 2},
    ]
    result = odds_check._simulate_exit(
        candles_after_entry, direction="achat", entry_price=1975.0, initial_stop=1960.0,
        entry_time="2026-01-01 05:00:00", symbol="XAUUSD",
    )
    assert result["close_reason"] == "fin_session"
    assert result["return_pct"] > 0  # 1981 > 1975, achat gagnant
    assert result["close_time"] == "2026-01-01 22:00:00"


def test_simulate_exit_closes_at_end_of_available_history_when_unresolved():
    candles_after_entry = [
        {"time": "2026-01-01 05:05:00", "open": 1976.0, "high": 1977.0, "low": 1974.0, "close": 1976.0,
         "tick_volume": 10, "spread": 2},
    ]
    result = odds_check._simulate_exit(
        candles_after_entry, direction="achat", entry_price=1975.0, initial_stop=1960.0,
        entry_time="2026-01-01 05:00:00", symbol="XAUUSD",
    )
    assert result["close_reason"] == "fin_historique"
    assert result["close_price"] == 1976.0
    assert result["close_time"] == "2026-01-01 05:05:00"


def test_simulate_exit_blends_partial_leg_target_with_runner_leg_stop():
    # XAUUSD : partial_tp_pct=0.7, partial_tp_r=2.0 (vwap_reversion.PARTIAL_TP_PCT/
    # PARTIAL_TP_R). distance = 1975-1960 = 15 -> cible partielle = 1975 + 2.0*15 = 2005.
    # Bougie 1 : touche la cible partielle (high=2006) SANS toucher le stop
    # runner (low=1970 > stop=1960) -- la jambe partielle se cloture ici,
    # la jambe runner continue (son stop ne bouge pas : avec seulement 2
    # bougies 5min dans l'historique fourni, compute_trailing_stop ne peut
    # pas calculer -- EMA200_PERIOD=200 -- et renvoie None, voir sa
    # docstring).
    # Bougie 2 : touche le stop runner, toujours a 1960 (low=1958).
    # Les deux jambes divergent donc reellement : la partielle est
    # gagnante (+), le runner est perdant (-) -- preuve que le melange
    # n'est pas un cas degenere a une seule bougie (contrairement aux 3
    # tests precedents, ou les deux jambes se resolvent forcement de
    # facon identique sur leur unique bougie).
    candles_after_entry = [
        {"time": "2026-01-01 05:05:00", "open": 1980.0, "high": 2006.0, "low": 1970.0, "close": 2000.0,
         "tick_volume": 10, "spread": 2},
        {"time": "2026-01-01 05:10:00", "open": 2000.0, "high": 2001.0, "low": 1958.0, "close": 1959.0,
         "tick_volume": 10, "spread": 2},
    ]
    result = odds_check._simulate_exit(
        candles_after_entry, direction="achat", entry_price=1975.0, initial_stop=1960.0,
        entry_time="2026-01-01 05:00:00", symbol="XAUUSD",
    )
    partial_return = (2005.0 - 1975.0) / 1975.0 * 100  # cible partielle touchee, bougie 1
    runner_return = (1960.0 - 1975.0) / 1975.0 * 100   # stop runner touche (jamais suivi), bougie 2
    expected = 0.7 * partial_return + 0.3 * runner_return
    assert result["return_pct"] == pytest.approx(expected)
    # close_reason/close_price/close_time refletent la jambe runner
    # (voir docstring de _simulate_exit) -- elle se cloture a la bougie 2.
    assert result["close_reason"] == "stop_touche"
    assert result["close_price"] == 1960.0
    assert result["close_time"] == "2026-01-01 05:10:00"


def test_historical_odds_for_setup_insufficient_history_does_not_block(monkeypatch):
    monkeypatch.setattr(odds_check, "_find_historical_entries", lambda candles, direction, symbol="XAUUSD": [])
    result = odds_check.historical_odds_for_setup([{"dummy": True}], "achat", min_sample_size=20)
    assert result["verdict"] == "insufficient_history"
    assert result["sample_size"] == 0


def test_historical_odds_for_setup_favorable_when_average_return_positive(monkeypatch):
    # Entrees espacees d'un jour chacune, cloture simulee 2h apres
    # l'entree (bien avant l'entree suivante) -- aucune ne doit etre
    # ignoree par la deduplication des signaux chevauchants.
    entries = [{"entry_time": f"2026-01-{i:02d} 05:00:00", "entry_price": 1975.0, "stop_loss": 1960.0}
               for i in range(1, 21)]
    monkeypatch.setattr(odds_check, "_find_historical_entries", lambda candles, direction, symbol="XAUUSD": entries)

    def fake_simulate_exit(candles, direction, entry_price, initial_stop, entry_time, symbol="XAUUSD"):
        day = entry_time.split(" ")[0]
        return {"close_reason": "fin_session", "close_price": 1985.0, "return_pct": 0.5,
                "close_time": f"{day} 07:00:00"}

    monkeypatch.setattr(odds_check, "_simulate_exit", fake_simulate_exit)
    result = odds_check.historical_odds_for_setup([{"dummy": True}], "achat", min_sample_size=20)
    assert result["sample_size"] == 20
    assert result["verdict"] == "favorable"
    assert result["avg_return_pct"] == pytest.approx(0.5)


def test_historical_odds_for_setup_unfavorable_when_average_return_negative(monkeypatch):
    entries = [{"entry_time": f"2026-01-{i:02d} 05:00:00", "entry_price": 1975.0, "stop_loss": 1960.0}
               for i in range(1, 21)]
    monkeypatch.setattr(odds_check, "_find_historical_entries", lambda candles, direction, symbol="XAUUSD": entries)

    def fake_simulate_exit(candles, direction, entry_price, initial_stop, entry_time, symbol="XAUUSD"):
        day = entry_time.split(" ")[0]
        return {"close_reason": "stop_touche", "close_price": 1960.0, "return_pct": -0.3,
                "close_time": f"{day} 07:00:00"}

    monkeypatch.setattr(odds_check, "_simulate_exit", fake_simulate_exit)
    result = odds_check.historical_odds_for_setup([{"dummy": True}], "achat", min_sample_size=20)
    assert result["verdict"] == "unfavorable"


def test_historical_odds_for_setup_skips_entries_overlapping_a_still_open_simulated_position(monkeypatch):
    # 3 entrees brutes : la 2e tombe PENDANT la position simulee ouverte
    # par la 1re (close_time de la 1re = 10h, entree de la 2e = 8h, donc
    # avant la cloture) -- doit etre ignoree. La 3e tombe apres la
    # cloture de la 1re -- doit etre retenue. Sans deduplication, les 3
    # seraient comptees comme des trades independants alors que le bot
    # reel n'aurait jamais ouvert la 2e (position deja ouverte sur la 1re).
    entries = [
        {"entry_time": "2026-01-01 05:00:00", "entry_price": 1975.0, "stop_loss": 1960.0},
        {"entry_time": "2026-01-01 08:00:00", "entry_price": 1976.0, "stop_loss": 1961.0},
        {"entry_time": "2026-01-01 11:00:00", "entry_price": 1977.0, "stop_loss": 1962.0},
    ]
    monkeypatch.setattr(odds_check, "_find_historical_entries", lambda candles, direction, symbol="XAUUSD": entries)

    def fake_simulate_exit(candles, direction, entry_price, initial_stop, entry_time, symbol="XAUUSD"):
        # La position ouverte a 05:00 se cloture a 10:00 (apres l'entree
        # brute de 08:00, avant celle de 11:00) ; les autres cloturent
        # vite, peu importe (jamais evaluees pour la 2e, ignoree avant).
        close_time = "2026-01-01 10:00:00" if entry_time == "2026-01-01 05:00:00" else "2026-01-01 12:00:00"
        return {"close_reason": "fin_session", "close_price": 1985.0, "return_pct": 0.5, "close_time": close_time}

    monkeypatch.setattr(odds_check, "_simulate_exit", fake_simulate_exit)
    result = odds_check.historical_odds_for_setup([{"dummy": True}], "achat", min_sample_size=2)
    assert result["sample_size"] == 2  # l'entree de 08:00 (chevauchante) a ete ignoree
