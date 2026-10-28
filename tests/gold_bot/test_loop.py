import json
from datetime import datetime, timedelta, timezone

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


def test_execute_steps_calls_modify_for_modification_step(monkeypatch):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: _permissive_state())
    captured = {}
    monkeypatch.setattr(
        loop.broker, "modify_position_stop_loss",
        lambda token, account_id, position_id, stop_loss, region:
            captured.__setitem__("modify", (token, account_id, position_id, stop_loss, region)) or {"orderId": "3"},
    )
    steps = [{"type": "modification_simulee", "position_id": "42", "symbol": "XAUUSD", "new_stop_loss": 2098.5}]
    results = loop.execute_steps("tok", "acc", steps, region="london")
    assert captured["modify"] == ("tok", "acc", "42", 2098.5, "london")
    assert results == [{"step": steps[0], "result": {"orderId": "3"}, "error": None}]


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
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas être appelé")))

    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker())

    assert result == {"action": "ignore", "reason": "interrupteur d'urgence activé", "symbol": "XAUUSD"}


def test_run_cycle_logs_dry_run_without_executing(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 10000})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    fake_steps = [{"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
                    "volume": 1.0, "entry": 2100, "stop_loss": 2095, "take_profit": 2115}]
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "simulation", "steps": fake_steps})
    monkeypatch.setattr(loop.broker, "place_market_order", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit jamais être appelé en dry-run")))

    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    assert result["action"] == "simulation_dry_run"


def test_run_cycle_applies_active_risk_profile_to_sizing_and_circuit_breaker(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True, "risk_profile": 5})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 10000})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    captured = {}

    def fake_decide_and_act(*args, **kwargs):
        captured["risk_pct"] = kwargs.get("risk_pct")
        return {"action": "aucune", "reason": "signal neutre"}

    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", fake_decide_and_act)

    cb = loop.risk.CircuitBreaker()
    loop.run_cycle("tok", "acc", cb, now=_FRESH_NOW)

    assert captured["risk_pct"] == 0.10
    assert cb.threshold_pct == 0.175


def test_run_cycle_defaults_to_profile_3_when_risk_profile_absent_from_state(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 10000})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    captured = {}

    def fake_decide_and_act(*args, **kwargs):
        captured["risk_pct"] = kwargs.get("risk_pct")
        return {"action": "aucune", "reason": "signal neutre"}

    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", fake_decide_and_act)

    cb = loop.risk.CircuitBreaker()
    loop.run_cycle("tok", "acc", cb, now=_FRESH_NOW)

    assert captured["risk_pct"] == 0.05
    assert cb.threshold_pct == 0.10


def test_run_cycle_executes_when_not_dry_run(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": False})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 10000})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    fake_steps = [{"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
                    "volume": 1.0, "entry": 2100, "stop_loss": 2095, "take_profit": 2115}]
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "simulation", "steps": fake_steps})
    placed = {}
    monkeypatch.setattr(loop.broker, "place_market_order", lambda *a, **k: placed.__setitem__("called", True) or {"orderId": "3"})

    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    assert placed.get("called") is True
    assert result["action"] == "exécuté"


def test_run_cycle_records_trade_opened_on_successful_order(monkeypatch, tmp_path):
    """Même montage que test_run_cycle_executes_when_not_dry_run
    (dry_run=False, kill_switch=False, un step "ouverture_simulee" qui
    aboutit sans erreur) -- le coupe-circuit partagé doit voir son
    compteur de trades/jour avancer après une exécution réelle
    réussie."""
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": False})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 10000})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    fake_steps = [{"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
                    "volume": 1.0, "entry": 2100, "stop_loss": 2095, "take_profit": 2115}]
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "simulation", "steps": fake_steps})
    monkeypatch.setattr(loop.broker, "place_market_order", lambda *a, **k: {"orderId": "3"})

    cb = loop.risk.CircuitBreaker(persist_path=str(tmp_path / "cb_state.json"))
    assert cb._trades_opened_today == 0

    result = loop.run_cycle("tok", "acc", cb, now=_FRESH_NOW)

    assert result["action"] == "exécuté"
    assert cb._trades_opened_today == 1


def test_run_cycle_does_not_record_trade_on_modification_only(monkeypatch, tmp_path):
    """Même montage, mais decide_and_act_vwap renvoie un step
    "modification_simulee" (mise à jour du stop suiveur sur une position
    déjà ouverte) -- ne doit jamais compter comme un nouveau trade."""
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": False})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 10000})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    fake_steps = [{"type": "modification_simulee", "position_id": "42", "symbol": "XAUUSD", "new_stop_loss": 2098.5}]
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "simulation", "steps": fake_steps})
    monkeypatch.setattr(loop.broker, "modify_position_stop_loss", lambda *a, **k: {"orderId": "4"})

    cb = loop.risk.CircuitBreaker(persist_path=str(tmp_path / "cb_state.json"))
    assert cb._trades_opened_today == 0

    result = loop.run_cycle("tok", "acc", cb, now=_FRESH_NOW)

    assert result["action"] == "exécuté"
    assert cb._trades_opened_today == 0


