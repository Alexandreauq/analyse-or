# Cours + repères d'entrée/sortie Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Display, for each pilot company, the current stock price and two price reference points — a favorable "entry" and "exit" level — combining a valuation-based fair value (average of a simplified DCF, book-value/asset approach, and historical-multiple approach) with a technical level (200-day moving average). Purely informational: no new scoring factor, no change to the composite score.

**Architecture:** Three small pure valuation functions (`estimate_dcf_price`, `estimate_asset_based_price`, `estimate_multiple_based_price`) each return a price-per-share estimate or `None` if their inputs are insufficient. `estimate_fair_value` averages whichever are available. `estimate_entry_exit_prices` combines that fair value (±30%) with the 200-day moving average (as-is / ×1.20) into final entry/exit levels, again averaging whichever side is available. `estimate_valuation_targets` orchestrates all of the above from the existing `fetch_company_financials` data dict. `build_company_entry` calls it once and adds 4 new top-level fields to its output. The front end gets one new always-visible panel (not behind a toggle) in `renderCompanyDetail`.

**Tech Stack:** Same as the existing pipeline — pure Python, no new dependencies. Reuses `_clamp`, `COST_OF_CAPITAL_PROXY`, `VALUATION_PREMIUM_SCALE`-equivalent margin, `PRICE_MOMENTUM_SCALE`-equivalent margin — all already-established constants/conventions from prior sub-projects.

**Spec:** `specs/2026-09-05-cours-entree-sortie-design.md`

## Global Constraints

- None of the new `estimate_*` functions may ever raise — insufficient/invalid input returns `None` (or, for the two combiners, a dict with `None` values), matching every other pure function in this file.
- Exact constant names/values: `DCF_PROJECTION_YEARS = 5`, `DCF_GROWTH_FLOOR = -5.0`, `DCF_GROWTH_CAP = 15.0`, `DCF_TERMINAL_GROWTH = 2.0`, `VALUATION_MARGIN_OF_SAFETY = 0.30`, `TECHNICAL_EXIT_MARGIN = 0.20`.
- This is a display-only feature: `WEIGHTS`, `compute_composite`, and `interpret` are NOT touched anywhere in this plan.
- New top-level company entry fields: `current_price`, `fair_value`, `entry_price`, `exit_price` — all `float | None`, always present (never absent/omitted).
- No new pip dependency.

---

## Task 1: Expose `fcf`, `net_debt`, `equity` from `extract_ratios`

