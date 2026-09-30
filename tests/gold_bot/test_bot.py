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
    result = bot.decide_and_act([], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker())
    assert result["action"] == "aucune"
    assert result["reason"] == "signal neutre"


def test_decide_and_act_opens_when_no_existing_position(monkeypatch):
    monkeypatch.setattr(
        bot.confluence, "compute_signal",
        lambda candles: _signal("achat", entry=2100, stop_loss=2095, take_profit=2115),
    )
    result = bot.decide_and_act([], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker())
    assert result["action"] == "simulation"
    assert len(result["steps"]) == 1
    step = result["steps"][0]
    assert step["type"] == "ouverture_simulee"
    assert step["direction"] == "achat"
    assert step["volume"] == pytest.approx(1.0)  # (10000*0.05) / (5*100)


def test_decide_and_act_uses_explicit_risk_pct_when_provided(monkeypatch):
    monkeypatch.setattr(
        bot.confluence, "compute_signal",
        lambda candles: _signal("achat", entry=2100, stop_loss=2095, take_profit=2115),
    )
    result = bot.decide_and_act(
        [], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=[],
        circuit_breaker=_open_circuit_breaker(), risk_pct=0.10,
    )
    step = result["steps"][0]
    assert step["volume"] == pytest.approx(2.0)  # (10000*0.10) / (5*100)


def test_decide_and_act_no_action_when_same_direction_already_open(monkeypatch):
    monkeypatch.setattr(
        bot.confluence, "compute_signal",
        lambda candles: _signal("achat", entry=2100, stop_loss=2095, take_profit=2115),
    )
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY"}]
    result = bot.decide_and_act([], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker())
    assert result["action"] == "aucune"
    assert result["reason"] == "position déjà ouverte dans le même sens"


def test_decide_and_act_closes_then_opens_on_opposite_signal(monkeypatch):
    monkeypatch.setattr(
        bot.confluence, "compute_signal",
        lambda candles: _signal("vente", entry=2100, stop_loss=2110, take_profit=2085),
    )
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY"}]
    result = bot.decide_and_act([], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker())
    assert result["action"] == "simulation"
    assert len(result["steps"]) == 2
    assert result["steps"][0] == {"type": "clôture_simulee", "position_id": "1", "symbol": "XAUUSD"}
    assert result["steps"][1]["type"] == "ouverture_simulee"
    assert result["steps"][1]["direction"] == "vente"


def test_decide_and_act_blocked_by_circuit_breaker():
    from datetime import datetime, timezone
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)  # référence de départ du jour (equity)

    import gold_bot.confluence as confluence
    real_compute_signal = confluence.compute_signal
    try:
        confluence.compute_signal = lambda candles: _signal("achat", entry=2100, stop_loss=2095, take_profit=2115)
        # equity à -11% depuis 10000 -> coupe-circuit déclenché, même avec
        # un balance encore intact (voir test ci-dessous pour la
        # distinction explicite balance/equity, le coeur du correctif).
        result = bot.decide_and_act([], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500, balance=10000, equity=8900,
                                     open_positions=[], circuit_breaker=cb)
    finally:
        confluence.compute_signal = real_compute_signal

    assert result["action"] == "aucune"
    assert result["reason"] == "coupe-circuit journalier déclenché"


def test_decide_and_act_circuit_breaker_reacts_to_equity_not_balance(monkeypatch):
    """Le coeur du correctif : une position ouverte en perte flottante
    (visible dans equity, pas encore dans balance tant qu'elle n'est pas
    clôturée) doit déclencher le coupe-circuit — avant ce correctif,
    balance seul ne l'aurait jamais vu venir."""
    from datetime import datetime, timezone
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)  # référence de départ du jour (equity=10000)

    monkeypatch.setattr(
        bot.confluence, "compute_signal",
        lambda candles: _signal("achat", entry=2100, stop_loss=2095, take_profit=2115),
    )
    # balance encore à 10000 (rien de réalisé) mais equity à 8900 (-11%,
    # perte flottante d'une position ouverte) -> doit bloquer.
    result = bot.decide_and_act([], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500, balance=10000, equity=8900,
                                 open_positions=[], circuit_breaker=cb)

    assert result["action"] == "aucune"
    assert result["reason"] == "coupe-circuit journalier déclenché"


