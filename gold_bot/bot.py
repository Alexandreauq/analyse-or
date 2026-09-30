# gold_bot/bot.py
# Orchestration du bot : combine confluence + risque + broker selon les
# règles de renversement/empilement du spec. DANS CE PLAN (Plan A),
# decide_and_act() ne place et ne clôture JAMAIS de vraie position — il
# ne fait que journaliser ce qu'il ferait. Voir
# docs/superpowers/specs/2026-09-10-bot-trading-or-design.md.
from datetime import datetime, timezone

import gold_bot.broker as broker
import gold_bot.confluence as confluence
import gold_bot.macro_signal as macro_signal
import gold_bot.risk as risk
import gold_bot.vwap_reversion as vwap_reversion


def decide_and_act(candles: list[dict], *, contract_size: float, balance: float, equity: float,
                    volume_step: float, min_volume: float, max_volume: float,
                    open_positions: list[dict], circuit_breaker: "risk.CircuitBreaker",
                    symbol: str = "XAUUSD", risk_pct: float = 0.05) -> dict:
    """Cœur de la boucle : évalue le signal, applique les règles de
    renversement/empilement, et — dans ce plan, TOUJOURS en simulation —
    journalise ce qu'il ferait sans jamais appeler
    broker.place_market_order / broker.close_position. `open_positions`
    doit provenir d'un appel récent à reconcile_positions (jamais d'un
    état local qui pourrait être périmé). Ne prend délibérément pas de
    token/account_id : elle ne parle jamais elle-même au broker — le
    code de câblage réel (Plan B) appellera broker.place_market_order/
    close_position séparément, avec les champs déjà présents dans
    `steps` ci-dessous.

    `risk_pct` détermine la taille de la position ouverte, selon le
    profil de risque actif — mais decide_and_act() elle-même ne connaît
    pas la notion de "profil" : c'est l'appelant (gold_bot.loop) qui
    résout le profil courant en `risk_pct` via
    risk.risk_profile_params() avant d'appeler cette fonction. Défaut
    inchangé (0.05, le profil 3) pour ne casser aucun appelant existant
    qui ne fournit pas ce paramètre.

    `balance` (capital réalisé) sert uniquement au dimensionnement
    (risk.compute_position_size) — convention standard, indépendante du
    P&L flottant. `equity` (balance + P&L flottant des positions
    ouvertes) sert uniquement au coupe-circuit : une position ouverte en
    train de perdre doit pouvoir le déclencher avant sa clôture, pas
    seulement une fois la perte réalisée dans balance.

    `volume_step`/`min_volume`/`max_volume` viennent de
    broker.get_symbol_specification() — la taille calculée par le risque
    est arrondie au pas du broker avant toute décision (voir
    risk.round_to_volume_step). Si le résultat tombe sous `min_volume`
    (compte trop petit pour ce stop à ce niveau de risque), aucune
    action n'est prise — ni clôture ni ouverture, même en cas de
    renversement — plutôt que de clôturer une position existante sans
    pouvoir rouvrir dans le nouveau sens.

    Gère toutes les positions correspondant à `symbol`, pas seulement la
    première trouvée (un redémarrage/crash pourrait en laisser
    plusieurs) — une position au type non reconnu bloque toute action
    par prudence plutôt que d'être devinée.

    Publication macro à fort impact (CPI/Emploi US/FOMC) imminente ou en
    cours (voir confluence.is_news_blackout) : toute position ouverte
    sur `symbol` est clôturée immédiatement, même sans signal de
    retournement — le signal lui-même est de toute façon neutre à ce
    moment (compute_signal applique le même garde), donc sans ce
    traitement dédié la position resterait ouverte pendant la
    publication au lieu d'être fermée avant qu'elle n'ait lieu."""
    signal = confluence.compute_signal(candles)
    # Fixe la référence du jour (equity, pas balance — voir docstring)
    # dès le premier cycle, même sur un signal neutre — sinon la
    # référence ne serait fixée qu'au premier cycle *actionnable* du
    # jour, potentiellement bien après l'ouverture UTC et sur une equity
    # déjà dérivée.
    circuit_breaker.check(equity)

    matching = [p for p in open_positions if p.get("symbol") == symbol]

    def _direction(p):
        raw_type = p.get("type")
        if raw_type == "POSITION_TYPE_BUY":
            return "achat"
        if raw_type == "POSITION_TYPE_SELL":
            return "vente"
        return None

    news_blackout = False
    if candles:
        as_of = datetime.fromisoformat(candles[-1]["time"].replace(" ", "T")).replace(tzinfo=timezone.utc)
        news_blackout = confluence.is_news_blackout(as_of)

    if news_blackout:
        if not matching:
            return {"action": "aucune", "reason": "publication macro imminente, aucune position ouverte à clôturer"}
        if None in {_direction(p) for p in matching}:
            return {"action": "aucune", "reason": "type de position non reconnu, aucune action par prudence"}
        steps = [{"type": "clôture_simulee", "position_id": p.get("id"), "symbol": symbol} for p in matching]
        return {"action": "simulation", "steps": steps}

    if signal["status"] == "neutre":
        return {"action": "aucune", "reason": "signal neutre"}

    directions = {_direction(p) for p in matching}

    if None in directions:
        return {"action": "aucune", "reason": "type de position non reconnu, aucune action par prudence"}

    if signal["status"] in directions:
        return {"action": "aucune", "reason": "position déjà ouverte dans le même sens"}

    if not circuit_breaker.can_open_position(equity):
        return {"action": "aucune", "reason": "coupe-circuit journalier déclenché"}

    raw_size = risk.compute_position_size(balance, signal["entry"], signal["stop_loss"], contract_size, risk_pct=risk_pct)
    size = risk.round_to_volume_step(raw_size, volume_step, min_volume, max_volume)
    if size is None:
        return {"action": "aucune", "reason": "compte trop petit pour ce stop (volume sous le minimum du broker)"}

    steps = []
    for p in matching:
        steps.append({"type": "clôture_simulee", "position_id": p.get("id"), "symbol": symbol})

    steps.append({
        "type": "ouverture_simulee",
        "symbol": symbol,
        "direction": signal["status"],
        "volume": size,
        "entry": signal["entry"],
        "stop_loss": signal["stop_loss"],
        "take_profit": signal["take_profit"],
    })
    return {"action": "simulation", "steps": steps}


