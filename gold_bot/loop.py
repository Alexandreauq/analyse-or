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

import gold_bot.backtest as backtest
import gold_bot.bot as bot
import gold_bot.broker as broker
import gold_bot.confluence as confluence
import gold_bot.ftmo_guard as ftmo_guard
import gold_bot.notify_ftmo_inactivity as notify_ftmo_inactivity
import gold_bot.notify_ftmo_signal as notify_ftmo_signal
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
# Argent (XAGUSD) ajouté le 2026-09-30 après une recalibration
# walk-forward complète et séparée (voir
# gold_bot.vwap_reversion.get_symbol_params) -- chaque symbole tourne
# son propre cycle complet (bougies/décision/exécution), le
# coupe-circuit est partagé entre les deux (même compte, même budget
# de perte journalière).
SYMBOLS = ["XAUUSD", "XAGUSD"]
DECISIONS_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decisions_log.jsonl")
# Fichier séparé de state.STATE_PATH (qui porte kill_switch/dry_run,
# écrit aussi par l'API du Task 3) — évite qu'une écriture concurrente
# entre deux processus sur le même fichier ne puisse jamais écraser
# silencieusement un changement de l'interrupteur d'urgence.
CIRCUIT_BREAKER_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "circuit_breaker_state.json")
# N'est utilisé que si FTMO_ISC est présente dans l'environnement (voir
# main()) -- surchargeable par FTMO_GUARD_STATE_PATH, même convention
# que les autres chemins d'état de ce fichier.
FTMO_GUARD_STATE_PATH = os.environ.get(
    "FTMO_GUARD_STATE_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ftmo_guard_state.json"))
LATEST_CANDLES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "latest_candles.json")
LATEST_BALANCE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "latest_balance.json")
LATEST_POSITIONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "latest_positions.json")

# TTL de l'historique utilisé par la vérification des cotes
# (gold_bot.odds_check) -- un historique de plusieurs dizaines de jours
# n'a pas besoin d'être rafraîchi à chaque cycle (60s) ; 24h tient
# largement compte de l'évolution réelle du marché pour ce besoin.
HISTORICAL_ODDS_CACHE_MAX_AGE_SECONDS = 24 * 3600
# Ne retente pas un fetch de 90 jours (~20 pages MetaApi) plus d'une fois
# par heure apres un echec -- MetaApi a deja renvoye des 429 dans ce
# projet ; retenter a chaque cycle (60s) pendant une panne prolongee
# serait pire que l'absence temporaire de verification des cotes.
HISTORICAL_ODDS_FAILURE_BACKOFF_SECONDS = 3600
HISTORICAL_ODDS_WINDOW_DAYS = 90


def _resolve_symbols(ftmo_isc: str | None, ftmo_symbols_env: str | None) -> list[str]:
    """FTMO_ISC absente (déploiement du compte perso actuel) : renvoie
    SYMBOLS inchangée. FTMO_ISC présente : les CGU FTMO (clause 7.5.3,
    lues par l'utilisateur le 2026-10-27) interdisent une exposition
    cumulée sur des symboles corrélés -- XAUUSD et XAGUSD le sont
    fortement -- donc seul XAUUSD est tradé par défaut sur l'instance
    FTMO, sauf surcharge explicite via FTMO_SYMBOLS (liste séparée par
    des virgules)."""
    if ftmo_isc is None:
        return SYMBOLS
    if ftmo_symbols_env:
        return [s.strip() for s in ftmo_symbols_env.split(",") if s.strip()]
    return ["XAUUSD"]


def _candles_cache_path(symbol: str) -> str:
    """LATEST_CANDLES_PATH reste réservé à XAUUSD (symbole par défaut,
    déjà lu par gold_bot.api::dashboard pour le graphique du site) --
    un autre symbole écrirait sinon par-dessus au cycle suivant et
    ferait flapper le graphique entre deux instruments. Chemin dédié
    (additif, rien ne le lit encore) pour tout autre symbole."""
    if symbol == SYMBOL:
        return LATEST_CANDLES_PATH
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), f"latest_candles_{symbol.lower()}.json")


