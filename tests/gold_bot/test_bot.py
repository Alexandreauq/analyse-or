import pytest
import gold_bot.bot as bot
import gold_bot.risk as risk


@pytest.fixture(autouse=True)
def _default_volatility_shock_guard(monkeypatch):
    """Le garde de choc de volatilite (decide_and_act_vwap/
    decide_and_act_fibonacci, ajoute le 2026-10-28) appelle
    confluence.is_volatility_shock a chaque evaluation d'entree -- sans
    historique reel (>=105 bougies), la vraie fonction renvoie None
    (historique insuffisant -> bloque par prudence), hors sujet pour la
    quasi-totalite des tests de ce fichier qui passent un seul candle
    factice. Scope a ce fichier (pas un conftest.py du dossier) :
    confluence.is_volatility_shock est le MEME objet module partout --
    un defaut autouse au niveau dossier avait pollue les tests de
    test_confluence.py qui exercent la vraie fonction directement.
    Un test qui veut exercer le choc explicitement l'ecrase avec son
    propre monkeypatch.setattr dans son corps (meme monkeypatch, meme
    test -- la derniere affectation gagne)."""
    monkeypatch.setattr(bot.confluence, "is_volatility_shock", lambda candles: False)


@pytest.fixture(autouse=True)
def _default_ai_shadow_veto(monkeypatch):
    """Le veto IA fantome (gold_bot/ai_shadow.py, ajoute le 2026-10-28)
    est appele par decide_and_act_fibonacci a chaque signal d'entree
    confirme. Sans ce defaut, les tests qui atteignent ce chemin
    ecriraient pour de vrai dans gold_bot/ai_shadow_log.jsonl (le vrai
    fichier de prod) et, si ANTHROPIC_API_KEY est present dans
    l'environnement de test, tenteraient un vrai appel reseau facture.
    Ne concerne plus que decide_and_act_ichimoku depuis ce chantier --
    decide_and_act_fibonacci utilise desormais
    ai_discretion.judge_fibonacci_entry (voir _default_ai_discretion_judge
    ci-dessous). Un test qui veut exercer shadow_judge_entry directement
    l'ecrase avec son propre monkeypatch (voir
    tests/gold_bot/test_ai_shadow.py)."""
    monkeypatch.setattr(bot.ai_shadow, "shadow_judge_entry", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _default_ai_discretion_judge(monkeypatch):
    """Les fiches "happy path" de decide_and_act_fibonacci (deux jambes,
    TP/SL exacts...) datent d'avant ce chantier et ne testent pas le
    jugement IA lui-meme (voir tests/gold_bot/test_ai_discretion.py pour
    ca) -- ce defaut "confirme" systematiquement, dans le MEME sens et
    aux MEMES distances que la regle (multiplicateurs 1.0/1.0 implicites,
    en renvoyant directement les distances de reference), pour que ces
    fiches restent inchangees. Un test qui veut exercer
    judge_fibonacci_entry lui-meme l'ecrase avec son propre monkeypatch."""
    def _confirm_rule(symbol, state, candles, *, baseline_stop_loss_distance,
                       baseline_take_profit_distance, ftmo_buffer, log_path=None, now=None):
        return {
            "action": state.get("bias"), "confiance": "haute",
            "stop_loss_distance": baseline_stop_loss_distance,
            "take_profit_distance": baseline_take_profit_distance,
            "raison": "defaut de test",
        }
    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry", _confirm_rule)


@pytest.fixture(autouse=True)
def _default_ai_news_watch(monkeypatch):
    """La fenetre grise post-actualite (gold_bot/ai_news_watch.py, ajoutee
    le 2026-11-06) est appelee par decide_and_act_fibonacci quand `as_of`
    tombe dans la fenetre de grace suivant un blackout calendaire connu
    (confluence.minutes_since_blackout_ended). Sans ce defaut, un test qui
    utilise par coincidence une date dans une de ces fenetres ecrirait pour
    de vrai dans gold_bot/ai_news_watch_log.jsonl et, si ANTHROPIC_API_KEY
    est present dans l'environnement de test, tenterait un vrai appel
    reseau facture -- meme risque que _default_ai_shadow_veto ci-dessus.
    Un test qui veut exercer judge_post_news_calm directement l'ecrase avec
    son propre monkeypatch (voir tests/gold_bot/test_ai_news_watch.py et les
    tests de non-regression de l'incident du 06/11 dans ce fichier)."""
    monkeypatch.setattr(bot.ai_news_watch, "judge_post_news_calm", lambda *a, **k: None)


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


def test_decide_and_act_vwap_closes_open_position_during_news_blackout(monkeypatch):
    state = _vwap_state()
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "takeProfit": None,
                 "openPrice": 2000.0, "stopLoss": 1990.0, "time": "2026-09-11T10:00:00+00:00"}]
    result = bot.decide_and_act_vwap(
        _blackout_candles(), contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "simulation", "steps": [
        {"type": "clôture_simulee", "position_id": "1", "symbol": "XAUUSD"},
    ]}


def test_decide_and_act_vwap_no_action_during_news_blackout_when_no_position(monkeypatch):
    state = _vwap_state()
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_vwap(
        _blackout_candles(), contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "publication macro imminente ou en cours, aucune position ouverte à clôturer"}


def test_decide_and_act_vwap_no_action_during_volatility_shock(monkeypatch):
    # regime haussier (close > ema200) et close <= vwap - 1.5*std (2010 - 1.5*20 = 1980)
    # -- signal d'entree valide SANS le choc, pour isoler le garde.
    state = _vwap_state(close=1975.0, vwap=2010.0, vwap_std=20.0, ema200=1900.0)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    monkeypatch.setattr(bot.confluence, "is_volatility_shock", lambda candles: True)
    result = bot.decide_and_act_vwap(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "volatilite anormale (actualite probable), signal ignore par prudence"}


