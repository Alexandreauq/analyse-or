import math
import pytest
import gold_bot.confluence as confluence


def test_fetch_gold_candles_returns_only_complete_candles_in_chronological_order(monkeypatch):
    raw = [
        {"time": "2026-09-29T15:55:00.000Z", "open": 4155.6, "high": 4158.9, "low": 4154.6, "close": 4158.2,
         "tickVolume": 2267, "spread": 21, "state": "complete"},
        {"time": "2026-09-29T15:50:00.000Z", "open": 4159.2, "high": 4159.7, "low": 4153.7, "close": 4155.6,
         "tickVolume": 2534, "spread": 21, "state": "complete"},
        {"time": "2026-09-29T16:00:00.000Z", "open": 4158.2, "high": 4158.9, "low": 4152.9, "close": 4153.2,
         "tickVolume": 900, "spread": 22, "state": "intermediate"},
    ]
    monkeypatch.setattr(confluence.broker, "get_historical_candles", lambda *a, **k: raw)

    candles = confluence.fetch_gold_candles("tok", "acc123", region="london")

    assert [c["time"] for c in candles] == ["2026-09-29 15:50:00", "2026-09-29 15:55:00"]
    assert candles[0]["open"] == 4159.2
    assert candles[0]["tick_volume"] == 2534
    assert candles[0]["spread"] == 21


def test_fetch_gold_candles_passes_token_account_symbol_timeframe_region(monkeypatch):
    captured = {}

    def fake_get_historical(token, account_id, symbol, timeframe, limit=None, region=None):
        captured.update(token=token, account_id=account_id, symbol=symbol, timeframe=timeframe,
                         limit=limit, region=region)
        return [{"time": "2026-09-29T15:50:00.000Z", "open": 1, "high": 1, "low": 1, "close": 1,
                  "tickVolume": 1, "spread": 1, "state": "complete"}]

    monkeypatch.setattr(confluence.broker, "get_historical_candles", fake_get_historical)
    confluence.fetch_gold_candles("tok", "acc123", region="new-york")

    assert captured["token"] == "tok"
    assert captured["account_id"] == "acc123"
    assert captured["symbol"] == "XAUUSD"
    assert captured["timeframe"] == "5m"
    assert captured["region"] == "new-york"
    assert captured["limit"] == 100


def test_fetch_gold_candles_passes_explicit_limit(monkeypatch):
    captured = {}

    def fake_get_historical(token, account_id, symbol, timeframe, limit=None, region=None):
        captured["limit"] = limit
        return [{"time": "2026-09-29T15:50:00.000Z", "open": 1, "high": 1, "low": 1, "close": 1,
                  "tickVolume": 1, "spread": 1, "state": "complete"}]

    monkeypatch.setattr(confluence.broker, "get_historical_candles", fake_get_historical)
    confluence.fetch_gold_candles("tok", "acc123", region="london", limit=1000)

    assert captured["limit"] == 1000


def test_fetch_gold_candles_passes_explicit_symbol(monkeypatch):
    captured = {}

    def fake_get_historical(token, account_id, symbol, timeframe, limit=None, region=None):
        captured["symbol"] = symbol
        return [{"time": "2026-09-29T15:50:00.000Z", "open": 1, "high": 1, "low": 1, "close": 1,
                  "tickVolume": 1, "spread": 1, "state": "complete"}]

    monkeypatch.setattr(confluence.broker, "get_historical_candles", fake_get_historical)
    confluence.fetch_gold_candles("tok", "acc123", region="london", symbol="XAGUSD")

    assert captured["symbol"] == "XAGUSD"


def test_fetch_gold_candles_raises_when_no_complete_candles(monkeypatch):
    raw = [{"time": "2026-09-29T16:00:00.000Z", "open": 1, "high": 1, "low": 1, "close": 1,
             "tickVolume": 1, "spread": 1, "state": "intermediate"}]
    monkeypatch.setattr(confluence.broker, "get_historical_candles", lambda *a, **k: raw)

    with pytest.raises(RuntimeError, match="aucune bougie complète"):
        confluence.fetch_gold_candles("tok", "acc123")


