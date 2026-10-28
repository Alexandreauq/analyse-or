# gold_bot/fibonacci_reversion.py
# Moteur de rebond sur retracement de Fibonacci pour l'instance FTMO --
# voir docs/superpowers/specs/2026-10-28-gold-bot-fibonacci-ftmo-design.md.
# Paramètres walk-forward validés (docs/superpowers/research/
# gold_bot_fibonacci_seasonality/, gold_bot_deepen_winners/,
# gold_bot_deepen_fibonacci/, gold_bot_validate_fib_trend_filter/) --
# copiés tels quels, jamais re-optimisés ici.
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

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
CANDLE_INTERVAL_MINUTES = 5


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


def _trend_strength_for_filter(candles: list[dict], historical_candles: list[dict] | None,
                                today: str) -> float | None:
    """Force de tendance a appliquer au filtre des signaux du jour
    `today` -- celle du dernier jour calendaire COMPLET avant `today`,
    jamais celle de `today` lui-meme (qui serait calculee sur une
    journee encore partielle en production, contrairement a la
    recherche de validation qui taguait chaque signal avec la cloture
    FINALE de son propre jour -- un lookahead que seul un backtest peut
    se permettre). `candles` seul (fenetre glissante recente, ~quelques
    jours) ne couvre jamais TREND_WINDOW_DAYS jours complets a lui
    seul -- `historical_candles` (fenetre plus large, deja recuperee
    par gold_bot.loop pour le filtre de cotes historiques de l'autre
    moteur) est necessaire pour avoir une vraie mesure. None si
    l'historique fusionne ne couvre pas STRICTEMENT PLUS de
    TREND_WINDOW_DAYS jours complets avant `today` -- a exactement
    TREND_WINDOW_DAYS jours, le jour retenu serait lui-meme le tout
    premier jour mesurable de daily_trend_strength, qui lui renvoie
    encore son 0.0 par defaut (historique insuffisant), pas une
    vraie mesure -- un signal sans mesure fiable de la tendance ne
    doit jamais etre laisse passer sans filtre."""
    merged = list(historical_candles or []) + list(candles)
    strength_by_day = daily_trend_strength(merged, window_days=TREND_WINDOW_DAYS)
    days_before_today = sorted(d for d in strength_by_day if d < today)
    if len(days_before_today) <= TREND_WINDOW_DAYS:
        return None
    return strength_by_day[days_before_today[-1]]


def latest_state(candles: list[dict], historical_candles: list[dict] | None = None) -> dict | None:
    """État de la dernière bougie : prix, niveau/biais Fibonacci, ATR,
    force de tendance du jour (voir _trend_strength_for_filter), heure
    UTC. None si l'historique est insuffisant pour l'ATR (le niveau
    Fibonacci, lui, peut être None individuellement -- c'est un état
    valide, pas une erreur)."""
    if not candles:
        return None
    atr = compute_atr(candles)
    i = len(candles) - 1
    if atr[i] is None:
        return None
    level, bias = levels_fibonacci(candles)
    c = candles[i]
    t = datetime.strptime(c["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    today = c["time"][:10]
    return {
        "close": c["close"], "high": c["high"], "low": c["low"],
        "level": level[i], "bias": bias[i], "atr": atr[i],
        "trend_strength": _trend_strength_for_filter(candles, historical_candles, today),
        "hour_utc": t.hour,
    }


def compute_trailing_stop(candles: list[dict], direction: str, entry_time_iso: str,
                           entry_price: float, current_stop_loss: float) -> float | None:
    """Stop suiveur sans état côté bot -- recalcule tout depuis les
    bougies fournies, comme vwap_reversion.compute_trailing_stop. Le
    stop INITIAL (utilisé pour R, l'activation à 1R) est recalculé
    depuis le niveau/ATR de la bougie d'ENTRÉE elle-même, jamais depuis
    `current_stop_loss` (qui peut déjà avoir été déplacé) -- même
    principe que le moteur VWAP. Une fois actif (excursion favorable
    >= 1R), le stop suit le meilleur prix atteint depuis l'entrée ∓
    TRAIL_BUFFER_ATR_MULT × l'ATR CAPTURÉ À L'ENTRÉE (pas l'ATR
    courant). Ne resserre jamais au-delà du prix d'entrée, ne recule
    jamais."""
    atr = compute_atr(candles)
    level, bias = levels_fibonacci(candles)

    # Le remplissage reel se produit APRES la cloture de la bougie
    # signal (quelques secondes a ~1 minute plus tard, pendant la
    # bougie suivante) -- comparer sur l'heure d'OUVERTURE de chaque
    # bougie choisirait donc systematiquement la bougie suivante (son
    # ouverture est <= entry_dt des que le remplissage a eu lieu),
    # decalant tout d'un cran par rapport a atr[i]/level[i] utilises
    # par decide_and_act_fibonacci pour calculer CE signal. Comparer
    # sur l'heure de CLOTURE (ouverture + CANDLE_INTERVAL_MINUTES)
    # retrouve la bougie signal elle-meme -- trouve en revue finale de
    # branche, 2026-10-28.
    entry_dt = datetime.fromisoformat(entry_time_iso.replace("Z", "+00:00"))
    entry_idx = None
    for i, c in enumerate(candles):
        c_close_dt = (datetime.strptime(c["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                      + timedelta(minutes=CANDLE_INTERVAL_MINUTES))
        if c_close_dt <= entry_dt:
            entry_idx = i
        else:
            break
    if entry_idx is None:
        return None

    atr_entry = atr[entry_idx]
    entry_level = level[entry_idx]
    entry_bias = bias[entry_idx]
    if atr_entry is None or atr_entry <= 0 or entry_level is None or entry_bias != direction:
        return None

    initial_stop = (entry_level - STOP_BUFFER_ATR_MULT * atr_entry if direction == "achat"
                     else entry_level + STOP_BUFFER_ATR_MULT * atr_entry)
    r = abs(entry_price - initial_stop)
    if r <= 0:
        return None

    since_entry = candles[entry_idx + 1:]
    if not since_entry:
        return None
    if direction == "achat":
        best_price = max(c["high"] for c in since_entry)
        best_excursion = best_price - entry_price
    else:
        best_price = min(c["low"] for c in since_entry)
        best_excursion = entry_price - best_price
    if best_excursion < r:
        return None

    if direction == "achat":
        candidate = max(entry_price, best_price - TRAIL_BUFFER_ATR_MULT * atr_entry)
        new_stop = max(current_stop_loss, candidate)
    else:
        candidate = min(entry_price, best_price + TRAIL_BUFFER_ATR_MULT * atr_entry)
        new_stop = min(current_stop_loss, candidate)

    if new_stop == current_stop_loss:
        return None
    return new_stop
