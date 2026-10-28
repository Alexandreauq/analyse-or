# Gold Bot Fibonacci FTMO Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the trading engine used by the FTMO instance of the gold bot with a Fibonacci-retracement-bounce engine (two-leg exit + trend-strength filter), while the personal instance keeps using the existing VWAP engine unchanged.

**Architecture:** A new pure-function module `gold_bot/fibonacci_reversion.py` (mirrors `gold_bot/vwap_reversion.py`'s structure: ATR, Fibonacci levels, trend strength, trailing stop) feeds a new orchestration function `gold_bot/bot.py::decide_and_act_fibonacci` (same signature/return contract as the existing `decide_and_act_vwap`, including the two-leg partial-TP + runner split). `gold_bot/loop.py::run_cycle` picks the engine per call based on whether `ftmo_guard_instance` is set (FTMO instance → Fibonacci, personal instance → VWAP, unchanged).

**Tech Stack:** Python, pytest (existing test suite conventions).

**Spec:** `docs/superpowers/specs/2026-10-28-gold-bot-fibonacci-ftmo-design.md`

## Global Constraints

- All parameter values are already walk-forward validated — use them verbatim, never re-derive or re-tune: `ATR_PERIOD=14`, `TOUCH_ATR_MULT=0.3`, `STOP_BUFFER_ATR_MULT=0.5`, `TRAIL_BUFFER_ATR_MULT=0.5`, `PARTIAL_TP_PCT=0.7`, `TARGET_R=1.5`, `FIB_WINDOW=40`, `FIB_PCT=0.618`, `TREND_WINDOW_DAYS=20`, `TREND_FILTER_THRESHOLD=0.05`, `SESSION_END_HOUR_UTC=22`, `MIN_DISTANCE_PCT=0.002`.
- `gold_bot/vwap_reversion.py`, `gold_bot/risk.py`, `gold_bot/ftmo_guard.py` must NOT be modified by this plan.
- The personal instance (`ftmo_guard_instance=None`) must keep using `decide_and_act_vwap` with zero behavior change — every task that touches `gold_bot/loop.py` must include a regression check for this.
- No XAGUSD support in the new engine (XAUUSD only — already the only symbol the FTMO instance trades).
- This changes a live system trading real money (FTMO Challenge account). No task in this plan deploys to the VPS except the final task, which is itself a `dry_run=true` verification step, not a live flip.

---

### Task 1: `gold_bot/fibonacci_reversion.py` — indicators (ATR, Fibonacci levels, trend strength)

**Files:**
- Create: `gold_bot/fibonacci_reversion.py`
- Test: `tests/gold_bot/test_fibonacci_reversion.py`

**Interfaces:**
- Produces: `compute_atr(candles: list[dict], period: int = ATR_PERIOD) -> list[float | None]`, `levels_fibonacci(candles: list[dict], window: int = FIB_WINDOW, fib_pct: float = FIB_PCT) -> tuple[list[float | None], list[str | None]]`, `daily_trend_strength(candles: list[dict], window_days: int = TREND_WINDOW_DAYS) -> dict[str, float]`. Module constants: `ATR_PERIOD = 14`, `TOUCH_ATR_MULT = 0.3`, `STOP_BUFFER_ATR_MULT = 0.5`, `TRAIL_BUFFER_ATR_MULT = 0.5`, `PARTIAL_TP_PCT = 0.7`, `TARGET_R = 1.5`, `FIB_WINDOW = 40`, `FIB_PCT = 0.618`, `TREND_WINDOW_DAYS = 20`, `TREND_FILTER_THRESHOLD = 0.05`, `SESSION_END_HOUR_UTC = 22`, `MIN_DISTANCE_PCT = 0.002`.

Candle dict format (from `gold_bot.confluence`, same as `vwap_reversion.py`): `{"time": "YYYY-MM-DD HH:MM:SS", "open": float, "high": float, "low": float, "close": float, "tick_volume": int, "spread": int}`.

- [ ] **Step 1: Write the failing tests for `compute_atr`**

```python
# tests/gold_bot/test_fibonacci_reversion.py
from datetime import datetime, timedelta, timezone

import gold_bot.fibonacci_reversion as fib


def _candle(time_str, open_, high, low, close, tick_volume=1000, spread=20):
    return {"time": time_str, "open": open_, "high": high, "low": low, "close": close,
            "tick_volume": tick_volume, "spread": spread}


def _series(n, start_price=2000.0, start_dt=None, step_minutes=5):
    start_dt = start_dt or datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    out = []
    price = start_price
    for i in range(n):
        t = start_dt + timedelta(minutes=step_minutes * i)
        price += 0.5 if i % 2 == 0 else -0.5
        out.append(_candle(t.strftime("%Y-%m-%d %H:%M:%S"), price, price + 1.0, price - 1.0, price))
    return out


def test_compute_atr_none_before_period_bars():
    candles = _series(fib.ATR_PERIOD - 1)
    atr = fib.compute_atr(candles)
    assert all(v is None for v in atr)


def test_compute_atr_positive_once_period_reached():
    candles = _series(fib.ATR_PERIOD + 5)
    atr = fib.compute_atr(candles)
    assert atr[fib.ATR_PERIOD - 1] is not None
    assert atr[fib.ATR_PERIOD - 1] > 0
    assert atr[-1] is not None


def test_compute_atr_is_causal_future_bars_dont_affect_past_value():
    candles = _series(fib.ATR_PERIOD + 5)
    atr_full = fib.compute_atr(candles)
    atr_truncated = fib.compute_atr(candles[: fib.ATR_PERIOD + 2])
    assert atr_full[fib.ATR_PERIOD + 1] == atr_truncated[fib.ATR_PERIOD + 1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_fibonacci_reversion.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gold_bot.fibonacci_reversion'`

- [ ] **Step 3: Write `gold_bot/fibonacci_reversion.py` with constants and `compute_atr`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_fibonacci_reversion.py -v`
Expected: 3 PASS

- [ ] **Step 5: Write the failing tests for `levels_fibonacci`**

```python
def test_levels_fibonacci_none_before_any_range():
    candles = _series(5)
    level, bias = fib.levels_fibonacci(candles, window=40)
    assert level[0] is None and bias[0] is None


def test_levels_fibonacci_vente_bias_when_low_is_most_recent_extreme():
    # Mouvement haut -> bas : le plus bas le plus recent -> retracement
    # HAUT attendu, biais VENTE (voir docstring de la fonction portee).
    candles = _series(60, start_price=2000.0)
    candles[10]["high"] = 2050.0  # haut ancien
    candles[50]["low"] = 1950.0   # bas recent (apres le haut)
    level, bias = fib.levels_fibonacci(candles, window=60, fib_pct=0.618)
    assert bias[59] == "vente"
    expected = 1950.0 + 0.618 * (2050.0 - 1950.0)
    assert level[59] == expected


def test_levels_fibonacci_achat_bias_when_high_is_most_recent_extreme():
    candles = _series(60, start_price=2000.0)
    candles[10]["low"] = 1950.0   # bas ancien
    candles[50]["high"] = 2050.0  # haut recent (apres le bas)
    level, bias = fib.levels_fibonacci(candles, window=60, fib_pct=0.618)
    assert bias[59] == "achat"
    expected = 2050.0 - 0.618 * (2050.0 - 1950.0)
    assert level[59] == expected
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_fibonacci_reversion.py -v`
Expected: FAIL with `AttributeError: module 'gold_bot.fibonacci_reversion' has no attribute 'levels_fibonacci'`

- [ ] **Step 7: Add `levels_fibonacci` to `gold_bot/fibonacci_reversion.py`**

```python
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
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_fibonacci_reversion.py -v`
Expected: 6 PASS

- [ ] **Step 9: Write the failing tests for `daily_trend_strength`**

```python
def test_daily_trend_strength_zero_before_window_days():
    candles = []
    for d in range(5):
        t = datetime(2026, 9, 1 + d, 10, 0, tzinfo=timezone.utc)
        candles.append(_candle(t.strftime("%Y-%m-%d %H:%M:%S"), 2000, 2001, 1999, 2000 + d))
    strength = fib.daily_trend_strength(candles, window_days=20)
    days = sorted({c["time"][:10] for c in candles})
    assert all(strength[d] == 0.0 for d in days)


def test_daily_trend_strength_positive_in_an_uptrend():
    candles = []
    for d in range(25):
        t = datetime(2026, 9, 1 + d, 10, 0, tzinfo=timezone.utc)
        price = 2000.0 + d * 2.0  # hausse reguliere
        candles.append(_candle(t.strftime("%Y-%m-%d %H:%M:%S"), price, price + 1, price - 1, price))
    strength = fib.daily_trend_strength(candles, window_days=20)
    last_day = sorted({c["time"][:10] for c in candles})[-1]
    assert strength[last_day] > 0
```

(Note: `_candle` uses `"%Y-%m-%d %H:%M:%S"` with a day-of-month that can exceed 28 for `d` up to 24 starting at day 1 -- September has 30 days, so `1 + d` for `d` in `range(25)` reaches day 25, safely within September. Do not change the test to cross a month boundary.)

- [ ] **Step 10: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_fibonacci_reversion.py -v`
Expected: FAIL with `AttributeError: module 'gold_bot.fibonacci_reversion' has no attribute 'daily_trend_strength'`

- [ ] **Step 11: Add `daily_trend_strength` to `gold_bot/fibonacci_reversion.py`**

```python
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
```

(`defaultdict` is already imported on the `from collections import defaultdict, deque` line added in Step 3 — do not add a second import line.)

- [ ] **Step 12: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_fibonacci_reversion.py -v`
Expected: 8 PASS

- [ ] **Step 13: Commit**

```bash
git add gold_bot/fibonacci_reversion.py tests/gold_bot/test_fibonacci_reversion.py
git commit -m "feat(gold-bot): ajoute les indicateurs du moteur Fibonacci (ATR, niveaux, force de tendance)"
```

---

### Task 2: `gold_bot/fibonacci_reversion.py` — `latest_state` and `compute_trailing_stop`

**Files:**
- Modify: `gold_bot/fibonacci_reversion.py`
- Test: `tests/gold_bot/test_fibonacci_reversion.py`

**Interfaces:**
- Consumes: `compute_atr`, `levels_fibonacci`, `daily_trend_strength` (Task 1), constants `STOP_BUFFER_ATR_MULT`, `TRAIL_BUFFER_ATR_MULT`.
- Produces: `latest_state(candles: list[dict]) -> dict | None` (keys: `close`, `high`, `low`, `level`, `bias`, `atr`, `trend_strength`, `hour_utc`), `compute_trailing_stop(candles: list[dict], direction: str, entry_time_iso: str, entry_price: float, current_stop_loss: float) -> float | None`.

- [ ] **Step 1: Write the failing tests for `latest_state`**

```python
def test_latest_state_none_when_empty():
    assert fib.latest_state([]) is None


def test_latest_state_none_before_atr_period():
    candles = _series(fib.ATR_PERIOD - 1)
    assert fib.latest_state(candles) is None


def test_latest_state_returns_expected_keys():
    candles = _series(fib.ATR_PERIOD + fib.FIB_WINDOW + 5)
    state = fib.latest_state(candles)
    assert state is not None
    for key in ("close", "high", "low", "level", "bias", "atr", "trend_strength", "hour_utc"):
        assert key in state


def test_latest_state_hour_utc_matches_last_candle():
    candles = _series(fib.ATR_PERIOD + fib.FIB_WINDOW + 5,
                       start_dt=datetime(2026, 9, 1, 3, 0, tzinfo=timezone.utc))
    state = fib.latest_state(candles)
    last_hour = datetime.strptime(candles[-1]["time"], "%Y-%m-%d %H:%M:%S").hour
    assert state["hour_utc"] == last_hour
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_fibonacci_reversion.py -v`
Expected: FAIL with `AttributeError: module 'gold_bot.fibonacci_reversion' has no attribute 'latest_state'`

- [ ] **Step 3: Add `latest_state` to `gold_bot/fibonacci_reversion.py`**

```python
def latest_state(candles: list[dict]) -> dict | None:
    """État de la dernière bougie : prix, niveau/biais Fibonacci, ATR,
    force de tendance du jour, heure UTC. None si l'historique est
    insuffisant pour l'ATR (le niveau Fibonacci, lui, peut être None
    individuellement -- c'est un état valide, pas une erreur)."""
    if not candles:
        return None
    atr = compute_atr(candles)
    i = len(candles) - 1
    if atr[i] is None:
        return None
    level, bias = levels_fibonacci(candles)
    strength_by_day = daily_trend_strength(candles)
    c = candles[i]
    t = datetime.strptime(c["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    day = c["time"][:10]
    return {
        "close": c["close"], "high": c["high"], "low": c["low"],
        "level": level[i], "bias": bias[i], "atr": atr[i],
        "trend_strength": strength_by_day.get(day, 0.0),
        "hour_utc": t.hour,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_fibonacci_reversion.py -v`
Expected: 12 PASS

- [ ] **Step 5: Write the failing tests for `compute_trailing_stop`**

```python
def _series_with_level(n, start_price=2000.0, start_dt=None):
    """Série causant un retracement ACHAT bien défini (bas ancien à
    l'indice 21, haut récent à l'indice 55 -> hi_idx > lo_idx -> niveau
    achat), avec un bruit de fond MINUSCULE (+-0.1) partout ailleurs --
    la cible (entry_idx = ATR_PERIOD+FIB_WINDOW+5) tombe après les deux
    anomalies, qui restent dans la fenêtre de 40 bougies de
    levels_fibonacci (40 bougies avant l'indice 59 = indices 19..58,
    qui couvre bien 21 et 55) mais sont assez ANCIENNES par rapport à
    `compute_atr` (EMA causale, k=1/14) pour que leur contribution à
    atr[59] soit fortement amortie (décroissance exponentielle sur
    ~38-40 bougies) -- jamais annulée exactement, d'où les marges
    volontairement larges dans les tests ci-dessous (ne jamais
    comparer `result` à une valeur numérique exacte ici, seulement à
    `current_stop_loss` ou à `None`)."""
    start_dt = start_dt or datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    out = []
    for i in range(n):
        t = start_dt + timedelta(minutes=5 * i)
        if i == 21:
            price = 1950.0  # bas ancien
        elif i == 55:
            price = 2050.0  # haut recent
        else:
            price = 2000.0
        out.append(_candle(t.strftime("%Y-%m-%d %H:%M:%S"), price, price + 0.1, price - 0.1, price))
    return out


def test_compute_trailing_stop_none_when_entry_time_before_all_candles():
    candles = _series_with_level(fib.ATR_PERIOD + fib.FIB_WINDOW + 20)
    result = fib.compute_trailing_stop(candles, "achat", "2020-01-01T00:00:00+00:00", 2000.0, 1990.0)
    assert result is None


def test_compute_trailing_stop_none_when_excursion_insufficient():
    candles = _series_with_level(fib.ATR_PERIOD + fib.FIB_WINDOW + 20)
    entry_idx = fib.ATR_PERIOD + fib.FIB_WINDOW + 5  # 59 -- apres les deux anomalies (21, 55)
    entry_price = candles[entry_idx]["close"]
    entry_time_iso = candles[entry_idx]["time"].replace(" ", "T") + "+00:00"
    for c in candles[entry_idx + 1:]:
        c["open"] = c["high"] = c["low"] = c["close"] = entry_price  # aucune excursion apres l'entree
    result = fib.compute_trailing_stop(candles, "achat", entry_time_iso, entry_price, entry_price - 5.0)
    assert result is None


def test_compute_trailing_stop_moves_stop_up_once_activated_achat():
    candles = _series_with_level(fib.ATR_PERIOD + fib.FIB_WINDOW + 20)
    entry_idx = fib.ATR_PERIOD + fib.FIB_WINDOW + 5
    entry_price = candles[entry_idx]["close"]
    entry_time_iso = candles[entry_idx]["time"].replace(" ", "T") + "+00:00"
    # Excursion tres large (+200) pour depasser R quelle que soit la
    # contamination residuelle d'ATR par les anomalies 21/55 (R reste
    # necessairement d'un ordre de grandeur bien inferieur, vu
    # l'amortissement exponentiel sur ~38-40 bougies -- voir docstring
    # de _series_with_level).
    candles[entry_idx + 2]["high"] = entry_price + 200.0
    current_stop_loss = entry_price - 5.0
    result = fib.compute_trailing_stop(candles, "achat", entry_time_iso, entry_price, current_stop_loss)
    assert result is not None
    assert result > current_stop_loss


def test_compute_trailing_stop_never_loosens():
    candles = _series_with_level(fib.ATR_PERIOD + fib.FIB_WINDOW + 20)
    entry_idx = fib.ATR_PERIOD + fib.FIB_WINDOW + 5
    entry_price = candles[entry_idx]["close"]
    entry_time_iso = candles[entry_idx]["time"].replace(" ", "T") + "+00:00"
    candles[entry_idx + 2]["high"] = entry_price + 200.0
    # Deja tres au-dessus de tout candidat possible (candidat <= meilleur
    # prix atteint depuis l'entree, lui-meme <= entry_price + 200).
    current_stop_loss = entry_price + 500.0
    result = fib.compute_trailing_stop(candles, "achat", entry_time_iso, entry_price, current_stop_loss)
    assert result is None
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_fibonacci_reversion.py -v`
Expected: FAIL with `AttributeError: module 'gold_bot.fibonacci_reversion' has no attribute 'compute_trailing_stop'`

- [ ] **Step 7: Add `compute_trailing_stop` to `gold_bot/fibonacci_reversion.py`**

```python
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
    level, _bias = levels_fibonacci(candles)

    entry_dt = datetime.fromisoformat(entry_time_iso.replace("Z", "+00:00"))
    entry_idx = None
    for i, c in enumerate(candles):
        c_dt = datetime.strptime(c["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        if c_dt <= entry_dt:
            entry_idx = i
        else:
            break
    if entry_idx is None:
        return None

    atr_entry = atr[entry_idx]
    entry_level = level[entry_idx]
    if atr_entry is None or atr_entry <= 0 or entry_level is None:
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
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_fibonacci_reversion.py -v`
Expected: 16 PASS

- [ ] **Step 9: Commit**

```bash
git add gold_bot/fibonacci_reversion.py tests/gold_bot/test_fibonacci_reversion.py
git commit -m "feat(gold-bot): ajoute latest_state et compute_trailing_stop au moteur Fibonacci"
```

---

### Task 3: `gold_bot/bot.py::decide_and_act_fibonacci` — existing-position handling

**Files:**
- Modify: `gold_bot/bot.py`
- Test: `tests/gold_bot/test_bot.py`

**Interfaces:**
- Consumes: `fibonacci_reversion.latest_state`, `fibonacci_reversion.compute_trailing_stop`, `fibonacci_reversion.SESSION_END_HOUR_UTC` (Task 1/2). `risk.CircuitBreaker.check`.
- Produces: `decide_and_act_fibonacci(candles, *, contract_size, balance, equity, volume_step, min_volume, max_volume, open_positions, circuit_breaker, symbol="XAUUSD", risk_pct=0.05, historical_candles=None) -> dict` (partial implementation this task: history-insufficient guard + existing-position branch only; entry-signal branch is Task 4).

- [ ] **Step 1: Write the failing tests**

Add to `tests/gold_bot/test_bot.py` (same file as the existing `decide_and_act_vwap` tests — add near them, after the last `decide_and_act_vwap` test). First, find the existing import block at the top of the file (it imports `gold_bot.vwap_reversion as vwap_reversion` alongside `import gold_bot.bot as bot` and `import gold_bot.risk as risk`) and add `import gold_bot.fibonacci_reversion as fib_reversion` next to it, matching the existing style.

```python
def _fib_state(close=2000.0, high=2001.0, low=1999.0, level=1990.0, bias="achat",
                atr=5.0, trend_strength=0.0, hour_utc=10):
    return {"close": close, "high": high, "low": low, "level": level, "bias": bias,
            "atr": atr, "trend_strength": trend_strength, "hour_utc": hour_utc}


def test_decide_and_act_fibonacci_no_action_when_insufficient_history(monkeypatch):
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles: None)
    result = bot.decide_and_act_fibonacci(
        [], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "historique insuffisant pour l'ATR/niveau Fibonacci"}


def test_decide_and_act_fibonacci_closes_both_legs_at_session_end(monkeypatch):
    state = _fib_state(hour_utc=fib_reversion.SESSION_END_HOUR_UTC)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles: state)
    open_positions = [
        {"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "takeProfit": None},
        {"id": "2", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "takeProfit": 2050.0},
    ]
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=open_positions, circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    assert {s["position_id"] for s in result["steps"]} == {"1", "2"}
    assert all(s["type"] == "clôture_simulee" for s in result["steps"])


def test_decide_and_act_fibonacci_no_action_when_only_partial_leg_open(monkeypatch):
    state = _fib_state()
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles: state)
    open_positions = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "takeProfit": 2050.0}]
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=open_positions, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "jambe partielle seule ouverte, rien a gerer"}


def test_decide_and_act_fibonacci_refuses_when_multiple_runner_positions_open(monkeypatch):
    state = _fib_state()
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles: state)
    open_positions = [
        {"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "takeProfit": None},
        {"id": "2", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "takeProfit": None},
    ]
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=open_positions, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "plusieurs positions ouvertes sur ce symbole, aucune action par prudence"}


