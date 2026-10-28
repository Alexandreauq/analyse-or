# gold_bot/fibonacci_reversion.py
# Moteur de rebond sur retracement de Fibonacci pour l'instance FTMO --
# voir docs/superpowers/specs/2026-10-28-gold-bot-fibonacci-ftmo-design.md.
# Paramètres walk-forward validés (docs/superpowers/research/
# gold_bot_fibonacci_seasonality/, gold_bot_deepen_winners/,
# gold_bot_deepen_fibonacci/, gold_bot_validate_fib_trend_filter/) --
# copiés tels quels, jamais re-optimisés ici.
from collections import defaultdict, deque
from datetime import datetime, timezone

ATR_PERIOD = 14
TOUCH_ATR_MULT = 0.3
STOP_BUFFER_ATR_MULT = 0.5
TRAIL_BUFFER_ATR_MULT = 0.5
PARTIAL_TP_PCT = 0.7
TARGET_R = 1.5
FIB_WINDOW = 40
FIB_PCT = 0.618
TREND_WINDOW_DAYS = 20
TREND_FILTER_THRESHOLD = 0.05
SESSION_END_HOUR_UTC = 22
MIN_DISTANCE_PCT = 0.002


def compute_atr(candles: list[dict], period: int = ATR_PERIOD) -> list[float | None]:
    """ATR causal (Wilder simplifié en moyenne mobile exponentielle) --
    atr[i] n'utilise que les bougies <= i, jamais le futur."""
    atrs: list[float | None] = [None] * len(candles)
    prev_close = None
    tr_sum = 0.0
    atr = None
    k = 1.0 / period
    for i, c in enumerate(candles):
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


def levels_fibonacci(candles: list[dict], window: int = FIB_WINDOW,
                      fib_pct: float = FIB_PCT) -> tuple[list[float | None], list[str | None]]:
    """Pour chaque bougie i (causal, fenêtre des `window` bougies
    PRÉCÉDENTES) : le plus RÉCENT des deux extrêmes (haut/bas) de la
    fenêtre détermine le sens du mouvement -- le bas le plus récent
    signifie un mouvement haut->bas, donc on attend un rebond BAISSIER
    sur un retracement HAUT (niveau = bas + fib_pct*(haut-bas)), biais
    vente ; symétrique pour un haut le plus récent, biais achat."""
    n = len(candles)
    level: list[float | None] = [None] * n
    bias: list[str | None] = [None] * n
    max_dq: deque[int] = deque()
    min_dq: deque[int] = deque()
    for i in range(n):
        if i > 0 and max_dq and min_dq:
            hi_idx, lo_idx = max_dq[0], min_dq[0]
            hi, lo = candles[hi_idx]["high"], candles[lo_idx]["low"]
            rng = hi - lo
            if rng > 0 and hi_idx != lo_idx:
                if lo_idx > hi_idx:
                    level[i] = lo + fib_pct * rng
                    bias[i] = "vente"
                else:
                    level[i] = hi - fib_pct * rng
                    bias[i] = "achat"
        h, lo = candles[i]["high"], candles[i]["low"]
        while max_dq and candles[max_dq[-1]]["high"] <= h:
            max_dq.pop()
        max_dq.append(i)
        while min_dq and candles[min_dq[-1]]["low"] >= lo:
            min_dq.pop()
        min_dq.append(i)
        while max_dq[0] <= i - window:
            max_dq.popleft()
        while min_dq[0] <= i - window:
            min_dq.popleft()
    return level, bias


def daily_trend_strength(candles: list[dict], window_days: int = TREND_WINDOW_DAYS) -> dict[str, float]:
    """Rendement glissant sur `window_days` jours ouvrés (clôture du
    jour vs clôture `window_days` jours avant), par jour calendaire
    ("YYYY-MM-DD"). 0.0 tant que l'historique est insuffisant."""
    by_day: dict[str, list[dict]] = defaultdict(list)
    for c in candles:
        by_day[c["time"][:10]].append(c)
    days = sorted(by_day)
    daily_close = {d: by_day[d][-1]["close"] for d in days}
    closes = [daily_close[d] for d in days]
    strength: dict[str, float] = {}
    for i, d in enumerate(days):
        if i < window_days:
            strength[d] = 0.0
            continue
        ref = closes[i - window_days]
        strength[d] = (closes[i] - ref) / ref if ref else 0.0
    return strength
