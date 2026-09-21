# gold_bot/bot.py
# Orchestration du bot : combine confluence + risque + broker selon les
# règles de renversement/empilement du spec. DANS CE PLAN (Plan A),
# decide_and_act() ne place et ne clôture JAMAIS de vraie position — il
# ne fait que journaliser ce qu'il ferait. Voir
# docs/superpowers/specs/2026-09-10-bot-trading-or-design.md.
from datetime import datetime, timezone

import gold_bot.broker as broker
import gold_bot.confluence as confluence
import gold_bot.risk as risk


def decide_and_act(candles: list[dict], *, contract_size: float, balance: float, equity: float,
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

    steps = []
    for p in matching:
        steps.append({"type": "clôture_simulee", "position_id": p.get("id"), "symbol": symbol})

    size = risk.compute_position_size(balance, signal["entry"], signal["stop_loss"], contract_size, risk_pct=risk_pct)
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


def reconcile_positions(token: str, account_id: str, region: str = broker.DEFAULT_MT5_REGION) -> list[dict]:
    """Interroge MetaApi pour l'état réel des positions avant toute
    décision — jamais de confiance aveugle en un état local qui
    pourrait être périmé après un redémarrage/crash du bot."""
    return broker.get_open_positions(token, account_id, region)
