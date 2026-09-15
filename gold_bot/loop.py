# gold_bot/loop.py
# Boucle continue et exécution réelle des ordres — le SEUL module de ce
# projet qui appelle broker.place_market_order/close_position pour de
# vrai. gold_bot.bot.decide_and_act() elle-même n'appelle jamais le
# broker (garantie posée et vérifiée en Plan A, jamais modifiée ici).
# Voir docs/superpowers/specs/2026-09-10-bot-trading-or-design.md.
import json
import os
import time
import traceback
from datetime import datetime, timezone

import gold_bot.bot as bot
import gold_bot.broker as broker
import gold_bot.confluence as confluence
import gold_bot.risk as risk
import gold_bot.state as state

# Remis à 60s (2026-09-12) : le compte Twelve Data est passé au plan
# Grow (29$/mois, 55 crédits/min, sans plafond journalier) — le
# plafond gratuit de 800/jour qui avait motivé le passage à 120s
# n'existe plus.
POLL_INTERVAL_SECONDS = 60
SYMBOL = "XAUUSD"
DECISIONS_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decisions_log.jsonl")
# Fichier séparé de state.STATE_PATH (qui porte kill_switch/dry_run,
# écrit aussi par l'API du Task 3) — évite qu'une écriture concurrente
# entre deux processus sur le même fichier ne puisse jamais écraser
# silencieusement un changement de l'interrupteur d'urgence.
CIRCUIT_BREAKER_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "circuit_breaker_state.json")
LATEST_CANDLES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "latest_candles.json")
LATEST_BALANCE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "latest_balance.json")
LATEST_POSITIONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "latest_positions.json")


def execute_steps(token: str, account_id: str, steps: list[dict],
                   region: str = broker.DEFAULT_MT5_REGION) -> list[dict]:
    """Exécute réellement les étapes renvoyées par bot.decide_and_act.
    Vérifie elle-même l'état (dry_run/kill_switch) par défense en
    profondeur, même si run_cycle l'a déjà vérifié avant d'appeler
    cette fonction — aucun futur appelant de ce module ne doit pouvoir
    déclencher un ordre réel sans repasser par ce filtre. N'interrompt
    jamais la liste sur une étape en échec : chaque étape, réussie ou
    non, est consignée dans le résultat, pour qu'une étape en échec
    n'efface jamais la trace d'une étape précédente réellement
    exécutée (ex. une clôture réussie suivie d'une ouverture ratée)."""
    current_state = state.load_state(state.STATE_PATH)
    if current_state["kill_switch"] or current_state["dry_run"]:
        return [
            {"step": step, "result": None, "error": "exécution refusée : kill_switch ou dry_run actif"}
            for step in steps
        ]
    results = []
    for step in steps:
        try:
            if step["type"] == "clôture_simulee":
                result = broker.close_position(token, account_id, step["position_id"], region)
            elif step["type"] == "ouverture_simulee":
                result = broker.place_market_order(
                    token, account_id, step["symbol"], step["direction"], step["volume"],
                    step["stop_loss"], step["take_profit"], region,
                )
            else:
                continue
            results.append({"step": step, "result": result, "error": None})
        except Exception as e:
            results.append({"step": step, "result": None, "error": str(e)})
    return results


def _log_decision(entry: dict, path: str = DECISIONS_LOG_PATH) -> None:
    """Journalise chaque décision de chaque cycle (localement) pour le
    résumé quotidien (gold_bot.notify). N'interrompt jamais le cycle si
    l'écriture échoue."""
    record = dict(entry)
    record["timestamp"] = _now_iso()
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"Erreur journalisation décision : {e}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _save_cache(data: dict, path: str) -> None:
    """Ne doit jamais interrompre le cycle si l'écriture du cache
    échoue (même contrat que _log_decision) — une panne disque sur
    ce cache, purement pour le tableau de bord, ne doit jamais
    empêcher la décision/exécution réelle de continuer."""
    try:
        state.save_state(data, path)
    except Exception as e:
        print(f"Erreur écriture cache ({path}) : {e}")


NETWORK_RETRY_ATTEMPTS = 3
NETWORK_RETRY_DELAY_SECONDS = 2.0


