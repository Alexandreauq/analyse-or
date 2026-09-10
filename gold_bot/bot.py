# gold_bot/bot.py
# Orchestration du bot : combine confluence + risque + broker selon les
# règles de renversement/empilement du spec. DANS CE PLAN (Plan A),
# decide_and_act() ne place et ne clôture JAMAIS de vraie position — il
# ne fait que journaliser ce qu'il ferait. Voir
# docs/superpowers/specs/2026-09-10-bot-trading-or-design.md.
import gold_bot.broker as broker
import gold_bot.confluence as confluence
import gold_bot.risk as risk


def decide_and_act(candles: list[dict], contract_size: float, balance: float,
                    open_positions: list[dict], circuit_breaker: "risk.CircuitBreaker",
                    symbol: str = "XAUUSD") -> dict:
    """Cœur de la boucle : évalue le signal, applique les règles de
    renversement/empilement, et — dans ce plan, TOUJOURS en simulation —
    journalise ce qu'il ferait sans jamais appeler
    broker.place_market_order / broker.close_position. `open_positions`
    doit provenir d'un appel récent à reconcile_positions (jamais d'un
    état local qui pourrait être périmé). Ne prend délibérément pas de
    token/account_id : elle ne parle jamais elle-même au broker — le
    code de câblage réel (Plan B) appellera broker.place_market_order/
    close_position séparément, avec les champs déjà présents dans
    `steps` ci-dessous."""
    signal = confluence.compute_signal(candles)

    existing = next((p for p in open_positions if p.get("symbol") == symbol), None)
    existing_direction = None
    if existing is not None:
        existing_direction = "achat" if existing.get("type") == "POSITION_TYPE_BUY" else "vente"

    if signal["status"] == "neutre":
        return {"action": "aucune", "reason": "signal neutre"}

    if existing_direction == signal["status"]:
        return {"action": "aucune", "reason": "position déjà ouverte dans le même sens"}

    if not circuit_breaker.can_open_position(balance):
        return {"action": "aucune", "reason": "coupe-circuit journalier déclenché"}

    steps = []
    if existing_direction is not None and existing_direction != signal["status"]:
        steps.append({"type": "clôture_simulee", "position_id": existing.get("id"), "symbol": symbol})

    size = risk.compute_position_size(balance, signal["entry"], signal["stop_loss"], contract_size)
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
