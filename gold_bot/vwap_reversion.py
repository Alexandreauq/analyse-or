# gold_bot/vwap_reversion.py
# Signal scalping retour-a-la-VWAP filtre par regime (EMA200), sortie par
# stop suiveur EMA50 -- voir docs/superpowers/specs/2026-09-30-gold-bot-
# vwap-reversion-design.md et son addendum (2026-09-30, stop suiveur +
# recalibrage entree/stop). Reproduit a l'identique le moteur valide par
# decoupage train (2018-2023) / test (2024-2026, jamais vu pendant
# l'optimisation) : positif sur les 3 annees individuelles du test,
# reste positif sans son meilleur trade -- reserve qui persiste : les 10
# plus gros trades sur 212 pesent 224% du gain net (le trade "moyen"
# reste legerement perdant).
from datetime import datetime, timezone

EMA200_PERIOD = 200
EMA50_PERIOD = 50
ENTRY_SIGMA = 1.5
STOP_SIGMA = 2.5
# Marge de securite entre l'EMA50 et le stop suiveur, en multiples de
# l'ecart-type VWAP au moment de l'entree (evite un stop colle
# exactement sur l'EMA50, qui se ferait toucher au moindre bruit).
# Recalibre de 0.5 a 0.25 le 2026-09-30 apres une validation
# walk-forward (6 fenetres roulantes independantes, entrainement
# uniquement sur le passe de chaque fenetre) : 0.25 a ete choisie de
# facon constante par les 6 fenetres d'entrainement, jamais une autre
# valeur -- signe de stabilite, pas de hasard.
TRAIL_BUFFER_SIGMA = 0.25
SESSION_END_HOUR_UTC = 22
MIN_BARS_INTO_SESSION = 12
MIN_DISTANCE_PCT = 0.002
# Prise de profit partielle -- ajoutee le 2026-09-30 apres validation
# walk-forward (6 fenetres roulantes) : fermer PARTIAL_TP_PCT de la
# position a PARTIAL_TP_R x la distance entree-stop, laisser le reste
# ("jambe runner") courir sans plafond via le stop suiveur -- a
# transforme 2 des 6 fenetres de test (2021, 2023) de legerement
# negatives en positives, et a nettement reduit le drawdown sur 5 des 6
# fenetres. PARTIAL_TP_PCT = 0.0 desactiverait la prise partielle
# (tout le volume part en jambe runner, comportement d'avant cet ajout).
PARTIAL_TP_PCT = 0.7
PARTIAL_TP_R = 2.0