def test_decide_and_act_vwap_opens_achat_with_partial_and_runner_legs(monkeypatch):
    # regime haussier (close > ema200) et close <= vwap - 1.5*std (2010 - 1.5*20 = 1980)
    state = _vwap_state(close=1975.0, vwap=2010.0, vwap_std=20.0, ema200=1900.0)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_vwap(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    assert len(result["steps"]) == 2
    partial, runner = result["steps"]
    stop_loss = 2010.0 - 2.5 * 20.0  # 1960.0
    distance = 1975.0 - stop_loss  # 15.0

    assert partial["type"] == "ouverture_simulee"
    assert partial["direction"] == "achat"
    assert partial["entry"] == 1975.0
    assert partial["stop_loss"] == pytest.approx(stop_loss)
    assert partial["take_profit"] == pytest.approx(1975.0 + 2.0 * distance)
    assert partial["volume"] == pytest.approx(0.23)  # floor(0.33 * 0.7, pas 0.01)

    assert runner["type"] == "ouverture_simulee"
    assert runner["direction"] == "achat"
    assert runner["stop_loss"] == pytest.approx(stop_loss)
    assert runner["take_profit"] is None
    assert runner["volume"] == pytest.approx(0.10)  # 0.33 - 0.23


def test_decide_and_act_vwap_blocked_by_unfavorable_odds_check(monkeypatch):
    state = _vwap_state(close=1975.0, vwap=2010.0, vwap_std=20.0, ema200=1900.0)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    monkeypatch.setattr(
        bot.odds_check, "historical_odds_for_setup_cached",
        lambda candles, direction, symbol="XAUUSD": {
            "sample_size": 25, "win_rate_pct": 30.0, "avg_return_pct": -0.2, "verdict": "unfavorable",
        },
    )
    result = bot.decide_and_act_vwap(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
        historical_candles=[{"dummy": True}],
    )
    assert result == {"action": "aucune", "reason": "cotes historiques defavorables pour ce type de signal"}


def test_decide_and_act_vwap_opens_when_odds_check_favorable(monkeypatch):
    state = _vwap_state(close=1975.0, vwap=2010.0, vwap_std=20.0, ema200=1900.0)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    monkeypatch.setattr(
        bot.odds_check, "historical_odds_for_setup_cached",
        lambda candles, direction, symbol="XAUUSD": {
            "sample_size": 25, "win_rate_pct": 60.0, "avg_return_pct": 0.3, "verdict": "favorable",
        },
    )
    result = bot.decide_and_act_vwap(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
        historical_candles=[{"dummy": True}],
    )
    assert result["action"] == "simulation"


def test_decide_and_act_vwap_checks_circuit_breaker_before_odds_check(monkeypatch):
    state = _vwap_state(close=1975.0, vwap=2010.0, vwap_std=20.0, ema200=1900.0)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)

    def _fail_if_called(*a, **k):
        raise AssertionError("historical_odds_for_setup_cached ne doit pas etre appele "
                              "quand le coupe-circuit est deja declenche")
    monkeypatch.setattr(bot.odds_check, "historical_odds_for_setup_cached", _fail_if_called)

    from datetime import datetime, timezone
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)  # reference de depart du jour
    result = bot.decide_and_act_vwap(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=8900, open_positions=[], circuit_breaker=cb,  # -11%, coupe-circuit declenche
        historical_candles=[{"dummy": True}],
    )
    assert result == {"action": "aucune", "reason": "coupe-circuit journalier déclenché"}


def test_decide_and_act_vwap_skips_odds_check_when_no_historical_candles(monkeypatch):
    # historical_candles=None (comportement par defaut) -> la verification
    # est sautee entierement, jamais un blocage par defaut faute de
    # donnees (coherent avec le reste de decide_and_act_vwap, qui ne
    # bloque jamais sur l'absence d'une donnee optionnelle).
    state = _vwap_state(close=1975.0, vwap=2010.0, vwap_std=20.0, ema200=1900.0)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)

    def _fail_if_called(*a, **k):
        raise AssertionError("historical_odds_for_setup ne doit pas être appelé sans historical_candles")
    monkeypatch.setattr(bot.odds_check, "historical_odds_for_setup", _fail_if_called)

    result = bot.decide_and_act_vwap(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"


def test_decide_and_act_vwap_uses_xagusd_params_when_symbol_is_xagusd(monkeypatch):
    # Meme etat que le test or ci-dessus (ENTRY_SIGMA=1.5 identique pour
    # les deux symboles, donc meme condition d'entree declenchee) mais
    # symbol="XAGUSD" -- STOP_SIGMA differe (2.0 au lieu de 2.5), le
    # stop-loss calcule doit donc differer de celui de l'or sur les
    # memes donnees, preuve que le bon jeu de parametres est utilise.
    state = _vwap_state(close=1975.0, vwap=2010.0, vwap_std=20.0, ema200=1900.0)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_vwap(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
        symbol="XAGUSD",
    )
    assert result["action"] == "simulation"
    assert len(result["steps"]) == 2
    partial, runner = result["steps"]
    stop_loss = 2010.0 - 2.0 * 20.0  # 1970.0 -- different du stop or (1960.0)
    distance = 1975.0 - stop_loss  # 5.0

    assert partial["stop_loss"] == pytest.approx(stop_loss)
    assert partial["take_profit"] == pytest.approx(1975.0 + 2.0 * distance)
    assert partial["volume"] == pytest.approx(0.7)
    assert runner["stop_loss"] == pytest.approx(stop_loss)
    assert runner["take_profit"] is None
    assert runner["volume"] == pytest.approx(0.3)


def test_decide_and_act_vwap_opens_vente_with_partial_and_runner_legs(monkeypatch):
    # regime baissier (close < ema200) et close >= vwap + 1.5*std (2010 + 1.5*20 = 2040)
    state = _vwap_state(close=2045.0, vwap=2010.0, vwap_std=20.0, ema200=2100.0)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_vwap(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    assert len(result["steps"]) == 2
    partial, runner = result["steps"]
    stop_loss = 2010.0 + 2.5 * 20.0  # 2060.0
    distance = stop_loss - 2045.0  # 15.0

    assert partial["direction"] == "vente"
    assert partial["stop_loss"] == pytest.approx(stop_loss)
    assert partial["take_profit"] == pytest.approx(2045.0 - 2.0 * distance)
    assert runner["direction"] == "vente"
    assert runner["take_profit"] is None


def test_decide_and_act_vwap_opens_single_leg_when_partial_too_small(monkeypatch):
    # min_volume eleve -> la jambe partielle (0.7 x le volume total) tombe
    # sous le minimum broker, repli sur une seule jambe runner sans TP.
    state = _vwap_state(close=1975.0, vwap=2010.0, vwap_std=20.0, ema200=1900.0)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_vwap(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.3, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    assert len(result["steps"]) == 1
    step = result["steps"][0]
    assert step["take_profit"] is None
    assert step["volume"] == pytest.approx(0.33)


def test_decide_and_act_vwap_no_action_when_no_entry_signal(monkeypatch):
    # ni condition haussiere ni baissiere reunie
    state = _vwap_state(close=2010.0, vwap=2010.0, vwap_std=2.0, ema200=1995.0)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_vwap(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "pas de signal d'entree"}


def test_decide_and_act_vwap_no_action_before_min_bars_into_session(monkeypatch):
    state = _vwap_state(close=1985.0, vwap=2010.0, vwap_std=10.0, ema200=1900.0, bars_into_session=3)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_vwap(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "debut de session, VWAP pas encore stabilisee"}


def test_decide_and_act_vwap_no_action_after_session_end_hour(monkeypatch):
    state = _vwap_state(close=1985.0, vwap=2010.0, vwap_std=10.0, ema200=1900.0, hour_utc=22)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_vwap(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "trop tard dans la session pour ouvrir"}


def test_decide_and_act_vwap_no_action_when_std_zero(monkeypatch):
    state = _vwap_state(close=2005.0, vwap=2010.0, vwap_std=0.0, ema200=1995.0)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_vwap(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
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
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "distance entree-stop trop faible (ecart-type degenere)"}


def test_decide_and_act_vwap_holds_open_position_when_no_trailing_update(monkeypatch):
    state = _vwap_state(hour_utc=10)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    monkeypatch.setattr(bot.vwap_reversion, "compute_trailing_stop", lambda *a, **k: None)
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "openPrice": 2000.0,
                 "stopLoss": 1990.0, "time": "2026-09-29T10:00:00.000Z"}]
    result = bot.decide_and_act_vwap(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "position ouverte, pas de mise a jour du stop suiveur"}


def test_decide_and_act_vwap_modifies_stop_when_trailing_stop_updates_achat(monkeypatch):
    state = _vwap_state(hour_utc=10)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    captured = {}

    def fake_trailing_stop(candles, direction, entry_time_iso, entry_price, current_stop_loss, symbol="XAUUSD"):
        captured.update(direction=direction, entry_time_iso=entry_time_iso, entry_price=entry_price,
                         current_stop_loss=current_stop_loss, symbol=symbol)
        return 1998.5

    monkeypatch.setattr(bot.vwap_reversion, "compute_trailing_stop", fake_trailing_stop)
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "openPrice": 2000.0,
                 "stopLoss": 1990.0, "time": "2026-09-29T10:00:00.000Z"}]
    result = bot.decide_and_act_vwap(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker(),
    )
    assert captured == {"direction": "achat", "entry_time_iso": "2026-09-29T10:00:00.000Z",
                         "entry_price": 2000.0, "current_stop_loss": 1990.0, "symbol": "XAUUSD"}
    assert result == {"action": "simulation", "steps": [
        {"type": "modification_simulee", "position_id": "1", "symbol": "XAUUSD", "new_stop_loss": 1998.5}
    ]}


