# Bot Or — plafond de trades/jour + vérification des cotes avant trade — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an automatic daily trade-count cap (alongside the existing daily loss circuit breaker) and a live pre-trade historical-odds check to the gold bot's VWAP-reversion strategy, without altering the strategy's own entry/exit logic.

**Architecture:** Extend the existing `gold_bot.risk.CircuitBreaker` class with trade-count tracking (same persistence/reset mechanism it already has for the loss-based trip). Add a new pure module `gold_bot/odds_check.py` that replays real historical 5-minute candles through the exact same entry-detection and trailing-stop-exit mechanics as `vwap_reversion`/`bot.decide_and_act_vwap`, to compute a verdict (`favorable`/`unfavorable`/`insufficient_history`). `gold_bot.bot.decide_and_act_vwap` stays network-free (per its module's existing guarantee) — it receives already-fetched historical candles as a plain parameter and calls the pure `odds_check` function itself; `gold_bot.loop.run_cycle` is the only place that fetches those candles, and it caches them (24h TTL) so the network cost stays negligible regardless of how often a signal fires.

**Tech Stack:** Python, pytest (TDD), existing `gold_bot/` package conventions (MetaApi via `gold_bot.broker`, local JSON state/cache files).

**Spec:** `docs/superpowers/specs/2026-10-22-gold-bot-risk-gate-design.md`

## Global Constraints

- `gold_bot.bot.decide_and_act_vwap` must never call the network or `gold_bot.broker` directly — this is an existing, load-bearing guarantee (see `gold_bot/loop.py` module docstring: "gold_bot.bot.decide_and_act() elle-même n'appelle jamais le broker"). All data it needs must be passed in as plain parameters.
- Every new rejection path must return `{"action": "aucune", "reason": "<explicit reason>"}`, matching the existing convention throughout `decide_and_act_vwap`.
- `CircuitBreaker`'s existing public API and behavior (threshold_pct mutation pattern, persistence format, `_tripped_today` latch semantics) must not change for existing callers — `max_trades_per_day` defaults to `None` (no cap) everywhere it's optional, so every existing test and call site keeps working unmodified unless explicitly updated by this plan.
- TDD throughout: write the failing test first, verify it fails, implement, verify it passes, commit. Mirror the exact fixture/mocking conventions already used in `tests/gold_bot/test_risk.py`, `tests/gold_bot/test_bot.py`, `tests/gold_bot/test_loop.py` (shown per-task below) rather than inventing new ones.
- Run the full suite (`python -m pytest tests/ -q`) before each commit that touches shared modules (`risk.py`, `bot.py`, `loop.py`) — these are imported by many other tests.

---

### Task 1: Add a `symbol` parameter to `fetch_gold_candles_range`

**Files:**
- Modify: `gold_bot/backtest.py:35-84`
- Test: `tests/gold_bot/test_backtest.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `fetch_gold_candles_range(token, account_id, start, end, region=..., symbol="XAUUSD") -> list[dict]` — Task 7 (loop.py wiring) calls this with `symbol=symbol` for both XAUUSD and XAGUSD cycles.

Today this function hardcodes `"XAUUSD"` at the `broker.get_historical_candles` call site (line 58), even though the bot trades both XAUUSD and XAGUSD (see `gold_bot/loop.py:47`, `SYMBOLS = ["XAUUSD", "XAGUSD"]`). This task makes it symbol-aware so Task 7 can fetch the correct instrument's history.

- [ ] **Step 1: Write the failing test**

Add to `tests/gold_bot/test_backtest.py` (check the existing file's top for how `broker.get_historical_candles` is already monkeypatched by other tests in this file, and mirror that exact pattern — the fixture below assumes `monkeypatch.setattr(backtest.broker, "get_historical_candles", fake_fn)` works, matching the module's `import gold_bot.broker as broker` at the top of `backtest.py`):

```python
def test_fetch_gold_candles_range_uses_the_requested_symbol(monkeypatch):
    captured_symbols = []

    def fake_get_historical_candles(token, account_id, symbol, timeframe, start_time, limit, region):
        captured_symbols.append(symbol)
        return []

    monkeypatch.setattr(backtest.broker, "get_historical_candles", fake_get_historical_candles)
    backtest.fetch_gold_candles_range(
        "token", "account", datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 2, tzinfo=timezone.utc), symbol="XAGUSD",
    )
    assert captured_symbols == ["XAGUSD"]


def test_fetch_gold_candles_range_defaults_to_xauusd(monkeypatch):
    captured_symbols = []

    def fake_get_historical_candles(token, account_id, symbol, timeframe, start_time, limit, region):
        captured_symbols.append(symbol)
        return []

    monkeypatch.setattr(backtest.broker, "get_historical_candles", fake_get_historical_candles)
    backtest.fetch_gold_candles_range(
        "token", "account", datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    assert captured_symbols == ["XAUUSD"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_backtest.py -k fetch_gold_candles_range_uses_the_requested_symbol -v`
Expected: FAIL with `TypeError: fetch_gold_candles_range() got an unexpected keyword argument 'symbol'`

- [ ] **Step 3: Add the `symbol` parameter**

In `gold_bot/backtest.py`, change the signature at line 35-36:

```python
def fetch_gold_candles_range(token: str, account_id: str, start: datetime, end: datetime,
                              region: str = broker.DEFAULT_MT5_REGION, symbol: str = "XAUUSD") -> list[dict]:
```

And change line 58 from:

```python
        raw = broker.get_historical_candles(token, account_id, "XAUUSD", "5m",
                                             start_time=cursor, limit=MAX_OUTPUT_SIZE, region=region)
```

to:

```python
        raw = broker.get_historical_candles(token, account_id, symbol, "5m",
                                             start_time=cursor, limit=MAX_OUTPUT_SIZE, region=region)
```

No other line in the function references the symbol, so this is the only change needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_backtest.py -v`
Expected: PASS (all tests, including the two new ones and every pre-existing test in this file, which all use the default `symbol="XAUUSD"` and must be unaffected).

- [ ] **Step 5: Commit**

```bash
git add gold_bot/backtest.py tests/gold_bot/test_backtest.py
git commit -m "feat(gold-bot): paramètre symbol pour fetch_gold_candles_range

Le bot trade XAUUSD et XAGUSD (gold_bot/loop.py::SYMBOLS) mais cette
fonction était câblée en dur sur XAUUSD -- bloquait toute récupération
d'historique XAGUSD, nécessaire pour la vérification des cotes (Task 7).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Add `max_trades_per_day` to `RISK_PROFILE_PARAMS`

**Files:**
- Modify: `gold_bot/risk.py:10-16`
- Test: `tests/gold_bot/test_risk.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `RISK_PROFILE_PARAMS[profile]["max_trades_per_day"]` (int) — consumed by Task 4 (`loop.py` reads `profile_params["max_trades_per_day"]`).

- [ ] **Step 1: Write the failing test**

Find the existing test for `RISK_PROFILE_PARAMS`/`risk_profile_params` in `tests/gold_bot/test_risk.py` (search for `risk_profile_params` or `RISK_PROFILE_PARAMS`) and add, in the same style:

```python
def test_risk_profile_params_includes_max_trades_per_day_for_every_profile():
    for profile in (1, 2, 3, 4, 5):
        params = risk.risk_profile_params(profile)
        assert isinstance(params["max_trades_per_day"], int)
        assert params["max_trades_per_day"] > 0


def test_risk_profile_params_max_trades_per_day_increases_with_profile():
    values = [risk.risk_profile_params(p)["max_trades_per_day"] for p in (1, 2, 3, 4, 5)]
    assert values == sorted(values)
    assert values[0] < values[-1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_risk.py -k max_trades_per_day -v`
Expected: FAIL with `KeyError: 'max_trades_per_day'`

- [ ] **Step 3: Add the values**

In `gold_bot/risk.py`, replace lines 10-16:

```python
RISK_PROFILE_PARAMS: dict[int, dict[str, float]] = {
    1: {"risk_pct": 0.02, "threshold_pct": 0.05, "max_trades_per_day": 2},
    2: {"risk_pct": 0.035, "threshold_pct": 0.075, "max_trades_per_day": 3},
    3: {"risk_pct": 0.05, "threshold_pct": 0.10, "max_trades_per_day": 4},
    4: {"risk_pct": 0.075, "threshold_pct": 0.135, "max_trades_per_day": 6},
    5: {"risk_pct": 0.10, "threshold_pct": 0.175, "max_trades_per_day": 8},
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_risk.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add gold_bot/risk.py tests/gold_bot/test_risk.py
git commit -m "feat(gold-bot): ajoute max_trades_per_day à chaque profil de risque

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `CircuitBreaker` — trade-count tracking and cap

**Files:**
- Modify: `gold_bot/risk.py:104-203` (the `CircuitBreaker` class)
- Test: `tests/gold_bot/test_risk.py`

**Interfaces:**
- Consumes: nothing new from earlier tasks.
- Produces:
  - `CircuitBreaker(..., max_trades_per_day: int | None = None)` — new constructor parameter.
  - `CircuitBreaker.max_trades_per_day` — public mutable attribute (same pattern as the existing `threshold_pct`), consumed by Task 4 (`loop.py` sets it every cycle from `profile_params["max_trades_per_day"]`).
  - `CircuitBreaker.record_trade_opened() -> None` — consumed by Task 4 (`loop.py` calls it after a confirmed real order).
  - `CircuitBreaker.can_open_position(current_balance: float) -> bool` — unchanged signature, now also returns `False` when the daily trade count is reached.

This is the core of the "max trades/day" feature. Follow the exact test style already in `tests/gold_bot/test_risk.py` (read lines 91-250 again if needed — `now_fn=lambda: datetime(...)`, `clock = {"now": ...}` for multi-step time travel, `tmp_path` for persistence tests).

- [ ] **Step 1: Write the failing tests**

Add to `tests/gold_bot/test_risk.py`:

```python
def test_circuit_breaker_allows_when_under_trade_count_limit():
    cb = risk.CircuitBreaker(threshold_pct=0.10, max_trades_per_day=2,
                              now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)
    cb.record_trade_opened()
    assert cb.can_open_position(10000) is True  # 1 trade ouvert, plafond à 2


def test_circuit_breaker_blocks_when_trade_count_limit_reached():
    cb = risk.CircuitBreaker(threshold_pct=0.10, max_trades_per_day=2,
                              now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)
    cb.record_trade_opened()
    cb.record_trade_opened()
    assert cb.can_open_position(10000) is False  # 2 trades ouverts, plafond atteint


def test_circuit_breaker_with_no_max_trades_per_day_never_blocks_on_count():
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)
    for _ in range(50):
        cb.record_trade_opened()
    assert cb.can_open_position(10000) is True  # max_trades_per_day=None (défaut) -> pas de plafond


def test_circuit_breaker_trade_count_resets_on_new_day():
    clock = {"now": datetime(2026, 9, 10, 23, 0, tzinfo=timezone.utc)}
    cb = risk.CircuitBreaker(threshold_pct=0.10, max_trades_per_day=2, now_fn=lambda: clock["now"])
    cb.check(10000)
    cb.record_trade_opened()
    cb.record_trade_opened()
    assert cb.can_open_position(10000) is False
    clock["now"] = datetime(2026, 9, 11, 1, 0, tzinfo=timezone.utc)  # jour suivant UTC
    assert cb.can_open_position(10000) is True  # compteur remis à zéro


def test_circuit_breaker_trade_count_persists_across_instances(tmp_path):
    path = str(tmp_path / "state.json")
    cb1 = risk.CircuitBreaker(threshold_pct=0.10, max_trades_per_day=2,
                               now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc), persist_path=path)
    cb1.check(10000)
    cb1.record_trade_opened()
    cb1.record_trade_opened()
    # Nouvelle instance (simule un redémarrage) le même jour UTC : doit
    # retrouver le compteur persisté, pas repartir de zéro.
    cb2 = risk.CircuitBreaker(threshold_pct=0.10, max_trades_per_day=2,
                               now_fn=lambda: datetime(2026, 9, 10, 18, tzinfo=timezone.utc), persist_path=path)
    assert cb2.can_open_position(10000) is False


def test_circuit_breaker_trade_count_trip_stays_tripped_like_loss_trip():
    # Même sémantique que test_circuit_breaker_stays_tripped_after_threshold_raised_mid_day :
    # un déclenchement par nombre de trades ne doit pas se "dé-déclencher"
    # silencieusement si max_trades_per_day est relevé le même jour.
    cb = risk.CircuitBreaker(threshold_pct=0.10, max_trades_per_day=2,
                              now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)
    cb.record_trade_opened()
    cb.record_trade_opened()
    assert cb.can_open_position(10000) is False
    cb.max_trades_per_day = 10  # relèvement en cours de journée
    assert cb.can_open_position(10000) is False  # reste bloqué


def test_circuit_breaker_trade_count_trip_does_not_affect_loss_based_check():
    # Les deux motifs de déclenchement sont indépendants à l'évaluation,
    # mais partagent le même flag _tripped_today une fois déclenchés
    # (comportement voulu, cohérent avec "reset automatique le lendemain"
    # pour les deux). Ici on vérifie juste qu'un compte largement sous le
    # seuil de perte reste bloqué uniquement à cause du nombre de trades.
    cb = risk.CircuitBreaker(threshold_pct=0.50, max_trades_per_day=1,
                              now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)
    cb.record_trade_opened()
    assert cb.can_open_position(9900) is False  # -1%, bien sous le seuil de perte (50%) -> bloqué par le compteur
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_risk.py -k "trade_count or trade_count_limit" -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'max_trades_per_day'`

- [ ] **Step 3: Implement**

In `gold_bot/risk.py`, modify the `CircuitBreaker` class (lines 104-203):

```python
    def __init__(self, threshold_pct: float = 0.10, now_fn=None, persist_path=None,
                 max_trades_per_day: int | None = None):
        if not 0 < threshold_pct <= 1:
            raise ValueError("threshold_pct doit être compris entre 0 (exclu) et 1 (inclus)")
        self.threshold_pct = threshold_pct
        self.max_trades_per_day = max_trades_per_day
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._persist_path = persist_path
        self._day = None
        self._starting_balance = None
        self._tripped_today = False
        self._trades_opened_today = 0
        if persist_path is not None:
            saved = state.load_state(persist_path)
            saved_day = saved.get("circuit_breaker_day")
            saved_balance = saved.get("circuit_breaker_starting_balance")
            if saved_day is not None and saved_balance is not None:
                try:
                    self._day = date.fromisoformat(saved_day)
                    self._starting_balance = float(saved_balance)
                    self._tripped_today = bool(saved.get("circuit_breaker_tripped_today", False))
                    self._trades_opened_today = int(saved.get("circuit_breaker_trades_opened_today", 0))
                except (ValueError, TypeError):
                    self._day = None
                    self._starting_balance = None
                    self._tripped_today = False
                    self._trades_opened_today = 0