def test_fetch_gold_candles_validates_candles(monkeypatch):
    raw = [{"time": "2026-09-29T16:00:00.000Z", "open": 100, "high": 95, "low": 99, "close": 100.5,
             "tickVolume": 1, "spread": 1, "state": "complete"}]
    monkeypatch.setattr(confluence.broker, "get_historical_candles", lambda *a, **k: raw)

    with pytest.raises(RuntimeError, match="incohérente"):
        confluence.fetch_gold_candles("tok", "acc123")


def test_validate_candles_accepts_a_consistent_candle():
    confluence.validate_candles([{"open": 100, "high": 101, "low": 99, "close": 100.5}])  # ne doit pas lever


def test_validate_candles_rejects_nan_price():
    with pytest.raises(RuntimeError, match="invalides"):
        confluence.validate_candles([{"open": 100, "high": 101, "low": 99, "close": float("nan")}])


def test_validate_candles_rejects_infinite_price():
    with pytest.raises(RuntimeError, match="invalides"):
        confluence.validate_candles([{"open": 100, "high": float("inf"), "low": 99, "close": 100.5}])


def test_validate_candles_rejects_high_below_low():
    with pytest.raises(RuntimeError, match="incohérente"):
        confluence.validate_candles([{"open": 100, "high": 95, "low": 99, "close": 100.5}])


def test_validate_candles_rejects_high_below_close():
    """Trouvé lors de l'audit pré-lancement du 2026-09-29 : une bougie
    glitchée mais finie (ex: high mal renvoyé par Twelve Data) pouvait
    devenir un pivot fantôme et gonfler patternHeight sans que
    meets_minimum_risk_reward (qui ne vérifie qu'un ratio) ne l'attrape."""
    with pytest.raises(RuntimeError, match="incohérente"):
        confluence.validate_candles([{"open": 100, "high": 101, "low": 99, "close": 105}])


def test_validate_candles_rejects_low_above_open():
    with pytest.raises(RuntimeError, match="incohérente"):
        confluence.validate_candles([{"open": 95, "high": 101, "low": 99, "close": 100.5}])


def test_detect_pivots_finds_high_and_low_with_k1():
    candles = [
        {"high": 10, "low": 8},
        {"high": 10, "low": 8},
        {"high": 14, "low": 8},
        {"high": 10, "low": 3},
        {"high": 10, "low": 8},
    ]
    pivots = confluence.detect_pivots(candles, 1)
    assert pivots == [
        {"index": 2, "type": "high", "price": 14},
        {"index": 3, "type": "low", "price": 3},
    ]


def test_detect_pivots_rejects_equal_neighbor_as_not_strictly_higher():
    candles = [
        {"high": 10, "low": 5},
        {"high": 10, "low": 5},
        {"high": 8, "low": 5},
    ]
    assert confluence.detect_pivots(candles, 1) == []


def test_classify_trend_haussier_on_rising_pivots():
    pivots = [
        {"index": 0, "type": "low", "price": 10},
        {"index": 1, "type": "high", "price": 15},
        {"index": 2, "type": "low", "price": 12},
        {"index": 3, "type": "high", "price": 18},
    ]
    assert confluence.classify_trend(pivots) == "haussier"


def test_classify_trend_neutre_when_pivots_disagree():
    pivots = [
        {"index": 0, "type": "low", "price": 10},
        {"index": 1, "type": "high", "price": 18},
        {"index": 2, "type": "low", "price": 12},
        {"index": 3, "type": "high", "price": 15},
    ]
    assert confluence.classify_trend(pivots) == "neutre"


def test_classify_trend_neutre_when_not_enough_pivots():
    pivots = [{"index": 0, "type": "low", "price": 10}]
    assert confluence.classify_trend(pivots) == "neutre"


def _candle(open_, high, low, close):
    # "2026-01-05 12:00:00" (lundi, midi UTC) : date fixe volontairement
    # loin de tout évènement macro de SCALP_HIGH_IMPACT_EVENTS_UTC (voir
    # is_news_blackout) — un simple "t" non parseable plantait sur
    # datetime.fromisoformat() une fois ce garde ajouté à compute_signal.
    return {"time": "2026-01-05 12:00:00", "open": open_, "high": high, "low": low, "close": close}


