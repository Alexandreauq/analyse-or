# gold_bot/odds_check.py
# Verification des cotes historiques avant l'ouverture d'un trade --
# voir docs/superpowers/specs/2026-10-22-gold-bot-risk-gate-design.md.
# Module pur (aucun appel reseau/broker) : recoit un historique de
# bougies 5min deja recupere (par gold_bot.loop) et rejoue les memes
# mecaniques de decision/sortie que gold_bot.bot.decide_and_act_vwap,
# pour estimer les cotes reelles du meme type de signal dans le passe.
from datetime import datetime, timezone

import gold_bot.vwap_reversion as vwap_reversion


def _find_historical_entries(candles_5min: list[dict], direction: str, symbol: str = "XAUUSD") -> list[dict]:
    """Rejoue vwap_reversion.resample_15min + compute_indicators UNE
    SEULE FOIS sur tout l'historique fourni (pas une fois par bougie --
    compute_indicators calcule deja tout en un seul passage progressif,
    avec reset VWAP/std par jour calendaire UTC, voir sa docstring),
    puis parcourt le resultat pour trouver chaque bougie 15min ou la
    MEME condition d'entree que decide_and_act_vwap (gold_bot/bot.py)
    se serait declenchee pour `direction`. Renvoie une liste vide si
    l'historique est trop court pour une EMA200 (meme seuil que
    vwap_reversion.latest_state)."""
    candles_15min = vwap_reversion.resample_15min(candles_5min)
    if len(candles_15min) < vwap_reversion.EMA200_PERIOD:
        return []
    vwap_reversion.compute_indicators(candles_15min)
    params = vwap_reversion.get_symbol_params(symbol)

    entries = []
    for c in candles_15min:
        if c["bars_into_session"] < vwap_reversion.MIN_BARS_INTO_SESSION:
            continue
        if c["hour_utc"] >= vwap_reversion.SESSION_END_HOUR_UTC:
            continue
        if c["vwap_std"] <= 0:
            continue
        if direction == "achat":
            fires = c["close"] > c["ema200"] and c["close"] <= c["vwap"] - params["entry_sigma"] * c["vwap_std"]
            stop_loss = c["vwap"] - params["stop_sigma"] * c["vwap_std"]
        elif direction == "vente":
            fires = c["close"] < c["ema200"] and c["close"] >= c["vwap"] + params["entry_sigma"] * c["vwap_std"]
            stop_loss = c["vwap"] + params["stop_sigma"] * c["vwap_std"]
        else:
            continue
        if not fires:
            continue
        distance = abs(c["close"] - stop_loss)
        if distance < c["close"] * vwap_reversion.MIN_DISTANCE_PCT:
            continue
        entries.append({
            "entry_time": c["time"], "entry_price": c["close"], "stop_loss": stop_loss,
        })
    return entries