def test_decide_and_act_fibonacci_refuses_on_unexpected_position_type(monkeypatch):
    state = _fib_state()
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles: state)
    open_positions = [{"id": "1", "symbol": "XAUUSD", "type": "SOMETHING_ELSE", "takeProfit": None}]
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=open_positions, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "position de type inattendu, aucune action par prudence"}


def test_decide_and_act_fibonacci_refuses_when_position_fields_missing(monkeypatch):
    state = _fib_state()
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles: state)
    open_positions = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "takeProfit": None}]  # pas de openPrice/stopLoss/time
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=open_positions, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "champs de position manquants, aucune action par prudence"}


def test_decide_and_act_fibonacci_holds_when_no_trailing_update(monkeypatch):
    state = _fib_state()
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles: state)
    monkeypatch.setattr(bot.fibonacci_reversion, "compute_trailing_stop", lambda *a, **k: None)
    open_positions = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "takeProfit": None,
                        "openPrice": 2000.0, "stopLoss": 1990.0, "time": "2026-09-01T10:00:00+00:00"}]
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=open_positions, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "position ouverte, pas de mise a jour du stop suiveur"}


def test_decide_and_act_fibonacci_modifies_stop_when_trailing_stop_updates(monkeypatch):
    state = _fib_state()
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles: state)
    monkeypatch.setattr(bot.fibonacci_reversion, "compute_trailing_stop", lambda *a, **k: 1995.0)
    open_positions = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "takeProfit": None,
                        "openPrice": 2000.0, "stopLoss": 1990.0, "time": "2026-09-01T10:00:00+00:00"}]
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=open_positions, circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "simulation", "steps": [
        {"type": "modification_simulee", "position_id": "1", "symbol": "XAUUSD", "new_stop_loss": 1995.0},
    ]}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_bot.py -k fibonacci -v`
Expected: FAIL with `AttributeError: module 'gold_bot.bot' has no attribute 'decide_and_act_fibonacci'` (and `fibonacci_reversion` not yet imported in `bot.py`)

- [ ] **Step 3: Add the import and the existing-position branch of `decide_and_act_fibonacci` to `gold_bot/bot.py`**

Add `import gold_bot.fibonacci_reversion as fibonacci_reversion` to the import block at the top of `gold_bot/bot.py` (alongside the existing `import gold_bot.vwap_reversion as vwap_reversion`, keep alphabetical order of the import block as already used there).

Append this function after `decide_and_act_vwap` (before `reconcile_positions`):

```python
def decide_and_act_fibonacci(candles: list[dict], *, contract_size: float, balance: float, equity: float,
                              volume_step: float, min_volume: float, max_volume: float,
                              open_positions: list[dict], circuit_breaker: "risk.CircuitBreaker",
                              symbol: str = "XAUUSD", risk_pct: float = 0.05,
                              historical_candles: list[dict] | None = None) -> dict:
    """Moteur de rebond sur retracement de Fibonacci + filtre de force
    de tendance + stop suiveur ATR -- voir docs/superpowers/specs/
    2026-10-28-gold-bot-fibonacci-ftmo-design.md. Même contrat
    d'entrée/sortie à deux jambes (prise partielle + runner) que
    decide_and_act_vwap, mais moteur de signal différent. `symbol` par
    défaut XAUUSD -- seul symbole calibré pour ce moteur.
    `historical_candles` est accepté pour compatibilité d'appel avec
    gold_bot.loop (même signature que decide_and_act_vwap) mais n'est
    pas utilisé : le filtre de cotes historiques n'a jamais été validé
    pour ce moteur, voir le spec (non-objectif explicite)."""
    circuit_breaker.check(equity)

    state = fibonacci_reversion.latest_state(candles)
    if state is None:
        return {"action": "aucune", "reason": "historique insuffisant pour l'ATR/niveau Fibonacci"}

    matching = [p for p in open_positions if p.get("symbol") == symbol]
    runner_matching = [p for p in matching if not p.get("takeProfit")]
    partial_matching = [p for p in matching if p.get("takeProfit")]

    if len(runner_matching) > 1 or len(partial_matching) > 1:
        return {"action": "aucune", "reason": "plusieurs positions ouvertes sur ce symbole, aucune action par prudence"}

    if matching:
        if state["hour_utc"] >= fibonacci_reversion.SESSION_END_HOUR_UTC:
            steps = [{"type": "clôture_simulee", "position_id": p.get("id"), "symbol": symbol} for p in matching]
            return {"action": "simulation", "steps": steps}

        if not runner_matching:
            return {"action": "aucune", "reason": "jambe partielle seule ouverte, rien a gerer"}

        position = runner_matching[0]
        raw_type = position.get("type")
        if raw_type == "POSITION_TYPE_BUY":
            direction = "achat"
        elif raw_type == "POSITION_TYPE_SELL":
            direction = "vente"
        else:
            return {"action": "aucune", "reason": "position de type inattendu, aucune action par prudence"}

        entry_price = position.get("openPrice")
        current_stop_loss = position.get("stopLoss")
        entry_time_iso = position.get("time")
        if entry_price is None or current_stop_loss is None or entry_time_iso is None:
            return {"action": "aucune", "reason": "champs de position manquants, aucune action par prudence"}

        new_stop = fibonacci_reversion.compute_trailing_stop(
            candles, direction, entry_time_iso, entry_price, current_stop_loss)
        if new_stop is None:
            return {"action": "aucune", "reason": "position ouverte, pas de mise a jour du stop suiveur"}

        return {"action": "simulation", "steps": [{
            "type": "modification_simulee", "position_id": position.get("id"), "symbol": symbol,
            "new_stop_loss": new_stop,
        }]}

    return {"action": "aucune", "reason": "pas de signal d'entree"}