def test_decide_and_act_vwap_modifies_stop_when_trailing_stop_updates_vente(monkeypatch):
    state = _vwap_state(hour_utc=10)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    captured = {}

    def fake_trailing_stop(candles, direction, entry_time_iso, entry_price, current_stop_loss, symbol="XAUUSD"):
        captured["direction"] = direction
        return 2011.5

    monkeypatch.setattr(bot.vwap_reversion, "compute_trailing_stop", fake_trailing_stop)
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_SELL", "openPrice": 2010.0,
                 "stopLoss": 2020.0, "time": "2026-09-29T10:00:00.000Z"}]
    result = bot.decide_and_act_vwap(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker(),
    )
    assert captured["direction"] == "vente"
    assert result == {"action": "simulation", "steps": [
        {"type": "modification_simulee", "position_id": "1", "symbol": "XAUUSD", "new_stop_loss": 2011.5}
    ]}


def test_decide_and_act_vwap_closes_at_session_end_before_checking_trailing_stop(monkeypatch):
    state = _vwap_state(hour_utc=22)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    monkeypatch.setattr(
        bot.vwap_reversion, "compute_trailing_stop",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas être appelé après l'heure de fin de session")),
    )
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "openPrice": 2000.0,
                 "stopLoss": 1990.0, "time": "2026-09-29T10:00:00.000Z"}]
    result = bot.decide_and_act_vwap(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "simulation", "steps": [{"type": "clôture_simulee", "position_id": "1", "symbol": "XAUUSD"}]}


def test_decide_and_act_vwap_refuses_when_position_fields_missing(monkeypatch):
    state = _vwap_state(hour_utc=10)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY"}]  # pas de openPrice/stopLoss/time
    result = bot.decide_and_act_vwap(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "champs de position manquants, aucune action par prudence"}


def test_decide_and_act_vwap_refuses_on_unexpected_position_type(monkeypatch):
    state = _vwap_state()
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_UNKNOWN"}]
    result = bot.decide_and_act_vwap(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
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
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "plusieurs positions ouvertes sur ce symbole, aucune action par prudence"}


def test_decide_and_act_vwap_blocked_by_circuit_breaker(monkeypatch):
    from datetime import datetime, timezone
    state = _vwap_state(close=1975.0, vwap=2010.0, vwap_std=20.0, ema200=1900.0)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)  # référence de départ du jour (equity=10000)
    result = bot.decide_and_act_vwap(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=8900, open_positions=[], circuit_breaker=cb,
    )
    assert result == {"action": "aucune", "reason": "coupe-circuit journalier déclenché"}


def test_decide_and_act_vwap_no_action_when_size_below_minimum(monkeypatch):
    state = _vwap_state(close=1975.0, vwap=2010.0, vwap_std=20.0, ema200=1900.0)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_vwap(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=1000.0, max_volume=5000.0,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "compte trop petit pour ce stop (volume sous le minimum du broker)"}


def _fib_state(close=2000.0, high=2001.0, low=1999.0, level=1990.0, bias="achat",
               atr=5.0, trend_strength=0.0, trend_strength_short=0.0, hour_utc=10):
    return {"close": close, "high": high, "low": low, "level": level, "bias": bias,
            "atr": atr, "trend_strength": trend_strength, "trend_strength_short": trend_strength_short,
            "hour_utc": hour_utc}


def test_decide_and_act_fibonacci_no_action_when_insufficient_history(monkeypatch):
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: None)
    result = bot.decide_and_act_fibonacci(
        [], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "historique insuffisant pour l'ATR"}


def test_decide_and_act_fibonacci_closes_open_position_during_news_blackout(monkeypatch):
    state = _fib_state()
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "takeProfit": None,
                 "openPrice": 2000.0, "stopLoss": 1990.0, "time": "2026-09-11T10:00:00+00:00"}]
    result = bot.decide_and_act_fibonacci(
        _blackout_candles(), contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=existing, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "simulation", "steps": [
        {"type": "clôture_simulee", "position_id": "1", "symbol": "XAUUSD"},
    ]}


def test_decide_and_act_fibonacci_no_action_during_news_blackout_when_no_position(monkeypatch):
    state = _fib_state()
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        _blackout_candles(), contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "publication macro imminente ou en cours, aucune position ouverte à clôturer"}


def test_decide_and_act_fibonacci_no_action_during_volatility_shock(monkeypatch):
    # signal d'entree valide (memes chiffres que
    # test_decide_and_act_fibonacci_opens_achat_with_partial_and_runner_legs)
    # SANS le choc, pour isoler le garde.
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    monkeypatch.setattr(bot.confluence, "is_volatility_shock", lambda candles: True)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "volatilite anormale (actualite probable), signal ignore par prudence"}


def test_decide_and_act_fibonacci_blocked_by_ai_when_still_agitated_after_blackout(monkeypatch):
    # Reproduction DIRECTE de l'incident reel du 2026-11-06 : NFP a
    # 13h30 UTC, fenetre fixe (15min) finit a 13h45 UTC, candle a 13h55
    # UTC (comme le vrai trade perdant) -- 10 minutes dans la fenetre
    # grise (60min). Avec l'IA qui juge "encore_agite", ce trade doit
    # maintenant etre bloque, contrairement au comportement actuel.
    monkeypatch.setattr(bot.ai_news_watch, "judge_post_news_calm", lambda *a, **k: {"verdict": "encore_agite", "confiance": "haute", "raison": "test"})
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-11-06 13:55:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "prudence post-actualite (IA)"}