def _simulate_exit(candles_after_entry: list[dict], direction: str, entry_price: float,
                    initial_stop: float, entry_time: str, symbol: str = "XAUUSD") -> dict:
    """Rejoue la sortie reelle bougie par bougie a partir de
    `candles_after_entry` (5min, strictement apres la bougie d'entree).

    MODELISE LES DEUX JAMBES de decide_and_act_vwap quand
    get_symbol_params(symbol)["partial_tp_pct"] > 0 (XAUUSD et XAGUSD
    aujourd'hui -- voir vwap_reversion.PARTIAL_TP_PCT/XAGUSD_PARTIAL_TP_PCT) :
    - **Jambe partielle** (partial_tp_pct du volume) : stop FIXE a
      `initial_stop` -- decide_and_act_vwap exclut explicitement du
      suivi toute position avec takeProfit (`runner_matching = [p for p
      in matching if not p.get("takeProfit")]`), donc son stop ne bouge
      JAMAIS -- cible fixe a `entry_price +/- partial_tp_r * distance`.
      Stop gagnant en cas de toucher simultane avec sa propre cible
      (meme convention que gold_bot.backtest.simulate_trades).
    - **Jambe runner** (1 - partial_tp_pct du volume) : stop SUIVEUR via
      vwap_reversion.compute_trailing_stop a chaque pas (jamais une
      reimplementation separee -- la fidelite aux vraies mecaniques de
      sortie est le point de ce module), jamais de cible.
    Les DEUX jambes se cloturent aussi de force a la fin de session
    (SESSION_END_HOUR_UTC, meme decide_and_act_vwap qui cloture TOUTES
    les positions `matching` a ce moment-la, pas seulement le runner),
    ou a la fin de l'historique fourni (fermees au dernier prix connu
    plutot que d'etre ignorees).

    Le retour renvoye (`return_pct`) est la moyenne ponderee des deux
    jambes par `partial_tp_pct` -- reproduit le P&L REEL combine du
    bot plutot que de ne modeliser qu'une jambe runner a 100% du volume
    (ce que faisait une version anterieure de cette fonction : un bug
    trouve en revue, puisque ~70% du volume reel suit en fait une
    cible fixe, jamais le stop suiveur). Si `partial_tp_pct <= 0`
    (aucun symbole aujourd'hui, mais defensif), tout le volume reste
    en jambe unique runner -- comportement inchange.

    `close_reason`/`close_price`/`close_time` refletent la jambe
    RUNNER specifiquement (pas un melange) : c'est elle qui determine
    la duree reelle de blocage d'un nouveau signal cote
    historical_odds_for_setup dans la tres grande majorite des cas
    (son stop ne peut que se resserrer en faveur du trade, jamais
    s'eloigner), seul `return_pct` est le melange des deux jambes."""
    is_achat = direction == "achat"
    params = vwap_reversion.get_symbol_params(symbol)
    distance = abs(entry_price - initial_stop)
    partial_tp_pct = params["partial_tp_pct"]
    has_partial_leg = partial_tp_pct > 0
    if has_partial_leg:
        partial_target = (entry_price + params["partial_tp_r"] * distance if is_achat
                           else entry_price - params["partial_tp_r"] * distance)

    def _return_pct(price):
        return ((price - entry_price) / entry_price * 100 if is_achat
                else (entry_price - price) / entry_price * 100)

    stop = initial_stop
    history_so_far = [{"time": entry_time, "open": entry_price, "high": entry_price,
                        "low": entry_price, "close": entry_price, "tick_volume": 0, "spread": 0}]

    partial_return = None
    runner_return = runner_close_reason = runner_close_price = runner_close_time = None

    for candle in candles_after_entry:
        history_so_far.append(candle)
        hour = datetime.fromisoformat(candle["time"].replace(" ", "T")).replace(tzinfo=timezone.utc).hour
        session_ended = hour >= vwap_reversion.SESSION_END_HOUR_UTC

        if has_partial_leg and partial_return is None:
            sl_hit = candle["low"] <= initial_stop if is_achat else candle["high"] >= initial_stop
            if sl_hit:
                partial_return = _return_pct(initial_stop)
            else:
                tp_hit = candle["high"] >= partial_target if is_achat else candle["low"] <= partial_target
                if tp_hit:
                    partial_return = _return_pct(partial_target)
                elif session_ended:
                    partial_return = _return_pct(candle["close"])

        if runner_return is None:
            sl_hit = candle["low"] <= stop if is_achat else candle["high"] >= stop
            if sl_hit:
                runner_return = _return_pct(stop)
                runner_close_reason, runner_close_price, runner_close_time = "stop_touche", stop, candle["time"]
            elif session_ended:
                runner_return = _return_pct(candle["close"])
                runner_close_reason = "fin_session"
                runner_close_price, runner_close_time = candle["close"], candle["time"]
            else:
                new_stop = vwap_reversion.compute_trailing_stop(
                    history_so_far, direction, entry_time, entry_price, stop, symbol=symbol)
                if new_stop is not None:
                    stop = new_stop

        if runner_return is not None and (not has_partial_leg or partial_return is not None):
            break

    if runner_return is None:
        last = candles_after_entry[-1]
        runner_return = _return_pct(last["close"])
        runner_close_reason, runner_close_price, runner_close_time = "fin_historique", last["close"], last["time"]
    if has_partial_leg and partial_return is None:
        partial_return = _return_pct(candles_after_entry[-1]["close"])

    blended_return = (partial_tp_pct * partial_return + (1 - partial_tp_pct) * runner_return
                       if has_partial_leg else runner_return)

    return {"close_reason": runner_close_reason, "close_price": runner_close_price,
            "return_pct": blended_return, "close_time": runner_close_time}