```

(The final `return {"action": "aucune", "reason": "pas de signal d'entree"}` is a placeholder that Task 4 replaces with the real entry logic — it exists only so this task's tests, which never reach that branch, pass without the function crashing on a missing return path.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_bot.py -k fibonacci -v`
Expected: 8 PASS

- [ ] **Step 5: Commit**

```bash
git add gold_bot/bot.py tests/gold_bot/test_bot.py
git commit -m "feat(gold-bot): ajoute la gestion de position existante de decide_and_act_fibonacci"
```

---

### Task 4: `gold_bot/bot.py::decide_and_act_fibonacci` — entry-signal logic

**Files:**
- Modify: `gold_bot/bot.py`
- Test: `tests/gold_bot/test_bot.py`

**Interfaces:**
- Consumes: `fibonacci_reversion.{TOUCH_ATR_MULT, STOP_BUFFER_ATR_MULT, PARTIAL_TP_PCT, TARGET_R, TREND_FILTER_THRESHOLD, MIN_DISTANCE_PCT, SESSION_END_HOUR_UTC}` (Task 1), `risk.compute_position_size`, `risk.round_to_volume_step` (existing, unchanged).
- Produces: completes `decide_and_act_fibonacci` (replaces the Task 3 placeholder return with the full entry-signal branch).

