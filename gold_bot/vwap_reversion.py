# gold_bot/vwap_reversion.py
# Signal scalping retour-a-la-VWAP filtre par regime (EMA200), sortie par
# stop suiveur EMA50 -- voir docs/superpowers/specs/2026-09-30-gold-bot-
# vwap-reversion-design.md. Reproduit a l'identique le moteur valide
# empiriquement la nuit du 2026-09-29 sur 8.5 ans de donnees XAU/USD
# reelles (reserve statistique documentee dans le spec : le resultat
# global est porte par 3 trades extremes sur 165).
from datetime import datetime, timezone

EMA200_PERIOD = 200
EMA50_PERIOD = 50
ENTRY_SIGMA = 2.0
STOP_SIGMA = 3.0
SESSION_END_HOUR_UTC = 22
MIN_BARS_INTO_SESSION = 12
MIN_DISTANCE_PCT = 0.002


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
