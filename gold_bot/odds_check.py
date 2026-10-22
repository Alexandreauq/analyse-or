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
    `candles_after_entry` (5min, strictement apres la bougie d'entree),
    en reutilisant vwap_reversion.compute_trailing_stop a chaque pas
    (jamais une reimplementation separee du stop suiveur -- la fidelite
    aux vraies mecaniques de sortie est le point de ce module). Meme
    priorite de cloture que gold_bot.backtest.simulate_trades : stop
    touche d'abord (gagnant en cas de toucher simultane avec le TP de la
    jambe partielle, meme desambiguisation prudente), puis fin de
    session (SESSION_END_HOUR_UTC), puis fin de l'historique fourni
    (ferme au dernier prix connu plutot que d'etre ignore)."""
    is_achat = direction == "achat"
    stop = initial_stop
    history_so_far = [{"time": entry_time, "open": entry_price, "high": entry_price,
                        "low": entry_price, "close": entry_price, "tick_volume": 0, "spread": 0}]

    for candle in candles_after_entry:
        history_so_far.append(candle)
        hour = datetime.fromisoformat(candle["time"].replace(" ", "T")).replace(tzinfo=timezone.utc).hour

        sl_touched = candle["low"] <= stop if is_achat else candle["high"] >= stop
        if sl_touched:
            return_pct = ((stop - entry_price) / entry_price * 100 if is_achat
                           else (entry_price - stop) / entry_price * 100)
            return {"close_reason": "stop_touche", "close_price": stop, "return_pct": return_pct,
                    "close_time": candle["time"]}

        if hour >= vwap_reversion.SESSION_END_HOUR_UTC:
            return_pct = ((candle["close"] - entry_price) / entry_price * 100 if is_achat
                           else (entry_price - candle["close"]) / entry_price * 100)
            return {"close_reason": "fin_session", "close_price": candle["close"], "return_pct": return_pct,
                    "close_time": candle["time"]}

        new_stop = vwap_reversion.compute_trailing_stop(
            history_so_far, direction, entry_time, entry_price, stop, symbol=symbol)
        if new_stop is not None:
            stop = new_stop

    last_price = candles_after_entry[-1]["close"]
    return_pct = ((last_price - entry_price) / entry_price * 100 if is_achat
                   else (entry_price - last_price) / entry_price * 100)
    return {"close_reason": "fin_historique", "close_price": last_price, "return_pct": return_pct,
            "close_time": candles_after_entry[-1]["time"]}


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
        after = []
        for c in candles_5min:
            c_time = c.get("time")
            if c_time is not None:
                c_dt = datetime.fromisoformat(c_time.replace(" ", "T")).replace(tzinfo=timezone.utc)
                if c_dt <= entry_dt:
                    continue
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