def test_decide_and_act_fibonacci_proceeds_when_ai_says_calm_after_blackout(monkeypatch):
    monkeypatch.setattr(bot.ai_news_watch, "judge_post_news_calm", lambda *a, **k: {"verdict": "calme", "confiance": "haute", "raison": "test"})
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-11-06 13:55:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"


def test_decide_and_act_fibonacci_proceeds_when_ai_news_watch_fails_open(monkeypatch):
    monkeypatch.setattr(bot.ai_news_watch, "judge_post_news_calm", lambda *a, **k: None)
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-11-06 13:55:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"


def test_decide_and_act_fibonacci_does_not_call_ai_news_watch_outside_grace_window(monkeypatch):
    # Garde-fou de cout : hors de toute fenetre grise, judge_post_news_calm
    # ne doit JAMAIS etre appelee -- une bougie datee loin de tout
    # evenement connu (ex. un dimanche calme) suffit a le prouver.
    monkeypatch.setattr(
        bot.ai_news_watch, "judge_post_news_calm",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("judge_post_news_calm ne doit pas etre appele hors fenetre grise")))
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-09-13 12:30:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"


def test_decide_and_act_fibonacci_closes_both_legs_at_session_end(monkeypatch):
    state = _fib_state(hour_utc=bot.fibonacci_reversion.SESSION_END_HOUR_UTC)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    open_positions = [
        {"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "takeProfit": None},
        {"id": "2", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "takeProfit": 2050.0},
    ]
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=open_positions, circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    assert {s["position_id"] for s in result["steps"]} == {"1", "2"}
    assert all(s["type"] == "clôture_simulee" for s in result["steps"])


def test_decide_and_act_fibonacci_no_action_when_only_partial_leg_open(monkeypatch):
    state = _fib_state()
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    open_positions = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "takeProfit": 2050.0}]
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=open_positions, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "jambe partielle seule ouverte, rien a gerer"}


def test_decide_and_act_fibonacci_refuses_when_multiple_runner_positions_open(monkeypatch):
    state = _fib_state()
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    open_positions = [
        {"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "takeProfit": None},
        {"id": "2", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "takeProfit": None},
    ]
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=open_positions, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "plusieurs positions ouvertes sur ce symbole, aucune action par prudence"}


def test_decide_and_act_fibonacci_refuses_on_unexpected_position_type(monkeypatch):
    state = _fib_state()
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    open_positions = [{"id": "1", "symbol": "XAUUSD", "type": "SOMETHING_ELSE", "takeProfit": None}]
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=open_positions, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "position de type inattendu, aucune action par prudence"}


def test_decide_and_act_fibonacci_refuses_when_position_fields_missing(monkeypatch):
    state = _fib_state()
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    open_positions = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "takeProfit": None}]  # pas de openPrice/stopLoss/time
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=open_positions, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "champs de position manquants, aucune action par prudence"}


def test_decide_and_act_fibonacci_holds_when_no_trailing_update(monkeypatch):
    state = _fib_state()
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    monkeypatch.setattr(bot.fibonacci_reversion, "compute_trailing_stop", lambda *a, **k: None)
    open_positions = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "takeProfit": None,
                        "openPrice": 2000.0, "stopLoss": 1990.0, "time": "2026-09-01T10:00:00+00:00"}]
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=open_positions, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "position ouverte, pas de mise a jour du stop suiveur"}


def test_decide_and_act_fibonacci_modifies_stop_when_trailing_stop_updates(monkeypatch):
    state = _fib_state()
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    monkeypatch.setattr(bot.fibonacci_reversion, "compute_trailing_stop", lambda *a, **k: 1995.0)
    open_positions = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "takeProfit": None,
                        "openPrice": 2000.0, "stopLoss": 1990.0, "time": "2026-09-01T10:00:00+00:00"}]
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=open_positions, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "simulation", "steps": [
        {"type": "modification_simulee", "position_id": "1", "symbol": "XAUUSD", "new_stop_loss": 1995.0},
    ]}


def test_decide_and_act_fibonacci_no_action_after_session_end_hour(monkeypatch):
    state = _fib_state(hour_utc=bot.fibonacci_reversion.SESSION_END_HOUR_UTC)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "trop tard dans la session pour ouvrir"}


def test_decide_and_act_fibonacci_no_action_when_no_level(monkeypatch):
    state = _fib_state(level=None, bias=None)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "aucun niveau de retracement disponible"}


def test_decide_and_act_fibonacci_no_action_when_atr_not_positive(monkeypatch):
    state = _fib_state(atr=0.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "atr indisponible"}


def test_decide_and_act_fibonacci_no_action_when_trend_strength_unmeasurable(monkeypatch):
    state = _fib_state(trend_strength=None)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "historique insuffisant pour mesurer la tendance"}


def test_decide_and_act_fibonacci_filtered_by_strong_trend(monkeypatch):
    # memes chiffres entree que le test d'ouverture plus bas, mais force
    # de tendance au-dela du seuil -- signal filtre avant toute
    # evaluation de touche/rejet.
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat",
                        atr=20.0, trend_strength=0.06)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "tendance trop forte, signal filtre"}


def test_decide_and_act_fibonacci_filtered_by_strong_short_term_trend_even_when_long_term_is_weak(monkeypatch):
    # Verrouille le correctif du 2026-11-06 (marche en V, incident reel
    # du 06/11 -- voir docs/superpowers/research/
    # gold_bot_fib_trend_filter_vshape_fix/README.md) : tendance LONGUE
    # (20j) sous son seuil (0.0 <= 5%, aurait passe l'ancien filtre),
    # mais tendance COURTE (5j) au-dela de son seuil propre (9% > 8%) --
    # doit bloquer quand meme (OR, pas juste le filtre 20j seul).
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat",
                        atr=20.0, trend_strength=0.0, trend_strength_short=0.09)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "tendance trop forte, signal filtre"}


def test_decide_and_act_fibonacci_filtered_by_negative_short_term_trend(monkeypatch):
    # Meme filtre OR, mais via abs() cote negatif (krach sur 5j plutot
    # qu'un rebond) -- releve en relecture finale : seul le cas positif
    # (+0.09) etait teste jusqu'ici.
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat",
                        atr=20.0, trend_strength=0.0, trend_strength_short=-0.09)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "tendance trop forte, signal filtre"}


def test_decide_and_act_fibonacci_not_filtered_at_exact_short_term_threshold(monkeypatch):
    # Limite exacte : inegalite stricte (">"), donc pile au seuil (8%)
    # le signal doit encore passer -- releve en relecture finale.
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat",
                        atr=20.0, trend_strength=0.0, trend_strength_short=bot.fibonacci_reversion.TREND_FILTER_SHORT_THRESHOLD)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"


def test_decide_and_act_fibonacci_not_filtered_when_both_trend_windows_are_weak(monkeypatch):
    # Non-regression : les deux fenetres sous seuil -> le signal passe
    # toujours normalement (meme chiffres d'entree que le test
    # d'ouverture plus bas, pour confirmer qu'il reste bien "simulation").
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat",
                        atr=20.0, trend_strength=0.0, trend_strength_short=0.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"