def test_run_cycle_does_not_record_trade_when_execution_fails(monkeypatch, tmp_path):
    """Même montage que le test de succès ci-dessus, mais
    broker.place_market_order lève -- execute_steps capture l'erreur et
    la renvoie dans results, run_cycle ne doit jamais compter un trade
    sur un échec d'exécution."""
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": False})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 10000})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    fake_steps = [{"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
                    "volume": 1.0, "entry": 2100, "stop_loss": 2095, "take_profit": 2115}]
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "simulation", "steps": fake_steps})
    monkeypatch.setattr(loop.broker, "place_market_order",
                         lambda *a, **k: (_ for _ in ()).throw(RuntimeError("échec MetaApi")))

    cb = loop.risk.CircuitBreaker(persist_path=str(tmp_path / "cb_state.json"))

    result = loop.run_cycle("tok", "acc", cb, now=_FRESH_NOW)

    assert result["action"] == "erreur"
    assert cb._trades_opened_today == 0


def test_run_cycle_records_trade_opened_once_for_partial_and_runner_legs(monkeypatch, tmp_path):
    """gold_bot.bot.decide_and_act_vwap peut scinder une entrée en deux
    ordres broker quand le compte est assez gros pour une prise de
    profit partielle (jambe partielle + jambe runner, voir
    bot.py:314-322 : deux steps "ouverture_simulee" dans la même liste
    quand runner_volume >= volume_step) -- le compteur de trades/jour ne
    doit avancer qu'une fois par CYCLE, jamais une fois par step. Un
    "correctif" qui remplacerait le any(...) de run_cycle par une boucle
    appelant record_trade_opened() par step correspondant doublerait
    silencieusement le compte sans ce test."""
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": False})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 10000})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    fake_steps = [
        {"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
         "volume": 0.6, "entry": 2100, "stop_loss": 2095, "take_profit": 2110},
        {"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
         "volume": 0.4, "entry": 2100, "stop_loss": 2095, "take_profit": None},
    ]
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "simulation", "steps": fake_steps})
    monkeypatch.setattr(loop.broker, "place_market_order", lambda *a, **k: {"orderId": "5"})

    cb = loop.risk.CircuitBreaker(persist_path=str(tmp_path / "cb_state.json"))
    assert cb._trades_opened_today == 0

    result = loop.run_cycle("tok", "acc", cb, now=_FRESH_NOW)

    assert result["action"] == "exécuté"
    assert len(result["results"]) == 2
    assert cb._trades_opened_today == 1


def test_run_cycle_passes_max_trades_per_day_from_risk_profile(monkeypatch, tmp_path):
    """Même montage que
    test_run_cycle_applies_active_risk_profile_to_sizing_and_circuit_breaker
    (dry_run=True, risk_profile=5) -- max_trades_per_day doit être relu
    depuis le profil actif à chaque cycle, même schéma que
    threshold_pct."""
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True, "risk_profile": 5})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 10000})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})

    cb = loop.risk.CircuitBreaker(persist_path=str(tmp_path / "cb_state.json"))
    loop.run_cycle("tok", "acc", cb, now=_FRESH_NOW)

    assert cb.max_trades_per_day == loop.risk.RISK_PROFILE_PARAMS[5]["max_trades_per_day"]


def test_entry_price_has_drifted_false_within_tolerance():
    step = {"entry": 2100, "stop_loss": 2095}  # risque prévu = 5, seuil = 1.25
    assert loop._entry_price_has_drifted(step, 2101) is False


def test_entry_price_has_drifted_true_beyond_tolerance():
    step = {"entry": 2100, "stop_loss": 2095}  # risque prévu = 5, seuil = 1.25
    assert loop._entry_price_has_drifted(step, 2102) is True


def test_entry_price_has_drifted_exactly_at_tolerance_boundary():
    step = {"entry": 2100, "stop_loss": 2095}  # risque prévu = 5, seuil = 0.25*5 = 1.25
    assert loop._entry_price_has_drifted(step, 2101.25) is False
    assert loop._entry_price_has_drifted(step, 2101.26) is True


def test_entry_price_has_drifted_true_when_intended_risk_is_zero():
    step = {"entry": 2100, "stop_loss": 2100}
    assert loop._entry_price_has_drifted(step, 2100) is True


def test_run_cycle_skips_opening_when_price_has_drifted_too_much(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": False})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 10000})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification",
                         lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    fake_steps = [{"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
                    "volume": 1.0, "entry": 2100, "stop_loss": 2095, "take_profit": 2115}]
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "simulation", "steps": fake_steps})
    # 1er appel (candles pour decide_and_act) renvoie 2100 ; le re-contrôle
    # anti-slippage juste avant l'exécution renvoie 2110 -- écart de 10,
    # bien au-delà du seuil de 1.25 (25% du risque prévu de 5).
    calls = {"n": 0}

    def fake_fetch(*a, **k):
        calls["n"] += 1
        return [{"time": "2026-09-11 16:40:00", "close": 2100 if calls["n"] == 1 else 2110}]

    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", fake_fetch)
    monkeypatch.setattr(loop.broker, "place_market_order",
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas être appelé : prix trop éloigné")))

    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    assert result["action"] == "ignore"
    assert calls["n"] == 2


def test_run_cycle_executes_opening_when_price_has_not_drifted(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": False})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 10000})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification",
                         lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    fake_steps = [{"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
                    "volume": 1.0, "entry": 2100, "stop_loss": 2095, "take_profit": 2115}]
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "simulation", "steps": fake_steps})
    # Écart de 1 sur un risque prévu de 5 -> ratio 0.2, sous le seuil de 0.25.
    calls = {"n": 0}

    def fake_fetch(*a, **k):
        calls["n"] += 1
        return [{"time": "2026-09-11 16:40:00", "close": 2100 if calls["n"] == 1 else 2101}]

    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", fake_fetch)
    placed = {}
    monkeypatch.setattr(loop.broker, "place_market_order", lambda *a, **k: placed.__setitem__("called", True) or {"orderId": "3"})

    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    assert placed.get("called") is True
    assert result["action"] == "exécuté"