```

(Only the new `max_trades_per_day` parameter/attribute, the new `self._trades_opened_today = 0` default, and the new `saved.get("circuit_breaker_trades_opened_today", 0)` restoration line are additions — everything else in `__init__` is unchanged, shown here for context so the diff is unambiguous.)

```python
    def check(self, current_balance: float) -> None:
        today = self._now_fn().date()
        needs_new_baseline = self._day != today or (
            self._starting_balance is not None and self._starting_balance <= 0 and current_balance > 0
        )
        if needs_new_baseline:
            self._day = today
            self._starting_balance = current_balance
            self._tripped_today = False
            self._trades_opened_today = 0
            self._persist()
```

(Same method, one line added: `self._trades_opened_today = 0` alongside the existing `self._tripped_today = False`.)

```python
    def _persist(self) -> None:
        if self._persist_path is None:
            return
        state.save_state(
            {
                "circuit_breaker_day": self._day.isoformat(),
                "circuit_breaker_starting_balance": self._starting_balance,
                "circuit_breaker_tripped_today": self._tripped_today,
                "circuit_breaker_trades_opened_today": self._trades_opened_today,
            },
            self._persist_path,
        )

    def record_trade_opened(self) -> None:
        """À appeler une fois par trade réellement ouvert (une seule fois
        même si l'ouverture se scinde en plusieurs ordres broker -- voir
        gold_bot.loop.run_cycle). Ne vérifie ni ne déclenche le plafond
        elle-même : c'est can_open_position() qui lit le compteur."""
        self._trades_opened_today += 1
        self._persist()

    def can_open_position(self, current_balance: float) -> bool:
        self.check(current_balance)
        if self._tripped_today:
            return False
        if self.max_trades_per_day is not None and self._trades_opened_today >= self.max_trades_per_day:
            self._tripped_today = True
            self._persist()
            return False
        loss = self._starting_balance - current_balance
        if loss < self._starting_balance * self.threshold_pct:
            return True
        self._tripped_today = True
        self._persist()
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_risk.py -v`
Expected: PASS (all tests in the file, including every pre-existing `CircuitBreaker` test — none of them pass `max_trades_per_day`, so they all exercise the `None`/no-cap default path and must be unaffected).

- [ ] **Step 5: Commit**

```bash
git add gold_bot/risk.py tests/gold_bot/test_risk.py
git commit -m "feat(gold-bot): plafond de trades/jour dans CircuitBreaker

Extension du coupe-circuit existant (perte journalière) plutôt qu'un
nouveau mécanisme -- même persistance, même reset automatique au
changement de jour UTC, même verrou _tripped_today (un déclenchement
par nombre de trades ne se dé-déclenche pas silencieusement si le
profil de risque est relevé en cours de journée).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Wire the trade-count cap into `loop.py`

**Files:**
- Modify: `gold_bot/loop.py:211-212` (profile mutation) and `gold_bot/loop.py:312-316` (post-execution)
- Test: `tests/gold_bot/test_loop.py`

**Interfaces:**
- Consumes: `CircuitBreaker.max_trades_per_day` (attribute, Task 3), `CircuitBreaker.record_trade_opened()` (Task 3), `risk.risk_profile_params(profile)["max_trades_per_day"]` (Task 2).
- Produces: nothing new for later tasks — this is the production wiring.

- [ ] **Step 1: Write the failing test**

First, read `tests/gold_bot/test_loop.py` in full to find how `run_cycle` is already tested (look for an existing test that exercises a successful real execution — search for `"exécuté"` or `execute_steps` mocking). Mirror that exact fixture setup (fake `broker`/`confluence` functions, fake `state.load_state` returning `dry_run: False`). Add:

```python
def test_run_cycle_records_trade_opened_on_successful_order(monkeypatch, tmp_path):
    # Reprend le même montage que le test existant qui vérifie une
    # exécution réelle réussie (dry_run=False, kill_switch=False, un
    # step "ouverture_simulee" qui aboutit sans erreur) -- adapter les
    # noms de fixtures/mocks exacts à ce qui existe déjà plus haut dans
    # ce fichier plutôt que de les redéfinir ici.
    cb = risk.CircuitBreaker(persist_path=str(tmp_path / "cb_state.json"))
    assert cb._trades_opened_today == 0

    # ... (monter les mêmes mocks que le test d'exécution réussie existant,
    # avec decide_and_act_vwap renvoyant un step "ouverture_simulee" et
    # execute_steps renvoyant {"error": None} pour ce step)

    loop.run_cycle("token", "account", cb, now=datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc))
    assert cb._trades_opened_today == 1


def test_run_cycle_does_not_record_trade_on_modification_only(monkeypatch, tmp_path):
    # Même montage, mais decide_and_act_vwap renvoie un step
    # "modification_simulee" (mise à jour du stop suiveur sur une
    # position déjà ouverte) -- ne doit jamais compter comme un nouveau trade.
    cb = risk.CircuitBreaker(persist_path=str(tmp_path / "cb_state.json"))

    # ... (mêmes mocks, step "modification_simulee" exécuté avec succès)

    loop.run_cycle("token", "account", cb, now=datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc))
    assert cb._trades_opened_today == 0


def test_run_cycle_does_not_record_trade_when_execution_fails(monkeypatch, tmp_path):
    cb = risk.CircuitBreaker(persist_path=str(tmp_path / "cb_state.json"))

    # ... (mêmes mocks, step "ouverture_simulee" dont l'exécution renvoie
    # {"error": "<message>"} au lieu de None)

    loop.run_cycle("token", "account", cb, now=datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc))
    assert cb._trades_opened_today == 0


def test_run_cycle_passes_max_trades_per_day_from_risk_profile(monkeypatch, tmp_path):
    cb = risk.CircuitBreaker(persist_path=str(tmp_path / "cb_state.json"))

    # ... (mêmes mocks de base ; monkeypatcher state.load_state pour
    # renvoyer risk_profile=5)

    loop.run_cycle("token", "account", cb, now=datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc))
    assert cb.max_trades_per_day == risk.RISK_PROFILE_PARAMS[5]["max_trades_per_day"]
```

**Note for the implementer**: the exact mock setup (which functions to monkeypatch on `loop.confluence`/`loop.broker`/`loop.bot`/`loop.state`) must mirror whatever the existing successful-execution test in `tests/gold_bot/test_loop.py` already does — read that test fully first and copy its pattern rather than inventing a new one, so this task stays consistent with the rest of the file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_loop.py -k "records_trade_opened or trades_per_day" -v`
Expected: FAIL (the new assertions on `cb._trades_opened_today`/`cb.max_trades_per_day` don't hold yet, since nothing in `run_cycle` sets them).

- [ ] **Step 3: Implement**

In `gold_bot/loop.py`, modify line 211-212:

```python
    profile_params = risk.risk_profile_params(current_state.get("risk_profile"))
    circuit_breaker.threshold_pct = profile_params["threshold_pct"]
    circuit_breaker.max_trades_per_day = profile_params["max_trades_per_day"]
```

Then modify the block at lines 312-316 (right after `execute_steps` returns):

```python
    results = execute_steps(token, account_id, steps, region)
    had_error = any(r["error"] for r in results) or not results
    if not had_error and any(s["type"] == "ouverture_simulee" for s in steps):
        circuit_breaker.record_trade_opened()
    result = {"action": "erreur" if had_error else "exécuté", "steps": steps, "results": results, "symbol": symbol}
    _log_decision(result, path=DECISIONS_LOG_PATH)
    return result
```

(Only the new `if not had_error and ...: circuit_breaker.record_trade_opened()` line is added — the surrounding lines are shown for exact placement.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_loop.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: PASS (`risk.py`/`loop.py` are imported broadly — confirm nothing else broke).

- [ ] **Step 6: Commit**

```bash
git add gold_bot/loop.py tests/gold_bot/test_loop.py
git commit -m "feat(gold-bot): branche le plafond de trades/jour dans la boucle live

max_trades_per_day est relu à chaque cycle depuis le profil de risque
actif (même schéma que threshold_pct), et un trade n'est compté
qu'après une exécution réellement réussie -- jamais sur une simple
modification de stop suiveur, jamais sur un échec d'exécution.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: `odds_check.py` — historical entry-point detection

**Files:**
- Create: `gold_bot/odds_check.py`
- Test: `tests/gold_bot/test_odds_check.py`

**Interfaces:**
- Consumes: `vwap_reversion.resample_15min`, `vwap_reversion.compute_indicators`, `vwap_reversion.get_symbol_params`, `vwap_reversion.MIN_BARS_INTO_SESSION`, `vwap_reversion.SESSION_END_HOUR_UTC` (all existing, read-only).
- Produces: `_find_historical_entries(candles_5min: list[dict], direction: str, symbol: str = "XAUUSD") -> list[dict]` — each returned dict is `{"entry_time": str, "entry_price": float, "stop_loss": float}`, in chronological order. Consumed by Task 6 (`_simulate_exit`, `historical_odds_for_setup`).

This task only detects where, in history, the same entry condition `decide_and_act_vwap` uses would have fired — it does not simulate any exit yet (Task 6).

- [ ] **Step 1: Write the failing test**

Create `tests/gold_bot/test_odds_check.py`:

```python
from datetime import datetime, timedelta, timezone

import gold_bot.odds_check as odds_check
import gold_bot.vwap_reversion as vwap_reversion


def _make_5min_candles(n, start, price_fn):
    """Génère n bougies 5min consécutives à partir de `start` (UTC),
    close=price_fn(i), open=high=low=close (bougies plates, suffisant
    pour tester la détection d'entrée qui ne regarde que close/vwap/ema)."""
    out = []
    for i in range(n):
        t = start + timedelta(minutes=5 * i)
        price = price_fn(i)
        out.append({
            "time": t.strftime("%Y-%m-%d %H:%M:%S"), "open": price, "high": price,
            "low": price, "close": price, "tick_volume": 10, "spread": 2,
        })
    return out


def test_find_historical_entries_returns_empty_list_on_short_history():
    candles = _make_5min_candles(10, datetime(2026, 1, 1, tzinfo=timezone.utc), lambda i: 2000.0)
    assert odds_check._find_historical_entries(candles, "achat") == []


def test_find_historical_entries_finds_a_known_achat_signal(monkeypatch):
    # Même recette que test_decide_and_act_vwap_opens_achat_with_partial_and_runner_legs
    # (tests/gold_bot/test_bot.py) mais appliquée a un etat vwap_reversion
    # fabrique directement, plutôt qu'a de vraies bougies generees — on
    # monkeypatch resample_15min/compute_indicators pour renvoyer une
    # liste de bougies 15min dont la DERNIERE a l'etat voulu, les
    # precedentes a un etat neutre (aucun signal), afin d'isoler
    # precisement la detection sans dependre du calcul reel de VWAP/EMA.
    neutral = {"time": "2026-01-01T00:00:00", "close": 2000.0, "vwap": 2000.0, "vwap_std": 5.0,
               "ema200": 2000.0, "ema50": 2000.0, "hour_utc": 10, "bars_into_session": 20}
    signal_bar = {"time": "2026-01-01T05:00:00", "close": 1975.0, "vwap": 2010.0, "vwap_std": 20.0,
                  "ema200": 1900.0, "ema50": 2005.0, "hour_utc": 10, "bars_into_session": 20}
    fifteen_min_candles = [dict(neutral) for _ in range(199)] + [signal_bar]
    monkeypatch.setattr(odds_check.vwap_reversion, "resample_15min", lambda c: fifteen_min_candles)
    monkeypatch.setattr(odds_check.vwap_reversion, "compute_indicators", lambda c: c)

    candles_5min = _make_5min_candles(1, datetime(2026, 1, 1, tzinfo=timezone.utc), lambda i: 2000.0)
    entries = odds_check._find_historical_entries(candles_5min, "achat")

    assert len(entries) == 1
    assert entries[0]["entry_price"] == 1975.0
    assert entries[0]["stop_loss"] == 2010.0 - 2.5 * 20.0  # vwap - stop_sigma*std, même formule que decide_and_act_vwap


def test_find_historical_entries_ignores_wrong_direction(monkeypatch):
    signal_bar = {"time": "2026-01-01T05:00:00", "close": 1975.0, "vwap": 2010.0, "vwap_std": 20.0,
                  "ema200": 1900.0, "ema50": 2005.0, "hour_utc": 10, "bars_into_session": 20}
    fifteen_min_candles = [signal_bar] * 200
    monkeypatch.setattr(odds_check.vwap_reversion, "resample_15min", lambda c: fifteen_min_candles)
    monkeypatch.setattr(odds_check.vwap_reversion, "compute_indicators", lambda c: c)
    candles_5min = _make_5min_candles(1, datetime(2026, 1, 1, tzinfo=timezone.utc), lambda i: 2000.0)
    # signal_bar correspond a un signal "achat" (voir test precedent) --
    # chercher "vente" ne doit rien trouver.
    assert odds_check._find_historical_entries(candles_5min, "vente") == []


def test_find_historical_entries_ignores_bars_before_min_bars_into_session(monkeypatch):
    signal_bar = {"time": "2026-01-01T05:00:00", "close": 1975.0, "vwap": 2010.0, "vwap_std": 20.0,
                  "ema200": 1900.0, "ema50": 2005.0, "hour_utc": 10,
                  "bars_into_session": vwap_reversion.MIN_BARS_INTO_SESSION - 1}
    fifteen_min_candles = [signal_bar] * 200
    monkeypatch.setattr(odds_check.vwap_reversion, "resample_15min", lambda c: fifteen_min_candles)
    monkeypatch.setattr(odds_check.vwap_reversion, "compute_indicators", lambda c: c)
    candles_5min = _make_5min_candles(1, datetime(2026, 1, 1, tzinfo=timezone.utc), lambda i: 2000.0)
    assert odds_check._find_historical_entries(candles_5min, "achat") == []


def test_find_historical_entries_ignores_bars_at_or_after_session_end(monkeypatch):
    signal_bar = {"time": "2026-01-01T22:00:00", "close": 1975.0, "vwap": 2010.0, "vwap_std": 20.0,
                  "ema200": 1900.0, "ema50": 2005.0, "hour_utc": vwap_reversion.SESSION_END_HOUR_UTC,
                  "bars_into_session": 20}
    fifteen_min_candles = [signal_bar] * 200
    monkeypatch.setattr(odds_check.vwap_reversion, "resample_15min", lambda c: fifteen_min_candles)
    monkeypatch.setattr(odds_check.vwap_reversion, "compute_indicators", lambda c: c)
    candles_5min = _make_5min_candles(1, datetime(2026, 1, 1, tzinfo=timezone.utc), lambda i: 2000.0)
    assert odds_check._find_historical_entries(candles_5min, "achat") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_odds_check.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gold_bot.odds_check'`

