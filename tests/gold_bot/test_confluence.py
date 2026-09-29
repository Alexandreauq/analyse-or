import math
import pytest
import gold_bot.confluence as confluence


class _FakeTDResponse:
    def __init__(self, json_data, ok=True, status_code=200):
        self._json_data = json_data
        self.ok = ok
        self.status_code = status_code

    def json(self):
        return self._json_data


def test_fetch_gold_candles_parses_and_reverses_to_chronological_order(monkeypatch):
    fake_data = {
        "status": "ok",
        "values": [
            {"datetime": "2026-09-09 10:02:00", "open": "2051.0", "high": "2051.5", "low": "2050.5", "close": "2051.2"},
            {"datetime": "2026-09-09 10:01:00", "open": "2050.0", "high": "2050.8", "low": "2049.5", "close": "2050.5"},
        ],
    }
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["params"] = params
        return _FakeTDResponse(fake_data)

    monkeypatch.setattr(confluence.requests, "get", fake_get)
    candles = confluence.fetch_gold_candles("fake-key")

    assert captured["params"]["symbol"] == "XAU/USD"
    assert captured["params"]["interval"] == "1min"
    assert captured["params"]["timezone"] == "UTC"
    assert len(candles) == 2
    assert candles[0]["time"] == "2026-09-09 10:01:00"
    assert candles[0]["close"] == 2050.5
    assert candles[1]["time"] == "2026-09-09 10:02:00"


def test_fetch_gold_candles_rejects_on_error_status(monkeypatch):
    monkeypatch.setattr(
        confluence.requests, "get",
        lambda *a, **k: _FakeTDResponse({"status": "error", "message": "quota dépassé"}),
    )
    with pytest.raises(RuntimeError, match="quota dépassé"):
        confluence.fetch_gold_candles("fake-key")


def test_fetch_gold_candles_rejects_on_http_error(monkeypatch):
    monkeypatch.setattr(
        confluence.requests, "get",
        lambda *a, **k: _FakeTDResponse({}, ok=False, status_code=429),
    )
    with pytest.raises(RuntimeError, match="429"):
        confluence.fetch_gold_candles("fake-key")


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


def test_compute_signal_neutre_during_news_blackout():
    candles = [_candle(100, 101, 99, 100.5) for _ in range(confluence.SCALP_MIN_CANDLES)]
    candles[-1]["time"] = "2026-09-16 18:00:00"  # décision FOMC du 16/09/2026
    result = confluence.compute_signal(candles)
    assert result["status"] == "neutre"
    assert result["entry"] is None
    assert result["stop_loss"] is None
    assert result["take_profit"] is None