def test_run_cycle_skips_opening_when_slippage_recheck_fetch_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": False})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.time, "sleep", lambda s: None)  # épuise les tentatives de _with_retry sans attendre
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 10000})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification",
                         lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    fake_steps = [{"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
                    "volume": 1.0, "entry": 2100, "stop_loss": 2095, "take_profit": 2115}]
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "simulation", "steps": fake_steps})
    calls = {"n": 0}

    def fake_fetch(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return [_FRESH_CANDLE]
        raise RuntimeError("Twelve Data indisponible")

    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", fake_fetch)
    monkeypatch.setattr(loop.broker, "place_market_order",
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas être appelé : re-contrôle raté")))

    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    assert result["action"] == "ignore"


def test_run_cycle_still_closes_position_when_reopening_leg_has_drifted(monkeypatch, tmp_path):
    """Renversement : la clôture de l'ancienne position doit toujours
    partir même si la ré-ouverture dans le nouveau sens est écartée par
    le contrôle anti-slippage -- mieux vaut rester plat que d'ouvrir avec
    un risque mal dimensionné."""
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": False})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 10000})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification",
                         lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    fake_steps = [
        {"type": "clôture_simulee", "position_id": "42", "symbol": "XAUUSD"},
        {"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "vente",
         "volume": 1.0, "entry": 2100, "stop_loss": 2105, "take_profit": 2085},
    ]
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "simulation", "steps": fake_steps})
    calls = {"n": 0}

    def fake_fetch(*a, **k):
        calls["n"] += 1
        return [{"time": "2026-09-11 16:40:00", "close": 2100 if calls["n"] == 1 else 2110}]

    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", fake_fetch)
    closed = {}
    monkeypatch.setattr(loop.broker, "close_position", lambda *a, **k: closed.__setitem__("called", True) or {"orderId": "1"})
    monkeypatch.setattr(loop.broker, "place_market_order",
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas être appelé : prix trop éloigné")))

    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    assert closed.get("called") is True
    assert result["action"] == "exécuté"
    assert len(result["steps"]) == 1
    assert result["steps"][0]["type"] == "clôture_simulee"


def test_run_cycle_re_checks_kill_switch_before_executing(monkeypatch, tmp_path):
    """L'interrupteur d'urgence peut être activé pendant la collecte des
    données (avant decide_and_act) : run_cycle doit relire l'état juste
    avant d'exécuter, pas seulement au tout début du cycle."""
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 10000})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    fake_steps = [{"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
                    "volume": 1.0, "entry": 2100, "stop_loss": 2095, "take_profit": 2115}]
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "simulation", "steps": fake_steps})
    monkeypatch.setattr(loop.broker, "place_market_order", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit jamais être appelé")))

    calls = {"n": 0}

    def fake_load_state(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"kill_switch": False, "dry_run": False}
        return {"kill_switch": True, "dry_run": False}

    monkeypatch.setattr(loop.state, "load_state", fake_load_state)

    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

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
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Twelve Data indisponible")),
    )

    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker())

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

    def flaky_fetch_gold_candles(*a, **k):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("HTTPSConnectionPool: Read timed out")
        return [_FRESH_CANDLE]

    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", flaky_fetch_gold_candles)
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000.0, "equity": 10000.0})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})

    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

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
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 10000})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    monkeypatch.setattr(
        loop.bot, "decide_and_act_vwap",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("solde invalide")),
    )

    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

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
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: fake_candles)
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000.0, "equity": 10000.0})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})

    loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    cached = json.loads((tmp_path / "latest_candles.json").read_text(encoding="utf-8"))
    assert cached["candles"] == fake_candles
    assert "fetched_at" in cached


def test_run_cycle_caches_balance_after_successful_fetch(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 9140.10, "equity": 9140.10})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})

    loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

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
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000.0, "equity": 10000.0})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: fake_positions)
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})

    loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

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
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000.0, "equity": 10000.0})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(
        loop.broker, "get_symbol_specification",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("504 Server Error: Gateway Timeout")),
    )

    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

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
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Twelve Data indisponible")),
    )

    loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker())

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
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000.0, "equity": 10000.0})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})

    loop.state.save_state({"kill_switch": False, "dry_run": True, "risk_profile": 3}, state_path)
    cb = loop.risk.CircuitBreaker()

    loop.run_cycle("tok", "acc", cb, now=_FRESH_NOW)
    assert cb.threshold_pct == 0.10

    # Un opérateur change de profil entre les deux cycles (POST /profile
    # ou édition manuelle) — le process ne redémarre pas, seul le fichier
    # change sous ses pieds.
    loop.state.save_state({"kill_switch": False, "dry_run": True, "risk_profile": 5}, state_path)

    loop.run_cycle("tok", "acc", cb, now=_FRESH_NOW)
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
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000.0, "equity": 10000.0})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})
    monkeypatch.setenv("BOT_API_TOKEN", "secret-token")

    loop.state.save_state({"kill_switch": False, "dry_run": True, "risk_profile": 3}, state_path)
    cb = loop.risk.CircuitBreaker()

    loop.run_cycle("tok", "acc", cb, now=_FRESH_NOW)
    assert cb.threshold_pct == 0.10

    client = TestClient(api.app)
    response = client.post("/profile", json={"profile": 5}, headers={"X-Bot-Token": "secret-token"})
    assert response.status_code == 200

    loop.run_cycle("tok", "acc", cb, now=_FRESH_NOW)
    assert cb.threshold_pct == 0.175


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
                         lambda *a, **k: [{"time": "2026-09-12 12:00:00", "close": 2100}])
    monkeypatch.setattr(loop.broker, "get_account_information",
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas être appelé")))

    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=saturday_noon)

    assert result == {"action": "ignore", "reason": "marché XAUUSD fermé (week-end)", "symbol": "XAUUSD"}


