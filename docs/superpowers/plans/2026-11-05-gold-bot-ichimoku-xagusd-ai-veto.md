# Moteur Ichimoku+XAGUSD avec veto IA branché — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Porter en code de production un moteur Ichimoku deux-jambes calibré sur XAGUSD (le candidat le plus robuste trouvé dans toute la recherche FTMO de ce projet), et brancher pour de vrai (fail-open) le veto IA jusqu'ici en mode fantôme — sans aucun impact sur les deux instances déjà en production (bot personnel VWAP, compte FTMO Fibonacci).

**Architecture:** Nouveau module `gold_bot/ichimoku_reversion.py` (indicateurs + état causal, calqué sur `fibonacci_reversion.py`) + nouvelle fonction `decide_and_act_ichimoku` dans `gold_bot/bot.py` (même contrat que `decide_and_act_fibonacci`) + généralisation de `gold_bot/ai_shadow.py` (le veto devient réutilisable par plusieurs moteurs, et distingue mode fantôme vs décision réelle) + sélection du moteur par variable d'environnement dans `gold_bot/loop.py`, avec non-régression stricte quand cette variable est absente.

**Tech Stack:** Python, pytest, SDK Anthropic (déjà utilisé par `ai_shadow.py`).

**Spec:** `docs/superpowers/specs/2026-11-05-gold-bot-ichimoku-xagusd-ai-veto-design.md`

## Global Constraints

- Ne modifie jamais le comportement de `decide_and_act_fibonacci` (compte FTMO actuel) ni `decide_and_act_vwap` (bot personnel) de façon observable — tous les tests existants de `tests/gold_bot/test_bot.py` et `tests/gold_bot/test_loop.py` doivent continuer à passer sans modification de leur côté.
- Paramètres du moteur Ichimoku walk-forward-validés, à copier tels quels, jamais ré-optimisés : `TENKAN_PERIOD=9`, `KIJUN_PERIOD=30`, `TARGET_R=2.0`, `SENKOU_B_PERIOD=52`, `CLOUD_SHIFT=26`, `STOP_BUFFER_ATR_MULT=0.5`, `TRAIL_BUFFER_ATR_MULT=0.5`, `PARTIAL_TP_PCT=0.7`, `ATR_PERIOD=14`, `MIN_DISTANCE_PCT=0.002`, `SESSION_END_HOUR_UTC=22`.
- Symbole : `XAGUSD` uniquement, `CONTRACT_SIZE=5000.0`, `MAX_VOLUME=20.0`, `VOLUME_STEP=0.01`, `MIN_VOLUME=0.01` (vérifiés empiriquement via le broker réel, voir spec).
- Veto IA fail-open : une erreur/timeout/réponse non parseable/cooldown (verdict `None`) laisse le trade se faire normalement. Seul un verdict explicite `"opposerait"` bloque.
- Aucune ouverture de compte FTMO réel dans ce plan — déploiement hors scope ici, traité séparément une fois le code mergé.

---

### Task 1: Moteur Ichimoku `gold_bot/ichimoku_reversion.py`

**Files:**
- Create: `gold_bot/ichimoku_reversion.py`
- Test: `tests/gold_bot/test_ichimoku_reversion.py`

**Interfaces:**
- Consumes: rien d'autre que la stdlib (`collections.deque`, `datetime`).
- Produces: `resample_h1(candles_5min: list[dict]) -> list[dict]`, `compute_atr_h1(candles_h1: list[dict], period: int = ATR_PERIOD) -> list[float | None]`, `compute_ichimoku(candles_h1: list[dict], tenkan_period=TENKAN_PERIOD, kijun_period=KIJUN_PERIOD, senkou_b_period=SENKOU_B_PERIOD, shift=CLOUD_SHIFT) -> dict` (clés `tenkan`/`kijun`/`senkou_a`/`senkou_b`, listes parallèles à `candles_h1`), `latest_state(candles_5min: list[dict]) -> dict | None` (clés `close`/`high`/`low`/`atr`/`tenkan`/`tenkan_prev`/`kijun`/`kijun_prev`/`senkou_a`/`senkou_b`/`hour_utc`), `signal(state: dict) -> str | None` (`"achat"`/`"vente"`/`None`), `compute_trailing_stop(candles_5min: list[dict], direction: str, entry_time_iso: str, entry_price: float, current_stop_loss: float) -> float | None`. Constantes de module : `SYMBOL`, `CONTRACT_SIZE`, `MAX_VOLUME`, `VOLUME_STEP`, `MIN_VOLUME`, `ATR_PERIOD`, `TENKAN_PERIOD`, `KIJUN_PERIOD`, `SENKOU_B_PERIOD`, `CLOUD_SHIFT`, `STOP_BUFFER_ATR_MULT`, `TRAIL_BUFFER_ATR_MULT`, `TARGET_R`, `PARTIAL_TP_PCT`, `MIN_DISTANCE_PCT`, `SESSION_END_HOUR_UTC`, `MIN_H1_CANDLES`.

- [ ] **Step 1: Write the failing tests (indicateurs de base)**

