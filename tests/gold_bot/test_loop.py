import json
from datetime import datetime, timezone

import pytest

import gold_bot.loop as loop


def _permissive_state(**overrides):
    base = {"kill_switch": False, "dry_run": False}
    base.update(overrides)
    return base


# Vendredi 11/09/2026 16:41 UTC : marché XAU/USD ouvert, à 1 minute d'une
# bougie fraîche — sert de "now" déterministe à tous les tests run_cycle
# qui doivent passer le nouveau garde marché-fermé/données-périmées sans
# dépendre de la date réelle d'exécution des tests.
_FRESH_NOW = datetime(2026, 9, 11, 16, 41, tzinfo=timezone.utc)
_FRESH_CANDLE = {"time": "2026-09-11 16:40:00", "close": 2100}


def test_execute_steps_calls_close_for_closing_step(monkeypatch):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: _permissive_state())
    captured = {}
    monkeypatch.setattr(
        loop.broker, "close_position",
        lambda token, account_id, position_id, region: captured.__setitem__("close", (token, account_id, position_id, region)) or {"orderId": "1"},
    )
    steps = [{"type": "clôture_simulee", "position_id": "42", "symbol": "XAUUSD"}]
    results = loop.execute_steps("tok", "acc", steps, region="london")
    assert captured["close"] == ("tok", "acc", "42", "london")
    assert results == [{"step": steps[0], "result": {"orderId": "1"}, "error": None}]


def test_execute_steps_calls_place_order_for_opening_step(monkeypatch):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: _permissive_state())
    captured = {}
    monkeypatch.setattr(
        loop.broker, "place_market_order",
        lambda token, account_id, symbol, direction, volume, stop_loss, take_profit, region:
            captured.__setitem__("place", (symbol, direction, volume, stop_loss, take_profit, region)) or {"orderId": "2"},
    )
    steps = [{
        "type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
        "volume": 1.0, "entry": 2100, "stop_loss": 2095, "take_profit": 2115,
    }]
    results = loop.execute_steps("tok", "acc", steps, region="london")
    assert captured["place"] == ("XAUUSD", "achat", 1.0, 2095, 2115, "london")
    assert results == [{"step": steps[0], "result": {"orderId": "2"}, "error": None}]


def test_execute_steps_skips_unknown_step_type(monkeypatch):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: _permissive_state())
    monkeypatch.setattr(loop.broker, "close_position", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas être appelé")))
    monkeypatch.setattr(loop.broker, "place_market_order", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas être appelé")))
    results = loop.execute_steps("tok", "acc", [{"type": "inconnu"}], region="london")
    assert results == []


def test_execute_steps_refuses_when_dry_run_active(monkeypatch):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: _permissive_state(dry_run=True))
    monkeypatch.setattr(loop.broker, "place_market_order", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit jamais être appelé en dry-run")))
    steps = [{"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
              "volume": 1.0, "entry": 2100, "stop_loss": 2095, "take_profit": 2115}]
    results = loop.execute_steps("tok", "acc", steps, region="london")
    assert len(results) == 1
    assert results[0]["result"] is None
    assert "refusée" in results[0]["error"]


def test_execute_steps_refuses_when_kill_switch_active(monkeypatch):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: _permissive_state(kill_switch=True))
    monkeypatch.setattr(loop.broker, "place_market_order", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit jamais être appelé, interrupteur actif")))
    steps = [{"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
              "volume": 1.0, "entry": 2100, "stop_loss": 2095, "take_profit": 2115}]
    results = loop.execute_steps("tok", "acc", steps, region="london")
    assert results[0]["result"] is None
    assert "refusée" in results[0]["error"]


def test_execute_steps_preserves_prior_results_when_a_later_step_fails(monkeypatch):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: _permissive_state())
    monkeypatch.setattr(loop.broker, "close_position", lambda *a, **k: {"orderId": "1"})
    monkeypatch.setattr(loop.broker, "place_market_order", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("échec MetaApi")))
    steps = [
        {"type": "clôture_simulee", "position_id": "42", "symbol": "XAUUSD"},
        {"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
         "volume": 1.0, "entry": 2100, "stop_loss": 2095, "take_profit": 2115},
    ]
    results = loop.execute_steps("tok", "acc", steps, region="london")
    assert len(results) == 2
    assert results[0]["result"] == {"orderId": "1"}
    assert results[0]["error"] is None
    assert results[1]["result"] is None
    assert "échec MetaApi" in results[1]["error"]