def test_run_cycle_ignores_when_candle_data_is_stale(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    # Dernière bougie ouverte à 16:30 (donc close à 16:35, voir
    # confluence.CANDLE_INTERVAL_MINUTES), "now" injecté à 16:46 -> 11
    # minutes de décalage depuis la clôture, au-delà du seuil de 5
    # minutes.
    stale_candle = {"time": "2026-09-11 16:30:00", "close": 2100}
    stale_now = datetime(2026, 9, 11, 16, 46, tzinfo=timezone.utc)
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [stale_candle])
    monkeypatch.setattr(loop.broker, "get_account_information",
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas être appelé")))

    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=stale_now)

    assert result == {"action": "ignore", "reason": "données périmées", "symbol": "XAUUSD"}


def test_run_cycle_treats_just_completed_candle_as_fresh(monkeypatch, tmp_path):
    """Une bougie ouverte à 16:40 clôture à 16:45 (durée de 5 minutes,
    voir confluence.CANDLE_INTERVAL_MINUTES) -- vérifiée à 16:46, elle n'a
    qu'1 minute de retard sur sa clôture, largement sous le seuil de 5
    minutes, même si son horodatage d'OUVERTURE a 6 minutes de retard."""
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    just_completed_now = datetime(2026, 9, 11, 16, 46, tzinfo=timezone.utc)
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000.0, "equity": 10000.0})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})

    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=just_completed_now)

    assert result == {"action": "aucune", "reason": "signal neutre", "symbol": "XAUUSD"}


def test_run_cycle_still_caches_candles_when_market_closed(monkeypatch, tmp_path):
    """Le cache candles (purement cosmétique pour le tableau de bord) doit
    rester à jour même quand le cycle n'agit pas — seuls les appels
    broker (solde/positions/spec) sont évités."""
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    saturday_noon = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    fake_candles = [{"time": "2026-09-12 12:00:00", "close": 2100}]
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: fake_candles)

    loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=saturday_noon)

    cached = json.loads((tmp_path / "latest_candles.json").read_text(encoding="utf-8"))
    assert cached["candles"] == fake_candles


def test_run_cycle_continues_when_market_open_and_data_fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000.0, "equity": 10000.0})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})

    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    assert result == {"action": "aucune", "reason": "signal neutre", "symbol": "XAUUSD"}


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
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000.0, "equity": 10000.0})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})
    monkeypatch.setattr(
        loop.state, "save_state",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disque plein")),
    )

    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    assert result == {"action": "aucune", "reason": "signal neutre", "symbol": "XAUUSD"}
    logged = json.loads((tmp_path / "decisions_log.jsonl").read_text(encoding="utf-8").strip())
    assert logged["action"] == "aucune"


def test_run_cycle_fetches_candles_with_extended_limit_for_vwap_engine(monkeypatch, tmp_path):
    """gold_bot.vwap_reversion a besoin de bien plus d'historique par
    cycle que l'ancien moteur (EMA200 sur des bougies 15min) -- run_cycle
    doit demander CANDLES_FETCH_LIMIT bougies, pas la valeur par défaut
    de fetch_gold_candles."""
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    captured = {}

    def fake_fetch(token, account_id, region, limit=None, symbol=None):
        captured["limit"] = limit
        return [_FRESH_CANDLE]

    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", fake_fetch)
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000.0, "equity": 10000.0})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "aucune", "reason": "pas de signal d'entree"})

    loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    assert captured["limit"] == loop.CANDLES_FETCH_LIMIT


def test_run_cycle_passes_candles_to_decide_and_act_vwap(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    fake_candles = [_FRESH_CANDLE]
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: fake_candles)
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000.0, "equity": 10000.0})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    captured = {}

    def fake_decide_and_act_vwap(candles, **kwargs):
        captured["candles"] = candles
        return {"action": "aucune", "reason": "pas de signal d'entree"}

    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", fake_decide_and_act_vwap)

    loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    assert captured["candles"] == fake_candles


def test_run_cycle_fetches_historical_candles_and_passes_them_to_decide_and_act(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(
        loop, "_historical_candles_cache_path", lambda symbol: str(tmp_path / "historical_candles_cache.json"),
        raising=False,
    )
    fake_candles = [_FRESH_CANDLE]
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: fake_candles)
    monkeypatch.setattr(loop.backtest, "fetch_gold_candles_range", lambda *a, **k: [{"fake": "history"}])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000.0, "equity": 10000.0})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    captured = {}

    def fake_decide_and_act_vwap(candles, **kwargs):
        captured["historical_candles"] = kwargs.get("historical_candles")
        return {"action": "aucune", "reason": "pas de signal d'entree"}

    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", fake_decide_and_act_vwap)

    loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    assert captured["historical_candles"] == [{"fake": "history"}]