**Files:**
- Modify: `indices_score.py:437-449` (`extract_ratios`'s return block)
- Modify: `tests/test_indices_score.py:365-379` (`test_extract_ratios_computes_expected_keys`)

**Interfaces:**
- Produces: `extract_ratios(...)`'s returned dict gains 3 keys: `"fcf": float`, `"net_debt": float`, `"equity": float` (all already computed as local variables `fcf`, `net_debt_latest`, `equity[latest]` inside the function — this task only exposes them). Used by Task 7.

- [ ] **Step 1: Write the failing test change**

In `tests/test_indices_score.py`, update `test_extract_ratios_computes_expected_keys`'s key list:

```python
def test_extract_ratios_computes_expected_keys():
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )
    for key in [
        "roce", "roe", "net_debt_ebitda", "icr", "cagr_ca", "cagr_ebitda",
        "fcf_conversion", "current_ev_ebitda", "avg_ev_ebitda_5y",
        "current_pe", "avg_pe_5y", "fcf", "net_debt", "equity",
    ]:
        assert key in ratios, f"clé manquante : {key}"
    # Revenu croît régulièrement de 800 à 1000 sur 5 ans ; CAGR lissé
    # (moyenne des 2 exercices récents vs moyenne des 2 plus anciens,
    # cf. test dédié ci-dessous) ~ 5.7%/an sur cette série linéaire.
    assert 5.0 < ratios["cagr_ca"] < 6.5
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_indices_score.py -k "test_extract_ratios_computes_expected_keys" -v`
Expected: FAIL — `clé manquante : fcf` (or `net_debt`/`equity`)

- [ ] **Step 3: Implement**

In `indices_score.py`, `extract_ratios` currently ends with:

```python
    return {
        "roce": roce,
        "roe": roe,
        "net_debt_ebitda": net_debt_ebitda,
        "icr": icr,
        "cagr_ca": cagr_ca,
        "cagr_ebitda": cagr_ebitda,
        "fcf_conversion": fcf_conversion,
        "current_ev_ebitda": current_ev_ebitda,
        "avg_ev_ebitda_5y": avg_ev_ebitda_5y,
        "current_pe": current_pe,
        "avg_pe_5y": avg_pe_5y,
    }
```

Replace with:

```python
    return {
        "roce": roce,
        "roe": roe,
        "net_debt_ebitda": net_debt_ebitda,
        "icr": icr,
        "cagr_ca": cagr_ca,
        "cagr_ebitda": cagr_ebitda,
        "fcf_conversion": fcf_conversion,
        "current_ev_ebitda": current_ev_ebitda,
        "avg_ev_ebitda_5y": avg_ev_ebitda_5y,
        "current_pe": current_pe,
        "avg_pe_5y": avg_pe_5y,
        "fcf": fcf,
        "net_debt": net_debt_latest,
        "equity": equity[latest],
    }
```

(`fcf`, `net_debt_latest`, and `equity` are already local variables computed earlier in this same function — this change only adds them to the returned dict, no new computation.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_indices_score.py -k "test_extract_ratios_computes_expected_keys" -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (69/69 — no new tests, one existing test extended)

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): exposer fcf/net_debt/equity depuis extract_ratios"
```

---

## Task 2: Expose `current_price`, `ma200`, `shares_outstanding` from `fetch_company_financials`

**Files:**
- Modify: `indices_score.py:501-505` (`fetch_company_financials`'s final lines)

**Interfaces:**
- Produces: `fetch_company_financials(...)`'s returned dict gains `"current_price": float | None`, `"ma200": float | None` (already computed locally, currently only used to derive `ecart_pct_ma200`), and `"shares_outstanding": float` (already a local variable). Used by Task 7.

No new test for this task, consistent with `fetch_company_financials` having no direct unit test anywhere in this codebase (live yfinance I/O; every test monkeypatches the whole function) — same precedent followed in the prior sub-project's equivalent task.

- [ ] **Step 1: Implement**

In `indices_score.py`, `fetch_company_financials` currently ends with:

```python
    ratios = extract_ratios(financials, balance_sheet, cashflow, closes_by_year, shares_outstanding)
    ratios["sector"] = info.get("sector")
    ratios["ecart_pct_ma200"] = ecart_pct_ma200
    ratios["quarterly_yoy_growth_ca"] = extract_quarterly_growth(quarterly_financials)
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
    return ratios
```

- [ ] **Step 2: Run the full suite to confirm nothing else broke**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (69/69 — same count, this task adds no tests)

- [ ] **Step 3: Commit**

```bash
git add indices_score.py
git commit -m "feat(indices): exposer cours actuel/MM200/actions en circulation depuis fetch_company_financials"
```

---

## Task 3: `estimate_dcf_price` — DCF simplifié

**Files:**
- Modify: `indices_score.py` (add function; exact placement in Step 3)
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes: `_clamp` (existing), `COST_OF_CAPITAL_PROXY` (existing constant, `indices_score.py:651`).
- Produces: `estimate_dcf_price(fcf, cagr_ebitda, net_debt, shares_outstanding) -> float | None`, used by Task 7.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_indices_score.py`, in a new section after the `summarize_news_item`/`fetch_news` test block and before the `_fake_ratios`/`test_build_company_entry_*` block (i.e., right before `def _fake_ratios():`):

```python
from indices_score import estimate_dcf_price


def test_estimate_dcf_price_nominal_case():
    result = estimate_dcf_price(fcf=100.0, cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=50.0)
    assert result == pytest.approx(43.8363904302955)


def test_estimate_dcf_price_clamps_growth_at_the_cap():
    over_cap = estimate_dcf_price(fcf=100.0, cagr_ebitda=50.0, net_debt=200.0, shares_outstanding=50.0)
    at_cap = estimate_dcf_price(fcf=100.0, cagr_ebitda=15.0, net_debt=200.0, shares_outstanding=50.0)
    assert over_cap == at_cap


def test_estimate_dcf_price_clamps_growth_at_the_floor():
    under_floor = estimate_dcf_price(fcf=100.0, cagr_ebitda=-50.0, net_debt=200.0, shares_outstanding=50.0)
    at_floor = estimate_dcf_price(fcf=100.0, cagr_ebitda=-5.0, net_debt=200.0, shares_outstanding=50.0)
    assert under_floor == at_floor


def test_estimate_dcf_price_returns_none_when_fcf_not_positive():
    assert estimate_dcf_price(fcf=0.0, cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=50.0) is None
    assert estimate_dcf_price(fcf=-10.0, cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=50.0) is None


def test_estimate_dcf_price_returns_none_when_shares_outstanding_is_zero():
    assert estimate_dcf_price(fcf=100.0, cagr_ebitda=10.0, net_debt=200.0, shares_outstanding=0.0) is None
```

Note: `import pytest` already exists at the top of `tests/test_indices_score.py` (line 1, added during an earlier sub-project's floating-point fix) — do NOT add another `import pytest` here, the module-level one already covers `pytest.approx(...)` used above.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k "estimate_dcf_price" -v`
Expected: FAIL — `ImportError: cannot import name 'estimate_dcf_price'`

- [ ] **Step 3: Implement**

In `indices_score.py`, `COST_OF_CAPITAL_PROXY = 8.0  # %` is defined at line 651, immediately followed by two blank lines and `def build_company_entry`. Insert the new function between them:

```python
DCF_PROJECTION_YEARS = 5
DCF_GROWTH_FLOOR = -5.0      # % croissance FCF minimum projetée
DCF_GROWTH_CAP = 15.0        # % croissance FCF maximum projetée
DCF_TERMINAL_GROWTH = 2.0    # % croissance perpétuelle (valeur terminale)


def estimate_dcf_price(
    fcf: float, cagr_ebitda: float, net_debt: float, shares_outstanding: float
) -> float | None:
    """Prix par action implicite d'un DCF simplifié : projette le FCF actuel
    sur 5 ans au taux de croissance historique de l'EBITDA (plafonné entre
    -5% et +15%/an pour éviter d'extrapoler un chiffre bruité de façon
    absurde), actualise au coût du capital (COST_OF_CAPITAL_PROXY), ajoute
    une valeur terminale à croissance perpétuelle de 2%. None si le FCF de
    départ n'est pas positif (DCF non pertinent) ou si le nombre d'actions
    est nul/inconnu."""
    if fcf <= 0 or not shares_outstanding:
        return None
    growth = _clamp(cagr_ebitda, DCF_GROWTH_FLOOR, DCF_GROWTH_CAP) / 100
    discount_rate = COST_OF_CAPITAL_PROXY / 100
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "estimate_dcf_price" -v`
Expected: PASS (5/5)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (74/74)

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): DCF simplifié (estimate_dcf_price)"
```

---

## Task 4: `estimate_asset_based_price` et `estimate_multiple_based_price`

**Files:**
- Modify: `indices_score.py` (add both functions after `estimate_dcf_price`)
- Test: `tests/test_indices_score.py`

Both functions are small, independent, one-guard-clause pure functions — implemented and tested together in one task per the plan's batching guidance for same-shape work.

**Interfaces:**
- Produces: `estimate_asset_based_price(equity, shares_outstanding) -> float | None`, `estimate_multiple_based_price(current_price, current_ev_ebitda, avg_ev_ebitda_5y) -> float | None`. Both used by Task 7.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_indices_score.py`, right after the Task 3 tests:

```python
from indices_score import estimate_asset_based_price, estimate_multiple_based_price


def test_estimate_asset_based_price_nominal_case():
    assert estimate_asset_based_price(equity=200.0, shares_outstanding=50.0) == 4.0


def test_estimate_asset_based_price_returns_none_when_equity_not_positive():
    assert estimate_asset_based_price(equity=0.0, shares_outstanding=50.0) is None
    assert estimate_asset_based_price(equity=-10.0, shares_outstanding=50.0) is None


def test_estimate_asset_based_price_returns_none_when_shares_outstanding_is_zero():
    assert estimate_asset_based_price(equity=200.0, shares_outstanding=0.0) is None


def test_estimate_multiple_based_price_nominal_case():
    result = estimate_multiple_based_price(
        current_price=100.0, current_ev_ebitda=10.0, avg_ev_ebitda_5y=8.0
    )
    assert result == 80.0


def test_estimate_multiple_based_price_returns_none_when_current_multiple_is_zero():
    result = estimate_multiple_based_price(
        current_price=100.0, current_ev_ebitda=0.0, avg_ev_ebitda_5y=8.0
    )
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k "estimate_asset_based_price or estimate_multiple_based_price" -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement**

In `indices_score.py`, add both functions immediately after `estimate_dcf_price`:

```python
def estimate_asset_based_price(equity: float, shares_outstanding: float) -> float | None:
    """Valeur comptable par action (capitaux propres / actions en
    circulation) — approche patrimoniale simplifiée, sans réévaluation des
    actifs à la valeur de marché (hors périmètre v1). None si les capitaux
    propres sont négatifs ou nuls (base non significative comme plancher
    de valorisation) ou si le nombre d'actions est nul/inconnu."""
    if not shares_outstanding or equity <= 0:
        return None
    return equity / shares_outstanding


def estimate_multiple_based_price(
    current_price: float, current_ev_ebitda: float, avg_ev_ebitda_5y: float
) -> float | None:
    """Prix impliqué par un retour du multiple EV/EBITDA actuel à sa
    moyenne 5 ans, en supposant que le prix varie proportionnellement au
    multiple — approximation qui ignore l'effet de la dette nette fixe,
    documentée comme telle (cf. Methodologie_Analyse_Indices.md), plutôt
    que de reconstruire précisément EV et capitalisation. None si le
    multiple actuel est nul/absent."""
    if not current_ev_ebitda:
        return None
    return current_price * (avg_ev_ebitda_5y / current_ev_ebitda)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "estimate_asset_based_price or estimate_multiple_based_price" -v`
Expected: PASS (5/5)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (79/79)

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): approches actif net et multiples (estimate_asset_based_price, estimate_multiple_based_price)"
```

---

## Task 5: `estimate_fair_value` — moyenne des 3 méthodes disponibles

**Files:**
- Modify: `indices_score.py` (add function after `estimate_multiple_based_price`)
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Produces: `estimate_fair_value(dcf_price, asset_price, multiple_price) -> float | None`, used by Task 7.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_indices_score.py`, right after the Task 4 tests:

```python
from indices_score import estimate_fair_value


def test_estimate_fair_value_averages_all_three_methods():
    result = estimate_fair_value(dcf_price=40.0, asset_price=50.0, multiple_price=60.0)
    assert result == 50.0


def test_estimate_fair_value_averages_available_methods_when_one_is_missing():
    result = estimate_fair_value(dcf_price=40.0, asset_price=None, multiple_price=60.0)
    assert result == 50.0


def test_estimate_fair_value_returns_none_when_no_method_is_available():
    assert estimate_fair_value(dcf_price=None, asset_price=None, multiple_price=None) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k "estimate_fair_value" -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement**

In `indices_score.py`, add immediately after `estimate_multiple_based_price`:

```python
def estimate_fair_value(
    dcf_price: float | None, asset_price: float | None, multiple_price: float | None
) -> float | None:
    """Moyenne des méthodes de valorisation disponibles (DCF, actif net,
    multiples) — ignore celles indisponibles (None) ; None si aucune des
    3 n'est calculable."""
    prices = [p for p in (dcf_price, asset_price, multiple_price) if p is not None]
    return sum(prices) / len(prices) if prices else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "estimate_fair_value" -v`
Expected: PASS (3/3)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (82/82)

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): moyenne des méthodes de valorisation disponibles (estimate_fair_value)"
```

---

## Task 6: `estimate_entry_exit_prices` — combinaison valorisation + technique

**Files:**
- Modify: `indices_score.py` (add constants + function after `estimate_fair_value`)
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes: nothing new (pure arithmetic on its two float|None arguments).
- Produces: `estimate_entry_exit_prices(fair_value, ma200) -> dict` with keys `"entry"` and `"exit"` (each `float | None`), used by Task 7.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_indices_score.py`, right after the Task 5 tests:

```python
from indices_score import estimate_entry_exit_prices


def test_estimate_entry_exit_prices_combines_valuation_and_technical():
    result = estimate_entry_exit_prices(fair_value=100.0, ma200=90.0)
    assert result == {"entry": 80.0, "exit": 119.0}


def test_estimate_entry_exit_prices_uses_only_valuation_when_ma200_missing():
    result = estimate_entry_exit_prices(fair_value=100.0, ma200=None)
    assert result == {"entry": 70.0, "exit": 130.0}


def test_estimate_entry_exit_prices_uses_only_technical_when_fair_value_missing():
    result = estimate_entry_exit_prices(fair_value=None, ma200=90.0)
    assert result == {"entry": 90.0, "exit": 108.0}


def test_estimate_entry_exit_prices_returns_none_for_both_when_nothing_available():
    result = estimate_entry_exit_prices(fair_value=None, ma200=None)
    assert result == {"entry": None, "exit": None}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k "estimate_entry_exit_prices" -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement**

In `indices_score.py`, add immediately after `estimate_fair_value`:

```python
VALUATION_MARGIN_OF_SAFETY = 0.30   # ±30%, cohérent avec le seuil de prime/décote
                                     # significative déjà utilisé dans score_valorisation
TECHNICAL_EXIT_MARGIN = 0.20        # +20% au-dessus de la MM200, cohérent avec
                                     # PRICE_MOMENTUM_SCALE de score_dynamique_recente