def test_log_decision_appends_jsonl_with_timestamp(tmp_path):
    path = tmp_path / "decisions_log.jsonl"
    loop._log_decision({"action": "aucune", "reason": "signal neutre"}, path=str(path))
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["action"] == "aucune"
    assert "timestamp" in entry


def test_run_cycle_no_action_when_kill_switch_engaged(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": True, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas être appelé")))

    result = loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker())

    assert result == {"action": "ignore", "reason": "interrupteur d'urgence activé"}


def test_run_cycle_logs_dry_run_without_executing(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 10000)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100})
    fake_steps = [{"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
                    "volume": 1.0, "entry": 2100, "stop_loss": 2095, "take_profit": 2115}]
    monkeypatch.setattr(loop.bot, "decide_and_act", lambda *a, **k: {"action": "simulation", "steps": fake_steps})
    monkeypatch.setattr(loop.broker, "place_market_order", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit jamais être appelé en dry-run")))

    result = loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    assert result["action"] == "simulation_dry_run"


def test_run_cycle_applies_active_risk_profile_to_sizing_and_circuit_breaker(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True, "risk_profile": 5})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 10000)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100})
    captured = {}

    def fake_decide_and_act(*args, **kwargs):
        captured["risk_pct"] = kwargs.get("risk_pct")
        return {"action": "aucune", "reason": "signal neutre"}

    monkeypatch.setattr(loop.bot, "decide_and_act", fake_decide_and_act)

    cb = loop.risk.CircuitBreaker()
    loop.run_cycle("tok", "acc", "td-key", cb, now=_FRESH_NOW)

    assert captured["risk_pct"] == 0.10
    assert cb.threshold_pct == 0.175


def test_run_cycle_defaults_to_profile_3_when_risk_profile_absent_from_state(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 10000)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100})
    captured = {}

    def fake_decide_and_act(*args, **kwargs):
        captured["risk_pct"] = kwargs.get("risk_pct")
        return {"action": "aucune", "reason": "signal neutre"}

    monkeypatch.setattr(loop.bot, "decide_and_act", fake_decide_and_act)

    cb = loop.risk.CircuitBreaker()
    loop.run_cycle("tok", "acc", "td-key", cb, now=_FRESH_NOW)

    assert captured["risk_pct"] == 0.05
    assert cb.threshold_pct == 0.10