def test_run_cycle_reuses_cached_historical_candles_within_ttl(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(
        loop, "_historical_candles_cache_path", lambda symbol: str(tmp_path / "historical_candles_cache.json"),
        raising=False,
    )
    # _now_iso est figé : c'est la valeur écrite comme "fetched_at" dans le
    # cache lors du premier appel -- sans ce contrôle, la comparaison de TTL
    # se ferait contre l'horloge murale réelle, pas contre une durée
    # maîtrisée par le test (voir la correction demandée en revue).
    monkeypatch.setattr(loop, "_now_iso", lambda: "2026-10-22T10:00:00+00:00")
    first_now = datetime(2026, 10, 22, 10, 0, 1, tzinfo=timezone.utc)
    second_now = datetime(2026, 10, 22, 11, 0, 0, tzinfo=timezone.utc)  # +1h, bien sous le TTL de 24h
    candle_times = iter([
        (first_now - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"),
        (second_now - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"),
    ])
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [{"time": next(candle_times), "close": 2100}])
    fetch_calls = []
    monkeypatch.setattr(loop.backtest, "fetch_gold_candles_range",
                         lambda *a, **k: fetch_calls.append(1) or [{"fake": "history"}])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000.0, "equity": 10000.0})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda candles, **kwargs: {"action": "aucune", "reason": "x"})

    cb = loop.risk.CircuitBreaker(persist_path=str(tmp_path / "cb_state.json"))
    loop.run_cycle("tok", "acc", cb, now=first_now)
    loop.run_cycle("tok", "acc", cb, now=second_now)
    assert len(fetch_calls) == 1  # deuxième cycle réutilise le cache, pas de second fetch réseau


def test_run_cycle_refetches_historical_candles_when_cache_is_stale(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(
        loop, "_historical_candles_cache_path", lambda symbol: str(tmp_path / "historical_candles_cache.json"),
        raising=False,
    )
    # Même contrôle que le test ci-dessus, mais avec un écart de 2 jours
    # entre l'écriture du cache et le second cycle -- bien au-delà du TTL
    # de 24h, pour prouver que le cache est bien considéré périmé et
    # redéclenche un vrai fetch réseau, pas réutilisé indéfiniment.
    monkeypatch.setattr(loop, "_now_iso", lambda: "2026-10-20T10:00:00+00:00")
    first_now = datetime(2026, 10, 20, 10, 0, 1, tzinfo=timezone.utc)
    second_now = datetime(2026, 10, 22, 10, 0, 0, tzinfo=timezone.utc)  # +2 jours, bien au-delà du TTL de 24h
    candle_times = iter([
        (first_now - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"),
        (second_now - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"),
    ])
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [{"time": next(candle_times), "close": 2100}])
    fetch_calls = []
    monkeypatch.setattr(loop.backtest, "fetch_gold_candles_range",
                         lambda *a, **k: fetch_calls.append(1) or [{"fake": "history"}])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000.0, "equity": 10000.0})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda candles, **kwargs: {"action": "aucune", "reason": "x"})

    cb = loop.risk.CircuitBreaker(persist_path=str(tmp_path / "cb_state.json"))
    loop.run_cycle("tok", "acc", cb, now=first_now)
    loop.run_cycle("tok", "acc", cb, now=second_now)
    assert len(fetch_calls) == 2  # cache perime -> second cycle refait un vrai fetch reseau


def test_load_or_fetch_historical_candles_never_raises_on_malformed_cache_content(monkeypatch, tmp_path):
    # JSON valide mais pas un dict (TypeError sur cached["fetched_at"]
    # avec l'ancien tuple d'exceptions precis, puisque l'indexation
    # d'une liste par une chaine leve TypeError, pas KeyError) -- doit
    # degrader vers un nouveau fetch plutot que de lever. Trouve en
    # revue finale de branche : la docstring promettait "ne leve
    # jamais", un tuple d'exceptions trop precis ne le garantissait pas
    # vraiment.
    cache_path = tmp_path / "hist.json"
    cache_path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(loop, "_historical_candles_cache_path", lambda symbol: str(cache_path), raising=False)
    monkeypatch.setattr(loop.backtest, "fetch_gold_candles_range", lambda *a, **k: [{"fake": "history"}])

    result = loop._load_or_fetch_historical_candles(
        "token", "account", "XAUUSD", "london", datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc))

    assert result == [{"fake": "history"}]  # cache malforme ignore, nouveau fetch reussi utilise


