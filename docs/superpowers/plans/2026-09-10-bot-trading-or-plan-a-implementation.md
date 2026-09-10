# Bot de trading or — Plan A : moteur de décision (simulation uniquement) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the "brain" of the gold trading bot — confluence decision engine (ported from `docs/scalping.js`), risk-based position sizing, daily circuit breaker, MetaApi trading-account integration, and the orchestration rules — entirely testable, and **always operating in simulation mode** (never calling a real order-placement endpoint).

**Architecture:** A new top-level package `gold_bot/` with 5 small, pure-as-possible modules (`confluence.py`, `risk.py`, `state.py`, `broker.py`, `bot.py`), each independently unit-tested. `broker.py` is the only module that talks to MetaApi's real trading API — its functions are fully built and tested (mocked), but `bot.py`'s orchestration never calls the order-placing ones in this plan; it only logs what it would do. Plan B (a separate later plan) adds the VPS deployment, the always-on polling loop, the kill-switch API, and the runtime toggle to go live.

**Tech Stack:** Python 3.12, `requests` (already a project dependency), pytest, `monkeypatch`/`tmp_path` fixtures (matching this repo's established conventions — see `tests/test_portfolio_sync_mt5.py`).

**Spec:** `docs/superpowers/specs/2026-09-10-bot-trading-or-design.md`

## Global Constraints

- **This plan never places or closes a real order.** `bot.decide_and_act()` only ever returns a description of what it *would* do (`"action": "simulation"`); it must never call `broker.place_market_order` or `broker.close_position`. Plan B adds the live path — do not add it here even if it looks trivial.
- **Position sizing is risk-based, not notional-based**: the loss if the stop-loss is hit must equal `risk_pct` (5% = `0.05`) of the account balance — `size = (risk_pct × balance) / (|entry − stop_loss| × contract_size)`.
- **Circuit-breaker threshold (10% = `0.10`) is computed against the balance fixed at the start of the current UTC calendar day**, never against the current balance — the reference must not shift as losses accumulate during the day.
- **The confluence engine's trend condition must be ported exactly as written in `docs/scalping.js`**, including the fact that a buy signal requires a `"baissier"` (bearish) trend context near support — this is a deliberate reversal-off-structure strategy, correct as originally designed, not a bug to "fix" during the port.
- **No hardcoded contract size.** `risk.compute_position_size` takes `contract_size` as a parameter; the caller (`bot.py`, later Plan B) is responsible for fetching it from `broker.get_symbol_specification` — never guess or hardcode XAUUSD's contract size inside `risk.py`.
- **Only real, verified MetaApi endpoint shapes** — the exact URLs/bodies given in Task 4 below were confirmed against MetaApi's own documentation (not invented). Do not alter the endpoint paths, field names, or `actionType` values.
- Reversal rule: no existing position → open; existing position **same direction as signal** → no action; existing position **opposite direction** → simulate closing it, then simulate opening the new one; neutral signal → no action regardless of any existing position.
- Startup/decision reliability: never decide based on stale local memory of "do I have an open position" — always work from positions passed in by the caller, which in the real (Plan B) system come from a fresh `broker.get_open_positions` call.

---

### Task 1: `gold_bot/confluence.py` — ported decision engine

**Files:**
- Create: `gold_bot/__init__.py` (empty)
- Create: `gold_bot/confluence.py`
- Test: `tests/gold_bot/__init__.py` (empty)
- Test: `tests/gold_bot/test_confluence.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `fetch_gold_candles(api_key: str) -> list[dict]` (each dict: `time, open, high, low, close`), `detect_pivots(candles, k) -> list[dict]`, `classify_trend(pivots) -> str`, `current_levels(pivots, current_price) -> dict`, `compute_rsi(closes, period) -> float`, `compute_macd(closes, fast, slow, signal) -> dict`, `compute_bollinger(closes, period, mult) -> dict`, `match_candlestick_pattern(candles, trend) -> dict | None`, `compute_signal(candles) -> dict` (keys: `status, price, entry, stop_loss, take_profit, trend, pattern`) — all consumed by Task 5's `bot.py`.

Read `docs/scalping.js` and `docs/scalping.test.js` in full before starting (they are the source of truth this task ports from) — the code below already reflects a careful line-by-line port, but re-read the originals to build confidence before typing.

- [ ] **Step 1: Write the failing tests (part 1 — direct ports from `scalping.test.js`)**

Create `tests/gold_bot/__init__.py` (empty file).

Create `tests/gold_bot/test_confluence.py`:

```python
import math
import pytest
import gold_bot.confluence as confluence


class _FakeTDResponse:
    def __init__(self, json_data, ok=True, status_code=200):
        self._json_data = json_data
        self.ok = ok
        self.status_code = status_code

    def json(self):
        return self._json_data


def test_fetch_gold_candles_parses_and_reverses_to_chronological_order(monkeypatch):
    fake_data = {
        "status": "ok",
        "values": [
            {"datetime": "2026-09-09 10:02:00", "open": "2051.0", "high": "2051.5", "low": "2050.5", "close": "2051.2"},
            {"datetime": "2026-09-09 10:01:00", "open": "2050.0", "high": "2050.8", "low": "2049.5", "close": "2050.5"},
        ],
    }
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["params"] = params
        return _FakeTDResponse(fake_data)

    monkeypatch.setattr(confluence.requests, "get", fake_get)
    candles = confluence.fetch_gold_candles("fake-key")

    assert captured["params"]["symbol"] == "XAU/USD"
    assert captured["params"]["interval"] == "1min"
    assert captured["params"]["timezone"] == "UTC"
    assert len(candles) == 2
    assert candles[0]["time"] == "2026-09-09 10:01:00"
    assert candles[0]["close"] == 2050.5
    assert candles[1]["time"] == "2026-09-09 10:02:00"


def test_fetch_gold_candles_rejects_on_error_status(monkeypatch):
    monkeypatch.setattr(
        confluence.requests, "get",
        lambda *a, **k: _FakeTDResponse({"status": "error", "message": "quota dépassé"}),
    )
    with pytest.raises(RuntimeError, match="quota dépassé"):
        confluence.fetch_gold_candles("fake-key")


def test_fetch_gold_candles_rejects_on_http_error(monkeypatch):
    monkeypatch.setattr(
        confluence.requests, "get",
        lambda *a, **k: _FakeTDResponse({}, ok=False, status_code=429),
    )
    with pytest.raises(RuntimeError, match="429"):
        confluence.fetch_gold_candles("fake-key")


def test_detect_pivots_finds_high_and_low_with_k1():
    candles = [
        {"high": 10, "low": 8},
        {"high": 10, "low": 8},
        {"high": 14, "low": 8},
        {"high": 10, "low": 3},
        {"high": 10, "low": 8},
    ]
    pivots = confluence.detect_pivots(candles, 1)
    assert pivots == [
        {"index": 2, "type": "high", "price": 14},
        {"index": 3, "type": "low", "price": 3},
    ]


def test_detect_pivots_rejects_equal_neighbor_as_not_strictly_higher():
    candles = [
        {"high": 10, "low": 5},
        {"high": 10, "low": 5},
        {"high": 8, "low": 5},
    ]
    assert confluence.detect_pivots(candles, 1) == []


def test_classify_trend_haussier_on_rising_pivots():
    pivots = [
        {"index": 0, "type": "low", "price": 10},
        {"index": 1, "type": "high", "price": 15},
        {"index": 2, "type": "low", "price": 12},
        {"index": 3, "type": "high", "price": 18},
    ]
    assert confluence.classify_trend(pivots) == "haussier"


def test_classify_trend_neutre_when_pivots_disagree():
    pivots = [
        {"index": 0, "type": "low", "price": 10},
        {"index": 1, "type": "high", "price": 18},
        {"index": 2, "type": "low", "price": 12},
        {"index": 3, "type": "high", "price": 15},
    ]
    assert confluence.classify_trend(pivots) == "neutre"


def test_classify_trend_neutre_when_not_enough_pivots():
    pivots = [{"index": 0, "type": "low", "price": 10}]
    assert confluence.classify_trend(pivots) == "neutre"


def test_current_levels_picks_nearest_unbroken_pivots():
    pivots = [
        {"index": 0, "type": "low", "price": 95},
        {"index": 1, "type": "high", "price": 105},
        {"index": 2, "type": "low", "price": 98},
        {"index": 3, "type": "high", "price": 110},
    ]
    assert confluence.current_levels(pivots, 100) == {"support": 98, "resistance": 110}


def test_current_levels_null_when_no_pivot_on_one_side():
    pivots = [{"index": 0, "type": "low", "price": 98}]
    assert confluence.current_levels(pivots, 100) == {"support": 98, "resistance": None}


def test_compute_rsi_all_gains_is_100():
    closes = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24]
    assert confluence.compute_rsi(closes, 14) == 100


