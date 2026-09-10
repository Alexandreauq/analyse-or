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


def test_current_levels_picks_nearest_unbroken_pivots():
    pivots = [
        {"index": 0, "type": "low", "price": 95},
        {"index": 1, "type": "high", "price": 105},
        {"index": 2, "type": "low", "price": 98},
        {"index": 3, "type": "high", "price": 110},
    ]
    assert confluence.current_levels(pivots, 100) == {"support": 98, "resistance": 110}


def test_current_levels_null_when_no_pivot_on_one_side():
    pivots = [{"index": 0, "type": "low", "price": 98}]
    assert confluence.current_levels(pivots, 100) == {"support": 98, "resistance": None}


def test_compute_rsi_all_gains_is_100():
    closes = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24]
    assert confluence.compute_rsi(closes, 14) == 100


def test_compute_rsi_all_losses_is_0():
    closes = [24, 23, 22, 21, 20, 19, 18, 17, 16, 15, 14, 13, 12, 11, 10]
    assert confluence.compute_rsi(closes, 14) == 0


def test_compute_rsi_balanced_alternating_is_50():
    closes = [10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10]
    assert confluence.compute_rsi(closes, 14) == 50


def test_compute_macd_constant_offset_on_linear_series():
    closes = [10, 12, 14, 16, 18, 20, 22]
    result = confluence.compute_macd(closes, 2, 4, 2)
    assert result == {"macd": 2, "signal": 2, "histogram": 0}


def test_compute_bollinger_zero_variance():
    closes = [100, 100, 100, 100]
    assert confluence.compute_bollinger(closes, 4, 2) == {"middle": 100, "upper": 100, "lower": 100}


def test_compute_bollinger_with_variance():
    closes = [0, 0, 4, 4]
    assert confluence.compute_bollinger(closes, 4, 2) == {"middle": 2, "upper": 6, "lower": -2}


def _candle(open_, high, low, close):
    return {"time": "t", "open": open_, "high": high, "low": low, "close": close}


def test_match_candlestick_pattern_marteau():
    # Corps=2 (100->102), mèche basse=6 (>=2*2), mèche haute=0.5 (<2*0.3).
    candles = [_candle(100, 102.5, 94, 102)]
    result = confluence.match_candlestick_pattern(candles, "baissier")
    assert result == {"name": "Marteau", "direction": "haussier"}


def test_match_candlestick_pattern_etoile_filante():
    # Corps=2 (100->98), mèche haute=6 (>=2*2), mèche basse=0.5 (<2*0.3).
    candles = [_candle(100, 106, 97.5, 98)]
    result = confluence.match_candlestick_pattern(candles, "haussier")
    assert result == {"name": "Étoile filante", "direction": "baissier"}


def test_match_candlestick_pattern_englobante_haussiere():
    prev = _candle(100, 101, 94, 95)
    cur = _candle(94, 102, 93, 101)
    result = confluence.match_candlestick_pattern([prev, cur], "baissier")
    assert result == {"name": "Englobante haussière", "direction": "haussier"}


def test_match_candlestick_pattern_englobante_baissiere():
    prev = _candle(95, 101, 94, 100)
    cur = _candle(101, 102, 93, 94)
    result = confluence.match_candlestick_pattern([prev, cur], "haussier")
    assert result == {"name": "Englobante baissière", "direction": "baissier"}


def test_match_candlestick_pattern_penetrante():
    prev = _candle(100, 101, 89, 90)
    cur = _candle(88, 98, 87, 97)
    result = confluence.match_candlestick_pattern([prev, cur], "baissier")
    assert result == {"name": "Pénétrante", "direction": "haussier"}


def test_match_candlestick_pattern_nuage_noir():
    prev = _candle(90, 101, 89, 100)
    cur = _candle(102, 103, 92, 93)
    result = confluence.match_candlestick_pattern([prev, cur], "haussier")
    assert result == {"name": "Nuage noir", "direction": "baissier"}


def test_match_candlestick_pattern_etoile_du_matin():
    c1 = _candle(100, 101, 89, 90)
    c2 = _candle(89, 90, 87, 88)
    c3 = _candle(87, 98, 86, 97)
    result = confluence.match_candlestick_pattern([c1, c2, c3], "baissier")
    assert result == {"name": "Étoile du Matin", "direction": "haussier"}