def decide_and_act_swing(macro_payload: dict, candles: list[dict], *, contract_size: float,
                          balance: float, equity: float, volume_step: float, min_volume: float,
                          max_volume: float, open_positions: list[dict],
                          circuit_breaker: "risk.CircuitBreaker", symbol: str = "XAUUSD",
                          risk_pct: float = 0.05, now: datetime | None = None) -> dict:
    """Contrepartie swing (position tenue plusieurs jours, longue
    uniquement) de decide_and_act() -- voir
    docs/superpowers/specs/2026-09-29-gold-bot-swing-macro-design.md.
    Déclenchée par le signal macro de gold_score.py (`macro_payload`,
    voir gold_bot.macro_signal), pas par confluence.compute_signal.

    Différences volontaires par rapport à decide_and_act (scalping) :
    pas de garde news_blackout (une position swing est conçue pour
    traverser la volatilité court terme d'une publication macro -- la
    condition d'entrée de gold_score.py exclut déjà l'ouverture d'une
    nouvelle position dans les heures précédant un FOMC, mais rien
    n'impose de clôturer une position déjà ouverte à chaque
    CPI/NFP/FOMC) ; pas de retournement long/court (signal long
    uniquement) ; sortie pilotée par macro_signal.should_exit
    (dégradation du signal macro), pas par un signal neutre/opposé."""
    now = now or datetime.now(timezone.utc)
    circuit_breaker.check(equity)

    matching = [p for p in open_positions if p.get("symbol") == symbol]

    if len(matching) > 1:
        return {"action": "aucune", "reason": "plusieurs positions ouvertes sur ce symbole, aucune action par prudence"}

    if matching:
        position = matching[0]
        if position.get("type") != "POSITION_TYPE_BUY":
            return {"action": "aucune", "reason": "position de type inattendu (pas un achat), aucune action par prudence"}
        exit_now, reason = macro_signal.should_exit(macro_payload, position.get("time"), now)
        if exit_now:
            return {"action": "simulation", "steps": [
                {"type": "clôture_simulee", "position_id": position.get("id"), "symbol": symbol}
            ]}
        return {"action": "aucune", "reason": f"position swing ouverte, aucune condition de sortie ({reason or 'rien à signaler'})"}

    if not macro_signal.has_entry_alert(macro_payload):
        return {"action": "aucune", "reason": "pas de signal d'entrée macro"}

    if not circuit_breaker.can_open_position(equity):
        return {"action": "aucune", "reason": "coupe-circuit journalier déclenché"}

    if not candles:
        return {"action": "aucune", "reason": "aucune bougie disponible pour le prix courant"}
    current_price = candles[-1]["close"]

    levels = macro_signal.compute_entry_levels(macro_payload, current_price)
    if levels is None:
        return {"action": "aucune", "reason": "niveaux d'entrée indisponibles (MM200 absente ou stop invalide)"}

    raw_size = risk.compute_position_size(balance, levels["entry"], levels["stop_loss"], contract_size, risk_pct=risk_pct)
    size = risk.round_to_volume_step(raw_size, volume_step, min_volume, max_volume)
    if size is None:
        return {"action": "aucune", "reason": "compte trop petit pour ce stop (volume sous le minimum du broker)"}

    return {"action": "simulation", "steps": [{
        "type": "ouverture_simulee", "symbol": symbol, "direction": "achat", "volume": size,
        "entry": levels["entry"], "stop_loss": levels["stop_loss"], "take_profit": levels["take_profit"],
    }]}


