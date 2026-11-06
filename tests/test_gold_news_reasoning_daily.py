# tests/test_gold_news_reasoning_daily.py
from datetime import date

import gold_news_reasoning_daily as g


def test_normalize_direction_call_haussier_variants():
    assert g.normalize_direction_call("haussier") == "achat"
    assert g.normalize_direction_call("leger_haussier") == "achat"
    assert g.normalize_direction_call("HAUSSIER") == "achat"


def test_normalize_direction_call_baissier_variants():
    assert g.normalize_direction_call("baissier") == "vente"
    assert g.normalize_direction_call("leger_baissier") == "vente"
    assert g.normalize_direction_call("Baissier") == "vente"


def test_normalize_direction_call_neutre_variants():
    assert g.normalize_direction_call("neutre") is None
    assert g.normalize_direction_call("neutre_prudent") is None


def test_normalize_direction_call_unrecognized_or_wrong_type_returns_none():
    assert g.normalize_direction_call("incertain") is None
    assert g.normalize_direction_call("") is None
    assert g.normalize_direction_call(None) is None
    assert g.normalize_direction_call(123) is None


def test_clamp_stop_pct_within_range_unchanged():
    assert g.clamp_stop_pct(0.08) == 0.08


def test_clamp_stop_pct_below_min_clamped_up():
    assert g.clamp_stop_pct(0.01) == g.MIN_STOP_PCT


def test_clamp_stop_pct_above_max_clamped_down():
    assert g.clamp_stop_pct(0.25) == g.MAX_STOP_PCT


def test_clamp_stop_pct_negative_input_takes_absolute_value():
    assert g.clamp_stop_pct(-0.10) == 0.10


def test_clamp_stop_pct_exact_boundaries_unchanged():
    assert g.clamp_stop_pct(0.03) == 0.03
    assert g.clamp_stop_pct(0.15) == 0.15


def test_compute_safety_stop_price_achat_is_below_entry():
    stop = g.compute_safety_stop_price(4000.0, "achat", 0.05)
    assert stop == 3800.0


def test_compute_safety_stop_price_vente_is_above_entry():
    stop = g.compute_safety_stop_price(4000.0, "vente", 0.05)
    assert stop == 4200.0


def test_compute_safety_stop_price_clamps_out_of_range_input():
    stop = g.compute_safety_stop_price(4000.0, "achat", 0.50)
    assert stop == 4000.0 * (1 - g.MAX_STOP_PCT)


def test_compute_safety_stop_pct_achat_is_negative():
    pct = g.compute_safety_stop_pct(4000.0, 3800.0, "achat")
    assert round(pct, 4) == -0.05


def test_compute_safety_stop_pct_vente_is_negative():
    pct = g.compute_safety_stop_pct(4000.0, 4200.0, "vente")
    assert round(pct, 4) == -0.0476


def test_compute_return_pct_achat_positive_when_price_rises():
    assert round(g.compute_return_pct(4000.0, 4200.0, "achat"), 4) == 0.05


def test_compute_return_pct_vente_positive_when_price_falls():
    assert round(g.compute_return_pct(4000.0, 3800.0, "vente"), 4) == 0.0526


def test_compute_buyhold_return_pct_is_raw_never_direction_adjusted():
    # Une vente dont le prix baisse gagne (return_pct positif), mais le
    # repere buy&hold reste neutre/BRUT sur le meme mouvement de prix --
    # ici negatif puisque le prix a baisse, peu importe la direction du pari.
    buyhold = g.compute_buyhold_return_pct(4000.0, 3800.0)
    assert round(buyhold, 4) == -0.05


def test_compute_max_close_date_adds_21_days():
    assert g.compute_max_close_date(date(2026, 11, 10)) == date(2026, 12, 1)


def test_find_stop_breach_achat_detects_low_crossing():
    rows = [
        {"date": "2026-11-10", "high": 4100.0, "low": 4050.0},
        {"date": "2026-11-11", "high": 4060.0, "low": 3940.0},
    ]
    breach = g.find_stop_breach("achat", 3950.0, rows)
    assert breach["date"] == "2026-11-11"


def test_find_stop_breach_vente_detects_high_crossing():
    rows = [
        {"date": "2026-11-10", "high": 4150.0, "low": 4050.0},
        {"date": "2026-11-11", "high": 4260.0, "low": 4040.0},
    ]
    breach = g.find_stop_breach("vente", 4250.0, rows)
    assert breach["date"] == "2026-11-11"


def test_find_stop_breach_returns_first_chronological_day_not_most_extreme():
    # Le 11 franchit deja le stop (low 3940 < 3950) ; le 12, non trie en
    # premier dans la liste, descend bien plus bas (3800) mais est APRES
    # le 11 chronologiquement -- doit renvoyer le 11, pas le 12.
    rows = [
        {"date": "2026-11-12", "high": 4000.0, "low": 3800.0},
        {"date": "2026-11-11", "high": 4060.0, "low": 3940.0},
        {"date": "2026-11-10", "high": 4100.0, "low": 4050.0},
    ]
    breach = g.find_stop_breach("achat", 3950.0, rows)
    assert breach["date"] == "2026-11-11"


def test_find_stop_breach_returns_none_when_never_crossed():
    rows = [
        {"date": "2026-11-10", "high": 4100.0, "low": 4050.0},
        {"date": "2026-11-11", "high": 4060.0, "low": 4030.0},
    ]
    assert g.find_stop_breach("achat", 3950.0, rows) is None
