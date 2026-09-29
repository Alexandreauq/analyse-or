# Bot Or — moteur "cassure de session + volume" — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bascule la source de données du bot Or (Twelve Data → API de données de marché MetaApi, avec tick volume et spread réel) et ajoute un nouveau moteur de signal autonome ("cassure de range de session confirmée par le volume"), sans toucher à la logique de décision/risque/exécution existante ni retirer l'ancien moteur à figures chartistes.

**Architecture:** `gold_bot/broker.py` gagne une fonction bas niveau pour l'API de données de marché MetaApi (hôte dédié). `gold_bot/confluence.py::fetch_gold_candles` et `gold_bot/backtest.py::fetch_gold_candles_range` s'appuient dessus pour remplacer Twelve Data, avec les cascades mécaniques de signature que ça implique dans `loop.py`/`backtest.py`. Un nouveau module `gold_bot/session_breakout.py` porte le nouveau moteur de signal — **fonction pure, indépendante, testable seule et via `backtest.simulate_trades(signal_fn=...)`, mais PAS branchée sur `bot.decide_and_act`** : la bascule réelle en production se décide après le plan de validation multi-années décrit dans la spec, pas dans ce plan.

**Tech Stack:** Python 3.14, pytest, requests, aucune nouvelle dépendance.

**Spec:** `docs/superpowers/specs/2026-09-29-gold-bot-session-breakout-volume-design.md`

## Global Constraints

