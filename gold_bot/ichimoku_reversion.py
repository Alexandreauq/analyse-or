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


def latest_state(candles_5min: list[dict]) -> dict | None:
    """Etat Ichimoku de la derniere bougie H1 complete, a partir d'une
    fenetre de bougies 5min (meme fenetre glissante que gold_bot.loop
    fournit aux autres moteurs). None si l'historique H1 resultant est
    trop court (< MIN_H1_CANDLES) pour que le nuage soit defini sur la
    derniere bougie."""
    candles_h1 = resample_h1(candles_5min)
    if len(candles_h1) < MIN_H1_CANDLES:
        return None
    atr = compute_atr_h1(candles_h1)
    ind = compute_ichimoku(candles_h1)
    i = len(candles_h1) - 1
    if atr[i] is None or ind["senkou_a"][i] is None or ind["senkou_b"][i] is None:
        return None
    last = candles_h1[i]
    return {
        "close": last["close"], "high": last["high"], "low": last["low"],
        "atr": atr[i],
        "tenkan": ind["tenkan"][i], "tenkan_prev": ind["tenkan"][i - 1],
        "kijun": ind["kijun"][i], "kijun_prev": ind["kijun"][i - 1],
        "senkou_a": ind["senkou_a"][i], "senkou_b": ind["senkou_b"][i],
        "hour_utc": _parse(last["time"]).hour,
    }


def signal(state: dict) -> str | None:
    """Croisement Tenkan/Kijun confirme par la position du prix vs nuage
    (meme signal que docs/superpowers/research/gold_bot_ichimoku_macd/
    generate_trend_trades.py::make_ichimoku_signals, evalue ici sur un
    seul etat courant plutot qu'en boucle sur tout l'historique)."""
    cloud_top = max(state["senkou_a"], state["senkou_b"])
    cloud_bot = min(state["senkou_a"], state["senkou_b"])
    crossed_up = state["tenkan_prev"] <= state["kijun_prev"] and state["tenkan"] > state["kijun"]
    crossed_down = state["tenkan_prev"] >= state["kijun_prev"] and state["tenkan"] < state["kijun"]
    if crossed_up and state["close"] > cloud_top:
        return "achat"
    if crossed_down and state["close"] < cloud_bot:
        return "vente"
    return None


def compute_trailing_stop(candles_5min: list[dict], direction: str, entry_time_iso: str,
                           entry_price: float, current_stop_loss: float) -> float | None:
    """Stop suiveur ancre sur le kijun courant +/- TRAIL_BUFFER_ATR_MULT*atr
    CAPTURE A L'ENTREE (jamais l'ATR courant -- meme principe que
    fibonacci_reversion.compute_trailing_stop/vwap_reversion.compute_trailing_stop),
    actif seulement une fois l'excursion favorable >= 1R, ou R est calcule
    depuis le stop INITIAL (kijun/atr de la bougie H1 d'entree -- stop_ref_fn
    du moteur de recherche -- jamais depuis current_stop_loss qui peut deja
    avoir ete deplace). Ne se resserre jamais que dans le sens favorable.
    None si aucun changement ou si l'historique/les index d'entree sont
    insuffisants."""
    candles_h1 = resample_h1(candles_5min)
    if len(candles_h1) < MIN_H1_CANDLES:
        return None
    atr = compute_atr_h1(candles_h1)
    ind = compute_ichimoku(candles_h1)
    i = len(candles_h1) - 1
    kijun_i = ind["kijun"][i]
    if kijun_i is None:
        return None

    entry_time = _parse(entry_time_iso) if isinstance(entry_time_iso, str) else entry_time_iso

    entry_5min_idx = None
    for idx, c in enumerate(candles_5min):
        if _parse(c["time"]) == entry_time:
            entry_5min_idx = idx
            break
    if entry_5min_idx is None:
        return None
    since_entry = candles_5min[entry_5min_idx + 1:]
    if not since_entry:
        return None

    entry_bucket_key = entry_time.replace(minute=0, second=0, microsecond=0).replace(tzinfo=None).isoformat()
    entry_h1_idx = None
    for idx, c in enumerate(candles_h1):
        if c["time"] == entry_bucket_key:
            entry_h1_idx = idx
            break
    if entry_h1_idx is None:
        return None
    atr_entry = atr[entry_h1_idx]
    kijun_entry = ind["kijun"][entry_h1_idx]
    if atr_entry is None or kijun_entry is None:
        return None

    initial_stop = (kijun_entry - STOP_BUFFER_ATR_MULT * atr_entry if direction == "achat"
                     else kijun_entry + STOP_BUFFER_ATR_MULT * atr_entry)
    r_distance = abs(entry_price - initial_stop)
    if r_distance == 0:
        return None

    if direction == "achat":
        best_price = max(c["high"] for c in since_entry)
        excursion_r = (best_price - entry_price) / r_distance
    else:
        best_price = min(c["low"] for c in since_entry)
        excursion_r = (entry_price - best_price) / r_distance
    if excursion_r < 1.0:
        return None

    if direction == "achat":
        candidate = max(entry_price, kijun_i - TRAIL_BUFFER_ATR_MULT * atr_entry)
        new_stop = max(current_stop_loss, candidate)
    else:
        candidate = min(entry_price, kijun_i + TRAIL_BUFFER_ATR_MULT * atr_entry)
        new_stop = min(current_stop_loss, candidate)
    return None if new_stop == current_stop_loss else new_stop