def estimate_entry_exit_prices(fair_value: float | None, ma200: float | None) -> dict:
    """Combine repère de valorisation (juste valeur ± 30%) et repère
    technique (MM200 / MM200 × 1,20) en moyennant ceux disponibles.
    Renvoie {"entry": float | None, "exit": float | None} — None des deux
    côtés si ni la valorisation ni la MM200 ne sont disponibles."""
    entry_candidates = []
    exit_candidates = []
    if fair_value is not None:
        entry_candidates.append(fair_value * (1 - VALUATION_MARGIN_OF_SAFETY))
        exit_candidates.append(fair_value * (1 + VALUATION_MARGIN_OF_SAFETY))
    if ma200 is not None:
        entry_candidates.append(ma200)
        exit_candidates.append(ma200 * (1 + TECHNICAL_EXIT_MARGIN))
    entry = sum(entry_candidates) / len(entry_candidates) if entry_candidates else None
    exit_price = sum(exit_candidates) / len(exit_candidates) if exit_candidates else None
    return {"entry": entry, "exit": exit_price}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "estimate_entry_exit_prices" -v`
Expected: PASS (4/4)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (86/86)

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): combinaison valorisation + technique en repères d'entrée/sortie"
```

---

## Task 7: Orchestrer et brancher dans `build_company_entry`

