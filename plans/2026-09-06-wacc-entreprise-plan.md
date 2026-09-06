# Coût du capital réel par entreprise (WACC) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat `COST_OF_CAPITAL_PROXY = 8.0` used across the Indices pipeline with a WACC computed per company (CAPM cost of equity + size premium, blended with an after-tax cost of debt), falling back to the flat proxy for any company where the required inputs are missing.

**Architecture:** A new pure function `estimate_wacc(...)` (plus its `_size_premium` helper) computes the per-company WACC from data already exposed by `fetch_company_financials`/`extract_ratios` (extended with 3 new raw fields: `tax_rate`, `total_debt`, `beta`) and one shared input fetched once per run (`fetch_risk_free_rate()`, a new FRED call). `estimate_dcf_price` and `estimate_valuation_targets` change from reading the global `COST_OF_CAPITAL_PROXY` constant directly to receiving the resolved cost-of-capital as a parameter, so `build_company_entry` can pass either the real WACC or the fallback proxy uniformly to every consumer.

**Tech Stack:** Same as the existing pipeline — pure Python, `requests` (already a dependency) for the new FRED call. No new pip dependency.

**Spec:** `specs/2026-09-06-wacc-entreprise-design.md`

## Global Constraints

- `estimate_wacc` must never raise — any missing/invalid input (`risk_free_rate`, `beta`, `market_cap`, `total_debt`, `tax_rate`) returns `None`; the caller (`build_company_entry`) falls back to `COST_OF_CAPITAL_PROXY` for that company only.
- `fetch_risk_free_rate` must never raise — missing `FRED_API_KEY` or any network/API failure returns `None` (no network call at all when the key is absent).
- Exact constant names/values: `MARKET_RISK_PREMIUM = 5.0`, `DEBT_INTEREST_RATE_PROXY = 3.0`, `FRED_RISK_FREE_SERIES = "IRLTLT01FRM156N"`, `SIZE_PREMIUM_BANDS = [(50_000_000_000, 0.0), (10_000_000_000, 0.5), (2_000_000_000, 1.5), (0, 3.0)]`.
- `DEBT_INTEREST_RATE_PROXY` replaces the inline `0.03` literal already used by `icr` in `extract_ratios` — same behavior, single named source of truth.
- New top-level company entry field: `wacc` (float, always present — never `null`; equals `COST_OF_CAPITAL_PROXY` on fallback).
- `risk_free_rate` is fetched once per run (in `main()`), not once per company.
- No new pip dependency.

---

## Task 1: Expose `tax_rate`/`total_debt`, extract `DEBT_INTEREST_RATE_PROXY`