def test_run_cycle_executes_when_not_dry_run(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": False})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 10000)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100})
    fake_steps = [{"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
                    "volume": 1.0, "entry": 2100, "stop_loss": 2095, "take_profit": 2115}]
    monkeypatch.setattr(loop.bot, "decide_and_act", lambda *a, **k: {"action": "simulation", "steps": fake_steps})
    placed = {}
    monkeypatch.setattr(loop.broker, "place_market_order", lambda *a, **k: placed.__setitem__("called", True) or {"orderId": "3"})

    result = loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    assert placed.get("called") is True
    assert result["action"] == "exécuté"


def test_run_cycle_re_checks_kill_switch_before_executing(monkeypatch, tmp_path):
    """L'interrupteur d'urgence peut être activé pendant la collecte des
    données (avant decide_and_act) : run_cycle doit relire l'état juste
    avant d'exécuter, pas seulement au tout début du cycle."""
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 10000)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100})
    fake_steps = [{"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
                    "volume": 1.0, "entry": 2100, "stop_loss": 2095, "take_profit": 2115}]
    monkeypatch.setattr(loop.bot, "decide_and_act", lambda *a, **k: {"action": "simulation", "steps": fake_steps})
    monkeypatch.setattr(loop.broker, "place_market_order", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit jamais être appelé")))

    calls = {"n": 0}

    def fake_load_state(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"kill_switch": False, "dry_run": False}
        return {"kill_switch": True, "dry_run": False}

    monkeypatch.setattr(loop.state, "load_state", fake_load_state)

    result = loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    assert result["action"] == "simulation_dry_run"


def test_with_retry_returns_result_on_first_success():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "ok"

    assert loop._with_retry(fn) == "ok"
    assert calls["n"] == 1


def test_with_retry_recovers_after_a_transient_failure(monkeypatch):
    """Reproduit le cas réel du 2026-09-14 (timeout Twelve Data isolé,
    429 MetaApi isolé) : un échec qui ne se reproduit pas au coup
    suivant ne doit plus faire perdre tout le cycle."""
    monkeypatch.setattr(loop.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("Read timed out")
        return "ok"

    assert loop._with_retry(fn) == "ok"
    assert calls["n"] == 2


def test_with_retry_raises_last_error_after_exhausting_attempts(monkeypatch):
    monkeypatch.setattr(loop.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise RuntimeError(f"échec {calls['n']}")

    with pytest.raises(RuntimeError, match="échec 3"):
        loop._with_retry(fn)
    assert calls["n"] == loop.NETWORK_RETRY_ATTEMPTS == 3


def test_with_retry_sleeps_between_attempts_but_not_after_the_last(monkeypatch):
    sleeps = []
    monkeypatch.setattr(loop.time, "sleep", lambda s: sleeps.append(s))

    def fn():
        raise RuntimeError("toujours en échec")

    with pytest.raises(RuntimeError):
        loop._with_retry(fn)

    assert sleeps == [loop.NETWORK_RETRY_DELAY_SECONDS] * (loop.NETWORK_RETRY_ATTEMPTS - 1)


def test_run_cycle_logs_error_when_data_fetch_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop.time, "sleep", lambda s: None)  # échec persistant -> _with_retry épuise ses tentatives
    monkeypatch.setattr(
        loop.confluence, "fetch_gold_candles",
        lambda api_key: (_ for _ in ()).throw(RuntimeError("Twelve Data indisponible")),
    )

    result = loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker())

    assert result["action"] == "erreur"
    assert "Twelve Data indisponible" in result["reason"]


def test_run_cycle_recovers_from_a_single_transient_network_blip(monkeypatch, tmp_path):
    """Le cas réel du 2026-09-14 de bout en bout : un premier appel à
    fetch_gold_candles échoue (timeout), le second (retry) réussit — le
    cycle doit se dérouler normalement, pas finir en "erreur"."""
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def flaky_fetch_gold_candles(api_key):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("HTTPSConnectionPool: Read timed out")
        return [_FRESH_CANDLE]

    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", flaky_fetch_gold_candles)
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 10000.0)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100})
    monkeypatch.setattr(loop.bot, "decide_and_act", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})

    result = loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    assert result["action"] == "aucune"
    assert calls["n"] == 2


def test_run_cycle_logs_error_when_decide_and_act_raises(monkeypatch, tmp_path):
    """decide_and_act (via risk.compute_position_size) peut lever si le
    solde ou la taille de contrat renvoyés par le broker sont invalides
    — ce cas doit être capturé et journalisé, jamais laissé remonter."""
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 10000)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100})
    monkeypatch.setattr(
        loop.bot, "decide_and_act",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("solde invalide")),
    )

    result = loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    assert result["action"] == "erreur"
    assert "solde invalide" in result["reason"]


def test_circuit_breaker_uses_separate_persist_path_from_kill_switch_state():
    """La persistance du coupe-circuit ne doit pas partager le même
    fichier que kill_switch/dry_run (voir CIRCUIT_BREAKER_STATE_PATH) —
    évite qu'une écriture concurrente de l'API (Task 3) sur l'un des
    deux fichiers ne puisse jamais écraser silencieusement une
    modification en cours sur l'autre."""
    assert loop.CIRCUIT_BREAKER_STATE_PATH != loop.state.STATE_PATH


def test_run_cycle_caches_candles_after_successful_fetch(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    fake_candles = [{"time": "2026-09-11 16:40:00", "close": 3651.5}]
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: fake_candles)
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 10000.0)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100})
    monkeypatch.setattr(loop.bot, "decide_and_act", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})

    loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    cached = json.loads((tmp_path / "latest_candles.json").read_text(encoding="utf-8"))
    assert cached["candles"] == fake_candles
    assert "fetched_at" in cached


def test_run_cycle_caches_balance_after_successful_fetch(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 9140.10)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100})
    monkeypatch.setattr(loop.bot, "decide_and_act", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})

    loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    cached = json.loads((tmp_path / "latest_balance.json").read_text(encoding="utf-8"))
    assert cached["balance"] == 9140.10
    assert "fetched_at" in cached