def test_load_or_fetch_historical_candles_backs_off_after_a_fetch_failure(monkeypatch, tmp_path):
    cache_path = str(tmp_path / "hist.json")
    monkeypatch.setattr(loop, "_historical_candles_cache_path", lambda symbol: cache_path, raising=False)
    # _save_cache ecrit "fetched_at" via loop._now_iso() (horloge reelle),
    # jamais via le `now_dt` injecte -- doit etre fige ici pour que le
    # calcul du backoff reste deterministe (meme precedent que
    # test_run_cycle_refetches_historical_candles_when_cache_is_stale,
    # qui mocke deja _now_iso pour la meme raison).
    fake_now_iso = {"value": None}
    monkeypatch.setattr(loop, "_now_iso", lambda: fake_now_iso["value"])
    fetch_calls = []

    def failing_fetch(*a, **k):
        fetch_calls.append(1)
        raise RuntimeError("MetaApi 429")

    monkeypatch.setattr(loop.backtest, "fetch_gold_candles_range", failing_fetch)
    now1 = datetime(2026, 10, 22, 10, 0, 0, tzinfo=timezone.utc)
    fake_now_iso["value"] = now1.strftime("%Y-%m-%dT%H:%M:%SZ")
    result1 = loop._load_or_fetch_historical_candles("token", "account", "XAUUSD", "london", now1)
    assert result1 == []
    assert len(fetch_calls) == 1
    # 10 minutes plus tard, bien sous le backoff d'echec (1h) : ne doit
    # PAS retenter un fetch.
    now2 = datetime(2026, 10, 22, 10, 10, 0, tzinfo=timezone.utc)
    result2 = loop._load_or_fetch_historical_candles("token", "account", "XAUUSD", "london", now2)
    assert result2 == []
    assert len(fetch_calls) == 1
    # 2 heures plus tard, au-dela du backoff d'echec : doit retenter.
    now3 = datetime(2026, 10, 22, 12, 10, 0, tzinfo=timezone.utc)
    loop._load_or_fetch_historical_candles("token", "account", "XAUUSD", "london", now3)
    assert len(fetch_calls) == 2


def test_run_cycle_counts_trade_when_partial_leg_succeeds_but_runner_leg_fails(monkeypatch, tmp_path):
    # Reprend le meme montage que test_run_cycle_records_trade_opened_once_for_partial_and_runner_legs
    # (Task 4), mais fait echouer SEULEMENT l'ordre de la jambe runner --
    # une vraie position (la jambe partielle) a tout de meme ete ouverte,
    # elle doit donc etre comptee. Trouve en revue finale de branche :
    # l'ancienne condition (not had_error and any(...)) sous-comptait ce
    # cas, puisque had_error devient vrai des qu'UNE SEULE etape echoue
    # dans le lot.
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": False})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 10000})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    fake_steps = [
        {"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
         "volume": 0.6, "entry": 2100, "stop_loss": 2095, "take_profit": 2110},
        {"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
         "volume": 0.4, "entry": 2100, "stop_loss": 2095, "take_profit": None},
    ]
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "simulation", "steps": fake_steps})

    def fake_place_market_order(token, account_id, symbol, direction, volume, stop_loss, take_profit, region):
        if take_profit is None:
            # Jambe runner (pas de takeProfit) : son ordre echoue --
            # la jambe partielle, elle, a reussi (voir ci-dessous).
            raise RuntimeError("ordre runner refuse par le broker")
        return {"orderId": "5"}

    monkeypatch.setattr(loop.broker, "place_market_order", fake_place_market_order)

    cb = loop.risk.CircuitBreaker(persist_path=str(tmp_path / "cb_state.json"))
    assert cb._trades_opened_today == 0

    # now=_FRESH_NOW (pas un horodatage arbitraire) -- _FRESH_CANDLE n'est
    # fraiche que par rapport a _FRESH_NOW ; un autre "now" ferait
    # echouer le garde donnees-perimees de run_cycle avant meme d'y
    # arriver, invalidant tout le test (voir _FRESH_NOW/_FRESH_CANDLE en
    # tete de fichier, meme convention que les autres tests run_cycle).
    loop.run_cycle("token", "account", cb, now=_FRESH_NOW)
    assert cb._trades_opened_today == 1


def test_symbols_includes_gold_and_silver():
    assert loop.SYMBOLS == ["XAUUSD", "XAGUSD"]


def test_candles_cache_path_default_symbol_uses_shared_path():
    assert loop._candles_cache_path("XAUUSD") == loop.LATEST_CANDLES_PATH


def test_candles_cache_path_other_symbol_uses_dedicated_path():
    path = loop._candles_cache_path("XAGUSD")
    assert path != loop.LATEST_CANDLES_PATH
    assert "xagusd" in path.lower()


def test_run_cycle_fetches_candles_for_the_given_symbol(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop, "_candles_cache_path", lambda symbol: str(tmp_path / "latest_candles_xagusd.json"))
    captured = {}

    def fake_fetch(token, account_id, region, limit=None, symbol=None):
        captured["symbol"] = symbol
        return [_FRESH_CANDLE]

    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", fake_fetch)
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000.0, "equity": 10000.0})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 5000, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 20})
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "aucune", "reason": "pas de signal d'entree"})

    loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), symbol="XAGUSD", now=_FRESH_NOW)

    assert captured["symbol"] == "XAGUSD"


def test_run_cycle_caches_candles_for_non_default_symbol_at_dedicated_path(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    silver_cache_path = tmp_path / "latest_candles_xagusd.json"
    monkeypatch.setattr(loop, "_candles_cache_path", lambda symbol: str(silver_cache_path))
    fake_candles = [_FRESH_CANDLE]
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: fake_candles)
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000.0, "equity": 10000.0})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 5000, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 20})
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "aucune", "reason": "pas de signal d'entree"})

    loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), symbol="XAGUSD", now=_FRESH_NOW)

    cached = json.loads(silver_cache_path.read_text(encoding="utf-8"))
    assert cached["candles"] == fake_candles


def test_run_cycle_tags_decisions_with_the_given_symbol(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop, "_candles_cache_path", lambda symbol: str(tmp_path / "latest_candles_xagusd.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000.0, "equity": 10000.0})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 5000, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 20})
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "aucune", "reason": "pas de signal d'entree"})

    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), symbol="XAGUSD", now=_FRESH_NOW)

    assert result["symbol"] == "XAGUSD"


