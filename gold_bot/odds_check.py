# gold_bot/odds_check.py
# Verification des cotes historiques avant l'ouverture d'un trade --
# voir docs/superpowers/specs/2026-10-22-gold-bot-risk-gate-design.md.
# Module pur vis-a-vis du RESEAU (aucun appel broker/MetaApi) : recoit
# un historique de bougies 5min deja recupere (par gold_bot.loop) et
# rejoue les memes mecaniques de decision/sortie que
# gold_bot.bot.decide_and_act_vwap, pour estimer les cotes reelles du
# meme type de signal dans le passe. Fait cependant de la lecture/
# ecriture de cache LOCALE (verdict memorise, voir
# historical_odds_for_setup_cached) -- meme precedent que
# gold_bot.risk.CircuitBreaker, qui persiste deja son propre etat
# localement depuis une fonction par ailleurs "pure" cote reseau.
import json
import os
from datetime import datetime, timedelta, timezone

import gold_bot.backtest as backtest
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


# Fenetre glissante de bougies 5min passee a compute_trailing_stop a
# chaque pas, pour reproduire fidelement ce qu'une boucle live voit
# reellement a un instant donne (gold_bot.loop.CANDLES_FETCH_LIMIT =
# 1000 ; valeur dupliquee ici plutot qu'importee pour eviter un cycle
# d'import odds_check -> loop -> bot -> odds_check, ce module etant
# repris par gold_bot.bot, lui-meme importe par gold_bot.loop).
TRAILING_STOP_WINDOW_SIZE = 1000


def _spread_cost_pct(entry_price: float, spread_points: float, symbol: str) -> float:
    """Cout du spread en % du prix d'entree, meme convention que
    gold_bot.backtest._compute_return. Seul XAUUSD a une taille de point
    verifiee (backtest.XAUUSD_POINT_SIZE, "coté à 2 décimales chez ce
    courtier" -- hypothese documentee dans backtest.py, jamais verifiee
    pour XAGUSD). Pour tout autre symbole, ne deduit rien plutot que
    d'appliquer une conversion non verifiee -- trouve en revue finale de
    branche (la spec promettait cette deduction, oubliee dans une
    version anterieure de ce module)."""
    if symbol != "XAUUSD":
        return 0.0
    return (spread_points * backtest.XAUUSD_POINT_SIZE) / entry_price * 100