def test_decide_and_act_circuit_breaker_not_tripped_by_stale_balance_when_equity_healthy(monkeypatch):
    """Symétrique du test précédent : un balance en apparence bas (par
    exemple juste après une clôture perdante déjà comptée) ne doit plus
    bloquer si l'equity réelle est saine."""
    from datetime import datetime, timezone
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)  # référence de départ du jour (equity=10000)

    monkeypatch.setattr(
        bot.confluence, "compute_signal",
        lambda candles: _signal("achat", entry=2100, stop_loss=2095, take_profit=2115),
    )
    result = bot.decide_and_act([], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500, balance=8900, equity=10000,
                                 open_positions=[], circuit_breaker=cb)

    assert result["action"] == "simulation"


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

    bot.decide_and_act([], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker())

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
    result = bot.decide_and_act([], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker())
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
    result = bot.decide_and_act([], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker())
    assert result["action"] == "aucune"


def test_decide_and_act_no_action_when_position_type_unrecognized(monkeypatch):
    monkeypatch.setattr(
        bot.confluence, "compute_signal",
        lambda candles: _signal("achat", entry=2100, stop_loss=2095, take_profit=2115),
    )
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_UNKNOWN"}]
    result = bot.decide_and_act([], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker())
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

    bot.decide_and_act([], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker())

    assert called == {"place": False, "close": False}


def _blackout_candles():
    # Une seule bougie suffit (compute_signal renvoie neutre via son
    # garde SCALP_MIN_CANDLES avant de toucher aux autres champs) —
    # horodatée pile sur le CPI du 11/09/2026 (8h30 ET = 12h30 UTC,
    # source : bls.gov/schedule/news_release/cpi.htm).
    return [{"time": "2026-09-11 12:30:00", "close": 2100}]


