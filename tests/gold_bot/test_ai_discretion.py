import inspect
import json
from datetime import datetime, timedelta, timezone

import anthropic
import pytest

import gold_bot.ai_discretion as ai_discretion
import gold_bot.ftmo_guard as ftmo_guard


@pytest.fixture(autouse=True)
def _reset_discretion_cooldown():
    ai_discretion._last_call_at.clear()
    yield
    ai_discretion._last_call_at.clear()


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeMessages:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._response


class _FakeAnthropicClient:
    def __init__(self, response=None, error=None):
        self.messages = _FakeMessages(response=response, error=error)

    def __call__(self, api_key, timeout, max_retries=None):
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        return self


def _state(**overrides):
    base = {"level": 2650.5, "close": 2654.0, "high": 2655.0, "low": 2649.0, "atr": 1.2,
            "trend_strength": 0.03, "trend_strength_short": 0.01, "hour_utc": 12, "bias": "achat"}
    base.update(overrides)
    return base


def _candles():
    return [
        {"time": "2026-10-28 12:00:00", "open": 2650.0, "high": 2652.0, "low": 2649.0, "close": 2651.0},
        {"time": "2026-10-28 12:05:00", "open": 2651.0, "high": 2655.0, "low": 2650.0, "close": 2654.0},
    ]


def _buffer():
    return {"daily_pct_remaining": 0.02, "overall_pct_remaining": 0.05}


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


def test_judge_fibonacci_entry_returns_verdict_on_successful_achat_call(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_discretion_log.jsonl")
    response_text = '{"action": "achat", "confiance": "haute", "tp_multiplicateur": 1.0, "sl_multiplicateur": 1.0, "raison": "rejet net"}'
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(response_text)]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)

    result = ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path,
    )

    assert result == {"action": "achat", "confiance": "haute", "stop_loss_distance": 10.0,
                       "take_profit_distance": 15.0, "raison": "rejet net"}
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert entries[0]["action_reelle"] == "execute"


def test_judge_fibonacci_entry_returns_verdict_on_successful_vente_call(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_discretion_log.jsonl")
    response_text = '{"action": "vente", "confiance": "moyenne", "tp_multiplicateur": 1.2, "sl_multiplicateur": 0.8, "raison": "x"}'
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(response_text)]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)

    result = ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path,
    )
    assert result["action"] == "vente"
    assert result["take_profit_distance"] == 18.0  # 15.0 * 1.2
    assert result["stop_loss_distance"] == 8.0  # 10.0 * 0.8


def test_judge_fibonacci_entry_returns_none_when_action_is_rien(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_discretion_log.jsonl")
    response_text = '{"action": "rien", "confiance": "faible", "raison": "rien exploitable"}'
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(response_text)]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)

    result = ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path,
    )
    assert result is None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert entries[0]["action_reelle"] == "aucune_ia"


def test_judge_fibonacci_entry_returns_none_and_logs_when_rr_guardrail_rejects(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_discretion_log.jsonl")
    # baseline tp=10, sl=20 ; multiplicateurs 0.6/1.3 (deja dans les bornes) ->
    # tp_distance=6.0, sl_distance=26.0, rr=0.23 < 1.0 -> rejete.
    response_text = '{"action": "achat", "confiance": "haute", "tp_multiplicateur": 0.6, "sl_multiplicateur": 1.3, "raison": "x"}'
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(response_text)]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)

    result = ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(), _candles(),
        baseline_stop_loss_distance=20.0, baseline_take_profit_distance=10.0,
        ftmo_buffer=_buffer(), log_path=log_path,
    )
    assert result is None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert entries[0]["action_reelle"] == "rejete_garde_fou_rr"


def test_judge_fibonacci_entry_returns_none_when_api_key_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    log_path = str(tmp_path / "ai_discretion_log.jsonl")
    result = ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path,
    )
    assert result is None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert entries[0]["error"] == "ANTHROPIC_API_KEY absent"
    assert entries[0]["action_reelle"] == "erreur"


def test_judge_fibonacci_entry_returns_none_and_logs_on_client_exception(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_discretion_log.jsonl")
    fake_client = _FakeAnthropicClient(error=RuntimeError("boom reseau"))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)

    result = ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path,
    )
    assert result is None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert entries[0]["error"] == "RuntimeError: boom reseau"
    assert entries[0]["action_reelle"] == "erreur"