def _zigzag_candles(vertices):
    """Bougies synthétiques (open=high=low=close, un seul prix par bougie)
    à partir d'une liste de sommets `[prix_départ, (n, prix), (n, prix), ...]`,
    reliés par des rampes strictement monotones de `n` bougies chacune.
    Une rampe strictement monotone entre deux sommets ne crée par
    construction aucun pivot intermédiaire (ni haut ni bas) : seul le sommet
    lui-même peut devenir un pivot, à condition d'avoir assez de bougies
    strictement plus basses (pour un haut) ou plus hautes (pour un bas) de
    part et d'autre. Utilisé pour construire des fixtures de figures
    chartistes dont les pivots K=3 (tendance) et K=5 (figure) ont été
    vérifiés empiriquement avant d'être committés ici (voir
    final-review-fix-report.md pour le détail des exécutions de
    vérification)."""
    prices = [vertices[0]]
    for n, target in vertices[1:]:
        start = prices[-1]
        step = (target - start) / n
        prices.extend(start + step * i for i in range(1, n + 1))
    return [_candle(p, p, p, p) for p in prices]


def test_compute_signal_achat_from_bullish_pattern(monkeypatch):
    candles = [_candle(100, 101, 99, 100.5) for _ in range(confluence.SCALP_MIN_CANDLES)]
    fake_pattern = {
        "name": "Triangle ascendant", "direction": "haussier", "kind": "continuation",
        "breakoutPrice": 100.0, "extremityPrice": 90.0, "patternHeight": 10.0,
    }
    monkeypatch.setattr(confluence.chart_patterns, "detect_chart_patterns", lambda pivots, trend, price: fake_pattern)
    result = confluence.compute_signal(candles)
    assert result["status"] == "achat"
    assert result["entry"] == candles[-1]["close"]
    assert result["stop_loss"] == pytest.approx(100.0 - confluence.CHARTPATTERN_STOP_BUFFER)
    assert result["take_profit"] == pytest.approx(110.0)
    assert result["pattern"] == fake_pattern


def test_compute_signal_vente_from_bearish_pattern(monkeypatch):
    candles = [_candle(100, 101, 99, 100.5) for _ in range(confluence.SCALP_MIN_CANDLES)]
    fake_pattern = {
        "name": "Triangle descendant", "direction": "baissier", "kind": "continuation",
        "breakoutPrice": 101.0, "extremityPrice": 110.0, "patternHeight": 9.0,
    }
    monkeypatch.setattr(confluence.chart_patterns, "detect_chart_patterns", lambda pivots, trend, price: fake_pattern)
    result = confluence.compute_signal(candles)
    assert result["status"] == "vente"
    assert result["entry"] == candles[-1]["close"]
    assert result["stop_loss"] == pytest.approx(101.0 + confluence.CHARTPATTERN_STOP_BUFFER)
    assert result["take_profit"] == pytest.approx(92.0)


def test_compute_signal_neutre_when_no_pattern_found(monkeypatch):
    candles = [_candle(100, 101, 99, 100.5) for _ in range(confluence.SCALP_MIN_CANDLES)]
    monkeypatch.setattr(confluence.chart_patterns, "detect_chart_patterns", lambda pivots, trend, price: None)
    result = confluence.compute_signal(candles)
    assert result["status"] == "neutre"
    assert result["entry"] is None
    assert result["stop_loss"] is None
    assert result["take_profit"] is None


def test_compute_signal_neutre_when_ratio_insufficient(monkeypatch):
    candles = [_candle(100, 101, 99, 100.5) for _ in range(confluence.SCALP_MIN_CANDLES)]
    fake_pattern = {
        "name": "Triangle ascendant", "direction": "haussier", "kind": "continuation",
        "breakoutPrice": 100.0, "extremityPrice": 99.5, "patternHeight": 0.5,
    }
    monkeypatch.setattr(confluence.chart_patterns, "detect_chart_patterns", lambda pivots, trend, price: fake_pattern)
    result = confluence.compute_signal(candles)
    assert result["status"] == "neutre"
    assert result["entry"] is None


def test_compute_signal_neutre_when_ratio_insufficient_vente(monkeypatch):
    # Miroir vente de test_compute_signal_neutre_when_ratio_insufficient
    # ci-dessus (même construction, valeurs symétriques autour de l'entrée
    # 100.5 : breakout 0.5 au-dessus au lieu de 0.5 en-dessous -> reward nul
    # des deux côtés).
    candles = [_candle(100, 101, 99, 100.5) for _ in range(confluence.SCALP_MIN_CANDLES)]
    fake_pattern = {
        "name": "Triangle descendant", "direction": "baissier", "kind": "continuation",
        "breakoutPrice": 101.0, "extremityPrice": 101.5, "patternHeight": 0.5,
    }
    monkeypatch.setattr(confluence.chart_patterns, "detect_chart_patterns", lambda pivots, trend, price: fake_pattern)
    result = confluence.compute_signal(candles)
    assert result["status"] == "neutre"
    assert result["entry"] is None
    assert result["stop_loss"] is None
    assert result["take_profit"] is None