def test_compute_rsi_all_losses_is_0():
    closes = [24, 23, 22, 21, 20, 19, 18, 17, 16, 15, 14, 13, 12, 11, 10]
    assert confluence.compute_rsi(closes, 14) == 0


def test_compute_rsi_balanced_alternating_is_50():
    closes = [10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10]
    assert confluence.compute_rsi(closes, 14) == 50


def test_compute_macd_constant_offset_on_linear_series():
    closes = [10, 12, 14, 16, 18, 20, 22]
    result = confluence.compute_macd(closes, 2, 4, 2)
    assert result == {"macd": 2, "signal": 2, "histogram": 0}


def test_compute_bollinger_zero_variance():
    closes = [100, 100, 100, 100]
    assert confluence.compute_bollinger(closes, 4, 2) == {"middle": 100, "upper": 100, "lower": 100}


def test_compute_bollinger_with_variance():
    closes = [0, 0, 4, 4]
    assert confluence.compute_bollinger(closes, 4, 2) == {"middle": 2, "upper": 6, "lower": -2}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_confluence.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gold_bot'`

- [ ] **Step 3: Write the implementation (part 1)**

Create `gold_bot/__init__.py` (empty).

Create `gold_bot/confluence.py`:

```python
# gold_bot/confluence.py
# Portage fidèle du moteur de confluence de docs/scalping.js (voir
# docs/superpowers/specs/2026-09-10-bot-trading-or-design.md). Le
# comportement du JS fait foi, y compris la condition de tendance
# volontairement inversée dans compute_signal (achat exige un contexte
# de tendance "baissier" près d'un support — stratégie de renversement
# sur structure, pas de suivi de tendance).
import math

import requests

TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"


def _parse_float(raw):
    """Comme parseFloat en JS : une valeur non numérique donne NaN plutôt
    que de lever, pour que le contrôle de validité en aval (math.isfinite)
    l'attrape au bon endroit plutôt qu'un crash prématuré."""
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float("nan")


def fetch_gold_candles(api_key: str) -> list[dict]:
    """Récupère les dernières bougies 1min XAU/USD via Twelve Data.
    Renvoie un tableau chronologique (plus ancien en premier), jamais
    vide en cas de succès. Lève RuntimeError au message clair en cas
    d'échec réseau, de quota dépassé, ou de réponse invalide."""
    params = {
        "symbol": "XAU/USD",
        "interval": "1min",
        "outputsize": "90",
        "timezone": "UTC",
        "apikey": api_key,
    }
    try:
        response = requests.get(TWELVE_DATA_URL, params=params, timeout=15)
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Impossible de contacter Twelve Data : {e}")
    if not response.ok:
        raise RuntimeError(f"Twelve Data a répondu {response.status_code}")
    data = response.json()
    if data.get("status") == "error" or not isinstance(data.get("values"), list):
        raise RuntimeError(f"Réponse Twelve Data invalide : {data.get('message', 'pas de données')}")
    candles = [
        {
            "time": v["datetime"],
            "open": _parse_float(v["open"]),
            "high": _parse_float(v["high"]),
            "low": _parse_float(v["low"]),
            "close": _parse_float(v["close"]),
        }
        for v in data["values"]
    ]
    candles.reverse()
    if not candles:
        raise RuntimeError("Twelve Data a renvoyé une liste de bougies vide")
    if any(not math.isfinite(c["close"]) for c in candles):
        raise RuntimeError("Twelve Data a renvoyé des valeurs de prix invalides")
    return candles


def detect_pivots(candles: list[dict], k: int) -> list[dict]:
    """Un pivot haut à l'index i : High[i] strictement supérieur aux High
    des k bougies avant ET après (symétrique pour un pivot bas sur Low)."""
    pivots = []
    for i in range(k, len(candles) - k):
        is_high = (
            all(candles[j]["high"] < candles[i]["high"] for j in range(i - k, i))
            and all(candles[j]["high"] < candles[i]["high"] for j in range(i + 1, i + 1 + k))
        )
        if is_high:
            pivots.append({"index": i, "type": "high", "price": candles[i]["high"]})
        is_low = (
            all(candles[j]["low"] > candles[i]["low"] for j in range(i - k, i))
            and all(candles[j]["low"] > candles[i]["low"] for j in range(i + 1, i + 1 + k))
        )
        if is_low:
            pivots.append({"index": i, "type": "low", "price": candles[i]["low"]})
    pivots.sort(key=lambda p: p["index"])
    return pivots


def classify_trend(pivots: list[dict]) -> str:
    """Haussier si les 2 derniers pivots bas ET les 2 derniers pivots hauts
    confirmés sont strictement croissants ; symétrique pour baissier ;
    neutre sinon (y compris si pas assez de pivots d'un type ou l'autre)."""
    lows = [p for p in pivots if p["type"] == "low"]
    highs = [p for p in pivots if p["type"] == "high"]
    if len(lows) < 2 or len(highs) < 2:
        return "neutre"
    last_lows = lows[-2:]
    last_highs = highs[-2:]
    lows_rising = last_lows[1]["price"] > last_lows[0]["price"]
    highs_rising = last_highs[1]["price"] > last_highs[0]["price"]
    lows_falling = last_lows[1]["price"] < last_lows[0]["price"]
    highs_falling = last_highs[1]["price"] < last_highs[0]["price"]
    if lows_rising and highs_rising:
        return "haussier"
    if lows_falling and highs_falling:
        return "baissier"
    return "neutre"


def current_levels(pivots: list[dict], current_price: float) -> dict:
    """Support = dernier pivot bas confirmé sous current_price ;
    résistance = dernier pivot haut confirmé au-dessus. None si aucun
    pivot de ce côté."""
    below_lows = [p for p in pivots if p["type"] == "low" and p["price"] < current_price]
    above_highs = [p for p in pivots if p["type"] == "high" and p["price"] > current_price]
    return {
        "support": below_lows[-1]["price"] if below_lows else None,
        "resistance": above_highs[-1]["price"] if above_highs else None,
    }


def compute_rsi(closes: list[float], period: int) -> float:
    """RSI classique : compare la moyenne des hausses à la moyenne des
    baisses sur les `period` dernières variations. 100 si aucune baisse
    (évite une division par zéro)."""
    changes = [closes[i] - closes[i - 1] for i in range(len(closes) - period, len(closes))]
    gains = [c for c in changes if c > 0]
    losses = [-c for c in changes if c < 0]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def _ema_last(values: list[float], period: int) -> float:
    """EMA seedée par une SMA des `period` premières valeurs. Dernière
    valeur uniquement."""
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def _ema_series(values: list[float], period: int) -> list[float]:
    """Série complète des EMA (une valeur par index à partir de
    period-1) — nécessaire pour la ligne signal du MACD."""
    k = 2 / (period + 1)
    out = []
    ema = sum(values[:period]) / period
    out.append(ema)
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
        out.append(ema)
    return out


def compute_macd(closes: list[float], fast_period: int, slow_period: int, signal_period: int) -> dict:
    """MACD(fast, slow, signal). Renvoie uniquement le dernier point."""
    fast_series = _ema_series(closes, fast_period)
    slow_series = _ema_series(closes, slow_period)
    offset = len(fast_series) - len(slow_series)
    macd_series = [fast_series[offset + i] - slow_series[i] for i in range(len(slow_series))]
    signal = _ema_last(macd_series, signal_period)
    macd = macd_series[-1]
    return {"macd": macd, "signal": signal, "histogram": macd - signal}


def compute_bollinger(closes: list[float], period: int, mult: float) -> dict:
    """Bandes de Bollinger : MM `period` ± mult × écart-type population.
    Dernier point uniquement. Non utilisé par compute_signal (comme dans
    le JS d'origine) — conservé pour parité de test."""
    window = closes[-period:]
    mean = sum(window) / period
    variance = sum((c - mean) ** 2 for c in window) / period
    stdev = variance ** 0.5
    return {"middle": mean, "upper": mean + mult * stdev, "lower": mean - mult * stdev}


def _body_size(c):
    return abs(c["close"] - c["open"])


def _is_bullish(c):
    return c["close"] > c["open"]


def _upper_wick(c):
    return c["high"] - max(c["open"], c["close"])


def _lower_wick(c):
    return min(c["open"], c["close"]) - c["low"]


def match_candlestick_pattern(candles: list[dict], trend: str) -> dict | None:
    """Reconnaît un sous-ensemble de 8 figures de chandeliers sur les 1 à
    3 dernières bougies. `trend` est la tendance courte au moment de
    l'examen — plusieurs figures n'ont de sens qu'en contexte. Renvoie la
    première figure trouvée (3 bougies avant 2 avant 1) ou None."""
    n = len(candles)
    if n < 1:
        return None
    last = candles[-1]

    if n >= 3:
        c1, c2, c3 = candles[-3:]
        if trend == "baissier" and not _is_bullish(c1) and _body_size(c2) < _body_size(c1) * 0.5 \
                and _is_bullish(c3) and c3["close"] > (c1["open"] + c1["close"]) / 2:
            return {"name": "Étoile du Matin", "direction": "haussier"}
        if trend == "haussier" and _is_bullish(c1) and _body_size(c2) < _body_size(c1) * 0.5 \
                and not _is_bullish(c3) and c3["close"] < (c1["open"] + c1["close"]) / 2:
            return {"name": "Étoile du Soir", "direction": "baissier"}

    if n >= 2:
        prev, cur = candles[-2:]
        if trend == "baissier" and not _is_bullish(prev) and _is_bullish(cur) \
                and cur["open"] <= prev["close"] and cur["close"] >= prev["open"]:
            return {"name": "Englobante haussière", "direction": "haussier"}
        if trend == "haussier" and _is_bullish(prev) and not _is_bullish(cur) \
                and cur["open"] >= prev["close"] and cur["close"] <= prev["open"]:
            return {"name": "Englobante baissière", "direction": "baissier"}
        if trend == "baissier" and not _is_bullish(prev) and _is_bullish(cur) \
                and cur["open"] < prev["close"] and cur["close"] > (prev["open"] + prev["close"]) / 2 \
                and cur["close"] < prev["open"]:
            return {"name": "Pénétrante", "direction": "haussier"}
        if trend == "haussier" and _is_bullish(prev) and not _is_bullish(cur) \
                and cur["open"] > prev["close"] and cur["close"] < (prev["open"] + prev["close"]) / 2 \
                and cur["close"] > prev["open"]:
            return {"name": "Nuage noir", "direction": "baissier"}

    body = _body_size(last)
    upper_wick = _upper_wick(last)
    lower_wick = _lower_wick(last)
    if trend == "baissier" and lower_wick >= body * 2 and upper_wick < body * 0.3:
        return {"name": "Marteau", "direction": "haussier"}
    if trend == "haussier" and upper_wick >= body * 2 and lower_wick < body * 0.3:
        return {"name": "Étoile filante", "direction": "baissier"}

    return None


SCALP_PIVOT_K = 3
SCALP_RSI_PERIOD = 14
SCALP_MACD_FAST = 12
SCALP_MACD_SLOW = 26
SCALP_MACD_SIGNAL = 9
SCALP_BOLLINGER_PERIOD = 20
SCALP_BOLLINGER_MULT = 2
SCALP_TAKEPROFIT_RISK_MULTIPLE = 1.5
SCALP_LEVEL_PROXIMITY = 0.5
SCALP_MIN_CANDLES = max(SCALP_BOLLINGER_PERIOD, SCALP_MACD_SLOW + SCALP_MACD_SIGNAL) + 1
SCALP_STOP_BUFFER = SCALP_LEVEL_PROXIMITY * 3


def compute_signal(candles: list[dict]) -> dict:
    """Moteur de confluence : combine tendance + S/R + indicateurs +
    chandeliers en un signal achat/vente/neutre, avec entry/stop_loss/
    take_profit si un signal est émis. Ne lève jamais d'exception —
    `candles` trop court renvoie neutre avec tous les prix à None."""
    price = candles[-1]["close"] if candles else None
    if not price or len(candles) < SCALP_MIN_CANDLES:
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": "neutre", "pattern": None}

    pivots = detect_pivots(candles, SCALP_PIVOT_K)
    trend = classify_trend(pivots)
    levels = current_levels(pivots, price)
    closes = [c["close"] for c in candles]
    rsi = compute_rsi(closes, SCALP_RSI_PERIOD)
    macd = compute_macd(closes, SCALP_MACD_FAST, SCALP_MACD_SLOW, SCALP_MACD_SIGNAL)
    pattern = match_candlestick_pattern(candles, trend)

    near_support = levels["support"] is not None and abs(price - levels["support"]) <= SCALP_LEVEL_PROXIMITY
    near_resistance = levels["resistance"] is not None and abs(price - levels["resistance"]) <= SCALP_LEVEL_PROXIMITY
    broke_resistance = levels["resistance"] is not None and price > levels["resistance"]
    broke_support = levels["support"] is not None and price < levels["support"]

    structurel_achat = trend == "baissier" and (near_support or broke_resistance)
    structurel_vente = trend == "haussier" and (near_resistance or broke_support)

    confirmation_achat = rsi < 70 and macd["macd"] > macd["signal"] and pattern is not None and pattern["direction"] == "haussier"
    confirmation_vente = rsi > 30 and macd["macd"] < macd["signal"] and pattern is not None and pattern["direction"] == "baissier"

    if structurel_achat and confirmation_achat:
        stop_loss = levels["support"] - SCALP_STOP_BUFFER if levels["support"] is not None else price - price * 0.001
        risk = price - stop_loss
        take_profit = levels["resistance"] if (levels["resistance"] is not None and levels["resistance"] > price) \
            else price + risk * SCALP_TAKEPROFIT_RISK_MULTIPLE
        return {"status": "achat", "price": price, "entry": price, "stop_loss": stop_loss,
                "take_profit": take_profit, "trend": trend, "pattern": pattern}
    if structurel_vente and confirmation_vente:
        stop_loss = levels["resistance"] + SCALP_STOP_BUFFER if levels["resistance"] is not None else price + price * 0.001
        risk = stop_loss - price
        take_profit = levels["support"] if (levels["support"] is not None and levels["support"] < price) \
            else price - risk * SCALP_TAKEPROFIT_RISK_MULTIPLE
        return {"status": "vente", "price": price, "entry": price, "stop_loss": stop_loss,
                "take_profit": take_profit, "trend": trend, "pattern": pattern}
    return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
            "take_profit": None, "trend": trend, "pattern": None}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_confluence.py -v`
