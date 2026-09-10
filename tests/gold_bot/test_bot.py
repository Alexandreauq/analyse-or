import pytest
import gold_bot.bot as bot
import gold_bot.risk as risk


def _signal(status, entry=None, stop_loss=None, take_profit=None):
    return {"status": status, "price": entry, "entry": entry, "stop_loss": stop_loss,
            "take_profit": take_profit, "trend": "baissier" if status != "neutre" else "neutre",
            "pattern": None}


def _open_circuit_breaker():
    # Aucun appel à check() préalable avec un solde bas -> autorise
    # toujours (solde de départ = solde courant passé au premier appel).
    return risk.CircuitBreaker()


def test_decide_and_act_no_action_on_neutral_signal(monkeypatch):
    monkeypatch.setattr(bot.confluence, "compute_signal", lambda candles: _signal("neutre"))
    result = bot.decide_and_act([], contract_size=100, balance=10000, open_positions=[], circuit_breaker=_open_circuit_breaker())
    assert result["action"] == "aucune"
    assert result["reason"] == "signal neutre"


def test_decide_and_act_opens_when_no_existing_position(monkeypatch):
    monkeypatch.setattr(
        bot.confluence, "compute_signal",
        lambda candles: _signal("achat", entry=2100, stop_loss=2095, take_profit=2115),
    )
    result = bot.decide_and_act([], contract_size=100, balance=10000, open_positions=[], circuit_breaker=_open_circuit_breaker())
    assert result["action"] == "simulation"
    assert len(result["steps"]) == 1
    step = result["steps"][0]
    assert step["type"] == "ouverture_simulee"
    assert step["direction"] == "achat"
    assert step["volume"] == pytest.approx(1.0)  # (10000*0.05) / (5*100)


def test_decide_and_act_no_action_when_same_direction_already_open(monkeypatch):
    monkeypatch.setattr(
        bot.confluence, "compute_signal",
        lambda candles: _signal("achat", entry=2100, stop_loss=2095, take_profit=2115),
    )
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY"}]
    result = bot.decide_and_act([], contract_size=100, balance=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker())
    assert result["action"] == "aucune"
    assert result["reason"] == "position déjà ouverte dans le même sens"


def test_decide_and_act_closes_then_opens_on_opposite_signal(monkeypatch):
    monkeypatch.setattr(
        bot.confluence, "compute_signal",
        lambda candles: _signal("vente", entry=2100, stop_loss=2110, take_profit=2085),
    )
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY"}]
    result = bot.decide_and_act([], contract_size=100, balance=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker())
    assert result["action"] == "simulation"
    assert len(result["steps"]) == 2
    assert result["steps"][0] == {"type": "clôture_simulee", "position_id": "1", "symbol": "XAUUSD"}
    assert result["steps"][1]["type"] == "ouverture_simulee"
    assert result["steps"][1]["direction"] == "vente"


def test_decide_and_act_blocked_by_circuit_breaker():
    from datetime import datetime, timezone
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)  # solde de départ du jour

    import gold_bot.confluence as confluence
    real_compute_signal = confluence.compute_signal
    try:
        confluence.compute_signal = lambda candles: _signal("achat", entry=2100, stop_loss=2095, take_profit=2115)
        result = bot.decide_and_act([], contract_size=100, balance=8900, open_positions=[], circuit_breaker=cb)  # -11% depuis 10000
    finally:
        confluence.compute_signal = real_compute_signal

    assert result["action"] == "aucune"
    assert result["reason"] == "coupe-circuit journalier déclenché"


def test_decide_and_act_never_calls_real_broker_order_functions(monkeypatch):
    """Garde-fou explicite de ce plan : même avec un signal achat clair
    et aucune position existante, decide_and_act ne doit jamais appeler
    broker.place_market_order ni broker.close_position."""
    monkeypatch.setattr(
        bot.confluence, "compute_signal",
        lambda candles: _signal("achat", entry=2100, stop_loss=2095, take_profit=2115),
    )
    called = {"place": False, "close": False}
    monkeypatch.setattr(bot.broker, "place_market_order", lambda *a, **k: called.__setitem__("place", True))
    monkeypatch.setattr(bot.broker, "close_position", lambda *a, **k: called.__setitem__("close", True))

    bot.decide_and_act([], contract_size=100, balance=10000, open_positions=[], circuit_breaker=_open_circuit_breaker())

    assert called == {"place": False, "close": False}


def test_decide_and_act_closes_all_matching_positions_on_reversal(monkeypatch):
    monkeypatch.setattr(
        bot.confluence, "compute_signal",
        lambda candles: _signal("vente", entry=2100, stop_loss=2110, take_profit=2085),
    )
    existing = [
        {"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY"},
        {"id": "2", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY"},
    ]
    result = bot.decide_and_act([], contract_size=100, balance=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker())
    assert result["action"] == "simulation"
    close_steps = [s for s in result["steps"] if s["type"] == "clôture_simulee"]
    assert len(close_steps) == 2
    assert {s["position_id"] for s in close_steps} == {"1", "2"}
    assert result["steps"][-1]["type"] == "ouverture_simulee"


def test_decide_and_act_no_action_when_any_matching_position_is_same_direction(monkeypatch):
    monkeypatch.setattr(
        bot.confluence, "compute_signal",
        lambda candles: _signal("achat", entry=2100, stop_loss=2095, take_profit=2115),
    )
    existing = [
        {"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_SELL"},
        {"id": "2", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY"},
    ]
    result = bot.decide_and_act([], contract_size=100, balance=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker())
    assert result["action"] == "aucune"


def test_decide_and_act_no_action_when_position_type_unrecognized(monkeypatch):
    monkeypatch.setattr(
        bot.confluence, "compute_signal",
        lambda candles: _signal("achat", entry=2100, stop_loss=2095, take_profit=2115),
    )
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_UNKNOWN"}]
    result = bot.decide_and_act([], contract_size=100, balance=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker())
    assert result["action"] == "aucune"
    assert "non reconnu" in result["reason"]


def test_decide_and_act_never_calls_real_broker_order_functions_on_reversal(monkeypatch):
    monkeypatch.setattr(
        bot.confluence, "compute_signal",
        lambda candles: _signal("vente", entry=2100, stop_loss=2110, take_profit=2085),
    )
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY"}]
    called = {"place": False, "close": False}
    monkeypatch.setattr(bot.broker, "place_market_order", lambda *a, **k: called.__setitem__("place", True))
    monkeypatch.setattr(bot.broker, "close_position", lambda *a, **k: called.__setitem__("close", True))

    bot.decide_and_act([], contract_size=100, balance=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker())

    assert called == {"place": False, "close": False}


def test_reconcile_positions_calls_broker(monkeypatch):
    captured = {}

    def fake_get_open_positions(token, account_id, region=bot.broker.DEFAULT_MT5_REGION):
        captured["args"] = (token, account_id, region)
        return [{"id": "1", "symbol": "XAUUSD"}]

    monkeypatch.setattr(bot.broker, "get_open_positions", fake_get_open_positions)
    result = bot.reconcile_positions("tok", "acc123")

    assert result == [{"id": "1", "symbol": "XAUUSD"}]
    assert captured["args"] == ("tok", "acc123", bot.broker.DEFAULT_MT5_REGION)