**Files:**
- Modify: `indices_score.py:118-120` (add constant near existing leverage/coverage constants)
- Modify: `indices_score.py:388` (`icr` calculation, DRY refactor)
- Modify: `indices_score.py:437-452` (`extract_ratios`'s return block, add 2 keys)
- Modify: `tests/test_indices_score.py` (extend `test_extract_ratios_computes_expected_keys`'s key list)

**Interfaces:**
- Produces: `DEBT_INTEREST_RATE_PROXY = 3.0` (module constant), `extract_ratios(...)`'s returned dict gains `"tax_rate": float` and `"total_debt": float` (both already computed as local variables `tax_rate[latest]` and `total_debt[latest]` inside the function). Used by Task 4 (via `_fake_ratios()`/`build_company_entry`) and Task 7.

- [ ] **Step 1: Write the failing test change**

In `tests/test_indices_score.py`, `test_extract_ratios_computes_expected_keys`'s key list currently reads:

```python
    for key in [
        "roce", "roe", "net_debt_ebitda", "icr", "cagr_ca", "cagr_ebitda",
        "fcf_conversion", "current_ev_ebitda", "avg_ev_ebitda_5y",
        "current_pe", "avg_pe_5y", "fcf", "net_debt", "equity",
    ]:
```

Add `"tax_rate"` and `"total_debt"` to the end of the list:

```python
    for key in [
        "roce", "roe", "net_debt_ebitda", "icr", "cagr_ca", "cagr_ebitda",
        "fcf_conversion", "current_ev_ebitda", "avg_ev_ebitda_5y",
        "current_pe", "avg_pe_5y", "fcf", "net_debt", "equity",
        "tax_rate", "total_debt",
    ]:
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_indices_score.py -k "test_extract_ratios_computes_expected_keys" -v`
Expected: FAIL — `clé manquante : tax_rate`

- [ ] **Step 3: Add the constant**

In `indices_score.py`, the three leverage/coverage constants currently read:

```python
NET_DEBT_EBITDA_COMFORTABLE = 3.0   # seuil Standard, ajusté par profil sectoriel
NET_DEBT_EBITDA_RISKY = 5.5         # seuil Standard, ajusté par profil sectoriel
ICR_CRITICAL = 3.0                  # seuil Standard, ajusté par profil sectoriel
```

Add a fourth constant immediately after:

```python
NET_DEBT_EBITDA_COMFORTABLE = 3.0   # seuil Standard, ajusté par profil sectoriel
NET_DEBT_EBITDA_RISKY = 5.5         # seuil Standard, ajusté par profil sectoriel
ICR_CRITICAL = 3.0                  # seuil Standard, ajusté par profil sectoriel
DEBT_INTEREST_RATE_PROXY = 3.0      # % taux d'intérêt proxy sur la dette totale
                                     # (frais financiers non fiablement isolés
                                     # chez ces entreprises) — utilisé pour l'ICR
                                     # et repris tel quel pour le coût de la
                                     # dette dans le calcul du WACC (Task 4).
```

- [ ] **Step 4: Refactor `icr` to use the named constant**

In `indices_score.py`, replace:

```python
    icr = ebit[latest] / (total_debt[latest] * 0.03) if total_debt[latest] else 10.0  # proxy frais financiers si non isolés
```

with:

```python
    icr = (
        ebit[latest] / (total_debt[latest] * DEBT_INTEREST_RATE_PROXY / 100)
        if total_debt[latest] else 10.0
    )  # proxy frais financiers si non isolés (DEBT_INTEREST_RATE_PROXY)
```

(Behavior is unchanged: `DEBT_INTEREST_RATE_PROXY / 100 == 0.03`.)

- [ ] **Step 5: Expose the 2 new raw fields**

In `indices_score.py`, `extract_ratios`'s return block currently ends with:

```python
        "fcf": fcf,
        "net_debt": net_debt_latest,
        "equity": equity[latest],
    }
```

Replace with:

```python
        "fcf": fcf,
        "net_debt": net_debt_latest,
        "equity": equity[latest],
        "tax_rate": tax_rate[latest],
        "total_debt": total_debt[latest],
    }
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `pytest tests/test_indices_score.py -k "test_extract_ratios_computes_expected_keys" -v`
Expected: PASS

- [ ] **Step 7: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (93/93 — no new tests, one existing test extended, `icr` behavior unchanged so no other test should be affected)

- [ ] **Step 8: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): exposer tax_rate/total_debt, extraire DEBT_INTEREST_RATE_PROXY"
```

---

## Task 2: Expose `beta` from `fetch_company_financials`

**Files:**
- Modify: `indices_score.py` (`fetch_company_financials`'s return block)

**Interfaces:**
- Produces: `fetch_company_financials(...)`'s returned dict gains `"beta": float | None` (from yfinance's `info` dict, already fetched in this function). Used by Task 7.

No new test for this task — same precedent as the rest of this function (live yfinance I/O, never directly unit-tested; every test monkeypatches the whole function).

- [ ] **Step 1: Implement**

In `indices_score.py`, `fetch_company_financials` currently ends with:

```python
    ratios = extract_ratios(financials, balance_sheet, cashflow, closes_by_year, shares_outstanding)
    ratios["sector"] = info.get("sector")
    ratios["ecart_pct_ma200"] = ecart_pct_ma200
    ratios["quarterly_yoy_growth_ca"] = extract_quarterly_growth(quarterly_financials)
    ratios["current_price"] = current_price
    ratios["ma200"] = ma200
    ratios["shares_outstanding"] = shares_outstanding
    return ratios
```

Replace with:

```python
    ratios = extract_ratios(financials, balance_sheet, cashflow, closes_by_year, shares_outstanding)
    ratios["sector"] = info.get("sector")
    ratios["ecart_pct_ma200"] = ecart_pct_ma200
    ratios["quarterly_yoy_growth_ca"] = extract_quarterly_growth(quarterly_financials)
    ratios["current_price"] = current_price
    ratios["ma200"] = ma200
    ratios["shares_outstanding"] = shares_outstanding
    ratios["beta"] = info.get("beta")
    return ratios
```

- [ ] **Step 2: Run the full suite to confirm nothing else broke**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (93/93 — same count as after Task 1)

- [ ] **Step 3: Commit**

```bash
git add indices_score.py
git commit -m "feat(indices): exposer le bêta yfinance depuis fetch_company_financials"
```

---

## Task 3: `fetch_risk_free_rate` — taux OAT France (FRED)

**Files:**
- Modify: `indices_score.py` (add function; exact placement in Step 3)
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Produces: `fetch_risk_free_rate() -> float | None`, used by Task 7 (called once in `main()`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_indices_score.py`, in a new section right after the `estimate_multiple_based_price` tests and before the `estimate_fair_value` import (find `from indices_score import estimate_fair_value` and insert immediately before it):

```python
import indices_score
from indices_score import fetch_risk_free_rate


class _FakeFredResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_fetch_risk_free_rate_returns_none_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)

    def fail_if_called(*a, **k):
        raise AssertionError("no network call expected without an API key")

    monkeypatch.setattr(indices_score.requests, "get", fail_if_called)
    assert fetch_risk_free_rate() is None


def test_fetch_risk_free_rate_returns_latest_observation(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fred-test-key")
    monkeypatch.setattr(
        indices_score.requests, "get",
        lambda *a, **k: _FakeFredResponse({
            "observations": [
                {"value": "3.60"},
                {"value": "3.68"},
            ]
        })
    )
    assert fetch_risk_free_rate() == 3.68


def test_fetch_risk_free_rate_ignores_missing_observations(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fred-test-key")
    monkeypatch.setattr(
        indices_score.requests, "get",
        lambda *a, **k: _FakeFredResponse({
            "observations": [
                {"value": "3.60"},
                {"value": "."},
            ]
        })
    )
    assert fetch_risk_free_rate() == 3.60


def test_fetch_risk_free_rate_returns_none_on_empty_observations(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fred-test-key")
    monkeypatch.setattr(
        indices_score.requests, "get",
        lambda *a, **k: _FakeFredResponse({"observations": []})
    )
    assert fetch_risk_free_rate() is None


def test_fetch_risk_free_rate_returns_none_on_request_exception(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fred-test-key")

    def raise_error(*a, **k):
        raise requests.RequestException("boom")

    monkeypatch.setattr(indices_score.requests, "get", raise_error)
    assert fetch_risk_free_rate() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k "fetch_risk_free_rate" -v`
Expected: FAIL — `ImportError: cannot import name 'fetch_risk_free_rate'`

- [ ] **Step 3: Implement**

In `indices_score.py`, add this function immediately after `estimate_entry_exit_prices` (added in an earlier plan) and before `estimate_valuation_targets`:

```python
FRED_RISK_FREE_SERIES = "IRLTLT01FRM156N"  # OAT 10 ans (France), FRED/OCDE, mensuel


def fetch_risk_free_rate() -> float | None:
    """Dernier taux OAT 10 ans publié (FRED, série IRLTLT01FRM156N,
    mensuelle avec ~1-2 mois de décalage) — taux sans risque pour le
    CAPM. None si la clé API FRED est absente (aucun appel réseau dans ce
    cas) ou en cas d'échec réseau/API : toutes les entreprises retombent
    alors sur COST_OF_CAPITAL_PROXY pour ce run."""
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        return None
    try:
        start = (datetime.today() - timedelta(days=120)).strftime("%Y-%m-%d")
        resp = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": FRED_RISK_FREE_SERIES,
                "api_key": api_key,
                "file_type": "json",
                "observation_start": start,
            },
            timeout=15,
        )
        resp.raise_for_status()
        obs = [o for o in resp.json()["observations"] if o["value"] != "."]
        if not obs:
            return None
        return float(obs[-1]["value"])
    except Exception:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "fetch_risk_free_rate" -v`
Expected: PASS (5/5 — missing key, latest observation, ignores missing, empty observations, request exception)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (98/98)

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): récupérer le taux sans risque français (fetch_risk_free_rate)"
```

---

## Task 4: `_size_premium` et `estimate_wacc`

**Files:**
- Modify: `indices_score.py` (add constants + 2 functions after `fetch_risk_free_rate`)
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes: `_is_missing` (existing), `DEBT_INTEREST_RATE_PROXY` (Task 1).
- Produces: `_size_premium(market_cap: float) -> float`, `estimate_wacc(risk_free_rate, beta, market_cap, total_debt, tax_rate) -> float | None`. Used by Task 7.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_indices_score.py`, right after the Task 3 tests (before `from indices_score import estimate_fair_value`):

```python
from indices_score import _size_premium, estimate_wacc


def test_size_premium_mega_cap():
    assert _size_premium(60_000_000_000) == 0.0


def test_size_premium_large_cap():
    assert _size_premium(30_000_000_000) == 0.5


def test_size_premium_mid_cap():
    assert _size_premium(5_000_000_000) == 1.5


def test_size_premium_small_cap():
    assert _size_premium(1_000_000_000) == 3.0


def test_size_premium_boundary_is_strict():
    """Une capitalisation pile au seuil ne doit PAS obtenir la tranche
    supérieure (comparaison stricte `>`)."""
    assert _size_premium(50_000_000_000) == 0.5
    assert _size_premium(10_000_000_000) == 1.5
    assert _size_premium(2_000_000_000) == 3.0


def test_estimate_wacc_nominal_case():
    # Re = 3.68 + 1.2*5.0 + 0.0 (méga cap) = 9.68
    # Rd_after_tax = 3.0 * (1 - 0.25) = 2.25
    # E=100, D=50 -> poids E=100/150, D=50/150
    # WACC = (100/150)*9.68 + (50/150)*2.25 = 6.4533... + 0.75 = 7.2033...
    result = estimate_wacc(
        risk_free_rate=3.68, beta=1.2, market_cap=100_000_000_000,
        total_debt=50_000_000_000, tax_rate=0.25,
    )
    assert result == pytest.approx(7.203333333333333)


def test_estimate_wacc_returns_none_when_risk_free_rate_missing():
    assert estimate_wacc(
        risk_free_rate=None, beta=1.2, market_cap=100_000_000_000,
        total_debt=50_000_000_000, tax_rate=0.25,
    ) is None


def test_estimate_wacc_returns_none_when_beta_missing():
    assert estimate_wacc(
        risk_free_rate=3.68, beta=None, market_cap=100_000_000_000,
        total_debt=50_000_000_000, tax_rate=0.25,
    ) is None


def test_estimate_wacc_returns_none_when_market_cap_not_positive():
    assert estimate_wacc(
        risk_free_rate=3.68, beta=1.2, market_cap=0.0,
        total_debt=50_000_000_000, tax_rate=0.25,
    ) is None
    assert estimate_wacc(
        risk_free_rate=3.68, beta=1.2, market_cap=-1.0,
        total_debt=50_000_000_000, tax_rate=0.25,
    ) is None


def test_estimate_wacc_returns_none_when_total_debt_negative():
    assert estimate_wacc(
        risk_free_rate=3.68, beta=1.2, market_cap=100_000_000_000,
        total_debt=-1.0, tax_rate=0.25,
    ) is None


def test_estimate_wacc_returns_none_when_tax_rate_missing():
    assert estimate_wacc(
        risk_free_rate=3.68, beta=1.2, market_cap=100_000_000_000,
        total_debt=50_000_000_000, tax_rate=None,
    ) is None


def test_estimate_wacc_returns_none_when_any_input_is_nan():
    assert estimate_wacc(
        risk_free_rate=float("nan"), beta=1.2, market_cap=100_000_000_000,
        total_debt=50_000_000_000, tax_rate=0.25,
    ) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k "size_premium or estimate_wacc" -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement**

In `indices_score.py`, add immediately after `fetch_risk_free_rate` and before `estimate_valuation_targets`:

```python
MARKET_RISK_PREMIUM = 5.0        # % prime de risque marché (hypothèse fixe)

SIZE_PREMIUM_BANDS = [
    (50_000_000_000, 0.0),
    (10_000_000_000, 0.5),
    (2_000_000_000, 1.5),
    (0, 3.0),
]


def _size_premium(market_cap: float) -> float:
    """Prime de taille (points ajoutés au coût des fonds propres) selon
    la capitalisation boursière — parcourt les bandes de la plus grande à
    la plus petite, renvoie la première dont le seuil est strictement
    dépassé."""
    for threshold, premium in SIZE_PREMIUM_BANDS:
        if market_cap > threshold:
            return premium
    return SIZE_PREMIUM_BANDS[-1][1]


def estimate_wacc(
    risk_free_rate: float | None,
    beta: float | None,
    market_cap: float | None,
    total_debt: float | None,
    tax_rate: float | None,
) -> float | None:
    """WACC par entreprise (CAPM + prime de taille, Vernimmen). None si une
    donnée nécessaire manque/est invalide — le repli sur
    COST_OF_CAPITAL_PROXY se fait chez l'appelant, pas ici."""
    if (
        risk_free_rate is None or _is_missing(risk_free_rate)
        or beta is None or _is_missing(beta)
        or market_cap is None or _is_missing(market_cap) or market_cap <= 0
        or total_debt is None or _is_missing(total_debt) or total_debt < 0
        or tax_rate is None or _is_missing(tax_rate)
    ):
        return None
    cost_of_equity = risk_free_rate + beta * MARKET_RISK_PREMIUM + _size_premium(market_cap)
    cost_of_debt_after_tax = DEBT_INTEREST_RATE_PROXY * (1 - tax_rate)
    total_capital = market_cap + total_debt
    equity_weight = market_cap / total_capital
    debt_weight = total_debt / total_capital
    return equity_weight * cost_of_equity + debt_weight * cost_of_debt_after_tax
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "size_premium or estimate_wacc" -v`
Expected: PASS (12/12 — 5 `_size_premium` + 7 `estimate_wacc`)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (110/110)

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): calcul du WACC par entreprise (CAPM + prime de taille)"
```

---

## Task 5: `estimate_dcf_price` reçoit le taux d'actualisation en paramètre

**Files:**
- Modify: `indices_score.py:665-692` (`estimate_dcf_price`)
- Modify: `tests/test_indices_score.py` (7 existing tests updated)

**Interfaces:**
- Produces: `estimate_dcf_price(fcf, cagr_ebitda, net_debt, shares_outstanding, discount_rate_pct) -> float | None` — was missing the `discount_rate_pct` parameter (read `COST_OF_CAPITAL_PROXY` directly instead). Used by Task 6.

This is a breaking signature change to an already-shipped, tested function. All 7 existing call sites in the test file must be updated in the same commit.

- [ ] **Step 1: Update the 7 existing tests**

In `tests/test_indices_score.py`, update each of these 7 calls to `estimate_dcf_price` to add `discount_rate_pct=8.0` (the value that was implicitly used before, via `COST_OF_CAPITAL_PROXY = 8.0` — passing it explicitly preserves the exact same expected values, no arithmetic changes):

```python
def test_estimate_dcf_price_nominal_case():
    result = estimate_dcf_price(
        fcf=100.0, cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=8.0,
    )
    assert result == pytest.approx(43.8363904302955)


def test_estimate_dcf_price_clamps_growth_at_the_cap():
    over_cap = estimate_dcf_price(
        fcf=100.0, cagr_ebitda=50.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=8.0,
    )
    at_cap = estimate_dcf_price(
        fcf=100.0, cagr_ebitda=15.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=8.0,
    )
    assert over_cap == at_cap


def test_estimate_dcf_price_clamps_growth_at_the_floor():
    under_floor = estimate_dcf_price(
        fcf=100.0, cagr_ebitda=-50.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=8.0,
    )
    at_floor = estimate_dcf_price(
        fcf=100.0, cagr_ebitda=-5.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=8.0,
    )
    assert under_floor == at_floor


def test_estimate_dcf_price_returns_none_when_fcf_not_positive():
    assert estimate_dcf_price(
        fcf=0.0, cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=8.0,
    ) is None
    assert estimate_dcf_price(
        fcf=-10.0, cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=8.0,
    ) is None


def test_estimate_dcf_price_returns_none_when_shares_outstanding_is_zero():
    assert estimate_dcf_price(
        fcf=100.0, cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=0.0,
        discount_rate_pct=8.0,
    ) is None


def test_estimate_dcf_price_returns_none_when_fcf_is_nan():
    """Un FCF NaN (yfinance en produit parfois) ne doit pas passer le garde-fou
    `fcf <= 0` (NaN <= 0 vaut False) et doit dégrader vers None, pas NaN."""
    result = estimate_dcf_price(
        fcf=float("nan"), cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=50.0,
        discount_rate_pct=8.0,
    )
    assert result is None


def test_estimate_dcf_price_returns_none_when_net_debt_is_nan():
    result = estimate_dcf_price(
        fcf=100.0, cagr_ebitda=10.0, net_debt=float("nan"), shares_outstanding=50.0,
        discount_rate_pct=8.0,
    )
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k "estimate_dcf_price" -v`
Expected: FAIL — `TypeError: estimate_dcf_price() got an unexpected keyword argument 'discount_rate_pct'`

- [ ] **Step 3: Implement**

In `indices_score.py`, replace `estimate_dcf_price`'s signature and its `discount_rate` line:

```python
def estimate_dcf_price(
    fcf: float, cagr_ebitda: float, net_debt: float, shares_outstanding: float,
    discount_rate_pct: float,
) -> float | None:
    """Prix par action implicite d'un DCF simplifié : projette le FCF actuel
    sur 5 ans au taux de croissance historique de l'EBITDA (plafonné entre
    -5% et +15%/an pour éviter d'extrapoler un chiffre bruité de façon
    absurde), actualise au coût du capital fourni par l'appelant (WACC de
    l'entreprise, ou COST_OF_CAPITAL_PROXY en repli), ajoute une valeur
    terminale à croissance perpétuelle de 2%. None si le FCF de départ
    n'est pas positif (DCF non pertinent) ou si le nombre d'actions est
    nul/inconnu."""
    if _is_missing(fcf) or fcf <= 0 or not shares_outstanding or _is_missing(net_debt):
        return None
    growth = _clamp(cagr_ebitda, DCF_GROWTH_FLOOR, DCF_GROWTH_CAP) / 100
    discount_rate = discount_rate_pct / 100
    terminal_growth = DCF_TERMINAL_GROWTH / 100

    pv_fcf = 0.0
    fcf_t = fcf
    for year in range(1, DCF_PROJECTION_YEARS + 1):
        fcf_t = fcf_t * (1 + growth)
        pv_fcf += fcf_t / (1 + discount_rate) ** year

    terminal_value = fcf_t * (1 + terminal_growth) / (discount_rate - terminal_growth)
    pv_terminal = terminal_value / (1 + discount_rate) ** DCF_PROJECTION_YEARS

    enterprise_value = pv_fcf + pv_terminal
    equity_value = enterprise_value - net_debt
    return equity_value / shares_outstanding
```

(Only the signature, docstring, and the `discount_rate = ...` line change — the rest of the function body is identical to before.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "estimate_dcf_price" -v`
Expected: PASS (7/7)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (110/110 — this task updates existing tests in place, adds none)

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): estimate_dcf_price reçoit le taux d'actualisation en paramètre"
```

---

## Task 6: `estimate_valuation_targets` reçoit le coût du capital en paramètre

**Files:**
- Modify: `indices_score.py:754-774` (`estimate_valuation_targets`)
- Modify: `tests/test_indices_score.py` (2 existing tests updated)

**Interfaces:**
- Consumes: `estimate_dcf_price(..., discount_rate_pct)` (Task 5).
- Produces: `estimate_valuation_targets(data: dict, cost_of_capital: float) -> dict` — was `estimate_valuation_targets(data: dict) -> dict`. Used by Task 7.

- [ ] **Step 1: Update the 2 existing tests**

In `tests/test_indices_score.py`, update both calls to `estimate_valuation_targets`:

```python
def test_estimate_valuation_targets_computes_all_three_output_keys():
    data = {
        "fcf": 50.0,
        "cagr_ebitda": 6.5,
        "net_debt": 100.0,
        "shares_outstanding": 10.0,
        "equity": 200.0,
        "current_price": 120.0,
        "current_ev_ebitda": 10.0,
        "avg_ev_ebitda_5y": 10.0,
        "ma200": 110.0,
    }
    result = estimate_valuation_targets(data, cost_of_capital=8.0)
    assert set(result.keys()) == {"fair_value", "entry_price", "exit_price"}
    assert result["fair_value"] is not None
    assert result["entry_price"] is not None
    assert result["exit_price"] is not None
    assert result["entry_price"] < result["exit_price"]


def test_estimate_valuation_targets_degrades_to_none_with_nan_inputs():
    """Une valeur manquante (NaN, comme yfinance en produit parfois) ne doit
    jamais se propager jusqu'en sortie — toujours None, jamais NaN, pour
    rester sérialisable en JSON valide."""
    import math
    data = {
        "fcf": float("nan"),
        "cagr_ebitda": 6.5,
        "net_debt": 100.0,
        "shares_outstanding": 10.0,
        "equity": float("nan"),
        "current_price": 120.0,
        "current_ev_ebitda": float("nan"),
        "avg_ev_ebitda_5y": 10.0,
        "ma200": float("nan"),
    }
    result = estimate_valuation_targets(data, cost_of_capital=8.0)
    assert result["fair_value"] is None
    assert result["entry_price"] is None
    assert result["exit_price"] is None
    for value in result.values():
        assert value is None or not (isinstance(value, float) and math.isnan(value))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k "estimate_valuation_targets" -v`
Expected: FAIL — `TypeError: estimate_valuation_targets() missing 1 required positional argument: 'cost_of_capital'`

- [ ] **Step 3: Implement**

In `indices_score.py`, replace `estimate_valuation_targets`:

```python
def estimate_valuation_targets(data: dict, cost_of_capital: float) -> dict:
    """Combine DCF, actif net et multiples en une juste valeur, puis en
    repères d'entrée/sortie. Toujours ces 3 clés en sortie, valeurs à None
    si non calculables (jamais d'exception). `cost_of_capital` est le taux
    d'actualisation du DCF (WACC de l'entreprise, ou COST_OF_CAPITAL_PROXY
    en repli — résolu par l'appelant)."""
    dcf_price = estimate_dcf_price(
        data["fcf"], data["cagr_ebitda"], data["net_debt"], data["shares_outstanding"],
        cost_of_capital,
    )
    asset_price = estimate_asset_based_price(data["equity"], data["shares_outstanding"])
    multiple_price = (
        estimate_multiple_based_price(
            data["current_price"], data["current_ev_ebitda"], data["avg_ev_ebitda_5y"]
        )
        if data["current_price"] is not None else None
    )
    fair_value = estimate_fair_value(dcf_price, asset_price, multiple_price)
    entry_exit = estimate_entry_exit_prices(fair_value, data["ma200"])
    return {
        "fair_value": fair_value,
        "entry_price": entry_exit["entry"],
        "exit_price": entry_exit["exit"],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "estimate_valuation_targets" -v`
Expected: PASS (2/2)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (110/110 — this task updates existing tests in place, adds none)

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): estimate_valuation_targets reçoit le coût du capital en paramètre"
```

---

## Task 7: Brancher le WACC dans `build_company_entry` et `main`

**Files:**
- Modify: `indices_score.py:777-828` (`build_company_entry`)
- Modify: `indices_score.py:831-` (`main`)
- Modify: `tests/test_indices_score.py` (`_fake_ratios`, 2 `test_build_company_entry_*` tests)

**Interfaces:**
- Consumes: `estimate_wacc` (Task 4), `estimate_valuation_targets(data, cost_of_capital)` (Task 6), `fetch_risk_free_rate` (Task 3).
- Produces: `build_company_entry(ticker: str, name: str, risk_free_rate: float | None) -> dict` — was `build_company_entry(ticker: str, name: str) -> dict`, gains a required 3rd parameter. Returned dict gains a top-level `"wacc": float` key.

- [ ] **Step 1: Update `_fake_ratios()`**

In `tests/test_indices_score.py`, replace `_fake_ratios()`:

```python
def _fake_ratios():
    return {
        "roce": 15.0,
        "roe": 18.0,
        "net_debt_ebitda": 1.5,
        "icr": 8.0,
        "cagr_ca": 6.0,
        "cagr_ebitda": 6.5,
        "fcf_conversion": 70.0,
        "current_ev_ebitda": 10.0,
        "avg_ev_ebitda_5y": 10.0,
        "current_pe": 20.0,
        "avg_pe_5y": 20.0,
        "ecart_pct_ma200": 5.0,
        "quarterly_yoy_growth_ca": 7.0,
        "fcf": 50.0,
        "net_debt": 100.0,
        "equity": 200.0,
        "current_price": 120.0,
        "ma200": 110.0,
        "shares_outstanding": 10.0,
        "tax_rate": 0.25,
        "total_debt": 150.0,
        "beta": 1.1,
        "sector": "Consumer Defensive",
    }
```

- [ ] **Step 2: Update the 2 `test_build_company_entry_*` tests**

In `tests/test_indices_score.py`, replace both tests' bodies:

```python
def test_build_company_entry_degrades_gracefully_when_news_fetch_fails(monkeypatch):
    """Une panne du flux RSS (fetch_news) ne doit pas faire perdre le score
    déjà calculé pour l'entreprise — seule la liste de news doit être vide,
    et le facteur Actualité récente doit rester neutre plutôt que planter."""
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: _fake_ratios())

    def _raise_news(name):
        raise RuntimeError("flux RSS indisponible")

    monkeypatch.setattr(indices_score, "fetch_news", _raise_news)

    entry = indices_score.build_company_entry("BN.PA", "Danone", risk_free_rate=3.68)

    assert entry["news"] == []
    assert entry["ticker"] == "BN.PA"
    assert entry["name"] == "Danone"
    assert isinstance(entry["score"], float)
    assert len(entry["factors"]) == 7
    assert entry["factors"][6]["name"] == "Actualité récente"
    assert entry["factors"][6]["score"] == 0.0
    assert entry["current_price"] == 120.0
    assert entry["fair_value"] is not None
    assert entry["entry_price"] is not None
    assert entry["exit_price"] is not None
    assert entry["entry_price"] < entry["exit_price"]
    assert entry["wacc"] is not None
    assert entry["wacc"] != COST_OF_CAPITAL_PROXY  # WACC réel calculable avec _fake_ratios()


def test_build_company_entry_includes_news_when_fetch_succeeds(monkeypatch):
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: _fake_ratios())
    monkeypatch.setattr(
        indices_score, "fetch_news",
        lambda name: [
            {"title": "Titre", "date": "2026-09-04", "link": "https://example.com", "sentiment": 1}
        ],
    )

    entry = indices_score.build_company_entry("BN.PA", "Danone", risk_free_rate=3.68)

    assert entry["news"] == [
        {"title": "Titre", "date": "2026-09-04", "link": "https://example.com", "sentiment": 1}
    ]
    assert entry["factors"][5]["name"] == "Dynamique récente"


def test_build_company_entry_falls_back_to_proxy_wacc_when_beta_missing(monkeypatch):
    """Si le bêta manque (yfinance ne le fournit pas toujours), le WACC ne
    doit pas être calculé partiellement — repli sur COST_OF_CAPITAL_PROXY
    pour cette entreprise, jamais d'exception."""
    ratios = _fake_ratios()
    ratios["beta"] = None
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: ratios)
    monkeypatch.setattr(indices_score, "fetch_news", lambda name: [])

    entry = indices_score.build_company_entry("BN.PA", "Danone", risk_free_rate=3.68)

    assert entry["wacc"] == COST_OF_CAPITAL_PROXY


def test_build_company_entry_falls_back_to_proxy_wacc_when_risk_free_rate_missing(monkeypatch):
    """Si le taux sans risque n'a pas pu être récupéré pour tout le run
    (ex : FRED_API_KEY absente, panne réseau), repli sur
    COST_OF_CAPITAL_PROXY pour chaque entreprise."""
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: _fake_ratios())
    monkeypatch.setattr(indices_score, "fetch_news", lambda name: [])

    entry = indices_score.build_company_entry("BN.PA", "Danone", risk_free_rate=None)

    assert entry["wacc"] == COST_OF_CAPITAL_PROXY
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k "test_build_company_entry" -v`
Expected: FAIL — `TypeError: build_company_entry() missing 1 required positional argument: 'risk_free_rate'`

- [ ] **Step 4: Implement — `build_company_entry`**

In `indices_score.py`, replace `build_company_entry`'s signature and body:

```python
def build_company_entry(ticker: str, name: str, risk_free_rate: float | None) -> dict:
    data = fetch_company_financials(ticker)
    sector = data["sector"]

    market_cap = (
        data["current_price"] * data["shares_outstanding"]
        if data["current_price"] is not None else None
    )
    wacc = estimate_wacc(
        risk_free_rate, data["beta"], market_cap, data["total_debt"], data["tax_rate"]
    )
    cost_of_capital = wacc if wacc is not None else COST_OF_CAPITAL_PROXY

    # Les news sont récupérées avant la construction des facteurs : le
    # facteur "Actualité récente" dépend du sentiment attaché à chaque
    # actu par fetch_news. Un échec total du flux RSS dégrade vers
    # news = [] (voir fetch_news / summarize_news_item, qui ne lèvent
    # jamais), ce qui fait à son tour retomber score_actualite_recente([])
    # sur son cas neutre — la dépendance se dégrade proprement de bout
    # en bout, sans faire perdre le score fondamental déjà calculable.
    try:
        news = fetch_news(name)
    except Exception as e:
        print(f"Erreur récupération news pour {name} : {e}")
        news = []

    factors = [
        score_rentabilite(data["roce"], data["roe"], cost_of_capital),
        score_structure_financiere(data["net_debt_ebitda"], data["icr"], sector),
        score_croissance(data["cagr_ca"], data["cagr_ebitda"]),
        score_generation_cash(data["fcf_conversion"]),
        score_valorisation(
            data["current_ev_ebitda"], data["avg_ev_ebitda_5y"],
            data["current_pe"], data["avg_pe_5y"], data["cagr_ebitda"],
        ),
        score_dynamique_recente(
            data["ecart_pct_ma200"], data["quarterly_yoy_growth_ca"], data["cagr_ca"],
        ),
        score_actualite_recente(news),
    ]
    composite = compute_composite(factors)

    valuation_targets = estimate_valuation_targets(data, cost_of_capital)

    return {
        "ticker": ticker,
        "name": name,
        "sector": sector,
        "sector_profile": sector_risk_profile(sector),
        "score": composite,
        "interpretation": interpret(composite),
        "factors": [
            {"name": f.name, "score": f.score, "weight": f.weight, "raw_value": f.raw_value}
            for f in factors
        ],
        "news": news,
        "current_price": data["current_price"],
        "fair_value": valuation_targets["fair_value"],
        "entry_price": valuation_targets["entry_price"],
        "exit_price": valuation_targets["exit_price"],
        "wacc": cost_of_capital,
    }
```

- [ ] **Step 5: Implement — `main`**

In `indices_score.py`, `main` currently starts with:

```python
def main():
    companies = []
    for company in COMPANIES:
        try:
            companies.append(build_company_entry(company["ticker"], company["name"]))
        except Exception as e:
            print(f"Erreur pour {company['ticker']} ({company['name']}) : {e}")
```

Replace with:

```python
def main():
    risk_free_rate = fetch_risk_free_rate()
    companies = []
    for company in COMPANIES:
        try:
            companies.append(
                build_company_entry(company["ticker"], company["name"], risk_free_rate)
            )
        except Exception as e:
            print(f"Erreur pour {company['ticker']} ({company['name']}) : {e}")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "test_build_company_entry" -v`
Expected: PASS (4/4 — the 2 updated tests plus the 2 new fallback tests)

- [ ] **Step 7: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (112/112 — 110 from Task 6 + 2 new fallback tests in this task)

- [ ] **Step 8: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): brancher le WACC dans build_company_entry et main"
```

---

## Task 8: Secret CI, documentation méthodologique

**Files:**
- Modify: `.github/workflows/indices.yml`
- Modify: `Methodologie_Analyse_Indices.md`

**Interfaces:** None — this task only wires existing infrastructure and updates prose documentation to match the shipped behavior. No code changes.

- [ ] **Step 1: Add the `FRED_API_KEY` secret to the workflow**

In `.github/workflows/indices.yml`, the "Calculer le score" step currently reads:

```yaml
      - name: Calculer le score des entreprises pilotes
        run: python3 indices_score.py
        env:
          ANTHROPIC_API_KEY: ${{ secrets.CLAUDE_API_KEY }}
```

Replace with:

```yaml
      - name: Calculer le score des entreprises pilotes
        run: python3 indices_score.py
        env:
          ANTHROPIC_API_KEY: ${{ secrets.CLAUDE_API_KEY }}
          FRED_API_KEY: ${{ secrets.FRED_API_KEY }}
```

(`FRED_API_KEY` is an existing secret, already used by `daily.yml` for the gold pipeline — no new secret to create in the repo's GitHub settings.)

- [ ] **Step 2: Validate YAML indentation**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/indices.yml'))"`
Expected: no output, exit code 0 (if `pyyaml` isn't installed, manually confirm `FRED_API_KEY:` is indented at the same level as `ANTHROPIC_API_KEY:`, two spaces further in than `env:`)

- [ ] **Step 3: Update the methodology doc — §1 Rentabilité**

In `Methodologie_Analyse_Indices.md`, the §1 paragraph currently reads:

```markdown
- Score favorable si le ROCE dépasse durablement une estimation
  simplifiée du coût du capital (proxy : taux sans risque + prime de
  risque actions, sans calcul de bêta complet au v1), et si la
  tendance sur 5 ans est stable ou croissante.
```

Replace with:

```markdown
- Score favorable si le ROCE dépasse durablement le coût du capital de
  l'entreprise (WACC calculé — voir ci-dessous), et si la tendance sur 5
  ans est stable ou croissante.

> **Coût du capital réel par entreprise (WACC).** `Re = Rf_France + β ×
> prime_marché + prime_taille(capitalisation)` (CAPM), `WACC =
> capitalisation/(capitalisation+dette) × Re + dette/(capitalisation+dette)
> × Rd_après_IS`. `Rf_France` = dernier taux OAT 10 ans publié (FRED,
> série `IRLTLT01FRM156N`, mensuelle) — pas le taux américain, une
> entreprise du CAC 40 valorisée en euros s'actualise avec un taux sans
> risque en euros. `β` = bêta yfinance brut (endetté) — pas un bêta
> désendetté puis réendetté à la structure financière de l'entreprise
> comme le recommanderait une analyse plus poussée, faute d'échantillon
> de comparables disponible ici. Prime de risque marché fixe à 5,0%.
> Prime de taille par bandes de capitalisation (de +0% au-delà de 50 Md€
> à +3,0 pts en dessous de 2 Md€). `Rd` reprend le même taux proxy que
> l'ICR (3,0%, charges financières non fiablement isolées chez ces
> entreprises). Si une donnée manque pour une entreprise (bêta absent,
> taux sans risque non récupéré...), repli sur un coût du capital fixe de
> 8% pour cette entreprise seulement.
```

- [ ] **Step 4: Update "Hors périmètre (v1)"**

In `Methodologie_Analyse_Indices.md`, the "Hors périmètre (v1)" section currently reads:

```markdown
## Hors périmètre (v1)

- Extension aux 40 valeurs du CAC 40 et aux valeurs financières
  (grille dédiée à construire séparément)
- Calcul complet du coût du capital (bêta désendetté, prime de risque
  de marché) — proxy simplifié au v1
- Comparaison à un échantillon de pairs sectoriels pour la valorisation
- Historique de score / alertes de franchissement de seuil par
  entreprise
```

Replace with:

```markdown
## Hors périmètre (v1)

- Extension aux 40 valeurs du CAC 40 et aux valeurs financières
  (grille dédiée à construire séparément)
- Bêta désendetté puis réendetté à la structure financière de chaque
  entreprise (nécessiterait un échantillon de comparables) — le WACC
  utilise le bêta yfinance brut
- Comparaison à un échantillon de pairs sectoriels pour la valorisation
- Historique de score / alertes de franchissement de seuil par
  entreprise
```

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/indices.yml Methodologie_Analyse_Indices.md
git commit -m "docs(indices): documenter le WACC par entreprise, brancher FRED_API_KEY sur indices.yml"
```

---

## Post-merge verification (not a task — informational)

Once this branch is merged and `indices.yml` runs for real: confirm in `docs/indices.json` that `wacc` is present and non-null for all 5 companies, and that its value is plausible (roughly 6-10% for these large-cap profiles, not wildly outside that range) — a `wacc` stuck at exactly `8.0` for every company would indicate `fetch_risk_free_rate()` or `beta` extraction silently failing across the board (check the workflow logs for `FRED_API_KEY` being correctly passed, and spot-check that yfinance's `.info` actually returns a `beta` value for these 5 tickers). Also confirm the "Rentabilité / création de valeur" factor's `raw_value` string now shows a per-company coût du capital that varies between companies rather than a flat `8.0%` for all 5.