def test_match_candlestick_pattern_etoile_du_soir():
    c1 = _candle(90, 101, 89, 100)
    c2 = _candle(101, 103, 100, 102)
    c3 = _candle(103, 104, 92, 93)
    result = confluence.match_candlestick_pattern([c1, c2, c3], "haussier")
    assert result == {"name": "Étoile du Soir", "direction": "baissier"}


def test_match_candlestick_pattern_none_when_no_match():
    candles = [_candle(100, 100.2, 99.8, 100.1)]
    assert confluence.match_candlestick_pattern(candles, "neutre") is None


def test_compute_signal_neutre_when_too_few_candles():
    candles = [_candle(100, 101, 99, 100.5) for _ in range(5)]
    result = confluence.compute_signal(candles)
    assert result["status"] == "neutre"
    assert result["entry"] is None
    assert result["stop_loss"] is None
    assert result["take_profit"] is None


def _build_bearish_then_hammer_candles():
    """36 bougies : déclin en zigzag net (3 cycles baisse-de-4/rebond-de-3,
    chaque creux et chaque sommet strictement plus bas que le précédent —
    ce qui donne des pivots confirmés décroissants des deux côtés, donc
    trend='baissier' avec K=3), suivi d'une approche finale qui ramène le
    prix tout près du dernier support confirmé, puis d'une bougie Marteau
    nette. Construit pour amener compute_signal() à une confluence d'achat
    complète (structure + RSI<70 + MACD haussier + pattern Marteau).

    Les valeurs exactes de RSI/MACD sur 36 bougies ne sont pas dérivées à
    la main (trop complexe) — ce test a été exécuté pendant le développement
    pour confirmer qu'il produit bien un signal 'achat' ; les paramètres
    ci-dessous (pas du zigzag, marge par rapport au support, ampleur des
    mèches) ont été ajustés à cet effet plutôt que d'affaiblir les
    assertions. Deux pièges rencontrés pendant le réglage, à ne pas
    réintroduire par inadvertance : (1) un simple déclin monotone (pas de
    vrai zigzag) ne produit AUCUN pivot, car detect_pivots exige un
    extremum local — d'où les 3 cycles baisse/rebond ; (2) sur une mèche
    de faible amplitude à chaque bougie, la bougie de retournement et sa
    voisine immédiate se retrouvent avec un high (ou low) strictement égal
    (candle[i].high == candle[i+1].high), ce qui invalide le pivot
    (l'inégalité doit être stricte) — d'où le `turn_wick` plus large
    spécifiquement sur les bougies de creux/sommet. Les fonctions
    composantes (detect_pivots, classify_trend, compute_rsi, compute_macd,
    match_candlestick_pattern) ont déjà leurs propres tests à valeurs
    exactes ci-dessus ; ce test est un test d'intégration qui verrouille
    le comportement réel, pas une réinvention du calcul."""
    candles = []
    price = 2200.0
    turn_wick = 1.2  # mèche renforcée sur les bougies de creux/sommet, pour
    # éviter l'égalité de high/low avec la bougie voisine (cf. docstring).
    last_trough_close = None
    for _ in range(3):
        for j in range(4):  # jambe baissière : 4 bougies de -3.0
            open_ = price
            close = price - 3.0
            is_trough = j == 3
            high = max(open_, close) + 0.3
            low = min(open_, close) - (turn_wick if is_trough else 0.3)
            candles.append(_candle(open_, high, low, close))
            price = close
        last_trough_close = price
        for j in range(3):  # jambe de rebond : 3 bougies de +1.5
            open_ = price
            close = price + 1.5
            is_peak = j == 2
            high = max(open_, close) + (turn_wick if is_peak else 0.3)
            low = min(open_, close) - 0.3
            candles.append(_candle(open_, high, low, close))
            price = close

    # Le dernier pivot bas confirmé aura pour prix (low) = creux - turn_wick.
    support_price = last_trough_close - turn_wick
    final_up = 0.2  # corps du Marteau final (petit, bougie haussière)
    close_margin = 0.1  # distance visée entre la clôture finale et le support

    # Jambe d'approche : ramène le prix de son niveau courant jusqu'au futur
    # open de la bougie Marteau, calculé pour que sa clôture finisse à
    # `close_margin` au-dessus du support (dans la tolérance SCALP_LEVEL_PROXIMITY).
    n_target = 35
    remaining = n_target - len(candles)
    final_open_target = support_price + close_margin - final_up
    step = (final_open_target - price) / remaining
    for _ in range(remaining):
        open_ = price
        close = price + step
        high = max(open_, close) + 0.3
        low = min(open_, close) - 0.3
        candles.append(_candle(open_, high, low, close))
        price = close

    # Bougie Marteau finale : petit corps haussier, mèche basse longue
    # (>= 2x le corps), mèche haute quasi nulle (< 0.3x le corps).
    last_open = price
    last_close = price + final_up
    last_low = min(last_open, last_close) - 6.5
    last_high = max(last_open, last_close) + 0.01
    candles.append(_candle(last_open, last_high, last_low, last_close))
    return candles