def test_decide_and_act_fibonacci_no_action_when_level_not_touched(monkeypatch):
    # touched (achat) = low <= level + tol ; tol = 0.3*20 = 6, level+tol =
    # 1986 -- low (1987) depasse ce seuil, donc pas de touche, meme si
    # close (1990) est bien au-dessus du niveau (rejet qui n'a pas de
    # sens a verifier puisque touched est deja faux). Bougie valide :
    # low(1987) <= close(1990) <= high(1992).
    state = _fib_state(close=1990.0, high=1992.0, low=1987.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "pas de signal d'entree"}


def test_decide_and_act_fibonacci_opens_achat_with_partial_and_runner_legs(monkeypatch):
    # biais achat, low (1974) touche le niveau 1980 avec tolerance
    # 0.3*20=6 (1980-6=1974, 1974<=1974 -> touche), close (1981) > niveau
    # (1980) -> rejet haussier confirme.
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    assert len(result["steps"]) == 2
    partial, runner = result["steps"]
    stop_loss = 1980.0 - 0.5 * 20.0  # 1970.0
    distance = 1981.0 - stop_loss  # 11.0

    assert partial["type"] == "ouverture_simulee"
    assert partial["direction"] == "achat"
    assert partial["entry"] == 1981.0
    assert partial["stop_loss"] == pytest.approx(stop_loss)
    assert partial["take_profit"] == pytest.approx(1981.0 + 1.5 * distance)

    assert runner["type"] == "ouverture_simulee"
    assert runner["direction"] == "achat"
    assert runner["stop_loss"] == pytest.approx(stop_loss)
    assert runner["take_profit"] is None


def test_decide_and_act_fibonacci_opens_vente_with_partial_and_runner_legs(monkeypatch):
    # biais vente, symetrique du test achat ci-dessus : high (1986)
    # touche le niveau 1980 avec tolerance 6 (1980+6=1986, 1986>=1986 ->
    # touche), close (1979) < niveau (1980) -> rejet baissier confirme.
    state = _fib_state(close=1979.0, high=1986.0, low=1978.0, level=1980.0, bias="vente", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    partial, runner = result["steps"]
    stop_loss = 1980.0 + 0.5 * 20.0  # 1990.0
    distance = stop_loss - 1979.0  # 11.0
    assert partial["direction"] == "vente"
    assert partial["take_profit"] == pytest.approx(1979.0 - 1.5 * distance)
    assert runner["direction"] == "vente"
    assert runner["take_profit"] is None


def test_decide_and_act_fibonacci_no_action_when_distance_too_small(monkeypatch):
    # close (2000.001) doit rester STRICTEMENT au-dessus du niveau
    # (2000.0) pour que "rejected" soit vrai (comparaison stricte ">")
    # -- atr quasi nul rend le stop (level - 0.5*atr) quasiment confondu
    # avec le niveau, donc la distance entree-stop est minuscule, sous
    # MIN_DISTANCE_PCT*entry_price (0.002 * ~2000 = 4.0).
    state = _fib_state(close=2000.001, high=2000.002, low=1999.999, level=2000.0, bias="achat", atr=0.0001)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "distance entree-stop trop faible"}


def _fib_total_volume(result):
    return round(sum(step["volume"] for step in result["steps"]), 8)


def test_decide_and_act_fibonacci_does_not_call_ai_discretion_when_distance_too_small(monkeypatch):
    """C'est exactement ce scenario (signal persistant, jamais assez de
    distance pour ouvrir) qui a fait tourner le veto IA toutes les 1-2
    minutes pendant des heures le 2026-10-29, facturant 28 appels reels
    pour 0 trade reellement ouvert -- garde-fou desormais devant
    ai_discretion.judge_fibonacci_entry (voir ce chantier)."""
    calls = []
    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry", lambda *a, **k: calls.append(a) or None)
    state = _fib_state(close=2000.001, high=2000.002, low=1999.999, level=2000.0, bias="achat", atr=0.0001)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert calls == []


def test_decide_and_act_fibonacci_blocked_by_circuit_breaker(monkeypatch):
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    from datetime import datetime, timezone
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=8900, open_positions=[], circuit_breaker=cb,  # -11%, coupe-circuit declenche
    )
    assert result == {"action": "aucune", "reason": "coupe-circuit journalier déclenché"}


def test_decide_and_act_fibonacci_does_not_call_ai_discretion_when_circuit_breaker_blocked(monkeypatch):
    """Meme garde-fou de cout que le test ci-dessus pour la distance trop
    faible, mais pour l'autre garde gratuit qui precede l'appel IA
    payant : le coupe-circuit journalier. Si le coupe-circuit bloque,
    ai_discretion.judge_fibonacci_entry ne doit jamais etre appele."""
    calls = []
    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry", lambda *a, **k: calls.append(a) or None)
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    from datetime import datetime, timezone
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)
    bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=8900, open_positions=[], circuit_breaker=cb,  # -11%, coupe-circuit declenche
    )
    assert calls == []


def test_decide_and_act_fibonacci_no_action_when_size_below_minimum(monkeypatch):
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=10000.0, max_volume=50000.0,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "compte trop petit pour ce stop (volume sous le minimum du broker)"}


def test_decide_and_act_fibonacci_opens_single_leg_when_partial_too_small(monkeypatch):
    # atr=28 -> stop_loss = 1980 - 0.5*28 = 1966, distance = close(1981) -
    # stop(1966) = 15 -- choisi pour que le volume total tombe juste
    # au-dessus du minimum (0.3) mais sa fraction partielle (70%) tombe
    # EN DESSOUS : balance=10000, risk_pct par defaut 0.05 -> risque
    # 500$ ; raw_size = 500/(15*100) = 0.3333 -> arrondi 0.33 (>= 0.3,
    # ouvrable) ; jambe partielle 0.33*0.7=0.231 -> arrondie 0.23 (<
    # 0.3, PAS ouvrable) -- repli sur une seule jambe runner, meme
    # mecanique que test_decide_and_act_vwap_opens_single_leg_when_partial_too_small.
    # tol = 0.3*28 = 8.4, touched (low=1974 <= 1980+8.4=1988.4) vrai ;
    # rejected (close=1981 > level=1980) vrai.
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=28.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.3, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    assert len(result["steps"]) == 1
    assert result["steps"][0]["take_profit"] is None


import gold_bot.ichimoku_reversion as ichimoku_reversion


def _ichimoku_candles_with_signal():
    """Reutilise l'historique de test de test_ichimoku_reversion.py --
    78 bougies H1 construisant un croisement haussier confirme par le
    nuage sur la derniere bougie, converties en bougies 5min."""
    from tests.gold_bot.test_ichimoku_reversion import _build_bullish_cross_history, _h1_to_5min
    return _h1_to_5min(_build_bullish_cross_history())