def _historical_candles_cache_path(symbol: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), f"historical_candles_cache_{symbol.lower()}.json")


def _load_or_fetch_historical_candles(token: str, account_id: str, symbol: str,
                                       region: str, now_dt: datetime) -> list[dict]:
    """Charge l'historique mis en cache pour `symbol` s'il a moins de
    HISTORICAL_ODDS_CACHE_MAX_AGE_SECONDS (succes) ou
    HISTORICAL_ODDS_FAILURE_BACKOFF_SECONDS (dernier essai en echec),
    sinon le (re)recupere via backtest.fetch_gold_candles_range. Ne leve
    JAMAIS : `except Exception` volontairement large sur toute la
    lecture/parsing du cache (fichier corrompu, JSON valide mais pas un
    dict, horodatage sans fuseau...) plutot qu'un tuple d'exceptions
    precis -- trouve insuffisant en revue finale de branche, puisque
    cette fonction est appelee AVANT decide_and_act_vwap et qu'une
    exception non capturee ici bloquerait alors aussi la gestion des
    positions deja ouvertes (stop suiveur, cloture de fin de session),
    pas seulement la recherche de nouveaux signaux."""
    path = _historical_candles_cache_path(symbol)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cached = json.load(f)
            fetched_at = datetime.fromisoformat(cached["fetched_at"].replace("Z", "+00:00"))
            age = (now_dt - fetched_at).total_seconds()
            if cached.get("is_failure_backoff"):
                if age < HISTORICAL_ODDS_FAILURE_BACKOFF_SECONDS:
                    return []
            elif age < HISTORICAL_ODDS_CACHE_MAX_AGE_SECONDS:
                return cached["candles"]
        except Exception:
            pass
    try:
        candles = backtest.fetch_gold_candles_range(
            token, account_id, now_dt - timedelta(days=HISTORICAL_ODDS_WINDOW_DAYS), now_dt,
            region=region, symbol=symbol,
        )
    except Exception:
        _save_cache({"candles": [], "fetched_at": _now_iso(), "is_failure_backoff": True}, path)
        return []
    _save_cache({"candles": candles, "fetched_at": _now_iso()}, path)
    return candles


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
            elif step["type"] == "modification_simulee":
                result = broker.modify_position_stop_loss(
                    token, account_id, step["position_id"], step["new_stop_loss"], region,
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
              symbol: str = SYMBOL, now: datetime | None = None,
              ftmo_guard_instance: "ftmo_guard.FtmoGuard | None" = None) -> dict:
    """Un cycle complet : interrupteur d'urgence -> bougies -> garde
    marché fermé/données périmées -> reste des données réelles + décision
    (dans un seul bloc try, jamais d'exception non capturée) ->
    re-vérification de l'état juste avant l'exécution (l'interrupteur a
    pu être activé pendant la collecte, qui prend jusqu'à ~1 minute) ->
    exécution réelle seulement si toujours pas dry_run. Journalise
    systématiquement le résultat, y compris les erreurs. `now` est
    injectable pour les tests (défaut : l'heure réelle), même convention
    que runOnce (scalping_tracker.js).

    `ftmo_guard_instance`, si fourni (None par défaut -- voir main(),
    actif seulement si FTMO_ISC est dans l'environnement), est contrôlé
    juste après la lecture balance/equity : un franchissement ferme
    TOUTES les positions ouvertes du compte (broker.close_all_positions,
    pas seulement celles de ce cycle/symbole) et arme le kill_switch
    existant, puis le cycle s'arrête là -- aucune nouvelle décision
    n'est prise ce cycle. Absent (None), ce bloc ne s'exécute jamais et
    rien ne change par rapport au comportement du bot sans FTMO."""
    now_dt = now or datetime.now(timezone.utc)
    current_state = state.load_state(state.STATE_PATH)
    if current_state["kill_switch"]:
        decision = {"action": "ignore", "reason": "interrupteur d'urgence activé", "symbol": symbol}
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
    circuit_breaker.max_trades_per_day = profile_params["max_trades_per_day"]

    try:
        candles = _with_retry(lambda: confluence.fetch_gold_candles(
            token, account_id, region, limit=CANDLES_FETCH_LIMIT, symbol=symbol))
        _save_cache({"candles": candles, "fetched_at": _now_iso()}, _candles_cache_path(symbol))

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
            reason = f"marché {symbol} fermé (week-end)" if market_closed else "données périmées"
            decision = {"action": "ignore", "reason": reason, "symbol": symbol}
            _log_decision(decision, path=DECISIONS_LOG_PATH)
            return decision

        account_info = _with_retry(lambda: broker.get_account_information(token, account_id, region))
        balance = account_info["balance"]
        equity = account_info["equity"]
        _save_cache({"balance": balance, "fetched_at": _now_iso()}, LATEST_BALANCE_PATH)

        if ftmo_guard_instance is not None:
            breach = ftmo_guard_instance.check(balance, equity)
            if breach is not None:
                # "target" (objectif de profit atteint) et "daily"/"overall"
                # (plancher franchi) déclenchent la MÊME action -- fermeture
                # totale + kill_switch -- seul le libellé du log diffère : un
                # objectif atteint doit être verrouillé (l'utilisateur
                # demande l'évaluation à FTMO manuellement) plutôt que de
                # laisser le bot continuer et risquer de redonner le gain.
                event_label = "objectif de profit atteint" if breach == "target" else f"plancher {breach} franchi"
                close_results = broker.close_all_positions(token, account_id, region)
                failed_closes = [r for r in close_results if not r["closed"]]
                if failed_closes:
                    print(f"ERREUR FTMO : {len(failed_closes)} position(s) non fermée(s) après "
                          f"{event_label} : {failed_closes}")
                state.update_state({"kill_switch": True})
                decision = {
                    "action": "ftmo_garde_fou", "reason": breach, "symbol": symbol,
                    "close_results": close_results,
                }
                _log_decision(decision, path=DECISIONS_LOG_PATH)
                return decision

        open_positions = _with_retry(lambda: bot.reconcile_positions(token, account_id, region))
        _save_cache({"positions": open_positions, "fetched_at": _now_iso()}, LATEST_POSITIONS_PATH)
        spec = _with_retry(lambda: broker.get_symbol_specification(token, account_id, symbol, region))
        contract_size = spec["contractSize"]
        historical_candles = _load_or_fetch_historical_candles(token, account_id, symbol, region, now_dt)
        engine = bot.decide_and_act_fibonacci if ftmo_guard_instance is not None else bot.decide_and_act_vwap
        decision = engine(
            candles, contract_size=contract_size, balance=balance, equity=equity,
            volume_step=spec["volumeStep"], min_volume=spec["minVolume"], max_volume=spec["maxVolume"],
            open_positions=open_positions, circuit_breaker=circuit_breaker, symbol=symbol,
            risk_pct=profile_params["risk_pct"], historical_candles=historical_candles,
        )
        decision = {**decision, "symbol": symbol}
        if ftmo_guard_instance is not None:
            # Alerte email sur nouveau signal d'entree FTMO -- distincte
            # du resume quotidien (gold_bot.notify), jamais appelee pour
            # l'instance perso (gardee sous ce meme "if" que le reste de
            # la logique FTMO). Avant _log_decision ci-dessous : l'ordre
            # n'a pas d'impact fonctionnel (les deux sont independants),
            # mais place ici le lien est visible avec le bloc FTMO.
            # Alerte distincte sur l'inactivite prolongee du compte
            # (aucun trade REEL depuis N jours, pas un signal) -- voir
            # gold_bot/notify_ftmo_inactivity.py, marge avant la clause
            # FTMO 13.2.3(a) (resiliation possible a 30 jours).
            notify_ftmo_signal.maybe_send_signal_email(decision)
            notify_ftmo_inactivity.maybe_send_inactivity_email(now_dt)
    except Exception as e:
        decision = {"action": "erreur", "reason": f"Erreur pendant la décision : {e}", "symbol": symbol}
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
            # Symbole explicite : un cycle argent ne doit jamais se fier
            # au prix de l'or (ou l'inverse) pour son contrôle
            # anti-slippage -- avant ce correctif, cet appel ignorait
            # toujours `symbol` et retombait sur XAUUSD par défaut.
            fresh_candles = _with_retry(lambda: confluence.fetch_gold_candles(token, account_id, region, symbol=symbol))
            fresh_price = fresh_candles[-1]["close"]
        except Exception:
            fresh_price = None
        filtered_steps = []
        for step in steps:
            if (step["type"] == "ouverture_simulee"
                    and (fresh_price is None or _entry_price_has_drifted(step, fresh_price))):
                _log_decision(
                    {"action": "ignore", "reason": "prix de marché trop éloigné du signal au moment de l'exécution "
                                                     "(protection anti-slippage)", "step": step, "symbol": symbol},
                    path=DECISIONS_LOG_PATH,
                )
                continue
            filtered_steps.append(step)
        steps = filtered_steps

    if not steps:
        result = {"action": "ignore", "reason": "aucune étape restante après le contrôle anti-slippage", "symbol": symbol}
        _log_decision(result, path=DECISIONS_LOG_PATH)
        return result

    results = execute_steps(token, account_id, steps, region)
    had_error = any(r["error"] for r in results) or not results
    opened_successfully = any(
        r["step"]["type"] == "ouverture_simulee" and r["error"] is None for r in results
    )
    if opened_successfully:
        circuit_breaker.record_trade_opened()
    result = {"action": "erreur" if had_error else "exécuté", "steps": steps, "results": results, "symbol": symbol}
    _log_decision(result, path=DECISIONS_LOG_PATH)
    return result