def test_compute_signal_triangle_ascendant_end_to_end_real_pivots():
    """Sans monkeypatch : exerce la vraie chaîne detect_pivots ->
    classify_trend -> detect_chart_patterns. Triangle ascendant, une figure
    déjà atteignable AVANT le correctif de séparation des pivots K=3/K=5
    (Finding 1 de la revue finale du 29/09) — sert de témoin de non-
    régression pour le chemin réel, que les 4 tests monkeypatchés
    ci-dessus ne pouvaient pas exercer. Fixture vérifiée empiriquement
    (compute_signal exécuté pour de vrai) avant d'être committée ici ;
    voir final-review-fix-report.md pour le détail des exécutions."""
    candles = _zigzag_candles([
        90,
        (6, 100),     # 1er sommet (résistance)
        (6, 95),      # 1er creux
        (6, 100.4),   # 2e sommet, ~= au 1er (résistance quasi plate)
        (5, 97),      # léger repli : garde le 2e sommet comme vrai pivot local
        (10, 101.0),  # cassure au-dessus de la résistance (R:R volontairement modeste)
    ])
    result = confluence.compute_signal(candles)
    assert result["trend"] == "haussier"
    assert result["pattern"]["name"] == "Triangle ascendant"
    assert result["status"] == "achat"
    assert result["entry"] == pytest.approx(101.0)
    assert result["stop_loss"] == pytest.approx(99.4)
    assert result["take_profit"] == pytest.approx(105.8)


def test_compute_signal_tete_epaule_end_to_end_real_pivots_previously_unreachable():
    """Sans monkeypatch, chaîne réelle comme le test ci-dessus. Tête-Épaule
    était mathématiquement IMPOSSIBLE à déclencher avant le correctif de
    Finding 1 : quand tendance et figure partageaient les mêmes pivots
    K=5, les 2 derniers hauts de ce jeu de pivots étaient forcément (tête,
    épaule 2) avec épaule 2 < tête, donc jamais "haussier" — condition
    pourtant exigée par detect_chart_patterns pour cette figure. Ici, la
    tendance est calculée séparément sur des pivots K=3 qui incluent un
    sommet supplémentaire après l'épaule 2 (hors de la fenêtre K=5, donc
    invisible à la figure), ce qui permet à la tendance K=3 de lire
    "haussier" pendant que la figure K=5 voit bien l'épaule-tête-épaule et
    la cassure sous la ligne de cou. C'est exactement l'interaction que
    Finding 1 corrige. Fixture vérifiée empiriquement avant d'être
    committée ici ; voir final-review-fix-report.md pour le détail des
    exécutions."""
    candles = _zigzag_candles([
        88,
        (6, 100),    # épaule 1
        (6, 95),     # creux 1
        (6, 108),    # tête (plus haute que les deux épaules)
        (6, 97),     # creux 2 -- devient la ligne de cou ; > creux 1 (montant, pour la tendance K=3)
        (6, 100.5),  # épaule 2, ~= épaule 1
        (10, 98),    # repli après l'épaule 2 (> creux 2 : montant, pour la tendance K=3)
        (3, 101),    # sommet K=3 supplémentaire après l'épaule 2 (> épaule 2 : montant) ;
                     # placé à moins de 5 bougies de la fin -> jamais candidat pivot K=5,
                     # donc invisible à detect_chart_patterns
        (4, 95),     # clôture finale bien sous la ligne de cou (97) -> cassure confirmée
    ])
    result = confluence.compute_signal(candles)
    assert result["trend"] == "haussier"
    assert result["pattern"]["name"] == "Tête-Épaule"
    assert result["pattern"]["direction"] == "baissier"
    assert result["status"] == "vente"
    assert result["entry"] == pytest.approx(95.0)
    assert result["stop_loss"] == pytest.approx(98.0)
    assert result["take_profit"] == pytest.approx(86.0)


