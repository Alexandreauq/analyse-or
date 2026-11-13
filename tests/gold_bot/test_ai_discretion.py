import inspect

import pytest

import gold_bot.ai_discretion as ai_discretion


def test_clamp_multipliers_passes_through_values_within_bounds():
    assert ai_discretion.clamp_multipliers(1.2, 1.1) == (1.2, 1.1)


def test_clamp_multipliers_clamps_tp_above_upper_bound():
    assert ai_discretion.clamp_multipliers(3.0, 1.0) == (2.0, 1.0)


def test_clamp_multipliers_clamps_tp_below_lower_bound():
    assert ai_discretion.clamp_multipliers(0.1, 1.0) == (0.6, 1.0)


def test_clamp_multipliers_clamps_sl_above_upper_bound():
    assert ai_discretion.clamp_multipliers(1.0, 5.0) == (1.0, 1.3)


def test_clamp_multipliers_clamps_sl_below_lower_bound():
    assert ai_discretion.clamp_multipliers(1.0, 0.01) == (1.0, 0.7)


def test_resolve_risk_reward_accepts_at_exact_floor():
    result = ai_discretion.resolve_risk_reward(10.0, 10.0, 1.0, 1.0)
    assert result["take_profit_distance"] == 10.0
    assert result["stop_loss_distance"] == 10.0
    assert result["risk_reward"] == 1.0
    assert result["accepted"] is True


def test_resolve_risk_reward_rejects_when_rr_below_floor_after_clamping():
    # tp_multiplicateur=0.6 et sl_multiplicateur=1.3 sont deja dans les
    # bornes (pas d'effet du bornage lui-meme) : take_profit_distance =
    # 10*0.6=6.0, stop_loss_distance = 20*1.3=26.0, rr=6/26=0.2308 < 1.0.
    result = ai_discretion.resolve_risk_reward(10.0, 20.0, 0.6, 1.3)
    assert result["take_profit_distance"] == 6.0
    assert result["stop_loss_distance"] == 26.0
    assert result["risk_reward"] == pytest.approx(6.0 / 26.0)
    assert result["accepted"] is False


def test_resolve_risk_reward_rejection_uses_clamped_values_not_raw_ones():
    # tp_multiplicateur=10.0 (hors bornes, sera clampe a 2.0) et
    # sl_multiplicateur=0.01 (hors bornes, clampe a 0.7) : sans bornage,
    # le R:R brut serait enorme et accepte a tort -- avec bornage,
    # take_profit_distance=10*2.0=20.0, stop_loss_distance=10*0.7=7.0,
    # rr=20/7=2.857 >= 1.0 -> accepte quand meme ici (cas positif pour
    # verifier que le bornage s'applique AVANT le calcul du ratio, pas
    # apres).
    result = ai_discretion.resolve_risk_reward(10.0, 10.0, 10.0, 0.01)
    assert result["take_profit_distance"] == 20.0
    assert result["stop_loss_distance"] == 7.0
    assert result["accepted"] is True


def test_parse_discretion_verdict_valid_achat():
    text = 'blabla\n{"action": "achat", "confiance": "haute", "tp_multiplicateur": 1.2, "sl_multiplicateur": 0.9, "raison": "x"}'
    result = ai_discretion._parse_discretion_verdict(text)
    assert result == {"action": "achat", "confiance": "haute", "tp_multiplicateur": 1.2,
                       "sl_multiplicateur": 0.9, "raison": "x"}


def test_parse_discretion_verdict_valid_vente():
    text = '{"action": "vente", "confiance": "faible", "tp_multiplicateur": 1.0, "sl_multiplicateur": 1.0, "raison": "y"}'
    result = ai_discretion._parse_discretion_verdict(text)
    assert result["action"] == "vente"


def test_parse_discretion_verdict_valid_rien_without_multipliers():
    text = '{"action": "rien", "confiance": "faible", "raison": "rien de clair"}'
    result = ai_discretion._parse_discretion_verdict(text)
    assert result == {"action": "rien", "confiance": "faible", "raison": "rien de clair"}


def test_parse_discretion_verdict_rejects_invalid_action_value():
    text = '{"action": "hold", "confiance": "haute", "tp_multiplicateur": 1.0, "sl_multiplicateur": 1.0, "raison": "x"}'
    assert ai_discretion._parse_discretion_verdict(text) is None


def test_parse_discretion_verdict_rejects_achat_missing_multipliers():
    text = '{"action": "achat", "confiance": "haute", "raison": "x"}'
    assert ai_discretion._parse_discretion_verdict(text) is None


def test_parse_discretion_verdict_rejects_achat_with_boolean_multiplier():
    text = '{"action": "achat", "confiance": "haute", "tp_multiplicateur": true, "sl_multiplicateur": 1.0, "raison": "x"}'
    assert ai_discretion._parse_discretion_verdict(text) is None


def test_parse_discretion_verdict_rejects_when_no_json_block_found():
    assert ai_discretion._parse_discretion_verdict("reponse sans bloc JSON") is None


def test_parse_discretion_verdict_normalizes_case_and_whitespace():
    text = '{"action": " ACHAT ", "confiance": " Haute ", "tp_multiplicateur": 1.0, "sl_multiplicateur": 1.0, "raison": "x"}'
    result = ai_discretion._parse_discretion_verdict(text)
    assert result["action"] == "achat"
    assert result["confiance"] == "haute"


def _prompt_kwargs(**overrides):
    base = dict(
        level=2650.5, close=2654.0, high=2655.0, low=2649.0, atr=1.2,
        trend_strength=0.03, trend_strength_short=0.01, hour_utc=12,
        candles=[{"time": "2026-10-28 12:00:00", "open": 2650.0, "high": 2652.0, "low": 2649.0, "close": 2651.0}],
        baseline_stop_loss_distance=11.0, baseline_take_profit_distance=16.5,
        ftmo_buffer={"daily_pct_remaining": 0.021, "overall_pct_remaining": 0.045},
    )
    base.update(overrides)
    return base


def test_build_prompt_signature_has_no_direction_or_bias_parameter():
    params = inspect.signature(ai_discretion._build_prompt).parameters
    assert "direction" not in params
    assert "bias" not in params


def test_build_prompt_includes_ftmo_buffer_values():
    prompt = ai_discretion._build_prompt("XAUUSD", **_prompt_kwargs())
    assert "2.10%" in prompt
    assert "4.50%" in prompt


def test_build_prompt_renders_unknown_when_buffer_values_are_none():
    prompt = ai_discretion._build_prompt(
        "XAUUSD", **_prompt_kwargs(ftmo_buffer={"daily_pct_remaining": None, "overall_pct_remaining": None}))
    assert "inconnu" in prompt


def test_build_prompt_includes_baseline_distances():
    prompt = ai_discretion._build_prompt("XAUUSD", **_prompt_kwargs())
    assert "11.00" in prompt
    assert "16.50" in prompt


def test_build_prompt_omits_trend_lines_when_none():
    prompt = ai_discretion._build_prompt(
        "XAUUSD", **_prompt_kwargs(trend_strength=None, trend_strength_short=None))
    assert "force de tendance" not in prompt