def resample_15min(candles_5min: list[dict]) -> list[dict]:
    """Regroupe des bougies 5min (format gold_bot.confluence :
    time/open/high/low/close/tick_volume/spread, "time" au format
    "YYYY-MM-DD HH:MM:SS") en bougies 15min (bucket = multiple de 15min
    de l'heure, en UTC)."""
    buckets: dict[str, dict] = {}
    for c in candles_5min:
        t = datetime.fromisoformat(c["time"].replace(" ", "T")).replace(tzinfo=timezone.utc)
        bucket_minute = (t.minute // 15) * 15
        bucket_t = t.replace(minute=bucket_minute, second=0, microsecond=0)
        key = bucket_t.isoformat()
        b = buckets.setdefault(key, {
            "time": key, "open": c["open"], "high": c["high"], "low": c["low"],
            "close": c["close"], "tick_volume": 0,
        })
        b["high"] = max(b["high"], c["high"])
        b["low"] = min(b["low"], c["low"])
        b["close"] = c["close"]
        b["tick_volume"] += c.get("tick_volume", 0)
    return [buckets[key] for key in sorted(buckets.keys())]


def compute_indicators(candles_15min: list[dict]) -> list[dict]:
    """Ajoute vwap/vwap_std/ema200/ema50/hour_utc/bars_into_session à
    chaque bougie (modifie la liste en place, la renvoie aussi). VWAP +
    écart-type : réinitialisés chaque jour calendaire UTC. EMA200/EMA50 :
    continues, jamais réinitialisées."""
    ema200 = None
    ema50 = None
    k200 = 2.0 / (EMA200_PERIOD + 1)
    k50 = 2.0 / (EMA50_PERIOD + 1)
    cum_pv = 0.0
    cum_v = 0.0
    cum_pv2 = 0.0
    current_day = None
    bars_into_session = 0

    for c in candles_15min:
        t = datetime.fromisoformat(c["time"])
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        day = t.date()
        if day != current_day:
            current_day = day
            cum_pv = 0.0
            cum_v = 0.0
            cum_pv2 = 0.0
            bars_into_session = 0
        bars_into_session += 1

        typical_price = (c["high"] + c["low"] + c["close"]) / 3.0
        vol = max(c.get("tick_volume", 0), 1)
        cum_pv += typical_price * vol
        cum_v += vol
        cum_pv2 += vol * typical_price * typical_price
        vwap = cum_pv / cum_v
        variance = max(0.0, (cum_pv2 / cum_v) - vwap * vwap)
        vwap_std = variance ** 0.5

        ema200 = c["close"] if ema200 is None else c["close"] * k200 + ema200 * (1 - k200)
        ema50 = c["close"] if ema50 is None else c["close"] * k50 + ema50 * (1 - k50)

        c["vwap"] = vwap
        c["vwap_std"] = vwap_std
        c["ema200"] = ema200
        c["ema50"] = ema50
        c["hour_utc"] = t.hour
        c["bars_into_session"] = bars_into_session
    return candles_15min


def compute_trailing_stop(candles_5min: list[dict], direction: str, entry_time_iso: str,
                           entry_price: float, current_stop_loss: float) -> float | None:
    """Stop suiveur sans état côté bot -- tout est recalculé à chaque
    appel depuis les bougies réelles fournies (mêmes convention que
    latest_state). Retrouve la bougie 15min d'ouverture dans
    `candles_5min` (les positions de ce moteur se ferment toujours dans
    la même session UTC, donc l'entrée est toujours dans la fenêtre
    récupérée par le bot), recalcule R (distance entrée→stop initial, en
    utilisant le VWAP/écart-type de CETTE bougie -- identique à la
    formule d'entrée), vérifie si le plus haut (achat)/plus bas (vente)
    atteint depuis l'entrée dépasse R, et si oui calcule le stop
    suiveur (EMA50 de la dernière bougie ∓ TRAIL_BUFFER_SIGMA × l'écart-
    type AU MOMENT DE L'ENTRÉE). Ne renvoie une valeur que si elle
    resserre réellement le stop en faveur du trade (jamais de recul) --
    None sinon (rien à modifier), y compris si le seuil de rentabilité
    n'est pas encore atteint ou si l'historique fourni ne couvre pas la
    bougie d'entrée (position ouverte avant le début de la fenêtre
    récupérée)."""
    candles_15min = resample_15min(candles_5min)
    if len(candles_15min) < EMA200_PERIOD:
        return None
    compute_indicators(candles_15min)

    entry_dt = datetime.fromisoformat(entry_time_iso.replace("Z", "+00:00"))
    entry_idx = None
    for i, c in enumerate(candles_15min):
        bucket_dt = datetime.fromisoformat(c["time"])
        if bucket_dt <= entry_dt:
            entry_idx = i
        else:
            break
    if entry_idx is None:
        return None

    entry_candle = candles_15min[entry_idx]
    entry_std = entry_candle["vwap_std"]
    if entry_std <= 0:
        return None
    if direction == "achat":
        initial_stop = entry_candle["vwap"] - STOP_SIGMA * entry_std
    else:
        initial_stop = entry_candle["vwap"] + STOP_SIGMA * entry_std
    r = abs(entry_price - initial_stop)
    if r <= 0:
        return None

    # Strictement APRES la bougie d'entree : son propre high/low s'est
    # produit avant (ou pendant) la decision d'entree elle-meme, jamais
    # "depuis" l'entree -- trouve lors de l'ecriture des tests.
    since_entry = candles_15min[entry_idx + 1:]
    if not since_entry:
        return None
    if direction == "achat":
        best_excursion = max(c["high"] for c in since_entry) - entry_price
    else:
        best_excursion = entry_price - min(c["low"] for c in since_entry)
    if best_excursion < r:
        return None

    ema50 = candles_15min[-1]["ema50"]
    if direction == "achat":
        candidate = max(entry_price, ema50 - TRAIL_BUFFER_SIGMA * entry_std)
        new_stop = max(current_stop_loss, candidate)
    else:
        candidate = min(entry_price, ema50 + TRAIL_BUFFER_SIGMA * entry_std)
        new_stop = min(current_stop_loss, candidate)

    if new_stop == current_stop_loss:
        return None
    return new_stop


def latest_state(candles_5min: list[dict]) -> dict | None:
    """Rééchantillonne, calcule les indicateurs, renvoie l'état complet
    de la dernière bougie 15min disponible -- None si l'historique fourni
    ne couvre pas assez de bougies 15min pour une EMA200 représentative."""
    candles_15min = resample_15min(candles_5min)
    if len(candles_15min) < EMA200_PERIOD:
        return None
    compute_indicators(candles_15min)
    last = candles_15min[-1]
    return {
        "close": last["close"], "vwap": last["vwap"], "vwap_std": last["vwap_std"],
        "ema200": last["ema200"], "ema50": last["ema50"], "hour_utc": last["hour_utc"],
        "bars_into_session": last["bars_into_session"],
    }