def test_meets_minimum_risk_reward_accepts_a_ratio_at_or_above_the_multiple():
    # achat : risque = 100-98 = 2, gain = 103-100 = 3, ratio = 1.5 (pile le seuil)
    assert confluence.meets_minimum_risk_reward(100, 98, 103, "achat") is True
    # vente : risque = 102-100 = 2, gain = 100-97 = 3, ratio = 1.5
    assert confluence.meets_minimum_risk_reward(100, 102, 97, "vente") is True


def test_meets_minimum_risk_reward_rejects_a_ratio_below_the_multiple():
    # achat : risque = 100-98 = 2, gain = 100.7-100 = 0.7, ratio = 0.35 (cas
    # exact du garde-fou : la resistance la plus proche plafonne l'objectif
    # bien en-deca du repli 1.5x, alors que le stop reste loin).
    assert confluence.meets_minimum_risk_reward(100, 98, 100.7, "achat") is False
    assert confluence.meets_minimum_risk_reward(100, 102, 99.3, "vente") is False


def test_meets_minimum_risk_reward_rejects_zero_or_negative_risk():
    # entry == stop_loss (risque nul) ne doit jamais etre accepte, meme si
    # le calcul de ratio deviendrait une division par zero sans ce garde.
    assert confluence.meets_minimum_risk_reward(100, 100, 105, "achat") is False
    assert confluence.meets_minimum_risk_reward(100, 100, 95, "vente") is False


def test_is_market_closed_true_on_saturday():
    from datetime import datetime, timezone
    assert confluence.is_market_closed(datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)) is True


def test_is_market_closed_true_friday_after_22h_utc():
    from datetime import datetime, timezone
    assert confluence.is_market_closed(datetime(2026, 9, 11, 22, 0, tzinfo=timezone.utc)) is True


def test_is_market_closed_false_friday_before_22h_utc():
    from datetime import datetime, timezone
    assert confluence.is_market_closed(datetime(2026, 9, 11, 21, 59, tzinfo=timezone.utc)) is False


def test_is_market_closed_true_sunday_before_22h_utc():
    from datetime import datetime, timezone
    assert confluence.is_market_closed(datetime(2026, 9, 13, 21, 59, tzinfo=timezone.utc)) is True


def test_is_market_closed_false_sunday_after_22h_utc():
    from datetime import datetime, timezone
    assert confluence.is_market_closed(datetime(2026, 9, 13, 22, 0, tzinfo=timezone.utc)) is False


def test_is_market_closed_false_on_a_weekday():
    from datetime import datetime, timezone
    # Lundi ordinaire, loin de tout bord de week-end.
    assert confluence.is_market_closed(datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)) is False


def test_compute_signal_neutre_when_too_few_candles():
    candles = [_candle(100, 101, 99, 100.5) for _ in range(5)]
    result = confluence.compute_signal(candles)
    assert result["status"] == "neutre"
    assert result["entry"] is None
    assert result["stop_loss"] is None
    assert result["take_profit"] is None


def test_is_news_blackout_exactly_at_event():
    from datetime import datetime, timezone
    # CPI du 11/09/2026, 8h30 ET = 12h30 UTC (source : bls.gov/schedule/news_release/cpi.htm).
    assert confluence.is_news_blackout(datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc)) is True


def test_is_news_blackout_within_window_before_event():
    from datetime import datetime, timezone
    # 14 min avant le CPI du 11/09 (fenêtre ±15 min) -> toujours en black-out.
    assert confluence.is_news_blackout(datetime(2026, 9, 11, 12, 16, tzinfo=timezone.utc)) is True


def test_is_news_blackout_just_outside_window():
    from datetime import datetime, timezone
    # 16 min avant le CPI du 11/09 -> hors fenêtre de 15 min.
    assert confluence.is_news_blackout(datetime(2026, 9, 11, 12, 14, tzinfo=timezone.utc)) is False


def test_is_news_blackout_false_on_quiet_day():
    from datetime import datetime, timezone
    # Dimanche, aucune publication macro programmée ce jour-là.
    assert confluence.is_news_blackout(datetime(2026, 9, 13, 12, 30, tzinfo=timezone.utc)) is False


def test_is_news_blackout_exactly_at_ecb_decision():
    from datetime import datetime, timezone
    # Décision BCE du 29/10/2026, 14h15 CET = 13h15 UTC (source : ecb.europa.eu/press/calendars/mgcgc).
    assert confluence.is_news_blackout(datetime(2026, 10, 29, 13, 15, tzinfo=timezone.utc)) is True


