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
from datetime import datetime, timedelta, timezone

import gold_bot.bot as bot
import gold_bot.broker as broker
import gold_bot.confluence as confluence
import gold_bot.risk as risk
import gold_bot.state as state

# gold_bot.vwap_reversion a besoin de bien plus d'historique par cycle
# que l'ancien moteur (EMA200 sur des bougies 15min rééchantillonnées ⇒
# au moins 600 bougies 5min) -- 1000 est le plafond MetaApi par appel
# (~3.5 jours de bougies 5min, large marge y compris pour absorber un
# week-end de marché fermé).
CANDLES_FETCH_LIMIT = 1000

# Remis à 60s (2026-09-12) : le compte Twelve Data est passé au plan
# Grow (29$/mois, 55 crédits/min, sans plafond journalier) — le
# plafond gratuit de 800/jour qui avait motivé le passage à 120s
# n'existe plus.
POLL_INTERVAL_SECONDS = 60
# Aligné sur STALE_THRESHOLD_MS (scalping_tracker.js) : au-delà de ce
# délai entre "now" et l'horodatage de la dernière bougie reçue, les
# données ne sont plus considérées fiables pour une décision.
STALE_CANDLE_THRESHOLD_SECONDS = 5 * 60
# Tolérance de dérive de prix entre le calcul du signal et l'exécution
# réelle, en fraction de la distance de risque prévue (|entry-stop_loss|)
# plutôt qu'en dollars fixes, pour s'adapter à n'importe quelle largeur
# de stop. Voir _entry_price_has_drifted ci-dessous.
MAX_ENTRY_SLIPPAGE_RATIO = 0.25
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


def _entry_price_has_drifted(step: dict, fresh_price: float) -> bool:
    """Le marché a-t-il trop bougé entre le calcul du signal et l'instant
    juste avant l'envoi réel de l'ordre ? `stop_loss`/`take_profit` sont
    envoyés au broker comme des prix absolus, jamais recalés sur le prix
    réel de remplissage -- calculés à partir de `entry`, qui peut être
    périmé de plusieurs secondes à ~1 minute (le temps des 3 appels
    réseau -- solde/positions/spécification -- faits entre le calcul du
    signal et ce point, voir run_cycle). Un trop grand écart signifie que
    le risque réellement pris (distance entre le remplissage réel et le
    stop) diverge de ce que risk.compute_position_size visait. Trouvé
    lors de l'audit pré-lancement du 2026-09-29."""
    entry = step["entry"]
    stop_loss = step["stop_loss"]
    intended_risk = abs(entry - stop_loss)
    if intended_risk <= 0:
        return True  # ne devrait jamais arriver (meets_minimum_risk_reward l'exclut déjà) -- prudence si ça arrive quand même
    return abs(fresh_price - entry) > MAX_ENTRY_SLIPPAGE_RATIO * intended_risk


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


