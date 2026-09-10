# Portefeuille — synchronisation MT5/Vantage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the IBKR read-only portfolio sync (currently displayed in the "Portefeuille" tab) with a MetaTrader 5 / Vantage sync via the MetaApi.cloud REST API, keeping the IBKR code in the repo but disabling its daily cron.

**Architecture:** A new `portfolio_sync_mt5.py` script (pure functions + a thin `main()`, mirroring `portfolio_sync.py`'s already-established graceful-degradation pattern) calls MetaApi's stateless REST endpoint once daily via a new GitHub Actions cron workflow, writing `docs/real_portfolio_mt5.json`. `docs/index.html`'s existing "Positions réelles (IBKR)" section is replaced in place with a "Positions réelles (MT5)" section reading the new file. The IBKR workflow's `schedule:` trigger is removed (kept as `workflow_dispatch:`-only) so it stops running daily without deleting any IBKR code.

**Tech Stack:** Python 3.12, `requests` (already a dependency — no new ones), pytest, vanilla JS in `docs/index.html`, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-10-portefeuille-mt5-sync-design.md`

## Global Constraints

- **Never persist or log any numeric/monetary field** from MetaApi's position data (`profit`, `volume`, `openPrice`, `currentPrice`, `swap`, `commission`, etc.) — only `symbol`, a translated `type` (`"achat"`/`"vente"`), and `pnl_sign` (`"positif"`/`"négatif"`, the *sign* of `profit`, never its value) may appear in `docs/real_portfolio_mt5.json`.
- No ticker matching / enrichment against `indices.json` for MT5 positions in this phase — display raw MetaApi data only (symbol, direction, sign).
- `docs/real_portfolio_mt5.json`'s `sync_status` ∈ `"ok" | "error" | "not_configured"` — `"not_configured"` (not `"error"`) when `METAAPI_TOKEN`/`METAAPI_ACCOUNT_ID` are absent, so the public site never shows a red failure banner before secrets are configured.
- `_write_real_portfolio` must use `json.dump(..., allow_nan=False)` — never silently persist invalid JSON.
- On any `requests.exceptions.RequestException`, persist only a sanitized message (generic text + HTTP status code if available), never `str(exception)` — mirrors the IBKR token-leak fix already shipped in `portfolio_sync.py`.
- The IBKR code (`portfolio_sync.py`, `tests/test_portfolio_sync.py`, `.github/workflows/portfolio_sync.yml`) is **not deleted** — only `.github/workflows/portfolio_sync.yml`'s `schedule:` trigger is removed.
- `docs/service-worker.js`'s `CACHE_NAME` must be bumped whenever `docs/index.html` changes (current value: `analyse-or-shell-v22`).
- No new pip dependencies — `requests` (already in `requirements.txt`) is sufficient; the MetaApi response is JSON, so no XML parsing is needed either.

---

### Task 1: MetaApi fetch + public-data transform (`portfolio_sync_mt5.py`)

**Files:**
- Create: `portfolio_sync_mt5.py`
- Test: `tests/test_portfolio_sync_mt5.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `fetch_positions(token: str, account_id: str, region: str = "london") -> list[dict]` (raises `requests.exceptions.RequestException` subclasses on HTTP errors) and `to_public_positions(raw_positions: list[dict]) -> list[dict]` (returns dicts with exactly the keys `symbol`, `type`, `pnl_sign`) — both consumed by Task 2's `main()`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_portfolio_sync_mt5.py`:

```python
import json

import pytest
import portfolio_sync_mt5


class _FakeMT5Response:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise portfolio_sync_mt5.requests.exceptions.HTTPError(
                f"{self.status_code} Client Error", response=self
            )

    def json(self):
        return self._json_data


def test_fetch_positions_returns_parsed_json_on_success(monkeypatch):
    raw = [{"symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "profit": 12.5}]
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return _FakeMT5Response(raw)

    monkeypatch.setattr(portfolio_sync_mt5.requests, "get", fake_get)
    result = portfolio_sync_mt5.fetch_positions("tok", "acc123", region="london")

    assert result == raw
    assert captured["url"] == (
        "https://mt-client-api-v1.london.agiliumtrade.ai"
        "/users/current/accounts/acc123/positions"
    )
    assert captured["headers"] == {"auth-token": "tok"}


def test_fetch_positions_uses_default_region_when_not_specified(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        return _FakeMT5Response([])

    monkeypatch.setattr(portfolio_sync_mt5.requests, "get", fake_get)
    portfolio_sync_mt5.fetch_positions("tok", "acc123")

    assert "mt-client-api-v1.london.agiliumtrade.ai" in captured["url"]


def test_fetch_positions_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(
        portfolio_sync_mt5.requests, "get",
        lambda *a, **k: _FakeMT5Response([], status_code=401),
    )
    with pytest.raises(portfolio_sync_mt5.requests.exceptions.HTTPError):
        portfolio_sync_mt5.fetch_positions("bad-token", "acc123")


def test_to_public_positions_maps_buy_and_sell_types():
    raw = [
        {"symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "profit": 12.5},
        {"symbol": "EURUSD", "type": "POSITION_TYPE_SELL", "profit": -3.0},
    ]
    result = portfolio_sync_mt5.to_public_positions(raw)
    assert result[0]["symbol"] == "XAUUSD"
    assert result[0]["type"] == "achat"
    assert result[1]["symbol"] == "EURUSD"
    assert result[1]["type"] == "vente"


def test_to_public_positions_maps_profit_sign():
    raw = [
        {"symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "profit": 12.5},
        {"symbol": "EURUSD", "type": "POSITION_TYPE_SELL", "profit": -3.0},
        {"symbol": "US30", "type": "POSITION_TYPE_BUY", "profit": 0},
    ]
    result = portfolio_sync_mt5.to_public_positions(raw)
    assert result[0]["pnl_sign"] == "positif"
    assert result[1]["pnl_sign"] == "négatif"
    assert result[2]["pnl_sign"] == "positif"  # profit == 0 compte comme positif


def test_to_public_positions_defaults_missing_profit_to_positive():
    raw = [{"symbol": "XAUUSD", "type": "POSITION_TYPE_BUY"}]
    result = portfolio_sync_mt5.to_public_positions(raw)
    assert result[0]["pnl_sign"] == "positif"


def test_to_public_positions_never_includes_numeric_fields():
    raw = [{
        "symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "profit": 12.5,
        "volume": 0.1, "openPrice": 2000.0, "currentPrice": 2010.0,
        "swap": -0.5, "commission": -0.5,
    }]
    result = portfolio_sync_mt5.to_public_positions(raw)
    forbidden = {"profit", "volume", "openPrice", "currentPrice", "swap", "commission"}
    for position in result:
        assert forbidden.isdisjoint(position.keys())
        assert set(position.keys()) == {"symbol", "type", "pnl_sign"}


def test_to_public_positions_empty_list_returns_empty_list():
    assert portfolio_sync_mt5.to_public_positions([]) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_portfolio_sync_mt5.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'portfolio_sync_mt5'`

- [ ] **Step 3: Write the implementation**

Create `portfolio_sync_mt5.py`:

```python
# portfolio_sync_mt5.py
# Synchronisation en lecture seule des positions MetaTrader 5 (compte
# Vantage) via l'API REST MetaApi.cloud — voir
# docs/superpowers/specs/2026-09-10-portefeuille-mt5-sync-design.md.
# Le compte MT5 est enregistré chez MetaApi avec le mot de passe
# investisseur : il est structurellement incapable de passer un ordre.
import requests

DEFAULT_MT5_REGION = "london"


def _base_url(region: str) -> str:
    return f"https://mt-client-api-v1.{region}.agiliumtrade.ai"


def fetch_positions(token: str, account_id: str, region: str = DEFAULT_MT5_REGION) -> list[dict]:
    """Appelle l'endpoint MetaApi qui renvoie les positions ouvertes du
    compte MT5. Contrairement à IBKR, une seule requête HTTP synchrone
    suffit — pas de génération asynchrone à attendre. Lève une
    requests.exceptions.RequestException (ex. HTTPError) sur toute
    erreur HTTP (401 jeton invalide, 404 compte introuvable, etc.)."""
    resp = requests.get(
        f"{_base_url(region)}/users/current/accounts/{account_id}/positions",
        headers={"auth-token": token},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def to_public_positions(raw_positions: list[dict]) -> list[dict]:
    """Transforme chaque position brute MetaApi en dict public minimal
    — ne construit jamais profit/volume/openPrice/currentPrice ni
    aucun champ numérique, ce fichier étant publié publiquement (voir
    la section Confidentialité du spec). Seul le signe du profit est
    conservé, jamais sa valeur. profit == 0 ou absent compte comme
    "positif" (convention arbitraire mais sans conséquence : aucun
    montant n'est de toute façon affiché)."""
    positions = []
    for p in raw_positions:
        position_type = "achat" if p.get("type") == "POSITION_TYPE_BUY" else "vente"
        pnl_sign = "positif" if p.get("profit", 0) >= 0 else "négatif"
        positions.append({
            "symbol": p.get("symbol"),
            "type": position_type,
            "pnl_sign": pnl_sign,
        })
    return positions
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_portfolio_sync_mt5.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add portfolio_sync_mt5.py tests/test_portfolio_sync_mt5.py
git commit -m "feat(portfolio-sync-mt5): appel MetaApi + transformation en données publiques

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `main()` + cron workflow + disable IBKR cron

**Files:**
- Modify: `portfolio_sync_mt5.py` (append `main()` and its file-I/O helpers)
- Modify: `tests/test_portfolio_sync_mt5.py` (append `main()` tests)
- Create: `.github/workflows/portfolio_sync_mt5.yml`
- Modify: `.github/workflows/portfolio_sync.yml:1-6` (remove the `schedule:` trigger)

**Interfaces:**
- Consumes: `fetch_positions(token, account_id, region)` and `to_public_positions(raw_positions)` from Task 1 (exact signatures above).
- Produces: `docs/real_portfolio_mt5.json` with shape `{"updated": str|null, "sync_status": "ok"|"error"|"not_configured", "sync_error": str|null, "positions": [{"symbol": str, "type": "achat"|"vente", "pnl_sign": "positif"|"négatif"}]}` — consumed by Task 3's frontend code.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_portfolio_sync_mt5.py`:

```python
def test_main_writes_not_configured_status_when_credentials_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("METAAPI_TOKEN", raising=False)
    monkeypatch.delenv("METAAPI_ACCOUNT_ID", raising=False)
    output_path = tmp_path / "real_portfolio_mt5.json"
    monkeypatch.setattr(portfolio_sync_mt5, "REAL_PORTFOLIO_JSON_PATH", str(output_path))

    portfolio_sync_mt5.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["sync_status"] == "not_configured"
    assert written["sync_error"] is None
    assert written["positions"] == []


def test_main_writes_ok_status_and_positions_on_success(monkeypatch, tmp_path):
    monkeypatch.setenv("METAAPI_TOKEN", "tok")
    monkeypatch.setenv("METAAPI_ACCOUNT_ID", "acc123")
    output_path = tmp_path / "real_portfolio_mt5.json"
    monkeypatch.setattr(portfolio_sync_mt5, "REAL_PORTFOLIO_JSON_PATH", str(output_path))
    monkeypatch.setattr(
        portfolio_sync_mt5, "fetch_positions",
        lambda token, account_id, region=portfolio_sync_mt5.DEFAULT_MT5_REGION: [
            {"symbol": "XAUUSD", "type": "POSITION_TYPE_BUY", "profit": 12.5},
        ],
    )

    portfolio_sync_mt5.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["sync_status"] == "ok"
    assert written["sync_error"] is None
    assert written["positions"] == [{"symbol": "XAUUSD", "type": "achat", "pnl_sign": "positif"}]


def test_main_keeps_previous_positions_and_sets_error_on_fetch_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("METAAPI_TOKEN", "tok")
    monkeypatch.setenv("METAAPI_ACCOUNT_ID", "acc123")
    output_path = tmp_path / "real_portfolio_mt5.json"
    output_path.write_text(
        json.dumps({
            "updated": "2026-09-09T07:00:00Z", "sync_status": "ok", "sync_error": None,
            "positions": [{"symbol": "XAUUSD", "type": "achat", "pnl_sign": "positif"}],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(portfolio_sync_mt5, "REAL_PORTFOLIO_JSON_PATH", str(output_path))

    def _raise(token, account_id, region=portfolio_sync_mt5.DEFAULT_MT5_REGION):
        raise portfolio_sync_mt5.requests.exceptions.HTTPError("401 Client Error")

    monkeypatch.setattr(portfolio_sync_mt5, "fetch_positions", _raise)

    portfolio_sync_mt5.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["sync_status"] == "error"
    assert written["positions"] == [{"symbol": "XAUUSD", "type": "achat", "pnl_sign": "positif"}]


def test_main_starts_from_empty_state_when_output_file_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("METAAPI_TOKEN", raising=False)
    output_path = tmp_path / "does_not_exist" / "real_portfolio_mt5.json"
    monkeypatch.setattr(portfolio_sync_mt5, "REAL_PORTFOLIO_JSON_PATH", str(output_path))

    portfolio_sync_mt5.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["positions"] == []


def test_write_real_portfolio_never_emits_invalid_json_nan(monkeypatch, tmp_path):
    output_path = tmp_path / "real_portfolio_mt5.json"
    monkeypatch.setattr(portfolio_sync_mt5, "REAL_PORTFOLIO_JSON_PATH", str(output_path))

    with pytest.raises(ValueError):
        portfolio_sync_mt5._write_real_portfolio({"positions": [{"x": float("nan")}]})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_portfolio_sync_mt5.py -v`
Expected: the 5 new tests FAIL with `AttributeError: module 'portfolio_sync_mt5' has no attribute 'main'` (or `REAL_PORTFOLIO_JSON_PATH`); the 7 Task 1 tests still PASS.

- [ ] **Step 3: Write the implementation**

Append to `portfolio_sync_mt5.py` (add these imports at the top, alongside the existing `import requests`):

```python
import json
import os
import traceback
from datetime import datetime, timezone
```

Then append at the end of the file:

```python
REAL_PORTFOLIO_JSON_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "docs", "real_portfolio_mt5.json"
)


def _load_existing_real_portfolio() -> dict:
    """État de repli si le fichier n'existe pas encore ou est illisible —
    jamais d'exception au démarrage du script."""
    try:
        with open(REAL_PORTFOLIO_JSON_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"updated": None, "sync_status": "ok", "sync_error": None, "positions": []}


def _write_real_portfolio(payload: dict) -> None:
    os.makedirs(os.path.dirname(REAL_PORTFOLIO_JSON_PATH), exist_ok=True)
    with open(REAL_PORTFOLIO_JSON_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, allow_nan=False)


def main():
    token = os.environ.get("METAAPI_TOKEN")
    account_id = os.environ.get("METAAPI_ACCOUNT_ID")
    region = os.environ.get("METAAPI_REGION", DEFAULT_MT5_REGION)
    payload = _load_existing_real_portfolio()
    payload["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not token or not account_id:
        payload["sync_status"] = "not_configured"
        payload["sync_error"] = None
        _write_real_portfolio(payload)
        print("Synchronisation MT5 non configurée (secrets absents) — étape attendue avant la configuration du compte.")
        return

    try:
        raw_positions = fetch_positions(token, account_id, region)
        positions = to_public_positions(raw_positions)
    except requests.exceptions.RequestException as e:
        # Même précaution que pour IBKR : ne jamais persister le message
        # brut d'une exception requests dans un fichier publié
        # publiquement. Le détail complet part quand même sur stdout
        # (traceback compris) : GitHub Actions masque automatiquement
        # la valeur du secret dans tous les logs d'un job qui la
        # référence via env:.
        status = getattr(getattr(e, "response", None), "status_code", None)
        payload["sync_status"] = "error"
        payload["sync_error"] = (
            f"Erreur réseau MT5 (HTTP {status})" if status else "Erreur réseau MT5 (pas de réponse)"
        )
        _write_real_portfolio(payload)
        print(f"Erreur synchronisation MT5 (réseau) : HTTP {status}")
        traceback.print_exc()
        return
    except Exception as e:
        payload["sync_status"] = "error"
        payload["sync_error"] = str(e)
        _write_real_portfolio(payload)
        print(f"Erreur synchronisation MT5 : {e}")
        traceback.print_exc()
        return

    payload["sync_status"] = "ok"
    payload["sync_error"] = None
    payload["positions"] = positions
    _write_real_portfolio(payload)
    print(f"Synchronisation MT5 réussie : {len(positions)} position(s).")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_portfolio_sync_mt5.py -v`
Expected: 13 passed

- [ ] **Step 5: Create the MT5 cron workflow**

Create `.github/workflows/portfolio_sync_mt5.yml`:

```yaml
name: Synchronisation portefeuille MT5

on:
  schedule:
    - cron: "0 8 * * *"   # une fois par jour, 08h00 UTC (décalé des crons Indices 07h00 et IBKR 07h00)
  workflow_dispatch: {}

concurrency:
  group: portfolio-sync-mt5
  cancel-in-progress: false

permissions:
  contents: write

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - name: Récupérer le dépôt
        uses: actions/checkout@v4

      - name: Installer Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Installer les dépendances
        run: pip install -r requirements.txt

      - name: Synchroniser les positions MT5
        run: python3 portfolio_sync_mt5.py
        env:
          METAAPI_TOKEN: ${{ secrets.METAAPI_TOKEN }}
          METAAPI_ACCOUNT_ID: ${{ secrets.METAAPI_ACCOUNT_ID }}

      - name: Publier les données mises à jour sur le site
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add docs/real_portfolio_mt5.json
          git diff --staged --quiet || git commit -m "Mise à jour de la synchronisation portefeuille MT5"
          git pull --rebase --autostash
          git push
```

- [ ] **Step 6: Disable the IBKR workflow's daily cron**

In `.github/workflows/portfolio_sync.yml`, replace the `on:` block (lines 3-6):

```yaml
on:
  schedule:
    - cron: "0 7 * * *"   # une fois par jour, 07h00 UTC (décalé du cron Indices à 06h00)
  workflow_dispatch: {}
```

with:

```yaml
on:
  workflow_dispatch: {}
```

Leave every other line in the file (`concurrency:`, `permissions:`, `jobs:`, all steps) exactly as-is — the workflow still exists and can be triggered manually, it just no longer runs automatically every day.

- [ ] **Step 7: Commit**

```bash
git add portfolio_sync_mt5.py tests/test_portfolio_sync_mt5.py \
        .github/workflows/portfolio_sync_mt5.yml .github/workflows/portfolio_sync.yml
git commit -m "feat(portfolio-sync-mt5): main() + workflow cron quotidien, désactive le cron IBKR

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Frontend — replace "Positions réelles (IBKR)" with "Positions réelles (MT5)"

**Files:**
- Modify: `docs/index.html` (the fetch block around line 1045-1052, and the `realPortfolioHtml` function around line 1180-1228, plus its call site around line 1071)
- Modify: `docs/service-worker.js` (`CACHE_NAME` bump, network-first path list)

**Interfaces:**
- Consumes: `docs/real_portfolio_mt5.json` with the shape produced by Task 2's `main()` (`{"updated", "sync_status", "sync_error", "positions": [{"symbol", "type", "pnl_sign"}]}`).
- Produces: nothing consumed by later tasks (this is the last task).

Before editing, re-read the current exact content of `docs/index.html` around lines 1040-1230 with the Read tool — the line numbers above are from a recent read and may have drifted slightly; find the anchors by searching for `realPortfolioData` and `function realPortfolioHtml` rather than trusting the line numbers blindly.

- [ ] **Step 1: Replace the fetch block**

Find this block (currently around line 1045-1052):

```javascript
  if (!realPortfolioData) {
    try {
      const res = await fetch('real_portfolio.json?t=' + Date.now());
      realPortfolioData = res.ok ? await res.json() : null;
    } catch (e) {
      realPortfolioData = null;
    }
  }
```

Replace with:

```javascript
  if (!realPortfolioData) {
    try {
      const res = await fetch('real_portfolio_mt5.json?t=' + Date.now());
      realPortfolioData = res.ok ? await res.json() : null;
    } catch (e) {
      realPortfolioData = null;
    }
  }
```

(Only the filename changes — `real_portfolio.json` → `real_portfolio_mt5.json`. The variable name `realPortfolioData` is kept as-is; it now holds MT5 data instead of IBKR data.)

- [ ] **Step 2: Update the call site to drop the now-unused argument**

Find (currently around line 1071, inside `renderPortfolioScreen`):

```javascript
    ${realPortfolioHtml(companiesByTicker)}
```

Replace with:

```javascript
    ${realPortfolioHtml()}
```

(`companiesByTicker` is still used elsewhere in `renderPortfolioScreen` for the manual positions — do not remove that variable, only stop passing it to `realPortfolioHtml`, which no longer does ticker matching.)

- [ ] **Step 3: Replace the `realPortfolioHtml` function**

Find the entire function (currently lines 1180-1228, from `function realPortfolioHtml(companiesByTicker) {` through its closing `}`):

```javascript
function realPortfolioHtml(companiesByTicker) {
  if (!realPortfolioData) {
    return `
      <h2>Positions réelles (IBKR)</h2>
      <div class="empty">Synchronisation IBKR pas encore disponible.</div>`;
  }
  if (realPortfolioData.sync_status === 'not_configured') {
    return `
      <h2>Positions réelles (IBKR)</h2>
      <div class="empty">Synchronisation IBKR pas encore configurée.</div>`;
  }
  const warningHtml = realPortfolioData.sync_status === 'error'
    ? `<p class="portfolio-form-error">Dernière synchronisation IBKR échouée : ${escHtml(realPortfolioData.sync_error)}</p>`
    : '';
  const positions = realPortfolioData.positions || [];
  if (!positions.length) {
    return `
      <h2>Positions réelles (IBKR)</h2>
      ${warningHtml}
      <div class="empty">Aucune position détenue chez IBKR pour l'instant.</div>`;
  }
  const rowsHtml = positions.map(p => {
    const company = p.matched_ticker ? companiesByTicker[p.matched_ticker] : null;
    const badgesHtml = company
      ? `${company.is_financial ? '<span class="financial-tag">Profil financier</span>' : ''}${hasMajorNewsAlert(company) ? '<span class="financial-tag majeure-tag">Actu majeure</span>' : ''}${allIndicesBadgesHtml(company)}`
      : '';
    const nameHtml = company
      ? `<a class="company-name" href="#indices/${company.ticker}">${company.name}</a>`
      : `<span class="company-name">${escHtml(p.description || p.ibkr_symbol)}</span>`;
    const pnlHtml = p.pnl_pct == null
      ? '<span class="hero-sub">Performance indisponible</span>'
      : `<span style="color:${p.pnl_pct >= 0 ? 'var(--gold)' : 'var(--rust)'}">${formatPct(p.pnl_pct)}</span>`;
    return `
      <div class="portfolio-position-row">
        <div class="portfolio-position-main">
          ${nameHtml}
          <span class="company-ticker">${escHtml(p.ibkr_symbol)}${badgesHtml}</span>
        </div>
        <div class="portfolio-position-pnl">
          ${pnlHtml}
          ${company ? `<span class="company-score" style="color:${company.score >= 0 ? 'var(--gold)' : 'var(--rust)'};">${company.score > 0 ? '+' : ''}${company.score}</span>` : ''}
        </div>
      </div>`;
  }).join('');
  return `
    <h2>Positions réelles (IBKR)</h2>
    ${warningHtml}
    <div class="portfolio-position-list">${rowsHtml}</div>`;
}
```

Replace with:

```javascript
function realPortfolioHtml() {
  if (!realPortfolioData) {
    return `
      <h2>Positions réelles (MT5)</h2>
      <div class="empty">Synchronisation MT5 pas encore disponible.</div>`;
  }
  if (realPortfolioData.sync_status === 'not_configured') {
    return `
      <h2>Positions réelles (MT5)</h2>
      <div class="empty">Synchronisation MT5 pas encore configurée.</div>`;
  }
  const warningHtml = realPortfolioData.sync_status === 'error'
    ? `<p class="portfolio-form-error">Dernière synchronisation MT5 échouée : ${escHtml(realPortfolioData.sync_error)}</p>`
    : '';
  const positions = realPortfolioData.positions || [];
  if (!positions.length) {
    return `
      <h2>Positions réelles (MT5)</h2>
      ${warningHtml}
      <div class="empty">Aucune position détenue chez MT5 pour l'instant.</div>`;
  }
  const rowsHtml = positions.map(p => `
    <div class="portfolio-position-row">
      <div class="portfolio-position-main">
        <span class="company-name">${escHtml(p.symbol)}</span>
        <span class="company-ticker">${p.type === 'achat' ? 'Achat' : 'Vente'}</span>
      </div>
      <div class="portfolio-position-pnl">
        <span style="color:${p.pnl_sign === 'positif' ? 'var(--gold)' : 'var(--rust)'}">${p.pnl_sign === 'positif' ? '▲' : '▼'}</span>
      </div>
    </div>`).join('');
  return `
    <h2>Positions réelles (MT5)</h2>
    ${warningHtml}
    <div class="portfolio-position-list">${rowsHtml}</div>`;
}
```

This reuses five pre-existing CSS classes (`portfolio-position-row`, `portfolio-position-main`, `company-name`, `company-ticker`, `portfolio-position-pnl`) already defined for the manual-positions section — no new CSS needed. `escHtml` and `formatPct`-adjacent helpers already exist elsewhere in this file (added in the IBKR fix round) — `escHtml` is reused here, `formatPct` is no longer called since no numbers are shown.

- [ ] **Step 4: Bump `CACHE_NAME` and widen the network-first path list**

In `docs/service-worker.js`, find:

```javascript
const CACHE_NAME = "analyse-or-shell-v22";
```

Replace with:

```javascript
const CACHE_NAME = "analyse-or-shell-v23";
```

Find:

```javascript
  if (url.pathname.endsWith("score.json") || url.pathname.endsWith("indices.json") || url.pathname.endsWith("real_portfolio.json")) {
```

Replace with:

```javascript
  if (url.pathname.endsWith("score.json") || url.pathname.endsWith("indices.json") || url.pathname.endsWith("real_portfolio.json") || url.pathname.endsWith("real_portfolio_mt5.json")) {
```

- [ ] **Step 5: Verify by careful reading (no browser automation available)**

Re-read the full modified `realPortfolioHtml` function and its call site to confirm: no reference to `matched_ticker`, `companiesByTicker`, `ibkr_symbol`, `description`, `pnl_pct`, `company.score`, or any IBKR-specific text remains in the new function; `companiesByTicker` is still declared and used elsewhere in `renderPortfolioScreen` (for `portfolioPositionsHtml`); the h2 title reads "Positions réelles (MT5)" in all three return branches.

- [ ] **Step 6: Run the full Python test suite (regression check)**

Run: `python -m pytest`
Expected: all tests still pass (this task touches no Python files, but confirms nothing else broke).

- [ ] **Step 7: Commit**

```bash
git add docs/index.html docs/service-worker.js
git commit -m "feat(portfolio): remplace la section IBKR par Positions réelles (MT5)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```