def test_run_cycle_caches_positions_after_successful_fetch(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    fake_positions = [{"symbol": "XAUUSD", "type": "POSITION_TYPE_SELL", "volume": 2.0,
                        "openPrice": 4316.28, "currentPrice": 4316.49, "profit": -36.18}]
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 10000.0)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: fake_positions)
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100})
    monkeypatch.setattr(loop.bot, "decide_and_act", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})

    loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    cached = json.loads((tmp_path / "latest_positions.json").read_text(encoding="utf-8"))
    assert cached["positions"] == fake_positions
    assert "fetched_at" in cached


def test_run_cycle_leaves_earlier_caches_intact_when_a_later_call_fails(monkeypatch, tmp_path):
    """Le 504 déjà observé en production tombe sur get_symbol_specification,
    APRÈS candles/balance/positions dans run_cycle — ces trois caches
    doivent rester écrits même quand ce dernier appel échoue."""
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.time, "sleep", lambda s: None)  # échec persistant -> _with_retry épuise ses tentatives
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 10000.0)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(
        loop.broker, "get_symbol_specification",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("504 Server Error: Gateway Timeout")),
    )

    result = loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    assert result["action"] == "erreur"
    assert (tmp_path / "latest_candles.json").exists()
    assert (tmp_path / "latest_balance.json").exists()
    assert (tmp_path / "latest_positions.json").exists()


def test_run_cycle_does_not_write_candles_cache_when_fetch_itself_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.time, "sleep", lambda s: None)  # échec persistant -> _with_retry épuise ses tentatives
    monkeypatch.setattr(
        loop.confluence, "fetch_gold_candles",
        lambda api_key: (_ for _ in ()).throw(RuntimeError("Twelve Data indisponible")),
    )

    loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker())

    assert not (tmp_path / "latest_candles.json").exists()
    assert not (tmp_path / "latest_balance.json").exists()
    assert not (tmp_path / "latest_positions.json").exists()


def test_run_cycle_picks_up_risk_profile_change_between_cycles(monkeypatch, tmp_path):
    """Test d'intégration exigé par la spec (§Tests,
    docs/superpowers/specs/2026-09-15-gold-bot-risk-profiles-design.md) :
    "le CircuitBreaker partagé voit son threshold_pct changer entre deux
    cycles simulés si risk_profile change dans state.json entre les
    deux". Contrairement aux tests ci-dessus (un seul run_cycle par test,
    avec un mock d'état fixe), celui-ci écrit un vrai state.json, appelle
    run_cycle deux fois avec le MÊME CircuitBreaker, et réécrit le
    fichier entre les deux appels — simulant un changement de profil
    par un opérateur pendant que la boucle tourne."""
    state_path = str(tmp_path / "state.json")
    monkeypatch.setattr(loop.state, "STATE_PATH", state_path)
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 10000.0)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100})
    monkeypatch.setattr(loop.bot, "decide_and_act", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})

    loop.state.save_state({"kill_switch": False, "dry_run": True, "risk_profile": 3}, state_path)
    cb = loop.risk.CircuitBreaker()

    loop.run_cycle("tok", "acc", "td-key", cb, now=_FRESH_NOW)
    assert cb.threshold_pct == 0.10

    # Un opérateur change de profil entre les deux cycles (POST /profile
    # ou édition manuelle) — le process ne redémarre pas, seul le fichier
    # change sous ses pieds.
    loop.state.save_state({"kill_switch": False, "dry_run": True, "risk_profile": 5}, state_path)

    loop.run_cycle("tok", "acc", "td-key", cb, now=_FRESH_NOW)
    assert cb.threshold_pct == 0.175


def test_run_cycle_picks_up_risk_profile_change_via_api_between_cycles(monkeypatch, tmp_path):
    """Variante bout-en-bout du test ci-dessus, suggérée par le
    relecteur : le changement de profil passe réellement par
    POST /profile (gold_bot/api.py), via FastAPI's TestClient, plutôt
    que par une écriture directe de state.json — le chemin qu'un
    opérateur utiliserait réellement."""
    import gold_bot.api as api
    from fastapi.testclient import TestClient

    state_path = str(tmp_path / "state.json")
    monkeypatch.setattr(loop.state, "STATE_PATH", state_path)
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 10000.0)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100})
    monkeypatch.setattr(loop.bot, "decide_and_act", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})
    monkeypatch.setenv("BOT_API_TOKEN", "secret-token")

    loop.state.save_state({"kill_switch": False, "dry_run": True, "risk_profile": 3}, state_path)
    cb = loop.risk.CircuitBreaker()

    loop.run_cycle("tok", "acc", "td-key", cb, now=_FRESH_NOW)
    assert cb.threshold_pct == 0.10

    client = TestClient(api.app)
    response = client.post("/profile", json={"profile": 5}, headers={"X-Bot-Token": "secret-token"})
    assert response.status_code == 200

    loop.run_cycle("tok", "acc", "td-key", cb, now=_FRESH_NOW)
    assert cb.threshold_pct == 0.175