def test_is_news_blackout_exactly_at_boe_decision():
    from datetime import datetime, timezone
    # Décision Bank of England du 18/06/2026, 12h00 heure de Londres = 11h00 UTC (BST)
    # (source : bankofengland.co.uk/monetary-policy/upcoming-mpc-dates).
    assert confluence.is_news_blackout(datetime(2026, 6, 18, 11, 0, tzinfo=timezone.utc)) is True


def test_is_news_blackout_exactly_at_fomc_decision():
    from datetime import datetime, timezone
    # Décision FOMC du 28/10/2026, 14h00 ET = 18h00 UTC -- jour de
    # l'incident reel du 2026-10-28.
    assert confluence.is_news_blackout(datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc)) is True


def test_is_news_blackout_covers_fomc_press_conference_90min_later():
    # Avant le correctif du 2026-10-28, la fenêtre etait ±15 min partout
    # -- 90 min apres l'annonce FOMC (pendant la conference de presse,
    # qui a cause la vraie perte) tombait HORS fenêtre. Doit maintenant
    # etre couverte par PRESS_CONFERENCE_BLACKOUT_MINUTES (100 min).
    from datetime import datetime, timezone
    assert confluence.is_news_blackout(datetime(2026, 10, 28, 19, 30, tzinfo=timezone.utc)) is True


def test_is_news_blackout_covers_ecb_press_conference_90min_later():
    from datetime import datetime, timezone
    # Décision BCE du 29/10/2026, 13h15 UTC + 90 min = 14h45 UTC.
    assert confluence.is_news_blackout(datetime(2026, 10, 29, 14, 45, tzinfo=timezone.utc)) is True


def test_is_news_blackout_false_just_outside_press_conference_window():
    from datetime import datetime, timezone
    # 101 min apres l'annonce FOMC du 28/10 (18h00 UTC) -> hors fenêtre
    # de 100 min.
    assert confluence.is_news_blackout(datetime(2026, 10, 28, 19, 41, 1, tzinfo=timezone.utc)) is False


def test_is_news_blackout_cpi_still_uses_the_short_window():
    # Le CPI n'a pas de conference de presse -- doit RESTER a ±15min,
    # ne pas avoir herite de la fenêtre longue par erreur.
    from datetime import datetime, timezone
    assert confluence.is_news_blackout(datetime(2026, 9, 11, 13, 30, tzinfo=timezone.utc)) is False


def test_is_volatility_shock_none_without_enough_history():
    candles = [_candle(100, 101, 99, 100) for _ in range(104)]  # baseline_window(100)+lag(5)-1
    assert confluence.is_volatility_shock(candles) is None


def test_is_volatility_shock_false_on_calm_market():
    candles = [_candle(100, 101, 99, 100) for _ in range(105)]  # amplitude 2, constante
    assert confluence.is_volatility_shock(candles) is False


def test_is_volatility_shock_true_when_last_candle_far_exceeds_baseline():
    candles = [_candle(100, 101, 99, 100) for _ in range(104)]
    candles.append(_candle(100, 130, 70, 100))  # amplitude 60, 30x la baseline (2)
    assert confluence.is_volatility_shock(candles) is True


def test_is_volatility_shock_baseline_excludes_the_lag_window():
    # Les 5 dernieres bougies AVANT la bougie testee sont deja des chocs
    # -- si la baseline les incluait, elle serait gonflee et un nouveau
    # choc du meme ordre de grandeur ne la depasserait plus (c'est
    # exactement le defaut d'une EMA ATR classique, trouve le
    # 2026-10-28 : un 2e/3e candle de la meme actualite ne depassait
    # plus le seuil alors que le marche etait toujours en plein choc).
    candles = [_candle(100, 101, 99, 100) for _ in range(99)]  # baseline calme
    candles += [_candle(100, 130, 70, 100) for _ in range(5)]  # lag : deja des chocs
    candles.append(_candle(100, 130, 70, 100))  # nouveau choc, meme amplitude
    assert confluence.is_volatility_shock(candles) is True