def _simulate_exit(candles_5min: list[dict], entry_index: int, direction: str, entry_price: float,
                    initial_stop: float, entry_time: str, symbol: str = "XAUUSD") -> dict:
    """Rejoue la sortie reelle bougie par bougie a partir de
    `candles_5min[entry_index]` (la premiere bougie 5min A OU APRES la
    cloture du bucket 15min d'entree -- voir historical_odds_for_setup,
    qui calcule cet index ; ne JAMAIS passer l'index d'une bougie a
    l'interieur du bucket d'entree lui-meme, biais d'anticipation trouve
    en revue finale de branche).

    A chaque pas, le stop suiveur de la jambe runner est recalcule via
    vwap_reversion.compute_trailing_stop sur une fenetre glissante de
    jusqu'a TRAILING_STOP_WINDOW_SIZE VRAIES bougies 5min se terminant a
    la bougie courante (`candles_5min[max(0, i-WINDOW+1):i+1]`) --
    EXACTEMENT ce qu'une boucle live lui donnerait a ce moment-la (voir
    gold_bot.loop.CANDLES_FETCH_LIMIT, gold_bot.bot.decide_and_act_vwap).
    Une version anterieure de cette fonction ne lui donnait qu'une
    bougie d'entree synthetique plus les bougies depuis l'entree : avec
    au plus ~88 bougies 15min dans une session (0h-22h UTC), le seuil
    EMA200_PERIOD=200 de compute_trailing_stop n'etait JAMAIS atteint en
    pratique, et le stop suiveur ne suivait donc jamais -- trouve en
    revue finale de branche. Consequence residuelle acceptee : les toutes
    premieres entrees de la fenetre de 90 jours recuperee
    (HISTORICAL_ODDS_WINDOW_DAYS, gold_bot.loop), qui n'ont pas encore
    ~2 jours d'historique REEL avant elles dans les donnees recuperees,
    degradent de la meme facon (compute_trailing_stop renvoie None, le
    stop reste fixe jusqu'a ce que suffisamment d'historique existe) --
    c'est attendu (meme degradation qu'un bot qui demarre), pas une
    regression de ce correctif. Autre ecart residuel documente : l'EMA200
    calculee ici repart de zero a chaque fenetre glissante plutot que
    d'etre continue depuis le tout debut de l'historique recupere comme
    en live continu -- poids residuel faible (~3.6% au bout de 200
    bougies) mais reel, accepte pour eviter de repasser des milliers de
    bougies a chaque pas (voir aussi historical_odds_for_setup_cached,
    qui rend ce cout tolerable en ne le payant qu'une fois par
    rafraichissement du cache d'historique plutot qu'a chaque cycle).

    Modelise les DEUX jambes de decide_and_act_vwap quand
    get_symbol_params(symbol)["partial_tp_pct"] > 0 (XAUUSD et XAGUSD
    aujourd'hui) : la jambe partielle (stop FIXE a `initial_stop`, cible
    fixe a `entry_price +/- partial_tp_r * distance`, stop gagnant en
    cas de toucher simultane avec sa propre cible) et la jambe runner
    (stop suiveur ci-dessus, jamais de cible). Les deux se cloturent
    aussi de force a la fin de session (SESSION_END_HOUR_UTC, comme
    TOUTES les positions `matching` dans decide_and_act_vwap), ou a la
    fin de l'historique fourni.

    Le retour renvoye (`return_pct`) est la moyenne ponderee des deux
    jambes par `partial_tp_pct`, APRES deduction du cout du spread (voir
    _spread_cost_pct) -- la spec promettait cette deduction, oubliee
    dans une version anterieure. Si `partial_tp_pct <= 0`, tout le
    volume reste en jambe unique runner.

    `close_reason`/`close_price` refletent la jambe RUNNER specifiquement
    (pas un melange). `close_time`, en revanche, est le PLUS TARDIF des
    deux cloture (max partielle/runner) -- avec le stop suiveur
    desormais reellement actif, la jambe partielle peut survivre plus
    longtemps que le runner (elle ne se cloture que sur son propre stop
    fixe, sa cible fixe, ou la fin de session), et c'est elle qui
    determine la duree reelle de blocage d'un nouveau signal cote
    historical_odds_for_setup (decide_and_act_vwap bloque tant que
    N'IMPORTE QUELLE jambe `matching` reste ouverte) -- corrige une
    approximation de la version precedente, trouvee en revue finale."""
    is_achat = direction == "achat"
    params = vwap_reversion.get_symbol_params(symbol)
    distance = abs(entry_price - initial_stop)
    partial_tp_pct = params["partial_tp_pct"]
    has_partial_leg = partial_tp_pct > 0
    if has_partial_leg:
        partial_target = (entry_price + params["partial_tp_r"] * distance if is_achat
                           else entry_price - params["partial_tp_r"] * distance)

    def _return_pct(price, spread_points):
        raw = ((price - entry_price) / entry_price * 100 if is_achat
               else (entry_price - price) / entry_price * 100)
        return raw - _spread_cost_pct(entry_price, spread_points, symbol)

    # Normalise entry_time en ISO8601 explicitement offset-aware avant de
    # le transmettre a compute_trailing_stop : celle-ci compare ce
    # timestamp aux cles de resample_15min (toujours offset-aware, voir sa
    # docstring), alors qu'une chaine "YYYY-MM-DD HH:MM:SS" (convention
    # des bougies 5min brutes) est naive une fois parsee -- sans cette
    # normalisation, compute_trailing_stop leve TypeError (comparaison
    # naive/aware) des que le seuil EMA200_PERIOD est reellement atteint,
    # chemin jamais exerce avant ce correctif (le seuil n'etait jamais
    # atteint, voir le defaut critique traite par ce module). Sans effet
    # quand entry_time porte deja un fuseau (cas reel via
    # historical_odds_for_setup, qui transmet le "time" d'une bougie
    # 15min resamplee, toujours offset-aware).
    entry_dt_for_trail = datetime.fromisoformat(entry_time.replace(" ", "T"))
    if entry_dt_for_trail.tzinfo is None:
        entry_dt_for_trail = entry_dt_for_trail.replace(tzinfo=timezone.utc)
    entry_time_for_trail = entry_dt_for_trail.isoformat()

    stop = initial_stop
    partial_return = partial_close_time = None
    runner_return = runner_close_reason = runner_close_price = runner_close_time = None

    for i in range(entry_index, len(candles_5min)):
        candle = candles_5min[i]
        hour = datetime.fromisoformat(candle["time"].replace(" ", "T")).replace(tzinfo=timezone.utc).hour
        session_ended = hour >= vwap_reversion.SESSION_END_HOUR_UTC
        spread = candle.get("spread", 0.0)

        if has_partial_leg and partial_return is None:
            sl_hit = candle["low"] <= initial_stop if is_achat else candle["high"] >= initial_stop
            if sl_hit:
                partial_return, partial_close_time = _return_pct(initial_stop, spread), candle["time"]
            else:
                tp_hit = candle["high"] >= partial_target if is_achat else candle["low"] <= partial_target
                if tp_hit:
                    partial_return, partial_close_time = _return_pct(partial_target, spread), candle["time"]
                elif session_ended:
                    partial_return, partial_close_time = _return_pct(candle["close"], spread), candle["time"]

        if runner_return is None:
            sl_hit = candle["low"] <= stop if is_achat else candle["high"] >= stop
            if sl_hit:
                runner_return = _return_pct(stop, spread)
                runner_close_reason, runner_close_price, runner_close_time = "stop_touche", stop, candle["time"]
            elif session_ended:
                runner_return = _return_pct(candle["close"], spread)
                runner_close_reason = "fin_session"
                runner_close_price, runner_close_time = candle["close"], candle["time"]
            else:
                window = candles_5min[max(0, i - TRAILING_STOP_WINDOW_SIZE + 1):i + 1]
                new_stop = vwap_reversion.compute_trailing_stop(
                    window, direction, entry_time_for_trail, entry_price, stop, symbol=symbol)
                if new_stop is not None:
                    stop = new_stop

        if runner_return is not None and (not has_partial_leg or partial_return is not None):
            break

    if runner_return is None:
        last = candles_5min[-1]
        runner_return = _return_pct(last["close"], last.get("spread", 0.0))
        runner_close_reason, runner_close_price, runner_close_time = "fin_historique", last["close"], last["time"]
    if has_partial_leg and partial_return is None:
        last = candles_5min[-1]
        partial_return, partial_close_time = _return_pct(last["close"], last.get("spread", 0.0)), last["time"]

    blended_return = (partial_tp_pct * partial_return + (1 - partial_tp_pct) * runner_return
                       if has_partial_leg else runner_return)
    dedup_close_time = max(runner_close_time, partial_close_time) if has_partial_leg else runner_close_time

    return {"close_reason": runner_close_reason, "close_price": runner_close_price,
            "return_pct": blended_return, "close_time": dedup_close_time}