def main():
    token = os.environ["METAAPI_TRADE_TOKEN"]
    account_id = os.environ["METAAPI_TRADE_ACCOUNT_ID"]
    circuit_breaker = risk.CircuitBreaker(persist_path=CIRCUIT_BREAKER_STATE_PATH)
    # FTMO_ISC optionnelle -- absente (déploiement du compte perso actuel) :
    # ftmo_guard_instance reste None, run_cycle ne change rien à son
    # comportement actuel (voir sa docstring). Présente (déploiement
    # dédié à un compte FTMO) : un seul garde-fou partagé entre les deux
    # symboles de SYMBOLS, même pattern que circuit_breaker ci-dessus.
    ftmo_isc = os.environ.get("FTMO_ISC")
    ftmo_guard_instance = (
        ftmo_guard.FtmoGuard(isc=float(ftmo_isc), persist_path=FTMO_GUARD_STATE_PATH)
        if ftmo_isc is not None else None
    )
    symbols = _resolve_symbols(ftmo_isc, os.environ.get("FTMO_SYMBOLS"))

    while True:
        for symbol in symbols:
            try:
                run_cycle(token, account_id, circuit_breaker, symbol=symbol, ftmo_guard_instance=ftmo_guard_instance)
            except Exception as e:
                # Filet de sécurité ultime — run_cycle ne devrait jamais
                # lever (elle capture déjà ses propres erreurs), mais un
                # vrai crash de la boucle serait pire qu'un cycle manqué
                # -- et ne doit surtout pas empêcher le symbole suivant
                # de tourner.
                print(f"Erreur inattendue pendant le cycle ({symbol}) : {e}")
                traceback.print_exc()
        # Dort jusqu'à la prochaine limite de minute plutôt qu'un délai
        # fixe après le cycle — sinon la durée du cycle lui-même (jusqu'à
        # ~60s cumulés sur les 4 appels réseau) s'additionne au délai et
        # le bot finit par évaluer des bougies en milieu de clôture au
        # lieu de leur clôture réelle.
        time.sleep(max(1, POLL_INTERVAL_SECONDS - time.time() % POLL_INTERVAL_SECONDS))


if __name__ == "__main__":
    main()
