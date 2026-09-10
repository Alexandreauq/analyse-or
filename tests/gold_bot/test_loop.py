import json

import gold_bot.loop as loop


def _permissive_state(**overrides):
    base = {"kill_switch": False, "dry_run": False}
    base.update(overrides)
    return base


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
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [{"close": 2100}])
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 10000)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100})
    fake_steps = [{"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
                    "volume": 1.0, "entry": 2100, "stop_loss": 2095, "take_profit": 2115}]
    monkeypatch.setattr(loop.bot, "decide_and_act", lambda *a, **k: {"action": "simulation", "steps": fake_steps})
    monkeypatch.setattr(loop.broker, "place_market_order", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit jamais être appelé en dry-run")))

    result = loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker())

    assert result["action"] == "simulation_dry_run"


def test_run_cycle_executes_when_not_dry_run(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": False})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [{"close": 2100}])
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 10000)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100})
    fake_steps = [{"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
                    "volume": 1.0, "entry": 2100, "stop_loss": 2095, "take_profit": 2115}]
    monkeypatch.setattr(loop.bot, "decide_and_act", lambda *a, **k: {"action": "simulation", "steps": fake_steps})
    placed = {}
    monkeypatch.setattr(loop.broker, "place_market_order", lambda *a, **k: placed.__setitem__("called", True) or {"orderId": "3"})

    result = loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker())

    assert placed.get("called") is True
    assert result["action"] == "exécuté"


def test_run_cycle_re_checks_kill_switch_before_executing(monkeypatch, tmp_path):
    """L'interrupteur d'urgence peut être activé pendant la collecte des
    données (avant decide_and_act) : run_cycle doit relire l'état juste
    avant d'exécuter, pas seulement au tout début du cycle."""
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [{"close": 2100}])
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

    result = loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker())

    assert result["action"] == "simulation_dry_run"


def test_run_cycle_logs_error_when_data_fetch_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(
        loop.confluence, "fetch_gold_candles",
        lambda api_key: (_ for _ in ()).throw(RuntimeError("Twelve Data indisponible")),
    )

    result = loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker())

    assert result["action"] == "erreur"
    assert "Twelve Data indisponible" in result["reason"]


def test_run_cycle_logs_error_when_decide_and_act_raises(monkeypatch, tmp_path):
    """decide_and_act (via risk.compute_position_size) peut lever si le
    solde ou la taille de contrat renvoyés par le broker sont invalides
    — ce cas doit être capturé et journalisé, jamais laissé remonter."""
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [{"close": 2100}])
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 10000)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100})
    monkeypatch.setattr(
        loop.bot, "decide_and_act",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("solde invalide")),
    )

    result = loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker())

    assert result["action"] == "erreur"
    assert "solde invalide" in result["reason"]


def test_circuit_breaker_uses_separate_persist_path_from_kill_switch_state():
    """La persistance du coupe-circuit ne doit pas partager le même
    fichier que kill_switch/dry_run (voir CIRCUIT_BREAKER_STATE_PATH) —
    évite qu'une écriture concurrente de l'API (Task 3) sur l'un des
    deux fichiers ne puisse jamais écraser silencieusement une
    modification en cours sur l'autre."""
    assert loop.CIRCUIT_BREAKER_STATE_PATH != loop.state.STATE_PATH