```python
# tests/gold_bot/test_ichimoku_reversion.py
from datetime import datetime, timezone

import gold_bot.ichimoku_reversion as ichimoku_reversion


def _candle(time_str, o, h, l, c, vol=100):
    return {"time": time_str, "open": o, "high": h, "low": l, "close": c, "tick_volume": vol, "spread": 1}


def test_resample_h1_aggregates_5min_bars_into_hourly_buckets():
    candles = [
        _candle("2026-01-05 10:00:00", 100, 101, 99, 100.5),
        _candle("2026-01-05 10:05:00", 100.5, 102, 100, 101.5),
        _candle("2026-01-05 10:55:00", 101.5, 103, 101, 102.0),
        _candle("2026-01-05 11:00:00", 102.0, 102.5, 101.8, 102.2),
    ]
    h1 = ichimoku_reversion.resample_h1(candles)
    assert len(h1) == 2
    assert h1[0]["time"] == "2026-01-05T10:00:00"
    assert h1[0]["open"] == 100
    assert h1[0]["high"] == 103
    assert h1[0]["low"] == 99
    assert h1[0]["close"] == 102.0
    assert h1[1]["time"] == "2026-01-05T11:00:00"


def test_compute_atr_h1_is_none_before_warmup_then_positive():
    candles_h1 = [_candle(f"2026-01-{d:02d} 00:00:00", 100 + d, 101 + d, 99 + d, 100.5 + d) for d in range(1, 20)]
    atr = ichimoku_reversion.compute_atr_h1(candles_h1, period=14)
    assert atr[12] is None
    assert atr[13] is not None
    assert atr[13] > 0


def test_compute_ichimoku_tenkan_kijun_are_midpoints_of_rolling_high_low():
    candles_h1 = [_candle(f"2026-01-01 {h:02d}:00:00", 100, 100 + h, 100 - h, 100) for h in range(10)]
    ind = ichimoku_reversion.compute_ichimoku(candles_h1, tenkan_period=3, kijun_period=5, senkou_b_period=5, shift=2)
    # a l'indice 9 (10e bougie), plus haut/plus bas sur les 3 dernieres bougies (indices 7,8,9) = high max 109 / low min 91
    assert ind["tenkan"][9] == (109 + 91) / 2.0
    # senkou_a[9] est decale : il reprend la valeur brute calculee a l'indice 9-2=7
    assert ind["senkou_a"][9] == ind["tenkan"][7] and ind["senkou_a"][9] is not None


def test_compute_ichimoku_senkou_is_none_before_shift():
    candles_h1 = [_candle(f"2026-01-01 {h:02d}:00:00", 100, 101, 99, 100) for h in range(5)]
    ind = ichimoku_reversion.compute_ichimoku(candles_h1, tenkan_period=3, kijun_period=3, senkou_b_period=3, shift=10)
    assert all(v is None for v in ind["senkou_a"])
    assert all(v is None for v in ind["senkou_b"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gold_bot/test_ichimoku_reversion.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gold_bot.ichimoku_reversion'`

- [ ] **Step 3: Implement `resample_h1`, `compute_atr_h1`, `compute_ichimoku`**

```python
# gold_bot/ichimoku_reversion.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gold_bot/test_ichimoku_reversion.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add gold_bot/ichimoku_reversion.py tests/gold_bot/test_ichimoku_reversion.py
git commit -m "feat(gold_bot): ajoute les indicateurs Ichimoku (resample H1, ATR, tenkan/kijun/nuage)"
```

- [ ] **Step 6: Write the failing tests (latest_state, signal, trailing stop)**

```python
# a ajouter a tests/gold_bot/test_ichimoku_reversion.py

def _build_bullish_cross_history():
    """78 bougies H1 (MIN_H1_CANDLES) avec un prix stable puis un
    croisement Tenkan/Kijun haussier confirme par un prix au-dessus du
    nuage sur la derniere bougie."""
    from datetime import timedelta
    candles = []
    base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    price = 100.0
    for h in range(80):
        ts = (base + timedelta(hours=h)).strftime("%Y-%m-%d %H:00:00")
        if h < 70:
            o, hh, l, c = price, price + 0.5, price - 0.5, price
        else:
            # pousse le prix a la hausse sur les 10 dernieres bougies pour provoquer un croisement
            price += 2.0
            o, hh, l, c = price - 2.0, price + 0.5, price - 2.5, price
        candles.append(_candle(ts, o, hh, l, c))
    return candles


def _h1_to_5min(candles_h1):
    """Convertit une liste de bougies H1 en bougies 5min equivalentes
    (une seule bougie 5min par heure, au prorata de l'OHLC) -- suffisant
    pour que resample_h1() reconstruise exactement les memes buckets."""
    out = []
    for c in candles_h1:
        t = ichimoku_reversion._parse(c["time"])
        out.append(_candle(t.strftime("%Y-%m-%d %H:00:00"), c["open"], c["high"], c["low"], c["close"]))
    return out


def test_latest_state_returns_none_when_history_too_short():
    candles_5min = _h1_to_5min(_build_bullish_cross_history())[:50]  # < MIN_H1_CANDLES
    assert ichimoku_reversion.latest_state(candles_5min) is None


def test_latest_state_returns_full_state_with_enough_history():
    candles_5min = _h1_to_5min(_build_bullish_cross_history())
    state = ichimoku_reversion.latest_state(candles_5min)
    assert state is not None
    for key in ("close", "high", "low", "atr", "tenkan", "tenkan_prev", "kijun", "kijun_prev", "senkou_a", "senkou_b", "hour_utc"):
        assert key in state
    assert state["atr"] > 0


def test_signal_returns_achat_on_bullish_tk_cross_above_cloud():
    state = {
        "tenkan_prev": 99.0, "kijun_prev": 100.0, "tenkan": 101.0, "kijun": 100.0,
        "close": 105.0, "senkou_a": 102.0, "senkou_b": 101.0,
    }
    assert ichimoku_reversion.signal(state) == "achat"


def test_signal_returns_none_when_cross_not_confirmed_by_cloud():
    state = {
        "tenkan_prev": 99.0, "kijun_prev": 100.0, "tenkan": 101.0, "kijun": 100.0,
        "close": 95.0, "senkou_a": 102.0, "senkou_b": 101.0,  # close sous le nuage malgre le croisement haussier
    }
    assert ichimoku_reversion.signal(state) is None


def test_compute_trailing_stop_returns_none_before_1r_excursion():
    candles_5min = _h1_to_5min(_build_bullish_cross_history())
    entry_time_iso = candles_5min[-1]["time"]
    result = ichimoku_reversion.compute_trailing_stop(
        candles_5min, "achat", entry_time_iso, entry_price=100.0, current_stop_loss=95.0)
    assert result is None
```

