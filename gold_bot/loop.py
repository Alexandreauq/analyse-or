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

POLL_INTERVAL_SECONDS = 60
SYMBOL = "XAUUSD"
DECISIONS_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decisions_log.jsonl")


def execute_steps(token: str, account_id: str, steps: list[dict],
                   region: str = broker.DEFAULT_MT5_REGION) -> list[dict]:
    """Exécute réellement les étapes renvoyées par bot.decide_and_act.
    Toute étape d'un type non reconnu est ignorée plutôt que de lever
    (garde-fou : ne jamais deviner une action à partir d'une donnée
    inattendue)."""
    results = []
    for step in steps:
        if step["type"] == "clôture_simulee":
            result = broker.close_position(token, account_id, step["position_id"], region)
        elif step["type"] == "ouverture_simulee":
            result = broker.place_market_order(
                token, account_id, step["symbol"], step["direction"], step["volume"],
                step["stop_loss"], step["take_profit"], region,
            )
        else:
            continue
        results.append({"step": step, "result": result})
    return results


def _log_decision(entry: dict, path: str = DECISIONS_LOG_PATH) -> None:
    """Journalise chaque décision de chaque cycle (localement) pour le
    résumé quotidien (gold_bot.notify). N'interrompt jamais le cycle si
    l'écriture échoue."""
    record = dict(entry)
    record["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"Erreur journalisation décision : {e}")


def run_cycle(token: str, account_id: str, twelve_data_api_key: str,
              circuit_breaker: "risk.CircuitBreaker", region: str = broker.DEFAULT_MT5_REGION,
              symbol: str = SYMBOL) -> dict:
    """Un cycle complet : interrupteur d'urgence -> données réelles ->
    décision (bot.decide_and_act, jamais d'appel broker dedans) ->
    exécution réelle seulement si dry_run est faux. Journalise
    systématiquement le résultat, y compris les erreurs (remontées dans
    le résumé quotidien par gold_bot.notify) — ne lève jamais, pour que
    la boucle appelante passe simplement au cycle suivant plutôt que de
    s'arrêter."""
    current_state = state.load_state()
    if current_state["kill_switch"]:
        decision = {"action": "ignore", "reason": "interrupteur d'urgence activé"}
        _log_decision(decision, path=DECISIONS_LOG_PATH)
        return decision

    try:
        candles = confluence.fetch_gold_candles(twelve_data_api_key)
        balance = broker.get_account_balance(token, account_id, region)
        open_positions = bot.reconcile_positions(token, account_id, region)
        spec = broker.get_symbol_specification(token, account_id, symbol, region)
        contract_size = spec["contractSize"]
    except Exception as e:
        decision = {"action": "erreur", "reason": f"Données indisponibles : {e}"}
        _log_decision(decision, path=DECISIONS_LOG_PATH)
        print(f"Erreur pendant la collecte des données : {e}")
        traceback.print_exc()
        return decision

    decision = bot.decide_and_act(
        candles, contract_size=contract_size, balance=balance,
        open_positions=open_positions, circuit_breaker=circuit_breaker, symbol=symbol,
    )

    if decision["action"] != "simulation":
        _log_decision(decision, path=DECISIONS_LOG_PATH)
        return decision

    if current_state["dry_run"]:
        result = {**decision, "action": "simulation_dry_run"}
        _log_decision(result, path=DECISIONS_LOG_PATH)
        return result

    try:
        results = execute_steps(token, account_id, decision["steps"], region)
        result = {"action": "exécuté", "steps": decision["steps"], "results": results}
    except Exception as e:
        result = {"action": "erreur", "reason": f"Échec d'exécution : {e}", "steps": decision["steps"]}
        print(f"Erreur pendant l'exécution des ordres : {e}")
        traceback.print_exc()
    _log_decision(result, path=DECISIONS_LOG_PATH)
    return result


def main():
    token = os.environ["METAAPI_TRADE_TOKEN"]
    account_id = os.environ["METAAPI_TRADE_ACCOUNT_ID"]
    twelve_data_api_key = os.environ["TWELVE_DATA_API_KEY"]
    circuit_breaker = risk.CircuitBreaker(persist_path=state.STATE_PATH)

    while True:
        try:
            run_cycle(token, account_id, twelve_data_api_key, circuit_breaker)
        except Exception as e:
            # Filet de sécurité ultime — run_cycle ne devrait jamais
            # lever (elle capture déjà ses propres erreurs), mais un
            # vrai crash de la boucle serait pire qu'un cycle manqué.
            print(f"Erreur inattendue pendant le cycle : {e}")
            traceback.print_exc()
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