- Aucun changement de LOGIQUE dans `gold_bot/risk.py`, `gold_bot/bot.py`, `gold_bot/api.py`, `gold_bot/state.py`, `gold_bot/notify.py`.
- `gold_bot/chart_patterns.py` et le moteur `confluence.compute_signal` existant (figures chartistes) restent inchangés et dans le dépôt — ce plan ne les touche pas.
- Nouvel hôte MetaApi données de marché : `https://mt-market-data-client-api-v1.{region}.agiliumtrade.ai` (différent de `_base_url` dans `broker.py`, qui reste l'hôte de trading).
- Endpoint : `GET /users/current/accounts/{accountId}/historical-market-data/symbols/{symbol}/timeframes/{timeframe}/candles`, header `auth-token`, query `startTime`/`limit` (max 1000).
- Chaque bougie renvoyée par MetaApi a la forme `{"symbol", "time" (ISO "...Z"), "timeframe", "open", "high", "low", "close", "tickVolume", "spread", "state"}` — `state != "complete"` doit toujours être exclu.
- Toute bougie produite par ce projet garde le format `"time"` existant : chaîne `"YYYY-MM-DD HH:MM:SS"` (pas de suffixe `Z`, pas de fraction de seconde) — tous les points de parsing existants (`datetime.fromisoformat(c["time"].replace(" ", "T"))`) en dépendent, dans ce plan comme dans le reste du code déjà en place.
- Symbole MetaApi : `"XAUUSD"` (pas `"XAU/USD"`, convention Twelve Data). Timeframe MetaApi pour 5 minutes : `"5m"`.
- Stop-loss du nouveau moteur : `range_high - confluence.CHARTPATTERN_STOP_BUFFER` (achat) / `range_low + confluence.CHARTPATTERN_STOP_BUFFER` (vente) — jamais le bord opposé du range (voir correction de la spec du 2026-09-29 : ça rendrait le ratio risque/rendement structurellement < 1).
- `VOLUME_CONFIRMATION_MULTIPLE = 1.5`, `VOLUME_CONFIRMATION_LOOKBACK = 20`, `MIN_ASIAN_SESSION_CANDLES = 48`, `ASIAN_SESSION_START_HOUR = 0`, `ASIAN_SESSION_END_HOUR = 8`, `TRADING_WINDOW_START_HOUR = 8`, `TRADING_WINDOW_END_HOUR = 16` — bornes UTC fixes, aucun ajustement heure d'été.

---

### Task 1: `broker.get_historical_candles` — accès bas niveau à l'API de données de marché MetaApi

**Files:**
- Modify: `gold_bot/broker.py`
- Test: `tests/gold_bot/test_broker.py`

**Interfaces:**
- Produces: `broker.get_historical_candles(token: str, account_id: str, symbol: str, timeframe: str, start_time: str | None = None, limit: int = 1000, region: str = DEFAULT_MT5_REGION) -> list[dict]` — renvoie la liste JSON brute de MetaApi (chaque élément avec `time`/`open`/`high`/`low`/`close`/`tickVolume`/`spread`/`state`), sans transformation. Consommé par les Tasks 2 et 4.

- [ ] **Step 1: Write the failing tests**

```python
def test_get_historical_candles_calls_correct_url_and_headers(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["params"] = params
        captured["timeout"] = timeout
        return _FakeMT5Response([{"time": "2026-09-29T15:50:00.000Z", "state": "complete"}])

    monkeypatch.setattr(broker.requests, "get", fake_get)
    result = broker.get_historical_candles("tok", "acc123", "XAUUSD", "5m", region="london")

    assert captured["url"] == (
        "https://mt-market-data-client-api-v1.london.agiliumtrade.ai"
        "/users/current/accounts/acc123/historical-market-data/symbols/XAUUSD/timeframes/5m/candles"
    )
    assert captured["headers"] == {"auth-token": "tok"}
    assert captured["params"] == {"limit": 1000}
    assert result == [{"time": "2026-09-29T15:50:00.000Z", "state": "complete"}]


def test_get_historical_candles_passes_start_time_when_given(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["params"] = params
        return _FakeMT5Response([])

    monkeypatch.setattr(broker.requests, "get", fake_get)
    broker.get_historical_candles("tok", "acc123", "XAUUSD", "5m", start_time="2021-09-30 00:00:00.000", limit=500)

    assert captured["params"] == {"limit": 500, "startTime": "2021-09-30 00:00:00.000"}


def test_get_historical_candles_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(broker.requests, "get", lambda *a, **k: _FakeMT5Response([], status_code=401))
    with pytest.raises(broker.requests.exceptions.HTTPError):
        broker.get_historical_candles("bad-tok", "acc123", "XAUUSD", "5m")


def test_get_historical_candles_default_region_and_limit(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeMT5Response([])

    monkeypatch.setattr(broker.requests, "get", fake_get)
    broker.get_historical_candles("tok", "acc123", "XAUUSD", "5m")

    assert captured["url"].startswith("https://mt-market-data-client-api-v1.london.agiliumtrade.ai")
    assert captured["params"] == {"limit": 1000}
```

Add these 4 tests to `tests/gold_bot/test_broker.py`, right after the existing `_FakeMT5Response` class definition (before `test_get_account_balance_returns_balance`). `_FakeMT5Response` already exists in this file and supports `raise_for_status()`/`json()` — reuse it as-is, no changes needed to that class.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gold_bot/test_broker.py -k historical_candles -v`
Expected: FAIL with `AttributeError: module 'gold_bot.broker' has no attribute 'get_historical_candles'`

- [ ] **Step 3: Implement**

Add to `gold_bot/broker.py`, after `_base_url`:

```python
def _market_data_base_url(region: str) -> str:
    """Hôte dédié aux données de marché historiques -- différent de
    _base_url (API de trading). Voir docs/superpowers/specs/2026-09-29-
    gold-bot-session-breakout-volume-design.md."""
    return f"https://mt-market-data-client-api-v1.{region}.agiliumtrade.ai"


def get_historical_candles(token: str, account_id: str, symbol: str, timeframe: str,
                            start_time: str | None = None, limit: int = 1000,
                            region: str = DEFAULT_MT5_REGION) -> list[dict]:
    """Bougies historiques brutes (liste JSON telle que renvoyée par
    MetaApi, non transformée) -- inclut tickVolume/spread, absents de
    l'API de trading. `start_time` (optionnel, "YYYY-MM-DD HH:MM:SS.mmm")
    charge en arrière depuis ce point ; `limit` plafonné à 1000 par
    MetaApi. La dernière bougie peut avoir `state == "intermediate"`
    (encore en formation) -- au consommateur de la filtrer."""
    params: dict = {"limit": limit}
    if start_time is not None:
        params["startTime"] = start_time
    resp = requests.get(
        f"{_market_data_base_url(region)}/users/current/accounts/{account_id}"
        f"/historical-market-data/symbols/{symbol}/timeframes/{timeframe}/candles",
        headers={"auth-token": token},
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gold_bot/test_broker.py -v`
Expected: PASS (toutes, y compris les tests déjà existants du fichier)

- [ ] **Step 5: Commit**

```bash
git add gold_bot/broker.py tests/gold_bot/test_broker.py
git commit -m "feat(gold_bot): ajoute broker.get_historical_candles (API données de marché MetaApi)"
```

---

### Task 2: `confluence.fetch_gold_candles` — bascule vers MetaApi

**Files:**
- Modify: `gold_bot/confluence.py`
- Test: `tests/gold_bot/test_confluence.py`

**Interfaces:**
- Consumes: `broker.get_historical_candles(token, account_id, symbol, timeframe, limit=..., region=...)` (Task 1).
- Produces: `fetch_gold_candles(token: str, account_id: str, region: str = broker.DEFAULT_MT5_REGION) -> list[dict]` — remplace l'ancienne signature `fetch_gold_candles(api_key: str)`. Chaque bougie : `{"time": "YYYY-MM-DD HH:MM:SS", "open", "high", "low", "close", "tick_volume", "spread"}`. Consommé par Task 3.

- [ ] **Step 1: Remove the old Twelve-Data-specific tests**

Dans `tests/gold_bot/test_confluence.py`, supprimer entièrement ces 4 tests (obsolètes, spécifiques à Twelve Data) : `test_fetch_gold_candles_parses_and_reverses_to_chronological_order`, `test_fetch_gold_candles_rejects_on_error_status`, `test_fetch_gold_candles_rejects_on_http_error`, `test_fetch_gold_candles_network_error_never_leaks_the_api_key`. Garder `_FakeTDResponse` (classe utilisée ailleurs dans le fichier par d'autres tests non liés à `fetch_gold_candles`) et le reste du fichier intact.

- [ ] **Step 2: Write the new failing tests**

Ajouter à la place des tests supprimés :

```python
def test_fetch_gold_candles_returns_only_complete_candles_in_chronological_order(monkeypatch):
    raw = [
        {"time": "2026-09-29T15:55:00.000Z", "open": 4155.6, "high": 4158.9, "low": 4154.6, "close": 4158.2,
         "tickVolume": 2267, "spread": 21, "state": "complete"},
        {"time": "2026-09-29T15:50:00.000Z", "open": 4159.2, "high": 4159.7, "low": 4153.7, "close": 4155.6,
         "tickVolume": 2534, "spread": 21, "state": "complete"},
        {"time": "2026-09-29T16:00:00.000Z", "open": 4158.2, "high": 4158.9, "low": 4152.9, "close": 4153.2,
         "tickVolume": 900, "spread": 22, "state": "intermediate"},
    ]
    monkeypatch.setattr(confluence.broker, "get_historical_candles", lambda *a, **k: raw)

    candles = confluence.fetch_gold_candles("tok", "acc123", region="london")

    assert [c["time"] for c in candles] == ["2026-09-29 15:50:00", "2026-09-29 15:55:00"]
    assert candles[0]["open"] == 4159.2
    assert candles[0]["tick_volume"] == 2534
    assert candles[0]["spread"] == 21


def test_fetch_gold_candles_passes_token_account_symbol_timeframe_region(monkeypatch):
    captured = {}

    def fake_get_historical(token, account_id, symbol, timeframe, limit=None, region=None):
        captured.update(token=token, account_id=account_id, symbol=symbol, timeframe=timeframe,
                         limit=limit, region=region)
        return [{"time": "2026-09-29T15:50:00.000Z", "open": 1, "high": 1, "low": 1, "close": 1,
                  "tickVolume": 1, "spread": 1, "state": "complete"}]

    monkeypatch.setattr(confluence.broker, "get_historical_candles", fake_get_historical)
    confluence.fetch_gold_candles("tok", "acc123", region="new-york")

    assert captured["token"] == "tok"
    assert captured["account_id"] == "acc123"
    assert captured["symbol"] == "XAUUSD"
    assert captured["timeframe"] == "5m"
    assert captured["region"] == "new-york"
    assert captured["limit"] == 100


def test_fetch_gold_candles_raises_when_no_complete_candles(monkeypatch):
    raw = [{"time": "2026-09-29T16:00:00.000Z", "open": 1, "high": 1, "low": 1, "close": 1,
             "tickVolume": 1, "spread": 1, "state": "intermediate"}]
    monkeypatch.setattr(confluence.broker, "get_historical_candles", lambda *a, **k: raw)

    with pytest.raises(RuntimeError, match="aucune bougie complète"):
        confluence.fetch_gold_candles("tok", "acc123")


def test_fetch_gold_candles_validates_candles(monkeypatch):
    raw = [{"time": "2026-09-29T16:00:00.000Z", "open": 100, "high": 95, "low": 99, "close": 100.5,
             "tickVolume": 1, "spread": 1, "state": "complete"}]
    monkeypatch.setattr(confluence.broker, "get_historical_candles", lambda *a, **k: raw)

    with pytest.raises(RuntimeError, match="incohérente"):
        confluence.fetch_gold_candles("tok", "acc123")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/gold_bot/test_confluence.py -k fetch_gold_candles -v`
Expected: FAIL (`fetch_gold_candles()` prend encore `api_key` en argument unique, `confluence.broker` n'existe pas encore)

- [ ] **Step 4: Implement**

Dans `gold_bot/confluence.py` :

1. Le bloc d'imports en haut du fichier passe de :
```python
import math
from datetime import datetime, timezone

import requests

import gold_bot.chart_patterns as chart_patterns
```
à :
```python
import math
from datetime import datetime, timezone

import gold_bot.broker as broker
import gold_bot.chart_patterns as chart_patterns
```
(supprime `import requests`, ajoute `import gold_bot.broker as broker` groupé avec l'import local existant, ordre alphabétique `broker` avant `chart_patterns`)
2. Supprimer la ligne `TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"`.
3. Remplacer entièrement la fonction `fetch_gold_candles` par :

```python
def fetch_gold_candles(token: str, account_id: str, region: str = broker.DEFAULT_MT5_REGION) -> list[dict]:
    """Récupère les dernières bougies 5min XAU/USD via l'API de données de
    marché MetaApi (broker.get_historical_candles) -- hôte dédié, différent
    de l'API de trading. Renvoie un tableau chronologique (plus ancien en
    premier), jamais vide en cas de succès, uniquement des bougies closes
    (state == "complete", jamais la bougie en formation). Bascule du
    2026-09-29 (voir docs/superpowers/specs/2026-09-29-gold-bot-session-
    breakout-volume-design.md) : remplace Twelve Data, donne accès à
    tick_volume/spread (absents de Twelve Data pour XAU/USD). `limit=100`
    reste une large marge au-dessus de SCALP_MIN_CANDLES (11) en un seul
    appel -- pas besoin de pagination multi-pages pour cette taille de
    fenêtre (contrairement à fetch_gold_candles_range, qui couvre des
    mois/années, voir gold_bot.backtest)."""
    raw = broker.get_historical_candles(token, account_id, "XAUUSD", "5m", limit=100, region=region)
    candles = [
        {
            "time": datetime.fromisoformat(c["time"].replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S"),
            "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"],
            "tick_volume": c["tickVolume"], "spread": c["spread"],
        }
        for c in raw if c["state"] == "complete"
    ]
    candles.sort(key=lambda c: c["time"])
    if not candles:
        raise RuntimeError("MetaApi n'a renvoyé aucune bougie complète pour XAUUSD")
    validate_candles(candles)
    return candles
```

4. Ne PAS supprimer `_parse_float` ni `import math`/`from datetime import datetime, timezone` — `_parse_float` reste utilisée par `gold_bot/backtest.py` jusqu'à la Task 4, `math`/`datetime` restent utilisées par `validate_candles`/le reste du fichier.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/gold_bot/test_confluence.py -v`
Expected: PASS (toutes, y compris tous les tests déjà existants du fichier -- `compute_signal`, `detect_pivots`, `is_news_blackout`, `is_market_closed`, `validate_candles`, etc. ne sont pas affectés par ce changement)

- [ ] **Step 6: Commit**

```bash
git add gold_bot/confluence.py tests/gold_bot/test_confluence.py
git commit -m "feat(gold_bot): bascule fetch_gold_candles de Twelve Data vers MetaApi (tick volume + spread)"
```

---

### Task 3: `loop.py` — cascade de signature (token/account_id/region au lieu de twelve_data_api_key)

**Files:**
- Modify: `gold_bot/loop.py:156-186,247,276-284`
- Test: `tests/gold_bot/test_loop.py`

**Interfaces:**
- Consumes: `confluence.fetch_gold_candles(token, account_id, region)` (Task 2).
- Produces: `run_cycle(token: str, account_id: str, circuit_breaker: "risk.CircuitBreaker", region=..., symbol=..., now=...) -> dict` — le paramètre `twelve_data_api_key` disparaît (3e position), `circuit_breaker` devient le 3e paramètre positionnel. Aucun autre changement de logique.

- [ ] **Step 1: Update existing test call sites (mechanical, no new test needed)**

Dans `tests/gold_bot/test_loop.py`, remplacer **toutes** les occurrences de `run_cycle("tok", "acc", "td-key", ` par `run_cycle("tok", "acc", ` (27 occurrences dans ce fichier, toutes suivent exactement ce motif — un remplacement global du texte `"tok", "acc", "td-key", ` vers `"tok", "acc", ` sur tout le fichier couvre les 27 d'un coup). Ne touche à aucun autre argument (`loop.risk.CircuitBreaker()`, `now=...`, etc. restent identiques après le 3e argument supprimé).

Ce n'est pas un test à écrire — c'est la mise à jour mécanique des tests déjà existants requise avant que la Step 3 (implémentation) puisse les faire passer.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gold_bot/test_loop.py -v`
Expected: FAIL — `run_cycle()` prend encore `twelve_data_api_key` en 3e position, donc `circuit_breaker` (maintenant passé en 3e position par les tests mis à jour) est reçu comme `twelve_data_api_key`, provoquant des erreurs de type dans le corps de la fonction (`current_state["kill_switch"]` etc. échoueront avant même d'atteindre l'appel réseau, ou `TypeError`/`AttributeError` selon le chemin exact).

- [ ] **Step 3: Implement**

Dans `gold_bot/loop.py` :

1. Ligne 156-158, remplacer la signature :
```python
def run_cycle(token: str, account_id: str,
              circuit_breaker: "risk.CircuitBreaker", region: str = broker.DEFAULT_MT5_REGION,
              symbol: str = SYMBOL, now: datetime | None = None) -> dict:
```
(supprime uniquement le paramètre `twelve_data_api_key: str,` ; ne change rien à la docstring qui suit, ni au reste de la fonction)

2. Ligne 186, remplacer :
```python
        candles = _with_retry(lambda: confluence.fetch_gold_candles(twelve_data_api_key))
```
par :
```python
        candles = _with_retry(lambda: confluence.fetch_gold_candles(token, account_id, region))
```

3. Ligne 247, remplacer :
```python
            fresh_candles = _with_retry(lambda: confluence.fetch_gold_candles(twelve_data_api_key))
```
par :
```python
            fresh_candles = _with_retry(lambda: confluence.fetch_gold_candles(token, account_id, region))
```

4. Ligne 276-284, dans `main()`, remplacer :
```python
def main():
    token = os.environ["METAAPI_TRADE_TOKEN"]
    account_id = os.environ["METAAPI_TRADE_ACCOUNT_ID"]
    twelve_data_api_key = os.environ["TWELVE_DATA_API_KEY"]
    circuit_breaker = risk.CircuitBreaker(persist_path=CIRCUIT_BREAKER_STATE_PATH)

    while True:
        try:
            run_cycle(token, account_id, twelve_data_api_key, circuit_breaker)
```
par :
```python
def main():
    token = os.environ["METAAPI_TRADE_TOKEN"]
    account_id = os.environ["METAAPI_TRADE_ACCOUNT_ID"]
    circuit_breaker = risk.CircuitBreaker(persist_path=CIRCUIT_BREAKER_STATE_PATH)

    while True:
        try:
            run_cycle(token, account_id, circuit_breaker)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gold_bot/test_loop.py -v`
Expected: PASS (toutes, sans exception)

- [ ] **Step 5: Commit**

```bash
git add gold_bot/loop.py tests/gold_bot/test_loop.py
git commit -m "feat(gold_bot): loop.py n'a plus besoin de TWELVE_DATA_API_KEY (fetch_gold_candles utilise déjà token/account_id/region)"
```

---

### Task 4: `backtest.fetch_gold_candles_range` — bascule vers MetaApi (pagination)

**Files:**
- Modify: `gold_bot/backtest.py:1-95,256-291`
- Test: `tests/gold_bot/test_backtest.py`

**Interfaces:**
- Consumes: `broker.get_historical_candles(token, account_id, symbol, timeframe, start_time=..., limit=..., region=...)` (Task 1).
- Produces: `fetch_gold_candles_range(token: str, account_id: str, start: datetime, end: datetime, region: str = broker.DEFAULT_MT5_REGION) -> list[dict]` (remplace `fetch_gold_candles_range(api_key, start, end)`) ; `run_backtest(token: str, account_id: str, days=..., end=..., region=...) -> dict` (remplace `run_backtest(api_key, days, end)`).

- [ ] **Step 1: Remove the old Twelve-Data-specific tests**

Dans `tests/gold_bot/test_backtest.py`, supprimer entièrement : `test_fetch_gold_candles_range_single_page`, `test_fetch_gold_candles_range_paginates_when_more_than_one_page_needed`, `test_fetch_gold_candles_range_rejects_on_error_status`, `test_fetch_gold_candles_range_rejects_on_http_error`, `test_fetch_gold_candles_range_network_error_never_leaks_the_api_key`. Garder `test_fetch_gold_candles_range_rejects_start_after_end` (logique inchangée, indépendante de la source de données) et `test_fetch_gold_candles_range_rejects_inconsistent_ohlc` (idem). Garder aussi `_FakeTDResponse` seulement si elle est encore utilisée ailleurs dans le fichier après ces suppressions -- si plus aucun test du fichier ne l'utilise, la supprimer aussi.

- [ ] **Step 2: Write the new failing tests**

```python
def test_fetch_gold_candles_range_paginates_backward_until_start_reached(monkeypatch):
    calls = []

    def fake_get_historical(token, account_id, symbol, timeframe, start_time=None, limit=None, region=None):
        calls.append(start_time)
        if len(calls) == 1:
            return [
                {"time": "2026-01-01T00:10:00.000Z", "open": 10, "high": 11, "low": 9, "close": 10.5,
                 "tickVolume": 5, "spread": 2, "state": "complete"},
                {"time": "2026-01-01T00:05:00.000Z", "open": 10, "high": 11, "low": 9, "close": 10.2,
                 "tickVolume": 5, "spread": 2, "state": "complete"},
            ]
        return [
            {"time": "2026-01-01T00:00:00.000Z", "open": 10, "high": 11, "low": 9, "close": 10.1,
             "tickVolume": 5, "spread": 2, "state": "complete"},
        ]

    monkeypatch.setattr(backtest.broker, "get_historical_candles", fake_get_historical)
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc)

    result = backtest.fetch_gold_candles_range("tok", "acc123", start, end)

    assert len(calls) >= 2
    times = [c["time"] for c in result]
    assert times == sorted(times)
    assert times[0] == "2026-01-01 00:00:00"
    assert times[-1] == "2026-01-01 00:10:00"
    assert result[0]["tick_volume"] == 5
    assert result[0]["spread"] == 2


def test_fetch_gold_candles_range_excludes_intermediate_state_candles(monkeypatch):
    raw = [
        {"time": "2026-01-01T00:05:00.000Z", "open": 10, "high": 11, "low": 9, "close": 10.5,
         "tickVolume": 5, "spread": 2, "state": "complete"},
        {"time": "2026-01-01T00:10:00.000Z", "open": 10, "high": 11, "low": 9, "close": 10.6,
         "tickVolume": 5, "spread": 2, "state": "intermediate"},
    ]
    monkeypatch.setattr(backtest.broker, "get_historical_candles", lambda *a, **k: raw)

    result = backtest.fetch_gold_candles_range(
        "tok", "acc123",
        datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 0, 15, tzinfo=timezone.utc),
    )

    assert len(result) == 1
    assert result[0]["time"] == "2026-01-01 00:05:00"


def test_fetch_gold_candles_range_rejects_start_after_end():
    with pytest.raises(ValueError):
        backtest.fetch_gold_candles_range(
            "tok", "acc123",
            datetime(2026, 1, 2, tzinfo=timezone.utc),
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
```

(`test_fetch_gold_candles_range_rejects_start_after_end` remplace la version existante du même nom -- même comportement, juste adaptée à la nouvelle signature `(token, account_id, start, end)`. `test_fetch_gold_candles_range_rejects_inconsistent_ohlc`, déjà présent, doit être adapté de la même façon : remplacer son `monkeypatch.setattr(backtest.requests, "get", fake_get)` et son appel `backtest.fetch_gold_candles_range("fake-key", ...)` par le nouveau style `monkeypatch.setattr(backtest.broker, "get_historical_candles", ...)` et `backtest.fetch_gold_candles_range("tok", "acc123", ...)`, en gardant sa bougie incohérente et son `pytest.raises(RuntimeError, match="incohérente")` intacts.)

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/gold_bot/test_backtest.py -k fetch_gold_candles_range -v`
Expected: FAIL (`backtest.broker` n'existe pas encore, `fetch_gold_candles_range` prend encore `api_key` en 1er argument)

- [ ] **Step 4: Implement**

Dans `gold_bot/backtest.py` :

1. Le bloc d'imports en haut du fichier passe de :
```python
import json
import os
from datetime import datetime, timedelta, timezone

import requests

import gold_bot.confluence as confluence
```
à :
```python
import json
import os
from datetime import datetime, timedelta, timezone

import gold_bot.broker as broker
import gold_bot.confluence as confluence
```
(supprime `import requests`, ajoute `import gold_bot.broker as broker` groupé avec l'import local existant, ordre alphabétique `broker` avant `confluence`)
2. Supprimer `TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"`.
3. Remplacer `MAX_OUTPUT_SIZE = 5000` et son commentaire par :
```python
# Plafond de l'API MetaApi données de marché pour un seul appel.
MAX_OUTPUT_SIZE = 1000
```
4. Remplacer entièrement `fetch_gold_candles_range` par :

```python
def fetch_gold_candles_range(token: str, account_id: str, start: datetime, end: datetime,
                              region: str = broker.DEFAULT_MT5_REGION) -> list[dict]:
    """Récupère tout l'historique XAU/USD 5min entre `start` et `end`
    (bornes incluses, UTC) via l'API de données de marché MetaApi, en
    paginant par blocs de MAX_OUTPUT_SIZE via le paramètre `start_time`
    (chaque appel renvoie au plus MAX_OUTPUT_SIZE bougies se terminant à
    `start_time`, les plus récentes en premier). Résultat trié
    chronologiquement, sans doublon, uniquement des bougies closes
    (state == "complete"). Bascule du 2026-09-29 (voir docs/superpowers/
    specs/2026-09-29-gold-bot-session-breakout-volume-design.md) :
    remplace Twelve Data, donne accès à tick_volume/spread."""
    if start >= end:
        raise ValueError("start doit être strictement antérieur à end")

    all_candles: dict[str, dict] = {}
    cursor = end.strftime("%Y-%m-%d %H:%M:%S.000")
    # Marge généreuse : même avec des pages plus petites que MAX_OUTPUT_SIZE
    # (marché fermé, trous de données), ce plafond laisse largement de quoi
    # couvrir la plage demandée sans boucler indéfiniment en cas de réponse
    # inattendue de l'API.
    max_iterations = max(10, int((end - start).total_seconds() / 60 / 5 / MAX_OUTPUT_SIZE) + 10)

    for _ in range(max_iterations):
        raw = broker.get_historical_candles(token, account_id, "XAUUSD", "5m",
                                             start_time=cursor, limit=MAX_OUTPUT_SIZE, region=region)
        if not raw:
            break
        for c in raw:
            if c["state"] != "complete":
                continue
            time_str = datetime.fromisoformat(c["time"].replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")
            all_candles[time_str] = {
                "time": time_str, "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"],
                "tick_volume": c["tickVolume"], "spread": c["spread"],
            }
        oldest = min(
            datetime.fromisoformat(c["time"].replace("Z", "+00:00")) for c in raw
        )
        if oldest <= start:
            break
        cursor = oldest.strftime("%Y-%m-%d %H:%M:%S.000")

    result = [
        c for c in all_candles.values()
        if start <= datetime.fromisoformat(c["time"].replace(" ", "T")).replace(tzinfo=timezone.utc) <= end
    ]
    result.sort(key=lambda c: c["time"])
    if result:
        confluence.validate_candles(result)
    return result
```

5. Ligne 256-271 (`run_backtest`), remplacer la signature et l'appel :
```python
def run_backtest(token: str, account_id: str, days: int = DEFAULT_BACKTEST_DAYS,
                  end: datetime | None = None, region: str = broker.DEFAULT_MT5_REGION) -> dict:
    """Un backtest complet : récupère `days` jours d'historique se terminant
    à `end` (défaut : maintenant), rejoue le moteur réel, résume. `end` est
    injectable pour les tests, même convention que loop.run_cycle(now=...)."""
    end = end or datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    candles = fetch_gold_candles_range(token, account_id, start, end, region=region)
    trades = simulate_trades(candles)
    summary = summarize_trades(trades)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "candle_count": len(candles),
        "summary": summary,
        "trades": trades,
    }
```

6. Ligne 274-291 (`main`), remplacer :
```python
    api_key = os.environ["TWELVE_DATA_API_KEY"]
    result = run_backtest(api_key, days=args.days)
```
par :
```python
    token = os.environ["METAAPI_TRADE_TOKEN"]
    account_id = os.environ["METAAPI_TRADE_ACCOUNT_ID"]
    result = run_backtest(token, account_id, days=args.days)
```
Ajuster aussi la description de l'`ArgumentParser` (`"Backtest du moteur de confluence Or réel (gold_bot.confluence) sur données historiques Twelve Data."`) pour dire `"...sur données historiques MetaApi."` au lieu de Twelve Data.

7. Ne PAS toucher `_compute_return`, `_close_trade`, `_open_trade_from_signal`, `simulate_trades`, `summarize_trades` dans cette tâche (Task 5 les modifie séparément).

8. Vérifier qu'après ce changement, `confluence._parse_float` n'est plus référencée nulle part dans le dépôt (`grep -rn "_parse_float" gold_bot/ tests/`) -- si c'est le cas, supprimer la fonction `_parse_float` et sa docstring de `gold_bot/confluence.py`, et supprimer `import math` de `confluence.py` UNIQUEMENT s'il n'est plus utilisé ailleurs dans ce fichier (il l'est encore, par `validate_candles` -- donc `import math` reste, seule `_parse_float` part).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/gold_bot/test_backtest.py tests/gold_bot/test_confluence.py -v`
Expected: PASS (toutes)

- [ ] **Step 6: Commit**

```bash
git add gold_bot/backtest.py gold_bot/confluence.py tests/gold_bot/test_backtest.py
git commit -m "feat(gold_bot): bascule fetch_gold_candles_range/run_backtest de Twelve Data vers MetaApi"
```

---

### Task 5: coût de transaction réel (spread) dans `backtest.simulate_trades`

**Files:**
- Modify: `gold_bot/backtest.py:97-123,126-220`
- Test: `tests/gold_bot/test_backtest.py`

**Interfaces:**
- Consumes: chaque bougie porte désormais `"spread"` (points, depuis Task 4 ; `0.0` par défaut si absent, pour ne pas casser les fixtures de test existantes qui n'en ont pas).
- Produces: `_compute_return(direction, entry_price, close_price, entry_spread_points=0.0)`, `_open_trade_from_signal(signal, entry_time, entry_spread=0.0)` — signatures étendues, rétro-compatibles (valeurs par défaut).

- [ ] **Step 1: Write the failing tests**

```python
def test_compute_return_subtracts_spread_cost_for_achat():
    return_usd, return_pct = backtest._compute_return("achat", 100.0, 110.0, entry_spread_points=20)
    # spread 20 points * XAUUSD_POINT_SIZE (0.01) = 0.20 $ retranché
    assert return_usd == pytest.approx(10.0 - 0.20)


def test_compute_return_subtracts_spread_cost_for_vente():
    return_usd, return_pct = backtest._compute_return("vente", 100.0, 90.0, entry_spread_points=20)
    assert return_usd == pytest.approx(10.0 - 0.20)


def test_compute_return_defaults_to_zero_spread_cost():
    return_usd, _ = backtest._compute_return("achat", 100.0, 110.0)
    assert return_usd == pytest.approx(10.0)


def test_simulate_trades_captures_entry_spread_from_the_opening_candle():
    candles = _warmup_candles(confluence.SCALP_MIN_CANDLES)
    for c in candles:
        c["spread"] = 15
    entry_price = 100.0
    stop_loss = 95.0
    take_profit = 110.0
    entry_time = datetime.strptime(candles[-1]["time"], "%Y-%m-%d %H:%M:%S")
    next_time = entry_time + timedelta(minutes=1)
    next_candle = _candle(next_time.strftime("%Y-%m-%d %H:%M:%S"), 100, 101, 94, 96)
    next_candle["spread"] = 15
    candles.append(next_candle)

    trades = backtest.simulate_trades(
        candles, signal_fn=_fire_once_then_neutral("achat", entry_price, stop_loss, take_profit))

    assert len(trades) == 1
    # SL touche a 95.0, spread de 15 points = 0.15$ retranche du (95-100)=-5.0
    assert trades[0]["return_usd"] == pytest.approx(-5.0 - 0.15)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gold_bot/test_backtest.py -k "spread" -v`
Expected: FAIL — `_compute_return()` ne prend pas encore `entry_spread_points`, `simulate_trades` ne capture pas encore le spread d'entrée.

- [ ] **Step 3: Implement**

Dans `gold_bot/backtest.py` :

1. Ajouter après `MAX_OUTPUT_SIZE`, avant `SIGNAL_WINDOW_SIZE` :
```python
# Hypothèse documentée : XAUUSD coté à 2 décimales chez ce courtier, donc
# 1 point = 0.01 $ -- à vérifier via broker.get_symbol_specification (champ
# "digits") si jamais exposé, non vérifié lors de la conception de ce
# module. Utilisé uniquement pour modéliser un coût de transaction
# réaliste en backtest -- n'affecte jamais l'exécution réelle (voir
# gold_bot.broker.place_market_order, qui ne connaît pas cette constante).
XAUUSD_POINT_SIZE = 0.01
```

2. Remplacer `_compute_return` :
```python
def _compute_return(direction: str, entry_price: float, close_price: float,
                     entry_spread_points: float = 0.0) -> tuple[float, float]:
    """Achat : gagnant si le prix monte. Vente : gagnant si le prix baisse.
    Même convention que computeReturn (scalping_tracker.js). Le coût du
    spread (en points, capturé à l'entrée, converti en $ via
    XAUUSD_POINT_SIZE) est retranché une fois par trade complet
    (aller-retour) -- backtest uniquement, voir XAUUSD_POINT_SIZE."""
    return_usd = close_price - entry_price if direction == "achat" else entry_price - close_price
    return_usd -= entry_spread_points * XAUUSD_POINT_SIZE
    return_pct = (return_usd / entry_price) * 100
    return return_usd, return_pct
```

3. Remplacer `_close_trade` :
```python
def _close_trade(trade: dict, close_price: float, reason: str, close_time: str) -> None:
    return_usd, return_pct = _compute_return(
        trade["direction"], trade["entry_price"], close_price, trade.get("entry_spread", 0.0))
    trade["close_time"] = close_time
    trade["close_price"] = close_price
    trade["close_reason"] = reason
    trade["return_usd"] = return_usd
    trade["return_pct"] = return_pct
```

4. Remplacer `_open_trade_from_signal` :
```python
def _open_trade_from_signal(signal: dict, entry_time: str, entry_spread: float = 0.0) -> dict:
    return {
        "direction": signal["status"],
        "entry_time": entry_time,
        "entry_price": signal["entry"],
        "stop_loss": signal["stop_loss"],
        "take_profit": signal["take_profit"],
        "trend_at_entry": signal["trend"],
        "pattern_at_entry": signal["pattern"]["name"] if signal["pattern"] else None,
        "entry_spread": entry_spread,
    }
```

5. Dans `simulate_trades`, les deux appels à `_open_trade_from_signal(signal, current["time"])` (un dans la branche renversement, un dans la branche ouverture initiale) deviennent `_open_trade_from_signal(signal, current["time"], current.get("spread", 0.0))`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gold_bot/test_backtest.py -v`
Expected: PASS (toutes, y compris tous les tests `simulate_trades`/`summarize_trades` déjà existants -- `current.get("spread", 0.0)` vaut `0.0` pour toutes leurs fixtures existantes qui n'ont pas de clé `"spread"`, donc leur comportement/valeurs numériques restent identiques)

- [ ] **Step 5: Commit**

```bash
git add gold_bot/backtest.py tests/gold_bot/test_backtest.py
git commit -m "feat(gold_bot): modélise le coût du spread réel dans les backtests"
```

---

### Task 6: `gold_bot/session_breakout.py` — nouveau moteur de signal

**Files:**
- Create: `gold_bot/session_breakout.py`
- Test: `tests/gold_bot/test_session_breakout.py`

**Interfaces:**
- Consumes: `confluence.is_news_blackout`, `confluence.meets_minimum_risk_reward`, `confluence.CHARTPATTERN_STOP_BUFFER` (tous inchangés, déjà existants).
- Produces: `session_breakout.compute_signal(candles: list[dict]) -> dict` — même contrat de sortie que `confluence.compute_signal` (`{"status", "price", "entry", "stop_loss", "take_profit", "trend", "pattern"}`, `"pattern"` toujours `None` pour ce moteur), utilisable directement comme `signal_fn` de `backtest.simulate_trades`. `compute_asian_range(candles, as_of) -> dict | None` exposée séparément pour ses propres tests.

- [ ] **Step 1: Write the failing tests**

Créer `tests/gold_bot/test_session_breakout.py` :

```python
from datetime import datetime, timedelta, timezone

import pytest

import gold_bot.session_breakout as session_breakout


def _candle(dt, open_, high, low, close, tick_volume=100, spread=1):
    return {"time": dt.strftime("%Y-%m-%d %H:%M:%S"), "open": open_, "high": high, "low": low,
            "close": close, "tick_volume": tick_volume, "spread": spread}


def _asian_session_candles(day_start, high=2010.0, low=2000.0, count=48, tick_volume=100):
    """`count` bougies de 5min à partir de `day_start` (00:00 UTC), toutes
    au milieu du range sauf 2 qui portent exactement `high`/`low` -- range
    asiatique contrôlé pour les tests."""
    mid = (high + low) / 2
    candles = [_candle(day_start + timedelta(minutes=5 * i), mid, mid, mid, mid, tick_volume) for i in range(count)]
    candles[0]["high"] = high
    candles[1]["low"] = low
    return candles


def _lookback_candles(start, count, price=2005.0, tick_volume=100):
    """`count` bougies neutres de 5min à partir de `start`, prix constant
    -- sert à remplir VOLUME_CONFIRMATION_LOOKBACK avant une bougie de
    cassure."""
    return [_candle(start + timedelta(minutes=5 * i), price, price + 1, price - 1, price, tick_volume)
            for i in range(count)]


DAY = datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc)  # lundi, loin de tout évènement macro
TRADING_START = datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc)


def test_compute_asian_range_returns_high_low_when_enough_candles():
    candles = _asian_session_candles(DAY, high=2010.0, low=2000.0, count=48)
    result = session_breakout.compute_asian_range(candles, TRADING_START)
    assert result == {"high": 2010.0, "low": 2000.0}


def test_compute_asian_range_none_when_too_few_candles():
    candles = _asian_session_candles(DAY, count=20)  # < MIN_ASIAN_SESSION_CANDLES (48)
    assert session_breakout.compute_asian_range(candles, TRADING_START) is None


def test_compute_asian_range_ignores_candles_outside_asian_window():
    candles = _asian_session_candles(DAY, high=2010.0, low=2000.0, count=48)
    # Bougie de la fenêtre de trading (08h) avec un extrême bien plus large --
    # ne doit pas influencer le range asiatique.
    candles.append(_candle(TRADING_START, 1900.0, 2500.0, 1900.0, 2000.0))
    result = session_breakout.compute_asian_range(candles, TRADING_START)
    assert result == {"high": 2010.0, "low": 2000.0}


def test_compute_signal_neutre_outside_trading_window():
    candles = _asian_session_candles(DAY, count=48)
    late = datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc)  # hors 08h-16h UTC
    candles.append(_candle(late - timedelta(minutes=5), 2005, 2006, 2004, 2005))
    candles.append(_candle(late, 2011, 2015, 2010, 2013, tick_volume=1000))
    result = session_breakout.compute_signal(candles)
    assert result["status"] == "neutre"
    assert result["entry"] is None


def test_compute_signal_neutre_when_asian_range_invalid():
    candles = _asian_session_candles(DAY, count=20)  # < MIN_ASIAN_SESSION_CANDLES
    candles.append(_candle(TRADING_START, 2011, 2015, 2010, 2013, tick_volume=1000))
    result = session_breakout.compute_signal(candles)
    assert result["status"] == "neutre"


def test_compute_signal_achat_on_fresh_upward_crossing_with_volume_confirmation():
    candles = _asian_session_candles(DAY, high=2010.0, low=2000.0, count=48, tick_volume=100)
    candles += _lookback_candles(TRADING_START, 21, price=2005.0, tick_volume=100)
    # dernière bougie du lookback (index -1 avant la cassure) reste sous range_high (2010)
    breakout_time = TRADING_START + timedelta(minutes=5 * 21)
    candles.append(_candle(breakout_time, 2009, 2013, 2008, 2012, tick_volume=200))  # 200 > 1.5*100

    result = session_breakout.compute_signal(candles)

    assert result["status"] == "achat"
    assert result["entry"] == 2012
    assert result["stop_loss"] == pytest.approx(2010.0 - 1.0)  # range_high - CHARTPATTERN_STOP_BUFFER
    assert result["take_profit"] == pytest.approx(2012.0 + 10.0)  # breakout + (range_high-range_low)
    assert result["trend"] == "haussier"
    assert result["pattern"] is None


def test_compute_signal_vente_on_fresh_downward_crossing_with_volume_confirmation():
    candles = _asian_session_candles(DAY, high=2010.0, low=2000.0, count=48, tick_volume=100)
    candles += _lookback_candles(TRADING_START, 21, price=2005.0, tick_volume=100)
    breakout_time = TRADING_START + timedelta(minutes=5 * 21)
    candles.append(_candle(breakout_time, 2001, 1997, 1996, 1998, tick_volume=200))  # close 1998 < range_low 2000

    result = session_breakout.compute_signal(candles)

    assert result["status"] == "vente"
    assert result["entry"] == 1998
    assert result["stop_loss"] == pytest.approx(2000.0 + 1.0)
    assert result["take_profit"] == pytest.approx(1998.0 - 10.0)
    assert result["trend"] == "baissier"


def test_compute_signal_neutre_when_crossing_not_fresh():
    candles = _asian_session_candles(DAY, high=2010.0, low=2000.0, count=48, tick_volume=100)
    candles += _lookback_candles(TRADING_START, 20, price=2005.0, tick_volume=100)
    t1 = TRADING_START + timedelta(minutes=5 * 20)
    candles.append(_candle(t1, 2011, 2012, 2010.5, 2011, tick_volume=100))  # déjà au-dessus du range
    t2 = t1 + timedelta(minutes=5)
    candles.append(_candle(t2, 2011, 2015, 2010.5, 2013, tick_volume=300))  # toujours au-dessus, volume fort

    result = session_breakout.compute_signal(candles)
    assert result["status"] == "neutre"


def test_compute_signal_neutre_when_volume_not_confirmed():
    candles = _asian_session_candles(DAY, high=2010.0, low=2000.0, count=48, tick_volume=100)
    candles += _lookback_candles(TRADING_START, 21, price=2005.0, tick_volume=100)
    breakout_time = TRADING_START + timedelta(minutes=5 * 21)
    candles.append(_candle(breakout_time, 2009, 2013, 2008, 2012, tick_volume=120))  # 120 < 1.5*100=150

    result = session_breakout.compute_signal(candles)
    assert result["status"] == "neutre"


def test_compute_signal_neutre_when_risk_reward_insufficient():
    # Range très étroit (1.0) : risque (~buffer=1.0) proche de la récompense
    # (range_height=1.0) -> ratio < 1.5, rejeté par meets_minimum_risk_reward.
    candles = _asian_session_candles(DAY, high=2001.0, low=2000.0, count=48, tick_volume=100)
    candles += _lookback_candles(TRADING_START, 21, price=2000.5, tick_volume=100)
    breakout_time = TRADING_START + timedelta(minutes=5 * 21)
    candles.append(_candle(breakout_time, 2000.8, 2002, 2000.5, 2001.5, tick_volume=1000))

    result = session_breakout.compute_signal(candles)
    assert result["status"] == "neutre"


def test_compute_signal_neutre_during_news_blackout():
    # CPI du 11/09/2026, 12h30 UTC (voir confluence.SCALP_HIGH_IMPACT_EVENTS_UTC).
    day = datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc)
    candles = _asian_session_candles(day, high=2010.0, low=2000.0, count=48, tick_volume=100)
    trading_start = datetime(2026, 9, 11, 8, 0, tzinfo=timezone.utc)
    candles += _lookback_candles(trading_start, 20, price=2005.0, tick_volume=100)
    t1 = datetime(2026, 9, 11, 12, 25, tzinfo=timezone.utc)
    candles.append(_candle(t1, 2005, 2006, 2004, 2005, tick_volume=100))
    t2 = datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc)  # exactement le CPI
    candles.append(_candle(t2, 2009, 2013, 2008, 2012, tick_volume=1000))

    result = session_breakout.compute_signal(candles)
    assert result["status"] == "neutre"


def test_compute_signal_neutre_when_no_candles():
    assert session_breakout.compute_signal([])["status"] == "neutre"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gold_bot/test_session_breakout.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'gold_bot.session_breakout'`

- [ ] **Step 3: Implement**

Créer `gold_bot/session_breakout.py` :

```python
# gold_bot/session_breakout.py
# Moteur de signal "cassure de range de session, confirmée par le volume"
# -- voir docs/superpowers/specs/2026-09-29-gold-bot-session-breakout-
# volume-design.md. Hypothèse de marché différente du moteur à figures
# chartistes de gold_bot/confluence.py (toujours présent, non modifié) :
# variation structurelle de la liquidité/participation selon les sessions
# de trading, confirmée par le volume réel (tick volume), plutôt que de
# la reconnaissance de forme pure. N'est PAS branché sur le bot en
# production par ce module -- la décision de bascule se prend après le
# plan de validation multi-années décrit dans la spec.
from datetime import datetime, timezone

import gold_bot.confluence as confluence

ASIAN_SESSION_START_HOUR = 0
ASIAN_SESSION_END_HOUR = 8
TRADING_WINDOW_START_HOUR = 8
TRADING_WINDOW_END_HOUR = 16
MIN_ASIAN_SESSION_CANDLES = 48
VOLUME_CONFIRMATION_MULTIPLE = 1.5
VOLUME_CONFIRMATION_LOOKBACK = 20


def _candle_dt(candle: dict) -> datetime:
    return datetime.fromisoformat(candle["time"].replace(" ", "T")).replace(tzinfo=timezone.utc)


def compute_asian_range(candles: list[dict], as_of: datetime) -> dict | None:
    """Plus haut/plus bas de la session asiatique (00h-08h UTC) du jour
    UTC de `as_of`. None si moins de MIN_ASIAN_SESSION_CANDLES bougies
    complètes sont disponibles pour cette fenêtre (trou de données ou
    fenêtre pas encore écoulée) -- neutre plutôt que deviner sur un
    échantillon non représentatif."""
    today = as_of.date()
    session_candles = [
        c for c in candles
        if _candle_dt(c).date() == today
        and ASIAN_SESSION_START_HOUR <= _candle_dt(c).hour < ASIAN_SESSION_END_HOUR
    ]
    if len(session_candles) < MIN_ASIAN_SESSION_CANDLES:
        return None
    return {
        "high": max(c["high"] for c in session_candles),
        "low": min(c["low"] for c in session_candles),
    }


def _fresh_crossing(candles: list[dict], asian_range: dict) -> tuple[str, float] | None:
    """Bougie de franchissement : le close courant dépasse une borne du
    range que le close précédent ne dépassait pas encore -- purement
    local (2 dernières bougies), pour que rester au-delà du range après
    la cassure ne redéclenche pas le signal indéfiniment. Une deuxième
    cassure plus tard dans la journée (après un retour dans le range)
    reste possible et légitime."""
    if len(candles) < 2:
        return None
    previous_close = candles[-2]["close"]
    current_close = candles[-1]["close"]
    if current_close > asian_range["high"] and previous_close <= asian_range["high"]:
        return "achat", current_close
    if current_close < asian_range["low"] and previous_close >= asian_range["low"]:
        return "vente", current_close
    return None


def _has_volume_confirmation(candles: list[dict]) -> bool:
    """tick_volume de la dernière bougie > VOLUME_CONFIRMATION_MULTIPLE
    fois la moyenne des VOLUME_CONFIRMATION_LOOKBACK bougies précédentes.
    False si pas assez d'historique pour calculer une moyenne."""
    if len(candles) < VOLUME_CONFIRMATION_LOOKBACK + 1:
        return False
    lookback = candles[-1 - VOLUME_CONFIRMATION_LOOKBACK:-1]
    average = sum(c["tick_volume"] for c in lookback) / len(lookback)
    if average <= 0:
        return False
    return candles[-1]["tick_volume"] > VOLUME_CONFIRMATION_MULTIPLE * average


def compute_signal(candles: list[dict]) -> dict:
    """Moteur de suivi de tendance par cassure de session confirmée par le
    volume -- voir le design pour le détail complet. Ne lève jamais
    d'exception : candles vide, hors fenêtre de trading, range invalide,
    cassure non fraîche, volume insuffisant, ratio risque/rendement
    insuffisant, ou black-out news renvoient tous neutre avec les prix à
    None."""
    price = candles[-1]["close"] if candles else None
    if not price:
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": "neutre", "pattern": None}
    as_of = _candle_dt(candles[-1])
    if confluence.is_news_blackout(as_of):
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": "neutre", "pattern": None}
    if not (TRADING_WINDOW_START_HOUR <= as_of.hour < TRADING_WINDOW_END_HOUR):
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": "neutre", "pattern": None}

    asian_range = compute_asian_range(candles, as_of)
    if asian_range is None:
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": "neutre", "pattern": None}

    crossing = _fresh_crossing(candles, asian_range)
    if crossing is None:
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": "neutre", "pattern": None}
    direction, breakout_price = crossing

    if not _has_volume_confirmation(candles):
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": "neutre", "pattern": None}

    range_height = asian_range["high"] - asian_range["low"]
    if direction == "achat":
        stop_loss = asian_range["high"] - confluence.CHARTPATTERN_STOP_BUFFER
        take_profit = breakout_price + range_height
        trend = "haussier"
    else:
        stop_loss = asian_range["low"] + confluence.CHARTPATTERN_STOP_BUFFER
        take_profit = breakout_price - range_height
        trend = "baissier"

    if not confluence.meets_minimum_risk_reward(price, stop_loss, take_profit, direction):
        return {"status": "neutre", "price": price, "entry": None, "stop_loss": None,
                "take_profit": None, "trend": trend, "pattern": None}

    return {"status": direction, "price": price, "entry": price, "stop_loss": stop_loss,
            "take_profit": take_profit, "trend": trend, "pattern": None}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gold_bot/test_session_breakout.py -v`
Expected: PASS (toutes)

- [ ] **Step 5: Run the full test suite**

Run: `pytest -q`
Expected: PASS (tous les tests du dépôt, sans régression -- ce module est entièrement nouveau et indépendant, aucun autre fichier ne l'importe)

- [ ] **Step 6: Commit**

```bash
git add gold_bot/session_breakout.py tests/gold_bot/test_session_breakout.py
git commit -m "feat(gold_bot): ajoute le moteur de signal cassure de session + volume (non branché sur la production)"
```

---

## Après ce plan (hors scope, fait par le contrôleur directement)

- Le plan de validation multi-années décrit dans la spec (section 4) : calibrage sur 2015-2020, validation sur 2021-2026, détail annuel, courbe de capital réelle, verdict de rejet/acceptation.
- Si validé : décision de brancher `session_breakout.compute_signal` sur `bot.decide_and_act` (aujourd'hui câblé en dur sur `confluence.compute_signal`), et reconsidérer à ce moment-là la taille de fenêtre demandée par `confluence.fetch_gold_candles` en production (actuellement dimensionnée pour l'ancien moteur, ~11 bougies minimum -- le nouveau moteur a besoin de largement plus pour couvrir une session asiatique complète, voir la note "risques/questions ouvertes" de la spec).
- Décision de retirer ou non `gold_bot/chart_patterns.py` et l'ancien `confluence.compute_signal` du dépôt.