def test_judge_fibonacci_entry_returns_none_when_verdict_unparseable(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_discretion_log.jsonl")
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock("reponse sans bloc JSON")]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)

    result = ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path,
    )
    assert result is None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert entries[0]["error"] == "verdict JSON non trouve ou invalide dans la reponse"


def test_judge_fibonacci_entry_uses_max_retries_zero_and_expected_request_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_discretion_log.jsonl")
    response_text = '{"action": "achat", "confiance": "haute", "tp_multiplicateur": 1.0, "sl_multiplicateur": 1.0, "raison": "x"}'
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(response_text)]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)

    ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path,
    )
    assert fake_client.max_retries == 0
    assert fake_client.timeout == ai_discretion.REQUEST_TIMEOUT_SECONDS
    kwargs = fake_client.messages.last_kwargs
    assert kwargs["model"] == ai_discretion.MODEL
    assert kwargs["max_tokens"] == ai_discretion.MAX_TOKENS
    assert kwargs["output_config"] == {"effort": "high"}
    assert kwargs["tools"] == [{"type": "web_search_20260209", "name": "web_search",
                                 "max_uses": ai_discretion.WEB_SEARCH_MAX_USES}]


def test_judge_fibonacci_entry_respects_cooldown_for_same_symbol_and_level(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_discretion_log.jsonl")
    response_text = '{"action": "achat", "confiance": "haute", "tp_multiplicateur": 1.0, "sl_multiplicateur": 1.0, "raison": "x"}'
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(response_text)]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)
    t0 = datetime(2026, 11, 13, 10, 0, 0, tzinfo=timezone.utc)

    ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path, now=t0,
    )
    result = ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path, now=t0 + timedelta(hours=1),
    )
    assert result is None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert len(entries) == 1


def test_judge_fibonacci_entry_does_not_share_cooldown_across_different_levels(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_discretion_log.jsonl")
    response_text = '{"action": "achat", "confiance": "haute", "tp_multiplicateur": 1.0, "sl_multiplicateur": 1.0, "raison": "x"}'
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(response_text)]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)
    t0 = datetime(2026, 11, 13, 10, 0, 0, tzinfo=timezone.utc)

    ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(level=2650.5), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path, now=t0,
    )
    result = ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(level=2700.0), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path, now=t0 + timedelta(minutes=1),
    )
    assert result is not None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert len(entries) == 2


def test_judge_fibonacci_entry_logs_regle_contrefactuelle_for_comparison(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_discretion_log.jsonl")
    response_text = '{"action": "vente", "confiance": "haute", "tp_multiplicateur": 1.0, "sl_multiplicateur": 1.0, "raison": "x"}'
    fake_client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock(response_text)]))
    monkeypatch.setattr(anthropic, "Anthropic", fake_client)

    ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(bias="achat"), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path,
    )
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    # L'IA a choisi "vente" alors que la regle (bias) aurait choisi
    # "achat" -- le journal garde les deux pour comparaison a posteriori,
    # la vraie decision executee est bien celle de l'IA (verifiee par
    # un autre test, ce champ n'affecte jamais ce qui est journalise ici).
    assert entries[0]["regle_contrefactuelle"]["direction"] == "achat"
    assert entries[0]["verdict"]["action"] == "vente"


def test_judge_fibonacci_entry_returns_none_when_level_is_not_roundable(monkeypatch, tmp_path):
    # level="pas-un-nombre" fait planter round(level, 2) avec un
    # TypeError au moment du calcul de la cle de cooldown, AVANT tout
    # appel reseau -- doit rester fail-closed (None, jamais d'exception
    # propagee), meme si ce calcul plante avant que le client Anthropic
    # ne soit meme construit.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    log_path = str(tmp_path / "ai_discretion_log.jsonl")

    result = ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(level="pas-un-nombre"), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path,
    )

    assert result is None
    entries = [json.loads(line) for line in open(log_path, encoding="utf-8")]
    assert entries[0]["action_reelle"] == "erreur"
    assert "TypeError" in entries[0]["error"]


def test_judge_fibonacci_entry_never_raises_on_unexpected_exception(monkeypatch, tmp_path):
    log_path = str(tmp_path / "ai_discretion_log.jsonl")

    def _boom(*args, **kwargs):
        raise ValueError("inattendu")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setattr(ai_discretion, "_build_prompt", _boom)
    result = ai_discretion.judge_fibonacci_entry(
        "XAUUSD", _state(), _candles(),
        baseline_stop_loss_distance=10.0, baseline_take_profit_distance=15.0,
        ftmo_buffer=_buffer(), log_path=log_path,
    )
    assert result is None