def test_decide_and_act_closes_open_position_during_news_blackout():
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY"}]
    result = bot.decide_and_act(
        _blackout_candles(), contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500, balance=10000, equity=10000,
        open_positions=existing, circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    assert result["steps"] == [{"type": "clôture_simulee", "position_id": "1", "symbol": "XAUUSD"}]


def test_decide_and_act_closes_all_matching_positions_during_news_blackout():
    existing = [
        {"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY"},
        {"id": "2", "symbol": "XAUUSD", "type": "POSITION_TYPE_SELL"},
    ]
    result = bot.decide_and_act(
        _blackout_candles(), contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500, balance=10000, equity=10000,
        open_positions=existing, circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    close_steps = [s for s in result["steps"] if s["type"] == "clôture_simulee"]
    assert len(close_steps) == 2
    assert {s["position_id"] for s in close_steps} == {"1", "2"}
    assert not any(s["type"] == "ouverture_simulee" for s in result["steps"])


def test_decide_and_act_no_action_during_news_blackout_when_no_open_position():
    result = bot.decide_and_act(
        _blackout_candles(), contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500, balance=10000, equity=10000,
        open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "aucune"
    assert "publication macro" in result["reason"]


def test_decide_and_act_no_action_when_position_type_unrecognized_during_blackout():
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_UNKNOWN"}]
    result = bot.decide_and_act(
        _blackout_candles(), contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500, balance=10000, equity=10000,
        open_positions=existing, circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "aucune"
    assert "non reconnu" in result["reason"]


def test_decide_and_act_never_calls_real_broker_order_functions_during_blackout(monkeypatch):
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY"}]
    called = {"place": False, "close": False}
    monkeypatch.setattr(bot.broker, "place_market_order", lambda *a, **k: called.__setitem__("place", True))
    monkeypatch.setattr(bot.broker, "close_position", lambda *a, **k: called.__setitem__("close", True))

    bot.decide_and_act(
        _blackout_candles(), contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500, balance=10000, equity=10000,
        open_positions=existing, circuit_breaker=_open_circuit_breaker(),
    )

    assert called == {"place": False, "close": False}


def test_decide_and_act_ignores_positions_for_other_symbols_during_blackout():
    existing = [{"id": "1", "symbol": "EURUSD", "type": "POSITION_TYPE_BUY"}]
    result = bot.decide_and_act(
        _blackout_candles(), contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500, balance=10000, equity=10000,
        open_positions=existing, circuit_breaker=_open_circuit_breaker(), symbol="XAUUSD",
    )
    assert result["action"] == "aucune"
    assert "publication macro" in result["reason"]


def test_decide_and_act_rounds_volume_down_to_broker_step(monkeypatch):
    monkeypatch.setattr(
        bot.confluence, "compute_signal",
        lambda candles: _signal("achat", entry=2100, stop_loss=2094.7, take_profit=2115),
    )
    # distance = 5.3 -> taille brute = 500 / (5.3*100) = 0.943396... ->
    # arrondi vers le bas au pas de 0.01 = 0.94, jamais 0.95.
    result = bot.decide_and_act(
        [], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["steps"][0]["volume"] == pytest.approx(0.94)


def test_decide_and_act_no_action_when_size_rounds_below_broker_minimum(monkeypatch):
    """Compte trop petit pour ce stop à ce niveau de risque : ni clôture
    ni ouverture, même en présence d'une position existante à inverser —
    voir la docstring de decide_and_act pour le choix assumé."""
    monkeypatch.setattr(
        bot.confluence, "compute_signal",
        lambda candles: _signal("vente", entry=2100, stop_loss=2106, take_profit=2085),
    )
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY"}]
    # Petit compte : distance = 6 -> taille brute = (100*0.05) / (6*100)
    # = 0.00833... -> arrondi à 0.00 au pas de 0.01 -> sous min_volume
    # (0.01) -> None.
    result = bot.decide_and_act(
        [], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=100, equity=100, open_positions=existing, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "compte trop petit pour ce stop (volume sous le minimum du broker)"}


def test_reconcile_positions_calls_broker(monkeypatch):
    captured = {}

    def fake_get_open_positions(token, account_id, region=bot.broker.DEFAULT_MT5_REGION):
        captured["args"] = (token, account_id, region)
        return [{"id": "1", "symbol": "XAUUSD"}]

    monkeypatch.setattr(bot.broker, "get_open_positions", fake_get_open_positions)
    result = bot.reconcile_positions("tok", "acc123")

    assert result == [{"id": "1", "symbol": "XAUUSD"}]
    assert captured["args"] == ("tok", "acc123", bot.broker.DEFAULT_MT5_REGION)


def _macro_payload(composite_score=20.0, cftc_percentile=50.0, ma200=4000.0, has_entry=True):
    alerts = [{"kind": "entree"}] if has_entry else []
    return {
        "composite_score": composite_score,
        "cftc_percentile": cftc_percentile,
        "technical": {"ma200": ma200, "spot": ma200},
        "alerts": alerts,
    }


def test_decide_and_act_swing_opens_when_entry_alert_and_no_position():
    payload = _macro_payload(has_entry=True, ma200=4000.0)
    candles = [{"time": "2026-09-29 10:00:00", "close": 4010.0}]
    result = bot.decide_and_act_swing(
        payload, candles, contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    assert len(result["steps"]) == 1
    step = result["steps"][0]
    assert step["type"] == "ouverture_simulee"
    assert step["direction"] == "achat"
    assert step["entry"] == 4010.0
    assert step["stop_loss"] == pytest.approx(4000.0 * 0.97)


def test_decide_and_act_swing_no_action_when_no_entry_alert():
    payload = _macro_payload(has_entry=False)
    result = bot.decide_and_act_swing(
        payload, [{"time": "2026-09-29 10:00:00", "close": 4010.0}], contract_size=100, volume_step=0.01,
        min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=[],
        circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "pas de signal d'entrée macro"}


def test_decide_and_act_swing_no_action_when_no_candles():
    payload = _macro_payload(has_entry=True)
    result = bot.decide_and_act_swing(
        payload, [], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "aucune bougie disponible pour le prix courant"}


def test_decide_and_act_swing_no_action_when_levels_unavailable():
    payload = _macro_payload(has_entry=True, ma200=None)
    result = bot.decide_and_act_swing(
        payload, [{"time": "2026-09-29 10:00:00", "close": 4010.0}], contract_size=100, volume_step=0.01,
        min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=[],
        circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "niveaux d'entrée indisponibles (MM200 absente ou stop invalide)"}


def test_decide_and_act_swing_holds_open_position_when_no_exit_condition():
    from datetime import datetime, timezone
    payload = _macro_payload(composite_score=20.0, cftc_percentile=50.0)
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "time": "2026-09-20T00:00:00.000Z"}]
    result = bot.decide_and_act_swing(
        payload, [{"time": "2026-09-29 10:00:00", "close": 4010.0}], contract_size=100, volume_step=0.01,
        min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=existing,
        circuit_breaker=_open_circuit_breaker(), now=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )
    assert result["action"] == "aucune"


def test_decide_and_act_swing_closes_when_composite_degrades():
    from datetime import datetime, timezone
    payload = _macro_payload(composite_score=0.0, cftc_percentile=50.0)
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "time": "2026-09-20T00:00:00.000Z"}]
    result = bot.decide_and_act_swing(
        payload, [{"time": "2026-09-29 10:00:00", "close": 4010.0}], contract_size=100, volume_step=0.01,
        min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=existing,
        circuit_breaker=_open_circuit_breaker(), now=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )
    assert result == {"action": "simulation", "steps": [{"type": "clôture_simulee", "position_id": "1", "symbol": "XAUUSD"}]}


def test_decide_and_act_swing_closes_when_cftc_extreme():
    from datetime import datetime, timezone
    payload = _macro_payload(composite_score=20.0, cftc_percentile=90.0)
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "time": "2026-09-20T00:00:00.000Z"}]
    result = bot.decide_and_act_swing(
        payload, [{"time": "2026-09-29 10:00:00", "close": 4010.0}], contract_size=100, volume_step=0.01,
        min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=existing,
        circuit_breaker=_open_circuit_breaker(), now=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )
    assert result == {"action": "simulation", "steps": [{"type": "clôture_simulee", "position_id": "1", "symbol": "XAUUSD"}]}