Expected: 16 passed

- [ ] **Step 5: Write the failing tests (part 2 — new tests: candlestick patterns + compute_signal)**

These test cases do not exist in `docs/scalping.test.js` (a real gap in the original JS suite) — this Python port is about to drive real money once Plan B goes live, so it needs this coverage now. Append to `tests/gold_bot/test_confluence.py`:

```python
def _candle(open_, high, low, close):
    return {"time": "t", "open": open_, "high": high, "low": low, "close": close}


def test_match_candlestick_pattern_marteau():
    # Corps=2 (100->102), mèche basse=6 (>=2*2), mèche haute=0.5 (<2*0.3).
    candles = [_candle(100, 102.5, 94, 102)]
    result = confluence.match_candlestick_pattern(candles, "baissier")
    assert result == {"name": "Marteau", "direction": "haussier"}


def test_match_candlestick_pattern_etoile_filante():
    # Corps=2 (100->98), mèche haute=6 (>=2*2), mèche basse=0.5 (<2*0.3).
    candles = [_candle(100, 106, 97.5, 98)]
    result = confluence.match_candlestick_pattern(candles, "haussier")
    assert result == {"name": "Étoile filante", "direction": "baissier"}


def test_match_candlestick_pattern_englobante_haussiere():
    prev = _candle(100, 101, 94, 95)
    cur = _candle(94, 102, 93, 101)
    result = confluence.match_candlestick_pattern([prev, cur], "baissier")
    assert result == {"name": "Englobante haussière", "direction": "haussier"}


def test_match_candlestick_pattern_englobante_baissiere():
    prev = _candle(95, 101, 94, 100)
    cur = _candle(101, 102, 93, 94)
    result = confluence.match_candlestick_pattern([prev, cur], "haussier")
    assert result == {"name": "Englobante baissière", "direction": "baissier"}


def test_match_candlestick_pattern_penetrante():
    prev = _candle(100, 101, 89, 90)
    cur = _candle(88, 98, 87, 97)
    result = confluence.match_candlestick_pattern([prev, cur], "baissier")
    assert result == {"name": "Pénétrante", "direction": "haussier"}


def test_match_candlestick_pattern_nuage_noir():
    prev = _candle(90, 101, 89, 100)
    cur = _candle(102, 103, 92, 93)
    result = confluence.match_candlestick_pattern([prev, cur], "haussier")
    assert result == {"name": "Nuage noir", "direction": "baissier"}


def test_match_candlestick_pattern_etoile_du_matin():
    c1 = _candle(100, 101, 89, 90)
    c2 = _candle(89, 90, 87, 88)
    c3 = _candle(87, 98, 86, 97)
    result = confluence.match_candlestick_pattern([c1, c2, c3], "baissier")
    assert result == {"name": "Étoile du Matin", "direction": "haussier"}


def test_match_candlestick_pattern_etoile_du_soir():
    c1 = _candle(90, 101, 89, 100)
    c2 = _candle(101, 103, 100, 102)
    c3 = _candle(103, 104, 92, 93)
    result = confluence.match_candlestick_pattern([c1, c2, c3], "haussier")
    assert result == {"name": "Étoile du Soir", "direction": "baissier"}


def test_match_candlestick_pattern_none_when_no_match():
    candles = [_candle(100, 100.2, 99.8, 100.1)]
    assert confluence.match_candlestick_pattern(candles, "neutre") is None


def test_compute_signal_neutre_when_too_few_candles():
    candles = [_candle(100, 101, 99, 100.5) for _ in range(5)]
    result = confluence.compute_signal(candles)
    assert result["status"] == "neutre"
    assert result["entry"] is None
    assert result["stop_loss"] is None
    assert result["take_profit"] is None


def _build_bearish_then_hammer_candles():
    """36 bougies : déclin régulier en zigzag (crée des pivots hauts et
    bas nets et décroissants, donc trend='baissier' avec K=3), suivi
    d'une bougie Marteau finale nette. Construit pour amener
    compute_signal() à une confluence d'achat complète.

    Les valeurs exactes de RSI/MACD sur 36 bougies ne sont pas dérivées
    à la main (trop complexe) — CE TEST DOIT ÊTRE EXÉCUTÉ pendant le
    développement pour confirmer qu'il produit bien un signal 'achat'.
    Si ce n'est pas le cas, ajuste les paramètres du déclin (pas du
    zigzag, ampleur) jusqu'à ce que les assertions passent — ne les
    affaiblis jamais pour les faire passer artificiellement ; les
    fonctions composantes (detect_pivots, classify_trend, compute_rsi,
    compute_macd, match_candlestick_pattern) ont déjà leurs propres
    tests à valeurs exactes ci-dessus, ce test est un test d'intégration
    qui verrouille le comportement réel, pas une réinvention du calcul."""
    candles = []
    price = 2100.0
    for i in range(35):
        step = -0.8 if i % 4 != 3 else -2.5
        open_ = price
        close = price + step
        high = max(open_, close) + 0.3
        low = min(open_, close) - 0.3
        candles.append(_candle(open_, high, low, close))
        price = close
    last_open = price
    last_close = price + 1.5
    last_low = min(last_open, last_close) - 6
    last_high = max(last_open, last_close) + 0.2
    candles.append(_candle(last_open, last_high, last_low, last_close))
    return candles


def test_compute_signal_full_achat_scenario():
    candles = _build_bearish_then_hammer_candles()
    result = confluence.compute_signal(candles)
    assert result["status"] == "achat"
    assert result["trend"] == "baissier"
    assert result["pattern"]["name"] == "Marteau"
    assert result["entry"] == candles[-1]["close"]
    assert result["stop_loss"] < result["entry"] < result["take_profit"]


def _build_bullish_then_shooting_star_candles():
    """Symétrique de _build_bearish_then_hammer_candles : montée en
    zigzag (trend='haussier'), bougie finale Étoile filante nette.
    Même remarque : exécuter pendant le développement pour confirmer le
    signal 'vente', ajuster l'ampleur du zigzag si besoin."""
    candles = []
    price = 2100.0
    for i in range(35):
        step = 0.8 if i % 4 != 3 else 2.5
        open_ = price
        close = price + step
        high = max(open_, close) + 0.3
        low = min(open_, close) - 0.3
        candles.append(_candle(open_, high, low, close))
        price = close
    last_open = price
    last_close = price - 1.5
    last_high = max(last_open, last_close) + 6
    last_low = min(last_open, last_close) - 0.2
    candles.append(_candle(last_open, last_high, last_low, last_close))
    return candles


def test_compute_signal_full_vente_scenario():
    candles = _build_bullish_then_shooting_star_candles()
    result = confluence.compute_signal(candles)
    assert result["status"] == "vente"
    assert result["trend"] == "haussier"
    assert result["pattern"]["name"] == "Étoile filante"
    assert result["entry"] == candles[-1]["close"]
    assert result["take_profit"] < result["entry"] < result["stop_loss"]


def test_compute_signal_neutre_when_no_confluence():
    # Bougies plates : aucun pivot net, donc trend='neutre', aucune
    # structure achat/vente possible -> neutre garanti quel que soit le
    # reste (pas besoin de dériver RSI/MACD pour ce cas).
    candles = [_candle(2100 + (i % 2) * 0.01, 2100.1, 2099.9, 2100) for i in range(40)]
    result = confluence.compute_signal(candles)
    assert result["status"] == "neutre"
    assert result["entry"] is None
```