def test_run_cycle_anti_slippage_recheck_uses_the_given_symbol(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": False})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop, "_candles_cache_path", lambda symbol: str(tmp_path / "latest_candles_xagusd.json"))
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 10000})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification",
                         lambda *a, **k: {"contractSize": 5000, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 20})
    fake_steps = [{"type": "ouverture_simulee", "symbol": "XAGUSD", "direction": "achat",
                    "volume": 1.0, "entry": 60, "stop_loss": 59, "take_profit": 62}]
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "simulation", "steps": fake_steps})
    captured_symbols = []

    def fake_fetch(*a, symbol=None, **k):
        captured_symbols.append(symbol)
        return [{"time": "2026-09-11 16:40:00", "close": 60}]

    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", fake_fetch)
    monkeypatch.setattr(loop.broker, "place_market_order", lambda *a, **k: {"orderId": "9"})

    loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), symbol="XAGUSD", now=_FRESH_NOW)

    # Un appel pour les bougies de decide_and_act_vwap, un pour le
    # re-controle anti-slippage -- les deux doivent porter sur XAGUSD,
    # jamais retomber implicitement sur XAUUSD (bug trouve et corrige
    # le 2026-09-30 : l'appel de re-controle ignorait `symbol`).
    assert captured_symbols == ["XAGUSD", "XAGUSD"]


def test_run_cycle_unaffected_when_ftmo_guard_not_passed(monkeypatch, tmp_path):
    """Sans ftmo_guard_instance (valeur par defaut None -- deploiement du
    compte perso actuel) : close_all_positions ne doit jamais etre
    appelee, le cycle se deroule exactement comme avant ce changement."""
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 9000, "equity": 100})
    monkeypatch.setattr(
        loop.broker, "close_all_positions",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit jamais etre appele sans ftmo_guard_instance")))
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})

    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    assert result["action"] == "aucune"


def test_run_cycle_uses_fibonacci_engine_for_ftmo_instance(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 9900})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    monkeypatch.setattr(
        loop.bot, "decide_and_act_vwap",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("decide_and_act_vwap ne doit jamais etre appele avec ftmo_guard_instance")))
    fibonacci_calls = []
    monkeypatch.setattr(
        loop.bot, "decide_and_act_fibonacci",
        lambda *a, **k: fibonacci_calls.append(k) or {"action": "aucune", "reason": "pas de signal d'entree"})

    guard = loop.ftmo_guard.FtmoGuard(isc=10000, now_fn=lambda: _FRESH_NOW)
    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW, ftmo_guard_instance=guard)

    assert result["action"] == "aucune"
    assert len(fibonacci_calls) == 1


def test_run_cycle_uses_vwap_engine_for_personal_instance(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 10000})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    monkeypatch.setattr(
        loop.bot, "decide_and_act_fibonacci",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("decide_and_act_fibonacci ne doit jamais etre appele sans ftmo_guard_instance")))
    vwap_calls = []
    monkeypatch.setattr(
        loop.bot, "decide_and_act_vwap",
        lambda *a, **k: vwap_calls.append(k) or {"action": "aucune", "reason": "signal neutre"})

    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW)  # pas de ftmo_guard_instance

    assert result["action"] == "aucune"
    assert len(vwap_calls) == 1


def test_run_cycle_ftmo_guard_does_nothing_when_equity_above_floors(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 9900})
    monkeypatch.setattr(
        loop.broker, "close_all_positions",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas etre appele, equite au-dessus des deux planchers")))
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    monkeypatch.setattr(loop.bot, "decide_and_act_fibonacci", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})

    guard = loop.ftmo_guard.FtmoGuard(isc=10000, now_fn=lambda: _FRESH_NOW)
    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW, ftmo_guard_instance=guard)

    assert result["action"] == "aucune"


def test_run_cycle_ftmo_guard_closes_all_and_arms_kill_switch_on_daily_breach(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    # Plancher quotidien par defaut (4%) sur un solde de 10000 : 9600.
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 9500})
    close_calls = []
    monkeypatch.setattr(
        loop.broker, "close_all_positions",
        lambda *a, **k: close_calls.append(a) or [{"position_id": "1", "closed": True, "error": None}])
    monkeypatch.setattr(
        loop.bot, "reconcile_positions",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas etre appele apres franchissement FTMO")))
    monkeypatch.setattr(
        loop.bot, "decide_and_act_vwap",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("aucune decision ne doit etre prise apres franchissement FTMO")))
    update_calls = []
    monkeypatch.setattr(loop.state, "update_state", lambda fields, **k: update_calls.append(fields))

    guard = loop.ftmo_guard.FtmoGuard(isc=10000, now_fn=lambda: _FRESH_NOW)
    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW, ftmo_guard_instance=guard)

    assert result["action"] == "ftmo_garde_fou"
    assert result["reason"] == "daily"
    assert len(close_calls) == 1
    assert update_calls == [{"kill_switch": True}]