def run_cycle(token: str, account_id: str,
              circuit_breaker: "risk.CircuitBreaker", region: str = broker.DEFAULT_MT5_REGION,
              symbol: str = SYMBOL, now: datetime | None = None) -> dict:
    """Un cycle complet : interrupteur d'urgence -> bougies -> garde
    marché fermé/données périmées -> reste des données réelles + décision
    (dans un seul bloc try, jamais d'exception non capturée) ->
    re-vérification de l'état juste avant l'exécution (l'interrupteur a
    pu être activé pendant la collecte, qui prend jusqu'à ~1 minute) ->
    exécution réelle seulement si toujours pas dry_run. Journalise
    systématiquement le résultat, y compris les erreurs. `now` est
    injectable pour les tests (défaut : l'heure réelle), même convention
    que runOnce (scalping_tracker.js)."""
    now_dt = now or datetime.now(timezone.utc)
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
        candles = _with_retry(lambda: confluence.fetch_gold_candles(token, account_id, region, limit=CANDLES_FETCH_LIMIT))
        _save_cache({"candles": candles, "fetched_at": _now_iso()}, LATEST_CANDLES_PATH)

        market_closed = confluence.is_market_closed(now_dt)
        last_candle_time = datetime.fromisoformat(candles[-1]["time"].replace(" ", "T")).replace(tzinfo=timezone.utc)
        # Une bougie MetaApi est horodatée à son OUVERTURE, pas à sa
        # clôture (voir confluence.CANDLE_INTERVAL_MINUTES) -- la fraîcheur
        # se mesure donc depuis l'heure de clôture (ouverture + durée de
        # la bougie), sans quoi la dernière bougie COMPLETE, qui a par
        # construction au moins CANDLE_INTERVAL_MINUTES de retard sur son
        # ouverture, serait quasi systématiquement jugée périmée. Trouvé
        # lors de la revue finale du 2026-09-29.
        last_candle_close_time = last_candle_time + timedelta(minutes=confluence.CANDLE_INTERVAL_MINUTES)
        data_stale = (now_dt - last_candle_close_time).total_seconds() > STALE_CANDLE_THRESHOLD_SECONDS
        if market_closed or data_stale:
            # Ni ouverture ni gestion de position ce cycle : les bougies
            # reçues ne sont pas des données de marché fiables, même si
            # leur horodatage paraît frais (voir is_market_closed). Évite
            # aussi les 3 appels broker suivants (solde/positions/spec),
            # inutiles puisqu'aucune décision n'en dépendra.
            reason = "marché XAU/USD fermé (week-end)" if market_closed else "données périmées"
            decision = {"action": "ignore", "reason": reason}
            _log_decision(decision, path=DECISIONS_LOG_PATH)
            return decision

        account_info = _with_retry(lambda: broker.get_account_information(token, account_id, region))
        balance = account_info["balance"]
        equity = account_info["equity"]
        _save_cache({"balance": balance, "fetched_at": _now_iso()}, LATEST_BALANCE_PATH)
        open_positions = _with_retry(lambda: bot.reconcile_positions(token, account_id, region))
        _save_cache({"positions": open_positions, "fetched_at": _now_iso()}, LATEST_POSITIONS_PATH)
        spec = _with_retry(lambda: broker.get_symbol_specification(token, account_id, symbol, region))
        contract_size = spec["contractSize"]
        decision = bot.decide_and_act_vwap(
            candles, contract_size=contract_size, balance=balance, equity=equity,
            volume_step=spec["volumeStep"], min_volume=spec["minVolume"], max_volume=spec["maxVolume"],
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

    # Garde anti-slippage : ne re-vérifie le prix (un appel réseau de
    # plus, uniquement quand une ouverture est réellement sur le point de
    # partir) que pour les étapes d'ouverture -- une clôture n'a pas de
    # prix figé à protéger. Un échec du re-contrôle est traité comme une
    # dérive (prudence : on ne sait pas si le prix a bougé, donc on
    # n'exécute pas plutôt que de risquer un stop mal dimensionné).
    steps = decision["steps"]
    if any(step["type"] == "ouverture_simulee" for step in steps):
        try:
            fresh_candles = _with_retry(lambda: confluence.fetch_gold_candles(token, account_id, region))
            fresh_price = fresh_candles[-1]["close"]
        except Exception:
            fresh_price = None
        filtered_steps = []
        for step in steps:
            if (step["type"] == "ouverture_simulee"
                    and (fresh_price is None or _entry_price_has_drifted(step, fresh_price))):
                _log_decision(
                    {"action": "ignore", "reason": "prix de marché trop éloigné du signal au moment de l'exécution "
                                                     "(protection anti-slippage)", "step": step},
                    path=DECISIONS_LOG_PATH,
                )
                continue
            filtered_steps.append(step)
        steps = filtered_steps

    if not steps:
        result = {"action": "ignore", "reason": "aucune étape restante après le contrôle anti-slippage"}
        _log_decision(result, path=DECISIONS_LOG_PATH)
        return result

    results = execute_steps(token, account_id, steps, region)
    had_error = any(r["error"] for r in results) or not results
    result = {"action": "erreur" if had_error else "exécuté", "steps": steps, "results": results}
    _log_decision(result, path=DECISIONS_LOG_PATH)
    return result


def main():
    token = os.environ["METAAPI_TRADE_TOKEN"]
    account_id = os.environ["METAAPI_TRADE_ACCOUNT_ID"]
    circuit_breaker = risk.CircuitBreaker(persist_path=CIRCUIT_BREAKER_STATE_PATH)

    while True:
        try:
            run_cycle(token, account_id, circuit_breaker)
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