- [ ] **Step 7: Run tests to verify they fail**

Run: `pytest tests/gold_bot/test_ichimoku_reversion.py -v`
Expected: FAIL with `AttributeError: module 'gold_bot.ichimoku_reversion' has no attribute 'latest_state'`

- [ ] **Step 8: Implement `latest_state`, `signal`, `compute_trailing_stop`**

```python
# a ajouter a la fin de gold_bot/ichimoku_reversion.py

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
    """Stop suiveur ancre sur le kijun courant +/- TRAIL_BUFFER_ATR_MULT*atr,
    actif seulement une fois l'excursion favorable >= 1R (meme convention
    que le moteur de recherche, docs/superpowers/research/
    gold_bot_metaux_silver_engines/two_leg_execution_xag.py::trail_fn),
    ne se resserre jamais que dans le sens favorable. None si aucun
    changement ou si l'historique est insuffisant."""
    candles_h1 = resample_h1(candles_5min)
    if len(candles_h1) < MIN_H1_CANDLES:
        return None
    atr = compute_atr_h1(candles_h1)
    ind = compute_ichimoku(candles_h1)
    i = len(candles_h1) - 1
    kijun_i = ind["kijun"][i]
    atr_i = atr[i]
    if kijun_i is None or atr_i is None:
        return None

    entry_time = _parse(entry_time_iso) if isinstance(entry_time_iso, str) else entry_time_iso
    since_entry = [c for c in candles_5min if _parse(c["time"]) >= entry_time]
    if not since_entry:
        return None
    r_distance = abs(entry_price - current_stop_loss)
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
        candidate = max(entry_price, kijun_i - TRAIL_BUFFER_ATR_MULT * atr_i)
        new_stop = max(current_stop_loss, candidate)
    else:
        candidate = min(entry_price, kijun_i + TRAIL_BUFFER_ATR_MULT * atr_i)
        new_stop = min(current_stop_loss, candidate)
    return None if new_stop == current_stop_loss else new_stop
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `pytest tests/gold_bot/test_ichimoku_reversion.py -v`
Expected: PASS (9 tests)

- [ ] **Step 10: Commit**

```bash
git add gold_bot/ichimoku_reversion.py tests/gold_bot/test_ichimoku_reversion.py
git commit -m "feat(gold_bot): ajoute latest_state/signal/compute_trailing_stop au moteur Ichimoku"
```

---

### Task 2: `decide_and_act_ichimoku` dans `gold_bot/bot.py`

**Files:**
- Modify: `gold_bot/bot.py` (ajouter en fin de fichier, après `decide_and_act_fibonacci`)
- Test: `tests/gold_bot/test_bot.py` (ajouter en fin de fichier)

**Interfaces:**
- Consumes: `gold_bot.ichimoku_reversion` (Task 1) — `latest_state`, `signal`, `compute_trailing_stop`, `SESSION_END_HOUR_UTC`, `STOP_BUFFER_ATR_MULT`, `MIN_DISTANCE_PCT`, `TARGET_R`, `PARTIAL_TP_PCT`. `gold_bot.risk` (déjà importé dans `bot.py`) — `compute_position_size`, `round_to_volume_step`. Pour cette tâche uniquement, PAS d'appel à `ai_shadow` (ajouté en Task 3).
- Produces: `decide_and_act_ichimoku(candles, *, contract_size, balance, equity, volume_step, min_volume, max_volume, open_positions, circuit_breaker, symbol="XAGUSD", risk_pct=0.02) -> dict`, même format de retour que `decide_and_act_fibonacci` (`{"action": "aucune", "reason": str}` ou `{"action": "simulation", "steps": [...]}`).

- [ ] **Step 1: Write the failing tests**

```python
# a ajouter a tests/gold_bot/test_bot.py

import gold_bot.ichimoku_reversion as ichimoku_reversion


def _ichimoku_candles_with_signal():
    """Reutilise l'historique de test de test_ichimoku_reversion.py --
    78 bougies H1 construisant un croisement haussier confirme par le
    nuage sur la derniere bougie, converties en bougies 5min."""
    from tests.gold_bot.test_ichimoku_reversion import _build_bullish_cross_history, _h1_to_5min
    return _h1_to_5min(_build_bullish_cross_history())


def _default_circuit_breaker():
    import shared_risk
    return shared_risk.CircuitBreaker(threshold_pct=0.10)