- [ ] **Step 3: Implement**

Create `gold_bot/odds_check.py`:

```python
# gold_bot/odds_check.py
# Verification des cotes historiques avant l'ouverture d'un trade --
# voir docs/superpowers/specs/2026-10-22-gold-bot-risk-gate-design.md.
# Module pur (aucun appel reseau/broker) : recoit un historique de
# bougies 5min deja recupere (par gold_bot.loop) et rejoue les memes
# mecaniques de decision/sortie que gold_bot.bot.decide_and_act_vwap,
# pour estimer les cotes reelles du meme type de signal dans le passe.
from datetime import datetime, timezone

import gold_bot.vwap_reversion as vwap_reversion


def _find_historical_entries(candles_5min: list[dict], direction: str, symbol: str = "XAUUSD") -> list[dict]:
    """Rejoue vwap_reversion.resample_15min + compute_indicators UNE
    SEULE FOIS sur tout l'historique fourni (pas une fois par bougie --
    compute_indicators calcule deja tout en un seul passage progressif,
    avec reset VWAP/std par jour calendaire UTC, voir sa docstring),
    puis parcourt le resultat pour trouver chaque bougie 15min ou la
    MEME condition d'entree que decide_and_act_vwap (gold_bot/bot.py)
    se serait declenchee pour `direction`. Renvoie une liste vide si
    l'historique est trop court pour une EMA200 (meme seuil que
    vwap_reversion.latest_state)."""
    candles_15min = vwap_reversion.resample_15min(candles_5min)
    if len(candles_15min) < vwap_reversion.EMA200_PERIOD:
        return []
    vwap_reversion.compute_indicators(candles_15min)
    params = vwap_reversion.get_symbol_params(symbol)

    entries = []
    for c in candles_15min:
        if c["bars_into_session"] < vwap_reversion.MIN_BARS_INTO_SESSION:
            continue
        if c["hour_utc"] >= vwap_reversion.SESSION_END_HOUR_UTC:
            continue
        if c["vwap_std"] <= 0:
            continue
        if direction == "achat":
            fires = c["close"] > c["ema200"] and c["close"] <= c["vwap"] - params["entry_sigma"] * c["vwap_std"]
            stop_loss = c["vwap"] - params["stop_sigma"] * c["vwap_std"]
        elif direction == "vente":
            fires = c["close"] < c["ema200"] and c["close"] >= c["vwap"] + params["entry_sigma"] * c["vwap_std"]
            stop_loss = c["vwap"] + params["stop_sigma"] * c["vwap_std"]
        else:
            continue
        if not fires:
            continue
        distance = abs(c["close"] - stop_loss)
        if distance < c["close"] * vwap_reversion.MIN_DISTANCE_PCT:
            continue
        entries.append({
            "entry_time": c["time"], "entry_price": c["close"], "stop_loss": stop_loss,
        })
    return entries
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_odds_check.py -v`
Expected: PASS (all tests in the new file).

- [ ] **Step 5: Commit**

```bash
git add gold_bot/odds_check.py tests/gold_bot/test_odds_check.py
git commit -m "feat(gold-bot): detection des instances historiques d'un signal VWAP

Premiere brique de la verification des cotes avant trade : rejoue la
MEME condition d'entree que decide_and_act_vwap sur un historique deja
recupere, module pur sans appel reseau.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: `odds_check.py` — exit simulation and aggregate verdict

**Files:**
- Modify: `gold_bot/odds_check.py` (add to the file created in Task 5)
- Test: `tests/gold_bot/test_odds_check.py`

**Interfaces:**
- Consumes: `_find_historical_entries` (Task 5), `vwap_reversion.compute_trailing_stop` (existing), `vwap_reversion.get_symbol_params` (existing).
- Produces: `historical_odds_for_setup(candles_5min: list[dict], direction: str, symbol: str = "XAUUSD", min_sample_size: int = 20) -> dict` — the public entry point, consumed by Task 7 (`bot.decide_and_act_vwap`). Returns `{"sample_size": int, "win_rate_pct": float | None, "avg_return_pct": float | None, "verdict": "insufficient_history" | "favorable" | "unfavorable"}`.

This reuses `vwap_reversion.compute_trailing_stop` directly (the exact function the live loop calls) rather than re-implementing the trailing-stop math, so the simulated exits can never drift from the real bot's behavior. It replicates the SL/TP-touch priority and end-of-data handling already established in `gold_bot/backtest.py::simulate_trades` (SL wins on a simultaneous touch; an unresolved trade at the end of available history is closed at the last known price rather than dropped).

`_simulate_exit` also returns a `"close_time"` key (the timestamp of whichever candle closed the simulated trade) alongside `close_reason`/`close_price`/`return_pct`. `historical_odds_for_setup` uses it to deduplicate overlapping raw entries from `_find_historical_entries`: if the market stays beyond the entry band for several consecutive 15-minute bars, each bar would otherwise count as its own "entry," even though the real bot only ever holds one position at a time and blocks new signal detection while one is open (see `bot.decide_and_act_vwap`'s `if matching:` branch). Entries are walked in chronological order (already guaranteed by Task 5) and any entry falling before the previous *simulated* trade's `close_time` is skipped — otherwise the same market excursion would be counted as several independent trials, biasing the sample.

- [ ] **Step 1: Write the failing test**

Add to `tests/gold_bot/test_odds_check.py`:

```python
def test_simulate_exit_closes_on_stop_touch():
    # achat a 1975, stop a 1960 -- une bougie qui touche 1960 en low doit
    # clore au stop, retour negatif.
    candles_after_entry = [
        {"time": "2026-01-01 05:05:00", "open": 1975.0, "high": 1976.0, "low": 1959.0, "close": 1960.0,
         "tick_volume": 10, "spread": 2},
    ]
    result = odds_check._simulate_exit(
        candles_after_entry, direction="achat", entry_price=1975.0, initial_stop=1960.0,
        entry_time="2026-01-01 05:00:00", symbol="XAUUSD",
    )
    assert result["close_reason"] == "stop_touche"
    assert result["return_pct"] < 0
    assert result["close_time"] == "2026-01-01 05:05:00"


def test_simulate_exit_closes_at_session_end():
    candles_after_entry = [
        {"time": "2026-01-01 22:00:00", "open": 1980.0, "high": 1982.0, "low": 1978.0, "close": 1981.0,
         "tick_volume": 10, "spread": 2},
    ]
    result = odds_check._simulate_exit(
        candles_after_entry, direction="achat", entry_price=1975.0, initial_stop=1960.0,
        entry_time="2026-01-01 05:00:00", symbol="XAUUSD",
    )
    assert result["close_reason"] == "fin_session"
    assert result["return_pct"] > 0  # 1981 > 1975, achat gagnant
    assert result["close_time"] == "2026-01-01 22:00:00"


def test_simulate_exit_closes_at_end_of_available_history_when_unresolved():
    candles_after_entry = [
        {"time": "2026-01-01 05:05:00", "open": 1976.0, "high": 1977.0, "low": 1974.0, "close": 1976.0,
         "tick_volume": 10, "spread": 2},
    ]
    result = odds_check._simulate_exit(
        candles_after_entry, direction="achat", entry_price=1975.0, initial_stop=1960.0,
        entry_time="2026-01-01 05:00:00", symbol="XAUUSD",
    )
    assert result["close_reason"] == "fin_historique"
    assert result["close_price"] == 1976.0
    assert result["close_time"] == "2026-01-01 05:05:00"


def test_simulate_exit_blends_partial_leg_target_with_runner_leg_stop():
    # XAUUSD : partial_tp_pct=0.7, partial_tp_r=2.0 (vwap_reversion.PARTIAL_TP_PCT/
    # PARTIAL_TP_R). distance = 1975-1960 = 15 -> cible partielle = 1975 + 2.0*15 = 2005.
    # Bougie 1 : touche la cible partielle (high=2006) SANS toucher le stop
    # runner (low=1970 > stop=1960) -- la jambe partielle se cloture ici,
    # la jambe runner continue (son stop ne bouge pas : avec seulement 2
    # bougies 5min dans l'historique fourni, compute_trailing_stop ne peut
    # pas calculer -- EMA200_PERIOD=200 -- et renvoie None, voir sa
    # docstring).
    # Bougie 2 : touche le stop runner, toujours a 1960 (low=1958).
    # Les deux jambes divergent donc reellement : la partielle est
    # gagnante (+), le runner est perdant (-) -- preuve que le melange
    # n'est pas un cas degenere a une seule bougie (contrairement aux 3
    # tests precedents, ou les deux jambes se resolvent forcement de
    # facon identique sur leur unique bougie).
    candles_after_entry = [
        {"time": "2026-01-01 05:05:00", "open": 1980.0, "high": 2006.0, "low": 1970.0, "close": 2000.0,
         "tick_volume": 10, "spread": 2},
        {"time": "2026-01-01 05:10:00", "open": 2000.0, "high": 2001.0, "low": 1958.0, "close": 1959.0,
         "tick_volume": 10, "spread": 2},
    ]
    result = odds_check._simulate_exit(
        candles_after_entry, direction="achat", entry_price=1975.0, initial_stop=1960.0,
        entry_time="2026-01-01 05:00:00", symbol="XAUUSD",
    )
    partial_return = (2005.0 - 1975.0) / 1975.0 * 100  # cible partielle touchee, bougie 1
    runner_return = (1960.0 - 1975.0) / 1975.0 * 100   # stop runner touche (jamais suivi), bougie 2
    expected = 0.7 * partial_return + 0.3 * runner_return
    assert result["return_pct"] == pytest.approx(expected)
    # close_reason/close_price/close_time refletent la jambe runner
    # (voir docstring de _simulate_exit) -- elle se cloture a la bougie 2.
    assert result["close_reason"] == "stop_touche"
    assert result["close_price"] == 1960.0
    assert result["close_time"] == "2026-01-01 05:10:00"


def test_historical_odds_for_setup_insufficient_history_does_not_block(monkeypatch):
    monkeypatch.setattr(odds_check, "_find_historical_entries", lambda candles, direction, symbol="XAUUSD": [])
    result = odds_check.historical_odds_for_setup([{"dummy": True}], "achat", min_sample_size=20)
    assert result["verdict"] == "insufficient_history"
    assert result["sample_size"] == 0


def test_historical_odds_for_setup_favorable_when_average_return_positive(monkeypatch):
    # Entrees espacees d'un jour chacune, cloture simulee 2h apres
    # l'entree (bien avant l'entree suivante) -- aucune ne doit etre
    # ignoree par la deduplication des signaux chevauchants.
    entries = [{"entry_time": f"2026-01-{i:02d} 05:00:00", "entry_price": 1975.0, "stop_loss": 1960.0}
               for i in range(1, 21)]
    monkeypatch.setattr(odds_check, "_find_historical_entries", lambda candles, direction, symbol="XAUUSD": entries)

    def fake_simulate_exit(candles, direction, entry_price, initial_stop, entry_time, symbol="XAUUSD"):
        day = entry_time.split(" ")[0]
        return {"close_reason": "fin_session", "close_price": 1985.0, "return_pct": 0.5,
                "close_time": f"{day} 07:00:00"}

    monkeypatch.setattr(odds_check, "_simulate_exit", fake_simulate_exit)
    result = odds_check.historical_odds_for_setup([{"dummy": True}], "achat", min_sample_size=20)
    assert result["sample_size"] == 20
    assert result["verdict"] == "favorable"
    assert result["avg_return_pct"] == pytest.approx(0.5)


def test_historical_odds_for_setup_unfavorable_when_average_return_negative(monkeypatch):
    entries = [{"entry_time": f"2026-01-{i:02d} 05:00:00", "entry_price": 1975.0, "stop_loss": 1960.0}
               for i in range(1, 21)]
    monkeypatch.setattr(odds_check, "_find_historical_entries", lambda candles, direction, symbol="XAUUSD": entries)

    def fake_simulate_exit(candles, direction, entry_price, initial_stop, entry_time, symbol="XAUUSD"):
        day = entry_time.split(" ")[0]
        return {"close_reason": "stop_touche", "close_price": 1960.0, "return_pct": -0.3,
                "close_time": f"{day} 07:00:00"}

    monkeypatch.setattr(odds_check, "_simulate_exit", fake_simulate_exit)
    result = odds_check.historical_odds_for_setup([{"dummy": True}], "achat", min_sample_size=20)
    assert result["verdict"] == "unfavorable"


def test_historical_odds_for_setup_skips_entries_overlapping_a_still_open_simulated_position(monkeypatch):
    # 3 entrees brutes : la 2e tombe PENDANT la position simulee ouverte
    # par la 1re (close_time de la 1re = 10h, entree de la 2e = 8h, donc
    # avant la cloture) -- doit etre ignoree. La 3e tombe apres la
    # cloture de la 1re -- doit etre retenue. Sans deduplication, les 3
    # seraient comptees comme des trades independants alors que le bot
    # reel n'aurait jamais ouvert la 2e (position deja ouverte sur la 1re).
    entries = [
        {"entry_time": "2026-01-01 05:00:00", "entry_price": 1975.0, "stop_loss": 1960.0},
        {"entry_time": "2026-01-01 08:00:00", "entry_price": 1976.0, "stop_loss": 1961.0},
        {"entry_time": "2026-01-01 11:00:00", "entry_price": 1977.0, "stop_loss": 1962.0},
    ]
    monkeypatch.setattr(odds_check, "_find_historical_entries", lambda candles, direction, symbol="XAUUSD": entries)

    def fake_simulate_exit(candles, direction, entry_price, initial_stop, entry_time, symbol="XAUUSD"):
        # La position ouverte a 05:00 se cloture a 10:00 (apres l'entree
        # brute de 08:00, avant celle de 11:00) ; les autres cloturent
        # vite, peu importe (jamais evaluees pour la 2e, ignoree avant).
        close_time = "2026-01-01 10:00:00" if entry_time == "2026-01-01 05:00:00" else "2026-01-01 12:00:00"
        return {"close_reason": "fin_session", "close_price": 1985.0, "return_pct": 0.5, "close_time": close_time}

    monkeypatch.setattr(odds_check, "_simulate_exit", fake_simulate_exit)
    result = odds_check.historical_odds_for_setup([{"dummy": True}], "achat", min_sample_size=2)
    assert result["sample_size"] == 2  # l'entree de 08:00 (chevauchante) a ete ignoree