def _default_circuit_breaker():
    import shared_risk
    return shared_risk.CircuitBreaker(threshold_pct=0.10)


def test_decide_and_act_ichimoku_opens_two_legs_on_entry_signal():
    # Brief d'origine utilisait balance=10000 (comme les autres tests
    # ichimoku de ce fichier) : avec risk_pct par defaut (0.02) et la
    # distance entree-stop de ce fixture (~11.02, kijun tire loin du
    # close par la poussee des 10 dernieres bougies -- voir
    # _build_bullish_cross_history), risk_amount=200 ne depasse jamais
    # 0.01 lot (raw_size ~0.0036) : round_to_volume_step renvoie None et
    # la fonction s'arrete a "compte trop petit pour ce stop" avant meme
    # d'atteindre la scission partiel/runner -- verifie en executant le
    # test tel qu'ecrit dans le brief, qui echoue reellement avec
    # l'implementation de decide_and_act_ichimoku telle que specifiee
    # (AssertionError: 'aucune' == 'simulation'). balance=200000 est le
    # plus petit palier qui laisse 0.07 lot a scinder sans toucher au
    # risk_pct par defaut ni au contract_size reel (5000, XAGUSD) --
    # deviation signalee au controleur dans task-2-report.md plutot
    # qu'appliquee silencieusement.
    #
    # Valeurs attendues (verifiees via gold_bot.risk/gold_bot.ichimoku_reversion
    # en script jetable, memes fonctions que la production) :
    # entry_price=120.0 (state["close"]), stop_loss=108.97659904336004
    # (kijun - 0.5*atr), distance=11.02340095663996,
    # target=142.04680191327992 (entry + TARGET_R(2.0)*distance).
    # raw_size=0.07257288409872445 -> total_size=0.07 (floor au pas 0.01).
    # partial_volume = floor(0.7*0.07, pas 0.01) = 0.04 (0.049 -> 0.04).
    # runner_volume = reste arithmetique = 0.07 - 0.04 = 0.03 (PAS un
    # second appel a round_to_volume_step -- voir le correctif de
    # scission ratifie dans progress.md).
    candles = _ichimoku_candles_with_signal()
    result = bot.decide_and_act_ichimoku(
        candles, contract_size=5000.0, balance=200000.0, equity=200000.0,
        volume_step=0.01, min_volume=0.01, max_volume=20.0,
        open_positions=[], circuit_breaker=_default_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    assert len(result["steps"]) == 2
    partial, runner = result["steps"]
    entry_price = 120.0
    stop_loss = 108.97659904336004
    target = 142.04680191327992

    assert partial["type"] == "ouverture_simulee"
    assert partial["direction"] == "achat"
    assert partial["entry"] == pytest.approx(entry_price)
    assert partial["stop_loss"] == pytest.approx(stop_loss)
    assert partial["take_profit"] == pytest.approx(target)
    assert partial["volume"] == pytest.approx(0.04)

    assert runner["type"] == "ouverture_simulee"
    assert runner["direction"] == "achat"
    assert runner["entry"] == pytest.approx(entry_price)
    assert runner["stop_loss"] == pytest.approx(stop_loss)
    assert runner["take_profit"] is None
    assert runner["volume"] == pytest.approx(0.03)


def test_decide_and_act_ichimoku_falls_back_to_single_leg_when_split_rounds_to_zero():
    # balance=35000 : total_size arrondi au pas (0.01, le minimum
    # broker) ; 70%/30% de 0.01 arrondissent chacun independamment sous
    # le minimum (0.007/0.003 < 0.01), ce qui rejetterait a tort tout le
    # trade si les deux jambes etaient arrondies independamment --
    # verifie via gold_bot.risk.round_to_volume_step en console avant
    # d'ecrire ce test. Le comportement attendu (meme patron que
    # decide_and_act_fibonacci/decide_and_act_vwap) est un repli sur une
    # seule jambe runner pleine taille, jamais un abandon du trade.
    candles = _ichimoku_candles_with_signal()
    result = bot.decide_and_act_ichimoku(
        candles, contract_size=5000.0, balance=35000.0, equity=35000.0,
        volume_step=0.01, min_volume=0.01, max_volume=20.0,
        open_positions=[], circuit_breaker=_default_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    assert len(result["steps"]) == 1
    assert result["steps"][0]["volume"] == 0.01
    assert result["steps"][0]["take_profit"] is None


def test_decide_and_act_ichimoku_returns_aucune_when_no_signal():
    candles = _ichimoku_candles_with_signal()
    flat = dict(candles[-1])
    flat["high"] = flat["close"] + 0.1
    flat["low"] = flat["close"] - 0.1
    candles = candles[:-1] + [flat]
    result = bot.decide_and_act_ichimoku(
        candles, contract_size=5000.0, balance=10000.0, equity=10000.0,
        volume_step=0.01, min_volume=0.01, max_volume=20.0,
        open_positions=[], circuit_breaker=_default_circuit_breaker(),
    )
    assert result["action"] == "aucune"


def test_decide_and_act_ichimoku_returns_aucune_when_history_insufficient():
    candles = _ichimoku_candles_with_signal()[:50]
    result = bot.decide_and_act_ichimoku(
        candles, contract_size=5000.0, balance=10000.0, equity=10000.0,
        volume_step=0.01, min_volume=0.01, max_volume=20.0,
        open_positions=[], circuit_breaker=_default_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "historique insuffisant pour l'ichimoku"}


def test_decide_and_act_ichimoku_rejects_when_circuit_breaker_tripped():
    candles = _ichimoku_candles_with_signal()
    cb = _default_circuit_breaker()
    cb.check(10000.0)
    cb.check(8900.0)  # perte > 10% du solde de depart -> coupe-circuit declenche
    result = bot.decide_and_act_ichimoku(
        candles, contract_size=5000.0, balance=8900.0, equity=8900.0,
        volume_step=0.01, min_volume=0.01, max_volume=20.0,
        open_positions=[], circuit_breaker=cb,
    )
    assert result == {"action": "aucune", "reason": "coupe-circuit journalier déclenché"}


def test_decide_and_act_ichimoku_closes_runner_leg_at_session_end():
    candles = _ichimoku_candles_with_signal()
    last = dict(candles[-1])
    from datetime import timedelta
    t = ichimoku_reversion._parse(last["time"])
    hour = t.hour
    delta_hours = (ichimoku_reversion.SESSION_END_HOUR_UTC - hour) % 24
    # :55 (pas :00) -- meme convention que _h1_to_5min, sinon
    # resample_h1() exclurait cette derniere bougie comme "heure encore
    # en formation" (voir son garde-fou de causalite).
    last["time"] = (t + timedelta(hours=delta_hours)).strftime("%Y-%m-%d %H:55:00")
    candles = candles[:-1] + [last]
    position = {"id": "1", "symbol": "XAGUSD", "type": "POSITION_TYPE_BUY", "openPrice": 100.0, "stopLoss": 95.0, "time": candles[0]["time"]}
    result = bot.decide_and_act_ichimoku(
        candles, contract_size=5000.0, balance=10000.0, equity=10000.0,
        volume_step=0.01, min_volume=0.01, max_volume=20.0,
        open_positions=[position], circuit_breaker=_default_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    assert result["steps"][0]["type"] == "clôture_simulee"


def test_decide_and_act_ichimoku_does_nothing_when_only_partial_leg_open():
    candles = _ichimoku_candles_with_signal()
    position = {"id": "1", "symbol": "XAGUSD", "type": "POSITION_TYPE_BUY", "openPrice": 100.0, "stopLoss": 95.0, "takeProfit": 110.0, "time": candles[0]["time"]}
    result = bot.decide_and_act_ichimoku(
        candles, contract_size=5000.0, balance=10000.0, equity=10000.0,
        volume_step=0.01, min_volume=0.01, max_volume=20.0,
        open_positions=[position], circuit_breaker=_default_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "jambe partielle seule ouverte, rien a gerer"}


def test_decide_and_act_ichimoku_blocks_trade_when_ai_opposes(monkeypatch):
    # balance=200000 (pas 10000 comme dans le brief d'origine) : avec
    # risk_pct par defaut (0.02) et la distance entree-stop de ce
    # fixture (~11.02), balance=10000 fait tomber total_size sous le
    # minimum broker (round_to_volume_step renvoie None) -- la fonction
    # s'arrete alors a "compte trop petit pour ce stop" AVANT d'atteindre
    # le veto IA, quel que soit le verdict mocke. Meme desync deja
    # rencontree et ratifiee en Task 2 (voir
    # test_decide_and_act_ichimoku_opens_two_legs_on_entry_signal
    # ci-dessus) -- deviation signalee au controleur dans
    # task-3-report.md plutot qu'appliquee silencieusement.
    monkeypatch.setattr(bot.ai_shadow, "shadow_judge_entry", lambda *a, **k: {"verdict": "opposerait", "confiance": "haute", "raison": "test"})
    candles = _ichimoku_candles_with_signal()
    result = bot.decide_and_act_ichimoku(
        candles, contract_size=5000.0, balance=200000.0, equity=200000.0,
        volume_step=0.01, min_volume=0.01, max_volume=20.0,
        open_positions=[], circuit_breaker=_default_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "veto IA (opposition)"}


def test_decide_and_act_ichimoku_proceeds_when_ai_confirms(monkeypatch):
    # balance=200000 : voir le commentaire de
    # test_decide_and_act_ichimoku_blocks_trade_when_ai_opposes ci-dessus.
    monkeypatch.setattr(bot.ai_shadow, "shadow_judge_entry", lambda *a, **k: {"verdict": "confirme", "confiance": "haute", "raison": "test"})
    candles = _ichimoku_candles_with_signal()
    result = bot.decide_and_act_ichimoku(
        candles, contract_size=5000.0, balance=200000.0, equity=200000.0,
        volume_step=0.01, min_volume=0.01, max_volume=20.0,
        open_positions=[], circuit_breaker=_default_circuit_breaker(),
    )
    assert result["action"] == "simulation"


def test_decide_and_act_ichimoku_halves_size_when_ai_confidence_is_low(monkeypatch):
    # balance=200000 : voir le commentaire de
    # test_decide_and_act_ichimoku_blocks_trade_when_ai_opposes ci-dessus.
    # Meme correctif (incident du 2026-11-10) que decide_and_act_fibonacci.
    monkeypatch.setattr(bot.ai_shadow, "shadow_judge_entry", lambda *a, **k: {"verdict": "confirme", "confiance": "haute", "raison": "test"})
    baseline = bot.decide_and_act_ichimoku(
        _ichimoku_candles_with_signal(), contract_size=5000.0, balance=200000.0, equity=200000.0,
        volume_step=0.01, min_volume=0.01, max_volume=20.0,
        open_positions=[], circuit_breaker=_default_circuit_breaker(),
    )
    baseline_volume = round(sum(step["volume"] for step in baseline["steps"]), 8)

    monkeypatch.setattr(bot.ai_shadow, "shadow_judge_entry", lambda *a, **k: {"verdict": "confirme", "confiance": "faible", "raison": "test"})
    reduced = bot.decide_and_act_ichimoku(
        _ichimoku_candles_with_signal(), contract_size=5000.0, balance=200000.0, equity=200000.0,
        volume_step=0.01, min_volume=0.01, max_volume=20.0,
        open_positions=[], circuit_breaker=_default_circuit_breaker(),
    )
    reduced_volume = round(sum(step["volume"] for step in reduced["steps"]), 8)

    expected = bot.risk.round_to_volume_step(
        baseline_volume * bot.ai_shadow.LOW_CONFIDENCE_SIZE_FACTOR, 0.01, 0.01, 20.0)
    assert reduced_volume == expected
    assert reduced_volume < baseline_volume


def test_decide_and_act_ichimoku_proceeds_when_ai_returns_none_fail_open(monkeypatch):
    # balance=200000 : voir le commentaire de
    # test_decide_and_act_ichimoku_blocks_trade_when_ai_opposes ci-dessus.
    monkeypatch.setattr(bot.ai_shadow, "shadow_judge_entry", lambda *a, **k: None)
    candles = _ichimoku_candles_with_signal()
    result = bot.decide_and_act_ichimoku(
        candles, contract_size=5000.0, balance=200000.0, equity=200000.0,
        volume_step=0.01, min_volume=0.01, max_volume=20.0,
        open_positions=[], circuit_breaker=_default_circuit_breaker(),
    )
    assert result["action"] == "simulation"


def test_decide_and_act_ichimoku_calls_ai_shadow_with_real_decision_true(monkeypatch):
    # balance=200000 : voir le commentaire de
    # test_decide_and_act_ichimoku_blocks_trade_when_ai_opposes ci-dessus.
    calls = []
    monkeypatch.setattr(bot.ai_shadow, "shadow_judge_entry", lambda *a, **k: calls.append(k) or {"verdict": "confirme", "confiance": "haute", "raison": "test"})
    candles = _ichimoku_candles_with_signal()
    bot.decide_and_act_ichimoku(
        candles, contract_size=5000.0, balance=200000.0, equity=200000.0,
        volume_step=0.01, min_volume=0.01, max_volume=20.0,
        open_positions=[], circuit_breaker=_default_circuit_breaker(),
    )
    assert calls[0]["real_decision"] is True
    assert calls[0]["engine_label"] == "croisement Tenkan/Kijun confirme par le nuage (Ichimoku)"


def test_decide_and_act_fibonacci_calls_ai_discretion_only_when_a_real_trade_would_open(monkeypatch):
    calls = []
    def _spy(*a, **k):
        calls.append(k)
        return {"action": "achat", "confiance": "haute", "stop_loss_distance": 11.0,
                "take_profit_distance": 16.5, "raison": "x"}
    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry", _spy)
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    assert len(calls) == 1


def test_decide_and_act_fibonacci_follows_ai_chosen_direction_even_when_opposite_of_rule(monkeypatch):
    # La regle (bias="achat") proposerait un achat -- l'IA choisit
    # "vente" a la place : c'est exactement l'autonomie de sens que ce
    # chantier ajoute (voir la spec, section "Autonomie du sens").
    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry",
                         lambda *a, **k: {"action": "vente", "confiance": "haute",
                                           "stop_loss_distance": 11.0, "take_profit_distance": 16.5, "raison": "x"})
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    partial, runner = result["steps"]
    assert partial["direction"] == "vente"
    assert runner["direction"] == "vente"
    # stop_loss/take_profit calcules par rapport a entry_price=1981.0
    # avec la distance DE L'IA (11.0/16.5), pas celle de la regle.
    assert partial["stop_loss"] == pytest.approx(1981.0 + 11.0)
    assert partial["take_profit"] == pytest.approx(1981.0 - 16.5)


def test_decide_and_act_fibonacci_no_action_when_ai_discretion_returns_none(monkeypatch):
    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry", lambda *a, **k: None)
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "aucune decision IA (echec, rejet ou rien a faire)"}


def test_decide_and_act_fibonacci_no_action_when_ai_adjusted_stop_too_small(monkeypatch):
    # fibonacci_reversion.MIN_DISTANCE_PCT == 0.002 ; entry_price=1981.0
    # donc le plancher vaut 1981.0 * 0.002 = 3.962. La distance de la
    # regle (avant appel IA) passerait ce garde-fou (atr=20.0 -> distance
    # bien superieure), mais l'IA peut resserrer le stop jusqu'a 0.7x via
    # sl_multiplicateur (voir SL_MULTIPLIER_BOUNDS) -- ici on simule
    # directement un verdict IA deja resolu avec une distance finale de
    # 2.0, sous le plancher, pour verifier que le garde-fou s'applique
    # aussi APRES l'appel IA, pas seulement avant.
    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry",
                         lambda *a, **k: {"action": "achat", "confiance": "haute",
                                           "stop_loss_distance": 2.0, "take_profit_distance": 16.5, "raison": "x"})
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "distance entree-stop trop faible apres ajustement IA"}