def test_decide_and_act_ichimoku_opens_two_legs_on_entry_signal():
    candles = _ichimoku_candles_with_signal()
    result = bot.decide_and_act_ichimoku(
        candles, contract_size=5000.0, balance=10000.0, equity=10000.0,
        volume_step=0.01, min_volume=0.01, max_volume=20.0,
        open_positions=[], circuit_breaker=_default_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    directions = {s["direction"] for s in result["steps"]}
    assert directions == {"achat"}
    types = [s["type"] for s in result["steps"]]
    assert types == ["ouverture_simulee", "ouverture_simulee"]
    take_profits = [s["take_profit"] for s in result["steps"]]
    assert take_profits.count(None) == 1  # une jambe runner (pas de TP), une jambe partielle (TP defini)


def test_decide_and_act_ichimoku_returns_aucune_when_no_signal():
    candles = _ichimoku_candles_with_signal()
    flat = dict(candles[-1])
    flat["high"] = flat["close"] + 0.1
    flat["low"] = flat["close"] - 0.1
    candles = candles[:-1] + [flat]
    result = bot.decide_and_act_ichimoku(
        candles, contract_size=5000.0, balance=10000.0, equity=10000.0,
        volume_step=0.01, min_volume=0.01, max_volume=20.0,
        open_positions=[], circuit_breaker=_default_circuit_breaker(),
    )
    assert result["action"] == "aucune"


def test_decide_and_act_ichimoku_returns_aucune_when_history_insufficient():
    candles = _ichimoku_candles_with_signal()[:50]
    result = bot.decide_and_act_ichimoku(
        candles, contract_size=5000.0, balance=10000.0, equity=10000.0,
        volume_step=0.01, min_volume=0.01, max_volume=20.0,
        open_positions=[], circuit_breaker=_default_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "historique insuffisant pour l'ichimoku"}


def test_decide_and_act_ichimoku_rejects_when_circuit_breaker_tripped():
    candles = _ichimoku_candles_with_signal()
    cb = _default_circuit_breaker()
    cb.check(10000.0)
    cb.check(8900.0)  # perte > 10% du solde de depart -> coupe-circuit declenche
    result = bot.decide_and_act_ichimoku(
        candles, contract_size=5000.0, balance=8900.0, equity=8900.0,
        volume_step=0.01, min_volume=0.01, max_volume=20.0,
        open_positions=[], circuit_breaker=cb,
    )
    assert result == {"action": "aucune", "reason": "coupe-circuit journalier déclenché"}


def test_decide_and_act_ichimoku_closes_runner_leg_at_session_end():
    candles = _ichimoku_candles_with_signal()
    last = dict(candles[-1])
    from datetime import timedelta
    t = ichimoku_reversion._parse(last["time"])
    hour = t.hour
    delta_hours = (ichimoku_reversion.SESSION_END_HOUR_UTC - hour) % 24
    last["time"] = (t + timedelta(hours=delta_hours)).strftime("%Y-%m-%d %H:00:00")
    candles = candles[:-1] + [last]
    position = {"id": "1", "symbol": "XAGUSD", "type": "POSITION_TYPE_BUY", "openPrice": 100.0, "stopLoss": 95.0, "time": candles[0]["time"]}
    result = bot.decide_and_act_ichimoku(
        candles, contract_size=5000.0, balance=10000.0, equity=10000.0,
        volume_step=0.01, min_volume=0.01, max_volume=20.0,
        open_positions=[position], circuit_breaker=_default_circuit_breaker(),
    )
    assert result["action"] == "simulation"
    assert result["steps"][0]["type"] == "clôture_simulee"


def test_decide_and_act_ichimoku_does_nothing_when_only_partial_leg_open():
    candles = _ichimoku_candles_with_signal()
    position = {"id": "1", "symbol": "XAGUSD", "type": "POSITION_TYPE_BUY", "openPrice": 100.0, "stopLoss": 95.0, "takeProfit": 110.0, "time": candles[0]["time"]}
    result = bot.decide_and_act_ichimoku(
        candles, contract_size=5000.0, balance=10000.0, equity=10000.0,
        volume_step=0.01, min_volume=0.01, max_volume=20.0,
        open_positions=[position], circuit_breaker=_default_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "jambe partielle seule ouverte, rien a gerer"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gold_bot/test_bot.py -k ichimoku -v`
Expected: FAIL with `AttributeError: module 'gold_bot.bot' has no attribute 'decide_and_act_ichimoku'`

- [ ] **Step 3: Implement `decide_and_act_ichimoku`**

Vérifier d'abord que `gold_bot/bot.py` importe déjà `gold_bot.ichimoku_reversion` en haut du fichier (ajouter `import gold_bot.ichimoku_reversion as ichimoku_reversion` à côté des imports existants `import gold_bot.fibonacci_reversion as fibonacci_reversion` / `import gold_bot.vwap_reversion as vwap_reversion` si absent), puis ajouter en fin de fichier :

```python
def decide_and_act_ichimoku(candles: list[dict], *, contract_size: float, balance: float, equity: float,
                             volume_step: float, min_volume: float, max_volume: float,
                             open_positions: list[dict], circuit_breaker: "risk.CircuitBreaker",
                             symbol: str = "XAGUSD", risk_pct: float = 0.02) -> dict:
    """Moteur Ichimoku deux-jambes (croisement Tenkan/Kijun confirme par
    le nuage) -- voir docs/superpowers/specs/
    2026-11-05-gold-bot-ichimoku-xagusd-ai-veto-design.md. Meme contrat
    d'entree/sortie a deux jambes (prise partielle + runner) que
    decide_and_act_fibonacci/decide_and_act_vwap. `symbol` par defaut
    XAGUSD -- seul symbole calibre pour ce moteur. Contrairement a
    decide_and_act_fibonacci, pas de blackout macro/choc de volatilite
    (non valide dans la recherche pour ce moteur)."""
    circuit_breaker.check(equity)

    state = ichimoku_reversion.latest_state(candles)
    if state is None:
        return {"action": "aucune", "reason": "historique insuffisant pour l'ichimoku"}

    matching = [p for p in open_positions if p.get("symbol") == symbol]
    runner_matching = [p for p in matching if not p.get("takeProfit")]
    partial_matching = [p for p in matching if p.get("takeProfit")]

    if len(runner_matching) > 1 or len(partial_matching) > 1:
        return {"action": "aucune", "reason": "plusieurs positions ouvertes sur ce symbole, aucune action par prudence"}

    if matching:
        if state["hour_utc"] >= ichimoku_reversion.SESSION_END_HOUR_UTC:
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

        new_stop = ichimoku_reversion.compute_trailing_stop(
            candles, direction, entry_time_iso, entry_price, current_stop_loss)
        if new_stop is None:
            return {"action": "aucune", "reason": "position ouverte, pas de mise a jour du stop suiveur"}

        return {"action": "simulation", "steps": [{
            "type": "modification_simulee", "position_id": position.get("id"), "symbol": symbol,
            "new_stop_loss": new_stop,
        }]}

    if state["hour_utc"] >= ichimoku_reversion.SESSION_END_HOUR_UTC:
        return {"action": "aucune", "reason": "trop tard dans la session pour ouvrir"}

    direction = ichimoku_reversion.signal(state)
    if direction is None:
        return {"action": "aucune", "reason": "pas de signal d'entree"}

    entry_price = state["close"]
    if direction == "achat":
        stop_loss = state["kijun"] - ichimoku_reversion.STOP_BUFFER_ATR_MULT * state["atr"]
    else:
        stop_loss = state["kijun"] + ichimoku_reversion.STOP_BUFFER_ATR_MULT * state["atr"]

    distance = abs(entry_price - stop_loss)
    if distance < entry_price * ichimoku_reversion.MIN_DISTANCE_PCT:
        return {"action": "aucune", "reason": "distance entree-stop trop faible"}

    if not circuit_breaker.can_open_position(equity):
        return {"action": "aucune", "reason": "coupe-circuit journalier déclenché"}

    raw_size = risk.compute_position_size(balance, entry_price, stop_loss, contract_size, risk_pct=risk_pct)
    total_size = risk.round_to_volume_step(raw_size, volume_step, min_volume, max_volume)
    if total_size is None:
        return {"action": "aucune", "reason": "compte trop petit pour ce stop (volume sous le minimum du broker)"}

    target_r = ichimoku_reversion.TARGET_R
    target = entry_price + target_r * distance if direction == "achat" else entry_price - target_r * distance

    partial_raw = ichimoku_reversion.PARTIAL_TP_PCT * total_size
    runner_raw = (1.0 - ichimoku_reversion.PARTIAL_TP_PCT) * total_size
    partial_volume = risk.round_to_volume_step(partial_raw, volume_step, min_volume, max_volume)
    runner_volume = risk.round_to_volume_step(runner_raw, volume_step, min_volume, max_volume)

    if partial_volume is None and runner_volume is None:
        return {"action": "aucune", "reason": "compte trop petit pour scinder en deux jambes"}

    steps = []
    if partial_volume is not None:
        steps.append({
            "type": "ouverture_simulee", "symbol": symbol, "direction": direction,
            "entry": entry_price, "stop_loss": stop_loss, "take_profit": target, "volume": partial_volume,
        })
    if runner_volume is not None:
        steps.append({
            "type": "ouverture_simulee", "symbol": symbol, "direction": direction,
            "entry": entry_price, "stop_loss": stop_loss, "take_profit": None, "volume": runner_volume,
        })
    return {"action": "simulation", "steps": steps}
```

**Note importante pour l'implémenteur** : cette version N'APPELLE PAS encore `ai_shadow` — c'est volontaire, ajouté en Task 3 après généralisation du module. Ne pas anticiper cet appel ici.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gold_bot/test_bot.py -k ichimoku -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run the full existing test suite to confirm no regression**

Run: `pytest tests/gold_bot/test_bot.py -v`
Expected: PASS (tous les tests existants + les 6 nouveaux, aucune régression sur `decide_and_act_fibonacci`/`decide_and_act_vwap`)

- [ ] **Step 6: Commit**

```bash
git add gold_bot/bot.py tests/gold_bot/test_bot.py
git commit -m "feat(gold_bot): ajoute decide_and_act_ichimoku (sans veto IA pour l'instant)"
```

---

### Task 3: Généralisation du veto IA (`gold_bot/ai_shadow.py`) + branchement fail-open dans `decide_and_act_ichimoku`

**Files:**
- Modify: `gold_bot/ai_shadow.py`
- Modify: `gold_bot/bot.py` (fonction `decide_and_act_fibonacci` ET `decide_and_act_ichimoku`)
- Test: `tests/gold_bot/test_ai_shadow.py`
- Test: `tests/gold_bot/test_bot.py`

**Interfaces:**
- Consumes: `gold_bot.ai_shadow.shadow_judge_entry` (signature étendue, voir ci-dessous).
- Produces: `shadow_judge_entry(symbol, direction, state, candles, *, engine_label: str, real_decision: bool = False, log_path=SHADOW_LOG_PATH, now=None) -> dict | None` — `real_decision` distingue le texte du prompt (mode fantôme vs décision réelle), n'affecte jamais le comportement de retour (toujours soit un dict `{"verdict", "confiance", "raison"}` soit `None`).

- [ ] **Step 1: Write the failing tests**

```python
# a ajouter a tests/gold_bot/test_ai_shadow.py
# (reutiliser les helpers _state()/_candles() deja presents en haut de ce fichier)

def test_build_prompt_uses_engine_label_instead_of_hardcoded_fibonacci():
    prompt = ai_shadow._build_prompt("XAGUSD", "achat", _state(), _candles(), engine_label="test-engine-label", real_decision=False)
    assert "test-engine-label" in prompt
    assert "retracement de Fibonacci" not in prompt


def test_build_prompt_shadow_mode_says_no_real_trade_affected():
    prompt = ai_shadow._build_prompt("XAGUSD", "achat", _state(), _candles(), engine_label="x", real_decision=False)
    assert "n'affecte AUCUN trade reel" in prompt


def test_build_prompt_real_decision_mode_says_trade_will_be_blocked():
    prompt = ai_shadow._build_prompt("XAGUSD", "achat", _state(), _candles(), engine_label="x", real_decision=True)
    assert "n'affecte AUCUN trade reel" not in prompt
    assert "ne sera PAS execute" in prompt


def test_build_prompt_omits_trend_strength_line_when_none():
    state = dict(_state())
    state["trend_strength"] = None
    prompt = ai_shadow._build_prompt("XAGUSD", "achat", state, _candles(), engine_label="x", real_decision=False)
    assert "force de tendance" not in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gold_bot/test_ai_shadow.py -k "engine_label or shadow_mode or real_decision or trend_strength" -v`
Expected: FAIL (`TypeError: _build_prompt() missing 1 required keyword-only argument: 'engine_label'` ou équivalent)

- [ ] **Step 3: Généraliser `_build_prompt` et `shadow_judge_entry`**

Remplacer entièrement `_build_prompt` dans `gold_bot/ai_shadow.py` :

```python
def _build_prompt(symbol: str, direction: str, state: dict, candles: list[dict], *,
                   engine_label: str, real_decision: bool) -> str:
    trend_line = (
        f"force de tendance glissante 20 jours={state.get('trend_strength')}, "
        if state.get("trend_strength") is not None else ""
    )
    consequence_line = (
        "Ceci est une VRAIE decision : si tu juges qu'il faut t'opposer (verdict "
        '"opposerait"), ce trade ne sera PAS execute. Sois honnete et direct, dis '
        "clairement si tu n'as rien trouve de pertinent -- ne t'oppose pas par excès "
        "de prudence sans raison concrete."
        if real_decision else
        "Ceci est un TEST EN MODE FANTOME : ton avis n'affecte AUCUN trade reel, "
        "sois honnete et direct, dis clairement si tu n'as rien trouve de pertinent."
    )
    return (
        f"Un moteur de trading mecanique sur l'or/l'argent ({symbol}) vient de detecter "
        f"un signal d'entree reel ({direction}) sur {engine_label}.\n\n"
        f"Contexte chiffre : niveau={state.get('level')}, cloture={state.get('close')}, "
        f"plus haut={state.get('high')}, plus bas={state.get('low')}, ATR={state.get('atr')}, "
        f"{trend_line}"
        f"heure UTC={state.get('hour_utc')}.\n\n"
        f"Dernieres bougies 5min (plus recente en dernier) :\n{_describe_candles(candles)}\n\n"
        f"Cherche s'il y a une actualite majeure en cours ou tres recente qui pourrait "
        f"expliquer ce mouvement de prix (decision de banque centrale, donnee macro, "
        f"evenement geopolitique). Juge si ce signal ressemble a un vrai rebond technique "
        f"ou a du bruit cause par une actualite -- meme principe que l'incident reel du "
        f"2026-10-28 (un signal technique authentique declenche par une conference de "
        f"presse de la Fed, pas un vrai retournement).\n\n"
        f"{consequence_line}\n\n"
        f"Termine ta reponse par un bloc JSON seul sur sa derniere ligne, exactement sous "
        f'cette forme : {{"verdict": "confirme ou opposerait", "confiance": "faible ou '
        f'moyenne ou haute", "raison": "une phrase"}}'
    )
```

Dans `shadow_judge_entry`, ajouter les paramètres `engine_label: str` et `real_decision: bool = False` à la signature (après `candles`, avant `log_path`), et transmettre ces deux valeurs à l'appel de `_build_prompt` (remplacer `_build_prompt(symbol, direction, state, candles)` par `_build_prompt(symbol, direction, state, candles, engine_label=engine_label, real_decision=real_decision)`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gold_bot/test_ai_shadow.py -v`
Expected: PASS (tous les tests existants + les 4 nouveaux — si un test existant appelait `_build_prompt`/`shadow_judge_entry` sans `engine_label`, le corriger pour passer `engine_label="rebond de retracement de Fibonacci"` explicitement, cohérent avec le point suivant)

- [ ] **Step 5: Mettre à jour l'appel existant dans `decide_and_act_fibonacci` (comportement inchangé, juste explicite)**

Dans `gold_bot/bot.py`, remplacer l'appel existant :
```python
    ai_shadow.shadow_judge_entry(symbol, direction, state, candles)
```
par :
```python
    ai_shadow.shadow_judge_entry(
        symbol, direction, state, candles,
        engine_label="rebond de retracement de Fibonacci", real_decision=False,
    )
```

- [ ] **Step 6: Run the Fibonacci test suite to confirm strictement aucune régression**

Run: `pytest tests/gold_bot/test_bot.py -k fibonacci -v`
Expected: PASS, comportement de `decide_and_act_fibonacci` inchangé (le veto reste en mode fantôme pour ce moteur)

- [ ] **Step 7: Write the failing tests for le branchement fail-open dans `decide_and_act_ichimoku`**

```python
# a ajouter a tests/gold_bot/test_bot.py

def test_decide_and_act_ichimoku_blocks_trade_when_ai_opposes(monkeypatch):
    monkeypatch.setattr(bot.ai_shadow, "shadow_judge_entry", lambda *a, **k: {"verdict": "opposerait", "confiance": "haute", "raison": "test"})
    candles = _ichimoku_candles_with_signal()
    result = bot.decide_and_act_ichimoku(
        candles, contract_size=5000.0, balance=10000.0, equity=10000.0,
        volume_step=0.01, min_volume=0.01, max_volume=20.0,
        open_positions=[], circuit_breaker=_default_circuit_breaker(),
    )
    assert result == {"action": "aucune", "reason": "veto IA (opposition)"}


def test_decide_and_act_ichimoku_proceeds_when_ai_confirms(monkeypatch):
    monkeypatch.setattr(bot.ai_shadow, "shadow_judge_entry", lambda *a, **k: {"verdict": "confirme", "confiance": "haute", "raison": "test"})
    candles = _ichimoku_candles_with_signal()
    result = bot.decide_and_act_ichimoku(
        candles, contract_size=5000.0, balance=10000.0, equity=10000.0,
        volume_step=0.01, min_volume=0.01, max_volume=20.0,
        open_positions=[], circuit_breaker=_default_circuit_breaker(),
    )
    assert result["action"] == "simulation"


def test_decide_and_act_ichimoku_proceeds_when_ai_returns_none_fail_open(monkeypatch):
    monkeypatch.setattr(bot.ai_shadow, "shadow_judge_entry", lambda *a, **k: None)
    candles = _ichimoku_candles_with_signal()
    result = bot.decide_and_act_ichimoku(
        candles, contract_size=5000.0, balance=10000.0, equity=10000.0,
        volume_step=0.01, min_volume=0.01, max_volume=20.0,
        open_positions=[], circuit_breaker=_default_circuit_breaker(),
    )
    assert result["action"] == "simulation"


def test_decide_and_act_ichimoku_calls_ai_shadow_with_real_decision_true(monkeypatch):
    calls = []
    monkeypatch.setattr(bot.ai_shadow, "shadow_judge_entry", lambda *a, **k: calls.append(k) or {"verdict": "confirme", "confiance": "haute", "raison": "test"})
    candles = _ichimoku_candles_with_signal()
    bot.decide_and_act_ichimoku(
        candles, contract_size=5000.0, balance=10000.0, equity=10000.0,
        volume_step=0.01, min_volume=0.01, max_volume=20.0,
        open_positions=[], circuit_breaker=_default_circuit_breaker(),
    )
    assert calls[0]["real_decision"] is True
    assert calls[0]["engine_label"] == "croisement Tenkan/Kijun confirme par le nuage (Ichimoku)"
```

- [ ] **Step 8: Run tests to verify they fail**

Run: `pytest tests/gold_bot/test_bot.py -k "blocks_trade_when_ai_opposes or proceeds_when_ai" -v`
Expected: FAIL (le veto n'est pas encore appelé, les 2 premiers tests échouent car `result["action"]` vaut toujours `"simulation"` quel que soit le verdict mocké, et le 4e échoue car `shadow_judge_entry` n'est jamais appelée)

- [ ] **Step 9: Brancher le veto IA dans `decide_and_act_ichimoku`**

Dans `gold_bot/bot.py`, fonction `decide_and_act_ichimoku`, insérer juste après le bloc `if total_size is None: ...` et avant le calcul de `target_r`/`target` :

```python
    # Veto IA branche pour de vrai (pas fantome, contrairement a
    # decide_and_act_fibonacci) -- voir le spec pour la justification du
    # choix fail-open : a la date de ce chantier, le veto n'a jamais
    # produit un seul verdict exploitable en conditions reelles (0/36
    # tentatives avant le fix du 2026-11-05 -- 28 timeouts, 7 echecs de
    # credits API, 1 reponse mal formee). Bloquer par defaut en cas
    # d'echec reviendrait quasiment a arreter le moteur -- le signal
    # technique valide par la recherche prime donc en cas de doute sur
    # la disponibilite/fiabilite de l'IA. L'IA ne peut qu'AJOUTER une
    # restriction (veto explicite "opposerait"), jamais remplacer le
    # signal en cas d'absence de reponse (verdict None).
    state_for_ai = {
        "level": state["kijun"], "close": state["close"], "high": state["high"],
        "low": state["low"], "atr": state["atr"], "trend_strength": None,
        "hour_utc": state["hour_utc"],
    }
    verdict = ai_shadow.shadow_judge_entry(
        symbol, direction, state_for_ai, candles,
        engine_label="croisement Tenkan/Kijun confirme par le nuage (Ichimoku)",
        real_decision=True,
    )
    if verdict is not None and verdict.get("verdict") == "opposerait":
        return {"action": "aucune", "reason": "veto IA (opposition)"}

```

- [ ] **Step 10: Run tests to verify they pass**

Run: `pytest tests/gold_bot/test_bot.py -k ichimoku -v`
Expected: PASS (tous les tests Ichimoku, y compris les 4 nouveaux du veto)

- [ ] **Step 11: Run the full test suite**

Run: `pytest tests/gold_bot/ -v`
Expected: PASS intégralement, aucune régression sur Fibonacci/VWAP

- [ ] **Step 12: Commit**

```bash
git add gold_bot/ai_shadow.py gold_bot/bot.py tests/gold_bot/test_ai_shadow.py tests/gold_bot/test_bot.py
git commit -m "feat(gold_bot): généralise ai_shadow (engine_label/real_decision) et branche le veto IA en fail-open sur decide_and_act_ichimoku"
```

---

### Task 4: Sélection du moteur via `GOLD_BOT_ENGINE` dans `gold_bot/loop.py`

**Files:**
- Modify: `gold_bot/loop.py`
- Test: `tests/gold_bot/test_loop.py`

**Interfaces:**
- Consumes: `gold_bot.bot.decide_and_act_ichimoku` (Task 2+3), `gold_bot.ichimoku_reversion.SYMBOL`/`CONTRACT_SIZE`/`MAX_VOLUME`/`VOLUME_STEP`/`MIN_VOLUME` (Task 1).
- Produces: comportement de sélection de moteur piloté par `os.environ.get("GOLD_BOT_ENGINE")`, lu une seule fois au démarrage du cycle (même endroit que la lecture actuelle de `FTMO_ISC`/`ftmo_guard_instance`).

- [ ] **Step 1: Lire le code existant de sélection de moteur dans `loop.py`**

Avant d'écrire le test, lire `gold_bot/loop.py` autour de l'endroit où `decide_and_act_fibonacci` vs `decide_and_act_vwap` est choisi (chercher `ftmo_guard_instance is not None` dans le fichier) pour identifier la fonction exacte à étendre — ne pas deviner son nom/sa signature, les lire directement.

- [ ] **Step 2: Write the failing test (non-régression + nouveau cas)**

```python
# a ajouter a tests/gold_bot/test_loop.py

def test_engine_selection_unchanged_when_gold_bot_engine_env_absent(monkeypatch):
    monkeypatch.delenv("GOLD_BOT_ENGINE", raising=False)
    # Reprendre ici le test de non-regression exact pour le bot personnel
    # (ftmo_guard_instance=None -> decide_and_act_vwap) et pour le compte
    # FTMO (ftmo_guard_instance non-None -> decide_and_act_fibonacci), en
    # s'appuyant sur les helpers/fixtures de test deja presents dans ce
    # fichier pour construire ftmo_guard_instance et simuler un cycle --
    # NE PAS reinventer ces helpers, les reutiliser tels quels (lire le
    # fichier avant d'ecrire ce test, ne pas deviner leur nom/forme).


def test_engine_selection_uses_ichimoku_xag_when_env_set(monkeypatch):
    monkeypatch.setenv("GOLD_BOT_ENGINE", "ichimoku_xag")
    # Verifier que le symbole resolu est ["XAGUSD"] et que la fonction de
    # decision appelee pour ce symbole est bot.decide_and_act_ichimoku --
    # s'appuyer sur le meme pattern de test que celui deja utilise pour
    # verifier le choix fibonacci/vwap existant dans ce fichier (meme
    # fixtures, meme structure d'assertion).
```

**Note pour l'implémenteur** : les deux corps de test ci-dessus sont volontairement incomplets — c'est la seule exception à "pas de placeholder" de tout ce plan, parce que leur code exact dépend de fixtures/mocks déjà présents dans `tests/gold_bot/test_loop.py` qui ne peuvent être connus sans lire ce fichier d'abord (ne pas les deviner). Avant d'écrire Step 2 pour de vrai : lire les tests existants de ce fichier qui vérifient déjà la sélection `fibonacci`/`vwap`, puis écrire ces deux nouveaux tests en réutilisant EXACTEMENT les mêmes fixtures/mocks/pattern d'assertion.

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/gold_bot/test_loop.py -k "engine_selection" -v`
Expected: FAIL (le deuxième test échoue, `GOLD_BOT_ENGINE` n'est pas encore lu ; le premier peut déjà passer puisqu'il vérifie un comportement existant — s'il échoue aussi, c'est un signal que le test a été mal écrit, pas que le code doit changer)

- [ ] **Step 4: Étendre la sélection de moteur dans `loop.py`**

À l'endroit identifié en Step 1, ajouter la lecture de `GOLD_BOT_ENGINE` :

```python
GOLD_BOT_ENGINE = os.environ.get("GOLD_BOT_ENGINE")
```

Et dans la fonction de sélection de moteur/symboles (nom exact trouvé en Step 1), ajouter un cas explicite avant la logique existante :

```python
if GOLD_BOT_ENGINE == "ichimoku_xag":
    symbols = [ichimoku_reversion.SYMBOL]
    # cablage de decide_and_act_ichimoku pour ce symbole, en suivant
    # exactement le pattern deja utilise pour cabler decide_and_act_fibonacci
    # ou decide_and_act_vwap a un symbole dans ce meme fichier. contract_size/
    # volume_step/min_volume/max_volume proviennent de gold_bot.ichimoku_reversion
    # (CONTRACT_SIZE/VOLUME_STEP/MIN_VOLUME/MAX_VOLUME) plutot que de
    # broker.get_symbol_specification, puisque ces valeurs sont deja
    # verifiees empiriquement et figees dans le moteur (voir Task 1).
```

(L'implémenteur doit adapter cette ébauche à la structure réelle trouvée en Step 1 — la structure exacte de `loop.py` à cet endroit n'est pas garantie par ce plan et doit être lue avant modification, ne pas copier-coller aveuglément.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/gold_bot/test_loop.py -v`
Expected: PASS intégralement

- [ ] **Step 6: Run the entire project test suite**

Run: `pytest -q`
Expected: PASS intégralement, zéro régression sur tout le projet (pas seulement `gold_bot/`)

- [ ] **Step 7: Commit**

```bash
git add gold_bot/loop.py tests/gold_bot/test_loop.py
git commit -m "feat(gold_bot): ajoute la selection du moteur Ichimoku+XAGUSD via GOLD_BOT_ENGINE, sans regression sur les instances existantes"
```

---

## Hors scope de ce plan (à traiter séparément)

- Déploiement VPS (nouveau checkout, nouvel utilisateur système ou partagé, `.env`, `dry_run=true` initial) — décision opérationnelle, pas une tâche de code.
- Ouverture d'un compte FTMO réel pour cette instance.
- Blackout macro/choc de volatilité pour le moteur Ichimoku (non validé dans la recherche, à envisager seulement après observation réelle en `dry_run`).