def test_run_cycle_ftmo_guard_closes_all_and_arms_kill_switch_on_overall_breach(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    # Plancher global par defaut (9%) sur un ISC de 10000 : 9100.
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 9000})
    close_calls = []
    monkeypatch.setattr(
        loop.broker, "close_all_positions",
        lambda *a, **k: close_calls.append(a) or [{"position_id": "1", "closed": True, "error": None}])
    monkeypatch.setattr(
        loop.bot, "reconcile_positions",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas etre appele apres franchissement FTMO")))
    monkeypatch.setattr(
        loop.bot, "decide_and_act_vwap",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("aucune decision ne doit etre prise apres franchissement FTMO")))
    update_calls = []
    monkeypatch.setattr(loop.state, "update_state", lambda fields, **k: update_calls.append(fields))

    guard = loop.ftmo_guard.FtmoGuard(isc=10000, now_fn=lambda: _FRESH_NOW)
    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW, ftmo_guard_instance=guard)

    assert result["action"] == "ftmo_garde_fou"
    assert result["reason"] == "overall"
    assert len(close_calls) == 1
    assert update_calls == [{"kill_switch": True}]


def test_run_cycle_ftmo_guard_still_arms_kill_switch_when_a_close_fails(monkeypatch, tmp_path, capsys):
    """Meme si une fermeture echoue, le kill_switch doit rester arme --
    il a deja ete declenche par la detection du franchissement,
    independamment du resultat de la fermeture."""
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 9000})
    monkeypatch.setattr(
        loop.broker, "close_all_positions",
        lambda *a, **k: [{"position_id": "1", "closed": False, "error": "echec MetaApi"}])
    monkeypatch.setattr(
        loop.bot, "reconcile_positions",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas etre appele apres franchissement FTMO")))
    monkeypatch.setattr(
        loop.bot, "decide_and_act_vwap",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("aucune decision ne doit etre prise apres franchissement FTMO")))
    update_calls = []
    monkeypatch.setattr(loop.state, "update_state", lambda fields, **k: update_calls.append(fields))

    guard = loop.ftmo_guard.FtmoGuard(isc=10000, now_fn=lambda: _FRESH_NOW)
    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW, ftmo_guard_instance=guard)

    assert update_calls == [{"kill_switch": True}]
    assert "ERREUR FTMO" in capsys.readouterr().out
    assert result["action"] == "ftmo_garde_fou"


def test_run_cycle_ftmo_guard_closes_all_and_arms_kill_switch_on_target_reached(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    # Objectif par defaut (10%) sur un ISC de 10000 : 11000.
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 11000})
    close_calls = []
    monkeypatch.setattr(
        loop.broker, "close_all_positions",
        lambda *a, **k: close_calls.append(a) or [{"position_id": "1", "closed": True, "error": None}])
    monkeypatch.setattr(
        loop.bot, "reconcile_positions",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas etre appele apres objectif FTMO atteint")))
    monkeypatch.setattr(
        loop.bot, "decide_and_act_vwap",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("aucune decision ne doit etre prise apres objectif FTMO atteint")))
    update_calls = []
    monkeypatch.setattr(loop.state, "update_state", lambda fields, **k: update_calls.append(fields))

    guard = loop.ftmo_guard.FtmoGuard(isc=10000, now_fn=lambda: _FRESH_NOW)
    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW, ftmo_guard_instance=guard)

    assert result["action"] == "ftmo_garde_fou"
    assert result["reason"] == "target"
    assert len(close_calls) == 1
    assert update_calls == [{"kill_switch": True}]


def test_resolve_symbols_unchanged_without_ftmo_isc():
    assert loop._resolve_symbols(None, None) == ["XAUUSD", "XAGUSD"]
    assert loop._resolve_symbols(None, "XAUUSD") == ["XAUUSD", "XAGUSD"]


def test_resolve_symbols_defaults_to_xauusd_only_with_ftmo_isc():
    assert loop._resolve_symbols("10000", None) == ["XAUUSD"]


def test_resolve_symbols_respects_explicit_ftmo_symbols_override():
    assert loop._resolve_symbols("10000", "XAUUSD,XAGUSD") == ["XAUUSD", "XAGUSD"]
    assert loop._resolve_symbols("10000", "XAGUSD") == ["XAGUSD"]


_SIGNAL_DECISION = {
    "action": "simulation",
    "steps": [{
        "type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
        "volume": 0.1, "entry": 2650.0, "stop_loss": 2645.0, "take_profit": None,
    }],
}


def test_run_cycle_calls_ftmo_signal_email_when_ftmo_guard_active_and_signal_found(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 10000})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    monkeypatch.setattr(loop.bot, "decide_and_act_fibonacci", lambda *a, **k: dict(_SIGNAL_DECISION))
    email_calls = []
    monkeypatch.setattr(loop.notify_ftmo_signal, "maybe_send_signal_email", lambda decision: email_calls.append(decision) or True)

    guard = loop.ftmo_guard.FtmoGuard(isc=10000, now_fn=lambda: _FRESH_NOW)
    loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW, ftmo_guard_instance=guard)

    assert len(email_calls) == 1
    assert email_calls[0]["action"] == "simulation"


def test_run_cycle_never_calls_ftmo_signal_email_without_ftmo_guard(monkeypatch, tmp_path):
    """Instance perso (ftmo_guard_instance=None, valeur par defaut) : le
    meme signal ne doit JAMAIS declencher l'alerte email FTMO."""
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 9000, "equity": 9000})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: dict(_SIGNAL_DECISION))
    monkeypatch.setattr(
        loop.notify_ftmo_signal, "maybe_send_signal_email",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit jamais etre appele sans ftmo_guard_instance")))

    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW)

    assert result["action"] in ("simulation", "simulation_dry_run")