def test_decide_and_act_fibonacci_applies_ai_tp_sl_distances_to_executed_legs(monkeypatch):
    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry",
                         lambda *a, **k: {"action": "achat", "confiance": "haute",
                                           "stop_loss_distance": 5.0, "take_profit_distance": 50.0, "raison": "x"})
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    partial, _ = result["steps"]
    assert partial["stop_loss"] == pytest.approx(1981.0 - 5.0)
    assert partial["take_profit"] == pytest.approx(1981.0 + 50.0)


def test_decide_and_act_fibonacci_halves_size_when_ai_confidence_is_low(monkeypatch):
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)

    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry",
                         lambda *a, **k: {"action": "achat", "confiance": "haute",
                                           "stop_loss_distance": 11.0, "take_profit_distance": 16.5, "raison": "x"})
    baseline = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    baseline_volume = _fib_total_volume(baseline)

    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry",
                         lambda *a, **k: {"action": "achat", "confiance": "faible",
                                           "stop_loss_distance": 11.0, "take_profit_distance": 16.5, "raison": "x"})
    reduced = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    reduced_volume = _fib_total_volume(reduced)

    expected = bot.risk.round_to_volume_step(
        baseline_volume * bot.ai_discretion.LOW_CONFIDENCE_SIZE_FACTOR, 0.01, 0.01, 500)
    assert reduced_volume == expected
    assert reduced_volume < baseline_volume