def test_compute_signal_full_achat_scenario():
    candles = _build_bearish_then_hammer_candles()
    result = confluence.compute_signal(candles)
    assert result["status"] == "achat"
    assert result["trend"] == "baissier"
    assert result["pattern"]["name"] == "Marteau"
    assert result["entry"] == candles[-1]["close"]
    assert result["stop_loss"] < result["entry"] < result["take_profit"]


def _build_bullish_then_shooting_star_candles():
    """Symétrique de _build_bearish_then_hammer_candles : 3 cycles
    hausse-de-4/repli-de-3 (chaque sommet et chaque creux strictement plus
    haut que le précédent -> trend='haussier'), approche finale qui
    ramène le prix tout près de la dernière résistance confirmée, puis
    une bougie Étoile filante nette. Mêmes pièges de construction que la
    fonction miroir ci-dessus (zigzag obligatoire pour avoir des pivots,
    `turn_wick` pour éviter les égalités de high/low sur les bougies de
    retournement) — voir sa docstring pour le détail. Exécuté pendant le
    développement pour confirmer qu'il produit bien un signal 'vente'."""
    candles = []
    price = 2100.0
    turn_wick = 1.2
    last_peak_close = None
    for _ in range(3):
        for j in range(4):  # jambe haussière : 4 bougies de +3.0
            open_ = price
            close = price + 3.0
            is_peak = j == 3
            high = max(open_, close) + (turn_wick if is_peak else 0.3)
            low = min(open_, close) - 0.3
            candles.append(_candle(open_, high, low, close))
            price = close
        last_peak_close = price
        for j in range(3):  # jambe de repli : 3 bougies de -1.5
            open_ = price
            close = price - 1.5
            is_trough = j == 2
            high = max(open_, close) + 0.3
            low = min(open_, close) - (turn_wick if is_trough else 0.3)
            candles.append(_candle(open_, high, low, close))
            price = close

    # Le dernier pivot haut confirmé aura pour prix (high) = sommet + turn_wick.
    resistance_price = last_peak_close + turn_wick
    final_down = 0.2  # corps de l'Étoile filante finale (petit, bougie baissière)
    close_margin = 0.1  # distance visée entre la clôture finale et la résistance

    n_target = 35
    remaining = n_target - len(candles)
    final_open_target = (resistance_price - close_margin) + final_down
    step = (final_open_target - price) / remaining
    for _ in range(remaining):
        open_ = price
        close = price + step
        high = max(open_, close) + 0.3
        low = min(open_, close) - 0.3
        candles.append(_candle(open_, high, low, close))
        price = close

    # Bougie Étoile filante finale : petit corps baissier, mèche haute
    # longue (>= 2x le corps), mèche basse quasi nulle (< 0.3x le corps).
    last_open = price
    last_close = price - final_down
    last_high = max(last_open, last_close) + 6.5
    last_low = min(last_open, last_close) - 0.01
    candles.append(_candle(last_open, last_high, last_low, last_close))
    return candles


def test_compute_signal_full_vente_scenario():
    candles = _build_bullish_then_shooting_star_candles()
    result = confluence.compute_signal(candles)
    assert result["status"] == "vente"
    assert result["trend"] == "haussier"
    assert result["pattern"]["name"] == "Étoile filante"
    assert result["entry"] == candles[-1]["close"]
    assert result["take_profit"] < result["entry"] < result["stop_loss"]


def test_compute_signal_neutre_when_no_confluence():
    # Bougies plates : aucun pivot net, donc trend='neutre', aucune
    # structure achat/vente possible -> neutre garanti quel que soit le
    # reste (pas besoin de dériver RSI/MACD pour ce cas).
    candles = [_candle(2100 + (i % 2) * 0.01, 2100.1, 2099.9, 2100) for i in range(40)]
    result = confluence.compute_signal(candles)
    assert result["status"] == "neutre"
    assert result["entry"] is None