```

(Add `import pytest` at the top of `tests/gold_bot/test_odds_check.py` if not already present from Task 5.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_odds_check.py -k "simulate_exit or historical_odds_for_setup" -v`
Expected: FAIL with `AttributeError: module 'gold_bot.odds_check' has no attribute '_simulate_exit'`

- [ ] **Step 3: Implement**

Append to `gold_bot/odds_check.py`:

```python
def _simulate_exit(candles_after_entry: list[dict], direction: str, entry_price: float,
                    initial_stop: float, entry_time: str, symbol: str = "XAUUSD") -> dict:
    """Rejoue la sortie reelle bougie par bougie a partir de
    `candles_after_entry` (5min, strictement apres la bougie d'entree).

    MODELISE LES DEUX JAMBES de decide_and_act_vwap quand
    get_symbol_params(symbol)["partial_tp_pct"] > 0 (XAUUSD et XAGUSD
    aujourd'hui -- voir vwap_reversion.PARTIAL_TP_PCT/XAGUSD_PARTIAL_TP_PCT) :
    - **Jambe partielle** (partial_tp_pct du volume) : stop FIXE a
      `initial_stop` -- decide_and_act_vwap exclut explicitement du
      suivi toute position avec takeProfit (`runner_matching = [p for p
      in matching if not p.get("takeProfit")]`), donc son stop ne bouge
      JAMAIS -- cible fixe a `entry_price +/- partial_tp_r * distance`.
      Stop gagnant en cas de toucher simultane avec sa propre cible
      (meme convention que gold_bot.backtest.simulate_trades).
    - **Jambe runner** (1 - partial_tp_pct du volume) : stop SUIVEUR via
      vwap_reversion.compute_trailing_stop a chaque pas (jamais une
      reimplementation separee -- la fidelite aux vraies mecaniques de
      sortie est le point de ce module), jamais de cible.
    Les DEUX jambes se cloturent aussi de force a la fin de session
    (SESSION_END_HOUR_UTC, meme decide_and_act_vwap qui cloture TOUTES
    les positions `matching` a ce moment-la, pas seulement le runner),
    ou a la fin de l'historique fourni (fermees au dernier prix connu
    plutot que d'etre ignorees).

    Le retour renvoye (`return_pct`) est la moyenne ponderee des deux
    jambes par `partial_tp_pct` -- reproduit le P&L REEL combine du
    bot plutot que de ne modeliser qu'une jambe runner a 100% du volume
    (ce que faisait une version anterieure de cette fonction : un bug
    trouve en revue, puisque ~70% du volume reel suit en fait une
    cible fixe, jamais le stop suiveur). Si `partial_tp_pct <= 0`
    (aucun symbole aujourd'hui, mais defensif), tout le volume reste
    en jambe unique runner -- comportement inchange.

    `close_reason`/`close_price`/`close_time` refletent la jambe
    RUNNER specifiquement (pas un melange) : c'est elle qui determine
    la duree reelle de blocage d'un nouveau signal cote
    historical_odds_for_setup dans la tres grande majorite des cas
    (son stop ne peut que se resserrer en faveur du trade, jamais
    s'eloigner), seul `return_pct` est le melange des deux jambes."""
    is_achat = direction == "achat"
    params = vwap_reversion.get_symbol_params(symbol)
    distance = abs(entry_price - initial_stop)
    partial_tp_pct = params["partial_tp_pct"]
    has_partial_leg = partial_tp_pct > 0
    if has_partial_leg:
        partial_target = (entry_price + params["partial_tp_r"] * distance if is_achat
                           else entry_price - params["partial_tp_r"] * distance)

    def _return_pct(price):
        return ((price - entry_price) / entry_price * 100 if is_achat
                else (entry_price - price) / entry_price * 100)

    stop = initial_stop
    history_so_far = [{"time": entry_time, "open": entry_price, "high": entry_price,
                        "low": entry_price, "close": entry_price, "tick_volume": 0, "spread": 0}]

    partial_return = None
    runner_return = runner_close_reason = runner_close_price = runner_close_time = None

    for candle in candles_after_entry:
        history_so_far.append(candle)
        hour = datetime.fromisoformat(candle["time"].replace(" ", "T")).replace(tzinfo=timezone.utc).hour
        session_ended = hour >= vwap_reversion.SESSION_END_HOUR_UTC

        if has_partial_leg and partial_return is None:
            sl_hit = candle["low"] <= initial_stop if is_achat else candle["high"] >= initial_stop
            if sl_hit:
                partial_return = _return_pct(initial_stop)
            else:
                tp_hit = candle["high"] >= partial_target if is_achat else candle["low"] <= partial_target
                if tp_hit:
                    partial_return = _return_pct(partial_target)
                elif session_ended:
                    partial_return = _return_pct(candle["close"])

        if runner_return is None:
            sl_hit = candle["low"] <= stop if is_achat else candle["high"] >= stop
            if sl_hit:
                runner_return = _return_pct(stop)
                runner_close_reason, runner_close_price, runner_close_time = "stop_touche", stop, candle["time"]
            elif session_ended:
                runner_return = _return_pct(candle["close"])
                runner_close_reason = "fin_session"
                runner_close_price, runner_close_time = candle["close"], candle["time"]
            else:
                new_stop = vwap_reversion.compute_trailing_stop(
                    history_so_far, direction, entry_time, entry_price, stop, symbol=symbol)
                if new_stop is not None:
                    stop = new_stop

        if runner_return is not None and (not has_partial_leg or partial_return is not None):
            break

    if runner_return is None:
        last = candles_after_entry[-1]
        runner_return = _return_pct(last["close"])
        runner_close_reason, runner_close_price, runner_close_time = "fin_historique", last["close"], last["time"]
    if has_partial_leg and partial_return is None:
        partial_return = _return_pct(candles_after_entry[-1]["close"])

    blended_return = (partial_tp_pct * partial_return + (1 - partial_tp_pct) * runner_return
                       if has_partial_leg else runner_return)

    return {"close_reason": runner_close_reason, "close_price": runner_close_price,
            "return_pct": blended_return, "close_time": runner_close_time}


def historical_odds_for_setup(candles_5min: list[dict], direction: str, symbol: str = "XAUUSD",
                               min_sample_size: int = 20) -> dict:
    """Point d'entree public -- voir docs/superpowers/specs/2026-10-22-
    gold-bot-risk-gate-design.md. `min_sample_size` sous ce seuil ->
    verdict "insufficient_history", NE bloque jamais le trade (un
    echantillon trop petit ne permet de conclure dans aucun sens).
    Au-dela, verdict "unfavorable" (bloque) si et seulement si
    l'esperance moyenne sur l'echantillon est negative ou nulle --
    critere simple et objectif, pas de combinaison taux de reussite/
    multiple de R arbitraire (voir la spec pour la justification).

    Deduplication des signaux chevauchants : _find_historical_entries
    renvoie TOUTE bougie 15min ou la condition d'entree est vraie --
    si le marche reste plusieurs bougies consecutives au-dela de la
    bande vwap +/- entry_sigma*std, chacune compte comme une
    "entree" distincte alors que le bot reel n'aurait ouvert qu'UNE
    position (voir bot.decide_and_act_vwap : une position deja
    ouverte bloque toute recherche de nouveau signal jusqu'a sa
    cloture). Reproduit ce comportement ici : les entrees sont
    parcourues dans l'ordre chronologique (deja garanti par
    _find_historical_entries), et toute entree dont `entry_time`
    tombe avant la cloture simulee de la position precedente est
    ignoree -- sans cela, un meme mouvement de marche serait compte
    plusieurs fois comme des trades independants, biaisant
    artificiellement l'echantillon."""
    entries = _find_historical_entries(candles_5min, direction, symbol=symbol)
    if len(entries) < min_sample_size:
        return {"sample_size": len(entries), "win_rate_pct": None, "avg_return_pct": None,
                "verdict": "insufficient_history"}

    outcomes = []
    blocked_until = None
    for entry in entries:
        entry_dt = datetime.fromisoformat(entry["entry_time"].replace(" ", "T")).replace(tzinfo=timezone.utc)
        if blocked_until is not None and entry_dt < blocked_until:
            continue
        # N'exclut QUE les bougies dont le "time" est parsable et <=
        # entry_dt -- une bougie sans "time" exploitable (jamais le cas
        # pour de vraies donnees 5min, mais vrai pour un fixture de test
        # minimal comme [{"dummy": True}]) est conservee plutot que de
        # faire planter le filtre : ce module ne doit jamais lever sur
        # un historique partiel ou mal forme, voir le contrat
        # "insufficient_history ne bloque jamais le trade" ci-dessus.
        after = []
        for c in candles_5min:
            c_time = c.get("time")
            if c_time is not None:
                try:
                    c_dt = datetime.fromisoformat(c_time.replace(" ", "T")).replace(tzinfo=timezone.utc)
                    if c_dt <= entry_dt:
                        continue
                except ValueError:
                    pass
            after.append(c)
        if not after:
            continue
        outcome = _simulate_exit(
            after, direction, entry["entry_price"], entry["stop_loss"], entry["entry_time"], symbol=symbol)
        outcomes.append(outcome)
        blocked_until = datetime.fromisoformat(outcome["close_time"].replace(" ", "T")).replace(tzinfo=timezone.utc)

    if len(outcomes) < min_sample_size:
        return {"sample_size": len(outcomes), "win_rate_pct": None, "avg_return_pct": None,
                "verdict": "insufficient_history"}

    avg_return_pct = sum(o["return_pct"] for o in outcomes) / len(outcomes)
    win_rate_pct = sum(1 for o in outcomes if o["return_pct"] > 0) / len(outcomes) * 100
    verdict = "unfavorable" if avg_return_pct <= 0 else "favorable"
    return {"sample_size": len(outcomes), "win_rate_pct": win_rate_pct,
            "avg_return_pct": avg_return_pct, "verdict": verdict}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_odds_check.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add gold_bot/odds_check.py tests/gold_bot/test_odds_check.py
git commit -m "feat(gold-bot): simulation de sortie et verdict agrege pour odds_check

Reutilise vwap_reversion.compute_trailing_stop telle quelle (jamais une
reimplementation separee) pour que la simulation ne puisse pas deriver
du comportement reel du bot. Verdict \"unfavorable\" seulement si
l'esperance moyenne sur l'echantillon est negative -- jamais sur un
echantillon trop petit, qui laisse toujours passer le trade.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: Wire `odds_check` into `decide_and_act_vwap`, fetch+cache history in `loop.py`

**Files:**
- Modify: `gold_bot/bot.py:197-213` (signature + call site)
- Modify: `gold_bot/loop.py:1-18` (imports), `gold_bot/loop.py:214-254` (fetch/cache historical candles, pass into `decide_and_act_vwap`)
- Test: `tests/gold_bot/test_bot.py`, `tests/gold_bot/test_loop.py`

**Interfaces:**
- Consumes: `odds_check.historical_odds_for_setup` (Task 6), `backtest.fetch_gold_candles_range(..., symbol=...)` (Task 1).
- Produces: nothing further — this is the final integration point.

`decide_and_act_vwap` must stay network-free (Global Constraint). It receives the historical candles as a plain parameter and only calls the pure `odds_check.historical_odds_for_setup` once it has already determined a direction/entry/stop — exactly where `circuit_breaker.can_open_position` is called today. `loop.py` fetches and caches those historical candles (24h TTL, same `_save_cache`/cache-path pattern already used for live candles) so the network cost stays independent of how often a signal fires.

- [ ] **Step 1: Write the failing test for `decide_and_act_vwap`**

Add to `tests/gold_bot/test_bot.py`, right after `test_decide_and_act_vwap_opens_achat_with_partial_and_runner_legs` (reuse its exact `state`/mocking setup):

```python
def test_decide_and_act_vwap_blocked_by_unfavorable_odds_check(monkeypatch):
    state = _vwap_state(close=1975.0, vwap=2010.0, vwap_std=20.0, ema200=1900.0)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    monkeypatch.setattr(
        bot.odds_check, "historical_odds_for_setup",
        lambda candles, direction, symbol="XAUUSD": {
            "sample_size": 25, "win_rate_pct": 30.0, "avg_return_pct": -0.2, "verdict": "unfavorable",
        },
    )
    result = bot.decide_and_act_vwap(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
        historical_candles=[{"dummy": True}],
    )
    assert result == {"action": "aucune", "reason": "cotes historiques defavorables pour ce type de signal"}


def test_decide_and_act_vwap_opens_when_odds_check_favorable(monkeypatch):
    state = _vwap_state(close=1975.0, vwap=2010.0, vwap_std=20.0, ema200=1900.0)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)
    monkeypatch.setattr(
        bot.odds_check, "historical_odds_for_setup",
        lambda candles, direction, symbol="XAUUSD": {
            "sample_size": 25, "win_rate_pct": 60.0, "avg_return_pct": 0.3, "verdict": "favorable",
        },
    )
    result = bot.decide_and_act_vwap(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
        historical_candles=[{"dummy": True}],
    )
    assert result["action"] == "simulation"


def test_decide_and_act_vwap_skips_odds_check_when_no_historical_candles(monkeypatch):
    # historical_candles=None (comportement par defaut) -> la verification
    # est sautee entierement, jamais un blocage par defaut faute de
    # donnees (coherent avec le reste de decide_and_act_vwap, qui ne
    # bloque jamais sur l'absence d'une donnee optionnelle).
    state = _vwap_state(close=1975.0, vwap=2010.0, vwap_std=20.0, ema200=1900.0)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)

    def _fail_if_called(*a, **k):
        raise AssertionError("historical_odds_for_setup ne doit pas être appelé sans historical_candles")
    monkeypatch.setattr(bot.odds_check, "historical_odds_for_setup", _fail_if_called)

    result = bot.decide_and_act_vwap(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=10000, open_positions=[], circuit_breaker=_open_circuit_breaker(),
    )
    assert result["action"] == "simulation"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_bot.py -k odds_check -v`
Expected: FAIL with `TypeError: decide_and_act_vwap() got an unexpected keyword argument 'historical_candles'`

- [ ] **Step 3: Implement the `bot.py` change**

In `gold_bot/bot.py`, add the import near the top of the file (alongside the existing `vwap_reversion`/`risk` imports):

```python
import gold_bot.odds_check as odds_check
```

Modify the `decide_and_act_vwap` signature (line 197-200):

```python
def decide_and_act_vwap(candles: list[dict], *, contract_size: float, balance: float, equity: float,
                         volume_step: float, min_volume: float, max_volume: float,
                         open_positions: list[dict], circuit_breaker: "risk.CircuitBreaker",
                         symbol: str = "XAUUSD", risk_pct: float = 0.05,
                         historical_candles: list[dict] | None = None) -> dict:
```

And insert the odds check right before the existing circuit-breaker call (line 289 in the current file — `if not circuit_breaker.can_open_position(equity):`):

```python
    if historical_candles is not None:
        odds = odds_check.historical_odds_for_setup(historical_candles, direction, symbol=symbol)
        if odds["verdict"] == "unfavorable":
            return {"action": "aucune", "reason": "cotes historiques defavorables pour ce type de signal"}

    if not circuit_breaker.can_open_position(equity):
        return {"action": "aucune", "reason": "coupe-circuit journalier déclenché"}
```

- [ ] **Step 4: Run the `bot.py` tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_bot.py -v`
Expected: PASS (all tests in the file, including every pre-existing `decide_and_act_vwap` test — none of them pass `historical_candles`, so they all exercise the `None`/skip path and must be unaffected).

- [ ] **Step 5: Write the failing test for the `loop.py` fetch/cache wiring**

Add to `tests/gold_bot/test_loop.py` (again, read the file's existing successful-run_cycle test first and mirror its mock style exactly):

```python
def test_run_cycle_fetches_historical_candles_and_passes_them_to_decide_and_act(monkeypatch, tmp_path):
    captured = {}

    def fake_decide_and_act_vwap(candles, **kwargs):
        captured["historical_candles"] = kwargs.get("historical_candles")
        return {"action": "aucune", "reason": "pas de signal d'entree"}

    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", fake_decide_and_act_vwap)
    monkeypatch.setattr(loop.backtest, "fetch_gold_candles_range", lambda *a, **k: [{"fake": "history"}])
    # ... (mêmes mocks de base que les autres tests de run_cycle dans ce
    # fichier : confluence.fetch_gold_candles, broker.get_account_information,
    # bot.reconcile_positions, broker.get_symbol_specification, state.load_state
    # avec dry_run=True pour rester simple)

    cb = risk.CircuitBreaker(persist_path=str(tmp_path / "cb_state.json"))
    loop.run_cycle("token", "account", cb, now=datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc))
    assert captured["historical_candles"] == [{"fake": "history"}]


def test_run_cycle_reuses_cached_historical_candles_within_ttl(monkeypatch, tmp_path):
    fetch_calls = []
    monkeypatch.setattr(loop.backtest, "fetch_gold_candles_range",
                         lambda *a, **k: fetch_calls.append(1) or [{"fake": "history"}])
    monkeypatch.setattr(loop.bot, "decide_and_act_vwap", lambda candles, **kwargs: {"action": "aucune", "reason": "x"})
    # ... (mêmes mocks de base, en pointant LATEST_BALANCE_PATH/etc. et le
    # cache d'historique vers tmp_path pour isoler le test du système de
    # fichiers réel -- monkeypatcher loop._historical_candles_cache_path
    # ou l'équivalent pour renvoyer un chemin sous tmp_path)

    cb = risk.CircuitBreaker(persist_path=str(tmp_path / "cb_state.json"))
    loop.run_cycle("token", "account", cb, now=datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc))
    loop.run_cycle("token", "account", cb, now=datetime(2026, 10, 22, 10, 1, tzinfo=timezone.utc))
    assert len(fetch_calls) == 1  # deuxième cycle réutilise le cache, pas de second fetch réseau
```

**Note for the implementer**: as with Task 4, the exact base mock setup must mirror the existing passing `run_cycle` tests already in this file — read them fully before writing these two, and keep every unrelated mock (price/balance/positions/spec) identical to what already works, changing only what's new here (`backtest.fetch_gold_candles_range`, `historical_candles` capture, cache-path isolation).

- [ ] **Step 6: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_loop.py -k historical_candles -v`
Expected: FAIL (`decide_and_act_vwap` never receives `historical_candles`, `backtest.fetch_gold_candles_range` is never called — `loop.py` doesn't import `backtest` yet either).

- [ ] **Step 7: Implement the `loop.py` change**

Add the import at the top of `gold_bot/loop.py` (alongside the existing `gold_bot.bot`/`gold_bot.broker`/etc. imports):

```python
import gold_bot.backtest as backtest
```

Add two new module-level constants near the existing `CANDLES_FETCH_LIMIT`/path constants (after line 57, `DECISIONS_LOG_PATH`):

```python
# TTL de l'historique utilisé par la vérification des cotes
# (gold_bot.odds_check) -- un historique de plusieurs dizaines de jours
# n'a pas besoin d'être rafraîchi à chaque cycle (60s) ; 24h tient
# largement compte de l'évolution réelle du marché pour ce besoin.
HISTORICAL_ODDS_CACHE_MAX_AGE_SECONDS = 24 * 3600
HISTORICAL_ODDS_WINDOW_DAYS = 90
```

Add a new helper function right after `_candles_cache_path` (after line 67):

```python
def _historical_candles_cache_path(symbol: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), f"historical_candles_cache_{symbol.lower()}.json")


def _load_or_fetch_historical_candles(token: str, account_id: str, symbol: str,
                                       region: str, now_dt: datetime) -> list[dict]:
    """Charge l'historique mis en cache pour `symbol` s'il a moins de
    HISTORICAL_ODDS_CACHE_MAX_AGE_SECONDS, sinon le récupère via
    backtest.fetch_gold_candles_range et le met en cache. Ne lève jamais
    -- un échec réseau renvoie [] (odds_check.historical_odds_for_setup
    traite un historique vide comme "insufficient_history", qui ne
    bloque jamais un trade), la vérification des cotes dégrade donc
    sans jamais interrompre le cycle."""
    path = _historical_candles_cache_path(symbol)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cached = json.load(f)
            fetched_at = datetime.fromisoformat(cached["fetched_at"])
            if (now_dt - fetched_at).total_seconds() < HISTORICAL_ODDS_CACHE_MAX_AGE_SECONDS:
                return cached["candles"]
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            pass
    try:
        candles = backtest.fetch_gold_candles_range(
            token, account_id, now_dt - timedelta(days=HISTORICAL_ODDS_WINDOW_DAYS), now_dt,
            region=region, symbol=symbol,
        )
    except Exception:
        return []
    _save_cache({"candles": candles, "fetched_at": _now_iso()}, path)
    return candles
```

Modify `run_cycle` to call this and pass the result through. Right after the existing `spec = _with_retry(...)` line (254 in the current file) and before the `decision = bot.decide_and_act_vwap(...)` call:

```python
        historical_candles = _load_or_fetch_historical_candles(token, account_id, symbol, region, now_dt)
        decision = bot.decide_and_act_vwap(
            candles, contract_size=contract_size, balance=balance, equity=equity,
            volume_step=spec["volumeStep"], min_volume=spec["minVolume"], max_volume=spec["maxVolume"],
            open_positions=open_positions, circuit_breaker=circuit_breaker, symbol=symbol,
            risk_pct=profile_params["risk_pct"], historical_candles=historical_candles,
        )
```

(Only the new `historical_candles = ...` line and the new `historical_candles=historical_candles` keyword argument are additions — the rest of the `decide_and_act_vwap` call is unchanged, shown for exact placement.)

- [ ] **Step 8: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_loop.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 9: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: PASS. This is the final task touching shared modules (`bot.py`, `loop.py`) — confirm nothing elsewhere in the project broke.

- [ ] **Step 10: Commit**

```bash
git add gold_bot/bot.py gold_bot/loop.py tests/gold_bot/test_bot.py tests/gold_bot/test_loop.py
git commit -m "feat(gold-bot): branche la vérification des cotes avant trade

decide_and_act_vwap reste sans appel réseau (reçoit l'historique déjà
récupéré en paramètre) -- loop.py récupère et met en cache cet
historique (TTL 24h) indépendamment du fait qu'un signal se déclenche
ou non, pour que le coût réseau reste négligeable. Un historique absent
ou un échec réseau dégrade silencieusement vers \"insufficient_history\",
qui ne bloque jamais un trade.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Self-review notes (completed during plan writing, not a step for the implementer)

- **Spec coverage**: max trades/day (Tasks 2-4), live odds check (Tasks 5-7), both integrated into the real production call path (`loop.py::run_cycle`) — matches the spec's two components exactly. R:R minimum and the IBKR bot are explicitly out of scope per the spec, no task touches either.
- **Architecture guarantee preserved**: `decide_and_act_vwap` never imports or calls `broker`/network code (Task 7 passes data in, never fetches) — verified against the existing module docstring in `loop.py` before writing Task 7.
- **Type/interface consistency checked**: `historical_odds_for_setup`'s return shape (`sample_size`/`win_rate_pct`/`avg_return_pct`/`verdict`) is identical across Task 6 (producer) and Task 7 (consumer, via the mocked `verdict` key). `_find_historical_entries`'s return shape (`entry_time`/`entry_price`/`stop_loss`) is identical across Task 5 (producer) and Task 6 (`historical_odds_for_setup`, consumer).
- **No placeholder steps**: every implementation step contains complete, runnable code; every test step contains complete assertions. The two "Note for the implementer" callouts (Tasks 4 and 7) are explicit only about *mirroring an existing test's mock setup* (named precisely, with file/pattern to copy) rather than inventing new fixture conventions — they are not placeholders for missing logic, every assertion and the code under test is fully specified.

---

### Task 8: Final whole-branch review fix wave