def historical_odds_for_setup(candles_5min: list[dict], direction: str, symbol: str = "XAUUSD",
                               min_sample_size: int = 20) -> dict:
    """Point d'entree public -- voir docs/superpowers/specs/2026-10-22-
    gold-bot-risk-gate-design.md. `min_sample_size` sous ce seuil ->
    verdict "insufficient_history", NE bloque jamais le trade. Au-dela,
    "unfavorable" (bloque) si et seulement si l'esperance moyenne nette
    (spread deduit, voir _simulate_exit) est negative ou nulle.

    Pour chaque entree detectee par _find_historical_entries (dont
    `entry_time` est le DEBUT du bucket 15min, voir sa docstring), la
    simulation ne doit commencer qu'a la toute premiere bougie 5min A OU
    APRES la CLOTURE de ce bucket (`entry_time + 15min`) -- jamais
    pendant le bucket lui-meme, qui a deja determine `entry_price` (son
    propre prix de cloture). Inclure ces bougies une deuxieme fois dans
    la simulation serait un biais d'anticipation -- trouve en revue
    finale de branche."""
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
        bucket_end = entry_dt + timedelta(minutes=15)
        entry_index = None
        for i, c in enumerate(candles_5min):
            c_time = c.get("time")
            if c_time is None:
                continue
            try:
                c_dt = datetime.fromisoformat(c_time.replace(" ", "T")).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if c_dt >= bucket_end:
                entry_index = i
                break
        if entry_index is None:
            continue
        outcome = _simulate_exit(
            candles_5min, entry_index, direction, entry["entry_price"], entry["stop_loss"],
            entry["entry_time"], symbol=symbol)
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


def _candles_fingerprint(candles_5min: list[dict]) -> str:
    """Identifiant bon marche (O(1), pas un hash de tout le contenu)
    pour detecter si `candles_5min` a change depuis le dernier appel --
    change des que loop.py rafraichit son cache d'historique (nouvelle
    premiere/derniere bougie ou nombre de bougies different), reste
    stable sinon (meme liste reutilisee a chaque cycle tant que le
    cache de loop.py n'expire pas, voir
    loop.HISTORICAL_ODDS_CACHE_MAX_AGE_SECONDS)."""
    if not candles_5min:
        return "empty"
    return f"{len(candles_5min)}:{candles_5min[0].get('time')}:{candles_5min[-1].get('time')}"


def _verdict_cache_path(symbol: str, direction: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         f"odds_verdict_cache_{symbol.lower()}_{direction}.json")


def historical_odds_for_setup_cached(candles_5min: list[dict], direction: str, symbol: str = "XAUUSD",
                                      min_sample_size: int = 20) -> dict:
    """Memoise historical_odds_for_setup par (symbol, direction,
    empreinte de candles_5min, voir _candles_fingerprint). Le replay
    complet (recalcul du stop suiveur sur une fenetre glissante REELLE a
    chaque bougie depuis le correctif de revue finale de branche, voir
    _simulate_exit) est devenu nettement plus couteux -- inutile de le
    refaire a chaque cycle (60s) tant que l'historique sous-jacent n'a
    pas change (rafraichi au plus une fois par 24h cote loop.py).
    gold_bot.bot.decide_and_act_vwap appelle CETTE fonction, jamais la
    version non memoisee, directement."""
    fingerprint = _candles_fingerprint(candles_5min)
    path = _verdict_cache_path(symbol, direction)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("fingerprint") == fingerprint:
                return cached["verdict"]
        except Exception:
            pass
    verdict = historical_odds_for_setup(candles_5min, direction, symbol=symbol, min_sample_size=min_sample_size)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"fingerprint": fingerprint, "verdict": verdict}, f)
    except OSError:
        pass
    return verdict
