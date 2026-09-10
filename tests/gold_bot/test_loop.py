import json
from datetime import datetime, timezone

import gold_bot.loop as loop


def test_execute_steps_calls_close_for_closing_step(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        loop.broker, "close_position",
        lambda token, account_id, position_id, region: captured.__setitem__("close", (token, account_id, position_id, region)) or {"orderId": "1"},
    )
    steps = [{"type": "clôture_simulee", "position_id": "42", "symbol": "XAUUSD"}]
    results = loop.execute_steps("tok", "acc", steps, region="london")
    assert captured["close"] == ("tok", "acc", "42", "london")
    assert results == [{"step": steps[0], "result": {"orderId": "1"}}]


def test_execute_steps_calls_place_order_for_opening_step(monkeypatch):
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
    assert results == [{"step": steps[0], "result": {"orderId": "2"}}]


def test_execute_steps_skips_unknown_step_type(monkeypatch):
    monkeypatch.setattr(loop.broker, "close_position", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas être appelé")))
    monkeypatch.setattr(loop.broker, "place_market_order", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas être appelé")))
    results = loop.execute_steps("tok", "acc", [{"type": "inconnu"}], region="london")
    assert results == []


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