The final review (after all 7 tasks passed their own task review, 4 of them
through one fix round each) found cross-task issues no single task's review
could see. The biggest: **the runner leg's trailing stop never actually
trails inside `odds_check._simulate_exit`**. `vwap_reversion.compute_trailing_stop`
requires at least `EMA200_PERIOD` (200) resampled 15-minute candles before it
will compute anything (`if len(candles_15min) < EMA200_PERIOD: return None`,
see `gold_bot/vwap_reversion.py`). A single trading session (00:00-22:00 UTC)
holds at most ~88 fifteen-minute bars. The Task 6 version of `_simulate_exit`
only ever fed `compute_trailing_stop` a synthetic entry candle plus whatever
came *after* entry within the same session — so that 200-bar threshold was
**never reached in production use**, meaning every simulated runner leg
effectively traded on a fixed stop, contradicting the module's entire stated
purpose (replaying the bot's *real* exit mechanics). In live trading,
`decide_and_act_vwap` feeds `compute_trailing_stop` whatever window `loop.py`
fetched that cycle (up to `CANDLES_FETCH_LIMIT=1000` 5-minute candles, ~3.5
days) — `_simulate_exit` must do the same: a real sliding window of actual
historical candles ending at each replay step, not a session-bounded,
synthetic-seeded one.

The final review also found: a look-ahead bias in entry-to-exit candle
selection (the 15-minute entry bucket's own candles were being replayed a
second time as if they were "after" the entry); the spec's promised spread
deduction was silently dropped; the odds-check gate runs before the (free,
in-memory) circuit-breaker check, paying the now-much-costlier replay even
when the circuit breaker alone would already block the trade; the historical
candle cache has no backoff on fetch failure (retries a 90-day/~20-page
MetaApi fetch every 60s during an outage); the daily trade counter
undercounts when an entry's partial leg succeeds but its runner leg's order
fails; and two of the previously-deferred Minor findings (missing `TypeError`
in the cache-read except tuple, `"Z"`-suffix `fromisoformat` inconsistency)
turn out to matter more than estimated, since the function they're in blocks
*all* position management for a symbol (not just new entries) when it
misbehaves.

**Files:**
- Modify: `gold_bot/odds_check.py` (rewrite `_simulate_exit` and
  `historical_odds_for_setup`; add `_candles_fingerprint`,
  `_verdict_cache_path`, `historical_odds_for_setup_cached`; new imports)
- Modify: `gold_bot/loop.py` (harden `_load_or_fetch_historical_candles`,
  add a failure-backoff constant, fix trade-counting to not require every
  step in a batch to succeed)
- Modify: `gold_bot/bot.py` (reorder the circuit-breaker and odds-check
  gates in `decide_and_act_vwap`; call the cached wrapper)
- Modify: `.gitignore` (new cache files)
- Test: `tests/gold_bot/test_odds_check.py`, `tests/gold_bot/test_loop.py`,
  `tests/gold_bot/test_bot.py`

**Interfaces:**
- `_simulate_exit`'s signature changes: it now takes the FULL
  `candles_5min: list[dict]` and an `entry_index: int` (the index of the
  first candle at or after the entry bucket's close) instead of a
  pre-sliced `candles_after_entry` list. Callers inside this same module
  (`historical_odds_for_setup`) are the only callers — no other task's
  code calls `_simulate_exit` directly.
- New public function `historical_odds_for_setup_cached(candles_5min,
  direction, symbol="XAUUSD", min_sample_size=20) -> dict` — same return
  shape as `historical_odds_for_setup` (unchanged: `sample_size`/
  `win_rate_pct`/`avg_return_pct`/`verdict`). `gold_bot/bot.py` calls this
  cached wrapper instead of the raw function from now on.

- [ ] **Step 1: Write the failing tests for the rewritten `_simulate_exit`**

Replace ALL FOUR existing `_simulate_exit` tests in
`tests/gold_bot/test_odds_check.py` (`test_simulate_exit_closes_on_stop_touch`,
`test_simulate_exit_closes_at_session_end`,
`test_simulate_exit_closes_at_end_of_available_history_when_unresolved`,
`test_simulate_exit_blends_partial_leg_target_with_runner_leg_stop`) with
the versions below — the signature changed (`candles_5min` + `entry_index`
instead of `candles_after_entry`), so every call site in these tests must
be updated. Also add two new tests proving the trailing stop and the
bucket-end filtering actually work. Replace the whole block of five
functions (the four existing ones plus the new fifth) with:

```python
def test_simulate_exit_closes_on_stop_touch():
    # achat a 1975, stop a 1960 -- une bougie qui touche 1960 en low doit
    # clore au stop, retour negatif. candles_5min[0] est la bougie
    # d'entree elle-meme (non utilisee par le stop fixe partiel/initial,
    # seulement comme reference temporelle) ; entry_index=1 pointe la
    # premiere bougie APRES l'entree.
    candles_5min = [
        {"time": "2026-01-01 05:00:00", "open": 1975.0, "high": 1975.0, "low": 1975.0, "close": 1975.0,
         "tick_volume": 10, "spread": 0},
        {"time": "2026-01-01 05:05:00", "open": 1975.0, "high": 1976.0, "low": 1959.0, "close": 1960.0,
         "tick_volume": 10, "spread": 2},
    ]
    result = odds_check._simulate_exit(
        candles_5min, entry_index=1, direction="achat", entry_price=1975.0, initial_stop=1960.0,
        entry_time="2026-01-01 05:00:00", symbol="XAUUSD",
    )
    assert result["close_reason"] == "stop_touche"
    assert result["return_pct"] < 0
    assert result["close_time"] == "2026-01-01 05:05:00"


def test_simulate_exit_closes_at_session_end():
    candles_5min = [
        {"time": "2026-01-01 05:00:00", "open": 1975.0, "high": 1975.0, "low": 1975.0, "close": 1975.0,
         "tick_volume": 10, "spread": 0},
        {"time": "2026-01-01 22:00:00", "open": 1980.0, "high": 1982.0, "low": 1978.0, "close": 1981.0,
         "tick_volume": 10, "spread": 2},
    ]
    result = odds_check._simulate_exit(
        candles_5min, entry_index=1, direction="achat", entry_price=1975.0, initial_stop=1960.0,
        entry_time="2026-01-01 05:00:00", symbol="XAUUSD",
    )
    assert result["close_reason"] == "fin_session"
    assert result["return_pct"] > 0  # 1981 > 1975, achat gagnant
    assert result["close_time"] == "2026-01-01 22:00:00"


def test_simulate_exit_closes_at_end_of_available_history_when_unresolved():
    candles_5min = [
        {"time": "2026-01-01 05:00:00", "open": 1975.0, "high": 1975.0, "low": 1975.0, "close": 1975.0,
         "tick_volume": 10, "spread": 0},
        {"time": "2026-01-01 05:05:00", "open": 1976.0, "high": 1977.0, "low": 1974.0, "close": 1976.0,
         "tick_volume": 10, "spread": 2},
    ]
    result = odds_check._simulate_exit(
        candles_5min, entry_index=1, direction="achat", entry_price=1975.0, initial_stop=1960.0,
        entry_time="2026-01-01 05:00:00", symbol="XAUUSD",
    )
    assert result["close_reason"] == "fin_historique"
    # close_price reste le prix de marche brut (non ajuste du spread) --
    # seul return_pct deduit le cout du spread, voir _spread_cost_pct.
    assert result["close_price"] == 1976.0
    assert result["return_pct"] == pytest.approx(
        (1976.0 - 1975.0) / 1975.0 * 100 - (2 * backtest.XAUUSD_POINT_SIZE) / 1975.0 * 100)
    assert result["close_time"] == "2026-01-01 05:05:00"


def test_simulate_exit_blends_partial_leg_target_with_runner_leg_stop():
    # XAUUSD : partial_tp_pct=0.7, partial_tp_r=2.0. distance = 1975-1960 = 15
    # -> cible partielle = 1975 + 2.0*15 = 2005. Bougie 1 : touche la cible
    # partielle (high=2006) SANS toucher le stop runner (low=1970 > 1960) --
    # la jambe partielle se cloture ici. Bougie 2 : touche le stop runner,
    # toujours a 1960 (compute_trailing_stop ne peut pas encore calculer
    # avec seulement 3 bougies 5min dans l'historique -- EMA200_PERIOD=200,
    # voir le nouveau test ci-dessous pour le cas ou le suivi s'active
    # vraiment). Spread nul sur ces 2 bougies pour garder le calcul simple.
    candles_5min = [
        {"time": "2026-01-01 05:00:00", "open": 1975.0, "high": 1975.0, "low": 1975.0, "close": 1975.0,
         "tick_volume": 10, "spread": 0},
        {"time": "2026-01-01 05:05:00", "open": 1980.0, "high": 2006.0, "low": 1970.0, "close": 2000.0,
         "tick_volume": 10, "spread": 0},
        {"time": "2026-01-01 05:10:00", "open": 2000.0, "high": 2001.0, "low": 1958.0, "close": 1959.0,
         "tick_volume": 10, "spread": 0},
    ]
    result = odds_check._simulate_exit(
        candles_5min, entry_index=1, direction="achat", entry_price=1975.0, initial_stop=1960.0,
        entry_time="2026-01-01 05:00:00", symbol="XAUUSD",
    )
    partial_return = (2005.0 - 1975.0) / 1975.0 * 100  # cible partielle touchee, bougie 1
    runner_return = (1960.0 - 1975.0) / 1975.0 * 100   # stop runner touche (pas encore suivi), bougie 2
    expected = 0.7 * partial_return + 0.3 * runner_return
    assert result["return_pct"] == pytest.approx(expected)
    assert result["close_reason"] == "stop_touche"
    assert result["close_price"] == 1960.0
    assert result["close_time"] == "2026-01-01 05:10:00"


def _make_trending_session(base_day: str, start_hour: int, n_bars: int, start_price: float,
                            price_step: float, spread: float = 1.0) -> list[dict]:
    """Construit `n_bars` bougies 5min consecutives a partir de
    `base_day`/`start_hour` (UTC), prix = start_price + i*price_step
    (haute/basse = +/-0.5 autour du close pour laisser un peu d'amplitude
    sans jamais toucher un stop). Utilise pour construire plusieurs jours
    d'historique REEL (pas un mock des indicateurs) afin de faire
    reellement s'activer compute_trailing_stop (seuil EMA200_PERIOD=200
    bougies 15min, soit >= 600 bougies 5min d'affilee)."""
    out = []
    t = datetime.fromisoformat(f"{base_day}T{start_hour:02d}:00:00").replace(tzinfo=timezone.utc)
    for i in range(n_bars):
        price = start_price + i * price_step
        out.append({
            "time": t.strftime("%Y-%m-%d %H:%M:%S"), "open": price, "high": price + 0.5,
            "low": price - 0.5, "close": price, "tick_volume": 10, "spread": spread,
        })
        t += timedelta(minutes=5)
    return out


def test_simulate_exit_trailing_stop_actually_tightens_with_enough_real_history():
    # Reproduit le defaut trouve en revue finale de branche : avec
    # seulement les bougies depuis l'entree, compute_trailing_stop ne
    # calcule jamais (< 200 bougies 15min dans une seule session). Ici,
    # 3 jours complets de bougies 5min AVANT l'entree donnent assez
    # d'historique pour EMA200 ; une monte franche puis un RETOUR BRUTAL
    # jusqu'au niveau du stop INITIAL tranche, de maniere decisive, si le
    # suiveur s'est vraiment resserre : le retour (-90 sur 30 bougies,
    # bien plus profond que les -15 de distance initiale) est assez
    # severe pour toucher N'IMPORTE QUEL stop, trailed ou non -- sans
    # suivi reel (bug), il touche exactement le stop initial
    # (close_price == initial_stop) ; avec un suivi reellement actif, le
    # stop s'est deja resserre AU-DESSUS de ce niveau pendant la monte,
    # donc le toucher se fait forcement a un prix strictement superieur.
    pre_entry = _make_trending_session("2026-01-01", 0, 864, 1900.0, 0.01)  # 3 jours, prix quasi plat
    entry_candle = pre_entry[-1]
    initial_stop = entry_candle["close"] - 15.0
    uptrend = _make_trending_session("2026-01-04", 0, 100, entry_candle["close"] + 0.5, 0.5)  # +50 sur 100 bougies
    peak_price = uptrend[-1]["close"]
    pullback = _make_trending_session("2026-01-04", 8, 30, peak_price - 3.0, -3.0)  # retour de -90 sur 30 bougies
    candles_5min = pre_entry + uptrend + pullback
    entry_index = len(pre_entry)

    result = odds_check._simulate_exit(
        candles_5min, entry_index=entry_index, direction="achat", entry_price=entry_candle["close"],
        initial_stop=initial_stop, entry_time=entry_candle["time"], symbol="XAUUSD",
    )
    assert result["close_reason"] == "stop_touche"
    assert result["close_price"] > initial_stop  # preuve decisive : sinon == initial_stop exactement


def test_historical_odds_for_setup_excludes_candles_inside_the_entry_bucket(monkeypatch):
    # Biais d'anticipation trouve en revue finale de branche : le bucket
    # 15min d'entree (05:00-05:15) couvre 3 bougies 5min (05:00, 05:05,
    # 05:10) dont le CLOSE (celui de 05:10) determine entry_price. La
    # simulation ne doit commencer qu'a la bougie de 05:15 (premiere APRES
    # la cloture du bucket), jamais inclure 05:05/05:10 une deuxieme fois.
    entries = [{"entry_time": "2026-01-01 05:00:00", "entry_price": 1975.0, "stop_loss": 1960.0}] + \
        [{"entry_time": f"2026-01-{i:02d} 05:00:00", "entry_price": 1975.0, "stop_loss": 1960.0} for i in range(2, 21)]
    monkeypatch.setattr(odds_check, "_find_historical_entries", lambda candles, direction, symbol="XAUUSD": entries)

    captured_entry_indices = []

    def fake_simulate_exit(candles_5min, entry_index, direction, entry_price, initial_stop, entry_time, symbol="XAUUSD"):
        captured_entry_indices.append((entry_time, entry_index, candles_5min[entry_index]["time"] if entry_index < len(candles_5min) else None))
        day = entry_time.split(" ")[0]
        return {"close_reason": "fin_session", "close_price": 1985.0, "return_pct": 0.5,
                "close_time": f"{day} 07:00:00"}

    monkeypatch.setattr(odds_check, "_simulate_exit", fake_simulate_exit)

    candles_5min = [
        {"time": "2026-01-01 05:00:00", "open": 1975.0, "high": 1975.0, "low": 1975.0, "close": 1975.0, "spread": 0},
        {"time": "2026-01-01 05:05:00", "open": 1975.0, "high": 1975.0, "low": 1975.0, "close": 1975.0, "spread": 0},
        {"time": "2026-01-01 05:10:00", "open": 1975.0, "high": 1975.0, "low": 1975.0, "close": 1975.0, "spread": 0},
        {"time": "2026-01-01 05:15:00", "open": 1975.0, "high": 1975.0, "low": 1975.0, "close": 1975.0, "spread": 0},
    ]
    odds_check.historical_odds_for_setup(candles_5min, "achat", min_sample_size=20)
    first_call_time, first_entry_index, first_candle_time = captured_entry_indices[0]
    assert first_call_time == "2026-01-01 05:00:00"
    # La bougie a l'index trouve doit etre a 05:15 (la premiere a ou apres
    # la cloture du bucket 05:00-05:15), jamais 05:05 ou 05:10.
    assert first_candle_time == "2026-01-01 05:15:00"
```

`import pytest` and `from datetime import datetime, timedelta, timezone`
are already present at the top of `tests/gold_bot/test_odds_check.py`
(from Task 5/6) — nothing to add there. Add
`import gold_bot.backtest as backtest` (needed for the
`XAUUSD_POINT_SIZE` reference in the updated
`test_simulate_exit_closes_at_end_of_available_history_when_unresolved`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_odds_check.py -v`
Expected: FAIL (signature mismatch — `_simulate_exit` doesn't accept
`entry_index` yet; `historical_odds_for_setup` doesn't exclude in-bucket
candles yet).

- [ ] **Step 3: Rewrite `_simulate_exit` and `historical_odds_for_setup`**

Add the import at the top of `gold_bot/odds_check.py` (alongside the
existing `vwap_reversion` import) and update the module docstring's
"aucun appel reseau/broker" line to clarify local file caching is fine
(mirrors the precedent already set by `gold_bot.risk.CircuitBreaker`'s
own local state persistence):

```python
# gold_bot/odds_check.py
# Verification des cotes historiques avant l'ouverture d'un trade --
# voir docs/superpowers/specs/2026-10-22-gold-bot-risk-gate-design.md.
# Module pur vis-a-vis du RESEAU (aucun appel broker/MetaApi) : recoit
# un historique de bougies 5min deja recupere (par gold_bot.loop) et
# rejoue les memes mecaniques de decision/sortie que
# gold_bot.bot.decide_and_act_vwap, pour estimer les cotes reelles du
# meme type de signal dans le passe. Fait cependant de la lecture/
# ecriture de cache LOCALE (verdict memorise, voir
# historical_odds_for_setup_cached) -- meme precedent que
# gold_bot.risk.CircuitBreaker, qui persiste deja son propre etat
# localement depuis une fonction par ailleurs "pure" cote reseau.
import json
import os
from datetime import datetime, timezone

import gold_bot.backtest as backtest
import gold_bot.vwap_reversion as vwap_reversion
```

Entirely replace the existing `_simulate_exit` function (everything from
`def _simulate_exit(candles_after_entry: ...` through its closing
`return {...}`) with:

```python
# Fenetre glissante de bougies 5min passee a compute_trailing_stop a
# chaque pas, pour reproduire fidelement ce qu'une boucle live voit
# reellement a un instant donne (gold_bot.loop.CANDLES_FETCH_LIMIT =
# 1000 ; valeur dupliquee ici plutot qu'importee pour eviter un cycle
# d'import odds_check -> loop -> bot -> odds_check, ce module etant
# repris par gold_bot.bot, lui-meme importe par gold_bot.loop).
TRAILING_STOP_WINDOW_SIZE = 1000


def _spread_cost_pct(entry_price: float, spread_points: float, symbol: str) -> float:
    """Cout du spread en % du prix d'entree, meme convention que
    gold_bot.backtest._compute_return. Seul XAUUSD a une taille de point
    verifiee (backtest.XAUUSD_POINT_SIZE, "coté à 2 décimales chez ce
    courtier" -- hypothese documentee dans backtest.py, jamais verifiee
    pour XAGUSD). Pour tout autre symbole, ne deduit rien plutot que
    d'appliquer une conversion non verifiee -- trouve en revue finale de
    branche (la spec promettait cette deduction, oubliee dans une
    version anterieure de ce module)."""
    if symbol != "XAUUSD":
        return 0.0
    return (spread_points * backtest.XAUUSD_POINT_SIZE) / entry_price * 100


def _simulate_exit(candles_5min: list[dict], entry_index: int, direction: str, entry_price: float,
                    initial_stop: float, entry_time: str, symbol: str = "XAUUSD") -> dict:
    """Rejoue la sortie reelle bougie par bougie a partir de
    `candles_5min[entry_index]` (la premiere bougie 5min A OU APRES la
    cloture du bucket 15min d'entree -- voir historical_odds_for_setup,
    qui calcule cet index ; ne JAMAIS passer l'index d'une bougie a
    l'interieur du bucket d'entree lui-meme, biais d'anticipation trouve
    en revue finale de branche).

    A chaque pas, le stop suiveur de la jambe runner est recalcule via
    vwap_reversion.compute_trailing_stop sur une fenetre glissante de
    jusqu'a TRAILING_STOP_WINDOW_SIZE VRAIES bougies 5min se terminant a
    la bougie courante (`candles_5min[max(0, i-WINDOW+1):i+1]`) --
    EXACTEMENT ce qu'une boucle live lui donnerait a ce moment-la (voir
    gold_bot.loop.CANDLES_FETCH_LIMIT, gold_bot.bot.decide_and_act_vwap).
    Une version anterieure de cette fonction ne lui donnait qu'une
    bougie d'entree synthetique plus les bougies depuis l'entree : avec
    au plus ~88 bougies 15min dans une session (0h-22h UTC), le seuil
    EMA200_PERIOD=200 de compute_trailing_stop n'etait JAMAIS atteint en
    pratique, et le stop suiveur ne suivait donc jamais -- trouve en
    revue finale de branche. Consequence residuelle acceptee : les toutes
    premieres entrees de la fenetre de 90 jours recuperee
    (HISTORICAL_ODDS_WINDOW_DAYS, gold_bot.loop), qui n'ont pas encore
    ~2 jours d'historique REEL avant elles dans les donnees recuperees,
    degradent de la meme facon (compute_trailing_stop renvoie None, le
    stop reste fixe jusqu'a ce que suffisamment d'historique existe) --
    c'est attendu (meme degradation qu'un bot qui demarre), pas une
    regression de ce correctif. Autre ecart residuel documente : l'EMA200
    calculee ici repart de zero a chaque fenetre glissante plutot que
    d'etre continue depuis le tout debut de l'historique recupere comme
    en live continu -- poids residuel faible (~3.6% au bout de 200
    bougies) mais reel, accepte pour eviter de repasser des milliers de
    bougies a chaque pas (voir aussi historical_odds_for_setup_cached,
    qui rend ce cout tolerable en ne le payant qu'une fois par
    rafraichissement du cache d'historique plutot qu'a chaque cycle).

    Modelise les DEUX jambes de decide_and_act_vwap quand
    get_symbol_params(symbol)["partial_tp_pct"] > 0 (XAUUSD et XAGUSD
    aujourd'hui) : la jambe partielle (stop FIXE a `initial_stop`, cible
    fixe a `entry_price +/- partial_tp_r * distance`, stop gagnant en
    cas de toucher simultane avec sa propre cible) et la jambe runner
    (stop suiveur ci-dessus, jamais de cible). Les deux se cloturent
    aussi de force a la fin de session (SESSION_END_HOUR_UTC, comme
    TOUTES les positions `matching` dans decide_and_act_vwap), ou a la
    fin de l'historique fourni.

    Le retour renvoye (`return_pct`) est la moyenne ponderee des deux
    jambes par `partial_tp_pct`, APRES deduction du cout du spread (voir
    _spread_cost_pct) -- la spec promettait cette deduction, oubliee
    dans une version anterieure. Si `partial_tp_pct <= 0`, tout le
    volume reste en jambe unique runner.

    `close_reason`/`close_price` refletent la jambe RUNNER specifiquement
    (pas un melange). `close_time`, en revanche, est le PLUS TARDIF des
    deux cloture (max partielle/runner) -- avec le stop suiveur
    desormais reellement actif, la jambe partielle peut survivre plus
    longtemps que le runner (elle ne se cloture que sur son propre stop
    fixe, sa cible fixe, ou la fin de session), et c'est elle qui
    determine la duree reelle de blocage d'un nouveau signal cote
    historical_odds_for_setup (decide_and_act_vwap bloque tant que
    N'IMPORTE QUELLE jambe `matching` reste ouverte) -- corrige une
    approximation de la version precedente, trouvee en revue finale."""
    is_achat = direction == "achat"
    params = vwap_reversion.get_symbol_params(symbol)
    distance = abs(entry_price - initial_stop)
    partial_tp_pct = params["partial_tp_pct"]
    has_partial_leg = partial_tp_pct > 0
    if has_partial_leg:
        partial_target = (entry_price + params["partial_tp_r"] * distance if is_achat
                           else entry_price - params["partial_tp_r"] * distance)

    def _return_pct(price, spread_points):
        raw = ((price - entry_price) / entry_price * 100 if is_achat
               else (entry_price - price) / entry_price * 100)
        return raw - _spread_cost_pct(entry_price, spread_points, symbol)

    stop = initial_stop
    partial_return = partial_close_time = None
    runner_return = runner_close_reason = runner_close_price = runner_close_time = None

    for i in range(entry_index, len(candles_5min)):
        candle = candles_5min[i]
        hour = datetime.fromisoformat(candle["time"].replace(" ", "T")).replace(tzinfo=timezone.utc).hour
        session_ended = hour >= vwap_reversion.SESSION_END_HOUR_UTC
        spread = candle.get("spread", 0.0)

        if has_partial_leg and partial_return is None:
            sl_hit = candle["low"] <= initial_stop if is_achat else candle["high"] >= initial_stop
            if sl_hit:
                partial_return, partial_close_time = _return_pct(initial_stop, spread), candle["time"]
            else:
                tp_hit = candle["high"] >= partial_target if is_achat else candle["low"] <= partial_target
                if tp_hit:
                    partial_return, partial_close_time = _return_pct(partial_target, spread), candle["time"]
                elif session_ended:
                    partial_return, partial_close_time = _return_pct(candle["close"], spread), candle["time"]

        if runner_return is None:
            sl_hit = candle["low"] <= stop if is_achat else candle["high"] >= stop
            if sl_hit:
                runner_return = _return_pct(stop, spread)
                runner_close_reason, runner_close_price, runner_close_time = "stop_touche", stop, candle["time"]
            elif session_ended:
                runner_return = _return_pct(candle["close"], spread)
                runner_close_reason = "fin_session"
                runner_close_price, runner_close_time = candle["close"], candle["time"]
            else:
                window = candles_5min[max(0, i - TRAILING_STOP_WINDOW_SIZE + 1):i + 1]
                new_stop = vwap_reversion.compute_trailing_stop(
                    window, direction, entry_time, entry_price, stop, symbol=symbol)
                if new_stop is not None:
                    stop = new_stop

        if runner_return is not None and (not has_partial_leg or partial_return is not None):
            break

    if runner_return is None:
        last = candles_5min[-1]
        runner_return = _return_pct(last["close"], last.get("spread", 0.0))
        runner_close_reason, runner_close_price, runner_close_time = "fin_historique", last["close"], last["time"]
    if has_partial_leg and partial_return is None:
        last = candles_5min[-1]
        partial_return, partial_close_time = _return_pct(last["close"], last.get("spread", 0.0)), last["time"]

    blended_return = (partial_tp_pct * partial_return + (1 - partial_tp_pct) * runner_return
                       if has_partial_leg else runner_return)
    dedup_close_time = max(runner_close_time, partial_close_time) if has_partial_leg else runner_close_time

    return {"close_reason": runner_close_reason, "close_price": runner_close_price,
            "return_pct": blended_return, "close_time": dedup_close_time}
```

Entirely replace `historical_odds_for_setup`'s body from `entries =
_find_historical_entries(...)` through its final `return {...}` with:

```python
def historical_odds_for_setup(candles_5min: list[dict], direction: str, symbol: str = "XAUUSD",
                               min_sample_size: int = 20) -> dict:
    """Point d'entree public -- voir docs/superpowers/specs/2026-10-22-
    gold-bot-risk-gate-design.md. `min_sample_size` sous ce seuil ->
    verdict "insufficient_history", NE bloque jamais le trade. Au-dela,
    "unfavorable" (bloque) si et seulement si l'esperance moyenne nette
    (spread deduit, voir _simulate_exit) est negative ou nulle.

    Pour chaque entree detectee par _find_historical_entries (dont
    `entry_time` est le DEBUT du bucket 15min, voir sa docstring), la
    simulation ne doit commencer qu'a la toute premiere bougie 5min A OU
    APRES la CLOTURE de ce bucket (`entry_time + 15min`) -- jamais
    pendant le bucket lui-meme, qui a deja determine `entry_price` (son
    propre prix de cloture). Inclure ces bougies une deuxieme fois dans
    la simulation serait un biais d'anticipation -- trouve en revue
    finale de branche."""
    entries = _find_historical_entries(candles_5min, direction, symbol=symbol)
    if len(entries) < min_sample_size:
        return {"sample_size": len(entries), "win_rate_pct": None, "avg_return_pct": None,
                "verdict": "insufficient_history"}

    outcomes = []
    blocked_until = None
    for entry in entries:
        entry_dt = datetime.fromisoformat(entry["entry_time"].replace(" ", "T")).replace(tzinfo=timezone.utc)
        if blocked_until is not None and entry_dt < blocked_until:
            continue
        bucket_end = entry_dt + timedelta(minutes=15)
        entry_index = None
        for i, c in enumerate(candles_5min):
            c_time = c.get("time")
            if c_time is None:
                continue
            try:
                c_dt = datetime.fromisoformat(c_time.replace(" ", "T")).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if c_dt >= bucket_end:
                entry_index = i
                break
        if entry_index is None:
            continue
        outcome = _simulate_exit(
            candles_5min, entry_index, direction, entry["entry_price"], entry["stop_loss"],
            entry["entry_time"], symbol=symbol)
        outcomes.append(outcome)
        blocked_until = datetime.fromisoformat(outcome["close_time"].replace(" ", "T")).replace(tzinfo=timezone.utc)

    if len(outcomes) < min_sample_size:
        return {"sample_size": len(outcomes), "win_rate_pct": None, "avg_return_pct": None,
                "verdict": "insufficient_history"}

    avg_return_pct = sum(o["return_pct"] for o in outcomes) / len(outcomes)
    win_rate_pct = sum(1 for o in outcomes if o["return_pct"] > 0) / len(outcomes) * 100
    verdict = "unfavorable" if avg_return_pct <= 0 else "favorable"
    return {"sample_size": len(outcomes), "win_rate_pct": win_rate_pct,
            "avg_return_pct": avg_return_pct, "verdict": verdict}


def _candles_fingerprint(candles_5min: list[dict]) -> str:
    """Identifiant bon marche (O(1), pas un hash de tout le contenu)
    pour detecter si `candles_5min` a change depuis le dernier appel --
    change des que loop.py rafraichit son cache d'historique (nouvelle
    premiere/derniere bougie ou nombre de bougies different), reste
    stable sinon (meme liste reutilisee a chaque cycle tant que le
    cache de loop.py n'expire pas, voir
    loop.HISTORICAL_ODDS_CACHE_MAX_AGE_SECONDS)."""
    if not candles_5min:
        return "empty"
    return f"{len(candles_5min)}:{candles_5min[0].get('time')}:{candles_5min[-1].get('time')}"


def _verdict_cache_path(symbol: str, direction: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         f"odds_verdict_cache_{symbol.lower()}_{direction}.json")


def historical_odds_for_setup_cached(candles_5min: list[dict], direction: str, symbol: str = "XAUUSD",
                                      min_sample_size: int = 20) -> dict:
    """Memoise historical_odds_for_setup par (symbol, direction,
    empreinte de candles_5min, voir _candles_fingerprint). Le replay
    complet (recalcul du stop suiveur sur une fenetre glissante REELLE a
    chaque bougie depuis le correctif de revue finale de branche, voir
    _simulate_exit) est devenu nettement plus couteux -- inutile de le
    refaire a chaque cycle (60s) tant que l'historique sous-jacent n'a
    pas change (rafraichi au plus une fois par 24h cote loop.py).
    gold_bot.bot.decide_and_act_vwap appelle CETTE fonction, jamais la
    version non memoisee, directement."""
    fingerprint = _candles_fingerprint(candles_5min)
    path = _verdict_cache_path(symbol, direction)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("fingerprint") == fingerprint:
                return cached["verdict"]
        except Exception:
            pass
    verdict = historical_odds_for_setup(candles_5min, direction, symbol=symbol, min_sample_size=min_sample_size)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"fingerprint": fingerprint, "verdict": verdict}, f)
    except OSError:
        pass
    return verdict
```

Also add `from datetime import timedelta` to the existing `from datetime
import datetime, timezone` line at the top of `gold_bot/odds_check.py`
(needed by `historical_odds_for_setup`'s `bucket_end` computation).

- [ ] **Step 4: Run the `odds_check.py` tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_odds_check.py -v`
Expected: PASS (all tests, including the two new ones).

- [ ] **Step 5: Write the failing test for `historical_odds_for_setup_cached`**

Add to `tests/gold_bot/test_odds_check.py`:

```python
def test_historical_odds_for_setup_cached_reuses_result_for_same_fingerprint(monkeypatch, tmp_path):
    call_count = {"n": 0}

    def fake_historical_odds_for_setup(candles, direction, symbol="XAUUSD", min_sample_size=20):
        call_count["n"] += 1
        return {"sample_size": 25, "win_rate_pct": 60.0, "avg_return_pct": 0.3, "verdict": "favorable"}

    monkeypatch.setattr(odds_check, "historical_odds_for_setup", fake_historical_odds_for_setup)
    monkeypatch.setattr(odds_check, "_verdict_cache_path", lambda symbol, direction: str(tmp_path / "verdict.json"))

    candles = [{"time": "2026-01-01 05:00:00"}, {"time": "2026-01-01 05:05:00"}]
    result1 = odds_check.historical_odds_for_setup_cached(candles, "achat")
    result2 = odds_check.historical_odds_for_setup_cached(candles, "achat")  # meme empreinte
    assert result1 == result2
    assert call_count["n"] == 1  # pas recalcule la deuxieme fois


def test_historical_odds_for_setup_cached_recomputes_when_candles_change(monkeypatch, tmp_path):
    call_count = {"n": 0}

    def fake_historical_odds_for_setup(candles, direction, symbol="XAUUSD", min_sample_size=20):
        call_count["n"] += 1
        return {"sample_size": 25, "win_rate_pct": 60.0, "avg_return_pct": 0.3, "verdict": "favorable"}

    monkeypatch.setattr(odds_check, "historical_odds_for_setup", fake_historical_odds_for_setup)
    monkeypatch.setattr(odds_check, "_verdict_cache_path", lambda symbol, direction: str(tmp_path / "verdict.json"))

    odds_check.historical_odds_for_setup_cached(
        [{"time": "2026-01-01 05:00:00"}, {"time": "2026-01-01 05:05:00"}], "achat")
    odds_check.historical_odds_for_setup_cached(
        [{"time": "2026-01-02 05:00:00"}, {"time": "2026-01-02 05:05:00"}], "achat")  # autre empreinte
    assert call_count["n"] == 2  # recalcule, l'historique a change
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_odds_check.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 7: Write the failing tests for `bot.py`'s reordered gate + cached call**

In `tests/gold_bot/test_bot.py`, find the two tests
`test_decide_and_act_vwap_blocked_by_unfavorable_odds_check` and
`test_decide_and_act_vwap_opens_when_odds_check_favorable` (added in
Task 7) and update BOTH of their `monkeypatch.setattr(bot.odds_check,
"historical_odds_for_setup", ...)` lines to patch
`"historical_odds_for_setup_cached"` instead (same lambda signature,
just the attribute name changes) — `decide_and_act_vwap` now calls the
cached wrapper. Then add one new test proving the circuit-breaker check
now runs BEFORE the odds check (so a tripped circuit breaker is never
masked by an "unfavorable odds" message, and the expensive replay is
skipped when the free check would already block):

```python
def test_decide_and_act_vwap_checks_circuit_breaker_before_odds_check(monkeypatch):
    state = _vwap_state(close=1975.0, vwap=2010.0, vwap_std=20.0, ema200=1900.0)
    monkeypatch.setattr(bot.vwap_reversion, "latest_state", lambda candles: state)

    def _fail_if_called(*a, **k):
        raise AssertionError("historical_odds_for_setup_cached ne doit pas etre appele "
                              "quand le coupe-circuit est deja declenche")
    monkeypatch.setattr(bot.odds_check, "historical_odds_for_setup_cached", _fail_if_called)

    from datetime import datetime, timezone
    cb = risk.CircuitBreaker(threshold_pct=0.10, now_fn=lambda: datetime(2026, 9, 10, tzinfo=timezone.utc))
    cb.check(10000)  # reference de depart du jour
    result = bot.decide_and_act_vwap(
        [{"dummy": True}], contract_size=100, volume_step=0.01, min_volume=0.01, max_volume=500,
        balance=10000, equity=8900, open_positions=[], circuit_breaker=cb,  # -11%, coupe-circuit declenche
        historical_candles=[{"dummy": True}],
    )
    assert result == {"action": "aucune", "reason": "coupe-circuit journalier déclenché"}
```

- [ ] **Step 8: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_bot.py -k odds_check -v`
Expected: the new test FAILS (the function isn't reordered yet — the
odds-check mock would currently be reached and raise `AssertionError`
instead of the circuit breaker short-circuiting first); the two updated
tests FAIL with `AttributeError` (`historical_odds_for_setup_cached`
doesn't exist on `bot.odds_check` yet from `bot.py`'s perspective, since
`bot.py` still calls the uncached name).

- [ ] **Step 9: Reorder the gate and call the cached wrapper in `bot.py`**

In `gold_bot/bot.py`, find the block (added in Task 7) that currently
reads:

```python
    if historical_candles is not None:
        odds = odds_check.historical_odds_for_setup(historical_candles, direction, symbol=symbol)
        if odds["verdict"] == "unfavorable":
            return {"action": "aucune", "reason": "cotes historiques defavorables pour ce type de signal"}

    if not circuit_breaker.can_open_position(equity):
        return {"action": "aucune", "reason": "coupe-circuit journalier déclenché"}
```

Replace it with (order swapped — cheap, in-memory check first; the
spec's original rationale for odds-check-first was "avant de consommer
un appel réseau pour le solde", which turned out to be incorrect —
`run_cycle` already has `equity` before calling `decide_and_act_vwap`,
no network call is saved either way — found in final branch review):

```python
    if not circuit_breaker.can_open_position(equity):
        return {"action": "aucune", "reason": "coupe-circuit journalier déclenché"}

    if historical_candles is not None:
        odds = odds_check.historical_odds_for_setup_cached(historical_candles, direction, symbol=symbol)
        if odds["verdict"] == "unfavorable":
            return {"action": "aucune", "reason": "cotes historiques defavorables pour ce type de signal"}
```

- [ ] **Step 10: Run the `bot.py` tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_bot.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 11: Write the failing tests for `loop.py`'s hardened cache + fixed trade-counting**

Add to `tests/gold_bot/test_loop.py`:

```python
def test_load_or_fetch_historical_candles_never_raises_on_malformed_cache_content(monkeypatch, tmp_path):
    # JSON valide mais pas un dict (TypeError sur cached["fetched_at"]
    # avec l'ancien tuple d'exceptions precis, puisque l'indexation
    # d'une liste par une chaine leve TypeError, pas KeyError) -- doit
    # degrader vers un nouveau fetch plutot que de lever. Trouve en
    # revue finale de branche : la docstring promettait "ne leve
    # jamais", un tuple d'exceptions trop precis ne le garantissait pas
    # vraiment.
    cache_path = tmp_path / "hist.json"
    cache_path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(loop, "_historical_candles_cache_path", lambda symbol: str(cache_path), raising=False)
    monkeypatch.setattr(loop.backtest, "fetch_gold_candles_range", lambda *a, **k: [{"fake": "history"}])

    result = loop._load_or_fetch_historical_candles(
        "token", "account", "XAUUSD", "london", datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc))

    assert result == [{"fake": "history"}]  # cache malforme ignore, nouveau fetch reussi utilise


def test_load_or_fetch_historical_candles_backs_off_after_a_fetch_failure(monkeypatch, tmp_path):
    cache_path = str(tmp_path / "hist.json")
    monkeypatch.setattr(loop, "_historical_candles_cache_path", lambda symbol: cache_path, raising=False)
    fetch_calls = []

    def failing_fetch(*a, **k):
        fetch_calls.append(1)
        raise RuntimeError("MetaApi 429")

    monkeypatch.setattr(loop.backtest, "fetch_gold_candles_range", failing_fetch)
    now1 = datetime(2026, 10, 22, 10, 0, 0, tzinfo=timezone.utc)
    result1 = loop._load_or_fetch_historical_candles("token", "account", "XAUUSD", "london", now1)
    assert result1 == []
    assert len(fetch_calls) == 1
    # 10 minutes plus tard, bien sous le backoff d'echec (1h) : ne doit
    # PAS retenter un fetch.
    now2 = datetime(2026, 10, 22, 10, 10, 0, tzinfo=timezone.utc)
    result2 = loop._load_or_fetch_historical_candles("token", "account", "XAUUSD", "london", now2)
    assert result2 == []
    assert len(fetch_calls) == 1
    # 2 heures plus tard, au-dela du backoff d'echec : doit retenter.
    now3 = datetime(2026, 10, 22, 12, 10, 0, tzinfo=timezone.utc)
    loop._load_or_fetch_historical_candles("token", "account", "XAUUSD", "london", now3)
    assert len(fetch_calls) == 2


def test_run_cycle_counts_trade_when_partial_leg_succeeds_but_runner_leg_fails(monkeypatch, tmp_path):
    # Reprend le meme montage que test_run_cycle_records_trade_opened_once_for_partial_and_runner_legs
    # (Task 4), mais fait echouer SEULEMENT l'ordre de la jambe runner --
    # une vraie position (la jambe partielle) a tout de meme ete ouverte,
    # elle doit donc etre comptee. Trouve en revue finale de branche :
    # l'ancienne condition (not had_error and any(...)) sous-comptait ce
    # cas, puisque had_error devient vrai des qu'UNE SEULE etape echoue
    # dans le lot.
    cb = risk.CircuitBreaker(persist_path=str(tmp_path / "cb_state.json"))
    assert cb._trades_opened_today == 0
    # ... (meme montage de base que les autres tests de run_cycle dans ce
    # fichier -- decide_and_act_vwap renvoie 2 steps "ouverture_simulee"
    # (partielle + runner), mais broker.place_market_order reussit pour
    # la partielle et leve une exception pour le runner)

    loop.run_cycle("token", "account", cb, now=datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc))
    assert cb._trades_opened_today == 1
```

**Note for the implementer**: for
`test_run_cycle_counts_trade_when_partial_leg_succeeds_but_runner_leg_fails`,
mirror `test_run_cycle_records_trade_opened_once_for_partial_and_runner_legs`'s
exact setup (added in Task 4's fix round — read it first), changing only
the runner leg's execution outcome to a failure.

- [ ] **Step 12: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_loop.py -k "historical_candles or partial_leg_succeeds" -v`
Expected: FAIL (hardening/backoff/counting-fix not implemented yet).

- [ ] **Step 13: Harden `_load_or_fetch_historical_candles` and fix trade-counting in `loop.py`**

Add a new constant next to the existing `HISTORICAL_ODDS_CACHE_MAX_AGE_SECONDS`:

```python
HISTORICAL_ODDS_CACHE_MAX_AGE_SECONDS = 24 * 3600
# Ne retente pas un fetch de 90 jours (~20 pages MetaApi) plus d'une fois
# par heure apres un echec -- MetaApi a deja renvoye des 429 dans ce
# projet ; retenter a chaque cycle (60s) pendant une panne prolongee
# serait pire que l'absence temporaire de verification des cotes.
HISTORICAL_ODDS_FAILURE_BACKOFF_SECONDS = 3600
HISTORICAL_ODDS_WINDOW_DAYS = 90
```

Entirely replace `_load_or_fetch_historical_candles`'s body with:

```python
def _load_or_fetch_historical_candles(token: str, account_id: str, symbol: str,
                                       region: str, now_dt: datetime) -> list[dict]:
    """Charge l'historique mis en cache pour `symbol` s'il a moins de
    HISTORICAL_ODDS_CACHE_MAX_AGE_SECONDS (succes) ou
    HISTORICAL_ODDS_FAILURE_BACKOFF_SECONDS (dernier essai en echec),
    sinon le (re)recupere via backtest.fetch_gold_candles_range. Ne leve
    JAMAIS : `except Exception` volontairement large sur toute la
    lecture/parsing du cache (fichier corrompu, JSON valide mais pas un
    dict, horodatage sans fuseau...) plutot qu'un tuple d'exceptions
    precis -- trouve insuffisant en revue finale de branche, puisque
    cette fonction est appelee AVANT decide_and_act_vwap et qu'une
    exception non capturee ici bloquerait alors aussi la gestion des
    positions deja ouvertes (stop suiveur, cloture de fin de session),
    pas seulement la recherche de nouveaux signaux."""
    path = _historical_candles_cache_path(symbol)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cached = json.load(f)
            fetched_at = datetime.fromisoformat(cached["fetched_at"].replace("Z", "+00:00"))
            age = (now_dt - fetched_at).total_seconds()
            if cached.get("is_failure_backoff"):
                if age < HISTORICAL_ODDS_FAILURE_BACKOFF_SECONDS:
                    return []
            elif age < HISTORICAL_ODDS_CACHE_MAX_AGE_SECONDS:
                return cached["candles"]
        except Exception:
            pass
    try:
        candles = backtest.fetch_gold_candles_range(
            token, account_id, now_dt - timedelta(days=HISTORICAL_ODDS_WINDOW_DAYS), now_dt,
            region=region, symbol=symbol,
        )
    except Exception:
        _save_cache({"candles": [], "fetched_at": _now_iso(), "is_failure_backoff": True}, path)
        return []
    _save_cache({"candles": candles, "fetched_at": _now_iso()}, path)
    return candles
```

(The only functional changes from the Task 7 version: the cache-read
`try` now catches `Exception` broadly instead of a specific tuple;
`fetched_at` is parsed with `.replace("Z", "+00:00")` first, matching
the rest of the codebase's convention; and a failed fetch is now cached
as an explicit `is_failure_backoff` marker with its own, shorter TTL
instead of writing nothing at all.)

Then, in `run_cycle`, find the trade-counting block (added in Task 4,
right after `execute_steps`):

```python
    results = execute_steps(token, account_id, steps, region)
    had_error = any(r["error"] for r in results) or not results
    if not had_error and any(s["type"] == "ouverture_simulee" for s in steps):
        circuit_breaker.record_trade_opened()
```

Replace the trade-counting condition only (keep `had_error` and its use
in the final `result["action"]` line unchanged — it's still correct for
that purpose, just not for trade-counting):

```python
    results = execute_steps(token, account_id, steps, region)
    had_error = any(r["error"] for r in results) or not results
    opened_successfully = any(
        r["step"]["type"] == "ouverture_simulee" and r["error"] is None for r in results
    )
    if opened_successfully:
        circuit_breaker.record_trade_opened()
```

- [ ] **Step 14: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_loop.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 15: Update `.gitignore`**

Add these lines to `.gitignore`, alongside the existing `gold_bot/*.json`
entries:

```
gold_bot/historical_candles_cache_xauusd.json
gold_bot/historical_candles_cache_xagusd.json
gold_bot/odds_verdict_cache_xauusd_achat.json
gold_bot/odds_verdict_cache_xauusd_vente.json
gold_bot/odds_verdict_cache_xagusd_achat.json
gold_bot/odds_verdict_cache_xagusd_vente.json
```

- [ ] **Step 16: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: PASS. This touches every module the whole plan built —
confirm nothing anywhere broke.

- [ ] **Step 17: Commit**

```bash
git add gold_bot/odds_check.py gold_bot/loop.py gold_bot/bot.py .gitignore \
        tests/gold_bot/test_odds_check.py tests/gold_bot/test_loop.py tests/gold_bot/test_bot.py
git commit -m "fix(gold-bot): corrige les defauts trouves en revue finale de branche

Defaut critique : le stop suiveur de la jambe runner ne s'activait
jamais dans odds_check._simulate_exit (fenetre glissante limitee a
l'apres-entree d'une seule session, jamais les 200 bougies 15min
necessaires a compute_trailing_stop) -- rejoue desormais sur une
vraie fenetre glissante de bougies 5min, comme en live.

Corrige aussi : biais d'anticipation sur les bougies du bucket
d'entree, deduction du spread manquante (promise par la spec),
coupe-circuit verifie avant les cotes historiques (plus couteuses,
jamais l'inverse), memoisation du verdict par empreinte de l'historique
(necessaire une fois le suivi reellement actif), durcissement de
_load_or_fetch_historical_candles (except trop precis, pas de backoff
sur echec reseau), sous-comptage du plafond de trades/jour en cas
d'echec partiel d'une entree a deux jambes.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