def test_is_market_closed_true_on_saturday():
    assert loop.is_market_closed(datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)) is True


def test_is_market_closed_true_friday_after_22h_utc():
    assert loop.is_market_closed(datetime(2026, 9, 11, 22, 0, tzinfo=timezone.utc)) is True


def test_is_market_closed_false_friday_before_22h_utc():
    assert loop.is_market_closed(datetime(2026, 9, 11, 21, 59, tzinfo=timezone.utc)) is False


def test_is_market_closed_true_sunday_before_22h_utc():
    assert loop.is_market_closed(datetime(2026, 9, 13, 21, 59, tzinfo=timezone.utc)) is True


def test_is_market_closed_false_sunday_after_22h_utc():
    assert loop.is_market_closed(datetime(2026, 9, 13, 22, 0, tzinfo=timezone.utc)) is False


def test_is_market_closed_false_on_a_weekday():
    assert loop.is_market_closed(_FRESH_NOW) is False


def test_run_cycle_ignores_when_market_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    # Bougie à l'horodatage frais malgré tout (reproduit l'incident réel
    # du 12-13/09/2026 côté paper-trading : Twelve Data peut renvoyer une
    # bougie fraîche même marché fermé) — seule l'horloge murale doit
    # bloquer ce cycle, pas la fraîcheur annoncée par l'API.
    saturday_noon = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles",
                         lambda api_key: [{"time": "2026-09-12 12:00:00", "close": 2100}])
    monkeypatch.setattr(loop.broker, "get_account_balance",
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas être appelé")))

    result = loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker(), now=saturday_noon)

    assert result == {"action": "ignore", "reason": "marché XAU/USD fermé (week-end)"}


def test_run_cycle_ignores_when_candle_data_is_stale(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    # Dernière bougie à 16:40, "now" injecté à 16:46 -> 6 minutes de
    # décalage, au-delà du seuil de 5 minutes.
    stale_now = datetime(2026, 9, 11, 16, 46, tzinfo=timezone.utc)
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_balance",
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas être appelé")))

    result = loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker(), now=stale_now)

    assert result == {"action": "ignore", "reason": "données périmées"}


def test_run_cycle_still_caches_candles_when_market_closed(monkeypatch, tmp_path):
    """Le cache candles (purement cosmétique pour le tableau de bord) doit
    rester à jour même quand le cycle n'agit pas — seuls les appels
    broker (solde/positions/spec) sont évités."""
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    saturday_noon = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    fake_candles = [{"time": "2026-09-12 12:00:00", "close": 2100}]
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: fake_candles)

    loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker(), now=saturday_noon)

    cached = json.loads((tmp_path / "latest_candles.json").read_text(encoding="utf-8"))
    assert cached["candles"] == fake_candles


def test_run_cycle_continues_when_market_open_and_data_fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 10000.0)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100})
    monkeypatch.setattr(loop.bot, "decide_and_act", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})

    result = loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    assert result == {"action": "aucune", "reason": "signal neutre"}


def test_run_cycle_continues_when_a_dashboard_cache_write_fails(monkeypatch, tmp_path):
    """Une panne disque sur un cache de tableau de bord (LATEST_*_PATH,
    purement cosmétique) ne doit jamais interrompre le cycle avant que
    la décision/exécution réelle n'ait eu lieu — même contrat que
    _log_decision pour son propre fichier."""
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 10000.0)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100})
    monkeypatch.setattr(loop.bot, "decide_and_act", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})
    monkeypatch.setattr(
        loop.state, "save_state",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disque plein")),
    )

    result = loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    assert result == {"action": "aucune", "reason": "signal neutre"}
    logged = json.loads((tmp_path / "decisions_log.jsonl").read_text(encoding="utf-8").strip())
    assert logged["action"] == "aucune"