def test_decide_and_act_swing_closes_when_max_holding_days_exceeded():
    from datetime import datetime, timezone
    payload = _macro_payload(composite_score=20.0, cftc_percentile=50.0)
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "time": "2026-08-01T00:00:00.000Z"}]
    result = bot.decide_and_act_swing(
        payload, [{"time": "2026-09-29 10:00:00", "close": 4010.0}], contract_size=100, volume_step=0.01,
        min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=existing,
        circuit_breaker=_open_circuit_breaker(), now=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )
    assert result == {"action": "simulation", "steps": [{"type": "clôture_simulee", "position_id": "1", "symbol": "XAUUSD"}]}


def test_decide_and_act_swing_refuses_on_unexpected_position_type():
    payload = _macro_payload(composite_score=20.0, cftc_percentile=50.0)
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_SELL", "time": "2026-09-20T00:00:00.000Z"}]
    result = bot.decide_and_act_swing(
        payload, [{"time": "2026-09-29 10:00:00", "close": 4010.0}], contract_size=100, volume_step=0.01,
        min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=existing,
        circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "position de type inattendu (pas un achat), aucune action par prudence"}


def test_decide_and_act_swing_refuses_when_multiple_positions_open():
    payload = _macro_payload(composite_score=20.0, cftc_percentile=50.0)
    existing = [
        {"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "time": "2026-09-20T00:00:00.000Z"},
        {"id": "2", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "time": "2026-09-21T00:00:00.000Z"},
    ]
    result = bot.decide_and_act_swing(
        payload, [{"time": "2026-09-29 10:00:00", "close": 4010.0}], contract_size=100, volume_step=0.01,
        min_volume=0.01, max_volume=500, balance=10000, equity=10000, open_positions=existing,
        circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "plusieurs positions ouvertes sur ce symbole, aucune action par prudence"}


def test_decide_and_act_swing_blocked_by_circuit_breaker():
    from datetime import datetime, timezone
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)  # référence de départ du jour (equity=10000)
    payload = _macro_payload(has_entry=True, ma200=4000.0)
    # equity à -11% depuis 10000 -> coupe-circuit déclenché.
    result = bot.decide_and_act_swing(
        payload, [{"time": "2026-09-29 10:00:00", "close": 4010.0}], contract_size=100, volume_step=0.01,
        min_volume=0.01, max_volume=500, balance=10000, equity=8900, open_positions=[], circuit_breaker=cb,
    )
    assert result == {"action": "aucune", "reason": "coupe-circuit journalier déclenché"}