def _with_retry(fn, *, attempts: int = NETWORK_RETRY_ATTEMPTS, delay_s: float = NETWORK_RETRY_DELAY_SECONDS):
    """Retente un appel réseau jusqu'à `attempts` fois avant de laisser
    remonter la dernière exception. Trouvé en production le 2026-09-14
    (27 erreurs sur 1600 cycles en ~1,5 jour, via l'email quotidien) :
    un timeout Twelve Data, un 429/504 MetaApi ou une résolution DNS
    ratée sur le VPS annulaient tout le cycle sans seconde chance avant
    le suivant, 60s plus tard — la boucle externe s'auto-cicatrise déjà
    cycle après cycle (contrairement au bug scalping_tracker.yml du
    même jour, où une seule panne tuait 5h40 de collecte d'un coup),
    donc ce n'est pas un correctif urgent, juste une robustesse en plus
    à faible coût. Appliqué uniquement aux 4 appels réseau du cycle
    (candles/solde/positions/spec), PAS à decide_and_act : ce n'est pas
    un appel réseau, une exception y est un bug logique que retenter ne
    résoudrait pas."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as e:
            last_error = e
            if attempt < attempts - 1:
                time.sleep(delay_s)
    raise last_error


def run_cycle(token: str, account_id: str, twelve_data_api_key: str,
              circuit_breaker: "risk.CircuitBreaker", region: str = broker.DEFAULT_MT5_REGION,
              symbol: str = SYMBOL) -> dict:
    """Un cycle complet : interrupteur d'urgence -> données réelles +
    décision (dans un seul bloc try, jamais d'exception non capturée) ->
    re-vérification de l'état juste avant l'exécution (l'interrupteur a
    pu être activé pendant la collecte, qui prend jusqu'à ~1 minute) ->
    exécution réelle seulement si toujours pas dry_run. Journalise
    systématiquement le résultat, y compris les erreurs."""
    current_state = state.load_state(state.STATE_PATH)
    if current_state["kill_switch"]:
        decision = {"action": "ignore", "reason": "interrupteur d'urgence activé"}
        _log_decision(decision, path=DECISIONS_LOG_PATH)
        return decision

    # Résolu à chaque cycle (pas seulement au démarrage du process) —
    # même contrat que dry_run/kill_switch : un changement de profil via
    # POST /profile doit prendre effet au cycle suivant, sans redémarrage
    # du service. circuit_breaker est un objet unique et persistant sur
    # toute la durée du while True de main() (il porte l'état du solde
    # de départ journalier) — on mute son threshold_pct en place plutôt
    # que de recréer l'objet, ce qui perdrait cet état.
    profile_params = risk.risk_profile_params(current_state.get("risk_profile"))
    circuit_breaker.threshold_pct = profile_params["threshold_pct"]

    try:
        candles = _with_retry(lambda: confluence.fetch_gold_candles(twelve_data_api_key))
        _save_cache({"candles": candles, "fetched_at": _now_iso()}, LATEST_CANDLES_PATH)
        balance = _with_retry(lambda: broker.get_account_balance(token, account_id, region))
        _save_cache({"balance": balance, "fetched_at": _now_iso()}, LATEST_BALANCE_PATH)
        open_positions = _with_retry(lambda: bot.reconcile_positions(token, account_id, region))
        _save_cache({"positions": open_positions, "fetched_at": _now_iso()}, LATEST_POSITIONS_PATH)
        spec = _with_retry(lambda: broker.get_symbol_specification(token, account_id, symbol, region))
        contract_size = spec["contractSize"]
        decision = bot.decide_and_act(
            candles, contract_size=contract_size, balance=balance,
            open_positions=open_positions, circuit_breaker=circuit_breaker, symbol=symbol,
            risk_pct=profile_params["risk_pct"],
        )
    except Exception as e:
        decision = {"action": "erreur", "reason": f"Erreur pendant la décision : {e}"}
        _log_decision(decision, path=DECISIONS_LOG_PATH)
        print(f"Erreur pendant la décision : {e}")
        traceback.print_exc()
        return decision

    if decision["action"] != "simulation":
        _log_decision(decision, path=DECISIONS_LOG_PATH)
        return decision

    # Re-vérification juste avant l'exécution : jusqu'à ~1 minute s'est
    # écoulée depuis le premier contrôle (4 appels réseau ci-dessus) —
    # on ne se fie pas à un état potentiellement périmé pour décider
    # d'exécuter réellement.
    fresh_state = state.load_state(state.STATE_PATH)
    if fresh_state["kill_switch"] or fresh_state["dry_run"]:
        result = {**decision, "action": "simulation_dry_run"}
        _log_decision(result, path=DECISIONS_LOG_PATH)
        return result

    results = execute_steps(token, account_id, decision["steps"], region)
    had_error = any(r["error"] for r in results) or not results
    result = {"action": "erreur" if had_error else "exécuté", "steps": decision["steps"], "results": results}
    _log_decision(result, path=DECISIONS_LOG_PATH)
    return result


def main():
    token = os.environ["METAAPI_TRADE_TOKEN"]
    account_id = os.environ["METAAPI_TRADE_ACCOUNT_ID"]
    twelve_data_api_key = os.environ["TWELVE_DATA_API_KEY"]
    circuit_breaker = risk.CircuitBreaker(persist_path=CIRCUIT_BREAKER_STATE_PATH)

    while True:
        try:
            run_cycle(token, account_id, twelve_data_api_key, circuit_breaker)
        except Exception as e:
            # Filet de sécurité ultime — run_cycle ne devrait jamais
            # lever (elle capture déjà ses propres erreurs), mais un
            # vrai crash de la boucle serait pire qu'un cycle manqué.
            print(f"Erreur inattendue pendant le cycle : {e}")
            traceback.print_exc()
        # Dort jusqu'à la prochaine limite de minute plutôt qu'un délai
        # fixe après le cycle — sinon la durée du cycle lui-même (jusqu'à
        # ~60s cumulés sur les 4 appels réseau) s'additionne au délai et
        # le bot finit par évaluer des bougies en milieu de clôture au
        # lieu de leur clôture réelle.
        time.sleep(max(1, POLL_INTERVAL_SECONDS - time.time() % POLL_INTERVAL_SECONDS))


if __name__ == "__main__":
    main()