- [ ] **Step 1: Write the failing tests**

Add to `tests/gold_bot/test_bot.py`:

```python
def test_decide_and_act_fibonacci_no_action_after_session_end_hour(monkeypatch):
    state = _fib_state(hour_utc=fib_reversion.SESSION_END_HOUR_UTC)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "trop tard dans la session pour ouvrir"}


def test_decide_and_act_fibonacci_no_action_when_no_level(monkeypatch):
    state = _fib_state(level=None, bias=None)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "aucun niveau de retracement disponible"}


def test_decide_and_act_fibonacci_no_action_when_atr_not_positive(monkeypatch):
    state = _fib_state(atr=0.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "atr indisponible"}


def test_decide_and_act_fibonacci_filtered_by_strong_trend(monkeypatch):
    # memes chiffres entree que le test d'ouverture plus bas, mais force
    # de tendance au-dela du seuil -- signal filtre avant toute
    # evaluation de touche/rejet.
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat",
                        atr=20.0, trend_strength=0.06)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "tendance trop forte, signal filtre"}


def test_decide_and_act_fibonacci_no_action_when_level_not_touched(monkeypatch):
    # touched (achat) = low <= level + tol ; tol = 0.3*20 = 6, level+tol =
    # 1986 -- low (1987) depasse ce seuil, donc pas de touche, meme si
    # close (1990) est bien au-dessus du niveau (rejet qui n'a pas de
    # sens a verifier puisque touched est deja faux). Bougie valide :
    # low(1987) <= close(1990) <= high(1992).
    state = _fib_state(close=1990.0, high=1992.0, low=1987.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "pas de signal d'entree"}


def test_decide_and_act_fibonacci_opens_achat_with_partial_and_runner_legs(monkeypatch):
    # biais achat, low (1974) touche le niveau 1980 avec tolerance
    # 0.3*20=6 (1980-6=1974, 1974<=1974 -> touche), close (1981) > niveau
    # (1980) -> rejet haussier confirme.
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    assert len(result["steps"]) == 2
    partial, runner = result["steps"]
    stop_loss = 1980.0 - 0.5 * 20.0  # 1970.0
    distance = 1981.0 - stop_loss  # 11.0

    assert partial["type"] == "ouverture_simulee"
    assert partial["direction"] == "achat"
    assert partial["entry"] == 1981.0
    assert partial["stop_loss"] == pytest.approx(stop_loss)
    assert partial["take_profit"] == pytest.approx(1981.0 + 1.5 * distance)

    assert runner["type"] == "ouverture_simulee"
    assert runner["direction"] == "achat"
    assert runner["stop_loss"] == pytest.approx(stop_loss)
    assert runner["take_profit"] is None


def test_decide_and_act_fibonacci_opens_vente_with_partial_and_runner_legs(monkeypatch):
    # biais vente, symetrique du test achat ci-dessus : high (1986)
    # touche le niveau 1980 avec tolerance 6 (1980+6=1986, 1986>=1986 ->
    # touche), close (1979) < niveau (1980) -> rejet baissier confirme.
    state = _fib_state(close=1979.0, high=1986.0, low=1978.0, level=1980.0, bias="vente", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    partial, runner = result["steps"]
    stop_loss = 1980.0 + 0.5 * 20.0  # 1990.0
    distance = stop_loss - 1979.0  # 11.0
    assert partial["direction"] == "vente"
    assert partial["take_profit"] == pytest.approx(1979.0 - 1.5 * distance)
    assert runner["direction"] == "vente"
    assert runner["take_profit"] is None


def test_decide_and_act_fibonacci_no_action_when_distance_too_small(monkeypatch):
    # close (2000.001) doit rester STRICTEMENT au-dessus du niveau
    # (2000.0) pour que "rejected" soit vrai (comparaison stricte ">")
    # -- atr quasi nul rend le stop (level - 0.5*atr) quasiment confondu
    # avec le niveau, donc la distance entree-stop est minuscule, sous
    # MIN_DISTANCE_PCT*entry_price (0.002 * ~2000 = 4.0).
    state = _fib_state(close=2000.001, high=2000.002, low=1999.999, level=2000.0, bias="achat", atr=0.0001)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "distance entree-stop trop faible"}


def test_decide_and_act_fibonacci_blocked_by_circuit_breaker(monkeypatch):
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles: state)
    from datetime import datetime, timezone
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=8900, open_positions=[], circuit_breaker=cb,  # -11%, coupe-circuit declenche
    )
    assert result == {"action": "aucune", "reason": "coupe-circuit journalier déclenché"}


def test_decide_and_act_fibonacci_no_action_when_size_below_minimum(monkeypatch):
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=20.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=10000.0, max_volume=50000.0,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "compte trop petit pour ce stop (volume sous le minimum du broker)"}


def test_decide_and_act_fibonacci_opens_single_leg_when_partial_too_small(monkeypatch):
    # atr=28 -> stop_loss = 1980 - 0.5*28 = 1966, distance = close(1981) -
    # stop(1966) = 15 -- choisi pour que le volume total tombe juste
    # au-dessus du minimum (0.3) mais sa fraction partielle (70%) tombe
    # EN DESSOUS : balance=10000, risk_pct par defaut 0.05 -> risque
    # 500$ ; raw_size = 500/(15*100) = 0.3333 -> arrondi 0.33 (>= 0.3,
    # ouvrable) ; jambe partielle 0.33*0.7=0.231 -> arrondie 0.23 (<
    # 0.3, PAS ouvrable) -- repli sur une seule jambe runner, meme
    # mecanique que test_decide_and_act_vwap_opens_single_leg_when_partial_too_small.
    # tol = 0.3*28 = 8.4, touched (low=1974 <= 1980+8.4=1988.4) vrai ;
    # rejected (close=1981 > level=1980) vrai.
    state = _fib_state(close=1981.0, high=1982.0, low=1974.0, level=1980.0, bias="achat", atr=28.0)
    monkeypatch.setattr(bot.fibonacci_reversion, "latest_state", lambda candles: state)
    result = bot.decide_and_act_fibonacci(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.3, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    assert len(result["steps"]) == 1
    assert result["steps"][0]["take_profit"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_bot.py -k fibonacci -v`