def test_decide_and_act_swing_no_action_when_size_below_minimum():
    payload = _macro_payload(has_entry=True, ma200=4000.0)
    result = bot.decide_and_act_swing(
        payload, [{"time": "2026-09-29 10:00:00", "close": 4010.0}], contract_size=100, volume_step=0.01,
        min_volume=1000.0, max_volume=5000.0, balance=10000, equity=10000, open_positions=[],
        circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "compte trop petit pour ce stop (volume sous le minimum du broker)"}


def _vwap_state(close=2000.0, vwap=2010.0, vwap_std=2.0, ema200=1995.0, ema50=2005.0,
                 hour_utc=10, bars_into_session=20):
    return {"close": close, "vwap": vwap, "vwap_std": vwap_std, "ema200": ema200, "ema50": ema50,
            "hour_utc": hour_utc, "bars_into_session": bars_into_session}


def test_decide_and_act_vwap_no_action_when_insufficient_history(monkeypatch):
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: None)
    result = bot.decide_and_act_vwap(
        [], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "historique insuffisant pour l'EMA200"}


def test_decide_and_act_vwap_opens_achat_on_bullish_regime_extension(monkeypatch):
    # regime haussier (close > ema200) et close <= vwap - 2*std (2010 - 2*10 = 1990)
    state = _vwap_state(close=1985.0, vwap=2010.0, vwap_std=10.0, ema200=1900.0)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_vwap(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    step = result["steps"][0]
    assert step["type"] == "ouverture_simulee"
    assert step["direction"] == "achat"
    assert step["entry"] == 1985.0
    assert step["stop_loss"] == pytest.approx(2010.0 - 3 * 10.0)
    assert step["take_profit"] is None


def test_decide_and_act_vwap_opens_vente_on_bearish_regime_extension(monkeypatch):
    # regime baissier (close < ema200) et close >= vwap + 2*std (2010 + 2*10 = 2030)
    state = _vwap_state(close=2035.0, vwap=2010.0, vwap_std=10.0, ema200=2100.0)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_vwap(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    step = result["steps"][0]
    assert step["direction"] == "vente"
    assert step["stop_loss"] == pytest.approx(2010.0 + 3 * 10.0)


def test_decide_and_act_vwap_no_action_when_no_entry_signal(monkeypatch):
    # ni condition haussiere ni baissiere reunie
    state = _vwap_state(close=2010.0, vwap=2010.0, vwap_std=2.0, ema200=1995.0)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_vwap(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "pas de signal d'entree"}


def test_decide_and_act_vwap_no_action_before_min_bars_into_session(monkeypatch):
    state = _vwap_state(close=1985.0, vwap=2010.0, vwap_std=10.0, ema200=1900.0, bars_into_session=3)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_vwap(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "debut de session, VWAP pas encore stabilisee"}


def test_decide_and_act_vwap_no_action_after_session_end_hour(monkeypatch):
    state = _vwap_state(close=1985.0, vwap=2010.0, vwap_std=10.0, ema200=1900.0, hour_utc=22)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_vwap(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "trop tard dans la session pour ouvrir"}


def test_decide_and_act_vwap_no_action_when_std_zero(monkeypatch):
    state = _vwap_state(close=2005.0, vwap=2010.0, vwap_std=0.0, ema200=1995.0)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_vwap(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "ecart-type VWAP indisponible"}


def test_decide_and_act_vwap_no_action_when_distance_too_small(monkeypatch):
    # entree/stop tres proches (std minuscule) -> distance < 0.2% du prix
    # close <= vwap - 2*std (2010 - 0.002 = 2009.998) et proche de
    # stop_loss = vwap - 3*std (2010 - 0.003 = 2009.997)
    state = _vwap_state(close=2009.9975, vwap=2010.0, vwap_std=0.001, ema200=1995.0)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_vwap(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "distance entree-stop trop faible (ecart-type degenere)"}


def test_decide_and_act_vwap_holds_open_achat_position_when_above_ema50(monkeypatch):
    state = _vwap_state(close=2020.0, ema50=2005.0, hour_utc=10)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY"}]
    result = bot.decide_and_act_vwap(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "position ouverte, aucune condition de sortie (EMA50/fin de session)"}


def test_decide_and_act_vwap_closes_achat_when_close_below_ema50(monkeypatch):
    state = _vwap_state(close=2000.0, ema50=2005.0, hour_utc=10)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY"}]
    result = bot.decide_and_act_vwap(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "simulation", "steps": [{"type": "clôture_simulee", "position_id": "1", "symbol": "XAUUSD"}]}


def test_decide_and_act_vwap_closes_vente_when_close_above_ema50(monkeypatch):
    state = _vwap_state(close=2010.0, ema50=2005.0, hour_utc=10)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_SELL"}]
    result = bot.decide_and_act_vwap(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "simulation", "steps": [{"type": "clôture_simulee", "position_id": "1", "symbol": "XAUUSD"}]}


def test_decide_and_act_vwap_closes_at_session_end_even_without_ema50_cross(monkeypatch):
    state = _vwap_state(close=2020.0, ema50=2005.0, hour_utc=22)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY"}]
    result = bot.decide_and_act_vwap(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "simulation", "steps": [{"type": "clôture_simulee", "position_id": "1", "symbol": "XAUUSD"}]}


def test_decide_and_act_vwap_refuses_on_unexpected_position_type(monkeypatch):
    state = _vwap_state()
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_UNKNOWN"}]
    result = bot.decide_and_act_vwap(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "position de type inattendu, aucune action par prudence"}


def test_decide_and_act_vwap_refuses_when_multiple_positions_open(monkeypatch):
    state = _vwap_state()
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    existing = [
        {"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY"},
        {"id": "2", "symbol": "XAUUSD", "type": "POSITION_TYPE_SELL"},
    ]
    result = bot.decide_and_act_vwap(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "plusieurs positions ouvertes sur ce symbole, aucune action par prudence"}


def test_decide_and_act_vwap_blocked_by_circuit_breaker(monkeypatch):
    from datetime import datetime, timezone
    state = _vwap_state(close=1985.0, vwap=2010.0, vwap_std=10.0, ema200=1900.0)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)  # référence de départ du jour (equity=10000)
    result = bot.decide_and_act_vwap(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=8900, open_positions=[], circuit_breaker=cb,
    )
    assert result == {"action": "aucune", "reason": "coupe-circuit journalier déclenché"}


def test_decide_and_act_vwap_no_action_when_size_below_minimum(monkeypatch):
    state = _vwap_state(close=1985.0, vwap=2010.0, vwap_std=10.0, ema200=1900.0)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_vwap(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=1000.0, max_volume=5000.0,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "compte trop petit pour ce stop (volume sous le minimum du broker)"}