def decide_and_act_vwap(candles: list[dict], *, contract_size: float, balance: float, equity: float,
                         volume_step: float, min_volume: float, max_volume: float,
                         open_positions: list[dict], circuit_breaker: "risk.CircuitBreaker",
                         symbol: str = "XAUUSD", risk_pct: float = 0.05) -> dict:
    """Moteur scalping retour-à-la-VWAP + filtre EMA200 + stop suiveur
    EMA50 -- voir docs/superpowers/specs/2026-09-30-gold-bot-vwap-
    reversion-design.md et son addendum (stop suiveur + recalibrage
    entrée/stop, validé par découpage train 2018-2023/test 2024-2026).
    Bidirectionnel (achat et vente), contrairement au mode swing (long
    uniquement). Pas de take-profit fixe : la position n'est fermée que
    par le stop (initial, ou déplacé par le suiveur une fois le seuil de
    rentabilité atteint -- voir vwap_reversion.compute_trailing_stop) ou
    en fin de session (22h UTC)."""
    circuit_breaker.check(equity)

    state = vwap_reversion.latest_state(candles)
    if state is None:
        return {"action": "aucune", "reason": "historique insuffisant pour l'EMA200"}

    matching = [p for p in open_positions if p.get("symbol") == symbol]
    # Prise de profit partielle (voir docs/superpowers/specs/2026-09-30-
    # gold-bot-vwap-partial-tp-addendum.md) : une entree peut ouvrir DEUX
    # positions broker distinctes -- la jambe partielle (takeProfit posé,
    # se ferme seule côté broker) et la jambe "runner" (jamais de
    # takeProfit, seule gérée par le stop suiveur ci-dessous).
    runner_matching = [p for p in matching if not p.get("takeProfit")]
    partial_matching = [p for p in matching if p.get("takeProfit")]

    if len(runner_matching) > 1 or len(partial_matching) > 1:
        return {"action": "aucune", "reason": "plusieurs positions ouvertes sur ce symbole, aucune action par prudence"}

    if matching:
        if state["hour_utc"] >= vwap_reversion.SESSION_END_HOUR_UTC:
            steps = [{"type": "clôture_simulee", "position_id": p.get("id"), "symbol": symbol} for p in matching]
            return {"action": "simulation", "steps": steps}

        if not runner_matching:
            return {"action": "aucune", "reason": "jambe partielle seule ouverte, rien a gerer"}

        position = runner_matching[0]
        raw_type = position.get("type")
        if raw_type == "POSITION_TYPE_BUY":
            direction = "achat"
        elif raw_type == "POSITION_TYPE_SELL":
            direction = "vente"
        else:
            return {"action": "aucune", "reason": "position de type inattendu, aucune action par prudence"}

        entry_price = position.get("openPrice")
        current_stop_loss = position.get("stopLoss")
        entry_time_iso = position.get("time")
        if entry_price is None or current_stop_loss is None or entry_time_iso is None:
            return {"action": "aucune", "reason": "champs de position manquants, aucune action par prudence"}

        new_stop = vwap_reversion.compute_trailing_stop(candles, direction, entry_time_iso, entry_price, current_stop_loss)
        if new_stop is None:
            return {"action": "aucune", "reason": "position ouverte, pas de mise a jour du stop suiveur"}

        return {"action": "simulation", "steps": [{
            "type": "modification_simulee", "position_id": position.get("id"), "symbol": symbol,
            "new_stop_loss": new_stop,
        }]}

    if state["bars_into_session"] < vwap_reversion.MIN_BARS_INTO_SESSION:
        return {"action": "aucune", "reason": "debut de session, VWAP pas encore stabilisee"}
    if state["hour_utc"] >= vwap_reversion.SESSION_END_HOUR_UTC:
        return {"action": "aucune", "reason": "trop tard dans la session pour ouvrir"}
    if state["vwap_std"] <= 0:
        return {"action": "aucune", "reason": "ecart-type VWAP indisponible"}

    direction = None
    stop_loss = None
    if state["close"] > state["ema200"] and state["close"] <= state["vwap"] - vwap_reversion.ENTRY_SIGMA * state["vwap_std"]:
        direction = "achat"
        stop_loss = state["vwap"] - vwap_reversion.STOP_SIGMA * state["vwap_std"]
    elif state["close"] < state["ema200"] and state["close"] >= state["vwap"] + vwap_reversion.ENTRY_SIGMA * state["vwap_std"]:
        direction = "vente"
        stop_loss = state["vwap"] + vwap_reversion.STOP_SIGMA * state["vwap_std"]

    if direction is None:
        return {"action": "aucune", "reason": "pas de signal d'entree"}

    entry_price = state["close"]
    distance = abs(entry_price - stop_loss)
    if distance < entry_price * vwap_reversion.MIN_DISTANCE_PCT:
        return {"action": "aucune", "reason": "distance entree-stop trop faible (ecart-type degenere)"}

    if not circuit_breaker.can_open_position(equity):
        return {"action": "aucune", "reason": "coupe-circuit journalier déclenché"}

    raw_size = risk.compute_position_size(balance, entry_price, stop_loss, contract_size, risk_pct=risk_pct)
    total_size = risk.round_to_volume_step(raw_size, volume_step, min_volume, max_volume)
    if total_size is None:
        return {"action": "aucune", "reason": "compte trop petit pour ce stop (volume sous le minimum du broker)"}

    partial_volume = None
    if vwap_reversion.PARTIAL_TP_PCT > 0:
        partial_volume = risk.round_to_volume_step(
            total_size * vwap_reversion.PARTIAL_TP_PCT, volume_step, min_volume, max_volume)

    if partial_volume is None:
        # Compte trop petit pour scinder en deux jambes -- tout le volume
        # part en jambe runner (comportement d'avant cet ajout).
        return {"action": "simulation", "steps": [{
            "type": "ouverture_simulee", "symbol": symbol, "direction": direction, "volume": total_size,
            "entry": entry_price, "stop_loss": stop_loss, "take_profit": None,
        }]}

    runner_volume = round(total_size - partial_volume, 2)
    partial_target = (entry_price + vwap_reversion.PARTIAL_TP_R * distance if direction == "achat"
                       else entry_price - vwap_reversion.PARTIAL_TP_R * distance)

    steps = [{
        "type": "ouverture_simulee", "symbol": symbol, "direction": direction, "volume": partial_volume,
        "entry": entry_price, "stop_loss": stop_loss, "take_profit": partial_target,
    }]
    if runner_volume >= volume_step:
        steps.append({
            "type": "ouverture_simulee", "symbol": symbol, "direction": direction, "volume": runner_volume,
            "entry": entry_price, "stop_loss": stop_loss, "take_profit": None,
        })
    return {"action": "simulation", "steps": steps}


def reconcile_positions(token: str, account_id: str, region: str = broker.DEFAULT_MT5_REGION) -> list[dict]:
    """Interroge MetaApi pour l'état réel des positions avant toute
    décision — jamais de confiance aveugle en un état local qui
    pourrait être périmé après un redémarrage/crash du bot."""
    return broker.get_open_positions(token, account_id, region)