def test_decide_and_act_fibonacci_does_not_reduce_size_for_moyenne_confidence(monkeypatch):
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)

    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry",
                         lambda *a, **k: {"action": "achat", "confiance": "haute",
                                           "stop_loss_distance": 11.0, "take_profit_distance": 16.5, "raison": "x"})
    baseline = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )

    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry",
                         lambda *a, **k: {"action": "achat", "confiance": "moyenne",
                                           "stop_loss_distance": 11.0, "take_profit_distance": 16.5, "raison": "x"})
    moyenne = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert _fib_total_volume(moyenne) == _fib_total_volume(baseline)


def test_decide_and_act_fibonacci_returns_aucune_when_halved_size_below_minimum(monkeypatch):
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry",
                         lambda *a, **k: {"action": "achat", "confiance": "haute",
                                           "stop_loss_distance": 11.0, "take_profit_distance": 16.5, "raison": "x"})
    baseline = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    baseline_volume = _fib_total_volume(baseline)
    min_volume = round(baseline_volume * 0.75, 2)

    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry",
                         lambda *a, **k: {"action": "achat", "confiance": "faible",
                                           "stop_loss_distance": 11.0, "take_profit_distance": 16.5, "raison": "x"})
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=min_volume, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "taille reduite (confiance IA faible) sous le minimum du broker"}


def test_decide_and_act_fibonacci_passes_ftmo_buffer_computed_from_ftmo_guard_instance(monkeypatch):
    calls = []
    def _spy(*a, **k):
        calls.append(k)
        return {"action": "achat", "confiance": "haute", "stop_loss_distance": 11.0,
                "take_profit_distance": 16.5, "raison": "x"}
    monkeypatch.setattr(bot.ai_discretion, "judge_fibonacci_entry", _spy)
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles, historical_candles=None: state)
    guard = bot.ftmo_guard.FtmoGuard(isc=10000.0)
    guard.check(balance=10000.0, equity=10000.0)
    bot.decide_and_act_fibonacci(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=9800, open_positions=[], circuit_breaker=_open_circuit_breaker(),
        ftmo_guard_instance=guard,
    )
    assert calls[0]["ftmo_buffer"] == {"daily_pct_remaining": pytest.approx(0.02),
                                        "overall_pct_remaining": pytest.approx(0.07)}


def test_decide_and_act_ichimoku_still_uses_ai_shadow_unaffected_by_this_change(monkeypatch):
    """Non-regression explicite : ce chantier ne touche que
    decide_and_act_fibonacci. decide_and_act_ichimoku doit continuer a
    utiliser ai_shadow.shadow_judge_entry exactement comme avant."""
    calls = []
    monkeypatch.setattr(bot.ai_shadow, "shadow_judge_entry", lambda *a, **k: calls.append(k) or None)
    state = {"tenkan": 2650.0, "kijun": 2645.0, "senkou_a": 2640.0, "senkou_b": 2635.0,
              "close": 2651.0, "high": 2652.0, "low": 2649.0, "atr": 2.0, "hour_utc": 10}
    monkeypatch.setattr(bot.ichimoku_reversion, "latest_state", lambda candles: state)
    monkeypatch.setattr(bot.ichimoku_reversion, "signal", lambda s: "achat")
    bot.decide_and_act_ichimoku(
        [{"dummy": True, "time": "2026-01-05 12:00:00"}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(), symbol="XAGUSD",
    )
    assert len(calls) == 1
    assert calls[0]["real_decision"] is True
    assert calls[0]["engine_label"] == "croisement Tenkan/Kijun confirme par le nuage (Ichimoku)"