**Files:**
- Modify: `indices_score.py` (add `estimate_valuation_targets`; modify `build_company_entry`)
- Modify: `tests/test_indices_score.py` (`_fake_ratios`, 2 `test_build_company_entry_*` tests)

**Interfaces:**
- Consumes: `estimate_dcf_price`, `estimate_asset_based_price`, `estimate_multiple_based_price` (Tasks 3-4), `estimate_fair_value` (Task 5), `estimate_entry_exit_prices` (Task 6).
- Produces: `estimate_valuation_targets(data: dict) -> dict` with keys `fair_value`/`entry_price`/`exit_price`. `build_company_entry`'s returned dict gains 4 new top-level keys: `current_price`, `fair_value`, `entry_price`, `exit_price`.

- [ ] **Step 1: Write the failing test changes**

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
        "sector": "Consumer Defensive",
    }
```

Add these assertions to `test_build_company_entry_degrades_gracefully_when_news_fetch_fails` (append at the end of the existing test body, after `assert entry["factors"][6]["score"] == 0.0`):

```python
    assert entry["current_price"] == 120.0
    assert entry["fair_value"] is not None
    assert entry["entry_price"] is not None
    assert entry["exit_price"] is not None
    assert entry["entry_price"] < entry["exit_price"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_indices_score.py -k "test_build_company_entry_degrades_gracefully_when_news_fetch_fails" -v`
Expected: FAIL — `KeyError: 'current_price'`

- [ ] **Step 3: Implement `estimate_valuation_targets`**

In `indices_score.py`, add this function immediately after `estimate_entry_exit_prices` (added in Task 6) and before `build_company_entry`:

```python
def estimate_valuation_targets(data: dict) -> dict:
    """Combine DCF, actif net et multiples en une juste valeur, puis en
    repères d'entrée/sortie. Toujours ces 3 clés en sortie, valeurs à None
    si non calculables (jamais d'exception)."""
    dcf_price = estimate_dcf_price(
        data["fcf"], data["cagr_ebitda"], data["net_debt"], data["shares_outstanding"]
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

- [ ] **Step 4: Wire into `build_company_entry`**

In `indices_score.py`, `build_company_entry` currently ends with:

```python
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
    }
```

Replace with (adds the `estimate_valuation_targets(data)` call and 4 new keys):

```python
    valuation_targets = estimate_valuation_targets(data)

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
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "test_build_company_entry" -v`
Expected: PASS (2/2)

- [ ] **Step 6: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (86/86 — this task updates existing tests in place, adds none new)

- [ ] **Step 7: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): brancher cours et repères d'entrée/sortie dans build_company_entry"
```

---

## Task 8: Affichage front-end (cours, repères d'entrée/sortie)

**Files:**
- Modify: `docs/index.html` (new CSS rules, new `formatPrice` helper, new panel in `renderCompanyDetail`)

**Interfaces:**
- Consumes: `company.current_price`, `company.fair_value` (not directly displayed but available if ever needed), `company.entry_price`, `company.exit_price` — all `float | null` from `docs/indices.json`.

- [ ] **Step 1: Add the CSS rules**

In `docs/index.html`, after the existing `.tech-value { font-weight: 500; }` rule (around line 115), add:

```css
  .price-panel { margin: 16px 0; }
  .price-row { display:flex; justify-content:space-between; font-size:14px; padding: 4px 0; }
  .price-label { color: var(--muted); }
  .price-value { font-weight: 500; }
  .price-disclaimer { color: var(--muted); font-size: 11px; line-height: 1.5; margin: 8px 0 0; }
```

- [ ] **Step 2: Add the `formatPrice` helper**

In `docs/index.html`, between the end of `renderIndicesList` (the closing `}` right before `function renderCompanyDetail(company) {`) add:

```javascript
function formatPrice(v) {
  return v != null ? `${v.toLocaleString('fr-FR', {maximumFractionDigits:2})} €` : 'indisponible';
}

```

- [ ] **Step 3: Add the price panel markup**

In `docs/index.html`, inside `renderCompanyDetail`, find:

```javascript
    <section class="hero">
      <div class="hero-number">${company.score > 0 ? '+' : ''}${company.score}</div>
      <div class="hero-label">${company.name} — ${company.interpretation}</div>
    </section>
    <div class="divider"></div>
```

Replace with (inserts the new panel between the hero and the divider):

```javascript
    <section class="hero">
      <div class="hero-number">${company.score > 0 ? '+' : ''}${company.score}</div>
      <div class="hero-label">${company.name} — ${company.interpretation}</div>
    </section>

    <div class="price-panel">
      <div class="price-row">
        <span class="price-label">Cours actuel</span>
        <span class="price-value">${formatPrice(company.current_price)}</span>
      </div>
      <div class="price-row">
        <span class="price-label">Repère d'entrée</span>
        <span class="price-value" style="color:var(--gold)">${formatPrice(company.entry_price)}</span>
      </div>
      <div class="price-row">
        <span class="price-label">Repère de sortie</span>
        <span class="price-value" style="color:var(--rust)">${formatPrice(company.exit_price)}</span>
      </div>
      <p class="price-disclaimer">Repères indicatifs basés sur la valorisation
        historique (DCF, actif net, multiples) et la tendance technique —
        pas un conseil d'investissement.</p>
    </div>
    <div class="divider"></div>
```

- [ ] **Step 4: Manual verification with a local fixture**

Create a temporary local copy of `docs/indices.json` with one company entry that has `current_price`/`entry_price`/`exit_price` populated (do NOT commit this file — it's only for manual browser verification):

```bash
cp docs/indices.json docs/indices.json.bak
python3 -c "
import json
data = json.load(open('docs/indices.json', encoding='utf-8'))
if data['companies']:
    data['companies'][0]['current_price'] = 123.45
    data['companies'][0]['entry_price'] = 100.0
    data['companies'][0]['exit_price'] = 150.0
json.dump(data, open('docs/indices.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
"
```

Serve `docs/` locally and open the Indices tab, click into the first company, and confirm visually (use Playwright headless if available in this environment, or any other means to actually render the page — do not just eyeball the HTML/CSS and assume it renders correctly):

```bash
cd docs && python3 -m http.server 8000
```

Confirm: current price, entry price (gold), and exit price (rust/red) all display correctly formatted with "€", positioned between the hero score and the divider, above the Actu/Détail du calcul buttons; the disclaimer sentence is visible in muted small text below the three rows.

**Restore the real file before committing:**

```bash
mv docs/indices.json.bak docs/indices.json
```

- [ ] **Step 5: Commit**

```bash
git add docs/index.html
git commit -m "feat(indices): afficher cours actuel et repères d'entrée/sortie"
```

---

## Post-merge verification (not a task — informational)

Once this branch is merged and `indices.yml` runs for real: confirm in `docs/indices.json` that `current_price`/`fair_value`/`entry_price`/`exit_price` are non-null for at least most of the 5 pilot companies (a company with negative FCF and negative/zero equity could legitimately have `fair_value: null` if the multiple-based method also fails — but not all 5 landing on `null` across the board, which would indicate a systemic wiring issue). Also sanity-check that `entry_price < exit_price` holds and that the values are the right order of magnitude for each company's actual share price (not off by a factor of `shares_outstanding` or similar unit-mixing bug) — this is the one thing pure unit tests cannot catch, since they use synthetic numbers.