def historical_odds_for_setup(candles_5min: list[dict], direction: str, symbol: str = "XAUUSD",
                               min_sample_size: int = 20) -> dict:
    """Point d'entree public -- voir docs/superpowers/specs/2026-10-22-
    gold-bot-risk-gate-design.md. `min_sample_size` sous ce seuil ->
    verdict "insufficient_history", NE bloque jamais le trade (un
    echantillon trop petit ne permet de conclure dans aucun sens).
    Au-dela, verdict "unfavorable" (bloque) si et seulement si
    l'esperance moyenne sur l'echantillon est negative ou nulle --
    critere simple et objectif, pas de combinaison taux de reussite/
    multiple de R arbitraire (voir la spec pour la justification).

    Deduplication des signaux chevauchants : _find_historical_entries
    renvoie TOUTE bougie 15min ou la condition d'entree est vraie --
    si le marche reste plusieurs bougies consecutives au-dela de la
    bande vwap +/- entry_sigma*std, chacune compte comme une
    "entree" distincte alors que le bot reel n'aurait ouvert qu'UNE
    position (voir bot.decide_and_act_vwap : une position deja
    ouverte bloque toute recherche de nouveau signal jusqu'a sa
    cloture). Reproduit ce comportement ici : les entrees sont
    parcourues dans l'ordre chronologique (deja garanti par
    _find_historical_entries), et toute entree dont `entry_time`
    tombe avant la cloture simulee de la position precedente est
    ignoree -- sans cela, un meme mouvement de marche serait compte
    plusieurs fois comme des trades independants, biaisant
    artificiellement l'echantillon."""
    entries = _find_historical_entries(candles_5min, direction, symbol=symbol)
    if len(entries) < min_sample_size:
        return {"sample_size": len(entries), "win_rate_pct": None, "avg_return_pct": None,
                "verdict": "insufficient_history"}

    outcomes = []
    blocked_until = None
    for entry in entries:
        entry_dt = datetime.fromisoformat(entry["entry_time"].replace(" ", "T")).replace(tzinfo=timezone.utc)
        if blocked_until is not None and entry_dt < blocked_until:
            continue
        # N'exclut QUE les bougies dont le "time" est parsable et <=
        # entry_dt -- une bougie sans "time" exploitable (jamais le cas
        # pour de vraies donnees 5min, mais vrai pour un fixture de test
        # minimal comme [{"dummy": True}]) est conservee plutot que de
        # faire planter le filtre : ce module ne doit jamais lever sur
        # un historique partiel ou mal forme, voir le contrat
        # "insufficient_history ne bloque jamais le trade" ci-dessus.
        after = []
        for c in candles_5min:
            c_time = c.get("time")
            if c_time is not None:
                try:
                    c_dt = datetime.fromisoformat(c_time.replace(" ", "T")).replace(tzinfo=timezone.utc)
                    if c_dt <= entry_dt:
                        continue
                except ValueError:
                    pass
            after.append(c)
        if not after:
            continue
        outcome = _simulate_exit(
            after, direction, entry["entry_price"], entry["stop_loss"], entry["entry_time"], symbol=symbol)
        outcomes.append(outcome)
        blocked_until = datetime.fromisoformat(outcome["close_time"].replace(" ", "T")).replace(tzinfo=timezone.utc)

    if len(outcomes) < min_sample_size:
        return {"sample_size": len(outcomes), "win_rate_pct": None, "avg_return_pct": None,
                "verdict": "insufficient_history"}

    avg_return_pct = sum(o["return_pct"] for o in outcomes) / len(outcomes)
    win_rate_pct = sum(1 for o in outcomes if o["return_pct"] > 0) / len(outcomes) * 100
    verdict = "unfavorable" if avg_return_pct <= 0 else "favorable"
    return {"sample_size": len(outcomes), "win_rate_pct": win_rate_pct,
            "avg_return_pct": avg_return_pct, "verdict": verdict}
