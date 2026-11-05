"""Moteur de renversement Ichimoku deux-jambes, calibre sur XAGUSD
(argent). Voir docs/superpowers/specs/
2026-11-05-gold-bot-ichimoku-xagusd-ai-veto-design.md.

Walk-forward valide : tenkan=9/kijun=30/target_r=2.0 (2 parametres
stables sur 6/6 fenetres annuelles 2021-2026) -- le moteur le plus
robuste trouve dans toute la recherche FTMO de ce projet, voir
docs/superpowers/research/gold_bot_metaux_silver_engines/README.md.
Port direct de ce chantier de recherche en code de production causal
(une decision par cycle, pas un backtest) -- indicateurs et execution
deux-jambes repris sans reinterpretation."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone

SYMBOL = "XAGUSD"
CONTRACT_SIZE = 5000.0  # verifie empiriquement via broker.get_symbol_specification
MAX_VOLUME = 20.0
VOLUME_STEP = 0.01
MIN_VOLUME = 0.01
ATR_PERIOD = 14
TENKAN_PERIOD = 9
KIJUN_PERIOD = 30
SENKOU_B_PERIOD = 52  # standard Ichimoku, non grid-searche
CLOUD_SHIFT = 26  # standard Ichimoku, non grid-searche
STOP_BUFFER_ATR_MULT = 0.5
TRAIL_BUFFER_ATR_MULT = 0.5  # meme valeur que STOP_BUFFER_ATR_MULT, non grid-searchee separement (limite documentee dans la recherche)
TARGET_R = 2.0
PARTIAL_TP_PCT = 0.7
MIN_DISTANCE_PCT = 0.002
SESSION_END_HOUR_UTC = 22  # meme convention que vwap_reversion/fibonacci_reversion

# Marge de securite production (le backtest de recherche n'avait pas
# besoin de ce garde-fou, demarrant toujours avec des annees d'historique
# deja disponibles) : il faut au moins SENKOU_B_PERIOD + CLOUD_SHIFT
# bougies H1 pour que le nuage (senkou_a/senkou_b) soit defini sur la
# derniere bougie -- sinon latest_state() refuse de produire un etat.
MIN_H1_CANDLES = SENKOU_B_PERIOD + CLOUD_SHIFT  # 78 bougies H1 ~ 3.25 jours


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace(" ", "T")).replace(tzinfo=timezone.utc)


def resample_h1(candles_5min: list[dict]) -> list[dict]:
    """Reechantillonne des bougies 5min en bougies H1 (bucket = heure
    UTC). Causal : `candles_5min` est toujours une fenetre glissante de
    bougies DEJA closes (gold_bot.loop ne fournit jamais de bougie en
    formation) -- le dernier bucket H1 produit ici est donc toujours
    complet, jamais partiel."""
    buckets: dict[str, dict] = {}
    for c in candles_5min:
        t = _parse(c["time"])
        bucket_t = t.replace(minute=0, second=0, microsecond=0)
        key = bucket_t.replace(tzinfo=None).isoformat()
        b = buckets.setdefault(key, {
            "time": key, "open": c["open"], "high": c["high"], "low": c["low"],
            "close": c["close"], "tick_volume": 0,
        })
        b["high"] = max(b["high"], c["high"])
        b["low"] = min(b["low"], c["low"])
        b["close"] = c["close"]
        b["tick_volume"] += c.get("tick_volume", 0)
    return [buckets[key] for key in sorted(buckets.keys())]


def compute_atr_h1(candles_h1: list[dict], period: int = ATR_PERIOD) -> list[float | None]:
    """ATR causal (Wilder) -- meme convention que partout ailleurs dans
    ce depot (ex. docs/superpowers/research/.../generate_trend_trades.py::compute_atr)."""
    atrs: list[float | None] = [None] * len(candles_h1)
    prev_close = None
    tr_sum = 0.0
    atr = None
    k = 1.0 / period
    for i, c in enumerate(candles_h1):
        high, low, close = c["high"], c["low"], c["close"]
        tr = (high - low) if prev_close is None else max(high - low, abs(high - prev_close), abs(low - prev_close))
        if atr is None:
            tr_sum += tr
            if i == period - 1:
                atr = tr_sum / period
                atrs[i] = atr
        else:
            atr = tr * k + atr * (1 - k)
            atrs[i] = atr
        prev_close = close
    return atrs


def _rolling_high_low(candles: list[dict], window: int) -> tuple[list[float | None], list[float | None]]:
    """Plus haut/plus bas glissant INCLUANT la bougie courante (definition
    standard Tenkan/Kijun -- different d'un niveau de cassure qui
    exclurait la bougie courante). Deque monotone, O(n) amorti. Toujours
    defini des l'indice 0 (fenetre partielle sur les premieres bougies,
    exactement le comportement du moteur de recherche original)."""
    n = len(candles)
    hi: list[float | None] = [None] * n
    lo: list[float | None] = [None] * n
    max_dq: deque[int] = deque()
    min_dq: deque[int] = deque()
    for i in range(n):
        h, l = candles[i]["high"], candles[i]["low"]
        while max_dq and candles[max_dq[-1]]["high"] <= h:
            max_dq.pop()
        max_dq.append(i)
        while min_dq and candles[min_dq[-1]]["low"] >= l:
            min_dq.pop()
        min_dq.append(i)
        while max_dq[0] <= i - window:
            max_dq.popleft()
        while min_dq[0] <= i - window:
            min_dq.popleft()
        hi[i] = candles[max_dq[0]]["high"]
        lo[i] = candles[min_dq[0]]["low"]
    return hi, lo


def compute_ichimoku(candles_h1: list[dict], tenkan_period: int = TENKAN_PERIOD,
                      kijun_period: int = KIJUN_PERIOD, senkou_b_period: int = SENKOU_B_PERIOD,
                      shift: int = CLOUD_SHIFT) -> dict:
    """Tenkan-sen/Kijun-sen/Senkou Span A/B sur bougies H1. Le nuage est
    decale de `shift` periodes en avant par definition (projection) --
    senkou_a[i]/senkou_b[i] representent deja le bord du nuage visible a
    la bougie i (calcule a partir de donnees disponibles a i-shift, donc
    causal par construction). None pour i < shift (rien de calculable
    avant)."""
    n = len(candles_h1)
    tenkan_hi, tenkan_lo = _rolling_high_low(candles_h1, tenkan_period)
    kijun_hi, kijun_lo = _rolling_high_low(candles_h1, kijun_period)
    senkou_b_hi, senkou_b_lo = _rolling_high_low(candles_h1, senkou_b_period)

    tenkan = [(h + l) / 2.0 for h, l in zip(tenkan_hi, tenkan_lo)]
    kijun = [(h + l) / 2.0 for h, l in zip(kijun_hi, kijun_lo)]
    senkou_a_raw = [(t + k) / 2.0 for t, k in zip(tenkan, kijun)]
    senkou_b_raw = [(h + l) / 2.0 for h, l in zip(senkou_b_hi, senkou_b_lo)]

    senkou_a: list[float | None] = [None] * n
    senkou_b: list[float | None] = [None] * n
    for i in range(n):
        src = i - shift
        if src >= 0:
            senkou_a[i] = senkou_a_raw[src]
            senkou_b[i] = senkou_b_raw[src]

    return {"tenkan": tenkan, "kijun": kijun, "senkou_a": senkou_a, "senkou_b": senkou_b}