Expected: FAIL — tests expecting `"simulation"`/entry-branch reasons instead get `{"action": "aucune", "reason": "pas de signal d'entree"}` from the Task 3 placeholder (the no-level/no-atr/session-end/distance/circuit-breaker tests fail because those guards don't exist yet either).

- [ ] **Step 3: Replace the placeholder return in `decide_and_act_fibonacci` with the full entry-signal branch**

Replace the line `return {"action": "aucune", "reason": "pas de signal d'entree"}` at the end of `decide_and_act_fibonacci` (added in Task 3) with:

```python
    if state["hour_utc"] >= fibonacci_reversion.SESSION_END_HOUR_UTC:
        return {"action": "aucune", "reason": "trop tard dans la session pour ouvrir"}
    if state["level"] is None or state["bias"] is None:
        return {"action": "aucune", "reason": "aucun niveau de retracement disponible"}
    if state["atr"] is None or state["atr"] <= 0:
        return {"action": "aucune", "reason": "atr indisponible"}
    if abs(state["trend_strength"]) > fibonacci_reversion.TREND_FILTER_THRESHOLD:
        return {"action": "aucune", "reason": "tendance trop forte, signal filtre"}

    tol = fibonacci_reversion.TOUCH_ATR_MULT * state["atr"]
    direction = None
    stop_loss = None
    if state["bias"] == "achat":
        touched = state["low"] <= state["level"] + tol
        rejected = state["close"] > state["level"]
        if touched and rejected:
            direction = "achat"
            stop_loss = state["level"] - fibonacci_reversion.STOP_BUFFER_ATR_MULT * state["atr"]
    else:
        touched = state["high"] >= state["level"] - tol
        rejected = state["close"] < state["level"]
        if touched and rejected:
            direction = "vente"
            stop_loss = state["level"] + fibonacci_reversion.STOP_BUFFER_ATR_MULT * state["atr"]

    if direction is None:
        return {"action": "aucune", "reason": "pas de signal d'entree"}

    entry_price = state["close"]
    distance = abs(entry_price - stop_loss)
    if distance < entry_price * fibonacci_reversion.MIN_DISTANCE_PCT:
        return {"action": "aucune", "reason": "distance entree-stop trop faible"}

    if not circuit_breaker.can_open_position(equity):
        return {"action": "aucune", "reason": "coupe-circuit journalier déclenché"}

    raw_size = risk.compute_position_size(balance, entry_price, stop_loss, contract_size, risk_pct=risk_pct)
    total_size = risk.round_to_volume_step(raw_size, volume_step, min_volume, max_volume)
    if total_size is None:
        return {"action": "aucune", "reason": "compte trop petit pour ce stop (volume sous le minimum du broker)"}

    partial_volume = None
    if fibonacci_reversion.PARTIAL_TP_PCT > 0:
        partial_volume = risk.round_to_volume_step(
            total_size * fibonacci_reversion.PARTIAL_TP_PCT, volume_step, min_volume, max_volume)

    if partial_volume is None:
        return {"action": "simulation", "steps": [{
            "type": "ouverture_simulee", "symbol": symbol, "direction": direction, "volume": total_size,
            "entry": entry_price, "stop_loss": stop_loss, "take_profit": None,
        }]}

    runner_volume = round(total_size - partial_volume, 2)
    partial_target = (entry_price + fibonacci_reversion.TARGET_R * distance if direction == "achat"
                       else entry_price - fibonacci_reversion.TARGET_R * distance)

    steps = [{
        "type": "ouverture_simulee", "symbol": symbol, "direction": direction, "volume": partial_volume,
        "entry": entry_price, "stop_loss": stop_loss, "take_profit": partial_target,
    }]
    if runner_volume >= volume_step:
        steps.append({
            "type": "ouverture_simulee", "symbol": symbol, "direction": direction, "volume": runner_volume,
            "entry": entry_price, "stop_loss": stop_loss, "take_profit": None,
        })
    return {"action": "simulation", "steps": steps}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_bot.py -k fibonacci -v`
Expected: 19 PASS (8 from Task 3 + 11 new)

- [ ] **Step 5: Run the full `test_bot.py` file to check for regressions**

Run: `python -m pytest tests/gold_bot/test_bot.py -v`
Expected: all PASS (no `decide_and_act_vwap`/`decide_and_act`/`decide_and_act_swing` test touched or broken)

- [ ] **Step 6: Commit**

```bash
git add gold_bot/bot.py tests/gold_bot/test_bot.py
git commit -m "feat(gold-bot): ajoute la logique d'entree de decide_and_act_fibonacci"
```

---

### Task 5: `gold_bot/loop.py` — engine selection per instance

**Files:**
- Modify: `gold_bot/loop.py`
- Modify: `tests/gold_bot/test_loop.py` (update 2 existing tests that currently mock `decide_and_act_vwap` while `ftmo_guard_instance` is set)
- Test: `tests/gold_bot/test_loop.py` (new regression tests)

**Interfaces:**
- Consumes: `bot.decide_and_act_fibonacci` (Task 4), `bot.decide_and_act_vwap` (existing, unchanged), `ftmo_guard_instance` parameter of `run_cycle` (existing).

- [ ] **Step 1: Locate and read the current call site**

In `gold_bot/loop.py::run_cycle`, find:

```python
        historical_candles = _load_or_fetch_historical_candles(token, account_id, symbol, region, now_dt)
        decision = bot.decide_and_act_vwap(
            candles, contract_size=contract_size, balance=balance, equity=equity,
            volume_step=spec["volumeStep"], min_volume=spec["minVolume"], max_volume=spec["maxVolume"],
            open_positions=open_positions, circuit_breaker=circuit_breaker, symbol=symbol,
            risk_pct=profile_params["risk_pct"], historical_candles=historical_candles,
        )
```

- [ ] **Step 2: Write the failing regression tests**

Add to `tests/gold_bot/test_loop.py`, near the other `ftmo_guard_instance` tests:

```python
def test_run_cycle_uses_fibonacci_engine_for_ftmo_instance(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 9900})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    monkeypatch.setattr(
        loop.bot, "decide_and_act_vwap",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("decide_and_act_vwap ne doit jamais etre appele avec ftmo_guard_instance")))
    fibonacci_calls = []
    monkeypatch.setattr(
        loop.bot, "decide_and_act_fibonacci",
        lambda *a, **k: fibonacci_calls.append(k) or {"action": "aucune", "reason": "pas de signal d'entree"})

    guard = loop.ftmo_guard.FtmoGuard(isc=10000, now_fn=lambda: _FRESH_NOW)
    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW, ftmo_guard_instance=guard)

    assert result["action"] == "aucune"
    assert len(fibonacci_calls) == 1


def test_run_cycle_uses_vwap_engine_for_personal_instance(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda *a, **k: [_FRESH_CANDLE])
    monkeypatch.setattr(loop.broker, "get_account_information", lambda *a, **k: {"balance": 10000, "equity": 10000})
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100, "volumeStep": 0.01, "minVolume": 0.01, "maxVolume": 500})
    monkeypatch.setattr(
        loop.bot, "decide_and_act_fibonacci",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("decide_and_act_fibonacci ne doit jamais etre appele sans ftmo_guard_instance")))
    vwap_calls = []
    monkeypatch.setattr(
        loop.bot, "decide_and_act_vwap",
        lambda *a, **k: vwap_calls.append(k) or {"action": "aucune", "reason": "signal neutre"})

    result = loop.run_cycle("tok", "acc", loop.risk.CircuitBreaker(), now=_FRESH_NOW)  # pas de ftmo_guard_instance

    assert result["action"] == "aucune"
    assert len(vwap_calls) == 1
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_loop.py -k "uses_fibonacci_engine or uses_vwap_engine" -v`
Expected: `test_run_cycle_uses_fibonacci_engine_for_ftmo_instance` FAILS (currently calls `decide_and_act_vwap` unconditionally, triggering the `AssertionError`); `test_run_cycle_uses_vwap_engine_for_personal_instance` PASSES already (no change needed for that path yet, but keep both tests together for clarity).

- [ ] **Step 4: Change the call site in `gold_bot/loop.py`**

Replace the block found in Step 1 with:

```python
        historical_candles = _load_or_fetch_historical_candles(token, account_id, symbol, region, now_dt)
        engine = bot.decide_and_act_fibonacci if ftmo_guard_instance is not None else bot.decide_and_act_vwap
        decision = engine(
            candles, contract_size=contract_size, balance=balance, equity=equity,
            volume_step=spec["volumeStep"], min_volume=spec["minVolume"], max_volume=spec["maxVolume"],
            open_positions=open_positions, circuit_breaker=circuit_breaker, symbol=symbol,
            risk_pct=profile_params["risk_pct"], historical_candles=historical_candles,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_loop.py -k "uses_fibonacci_engine or uses_vwap_engine" -v`
Expected: 2 PASS

- [ ] **Step 6: Run the full `test_loop.py` file to find regressions from the engine switch**

Run: `python -m pytest tests/gold_bot/test_loop.py -v`
Expected: Exactly 2 FAILURES — `test_run_cycle_ftmo_guard_does_nothing_when_equity_above_floors` and `test_run_cycle_calls_ftmo_signal_email_when_ftmo_guard_active_and_signal_found` (both pass `ftmo_guard_instance=guard` while mocking `decide_and_act_vwap`, which is no longer called in that path). If any OTHER test fails, stop and investigate before continuing — that would mean the engine-selection change has a wider side effect than designed.

- [ ] **Step 7: Fix the 2 broken tests**

In `tests/gold_bot/test_loop.py`:

In `test_run_cycle_ftmo_guard_does_nothing_when_equity_above_floors`, change:
```python
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})
```
to:
```python
    monkeypatch.setattr(loop.bot, "decide_and_act_fibonacci", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})
```

In `test_run_cycle_calls_ftmo_signal_email_when_ftmo_guard_active_and_signal_found`, change:
```python
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda *a, **k: dict(_SIGNAL_DECISION))
```
to:
```python
    monkeypatch.setattr(loop.bot, "decide_and_act_fibonacci", lambda *a, **k: dict(_SIGNAL_DECISION))
```

- [ ] **Step 8: Run the full `test_loop.py` file again**

Run: `python -m pytest tests/gold_bot/test_loop.py -v`
Expected: all PASS

- [ ] **Step 9: Run the full project test suite**

Run: `python -m pytest -q`
Expected: all PASS (this is the final regression gate before deployment — do not proceed to Task 6 if anything fails)

- [ ] **Step 10: Commit**

```bash
git add gold_bot/loop.py tests/gold_bot/test_loop.py
git commit -m "feat(gold-bot): bascule l'instance FTMO sur le moteur Fibonacci, instance perso inchangee"
```

---

### Task 6: Deployment verification (dry_run) — NOT a code task

**Files:** none (operational task on the VPS, no repo changes).

This task is deliberately NOT delegated to a code-writing subagent — it is a manual verification step on the live FTMO VPS, performed by whoever is driving this plan (the orchestrator), following the spec's "Déploiement (sécurité — compte réel)" section:

- [ ] **Step 1: Deploy the merged code to the FTMO VPS checkout**

```bash
ssh root@<ftmo-vps-host> "cd /home/goldbot/analyse-or-ftmo && git pull --ff-only"
```

- [ ] **Step 2: Confirm `dry_run=true` before restarting**

Read `/home/goldbot/analyse-or-ftmo/gold_bot/state.json` on the VPS. If `dry_run` is `false`, back it up and set it to `true` (same procedure already used earlier for this account — copy the file, edit the JSON field, write back) BEFORE restarting the services, so the new engine's first real cycles never place a live order.

- [ ] **Step 3: Restart the FTMO services**

```bash
ssh root@<ftmo-vps-host> "systemctl restart gold-bot-loop-ftmo gold-bot-api-ftmo && systemctl is-active gold-bot-loop-ftmo gold-bot-api-ftmo"
```

- [ ] **Step 4: Observe several real cycles**

Wait at least 10-15 minutes (several ~60s cycles). Check `decisions_log.jsonl` (`tail -n 20`) for sane `action`/`reason` values (no `erreur` entries, no Python tracebacks in `journalctl -u gold-bot-loop-ftmo`), and `/status` / `/dashboard` on the API for a consistent picture. If a signal fires, confirm its `steps` look reasonable (direction, stop/target distances in a sane range for gold).

- [ ] **Step 5: Report back and wait for explicit go-ahead before flipping to live**

Do not flip `dry_run` to `false` as part of this plan — report the dry-run observation results back to the user and wait for their explicit confirmation, exactly as was done for this account's original VWAP-to-live transition. This step intentionally ends the plan without completing the live cutover.
