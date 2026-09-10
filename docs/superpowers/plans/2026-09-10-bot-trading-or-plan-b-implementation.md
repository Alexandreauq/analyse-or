# Bot de trading or — Plan B : mise en production Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Plan A's decision engine (`gold_bot/confluence.py`, `risk.py`, `state.py`, `broker.py`, `bot.py` — already merged/mergeable, built entirely in simulation mode) into a real, deployable service: an always-on polling loop capable of placing real orders, a daily email summary, a kill-switch HTTPS API, a "Bot Or" block on the site, and the VPS deployment artifacts to run all of it.

**Architecture:** A new `gold_bot/loop.py` is the *only* module in the whole codebase allowed to call `broker.place_market_order`/`close_position` for real (gated by `state.json`'s `dry_run`/`kill_switch` flags) — `bot.py`'s `decide_and_act()` itself is never modified and still never calls the broker, preserving Plan A's safety guarantee exactly as built. `gold_bot/api.py` (FastAPI) exposes a minimal HTTPS surface whose only writable action is "stop" — there is no HTTP path that can start, resize, or redirect a trade. `gold_bot/notify.py` sends one daily email from a local decision log. Two systemd services run on the VPS; a manual, SSH-only, no-HTTP-endpoint command flips `dry_run` off when the user is ready — deliberately higher-friction than everything else in this system, since it's the single most consequential switch in the whole project.

**Tech Stack:** Python 3.12, `fastapi`+`uvicorn` (new — VPS-only, kept out of the main `requirements.txt` used by GitHub Actions crons), `smtplib`/`email.mime` (stdlib, already used by `indices_score.py`), pytest, `httpx` (test-only, required by FastAPI's `TestClient`).

**Spec:** `docs/superpowers/specs/2026-09-10-bot-trading-or-design.md`

## Global Constraints

- **`gold_bot/bot.py` is never modified by this plan.** `decide_and_act()`'s "never calls the broker" guarantee, verified four times independently during Plan A, must stay exactly as built. All real order execution lives in the new `gold_bot/loop.py` only.
- **The HTTPS API's only writable action reduces risk, never increases it.** `/kill` and `/resume` only ever touch the `kill_switch` flag. There is no HTTP endpoint anywhere that can set `dry_run` to `False`, open a position, or change position sizing — going live is a manual, SSH-only action (see Task 4), by design.
- **`state.json`'s `dry_run` defaults to `True`** (already true from Plan A) — every new module must treat "flag absent or true" as "never call a live order function," never the inverse.
- Daily email: **one email per day, not per trade** (explicit spec requirement) — built from a local decision log, not sent inline from the polling loop.
- No new dependency in the root `requirements.txt` (used by GitHub Actions crons) — VPS-only dependencies (`fastapi`, `uvicorn`, `httpx`) go in a new `requirements-bot.txt`.
- Reuse `indices_score.py`'s exact SMTP conventions (`SMTP_HOST = "smtp.gmail.com"`, `SMTP_PORT = 587`, `smtplib.SMTP(...).starttls()`/`.login()`/`.sendmail()`, `SMTP_USER`/`SMTP_PASSWORD`/`MAIL_TO` env vars, silent skip with a printed message if unconfigured — never an exception) rather than inventing a different email-sending pattern.
- No browser automation available — verify the frontend task (Task 5) by careful diff reading, not a live browser. `docs/service-worker.js`'s `CACHE_NAME` must be bumped whenever `docs/index.html` changes (current value: `analyse-or-shell-v23`).

---

### Task 1: `gold_bot/loop.py` — real execution wiring + polling loop

**Files:**
- Create: `gold_bot/loop.py`
- Test: `tests/gold_bot/test_loop.py`

**Interfaces:**
- Consumes: `bot.decide_and_act(...)` / `bot.reconcile_positions(...)` (Plan A), `broker.get_account_balance/get_symbol_specification/place_market_order/close_position` (Plan A), `confluence.fetch_gold_candles(api_key)` (Plan A), `state.load_state()`/`state.STATE_PATH` (Plan A), `risk.CircuitBreaker(persist_path=...)` (Plan A, already supports this from its own final-review fix).
- Produces: `execute_steps(token, account_id, steps, region) -> list[dict]`, `run_cycle(token, account_id, twelve_data_api_key, circuit_breaker, region, symbol) -> dict`, `main()` — consumed by Task 4's systemd unit (`python3 -m gold_bot.loop`) and read (log file path) by Task 2's `notify.py`.

Before writing task code, re-read the actual current `gold_bot/bot.py`, `gold_bot/risk.py`, `gold_bot/broker.py`, `gold_bot/state.py`, and `gold_bot/confluence.py` — their exact current signatures are given below verbatim, but confirm nothing drifted since Plan A's merge.

- [ ] **Step 1: Write the failing tests**

Create `tests/gold_bot/test_loop.py`:

```python
import json
from datetime import datetime, timezone

import gold_bot.loop as loop


def test_execute_steps_calls_close_for_closing_step(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        loop.broker, "close_position",
        lambda token, account_id, position_id, region: captured.__setitem__("close", (token, account_id, position_id, region)) or {"orderId": "1"},
    )
    steps = [{"type": "clôture_simulee", "position_id": "42", "symbol": "XAUUSD"}]
    results = loop.execute_steps("tok", "acc", steps, region="london")
    assert captured["close"] == ("tok", "acc", "42", "london")
    assert results == [{"step": steps[0], "result": {"orderId": "1"}}]


def test_execute_steps_calls_place_order_for_opening_step(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        loop.broker, "place_market_order",
        lambda token, account_id, symbol, direction, volume, stop_loss, take_profit, region:
            captured.__setitem__("place", (symbol, direction, volume, stop_loss, take_profit, region)) or {"orderId": "2"},
    )
    steps = [{
        "type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
        "volume": 1.0, "entry": 2100, "stop_loss": 2095, "take_profit": 2115,
    }]
    results = loop.execute_steps("tok", "acc", steps, region="london")
    assert captured["place"] == ("XAUUSD", "achat", 1.0, 2095, 2115, "london")
    assert results == [{"step": steps[0], "result": {"orderId": "2"}}]


def test_execute_steps_skips_unknown_step_type(monkeypatch):
    monkeypatch.setattr(loop.broker, "close_position", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas être appelé")))
    monkeypatch.setattr(loop.broker, "place_market_order", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas être appelé")))
    results = loop.execute_steps("tok", "acc", [{"type": "inconnu"}], region="london")
    assert results == []


def test_log_decision_appends_jsonl_with_timestamp(tmp_path):
    path = tmp_path / "decisions_log.jsonl"
    loop._log_decision({"action": "aucune", "reason": "signal neutre"}, path=str(path))
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["action"] == "aucune"
    assert "timestamp" in entry


def test_run_cycle_no_action_when_kill_switch_engaged(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": True, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas être appelé")))

    result = loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker())

    assert result == {"action": "ignore", "reason": "interrupteur d'urgence activé"}


def test_run_cycle_logs_dry_run_without_executing(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [{"close": 2100}])
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 10000)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100})
    fake_steps = [{"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
                    "volume": 1.0, "entry": 2100, "stop_loss": 2095, "take_profit": 2115}]
    monkeypatch.setattr(loop.bot, "decide_and_act", lambda *a, **k: {"action": "simulation", "steps": fake_steps})
    monkeypatch.setattr(loop.broker, "place_market_order", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit jamais être appelé en dry-run")))

    result = loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker())

    assert result["action"] == "simulation_dry_run"


def test_run_cycle_executes_when_not_dry_run(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": False})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(loop.confluence, "fetch_gold_candles", lambda api_key: [{"close": 2100}])
    monkeypatch.setattr(loop.broker, "get_account_balance", lambda *a, **k: 10000)
    monkeypatch.setattr(loop.bot, "reconcile_positions", lambda *a, **k: [])
    monkeypatch.setattr(loop.broker, "get_symbol_specification", lambda *a, **k: {"contractSize": 100})
    fake_steps = [{"type": "ouverture_simulee", "symbol": "XAUUSD", "direction": "achat",
                    "volume": 1.0, "entry": 2100, "stop_loss": 2095, "take_profit": 2115}]
    monkeypatch.setattr(loop.bot, "decide_and_act", lambda *a, **k: {"action": "simulation", "steps": fake_steps})
    placed = {}
    monkeypatch.setattr(loop.broker, "place_market_order", lambda *a, **k: placed.__setitem__("called", True) or {"orderId": "3"})

    result = loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker())

    assert placed.get("called") is True
    assert result["action"] == "exécuté"


def test_run_cycle_logs_error_when_data_fetch_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(loop.state, "load_state", lambda *a, **k: {"kill_switch": False, "dry_run": True})
    monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.jsonl"))
    monkeypatch.setattr(
        loop.confluence, "fetch_gold_candles",
        lambda api_key: (_ for _ in ()).throw(RuntimeError("Twelve Data indisponible")),
    )

    result = loop.run_cycle("tok", "acc", "td-key", loop.risk.CircuitBreaker())

    assert result["action"] == "erreur"
    assert "Twelve Data indisponible" in result["reason"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_loop.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gold_bot.loop'`

- [ ] **Step 3: Write the implementation**

Create `gold_bot/loop.py`:

```python
# gold_bot/loop.py
# Boucle continue et exécution réelle des ordres — le SEUL module de ce
# projet qui appelle broker.place_market_order/close_position pour de
# vrai. gold_bot.bot.decide_and_act() elle-même n'appelle jamais le
# broker (garantie posée et vérifiée en Plan A, jamais modifiée ici).
# Voir docs/superpowers/specs/2026-09-10-bot-trading-or-design.md.
import json
import os
import time
import traceback
from datetime import datetime, timezone

import gold_bot.bot as bot
import gold_bot.broker as broker
import gold_bot.confluence as confluence
import gold_bot.risk as risk
import gold_bot.state as state

POLL_INTERVAL_SECONDS = 60
SYMBOL = "XAUUSD"
DECISIONS_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decisions_log.jsonl")


def execute_steps(token: str, account_id: str, steps: list[dict],
                   region: str = broker.DEFAULT_MT5_REGION) -> list[dict]:
    """Exécute réellement les étapes renvoyées par bot.decide_and_act.
    Toute étape d'un type non reconnu est ignorée plutôt que de lever
    (garde-fou : ne jamais deviner une action à partir d'une donnée
    inattendue)."""
    results = []
    for step in steps:
        if step["type"] == "clôture_simulee":
            result = broker.close_position(token, account_id, step["position_id"], region)
        elif step["type"] == "ouverture_simulee":
            result = broker.place_market_order(
                token, account_id, step["symbol"], step["direction"], step["volume"],
                step["stop_loss"], step["take_profit"], region,
            )
        else:
            continue
        results.append({"step": step, "result": result})
    return results


def _log_decision(entry: dict, path: str = DECISIONS_LOG_PATH) -> None:
    """Journalise chaque décision de chaque cycle (localement) pour le
    résumé quotidien (gold_bot.notify). N'interrompt jamais le cycle si
    l'écriture échoue."""
    record = dict(entry)
    record["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"Erreur journalisation décision : {e}")


def run_cycle(token: str, account_id: str, twelve_data_api_key: str,
              circuit_breaker: "risk.CircuitBreaker", region: str = broker.DEFAULT_MT5_REGION,
              symbol: str = SYMBOL) -> dict:
    """Un cycle complet : interrupteur d'urgence -> données réelles ->
    décision (bot.decide_and_act, jamais d'appel broker dedans) ->
    exécution réelle seulement si dry_run est faux. Journalise
    systématiquement le résultat, y compris les erreurs (remontées dans
    le résumé quotidien par gold_bot.notify) — ne lève jamais, pour que
    la boucle appelante passe simplement au cycle suivant plutôt que de
    s'arrêter."""
    current_state = state.load_state()
    if current_state["kill_switch"]:
        decision = {"action": "ignore", "reason": "interrupteur d'urgence activé"}
        _log_decision(decision, path=DECISIONS_LOG_PATH)
        return decision

    try:
        candles = confluence.fetch_gold_candles(twelve_data_api_key)
        balance = broker.get_account_balance(token, account_id, region)
        open_positions = bot.reconcile_positions(token, account_id, region)
        spec = broker.get_symbol_specification(token, account_id, symbol, region)
        contract_size = spec["contractSize"]
    except Exception as e:
        decision = {"action": "erreur", "reason": f"Données indisponibles : {e}"}
        _log_decision(decision, path=DECISIONS_LOG_PATH)
        print(f"Erreur pendant la collecte des données : {e}")
        traceback.print_exc()
        return decision

    decision = bot.decide_and_act(
        candles, contract_size=contract_size, balance=balance,
        open_positions=open_positions, circuit_breaker=circuit_breaker, symbol=symbol,
    )

    if decision["action"] != "simulation":
        _log_decision(decision, path=DECISIONS_LOG_PATH)
        return decision

    if current_state["dry_run"]:
        result = {**decision, "action": "simulation_dry_run"}
        _log_decision(result, path=DECISIONS_LOG_PATH)
        return result

    try:
        results = execute_steps(token, account_id, decision["steps"], region)
        result = {"action": "exécuté", "steps": decision["steps"], "results": results}
    except Exception as e:
        result = {"action": "erreur", "reason": f"Échec d'exécution : {e}", "steps": decision["steps"]}
        print(f"Erreur pendant l'exécution des ordres : {e}")
        traceback.print_exc()
    _log_decision(result, path=DECISIONS_LOG_PATH)
    return result


def main():
    token = os.environ["METAAPI_TRADE_TOKEN"]
    account_id = os.environ["METAAPI_TRADE_ACCOUNT_ID"]
    twelve_data_api_key = os.environ["TWELVE_DATA_API_KEY"]
    circuit_breaker = risk.CircuitBreaker(persist_path=state.STATE_PATH)

    while True:
        try:
            run_cycle(token, account_id, twelve_data_api_key, circuit_breaker)
        except Exception as e:
            # Filet de sécurité ultime — run_cycle ne devrait jamais
            # lever (elle capture déjà ses propres erreurs), mais un
            # vrai crash de la boucle serait pire qu'un cycle manqué.
            print(f"Erreur inattendue pendant le cycle : {e}")
            traceback.print_exc()
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
```

Note in `test_run_cycle_*`, `monkeypatch.setattr(loop, "DECISIONS_LOG_PATH", ...)` changes the module-level constant, but `run_cycle`/`_log_decision` must read it via `DECISIONS_LOG_PATH` at call time (not a bound default) for this to work — the code above already does this correctly by passing `path=DECISIONS_LOG_PATH` explicitly inside `run_cycle` rather than relying on `_log_decision`'s default parameter (which is bound once at import time and would NOT pick up the monkeypatch). This mirrors a lesson from Plan A's final review (`gold_bot/api.py` in Task 3 below has the same pattern for `state.STATE_PATH` — read the note there too).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_loop.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add gold_bot/loop.py tests/gold_bot/test_loop.py
git commit -m "feat(gold-bot): boucle continue + exécution réelle des ordres

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `gold_bot/notify.py` — résumé quotidien par email

**Files:**
- Create: `gold_bot/notify.py`
- Test: `tests/gold_bot/test_notify.py`

**Interfaces:**
- Consumes: nothing from Task 1 directly at import time, but reads the same `DECISIONS_LOG_PATH` file Task 1's `_log_decision` writes to (recomputed independently in this file the same way, both resolve to `gold_bot/decisions_log.jsonl` since both use `os.path.dirname(os.path.abspath(__file__))` from within the `gold_bot/` package).
- Produces: `read_todays_decisions(path, today=None) -> list[dict]`, `send_daily_summary(decisions, day=None) -> bool`, `main()` — `main()` consumed by Task 4's daily cron entry.

- [ ] **Step 1: Write the failing tests**

Create `tests/gold_bot/test_notify.py`:

```python
import json

import gold_bot.notify as notify


def test_read_todays_decisions_filters_by_date(tmp_path):
    path = tmp_path / "decisions_log.jsonl"
    path.write_text(
        json.dumps({"timestamp": "2026-09-10T08:00:00Z", "action": "aucune"}) + "\n" +
        json.dumps({"timestamp": "2026-09-09T08:00:00Z", "action": "aucune"}) + "\n" +
        json.dumps({"timestamp": "2026-09-10T09:00:00Z", "action": "exécuté"}) + "\n",
        encoding="utf-8",
    )
    result = notify.read_todays_decisions(str(path), today="2026-09-10")
    assert len(result) == 2
    assert all(d["timestamp"].startswith("2026-09-10") for d in result)


def test_read_todays_decisions_returns_empty_list_when_file_absent(tmp_path):
    path = tmp_path / "does_not_exist.jsonl"
    assert notify.read_todays_decisions(str(path), today="2026-09-10") == []


def test_read_todays_decisions_skips_corrupted_lines(tmp_path):
    path = tmp_path / "decisions_log.jsonl"
    path.write_text(
        "{not valid json\n" + json.dumps({"timestamp": "2026-09-10T08:00:00Z", "action": "aucune"}) + "\n",
        encoding="utf-8",
    )
    result = notify.read_todays_decisions(str(path), today="2026-09-10")
    assert len(result) == 1


def test_send_daily_summary_skipped_when_smtp_not_configured(monkeypatch):
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    assert notify.send_daily_summary([], day="2026-09-10") is False


def test_send_daily_summary_sends_email_when_configured(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    sent = {}

    class _FakeSMTP:
        def __init__(self, host, port):
            sent["host_port"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, user, password):
            sent["login"] = (user, password)

        def sendmail(self, from_addr, to_addrs, message):
            sent["sendmail"] = (from_addr, to_addrs)

    monkeypatch.setattr(notify.smtplib, "SMTP", _FakeSMTP)
    result = notify.send_daily_summary([{"action": "exécuté", "steps": []}], day="2026-09-10")

    assert result is True
    assert sent["host_port"] == (notify.SMTP_HOST, notify.SMTP_PORT)
    assert sent["login"] == ("bot@example.com", "secret")
    assert sent["sendmail"][0] == "bot@example.com"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_notify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gold_bot.notify'`

- [ ] **Step 3: Write the implementation**

Create `gold_bot/notify.py`:

```python
# gold_bot/notify.py
# Résumé quotidien par email des décisions du bot — même conventions
# SMTP que indices_score.py (voir SMTP_HOST/SMTP_PORT/variables
# d'environnement ci-dessous). Un email par jour, jamais par trade.
import json
import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# Calculé indépendamment de gold_bot.loop plutôt qu'importé de là —
# les deux modules résolvent au même chemin car tous deux basés sur
# leur propre __file__ dans le même package gold_bot/, sans avoir à se
# dépendre l'un l'autre pour une simple constante de chemin.
DECISIONS_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decisions_log.jsonl")


def read_todays_decisions(path: str = DECISIONS_LOG_PATH, today: str | None = None) -> list[dict]:
    """Lit les décisions journalisées dont l'horodatage tombe le jour
    UTC demandé (aujourd'hui par défaut). Fichier absent/illisible ou
    lignes corrompues -> ignorées silencieusement, jamais d'exception."""
    if today is None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    decisions = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("timestamp", "").startswith(today):
                    decisions.append(entry)
    except Exception:
        return []
    return decisions


def build_summary_email_html(decisions: list[dict], day: str) -> str:
    if not decisions:
        body_html = '<p style="color:#8a90a3;font-size:13px;">Aucun cycle journalisé aujourd\'hui.</p>'
    else:
        executed = [d for d in decisions if d.get("action") == "exécuté"]
        body_html = (
            f'<p style="color:#edeef3;font-size:14px;margin:0 0 16px;">'
            f'{len(decisions)} cycle(s) évalué(s), {len(executed)} ordre(s) exécuté(s).</p>'
        )
        for d in executed:
            for step in d.get("steps", []):
                body_html += (
                    f'<p style="margin:0 0 8px;padding:10px 14px;background:#1b1d25;'
                    f'border-left:3px solid #2a2d38;font-family:Arial,sans-serif;">'
                    f'<span style="color:#edeef3;font-size:13px;">{step.get("type")} — '
                    f'{step.get("symbol")} {step.get("direction", "")}</span></p>'
                )
    return f"""
    <html><body style="background:#15161c;margin:0;padding:0;">
      <div style="max-width:480px;margin:0 auto;padding:32px 24px;font-family:Arial,Helvetica,sans-serif;">
        <p style="color:#8a90a3;font-size:11px;letter-spacing:0.08em;text-transform:uppercase;margin:0 0 10px;">
          Bot Or — Résumé du {day}
        </p>
        {body_html}
      </div>
    </body></html>
    """


def send_daily_summary(decisions: list[dict], day: str | None = None) -> bool:
    """Envoie le résumé quotidien. Ignoré silencieusement (avec un
    message) si SMTP_USER/SMTP_PASSWORD ne sont pas configurés — même
    contrat que indices_score.send_entry_alert_email, jamais
    d'exception."""
    if day is None:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    mail_to = os.environ.get("MAIL_TO") or smtp_user
    if not smtp_user or not smtp_password:
        print("\n(Envoi du résumé quotidien Bot Or ignoré : SMTP_USER / SMTP_PASSWORD non configurés.)")
        return False
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            msg = MIMEMultipart("mixed")
            msg["Subject"] = f"Bot Or — résumé du {day}"
            msg["From"] = smtp_user
            msg["To"] = mail_to
            msg.attach(MIMEText(build_summary_email_html(decisions, day), "html"))
            server.sendmail(smtp_user, [mail_to], msg.as_string())
        print(f"Résumé quotidien Bot Or envoyé ({len(decisions)} cycle(s)).")
        return True
    except Exception as e:
        print(f"Erreur envoi résumé quotidien Bot Or : {e}")
        return False


def main():
    decisions = read_todays_decisions()
    send_daily_summary(decisions)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_notify.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add gold_bot/notify.py tests/gold_bot/test_notify.py
git commit -m "feat(gold-bot): résumé quotidien par email des décisions du bot

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `gold_bot/api.py` — API HTTPS interrupteur d'urgence

**Files:**
- Create: `gold_bot/api.py`
- Test: `tests/gold_bot/test_api.py`
- Modify: `requirements-bot.txt` (create if this is the first task to need it — check whether Task 1/2 already created it; if not, create it now)

**Interfaces:**
- Consumes: `state.load_state(path)`/`state.save_state(state, path)`/`state.STATE_PATH` (Plan A).
- Produces: FastAPI `app` object with `GET /status`, `POST /kill`, `POST /resume` — consumed by Task 4's systemd unit (`uvicorn gold_bot.api:app`) and Task 5's frontend.

**Critical gotcha to avoid** (a real bug class already caught once in this project's history, during Plan A's final review): `gold_bot/state.py`'s `load_state(path: str = STATE_PATH)` and `save_state(state, path: str = STATE_PATH)` bind their default parameter value ONCE, at module-import time. If this module's route handlers call `state.load_state()` with no explicit argument, monkeypatching `state.STATE_PATH` in a test will NOT affect them (the old, real path stays baked into the already-bound default) — tests would silently read/write the real production-adjacent `gold_bot/state.json` file. Every call in this task must pass `state.STATE_PATH` **explicitly** as an argument (`state.load_state(state.STATE_PATH)`), which re-reads the module attribute fresh at call time and is what makes `monkeypatch.setattr(state, "STATE_PATH", ...)` actually work in tests.

- [ ] **Step 1: Write the failing tests**

Create `tests/gold_bot/test_api.py`:

```python
import pytest
from fastapi.testclient import TestClient

import gold_bot.api as api
import gold_bot.state as state


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("BOT_API_TOKEN", "secret-token")
    return TestClient(api.app)


def test_status_returns_current_state(client):
    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()
    assert body["kill_switch"] is False
    assert body["dry_run"] is True


def test_kill_requires_valid_token(client):
    response = client.post("/kill", headers={"X-Bot-Token": "wrong-token"})
    assert response.status_code == 401


def test_kill_without_token_header_is_rejected(client):
    response = client.post("/kill")
    assert response.status_code == 401


def test_kill_sets_kill_switch_true(client):
    response = client.post("/kill", headers={"X-Bot-Token": "secret-token"})
    assert response.status_code == 200
    assert response.json()["kill_switch"] is True
    assert client.get("/status").json()["kill_switch"] is True


def test_resume_sets_kill_switch_false(client):
    client.post("/kill", headers={"X-Bot-Token": "secret-token"})
    response = client.post("/resume", headers={"X-Bot-Token": "secret-token"})
    assert response.status_code == 200
    assert response.json()["kill_switch"] is False


def test_resume_requires_valid_token(client):
    response = client.post("/resume", headers={"X-Bot-Token": "wrong-token"})
    assert response.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/gold_bot/test_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fastapi'` (or `'gold_bot.api'` once `fastapi` is installed) — install `requirements-bot.txt` first (see Step 3b).

- [ ] **Step 3a: Write the implementation**

Create `gold_bot/api.py`:

```python
# gold_bot/api.py
# Point d'accès HTTPS minimal pour l'interrupteur d'urgence — la SEULE
# action possible via ce point d'accès est d'arrêter le bot (jamais de
# route qui l'active, augmente une position, ou déclenche un ordre :
# ces actions n'existent tout simplement pas ici. Voir
# docs/superpowers/specs/2026-09-10-bot-trading-or-design.md.
import os

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import gold_bot.state as state

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://alexandreauq.github.io"],
    allow_methods=["GET", "POST"],
    allow_headers=["X-Bot-Token"],
)


def _check_token(x_bot_token: str | None) -> None:
    """Refuse par défaut si BOT_API_TOKEN n'est pas configuré (échec
    fermé, jamais ouvert) — pas seulement si le jeton fourni est faux."""
    expected = os.environ.get("BOT_API_TOKEN")
    if not expected or x_bot_token != expected:
        raise HTTPException(status_code=401, detail="Jeton invalide ou absent")


@app.get("/status")
def get_status():
    current = state.load_state(state.STATE_PATH)
    return {
        "kill_switch": current["kill_switch"],
        "dry_run": current["dry_run"],
        "circuit_breaker_day": current.get("circuit_breaker_day"),
        "circuit_breaker_starting_balance": current.get("circuit_breaker_starting_balance"),
    }


@app.post("/kill")
def kill(x_bot_token: str | None = Header(default=None)):
    _check_token(x_bot_token)
    current = state.load_state(state.STATE_PATH)
    current["kill_switch"] = True
    state.save_state(current, state.STATE_PATH)
    return {"kill_switch": True}


@app.post("/resume")
def resume(x_bot_token: str | None = Header(default=None)):
    _check_token(x_bot_token)
    current = state.load_state(state.STATE_PATH)
    current["kill_switch"] = False
    state.save_state(current, state.STATE_PATH)
    return {"kill_switch": False}
```

- [ ] **Step 3b: Create/update `requirements-bot.txt`**

If Tasks 1-2 haven't already created it, create `requirements-bot.txt` at the repo root:

```
requests
fastapi
uvicorn[standard]
httpx
```

(`httpx` is required by FastAPI's `TestClient`, used only in tests, but this project has no separate dev-requirements file — keep it in the same file for simplicity.) Install it: `pip install -r requirements-bot.txt`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/gold_bot/test_api.py -v`
Expected: 6 passed

- [ ] **Step 5: Run the full repo suite (regression check)**

Run: `python -m pytest`
Expected: all pre-existing tests plus every `gold_bot` test still pass.

- [ ] **Step 6: Commit**

```bash
git add gold_bot/api.py tests/gold_bot/test_api.py requirements-bot.txt
git commit -m "feat(gold-bot): API HTTPS interrupteur d'urgence (FastAPI)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: déploiement — systemd, script de déploiement, guide VPS

**Files:**
- Create: `deploy/gold-bot-loop.service`
- Create: `deploy/gold-bot-api.service`
- Create: `deploy/deploy.sh`
- Create: `deploy/README.md`

**Interfaces:**
- Consumes: `gold_bot.loop:main` (`python3 -m gold_bot.loop`), `gold_bot.api:app` (`uvicorn gold_bot.api:app`), `gold_bot.notify:main` (`python3 -m gold_bot.notify`, invoked by cron, not systemd).
- Produces: operational runbook — no code consumed by later tasks.

This task has no automated tests (deployment configuration and documentation, not application code) — verify by careful reading: do the paths in each file agree with each other, does the runbook's sequence of commands actually match the files it references.

- [ ] **Step 1: Create the systemd unit for the trading loop**

Create `deploy/gold-bot-loop.service`:

```ini
[Unit]
Description=Bot Or - boucle de trading
After=network.target

[Service]
Type=simple
User=goldbot
WorkingDirectory=/home/goldbot/analyse-or
EnvironmentFile=/home/goldbot/analyse-or/.env
ExecStart=/home/goldbot/analyse-or/venv/bin/python3 -m gold_bot.loop
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create the systemd unit for the API**

Create `deploy/gold-bot-api.service`:

```ini
[Unit]
Description=Bot Or - API interrupteur d'urgence
After=network.target

[Service]
Type=simple
User=goldbot
WorkingDirectory=/home/goldbot/analyse-or
EnvironmentFile=/home/goldbot/analyse-or/.env
ExecStart=/home/goldbot/analyse-or/venv/bin/uvicorn gold_bot.api:app --host 0.0.0.0 --port 8443 --ssl-keyfile /etc/letsencrypt/live/DOMAINE_A_REMPLACER/privkey.pem --ssl-certfile /etc/letsencrypt/live/DOMAINE_A_REMPLACER/fullchain.pem
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Create the deploy script**

Create `deploy/deploy.sh`:

```bash
#!/bin/bash
# Déploiement manuel (pas de CI/CD pour cette phase — voir le spec).
# À exécuter sur le VPS, depuis /home/goldbot/analyse-or.
set -e

cd /home/goldbot/analyse-or
git pull
source venv/bin/activate
pip install -r requirements-bot.txt
sudo systemctl restart gold-bot-loop
sudo systemctl restart gold-bot-api
echo "Déploiement terminé."
```

Make it executable as part of this step: `chmod +x deploy/deploy.sh`.

- [ ] **Step 4: Write the VPS setup runbook**

Create `deploy/README.md`:

```markdown
# Déploiement du Bot Or sur un VPS

Guide d'installation initiale (à faire une fois). Les mises à jour
suivantes se font avec `deploy.sh` (voir en bas).

## 1. Provisionner le VPS

N'importe quel petit VPS Linux (ex. Hetzner CX22, DigitalOcean droplet
1 Go) avec Ubuntu/Debian récent. Un nom de domaine ou sous-domaine
pointant vers son IP est nécessaire pour le certificat HTTPS (ex.
`bot.tondomaine.fr`).

## 2. Sécurisation de base

```bash
# Connexion SSH par clé uniquement (pas de mot de passe)
sudo sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart sshd

# Pare-feu : uniquement SSH (22) et l'API du bot (8443)
sudo apt install -y ufw
sudo ufw allow 22
sudo ufw allow 8443
sudo ufw enable

# Mises à jour de sécurité automatiques
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

## 3. Utilisateur dédié + dépôt

```bash
sudo adduser --disabled-password goldbot
sudo -u goldbot -i
git clone https://github.com/Alexandreauq/analyse-or.git
cd analyse-or
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-bot.txt
```

## 4. Fichier `.env` (jamais commité)

Créer `/home/goldbot/analyse-or/.env` :

```
METAAPI_TRADE_TOKEN=...       # token du DEUXIÈME compte MetaApi, connecté avec le mot de passe de trading réel
METAAPI_TRADE_ACCOUNT_ID=...  # account ID MetaApi de ce compte (différent du compte lecture seule)
TWELVE_DATA_API_KEY=...       # déjà utilisé ailleurs dans ce projet
SMTP_USER=...
SMTP_PASSWORD=...
MAIL_TO=...                   # optionnel, sinon = SMTP_USER
BOT_API_TOKEN=...             # jeton secret pour /kill et /resume — génère une chaîne aléatoire longue
```

Permissions restreintes : `chmod 600 .env`.

## 5. Certificat HTTPS (Let's Encrypt)

```bash
sudo apt install -y certbot
sudo certbot certonly --standalone -d DOMAINE_A_REMPLACER
```

Remplace `DOMAINE_A_REMPLACER` dans `deploy/gold-bot-api.service` par le
vrai domaine avant l'étape suivante.

## 6. Installer les services systemd

```bash
sudo cp deploy/gold-bot-loop.service /etc/systemd/system/
sudo cp deploy/gold-bot-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gold-bot-loop
sudo systemctl enable --now gold-bot-api
```

## 7. Résumé quotidien par email (cron)

```bash
crontab -e -u goldbot
```

Ajouter :

```
0 23 * * * cd /home/goldbot/analyse-or && venv/bin/python3 -m gold_bot.notify >> /home/goldbot/gold_bot_notify.log 2>&1
```

## 8. Vérifier

```bash
sudo systemctl status gold-bot-loop
sudo systemctl status gold-bot-api
curl https://DOMAINE_A_REMPLACER:8443/status
```

Le bot tourne maintenant en **mode simulation** (`dry_run: true` par
défaut) — il évalue les signaux et journalise ce qu'il ferait, sans
jamais passer de vrai ordre. Laisse-le tourner plusieurs jours et
observe `gold_bot/decisions_log.jsonl` / les résumés quotidiens par
email avant de passer à l'étape suivante.

## 9. Passage en mode réel (à ne faire qu'après validation du mode simulation)

**Volontairement pas exposé via l'API HTTPS** — le seul chemin est une
commande manuelle sur le VPS, en SSH, pour que ce soit la bascule la
plus difficile à déclencher par erreur de tout le système :

```bash
sudo -u goldbot -i
cd analyse-or
source venv/bin/activate
python3 -c "import gold_bot.state as state; s = state.load_state(state.STATE_PATH); s['dry_run'] = False; state.save_state(s, state.STATE_PATH)"
sudo systemctl restart gold-bot-loop
```

Pour revenir en simulation, remets `dry_run` à `True` avec la même
commande et redémarre à nouveau.

## Mises à jour

```bash
sudo -u goldbot -i
cd analyse-or
./deploy/deploy.sh
```
```

- [ ] **Step 5: Self-review — cross-check paths**

Re-read all 4 files together and confirm: both `.service` files reference `/home/goldbot/analyse-or` and `venv/bin/...` consistently with the runbook's setup steps; `deploy.sh`'s `pip install -r requirements-bot.txt` matches the file Task 3 created; the cron line's working directory and venv path match; the "passage en mode réel" command uses `gold_bot.state`'s actual `load_state`/`save_state`/`STATE_PATH` names exactly as defined in the already-merged `gold_bot/state.py`.

- [ ] **Step 6: Commit**

```bash
git add deploy/
git commit -m "docs(gold-bot): déploiement VPS (systemd, script, guide d'installation)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: intégration site — bloc "Bot Or" dans l'onglet Portefeuille

**Files:**
- Modify: `docs/index.html`
- Modify: `docs/service-worker.js`

**Interfaces:**
- Consumes: Task 3's `GET /status`, `POST /kill`, `POST /resume` (response shape: `{"kill_switch": bool, "dry_run": bool, "circuit_breaker_day": str|null, "circuit_breaker_starting_balance": number|null}`).
- Produces: nothing consumed by later tasks (final task of this plan).

Before editing, re-read the actual current `docs/index.html` around `renderPortfolioScreen`, `realPortfolioHtml`, and `escHtml` (Grep for these exact strings — do not trust stale line numbers) — this task adds a new block to the same screen, following the same patterns already established there.

- [ ] **Step 1: Add the global state variable and API base URL constant**

Find where other portfolio-related `let` globals are declared (search for `let realPortfolioData = null;`) and add immediately after it:

```javascript
let botStatusData = null;
const BOT_API_BASE_URL = 'https://DOMAINE_A_REMPLACER:8443'; // remplacer après le déploiement (Task 4)
```

- [ ] **Step 2: Add the fetch + render + wire functions**

Add these three functions near `realPortfolioHtml` (same file, search for `function realPortfolioHtml` to find a good insertion point right after it):

```javascript
async function loadBotStatus() {
  try {
    const res = await fetch(`${BOT_API_BASE_URL}/status`);
    botStatusData = res.ok ? await res.json() : null;
  } catch (e) {
    botStatusData = null;
  }
}

function getBotToken() {
  let token = localStorage.getItem('goldBotToken');
  if (!token) {
    token = prompt('Jeton d\'accès au bot (demandé une seule fois, conservé sur cet appareil) :');
    if (token) localStorage.setItem('goldBotToken', token);
  }
  return token;
}

function botStatusHtml() {
  if (!botStatusData) {
    return `
      <h2>Bot Or</h2>
      <div class="empty">Statut du bot indisponible.</div>`;
  }
  const label = botStatusData.kill_switch ? 'Coupé' : (botStatusData.dry_run ? 'Simulation' : 'Actif');
  const color = botStatusData.kill_switch ? 'var(--rust)' : 'var(--gold)';
  const actionLabel = botStatusData.kill_switch ? 'Relancer' : 'Arrêter';
  return `
    <h2>Bot Or</h2>
    <div class="portfolio-position-row">
      <div class="portfolio-position-main">
        <span class="company-name" style="color:${color}">${label}</span>
        <span class="hero-sub">${botStatusData.dry_run ? 'Mode simulation — aucun ordre réel' : 'Mode réel'}</span>
      </div>
      <div class="portfolio-position-actions">
        <button class="toggle-btn" type="button" id="botKillBtn">${actionLabel}</button>
      </div>
    </div>`;
}

function wireBotControls() {
  const btn = document.getElementById('botKillBtn');
  if (!btn || !botStatusData) return;
  btn.addEventListener('click', async () => {
    const token = getBotToken();
    if (!token) return;
    const action = botStatusData.kill_switch ? 'resume' : 'kill';
    try {
      const res = await fetch(`${BOT_API_BASE_URL}/${action}`, {
        method: 'POST',
        headers: { 'X-Bot-Token': token },
      });
      if (!res.ok) throw new Error('échec de la requête');
      await loadBotStatus();
      renderPortfolioScreen();
    } catch (e) {
      alert('Action impossible : ' + e.message);
    }
  });
}
```

- [ ] **Step 3: Wire the fetch into the screen's load sequence**

Find the block that fetches `real_portfolio_mt5.json` inside the async function that loads the Portefeuille screen (search for `real_portfolio_mt5.json`). Immediately after that `if (!realPortfolioData) { ... }` block (same function, same indentation level), add:

```javascript
  if (!botStatusData) {
    await loadBotStatus();
  }
```

- [ ] **Step 4: Add the block to the rendered screen and wire its button**

Find `renderPortfolioScreen`'s template literal (search for `${realPortfolioHtml()}`) and add `${botStatusHtml()}` immediately after it:

```javascript
    ${realPortfolioHtml()}
    ${botStatusHtml()}
```

Find where `wirePortfolioForm()`/`wirePortfolioPositionActions(companiesByTicker)` are called at the end of `renderPortfolioScreen` and add a call to `wireBotControls()` alongside them:

```javascript
  wirePortfolioForm();
  wirePortfolioPositionActions(companiesByTicker);
  wireBotControls();
```

- [ ] **Step 5: Bump `CACHE_NAME`**

In `docs/service-worker.js`, find the current `CACHE_NAME` constant and bump it by one (e.g. `analyse-or-shell-v23` → `analyse-or-shell-v24` — confirm the actual current value first, it may have changed since this plan was written).

- [ ] **Step 6: Verify by careful reading (no browser automation available)**

Re-read the full diff and confirm: `botStatusData`/`BOT_API_BASE_URL` declared once, not duplicated; `loadBotStatus`/`getBotToken`/`botStatusHtml`/`wireBotControls` each defined exactly once; `botStatusHtml()` reuses pre-existing CSS classes only (`portfolio-position-row`, `portfolio-position-main`, `company-name`, `hero-sub`, `portfolio-position-actions`, `toggle-btn` — all already used elsewhere in this file, no new CSS needed); the kill-switch token is never sent anywhere except this API's `/kill`/`/resume` requests headers (never logged, never included in a URL).

- [ ] **Step 7: Run the full Python test suite (regression check)**

Run: `python -m pytest`
Expected: unaffected (this task touches no Python files), confirms nothing else broke.

- [ ] **Step 8: Commit**

```bash
git add docs/index.html docs/service-worker.js
git commit -m "feat(portfolio): bloc Bot Or (statut + interrupteur d'urgence) dans Portefeuille

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