def test_is_volatility_shock_real_incident_sequence_2026_10_28():
    # Sequence reelle (XAUUSD 5min, 2026-10-28, voir decisions_log.jsonl)
    # : annonce FOMC a 18h00 UTC (TR~11, 18h00-18h25), puis conference de
    # presse a 18h30 UTC qui fait grimper le TR a 17-25 pendant 35+ min --
    # exactement la fenetre ou le bot a perdu de l'argent (18h55-18h59).
    # 400 bougies calmes (TR~1.5-3.5, amplitude realiste) avant la
    # sequence reelle pour une baseline representative d'une vraie
    # journee de trading (pas juste quelques heures).
    calm = [_candle(4040.0, 4041.5, 4038.5, 4040.0) for _ in range(400)]
    real_ohlc = [
        (4041.47, 4041.88, 4040.31, 4041.68), (4041.53, 4042.32, 4035.88, 4037.6),
        (4037.55, 4039.02, 4037.14, 4037.84), (4037.83, 4039.97, 4037.1, 4037.42),
        (4037.36, 4040.03, 4036.83, 4039.24), (4039.23, 4040.79, 4038.33, 4039.9),
        (4039.91, 4040.99, 4038.58, 4040.05), (4040.06, 4040.52, 4038.59, 4039.85),
        (4039.83, 4040.44, 4038.91, 4039.34), (4039.35, 4040.96, 4038.96, 4040.36),
        (4040.29, 4040.92, 4039.5, 4040.15), (4040.12, 4047.85, 4039.09, 4047.81),
        (4047.71, 4048.07, 4041.62, 4042.25), (4042.24, 4042.66, 4041.23, 4041.63),
        (4041.6, 4042.15, 4040.63, 4041.71), (4041.7, 4042.65, 4040.75, 4041.37),
        (4041.33, 4041.8, 4038.44, 4040.12), (4040.09, 4040.18, 4038.74, 4038.92),
        (4038.91, 4039.55, 4037.8, 4038.26), (4038.2, 4041.53, 4037.88, 4040.91),
        (4040.88, 4040.93, 4039.07, 4039.21), (4039.19, 4039.46, 4037.66, 4037.99),
        (4037.98, 4039.62, 4037.26, 4039.62), (4039.61, 4040.42, 4039.07, 4039.93),
        (4039.96, 4041.52, 4039.22, 4039.85),
        (4040.14, 4044.76, 4033.49, 4034.11),  # 18h00 UTC -- annonce FOMC
        (4034.1, 4038.55, 4031.55, 4032.57), (4032.62, 4036.29, 4030.2, 4033.82),
        (4033.86, 4035.86, 4029.58, 4032.88), (4032.89, 4034.38, 4030.46, 4031.86),
        (4031.81, 4037.08, 4031.8, 4036.19),
        (4036.29, 4050.13, 4024.63, 4039.2),  # 18h30 UTC -- conference de presse
        (4038.78, 4062.24, 4037.74, 4052.25), (4052.29, 4067.49, 4049.51, 4054.88),
        (4054.75, 4061.79, 4044.59, 4055.74),
        (4055.72, 4061.5, 4040.93, 4050.65),  # 18h50 UTC
        (4050.57, 4050.57, 4032.94, 4032.98),  # 18h55 UTC -- 1er trade perdant reel
        (4033, 4049.21, 4027.28, 4046.48),  # 19h00 UTC -- 2e trade perdant reel
        (4047.14, 4054.49, 4031.42, 4049.11), (4049.06, 4049.1, 4042.23, 4045.02),
    ]
    candles = calm + [_candle(o, h, l, c) for o, h, l, c in real_ohlc]

    def shock_at(n_real_candles):
        return confluence.is_volatility_shock(candles[: len(calm) + n_real_candles])

    assert shock_at(25) is False  # 17h55 UTC, juste avant l'annonce -- calme
    assert shock_at(37) is True  # 18h55 UTC -- 1er trade perdant reel : aurait du etre bloque
    assert shock_at(38) is True  # 19h00 UTC -- 2e trade perdant reel : aurait du etre bloque


def test_compute_signal_neutre_during_news_blackout():
    candles = [_candle(100, 101, 99, 100.5) for _ in range(confluence.SCALP_MIN_CANDLES)]
    candles[-1]["time"] = "2026-09-16 18:00:00"  # décision FOMC du 16/09/2026
    result = confluence.compute_signal(candles)
    assert result["status"] == "neutre"
    assert result["entry"] is None
    assert result["stop_loss"] is None
    assert result["take_profit"] is None
