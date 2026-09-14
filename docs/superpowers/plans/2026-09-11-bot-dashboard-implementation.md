# Onglet "Bot" — tableau de bord du bot de trading or — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new "Bot" tab to the site showing the trading bot's real balance, open positions, and a price chart with entry/exit markers — all behind the existing bot-token authentication.

**Architecture:** `gold_bot/loop.py` caches candles/balance/positions to three local JSON files right after each of its own successful MetaApi/Twelve Data calls (one cache write per data source, independent of whether later calls in the same cycle fail). A new `GET /dashboard` route on `gold_bot/api.py` reads those three caches plus a tail of `decisions_log.jsonl` and returns them as one JSON body — `api.py` never imports `gold_bot.broker` and never calls MetaApi/Twelve Data itself. `docs/index.html` gets a new top-level "Bot" tab (same hash-routing pattern as Or/Indices/Portefeuille) that fetches `/dashboard` with the existing bot token and renders balance, positions, and a hand-drawn SVG price chart.

**Tech Stack:** Python 3.14 / FastAPI (backend, `gold_bot/`), vanilla JS + inline SVG (frontend, `docs/index.html`) — no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-11-bot-dashboard-design.md`

## Global Constraints

- `gold_bot/api.py` must never import `gold_bot.broker`, directly or indirectly, and must never call MetaApi or Twelve Data itself — `/dashboard` only reads local cache files written by `gold_bot/loop.py`. The existing test `tests/gold_bot/test_api.py::test_broker_module_is_never_referenced_in_api_source` (asserts the literal string `"broker"` never appears in `api.py`'s source) must stay green, unmodified, after this plan.
- Zero additional Twelve Data or MetaApi API calls anywhere in this feature. Candles/balance/positions for `/dashboard` come exclusively from caches `gold_bot/loop.py` already has the data for as part of its existing per-minute cycle.
- Balance and open-position numbers must never be written to any file that is committed to the repo or served as static public JSON (e.g. never touch `docs/real_portfolio_mt5.json` or add a sibling public file) — they only ever leave the VPS through the token-gated `/dashboard` route.
- `/dashboard` is strictly read-only: no new route may call `broker.place_market_order`/`close_position`, and this task must not touch `gold_bot/bot.py` or `gold_bot/loop.py`'s `execute_steps`/order-placement logic at all beyond the cache-writing addition in Task 1.
- Every cache write happens immediately after its own successful call, not at the end of the cycle — a later failure in the same cycle (e.g. the `504 Gateway Timeout` already observed in production on `get_symbol_specification`) must never prevent or roll back a cache write that already succeeded this cycle.
- Given this touches the same account-balance/position-reading data paths as the rest of the money-moving bot code (even though `/dashboard` itself cannot place orders), `gold_bot/api.py` and the position/balance data-shaping logic in Task 2 should get the same maximally-scrutinized per-task review rigor that `gold_bot/broker.py` and `gold_bot/confluence.py` got in Plan A — re-trace the "no route can place an order" and "no live MetaApi/Twelve Data call from api.py" invariants from scratch at review time, don't assume they transferred automatically from Plan A/B's own verification.
- No Node.js is available in this project's local dev environment, and `.github/workflows/js_tests.yml` only runs `docs/*.test.js` files, none of which cover `docs/index.html` itself — Task 3's frontend changes are verified manually in a browser, not by an automated suite.

---

## Task 1: Cache candles/balance/positions in `gold_bot/loop.py`

**Files:**
- Modify: `gold_bot/loop.py`
- Test: `tests/gold_bot/test_loop.py`

**Interfaces:**
- Consumes: `gold_bot.state.save_state(state: dict, path: str) -> None` (existing, atomic write via temp file + `os.replace`), `gold_bot.confluence.fetch_gold_candles`, `gold_bot.broker.get_account_balance`, `gold_bot.bot.reconcile_positions` (all existing, unchanged signatures).
- Produces: three new module-level path constants read by Task 2 — `loop.LATEST_CANDLES_PATH`, `loop.LATEST_BALANCE_PATH`, `loop.LATEST_POSITIONS_PATH` (each a `str`, same `os.path.join(os.path.dirname(os.path.abspath(__file__)), "<name>.json")` pattern as `loop.DECISIONS_LOG_PATH`). Cache file shapes: `{"candles": <list[dict]>, "fetched_at": "<ISO8601Z>"}`, `{"balance": <float>, "fetched_at": "<ISO8601Z>"}`, `{"positions": <list[dict]>, "fetched_at": "<ISO8601Z>"}` — `positions` is the raw list `broker.get_open_positions` returns (untranslated, e.g. `type: "POSITION_TYPE_SELL"`), not the achat/vente-translated form.

- [ ] **Step 1: Write the failing tests**

Add to `tests/gold_bot/test_loop.py` (append at the end of the file):

```python
def test_run_cycle_caches_candles_after_successful_fetch(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    fake_candles = [{"time": "2026-09-11 16:40:00", "close": 3651.5}]
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: fake_candles)
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 10000.0)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100})
    monkeypatch.setattr(loop.bot, "decide_and_act", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})

    loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker())

    cached = json.loads((tmp_path / "latest_candles.json").read_text(encoding="utf-8"))
    assert cached["candles"] == fake_candles
    assert "fetched_at" in cached


def test_run_cycle_caches_balance_after_successful_fetch(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [{"close": 2100}])
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 9140.10)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100})
    monkeypatch.setattr(loop.bot, "decide_and_act", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})

    loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker())

    cached = json.loads((tmp_path / "latest_balance.json").read_text(encoding="utf-8"))
    assert cached["balance"] == 9140.10
    assert "fetched_at" in cached


def test_run_cycle_caches_positions_after_successful_fetch(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    fake_positions = [{"symbol": "XAUUSD", "type": "POSITION_TYPE_SELL", "volume": 2.0,
                        "openPrice": 4316.28, "currentPrice": 4316.49, "profit": -36.18}]
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [{"close": 2100}])
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 10000.0)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: fake_positions)
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100})
    monkeypatch.setattr(loop.bot, "decide_and_act", lambda *a, **k: {"action": "aucune", "reason": "signal neutre"})

    loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker())

    cached = json.loads((tmp_path / "latest_positions.json").read_text(encoding="utf-8"))
    assert cached["positions"] == fake_positions
    assert "fetched_at" in cached


def test_run_cycle_leaves_earlier_caches_intact_when_a_later_call_fails(monkeypatch, tmp_path):
    """Le 504 déjà observé en production tombe sur get_symbol_specification,
    APRÈS candles/balance/positions dans run_cycle — ces trois caches
    doivent rester écrits même quand ce dernier appel échoue."""
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [{"close": 2100}])
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 10000.0)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(
        loop.broker, "get_symbol_specification",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("504 Server Error: Gateway Timeout")),
    )

    result = loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker())

    assert result["action"] == "erreur"
    assert (tmp_path / "latest_candles.json").exists()
    assert (tmp_path / "latest_balance.json").exists()
    assert (tmp_path / "latest_positions.json").exists()


def test_run_cycle_does_not_write_candles_cache_when_fetch_itself_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(loop, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(loop, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(
        loop.confluence, "fetch_gold_candles",
        lambda api_key: (_ for _ in ()).throw(RuntimeError("Twelve Data indisponible")),
    )

    loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker())

    assert not (tmp_path / "latest_candles.json").exists()
    assert not (tmp_path / "latest_balance.json").exists()
    assert not (tmp_path / "latest_positions.json").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gold_bot/test_loop.py -k "caches or leaves_earlier or does_not_write_candles" -v`
Expected: FAIL — `AttributeError: module 'gold_bot.loop' has no attribute 'LATEST_CANDLES_PATH'` (or similar for the other two constants).

- [ ] **Step 3: Implement the cache writes**

In `gold_bot/loop.py`, add the three new path constants right after the existing `CIRCUIT_BREAKER_STATE_PATH` (around line 26):

```python
LATEST_CANDLES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "latest_candles.json")
LATEST_BALANCE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "latest_balance.json")
LATEST_POSITIONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "latest_positions.json")
```

Add a small timestamp helper right after `_log_decision` (around line 75, after its closing `except` block):

```python
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
```

In `run_cycle`, modify the `try` block (currently lines 92-101) to cache each result immediately after its own successful call:

```python
    try:
        candles = confluence.fetch_gold_candles(twelve_data_api_key)
        state.save_state({"candles": candles, "fetched_at": _now_iso()}, LATEST_CANDLES_PATH)
        balance = broker.get_account_balance(token, account_id, region)
        state.save_state({"balance": balance, "fetched_at": _now_iso()}, LATEST_BALANCE_PATH)
        open_positions = bot.reconcile_positions(token, account_id, region)
        state.save_state({"positions": open_positions, "fetched_at": _now_iso()}, LATEST_POSITIONS_PATH)
        spec = broker.get_symbol_specification(token, account_id, symbol, region)
        contract_size = spec["contractSize"]
        decision = bot.decide_and_act(
            candles, contract_size=contract_size, balance=balance,
            open_positions=open_positions, circuit_breaker=circuit_breaker, symbol=symbol,
        )
    except Exception as e:
        decision = {"action": "erreur", "reason": f"Erreur pendant la décision : {e}"}
        _log_decision(decision, path=DECISIONS_LOG_PATH)
        print(f"Erreur pendant la décision : {e}")
        traceback.print_exc()
        return decision
```

(Only the additions of the three `state.save_state(...)` lines and the new constants/helper are new — nothing else in `run_cycle` changes.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gold_bot/test_loop.py -v`
Expected: PASS — all tests in the file, including the 5 new ones and every pre-existing test (this change must not alter any existing behavior).

- [ ] **Step 5: Run the full test suite**

Run: `pytest -q`
Expected: PASS — no regressions elsewhere (419 tests passed on `main` before this branch; this task adds 5).

- [ ] **Step 6: Commit**

```bash
git add gold_bot/loop.py tests/gold_bot/test_loop.py
git commit -m "feat(gold-bot): met en cache bougies/solde/positions à chaque cycle réussi

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: `GET /dashboard` route on `gold_bot/api.py`

**Files:**
- Modify: `gold_bot/api.py`
- Test: `tests/gold_bot/test_api.py`

**Interfaces:**
- Consumes: the three cache path constants and shapes Task 1 produces (`{"candles"/"balance"/"positions": ..., "fetched_at": "<ISO8601Z>"}`), read as **independently-defined local constants in `api.py`** (NOT imported from `gold_bot.loop` — see Global Constraints: `api.py` must not import anything that transitively pulls in `gold_bot.broker`, and the existing codebase convention, per `api.CIRCUIT_BREAKER_STATE_PATH` already duplicating `loop.CIRCUIT_BREAKER_STATE_PATH` by value rather than importing it, is to duplicate these small path constants and test their equality). Also reads `gold_bot/decisions_log.jsonl` (same format `gold_bot.notify.read_todays_decisions` already parses, but this route reads the last N lines regardless of date, not "today only").
- Produces: `GET /dashboard` — `401` on missing/invalid `X-Bot-Token` (via existing `_check_token`); `200` otherwise, with body `{"balance": float|null, "balance_fetched_at": str|null, "positions": list[dict], "positions_fetched_at": str|null, "candles": list[dict]|null, "candles_fetched_at": str|null, "recent_decisions": list[dict]}`. Each `positions` entry: `{"symbol": str|null, "direction": "achat"|"vente"|"inconnu", "volume": float|null, "open_price": float|null, "current_price": float|null, "profit": float|null, "stop_loss": float|null, "take_profit": float|null}`. Each `recent_decisions` entry: `{"timestamp": str, "type": "ouverture_simulee", "symbol": str, "direction": "achat"|"vente", "entry": float, "stop_loss": float, "take_profit": float}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/gold_bot/test_api.py`. First add `import json` at the top of the file (it currently only imports `pytest`, `TestClient`, `api`, `loop`, `state`):

```python
import json

import pytest
```

Then append at the end of the file:

```python
def _seed_dashboard_caches(monkeypatch, tmp_path, *, balance=None, positions=None, candles=None, decisions_lines=None):
    monkeypatch.setattr(api, "LATEST_BALANCE_PATH", str(tmp_path / "latest_balance.json"))
    monkeypatch.setattr(api, "LATEST_POSITIONS_PATH", str(tmp_path / "latest_positions.json"))
    monkeypatch.setattr(api, "LATEST_CANDLES_PATH", str(tmp_path / "latest_candles.json"))
    monkeypatch.setattr(api, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    if balance is not None:
        state.save_state(balance, api.LATEST_BALANCE_PATH)
    if positions is not None:
        state.save_state(positions, api.LATEST_POSITIONS_PATH)
    if candles is not None:
        state.save_state(candles, api.LATEST_CANDLES_PATH)
    if decisions_lines is not None:
        with open(api.DECISIONS_LOG_PATH, "w", encoding="utf-8") as fh:
            for entry in decisions_lines:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def test_dashboard_requires_valid_token(client):
    response = client.get("/dashboard")
    assert response.status_code == 401


def test_dashboard_rejects_wrong_token(client):
    response = client.get("/dashboard", headers={"X-Bot-Token": "wrong-token"})
    assert response.status_code == 401


def test_dashboard_returns_full_body_when_all_caches_present(client, monkeypatch, tmp_path):
    _seed_dashboard_caches(
        monkeypatch, tmp_path,
        balance={"balance": 9140.10, "fetched_at": "2026-09-11T16:40:05Z"},
        positions={"positions": [
            {"symbol": "XAUUSD", "type": "POSITION_TYPE_SELL", "volume": 2.0,
             "openPrice": 4316.28, "currentPrice": 4316.49, "profit": -36.18},
        ], "fetched_at": "2026-09-11T16:40:06Z"},
        candles={"candles": [{"time": "2026-09-11 16:40:00", "close": 3651.5}],
                 "fetched_at": "2026-09-11T16:40:04Z"},
        decisions_lines=[
            {"action": "simulation_dry_run", "timestamp": "2026-09-11T14:22:03Z",
             "steps": [{"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
                        "entry": 3648.2, "stop_loss": 3644.0, "take_profit": 3656.0}]},
            {"action": "aucune", "reason": "signal neutre", "timestamp": "2026-09-11T14:23:03Z"},
        ],
    )

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["balance"] == 9140.10
    assert body["balance_fetched_at"] == "2026-09-11T16:40:05Z"
    assert body["positions"] == [{
        "symbol": "XAUUSD", "direction": "vente", "volume": 2.0,
        "open_price": 4316.28, "current_price": 4316.49, "profit": -36.18,
        "stop_loss": None, "take_profit": None,
    }]
    assert body["candles"] == [{"time": "2026-09-11 16:40:00", "close": 3651.5}]
    assert body["candles_fetched_at"] == "2026-09-11T16:40:04Z"
    assert body["recent_decisions"] == [{
        "timestamp": "2026-09-11T14:22:03Z", "type": "ouverture_simulee", "symbol": "XAUUSD",
        "direction": "achat", "entry": 3648.2, "stop_loss": 3644.0, "take_profit": 3656.0,
    }]


def test_dashboard_translates_buy_position_type(client, monkeypatch, tmp_path):
    _seed_dashboard_caches(monkeypatch, tmp_path, positions={"positions": [
        {"symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "volume": 1.0,
         "openPrice": 3600.0, "currentPrice": 3610.0, "profit": 10.0},
    ], "fetched_at": "2026-09-11T16:40:06Z"})

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.json()["positions"][0]["direction"] == "achat"


def test_dashboard_balance_null_when_cache_missing_but_other_sources_intact(client, monkeypatch, tmp_path):
    _seed_dashboard_caches(
        monkeypatch, tmp_path,
        positions={"positions": [], "fetched_at": "2026-09-11T16:40:06Z"},
        candles={"candles": [{"time": "2026-09-11 16:40:00", "close": 3651.5}], "fetched_at": "2026-09-11T16:40:04Z"},
    )

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["balance"] is None
    assert body["balance_fetched_at"] is None
    assert body["positions"] == []
    assert body["candles"] == [{"time": "2026-09-11 16:40:00", "close": 3651.5}]


def test_dashboard_positions_empty_when_cache_missing_but_other_sources_intact(client, monkeypatch, tmp_path):
    _seed_dashboard_caches(
        monkeypatch, tmp_path,
        balance={"balance": 9140.10, "fetched_at": "2026-09-11T16:40:05Z"},
        candles={"candles": [{"time": "2026-09-11 16:40:00", "close": 3651.5}], "fetched_at": "2026-09-11T16:40:04Z"},
    )

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["positions"] == []
    assert body["balance"] == 9140.10
    assert body["candles"] == [{"time": "2026-09-11 16:40:00", "close": 3651.5}]


def test_dashboard_candles_null_when_cache_missing_but_other_sources_intact(client, monkeypatch, tmp_path):
    _seed_dashboard_caches(
        monkeypatch, tmp_path,
        balance={"balance": 9140.10, "fetched_at": "2026-09-11T16:40:05Z"},
        positions={"positions": [], "fetched_at": "2026-09-11T16:40:06Z"},
    )

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["candles"] is None
    assert body["candles_fetched_at"] is None
    assert body["balance"] == 9140.10


def test_dashboard_recent_decisions_empty_when_log_missing(client, monkeypatch, tmp_path):
    _seed_dashboard_caches(monkeypatch, tmp_path)

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 200
    assert response.json()["recent_decisions"] == []


def test_dashboard_excludes_neutral_and_error_decisions(client, monkeypatch, tmp_path):
    _seed_dashboard_caches(monkeypatch, tmp_path, decisions_lines=[
        {"action": "aucune", "reason": "signal neutre", "timestamp": "2026-09-11T14:20:00Z"},
        {"action": "erreur", "reason": "504 Gateway Timeout", "timestamp": "2026-09-11T14:21:00Z"},
        {"action": "ignore", "reason": "interrupteur d'urgence activé", "timestamp": "2026-09-11T14:22:00Z"},
    ])

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.json()["recent_decisions"] == []


def test_dashboard_excludes_closing_steps_from_markers(client, monkeypatch, tmp_path):
    _seed_dashboard_caches(monkeypatch, tmp_path, decisions_lines=[
        {"action": "exécuté", "timestamp": "2026-09-11T14:22:03Z", "steps": [
            {"type": "clôture_simulee", "position_id": "1", "symbol": "XAUUSD"},
            {"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "vente",
             "entry": 3648.2, "stop_loss": 3652.0, "take_profit": 3640.0},
        ]},
    ])

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    decisions = response.json()["recent_decisions"]
    assert len(decisions) == 1
    assert decisions[0]["type"] == "ouverture_simulee"


def test_broker_module_is_never_referenced_in_api_source():
    import inspect
    source = inspect.getsource(api)
    assert "broker" not in source
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gold_bot/test_api.py -k dashboard -v`
Expected: FAIL — `404 Not Found` (route doesn't exist yet) on every new test.

- [ ] **Step 3: Implement the route**

In `gold_bot/api.py`, replace the module's top-of-file comment block (lines 1-6):

```python
# gold_bot/api.py
# Point d'accès HTTPS du bot : deux routes de lecture (/status,
# /dashboard) et deux routes de mutation (/kill, /resume — seules
# routes qui changent un état, et seul kill_switch peut être modifié,
# jamais dry_run). Ce module n'importe jamais gold_bot.broker, même
# pour lire un solde ou des positions : /dashboard ne lit que des
# fichiers locaux mis en cache par gold_bot/loop.py (voir
# docs/superpowers/specs/2026-09-11-bot-dashboard-design.md). Aucune
# route ne peut jamais déclencher un ordre — ces actions n'existent
# tout simplement pas ici. Voir aussi
# docs/superpowers/specs/2026-09-10-bot-trading-or-design.md.
```

Add the new path constants right after `CIRCUIT_BREAKER_STATE_PATH` (currently line 15):

```python
DECISIONS_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decisions_log.jsonl")
LATEST_CANDLES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "latest_candles.json")
LATEST_BALANCE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "latest_balance.json")
LATEST_POSITIONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "latest_positions.json")
RECENT_DECISIONS_LIMIT = 50
```

Add helper functions right after `_check_token` (currently ending at line 39), before the `/status` route:

```python
def _position_direction(raw_type: str | None) -> str:
    if raw_type == "POSITION_TYPE_BUY":
        return "achat"
    if raw_type == "POSITION_TYPE_SELL":
        return "vente"
    return "inconnu"


def _read_cache(path: str) -> dict:
    """Lit un fichier de cache JSON écrit par gold_bot.loop — absent,
    illisible, ou JSON invalide compte comme cache vide, jamais
    d'exception (même contrat que gold_bot.state.load_state)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_recent_decisions(path: str, limit: int = RECENT_DECISIONS_LIMIT) -> list[dict]:
    """Dernières lignes de decisions_log.jsonl portant une ouverture de
    position (simulée ou réelle) — voir la spec pour pourquoi seul le
    step "ouverture_simulee" (jamais "clôture_simulee") sert de
    marqueur, et pourquoi ce nom de type reste le même en mode réel."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except Exception:
        return []
    decisions = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("action") not in ("simulation_dry_run", "exécuté"):
            continue
        for step in entry.get("steps", []):
            if step.get("type") != "ouverture_simulee":
                continue
            decisions.append({
                "timestamp": entry.get("timestamp"),
                "type": step.get("type"),
                "symbol": step.get("symbol"),
                "direction": step.get("direction"),
                "entry": step.get("entry"),
                "stop_loss": step.get("stop_loss"),
                "take_profit": step.get("take_profit"),
            })
    return decisions
```

Add the route itself right after `/status` (currently ending at line 50), before `/kill`:

```python
@app.get("/dashboard")
def dashboard(x_bot_token: str | None = Header(default=None)):
    _check_token(x_bot_token)

    balance_cache = _read_cache(LATEST_BALANCE_PATH)
    positions_cache = _read_cache(LATEST_POSITIONS_PATH)
    candles_cache = _read_cache(LATEST_CANDLES_PATH)

    positions = [
        {
            "symbol": p.get("symbol"),
            "direction": _position_direction(p.get("type")),
            "volume": p.get("volume"),
            "open_price": p.get("openPrice"),
            "current_price": p.get("currentPrice"),
            "profit": p.get("profit"),
            "stop_loss": p.get("stopLoss"),
            "take_profit": p.get("takeProfit"),
        }
        for p in positions_cache.get("positions", [])
    ]

    return {
        "balance": balance_cache.get("balance"),
        "balance_fetched_at": balance_cache.get("fetched_at"),
        "positions": positions,
        "positions_fetched_at": positions_cache.get("fetched_at"),
        "candles": candles_cache.get("candles"),
        "candles_fetched_at": candles_cache.get("fetched_at"),
        "recent_decisions": _read_recent_decisions(DECISIONS_LOG_PATH),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gold_bot/test_api.py -v`
Expected: PASS — all tests in the file, including every pre-existing one (`test_broker_module_is_never_referenced_in_api_source` must still pass unmodified — if it fails, the implementation imported `gold_bot.broker` somewhere and must be fixed to read only from the caches).

- [ ] **Step 5: Run the full test suite**

Run: `pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gold_bot/api.py tests/gold_bot/test_api.py
git commit -m "feat(gold-bot): route GET /dashboard (solde, positions, bougies, décisions récentes)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: "Bot" tab in `docs/index.html`

**Files:**
- Modify: `docs/index.html`

**Interfaces:**
- Consumes: `GET {BOT_API_BASE_URL}/dashboard` (Task 2's response shape, exact field names as above), existing `getBotToken()`, existing CSS custom properties `--gold`/`--rust`/`--muted`/`--bg`, existing helpers `formatPrice` (EUR) and `formatPriceUsd` (USD).
- Produces: a new `#bot` screen reachable via `#bot` in the URL hash and a new "Bot" entry in both nav menus, following the exact same pattern as the existing `#portefeuille` screen.

This task has many small mechanical edits across one large file — do every step in order, each is small and low-risk in isolation, but skipping one leaves the tab partially wired (e.g. present in the menu but not sliding in, or reachable by URL but not in the menu).

- [ ] **Step 1: Add `#bot` to every shared CSS selector list**

In `docs/index.html`, there are 5 places where `#or, #indices, #portefeuille` (or a subset with pseudo-classes) are listed together for the screen-slide mechanism. Add `, #bot` (or `#bot::-webkit-scrollbar` / `#bot.screen-in` / `#bot.no-anim` as appropriate) to each:

```css
  .home, #or, #indices, #portefeuille, #bot {
```
(was: `.home, #or, #indices, #portefeuille {`)

```css
  .home::-webkit-scrollbar, #or::-webkit-scrollbar, #indices::-webkit-scrollbar, #portefeuille::-webkit-scrollbar, #bot::-webkit-scrollbar { display: none; }
```
(was without the `#bot::-webkit-scrollbar` term)

```css
  #or, #indices, #portefeuille, #bot {
    z-index: 2; transform: translateX(100%);
    transition: transform 0.32s cubic-bezier(.22,1,.36,1);
  }
```
(was: `#or, #indices, #portefeuille {`)

```css
  #or.screen-in, #indices.screen-in, #portefeuille.screen-in, #bot.screen-in { transform: translateX(0); }
```
(was without the `#bot.screen-in` term)

```css
  #or.no-anim, #indices.no-anim, #portefeuille.no-anim, #bot.no-anim { transition: none; }
```
(was without the `#bot.no-anim` term)

- [ ] **Step 2: Add the "Bot" icon symbol**

Right after the `icon-portefeuille` symbol definition (in the `<svg>` block near the top of the body containing all `<symbol id="icon-...">` defs):

```html
  <symbol id="icon-bot" viewBox="0 0 24 24"><path d="M12 3v3 M6 9h12a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1z M9 13v2 M15 13v2 M4 12h2 M18 12h2"/></symbol>
```

- [ ] **Step 3: Add "Bot" to both nav menus**

In the `<nav class="top-bar">` block, right after the `data-route="portefeuille"` button:

```html
  <button class="nav-item" data-route="bot">
    <svg class="nav-icon"><use href="#icon-bot"/></svg>
    <span>Bot</span>
  </button>
```

In the `<nav class="drawer">` block, right after its own `data-route="portefeuille"` button (same markup, this is a second, separate copy for mobile):

```html
  <button class="nav-item" data-route="bot">
    <svg class="nav-icon"><use href="#icon-bot"/></svg>
    <span>Bot</span>
  </button>
```

- [ ] **Step 4: Add the home-screen module card**

Right after the `<a href="#portefeuille" class="module-card">` card:

```html
    <a href="#bot" class="module-card">
      <svg class="module-card-icon"><use href="#icon-bot"/></svg>
      <span class="module-card-name">Bot</span>
      <span class="module-card-desc">Solde, positions et cours du bot de trading or.</span>
    </a>
```

- [ ] **Step 5: Add the screen container**

Right after the closing `</div>` of the `#portefeuille` screen (before the `<script>` tag):

```html
<div id="bot" class="screen app" hidden>
  <header>
    <div class="eyebrow">Trading automatique</div>
    <h1>Bot</h1>
  </header>

  <main id="botContent">
    <div class="skeleton">
      <div class="skeleton-line"></div>
      <div class="skeleton-line"></div>
      <div class="skeleton-line"></div>
      <div class="skeleton-line"></div>
    </div>
  </main>
</div>
```

- [ ] **Step 6: Wire the route**

Add `'bot'` to `SLIDING_PANES`:

```js
const SLIDING_PANES = ['or', 'indices', 'portefeuille', 'bot'];
```
(was: `const SLIDING_PANES = ['or', 'indices', 'portefeuille'];`)

Add a case to `parseRoute()`, right after the `portefeuille` line:

```js
  if (hash === 'bot') return { screen: 'bot' };
```

Add a dispatch to `renderRoute()`, right after the `portefeuille` block:

```js
  if (route.screen === 'bot') {
    loadBotScreen();
  }
```

- [ ] **Step 7: Write the chart, rendering, and fetch functions**

Add these functions right after `wireBotControls()` (which ends with its closing `}` a few lines after `botStatusHtml`):

```js
function buildBotChartSvg(candles, recentDecisions) {
  if (!candles || candles.length < 2) {
    return '<div class="empty">Graphique indisponible pour l\'instant.</div>';
  }
  const width = 600, height = 220, padding = 24;
  const closes = candles.map(c => c.close).filter(v => v != null);
  const decisionPrices = (recentDecisions || []).map(d => d.entry).filter(v => v != null);
  const allPrices = closes.concat(decisionPrices);
  const min = Math.min(...allPrices);
  const max = Math.max(...allPrices);
  const range = (max - min) || 1;

  const xAt = i => padding + (i / (candles.length - 1)) * (width - 2 * padding);
  const yAt = price => height - padding - ((price - min) / range) * (height - 2 * padding);

  const points = candles.map((c, i) => `${xAt(i)},${yAt(c.close)}`).join(' ');

  const candleTimes = candles.map(c => new Date(c.time.replace(' ', 'T') + 'Z').getTime());
  const markers = (recentDecisions || [])
    .filter(d => d.entry != null && d.timestamp)
    .map(d => {
      const t = new Date(d.timestamp).getTime();
      let closestIdx = 0, closestDiff = Infinity;
      candleTimes.forEach((ct, i) => {
        const diff = Math.abs(ct - t);
        if (diff < closestDiff) { closestDiff = diff; closestIdx = i; }
      });
      const color = d.direction === 'achat' ? 'var(--gold)' : 'var(--rust)';
      return `<circle cx="${xAt(closestIdx)}" cy="${yAt(d.entry)}" r="4" fill="${color}" stroke="var(--bg)" stroke-width="1.5"/>`;
    })
    .join('');

  return `
    <svg viewBox="0 0 ${width} ${height}" style="width:100%;height:auto;display:block;" role="img" aria-label="Cours XAUUSD avec points d'entrée/sortie du bot">
      <polyline points="${points}" fill="none" stroke="var(--muted)" stroke-width="1.5"/>
      ${markers}
    </svg>`;
}

function botBalanceHtml(data) {
  if (data.balance == null) {
    return `<div class="empty">Solde indisponible pour l'instant.</div>`;
  }
  return `
    <div class="portfolio-position-row">
      <div class="portfolio-position-main">
        <span class="company-name">${formatPrice(data.balance)}</span>
        <span class="hero-sub">Solde disponible</span>
      </div>
    </div>`;
}

function botPositionsHtml(data) {
  if (!data.positions || data.positions.length === 0) {
    return `<div class="empty">Aucune position ouverte.</div>`;
  }
  return data.positions.map(p => {
    const color = p.direction === 'achat' ? 'var(--gold)' : 'var(--rust)';
    const profitColor = (p.profit ?? 0) >= 0 ? 'var(--gold)' : 'var(--rust)';
    const profitSign = (p.profit ?? 0) >= 0 ? '+' : '';
    return `
    <div class="portfolio-position-row">
      <div class="portfolio-position-main">
        <span class="company-name">${p.symbol}</span>
        <span class="hero-sub" style="color:${color}">${p.direction === 'achat' ? 'Achat' : 'Vente'} · ${p.volume} lot(s)</span>
      </div>
      <div class="portfolio-position-pnl">
        <span class="price-value">${formatPriceUsd(p.current_price)}</span>
        <span style="color:${profitColor}">${profitSign}${formatPrice(p.profit)}</span>
      </div>
    </div>`;
  }).join('');
}

function botChartHtml(data) {
  if (!data.candles || data.candles.length < 2) {
    return `<div class="empty">Graphique indisponible pour l'instant.</div>`;
  }
  return buildBotChartSvg(data.candles, data.recent_decisions);
}

function renderBotDashboard(data) {
  document.getElementById('botContent').innerHTML = `
    <h2>Solde</h2>
    ${botBalanceHtml(data)}
    <h2>Positions ouvertes</h2>
    ${botPositionsHtml(data)}
    <h2>Cours &amp; décisions récentes</h2>
    ${botChartHtml(data)}
    <button class="toggle-btn" type="button" id="botRefreshBtn">Actualiser</button>
  `;
  wireBotDashboardRefresh();
}

async function fetchBotDashboard() {
  const content = document.getElementById('botContent');
  const token = getBotToken();
  if (!token) {
    content.innerHTML = `<div class="empty">Jeton requis pour afficher le tableau de bord du bot.</div>`;
    return;
  }
  try {
    const res = await fetch(`${BOT_API_BASE_URL}/dashboard`, { headers: { 'X-Bot-Token': token } });
    if (!res.ok) {
      if (res.status === 401) localStorage.removeItem('goldBotToken');
      throw new Error('échec de la requête (' + res.status + ')');
    }
    const data = await res.json();
    renderBotDashboard(data);
  } catch (e) {
    content.innerHTML = `<div class="empty">Tableau de bord indisponible pour l'instant.<br>${e.message}</div>`;
  }
}

function wireBotDashboardRefresh() {
  const btn = document.getElementById('botRefreshBtn');
  if (!btn) return;
  btn.addEventListener('click', () => fetchBotDashboard());
}

function loadBotScreen() {
  fetchBotDashboard();
}
```

- [ ] **Step 8: Manual verification in a browser**

Node isn't available locally and no automated suite covers `docs/index.html` — verify by hand:

1. Serve the site locally (e.g. `python3 -m http.server` from the `docs/` directory) and open it in a browser.
2. Click the "Bot" card on the home screen (or a "Bot" nav item) — the screen should slide in like Or/Indices/Portefeuille do, with the skeleton loading state visible briefly.
3. Enter the real `BOT_API_TOKEN` when prompted (same token already used for the existing Arrêter/Relancer button — check `deploy/README.md`'s `.env` template if it needs to be looked up again) — confirm balance, open positions (or "Aucune position ouverte"), and a chart (or "Graphique indisponible" if `goldbot.fr` hasn't completed a cycle since deploy) render without a blank/broken page.
4. Click "Actualiser" — confirm it re-fetches without a full page reload.
5. Test the invalid-token path: in the browser devtools console, run `localStorage.setItem('goldBotToken', 'wrong')`, reload, navigate to Bot — confirm it shows the "indisponible" error state and that a second reload re-prompts for the token (proving the `401` clears the stored one).
6. Resize to a narrow (phone-width) viewport and confirm the drawer menu also shows and correctly navigates to "Bot".
7. Toggle light/dark theme and confirm the chart line/markers stay legible in both (they use `var(--muted)`/`var(--gold)`/`var(--rust)`/`var(--bg)`, which are already theme-aware everywhere else on the site).

- [ ] **Step 9: Commit**

```bash
git add docs/index.html
git commit -m "feat(portfolio): onglet Bot — solde, positions et graphique entrée/sortie

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