- [ ] **Step 6: Run tests to verify they pass — and fix the two integration scenarios if needed**

Run: `python -m pytest tests/gold_bot/test_confluence.py -v`

Expected: all tests pass. **If `test_compute_signal_full_achat_scenario` or `test_compute_signal_full_vente_scenario` fail** (e.g. RSI or MACD conditions aren't satisfied by the constructed series), do not weaken the assertions — instead adjust the candle-generation helper's numeric parameters (the `step` sizes, the final reversal candle's wick length) until the full confluence genuinely fires, then re-run. Use a throwaway Python REPL (`python3 -c "..."` or a scratch script) to inspect `confluence.compute_signal(candles)`'s actual RSI/MACD/pattern values while tuning, then delete the scratch script before committing.

- [ ] **Step 7: Commit**

```bash
git add gold_bot/__init__.py gold_bot/confluence.py tests/gold_bot/__init__.py tests/gold_bot/test_confluence.py
git commit -m "feat(gold-bot): portage Python du moteur de confluence (docs/scalping.js)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `gold_bot/risk.py` — dimensionnement par le risque + coupe-circuit

**Files:**
- Create: `gold_bot/risk.py`
- Test: `tests/gold_bot/test_risk.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `compute_position_size(balance, entry, stop_loss, contract_size, risk_pct=0.05) -> float`, `CircuitBreaker(threshold_pct=0.10, now_fn=None)` with methods `check(current_balance)` and `can_open_position(current_balance) -> bool` — both consumed by Task 5's `bot.py`.

- [ ] **Step 1: Write the failing tests**

Create `tests/gold_bot/test_risk.py`:

```python
from datetime import date, datetime, timezone

import pytest
import gold_bot.risk as risk


def test_compute_position_size_basic():
    # Risque 5% de 10000 = 500. Distance entrée->stop = 5. contract_size=100.
    # size = 500 / (5 * 100) = 1.0
    size = risk.compute_position_size(balance=10000, entry=2100, stop_loss=2095, contract_size=100)
    assert size == pytest.approx(1.0)


def test_compute_position_size_scales_with_risk_pct():
    size = risk.compute_position_size(balance=10000, entry=2100, stop_loss=2095, contract_size=100, risk_pct=0.10)
    assert size == pytest.approx(2.0)


def test_compute_position_size_raises_on_zero_distance():
    with pytest.raises(ValueError, match="nulle"):
        risk.compute_position_size(balance=10000, entry=2100, stop_loss=2100, contract_size=100)


def test_circuit_breaker_allows_when_under_threshold():
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)  # fixe le solde de départ du jour
    assert cb.can_open_position(9500) is True  # -5%, sous le seuil de -10%


def test_circuit_breaker_blocks_when_threshold_exceeded():
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)
    assert cb.can_open_position(8900) is False  # -11%, au-delà du seuil


def test_circuit_breaker_uses_fixed_starting_balance_not_current():
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)
    cb.can_open_position(9200)  # -8%, toujours autorisé
    # Un deuxième appel le même jour ne doit pas re-fixer le solde de
    # référence à 9200 — le seuil doit rester calculé contre 10000.
    assert cb.can_open_position(8900) is False  # -11% depuis 10000, pas depuis 9200


def test_circuit_breaker_resets_on_new_day():
    clock = {"now": datetime(2026, 9, 10, 23, 0, tzinfo=timezone.utc)}
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: clock["now"])
    cb.check(10000)
    cb.can_open_position(8900)  # -11%, coupe-circuit déclenché le 10
    clock["now"] = datetime(2026, 9, 11, 1, 0, tzinfo=timezone.utc)  # jour suivant UTC
    # Le solde de référence doit être refixé au solde courant (8900) au
    # premier appel du nouveau jour, donc plus aucune perte accumulée.
    assert cb.can_open_position(8900) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_risk.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gold_bot.risk'`

- [ ] **Step 3: Write the implementation**

Create `gold_bot/risk.py`:

```python
# gold_bot/risk.py
# Dimensionnement de position par le risque et coupe-circuit journalier
# — voir docs/superpowers/specs/2026-09-10-bot-trading-or-design.md.
from datetime import datetime, timezone


def compute_position_size(balance: float, entry: float, stop_loss: float,
                           contract_size: float, risk_pct: float = 0.05) -> float:
    """Dimensionnement par le risque : la perte si le stop-loss est
    touché vaut risk_pct * balance — pas la valeur notionnelle engagée.
    Lève ValueError si entry == stop_loss (risque nul, division par zéro
    évitée explicitement plutôt que renvoyer une taille infinie)."""
    distance = abs(entry - stop_loss)
    if distance == 0:
        raise ValueError("La distance entrée→stop-loss ne peut pas être nulle")
    risk_amount = balance * risk_pct
    return risk_amount / (distance * contract_size)


class CircuitBreaker:
    """Coupe-circuit journalier : bloque l'ouverture de nouvelles
    positions si la perte cumulée du jour dépasse threshold_pct du solde
    fixé au début de la journée UTC courante. Ne ferme jamais de
    position existante — seul un filtre sur can_open_position().
    `now_fn` est injectable pour les tests (horloge fixe/contrôlable)."""

    def __init__(self, threshold_pct: float = 0.10, now_fn=None):
        self.threshold_pct = threshold_pct
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._day = None
        self._starting_balance = None

    def check(self, current_balance: float) -> None:
        """À appeler avant toute décision — fixe le solde de référence
        du jour s'il n'existe pas encore ou si on a changé de jour UTC."""
        today = self._now_fn().date()
        if self._day != today:
            self._day = today
            self._starting_balance = current_balance

    def can_open_position(self, current_balance: float) -> bool:
        self.check(current_balance)
        loss = self._starting_balance - current_balance
        return loss < self._starting_balance * self.threshold_pct
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_risk.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add gold_bot/risk.py tests/gold_bot/test_risk.py
git commit -m "feat(gold-bot): dimensionnement par le risque + coupe-circuit journalier

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `gold_bot/state.py` — état persistant local

**Files:**
- Create: `gold_bot/state.py`
- Test: `tests/gold_bot/test_state.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `load_state(path=STATE_PATH) -> dict` (keys: `kill_switch: bool, dry_run: bool`), `save_state(state: dict, path=STATE_PATH) -> None`. Not consumed by Task 5 in this plan (Plan A hardcodes dry-run behavior directly in `bot.py`, per Global Constraints) — this module exists now so Plan B can build the kill-switch API on top of it without touching `bot.py`'s orchestration logic again.

- [ ] **Step 1: Write the failing tests**

Create `tests/gold_bot/test_state.py`:

```python
import json

import gold_bot.state as state


def test_load_state_returns_fallback_when_file_absent(tmp_path):
    path = str(tmp_path / "does_not_exist" / "state.json")
    result = state.load_state(path)
    assert result == {"kill_switch": False, "dry_run": True}


def test_load_state_returns_fallback_on_corrupted_json(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not valid json", encoding="utf-8")
    result = state.load_state(str(path))
    assert result == {"kill_switch": False, "dry_run": True}


def test_save_state_then_load_state_round_trips(tmp_path):
    path = str(tmp_path / "nested" / "state.json")
    state.save_state({"kill_switch": True, "dry_run": False}, path)
    result = state.load_state(path)
    assert result == {"kill_switch": True, "dry_run": False}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gold_bot.state'`

- [ ] **Step 3: Write the implementation**

Create `gold_bot/state.py`:

```python
# gold_bot/state.py
# État persistant local du bot (interrupteur d'urgence, mode simulation)
# — voir docs/superpowers/specs/2026-09-10-bot-trading-or-design.md.
import json
import os

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")


def load_state(path: str = STATE_PATH) -> dict:
    """État de repli si le fichier n'existe pas encore ou est illisible
    — jamais d'exception au démarrage du bot."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"kill_switch": False, "dry_run": True}


def save_state(state: dict, path: str = STATE_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_state.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add gold_bot/state.py tests/gold_bot/test_state.py
git commit -m "feat(gold-bot): état persistant local (interrupteur d'urgence, dry-run)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: `gold_bot/broker.py` — appels MetaApi (compte de trading réel)

**Files:**
- Create: `gold_bot/broker.py`
- Test: `tests/gold_bot/test_broker.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `get_account_balance(token, account_id, region="london") -> float`, `get_open_positions(token, account_id, region="london") -> list[dict]`, `get_symbol_specification(token, account_id, symbol, region="london") -> dict` (includes `contractSize`), `place_market_order(token, account_id, symbol, direction, volume, stop_loss, take_profit, region="london") -> dict` (`direction` : `"achat"` ou `"vente"`), `close_position(token, account_id, position_id, region="london") -> dict` — all consumed by Task 5's `bot.py`, but **`place_market_order`/`close_position` are never actually called by `bot.py` in this plan** (Plan B wires them).

These endpoints were verified against MetaApi's own documentation
(`https://metaapi.cloud/docs/client/restApi/`) before writing this task —
use the exact shapes below, do not invent alternatives:
- `GET /users/current/accounts/{accountId}/account-information` → `{"balance": ..., "currency": ..., ...}`
- `GET /users/current/accounts/{accountId}/positions` → `[...]` (same shape `portfolio_sync_mt5.py` already reads, but from the trading-capable account, not the read-only one)
- `GET /users/current/accounts/{accountId}/symbols/{symbol}/specification` → `{"contractSize": ..., "tickSize": ..., ...}`
- `POST /users/current/accounts/{accountId}/trade` with body `{"actionType": "ORDER_TYPE_BUY"|"ORDER_TYPE_SELL", "symbol": ..., "volume": ..., "stopLoss": ..., "takeProfit": ...}` → `{"orderId": ..., ...}`
- `POST /users/current/accounts/{accountId}/trade` with body `{"actionType": "POSITION_CLOSE_ID", "positionId": ...}` → `{"orderId": ..., "positionId": ..., ...}`

All require header `auth-token: {token}`; the base URL follows the same
region pattern already used by `portfolio_sync_mt5.py`:
`https://mt-client-api-v1.{region}.agiliumtrade.ai`.

- [ ] **Step 1: Write the failing tests**

Create `tests/gold_bot/test_broker.py`:

```python
import pytest
import gold_bot.broker as broker


class _FakeMT5Response:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise broker.requests.exceptions.HTTPError(
                f"{self.status_code} Client Error", response=self
            )

    def json(self):
        return self._json_data


def test_get_account_balance_returns_balance(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return _FakeMT5Response({"balance": 10234.5, "currency": "USD"})

    monkeypatch.setattr(broker.requests, "get", fake_get)
    result = broker.get_account_balance("tok", "acc123", region="london")

    assert result == 10234.5
    assert captured["url"] == (
        "https://mt-client-api-v1.london.agiliumtrade.ai"
        "/users/current/accounts/acc123/account-information"
    )
    assert captured["headers"] == {"auth-token": "tok"}


def test_get_account_balance_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(broker.requests, "get", lambda *a, **k: _FakeMT5Response({}, status_code=401))
    with pytest.raises(broker.requests.exceptions.HTTPError):
        broker.get_account_balance("bad-tok", "acc123")


def test_get_open_positions_returns_list(monkeypatch):
    raw = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY"}]
    monkeypatch.setattr(broker.requests, "get", lambda *a, **k: _FakeMT5Response(raw))
    result = broker.get_open_positions("tok", "acc123")
    assert result == raw


def test_get_symbol_specification_returns_contract_size(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        return _FakeMT5Response({"contractSize": 100, "tickSize": 0.01})

    monkeypatch.setattr(broker.requests, "get", fake_get)
    result = broker.get_symbol_specification("tok", "acc123", "XAUUSD")

    assert result["contractSize"] == 100
    assert captured["url"].endswith("/users/current/accounts/acc123/symbols/XAUUSD/specification")


def test_place_market_order_sends_correct_body_for_achat(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeMT5Response({"orderId": "999"})

    monkeypatch.setattr(broker.requests, "post", fake_post)
    result = broker.place_market_order("tok", "acc123", "XAUUSD", "achat", 1.0, 2095, 2110)

    assert result == {"orderId": "999"}
    assert captured["json"] == {
        "actionType": "ORDER_TYPE_BUY",
        "symbol": "XAUUSD",
        "volume": 1.0,
        "stopLoss": 2095,
        "takeProfit": 2110,
    }
    assert captured["url"].endswith("/users/current/accounts/acc123/trade")


def test_place_market_order_sends_correct_body_for_vente(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _FakeMT5Response({"orderId": "998"})

    monkeypatch.setattr(broker.requests, "post", fake_post)
    broker.place_market_order("tok", "acc123", "XAUUSD", "vente", 1.0, 2110, 2095)

    assert captured["json"]["actionType"] == "ORDER_TYPE_SELL"


def test_close_position_sends_correct_body(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _FakeMT5Response({"orderId": "997", "positionId": "46648037"})

    monkeypatch.setattr(broker.requests, "post", fake_post)
    result = broker.close_position("tok", "acc123", "46648037")

    assert result == {"orderId": "997", "positionId": "46648037"}
    assert captured["json"] == {"actionType": "POSITION_CLOSE_ID", "positionId": "46648037"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_broker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gold_bot.broker'`

- [ ] **Step 3: Write the implementation**

Create `gold_bot/broker.py`:

```python
# gold_bot/broker.py
# Appels à l'API de trading MetaApi — compte connecté avec le mot de
# passe de trading réel, distinct du compte lecture seule utilisé par
# portfolio_sync_mt5.py. Voir
# docs/superpowers/specs/2026-09-10-bot-trading-or-design.md.
import requests

DEFAULT_MT5_REGION = "london"


def _base_url(region: str) -> str:
    return f"https://mt-client-api-v1.{region}.agiliumtrade.ai"


def get_account_balance(token: str, account_id: str, region: str = DEFAULT_MT5_REGION) -> float:
    resp = requests.get(
        f"{_base_url(region)}/users/current/accounts/{account_id}/account-information",
        headers={"auth-token": token},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["balance"]


def get_open_positions(token: str, account_id: str, region: str = DEFAULT_MT5_REGION) -> list[dict]:
    resp = requests.get(
        f"{_base_url(region)}/users/current/accounts/{account_id}/positions",
        headers={"auth-token": token},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_symbol_specification(token: str, account_id: str, symbol: str,
                              region: str = DEFAULT_MT5_REGION) -> dict:
    resp = requests.get(
        f"{_base_url(region)}/users/current/accounts/{account_id}/symbols/{symbol}/specification",
        headers={"auth-token": token},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def place_market_order(token: str, account_id: str, symbol: str, direction: str, volume: float,
                        stop_loss: float, take_profit: float, region: str = DEFAULT_MT5_REGION) -> dict:
    """direction : "achat" ou "vente" (convention interne du projet) —
    traduit en ORDER_TYPE_BUY/ORDER_TYPE_SELL attendu par MetaApi."""
    action_type = "ORDER_TYPE_BUY" if direction == "achat" else "ORDER_TYPE_SELL"
    resp = requests.post(
        f"{_base_url(region)}/users/current/accounts/{account_id}/trade",
        headers={"auth-token": token},
        json={
            "actionType": action_type,
            "symbol": symbol,
            "volume": volume,
            "stopLoss": stop_loss,
            "takeProfit": take_profit,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def close_position(token: str, account_id: str, position_id: str,
                    region: str = DEFAULT_MT5_REGION) -> dict:
    resp = requests.post(
        f"{_base_url(region)}/users/current/accounts/{account_id}/trade",
        headers={"auth-token": token},
        json={"actionType": "POSITION_CLOSE_ID", "positionId": position_id},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_broker.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add gold_bot/broker.py tests/gold_bot/test_broker.py
git commit -m "feat(gold-bot): appels MetaApi côté compte de trading réel

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: `gold_bot/bot.py` — orchestration (simulation uniquement)

**Files:**
- Create: `gold_bot/bot.py`
- Test: `tests/gold_bot/test_bot.py`

**Interfaces:**
- Consumes: `confluence.compute_signal(candles) -> dict` (Task 1), `risk.compute_position_size(...)` and `risk.CircuitBreaker` (Task 2), `broker.get_open_positions(token, account_id, region)` (Task 4, exact signature).
- Produces: `decide_and_act(candles, contract_size, balance, open_positions, circuit_breaker, symbol="XAUUSD") -> dict` and `reconcile_positions(token, account_id, region=broker.DEFAULT_MT5_REGION) -> list[dict]` — the entry points Plan B's always-on loop will call. `decide_and_act` deliberately takes no `token`/`account_id` — it never calls the broker itself (see below); Plan B's live-wiring code calls `broker.place_market_order`/`close_position` separately, using the fields already present in `decide_and_act`'s returned `steps`.

**Critical: `decide_and_act` must never call `broker.place_market_order` or
`broker.close_position` in this plan.** It only returns a structured
description (`"action": "simulation"`, with a `"steps"` list) of what it
would have done — this is what makes Plan A safe to merge and run before
Plan B exists.

- [ ] **Step 1: Write the failing tests**

Create `tests/gold_bot/test_bot.py`:

```python
import pytest
import gold_bot.bot as bot
import gold_bot.risk as risk


def _signal(status, entry=None, stop_loss=None, take_profit=None):
    return {"status": status, "price": entry, "entry": entry, "stop_loss": stop_loss,
            "take_profit": take_profit, "trend": "baissier" if status != "neutre" else "neutre",
            "pattern": None}


def _open_circuit_breaker():
    # Aucun appel à check() préalable avec un solde bas -> autorise
    # toujours (solde de départ = solde courant passé au premier appel).
    return risk.CircuitBreaker()


def test_decide_and_act_no_action_on_neutral_signal(monkeypatch):
    monkeypatch.setattr(bot.confluence, "compute_signal", lambda candles: _signal("neutre"))
    result = bot.decide_and_act([], 100, 10000, [], _open_circuit_breaker())
    assert result["action"] == "aucune"
    assert result["reason"] == "signal neutre"


def test_decide_and_act_opens_when_no_existing_position(monkeypatch):
    monkeypatch.setattr(
        bot.confluence, "compute_signal",
        lambda candles: _signal("achat", entry=2100, stop_loss=2095, take_profit=2115),
    )
    result = bot.decide_and_act([], 100, 10000, [], _open_circuit_breaker())
    assert result["action"] == "simulation"
    assert len(result["steps"]) == 1
    step = result["steps"][0]
    assert step["type"] == "ouverture_simulee"
    assert step["direction"] == "achat"
    assert step["volume"] == pytest.approx(1.0)  # (10000*0.05) / (5*100)


def test_decide_and_act_no_action_when_same_direction_already_open(monkeypatch):
    monkeypatch.setattr(
        bot.confluence, "compute_signal",
        lambda candles: _signal("achat", entry=2100, stop_loss=2095, take_profit=2115),
    )
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY"}]
    result = bot.decide_and_act([], 100, 10000, existing, _open_circuit_breaker())
    assert result["action"] == "aucune"
    assert result["reason"] == "position déjà ouverte dans le même sens"


def test_decide_and_act_closes_then_opens_on_opposite_signal(monkeypatch):
    monkeypatch.setattr(
        bot.confluence, "compute_signal",
        lambda candles: _signal("vente", entry=2100, stop_loss=2110, take_profit=2085),
    )
    existing = [{"id": "1", "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY"}]
    result = bot.decide_and_act([], 100, 10000, existing, _open_circuit_breaker())
    assert result["action"] == "simulation"
    assert len(result["steps"]) == 2
    assert result["steps"][0] == {"type": "clôture_simulee", "position_id": "1", "symbol": "XAUUSD"}
    assert result["steps"][1]["type"] == "ouverture_simulee"
    assert result["steps"][1]["direction"] == "vente"


def test_decide_and_act_blocked_by_circuit_breaker():
    from datetime import datetime, timezone
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)  # solde de départ du jour

    import gold_bot.confluence as confluence
    real_compute_signal = confluence.compute_signal
    try:
        confluence.compute_signal = lambda candles: _signal("achat", entry=2100, stop_loss=2095, take_profit=2115)
        result = bot.decide_and_act([], 100, 8900, [], cb)  # -11% depuis 10000
    finally:
        confluence.compute_signal = real_compute_signal

    assert result["action"] == "aucune"
    assert result["reason"] == "coupe-circuit journalier déclenché"


def test_decide_and_act_never_calls_real_broker_order_functions(monkeypatch):
    """Garde-fou explicite de ce plan : même avec un signal achat clair
    et aucune position existante, decide_and_act ne doit jamais appeler
    broker.place_market_order ni broker.close_position."""
    monkeypatch.setattr(
        bot.confluence, "compute_signal",
        lambda candles: _signal("achat", entry=2100, stop_loss=2095, take_profit=2115),
    )
    called = {"place": False, "close": False}
    monkeypatch.setattr(bot.broker, "place_market_order", lambda *a, **k: called.__setitem__("place", True))
    monkeypatch.setattr(bot.broker, "close_position", lambda *a, **k: called.__setitem__("close", True))

    bot.decide_and_act([], 100, 10000, [], _open_circuit_breaker())

    assert called == {"place": False, "close": False}


def test_reconcile_positions_calls_broker(monkeypatch):
    captured = {}

    def fake_get_open_positions(token, account_id, region=bot.broker.DEFAULT_MT5_REGION):
        captured["args"] = (token, account_id, region)
        return [{"id": "1", "symbol": "XAUUSD"}]

    monkeypatch.setattr(bot.broker, "get_open_positions", fake_get_open_positions)
    result = bot.reconcile_positions("tok", "acc123")

    assert result == [{"id": "1", "symbol": "XAUUSD"}]
    assert captured["args"] == ("tok", "acc123", bot.broker.DEFAULT_MT5_REGION)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_bot.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gold_bot.bot'`

- [ ] **Step 3: Write the implementation**

Create `gold_bot/bot.py`:

```python
# gold_bot/bot.py
# Orchestration du bot : combine confluence + risque + broker selon les
# règles de renversement/empilement du spec. DANS CE PLAN (Plan A),
# decide_and_act() ne place et ne clôture JAMAIS de vraie position — il
# ne fait que journaliser ce qu'il ferait. Voir
# docs/superpowers/specs/2026-09-10-bot-trading-or-design.md.
import gold_bot.broker as broker
import gold_bot.confluence as confluence
import gold_bot.risk as risk


def decide_and_act(candles: list[dict], contract_size: float, balance: float,
                    open_positions: list[dict], circuit_breaker: "risk.CircuitBreaker",
                    symbol: str = "XAUUSD") -> dict:
    """Cœur de la boucle : évalue le signal, applique les règles de
    renversement/empilement, et — dans ce plan, TOUJOURS en simulation —
    journalise ce qu'il ferait sans jamais appeler
    broker.place_market_order / broker.close_position. `open_positions`
    doit provenir d'un appel récent à reconcile_positions (jamais d'un
    état local qui pourrait être périmé). Ne prend délibérément pas de
    token/account_id : elle ne parle jamais elle-même au broker — le
    code de câblage réel (Plan B) appellera broker.place_market_order/
    close_position séparément, avec les champs déjà présents dans
    `steps` ci-dessous."""
    signal = confluence.compute_signal(candles)

    existing = next((p for p in open_positions if p.get("symbol") == symbol), None)
    existing_direction = None
    if existing is not None:
        existing_direction = "achat" if existing.get("type") == "POSITION_TYPE_BUY" else "vente"

    if signal["status"] == "neutre":
        return {"action": "aucune", "reason": "signal neutre"}

    if existing_direction == signal["status"]:
        return {"action": "aucune", "reason": "position déjà ouverte dans le même sens"}

    if not circuit_breaker.can_open_position(balance):
        return {"action": "aucune", "reason": "coupe-circuit journalier déclenché"}

    steps = []
    if existing_direction is not None and existing_direction != signal["status"]:
        steps.append({"type": "clôture_simulee", "position_id": existing.get("id"), "symbol": symbol})

    size = risk.compute_position_size(balance, signal["entry"], signal["stop_loss"], contract_size)
    steps.append({
        "type": "ouverture_simulee",
        "symbol": symbol,
        "direction": signal["status"],
        "volume": size,
        "entry": signal["entry"],
        "stop_loss": signal["stop_loss"],
        "take_profit": signal["take_profit"],
    })
    return {"action": "simulation", "steps": steps}


def reconcile_positions(token: str, account_id: str, region: str = broker.DEFAULT_MT5_REGION) -> list[dict]:
    """Interroge MetaApi pour l'état réel des positions avant toute
    décision — jamais de confiance aveugle en un état local qui
    pourrait être périmé après un redémarrage/crash du bot."""
    return broker.get_open_positions(token, account_id, region)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_bot.py -v`
Expected: 7 passed

- [ ] **Step 5: Run the full repo test suite (regression check)**

Run: `python -m pytest`
Expected: all pre-existing tests still pass, plus the new `gold_bot` tests.

- [ ] **Step 6: Commit**

```bash
git add gold_bot/bot.py tests/gold_bot/test_bot.py
git commit -m "feat(gold-bot): orchestration achat/vente/renversement (simulation uniquement)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
