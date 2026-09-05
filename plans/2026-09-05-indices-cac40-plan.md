# Scoring fondamental CAC 40 (onglet "Indices") — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Activate the "Indices" tab with a fundamental score (0 to ±100 scale) for 5 pilot CAC 40 companies, computed from 5 years of yfinance financial statements per the Vernimmen-derived methodology, refreshed daily alongside a per-company news feed.

**Architecture:** A new standalone `indices_score.py` module (pure scoring functions separated from yfinance/RSS I/O for testability) writes `docs/indices.json`, run by a new daily GitHub Actions workflow (`indices.yml`) independent from the existing gold pipeline. The front-end (`docs/index.html`) gains a third sliding pane (`#indices`) reusing the existing accordion (`toggle-btn`/`section-wrap`) and panel/alert-row CSS already built for `#or`, with hash sub-routing for the company list vs. a single company's detail.

**Tech Stack:** Python 3.12, yfinance (already a dependency), stdlib `xml.etree.ElementTree` for RSS (no new runtime dependency), pytest (new, dev-only) for the pure scoring functions, vanilla JS/CSS front-end (no framework, matching the existing codebase).

**Spec:** `specs/2026-09-05-indices-cac40-design.md` and `Methodologie_Analyse_Indices.md` (both already committed on branch `indice`).

## Global Constraints

- Weights (from methodology): rentabilité 30%, structure financière 25%, croissance 20%, génération de cash 15%, valorisation 10% — sum to 1.0.
- Composite scale: same as gold — `round(sum(score * weight for each factor) * 10, 1)`, range -100 to +100, each factor's `score` in -10..+10.
- Interpretation bands (from methodology): `> +50` "Profil fondamental très solide", `+15` to `+50` "Solide", `-15` to `+15` "Neutre", `< -15` "Fragile".
- Pilot companies (ticker, name, yfinance `sector` string expected): `MC.PA`/LVMH/`Consumer Cyclical`, `TTE.PA`/TotalEnergies/`Energy`, `SU.PA`/Schneider Electric/`Industrials`, `SAN.PA`/Sanofi/`Healthcare`, `BN.PA`/Danone/`Consumer Defensive`.
- Sector risk profiles and leverage-threshold multipliers (from methodology): Défensif (`Utilities`, `Consumer Defensive`, `Healthcare`, `Real Estate`) ×1.3, Standard (`Industrials`, `Communication Services`) ×1.0, Cyclique (`Energy`, `Basic Materials`, `Consumer Cyclical`, `Technology`) ×0.7. Unknown sector defaults to Standard (×1.0).
- Base thresholds (Standard profile): Dette nette/EBITDA confortable = 3.0, risqué = 5.5; couverture des intérêts (ICR) critique = 3.0.
- No new runtime dependency in `requirements.txt` — RSS parsing uses the standard library.
- `docs/` is the GitHub Pages site root — never put planning/spec/test artifacts there.

---

## File Structure

| File | Responsibility |
|---|---|
| `indices_score.py` | Constants, `FactorResult` dataclass, sector profile mapping, the 5 pure scoring functions, `compute_composite`/`interpret`, financial-statement extraction (alias-fallback + per-year multiple reconstruction), yfinance glue, news RSS fetch+parse, orchestration (`build_company_entry`, `main`) |
| `tests/test_indices_score.py` | Unit tests for every pure function in `indices_score.py` (no network calls) |
| `requirements-dev.txt` | `pytest` — dev-only, not installed by the production workflow |
| `.github/workflows/indices.yml` | Daily cron + `workflow_dispatch`, mirrors `daily.yml`'s commit/push pattern |
| `docs/index.html` | Modified: enable "Indices" tab, add `#indices` pane, generalize the pane show/hide routing from 2 panes (home/or) to 3 (home/or/indices), add company list + detail sub-views reusing existing CSS |

---

### Task 1: Project scaffolding — pytest, sector profile mapping

**Files:**
- Create: `requirements-dev.txt`
- Create: `indices_score.py`
- Create: `tests/test_indices_score.py`

**Interfaces:**
- Produces: `SECTOR_PROFILES: dict[str, str]`, `SECTOR_ADJUSTMENT: dict[str, float]`, `sector_risk_profile(sector: str | None) -> str`

- [ ] **Step 1: Create the dev requirements file**

```
pytest
```
Save as `requirements-dev.txt`.

- [ ] **Step 2: Write the failing test for sector profile mapping**

Create `tests/test_indices_score.py`:
```python
from indices_score import sector_risk_profile


def test_sector_risk_profile_defensif():
    assert sector_risk_profile("Healthcare") == "defensif"
    assert sector_risk_profile("Utilities") == "defensif"
    assert sector_risk_profile("Consumer Defensive") == "defensif"
    assert sector_risk_profile("Real Estate") == "defensif"


def test_sector_risk_profile_standard():
    assert sector_risk_profile("Industrials") == "standard"
    assert sector_risk_profile("Communication Services") == "standard"


def test_sector_risk_profile_cyclique():
    assert sector_risk_profile("Energy") == "cyclique"
    assert sector_risk_profile("Consumer Cyclical") == "cyclique"


def test_sector_risk_profile_defaults_to_standard_when_unknown():
    assert sector_risk_profile("Some Unmapped Sector") == "standard"
    assert sector_risk_profile(None) == "standard"
```

- [ ] **Step 2b: Run the test to verify it fails**

Run: `pytest tests/test_indices_score.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'indices_score'`

- [ ] **Step 3: Create `indices_score.py` with constants and the mapping function**

```python
"""
Score fondamental CAC 40 — Phase pilote (5 entreprises)
=========================================================

Calcule un score composite par entreprise à partir de 5 ans de comptes
publiés (via yfinance), selon la méthodologie décrite dans
Methodologie_Analyse_Indices.md (synthèse Vernimmen : rentabilité
comptable, analyse du financement, coût du capital, pratique de
l'évaluation).

Installation :
    pip install requests yfinance pandas
"""

from dataclasses import dataclass


WEIGHTS = {
    "rentabilite": 0.30,
    "structure_financiere": 0.25,
    "croissance": 0.20,
    "generation_cash": 0.15,
    "valorisation": 0.10,
}

COMPANIES = [
    {"ticker": "MC.PA", "name": "LVMH"},
    {"ticker": "TTE.PA", "name": "TotalEnergies"},
    {"ticker": "SU.PA", "name": "Schneider Electric"},
    {"ticker": "SAN.PA", "name": "Sanofi"},
    {"ticker": "BN.PA", "name": "Danone"},
]

SECTOR_PROFILES = {
    "Utilities": "defensif",
    "Consumer Defensive": "defensif",
    "Healthcare": "defensif",
    "Real Estate": "defensif",
    "Industrials": "standard",
    "Communication Services": "standard",
    "Energy": "cyclique",
    "Basic Materials": "cyclique",
    "Consumer Cyclical": "cyclique",
    "Technology": "cyclique",
}

SECTOR_ADJUSTMENT = {"defensif": 1.3, "standard": 1.0, "cyclique": 0.7}


@dataclass
class FactorResult:
    name: str
    score: float          # -10 à +10
    weight: float
    raw_value: str


def sector_risk_profile(sector: str | None) -> str:
    """Renvoie 'defensif' / 'standard' / 'cyclique' pour un secteur
    yfinance donné, 'standard' par défaut si secteur inconnu ou absent."""
    return SECTOR_PROFILES.get(sector, "standard")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_indices_score.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add requirements-dev.txt indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): scaffold module, sector risk profile mapping"
```

---

### Task 2: Score de rentabilité

**Files:**
- Modify: `indices_score.py`
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes: `FactorResult`, `WEIGHTS["rentabilite"]` (from Task 1)
- Produces: `score_rentabilite(roce: float, roe: float, cost_of_capital: float) -> FactorResult`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_indices_score.py`:
```python
from indices_score import score_rentabilite


def test_score_rentabilite_above_cost_of_capital_is_positive():
    result = score_rentabilite(roce=13.0, roe=15.0, cost_of_capital=8.0)
    assert result.name == "Rentabilité / création de valeur"
    assert result.weight == 0.30
    assert result.score == 10.0  # spread of +5pp caps the score at +10
    assert "13.0" in result.raw_value
    assert "8.0" in result.raw_value


def test_score_rentabilite_below_cost_of_capital_is_negative():
    result = score_rentabilite(roce=3.0, roe=2.0, cost_of_capital=8.0)
    assert result.score == -10.0  # spread of -5pp floors the score at -10


def test_score_rentabilite_equal_to_cost_of_capital_is_neutral():
    result = score_rentabilite(roce=8.0, roe=8.0, cost_of_capital=8.0)
    assert result.score == 0.0


def test_score_rentabilite_partial_spread_scales_linearly():
    result = score_rentabilite(roce=10.5, roe=11.0, cost_of_capital=8.0)
    assert result.score == 5.0  # +2.5pp spread / 5.0pp scale * 10 = 5.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k rentabilite -v`
Expected: FAIL with `ImportError: cannot import name 'score_rentabilite'`

- [ ] **Step 3: Implement `score_rentabilite`**

Add to `indices_score.py`:
```python
def _clamp(value: float, low: float = -10.0, high: float = 10.0) -> float:
    return max(low, min(high, value))


ROCE_SPREAD_SCALE = 5.0  # points d'écart ROCE - coût du capital pour un score plein


def score_rentabilite(roce: float, roe: float, cost_of_capital: float) -> FactorResult:
    """
    ROCE = rentabilité économique après IS (Résultat d'exploitation après
    IS / Actif économique). Le signal principal est l'écart entre le ROCE
    et le coût du capital (proxy simplifié) : au-dessus, l'entreprise crée
    de la valeur ; en dessous, elle en détruit. Le ROE est affiché à titre
    informatif (permet de repérer si la rentabilité des capitaux propres
    provient surtout de l'effet de levier plutôt que de la performance
    opérationnelle), sans peser directement sur le score.
    """
    spread = roce - cost_of_capital
    score = _clamp((spread / ROCE_SPREAD_SCALE) * 10)
    return FactorResult(
        "Rentabilité / création de valeur",
        score,
        WEIGHTS["rentabilite"],
        f"ROCE {roce:.1f}% vs coût du capital {cost_of_capital:.1f}% "
        f"(ROE {roe:.1f}%)",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k rentabilite -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): score de rentabilité (ROCE vs coût du capital)"
```

---

### Task 3: Score de structure financière / solvabilité (avec ajustement sectoriel)

**Files:**
- Modify: `indices_score.py`
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes: `FactorResult`, `WEIGHTS["structure_financiere"]`, `sector_risk_profile`, `SECTOR_ADJUSTMENT` (Task 1)
- Produces: `score_structure_financiere(net_debt_ebitda: float, icr: float, sector: str | None) -> FactorResult`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_indices_score.py`:
```python
from indices_score import score_structure_financiere


def test_score_structure_financiere_comfortable_standard_profile():
    # net_debt_ebitda=0 -> +10 sub-score ; icr très élevé -> +10 sub-score
    result = score_structure_financiere(net_debt_ebitda=0.0, icr=10.0, sector="Industrials")
    assert result.name == "Structure financière / solvabilité"
    assert result.weight == 0.25
    assert result.score == 10.0


def test_score_structure_financiere_at_standard_thresholds_is_neutral():
    # pile au seuil confortable (3.0) et pile au seuil critique ICR (3.0) -> 0 des deux côtés
    result = score_structure_financiere(net_debt_ebitda=3.0, icr=3.0, sector="Industrials")
    assert result.score == 0.0


def test_score_structure_financiere_beyond_risky_threshold_floors_at_minus_ten():
    # net_debt_ebitda largement au-delà du seuil risqué (-10) ET icr nul (-10) -> combiné = -10.0
    result = score_structure_financiere(net_debt_ebitda=8.0, icr=0.0, sector="Industrials")
    assert result.score == -10.0


def test_score_structure_financiere_defensif_profile_more_tolerant():
    # même ratio de 4.0x : risqué en Standard (entre 3 et 5.5) mais mieux toléré en Défensif
    # (seuil confortable ajusté = 3.0 * 1.3 = 3.9, donc 4.0 est tout juste au-delà -> proche de 0)
    standard = score_structure_financiere(net_debt_ebitda=4.0, icr=3.0, sector="Industrials")
    defensif = score_structure_financiere(net_debt_ebitda=4.0, icr=3.0, sector="Healthcare")
    assert defensif.score > standard.score


def test_score_structure_financiere_cyclique_profile_stricter():
    cyclique = score_structure_financiere(net_debt_ebitda=2.5, icr=3.0, sector="Energy")
    standard = score_structure_financiere(net_debt_ebitda=2.5, icr=3.0, sector="Industrials")
    assert cyclique.score < standard.score
    assert "cyclique" in cyclique.raw_value
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k structure_financiere -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `score_structure_financiere`**

Add to `indices_score.py`:
```python
NET_DEBT_EBITDA_COMFORTABLE = 3.0   # seuil Standard, ajusté par profil sectoriel
NET_DEBT_EBITDA_RISKY = 5.5         # seuil Standard, ajusté par profil sectoriel
ICR_CRITICAL = 3.0                  # seuil Standard, ajusté par profil sectoriel


def _score_leverage(ratio: float, comfortable: float, risky: float) -> float:
    """+10 à ratio nul, 0 au seuil confortable, -10 au seuil risqué et au-delà."""
    if ratio <= comfortable:
        return 10.0 - 10.0 * (ratio / comfortable)
    if ratio <= risky:
        return -10.0 * (ratio - comfortable) / (risky - comfortable)
    return -10.0


def _score_coverage(icr: float, critical: float) -> float:
    """-10 à ICR nul ou négatif, 0 au seuil critique, +10 au double du seuil critique."""
    if icr <= 0:
        return -10.0
    if icr <= critical:
        return -10.0 + 10.0 * (icr / critical)
    return _clamp(10.0 * (icr - critical) / critical, -10.0, 10.0)


def score_structure_financiere(net_debt_ebitda: float, icr: float, sector: str | None) -> FactorResult:
    """
    Dette nette/EBITDA et couverture des intérêts (ICR = EBIT / frais
    financiers nets), seuils Vernimmen ajustés par profil de risque
    sectoriel : un même niveau d'endettement ne représente pas le même
    risque selon la stabilité des flux de trésorerie du secteur.
    """
    profile = sector_risk_profile(sector)
    adjustment = SECTOR_ADJUSTMENT[profile]

    comfortable = NET_DEBT_EBITDA_COMFORTABLE * adjustment
    risky = NET_DEBT_EBITDA_RISKY * adjustment
    critical_icr = ICR_CRITICAL / adjustment

    leverage_score = _score_leverage(net_debt_ebitda, comfortable, risky)
    coverage_score = _score_coverage(icr, critical_icr)
    score = _clamp((leverage_score + coverage_score) / 2)

    return FactorResult(
        "Structure financière / solvabilité",
        score,
        WEIGHTS["structure_financiere"],
        f"Dette nette/EBITDA {net_debt_ebitda:.1f}x (seuil confort "
        f"{comfortable:.1f}x, profil {profile}) — ICR {icr:.1f}x",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k structure_financiere -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): score structure financière avec ajustement sectoriel"
```

---

### Task 4: Score de croissance

**Files:**
- Modify: `indices_score.py`
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes: `FactorResult`, `WEIGHTS["croissance"]` (Task 1)
- Produces: `score_croissance(cagr_ca: float, cagr_ebitda: float) -> FactorResult`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_indices_score.py`:
```python
from indices_score import score_croissance


def test_score_croissance_strong_aligned_growth():
    result = score_croissance(cagr_ca=12.0, cagr_ebitda=12.0)
    assert result.name == "Croissance"
    assert result.weight == 0.20
    assert result.score == 10.0  # moyenne 12% / échelle 10% -> plafonné à +10


def test_score_croissance_no_growth_is_neutral():
    result = score_croissance(cagr_ca=0.0, cagr_ebitda=0.0)
    assert result.score == 0.0


def test_score_croissance_decline_is_negative():
    result = score_croissance(cagr_ca=-10.0, cagr_ebitda=-10.0)
    assert result.score == -10.0


def test_score_croissance_penalizes_ebitda_divergence():
    # CA croît bien, mais l'EBITDA décroche largement (dégradation de la rentabilité)
    aligned = score_croissance(cagr_ca=6.0, cagr_ebitda=6.0)
    diverging = score_croissance(cagr_ca=6.0, cagr_ebitda=-2.0)
    assert diverging.score < aligned.score
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k croissance -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `score_croissance`**

Add to `indices_score.py`:
```python
GROWTH_SCALE = 10.0          # % de CAGR moyen pour un score plein
GROWTH_DIVERGENCE_MARGIN = 5.0   # points d'écart CA/EBITDA tolérés avant pénalité
GROWTH_DIVERGENCE_PENALTY = 3.0


def score_croissance(cagr_ca: float, cagr_ebitda: float) -> FactorResult:
    """
    CAGR chiffre d'affaires et EBITDA sur 5 ans. Une croissance du CA non
    suivie par l'EBITDA signale une dégradation de la rentabilité -> pénalité.
    """
    base = _clamp(((cagr_ca + cagr_ebitda) / 2) / GROWTH_SCALE * 10)
    if cagr_ebitda < cagr_ca - GROWTH_DIVERGENCE_MARGIN:
        base = _clamp(base - GROWTH_DIVERGENCE_PENALTY)
    return FactorResult(
        "Croissance",
        base,
        WEIGHTS["croissance"],
        f"CAGR CA {cagr_ca:+.1f}%/an, CAGR EBITDA {cagr_ebitda:+.1f}%/an (5 ans)",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k croissance -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): score de croissance (CAGR CA/EBITDA 5 ans)"
```

---

### Task 5: Score de génération de cash

**Files:**
- Modify: `indices_score.py`
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes: `FactorResult`, `WEIGHTS["generation_cash"]` (Task 1)
- Produces: `score_generation_cash(fcf_conversion: float) -> FactorResult`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_indices_score.py`:
```python
from indices_score import score_generation_cash


def test_score_generation_cash_full_conversion():
    result = score_generation_cash(fcf_conversion=100.0)
    assert result.name == "Génération de cash"
    assert result.weight == 0.15
    assert result.score == 10.0


def test_score_generation_cash_neutral_at_fifty_percent():
    result = score_generation_cash(fcf_conversion=50.0)
    assert result.score == 0.0


def test_score_generation_cash_negative_conversion_floors_at_minus_ten():
    result = score_generation_cash(fcf_conversion=-20.0)
    assert result.score == -10.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k generation_cash -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `score_generation_cash`**

Add to `indices_score.py`:
```python
FCF_CONVERSION_NEUTRAL = 50.0   # % de conversion FCF/EBITDA jugé neutre
FCF_CONVERSION_SCALE = 5.0      # points de conversion % pour 1 point de score


def score_generation_cash(fcf_conversion: float) -> FactorResult:
    """Conversion FCF/EBITDA (%) : au-dessus de 50%, la rentabilité comptable
    se traduit bien en cash réel ; en dessous, le BFR ou les capex absorbent
    l'essentiel de la génération de cash."""
    score = _clamp((fcf_conversion - FCF_CONVERSION_NEUTRAL) / FCF_CONVERSION_SCALE)
    return FactorResult(
        "Génération de cash",
        score,
        WEIGHTS["generation_cash"],
        f"Conversion FCF/EBITDA {fcf_conversion:.0f}%",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k generation_cash -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): score de génération de cash (conversion FCF/EBITDA)"
```

---

### Task 6: Score de valorisation relative

**Files:**
- Modify: `indices_score.py`
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes: `FactorResult`, `WEIGHTS["valorisation"]` (Task 1)
- Produces: `score_valorisation(current_ev_ebitda: float, avg_ev_ebitda_5y: float, current_pe: float, avg_pe_5y: float, cagr_ebitda: float) -> FactorResult`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_indices_score.py`:
```python
from indices_score import score_valorisation


def test_score_valorisation_trading_at_discount_is_positive():
    # EV/EBITDA et PER tous deux 30% sous leur moyenne 5 ans -> décote favorable
    result = score_valorisation(
        current_ev_ebitda=7.0, avg_ev_ebitda_5y=10.0,
        current_pe=10.5, avg_pe_5y=15.0,
        cagr_ebitda=5.0,
    )
    assert result.name == "Valorisation relative"
    assert result.weight == 0.10
    assert result.score > 0


def test_score_valorisation_premium_with_weak_growth_is_penalized():
    result = score_valorisation(
        current_ev_ebitda=13.0, avg_ev_ebitda_5y=10.0,
        current_pe=19.5, avg_pe_5y=15.0,
        cagr_ebitda=1.0,  # croissance faible -> la prime n'est pas justifiée
    )
    assert result.score < 0


def test_score_valorisation_premium_with_strong_growth_is_dampened():
    weak_growth = score_valorisation(
        current_ev_ebitda=13.0, avg_ev_ebitda_5y=10.0,
        current_pe=19.5, avg_pe_5y=15.0,
        cagr_ebitda=1.0,
    )
    strong_growth = score_valorisation(
        current_ev_ebitda=13.0, avg_ev_ebitda_5y=10.0,
        current_pe=19.5, avg_pe_5y=15.0,
        cagr_ebitda=12.0,  # même prime, mais croissance forte -> pénalité atténuée
    )
    assert strong_growth.score > weak_growth.score


def test_score_valorisation_at_historical_average_is_neutral():
    result = score_valorisation(
        current_ev_ebitda=10.0, avg_ev_ebitda_5y=10.0,
        current_pe=15.0, avg_pe_5y=15.0,
        cagr_ebitda=5.0,
    )
    assert result.score == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k valorisation -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `score_valorisation`**

Add to `indices_score.py`:
```python
VALUATION_PREMIUM_SCALE = 3.0       # % d'écart au multiple historique pour 1 point de score
VALUATION_GROWTH_DAMPENING_CAGR = 5.0   # au-dessus de ce CAGR EBITDA, une prime est jugée justifiée
VALUATION_GROWTH_DAMPENING_FACTOR = 0.4  # atténuation de la pénalité si croissance forte


def _premium_score(current: float, avg_5y: float, cagr_ebitda: float) -> float:
    """Décote vs moyenne 5 ans -> score positif (favorable) ; prime -> score
    négatif, mais atténué si la croissance de l'EBITDA justifie une prime."""
    if avg_5y == 0:
        return 0.0
    premium_pct = (current / avg_5y - 1.0) * 100
    if premium_pct <= 0:
        return _clamp(-premium_pct / VALUATION_PREMIUM_SCALE, 0.0, 10.0)
    dampening = (
        VALUATION_GROWTH_DAMPENING_FACTOR
        if cagr_ebitda >= VALUATION_GROWTH_DAMPENING_CAGR
        else 1.0
    )
    penalty = _clamp(premium_pct / VALUATION_PREMIUM_SCALE, 0.0, 10.0) * dampening
    return -penalty


def score_valorisation(
    current_ev_ebitda: float, avg_ev_ebitda_5y: float,
    current_pe: float, avg_pe_5y: float,
    cagr_ebitda: float,
) -> FactorResult:
    """Multiples EV/EBITDA et P/E actuels comparés à la moyenne 5 ans de
    l'entreprise elle-même (pas de comparaison à des pairs au v1). Une
    prime n'est pénalisée que modérément et seulement si elle n'est pas
    soutenue par la croissance de l'EBITDA (cf. Méthodologie section 5)."""
    ev_ebitda_score = _premium_score(current_ev_ebitda, avg_ev_ebitda_5y, cagr_ebitda)
    pe_score = _premium_score(current_pe, avg_pe_5y, cagr_ebitda)
    score = _clamp((ev_ebitda_score + pe_score) / 2)
    return FactorResult(
        "Valorisation relative",
        score,
        WEIGHTS["valorisation"],
        f"EV/EBITDA {current_ev_ebitda:.1f}x (moy. 5 ans {avg_ev_ebitda_5y:.1f}x) — "
        f"PER {current_pe:.1f}x (moy. 5 ans {avg_pe_5y:.1f}x)",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k valorisation -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): score de valorisation relative (EV/EBITDA, PER vs historique)"
```

---

### Task 7: Score composite et interprétation

**Files:**
- Modify: `indices_score.py`
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes: `FactorResult` (Task 1)
- Produces: `compute_composite(factors: list[FactorResult]) -> float`, `interpret(composite: float) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_indices_score.py`:
```python
from indices_score import compute_composite, interpret, FactorResult


def test_compute_composite_all_max_positive():
    factors = [
        FactorResult("A", 10.0, 0.30, ""),
        FactorResult("B", 10.0, 0.25, ""),
        FactorResult("C", 10.0, 0.20, ""),
        FactorResult("D", 10.0, 0.15, ""),
        FactorResult("E", 10.0, 0.10, ""),
    ]
    assert compute_composite(factors) == 100.0


def test_compute_composite_all_zero_is_neutral():
    factors = [FactorResult("A", 0.0, 1.0, "")]
    assert compute_composite(factors) == 0.0


def test_interpret_bands():
    assert interpret(60.0) == "Profil fondamental très solide"
    assert interpret(20.0) == "Solide"
    assert interpret(0.0) == "Neutre"
    assert interpret(-30.0) == "Fragile"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k "composite or interpret" -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement both functions**

Add to `indices_score.py`:
```python
def compute_composite(factors: list[FactorResult]) -> float:
    weighted_sum = sum(f.score * f.weight for f in factors)
    return round(weighted_sum * 10, 1)


def interpret(composite: float) -> str:
    if composite > 50:
        return "Profil fondamental très solide"
    if composite > 15:
        return "Solide"
    if composite > -15:
        return "Neutre"
    return "Fragile"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "composite or interpret" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): score composite et bandes d'interprétation"
```

---

### Task 8: Extraction des données comptables (alias fallback + reconstruction des multiples annuels)

**Files:**
- Modify: `indices_score.py`
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Produces: `get_row(df, *aliases) -> "pandas.Series"`, `extract_ratios(financials, balance_sheet, cashflow, closes_by_year: dict[str, float], shares_outstanding: float) -> dict`

This task's functions take pandas DataFrames/Series shaped exactly like
yfinance's `Ticker(t).financials` / `.balance_sheet` / `.cashflow`
(index = line-item labels, columns = fiscal year-end dates, most recent
first) — so tests build small fixture DataFrames instead of calling the
network, but the real shape is preserved.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_indices_score.py`:
```python
import pandas as pd
from indices_score import get_row, extract_ratios


def test_get_row_returns_first_matching_alias():
    df = pd.DataFrame({"2025-12-31": [100.0]}, index=["Total Debt"])
    row = get_row(df, "Net Debt", "Total Debt")
    assert row.iloc[0] == 100.0


def test_get_row_raises_when_no_alias_matches():
    df = pd.DataFrame({"2025-12-31": [100.0]}, index=["Something Else"])
    try:
        get_row(df, "Net Debt", "Total Debt")
        assert False, "expected KeyError"
    except KeyError:
        pass


def _make_fixture_statements():
    years = ["2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31", "2021-12-31"]
    financials = pd.DataFrame(
        {
            years[0]: [1000, 200, 150, 140, 0.25],
            years[1]: [950, 185, 138, 130, 0.25],
            years[2]: [900, 170, 128, 120, 0.25],
            years[3]: [850, 155, 116, 108, 0.25],
            years[4]: [800, 140, 104, 96, 0.25],
        },
        index=["Total Revenue", "EBITDA", "EBIT", "Net Income", "Tax Rate For Calcs"],
    )
    balance_sheet = pd.DataFrame(
        {
            years[0]: [300, 50],
            years[1]: [320, 45],
            years[2]: [340, 40],
            years[3]: [360, 35],
            years[4]: [380, 30],
        },
        index=["Total Debt", "Stockholders Equity"],
    )
    balance_sheet.loc["Cash And Cash Equivalents"] = [50, 45, 40, 35, 30]
    cashflow = pd.DataFrame(
        {
            years[0]: [120, -30],
            years[1]: [110, -28],
            years[2]: [100, -26],
            years[3]: [90, -24],
            years[4]: [80, -22],
        },
        index=["Operating Cash Flow", "Capital Expenditure"],
    )
    closes_by_year = {y: 100.0 for y in years}
    return financials, balance_sheet, cashflow, closes_by_year


def test_extract_ratios_computes_expected_keys():
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )
    for key in [
        "roce", "roe", "net_debt_ebitda", "icr", "cagr_ca", "cagr_ebitda",
        "fcf_conversion", "current_ev_ebitda", "avg_ev_ebitda_5y",
        "current_pe", "avg_pe_5y",
    ]:
        assert key in ratios, f"clé manquante : {key}"
    # Revenu passe de 800 à 1000 sur 4 intervalles annuels -> CAGR ~ 5.7%
    assert 5.0 < ratios["cagr_ca"] < 6.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k "get_row or extract_ratios" -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `get_row` and `extract_ratios`**

Add to `indices_score.py`:
```python
def get_row(df, *aliases):
    """Renvoie la première ligne du DataFrame dont le libellé correspond à
    l'un des alias fournis. Les libellés de lignes yfinance varient parfois
    d'une entreprise à l'autre (ex: 'Total Debt' absent chez certaines) —
    d'où la liste d'alias plutôt qu'un seul nom fixe."""
    for alias in aliases:
        if alias in df.index:
            return df.loc[alias]
    raise KeyError(
        f"Aucune des lignes {aliases} trouvée (lignes disponibles : {list(df.index)})"
    )


def _cagr(first_value: float, last_value: float, years: int) -> float:
    """CAGR en % entre la valeur la plus ancienne et la plus récente."""
    if first_value <= 0 or years <= 0:
        return 0.0
    return ((last_value / first_value) ** (1 / years) - 1) * 100


def extract_ratios(financials, balance_sheet, cashflow, closes_by_year, shares_outstanding: float) -> dict:
    """
    Calcule les ratios bruts nécessaires aux fonctions de score à partir des
    états financiers yfinance (financials, balance_sheet, cashflow — colonnes
    = dates d'exercice, la plus récente en premier) et des cours de clôture
    par date d'exercice (closes_by_year, même clés que les colonnes).
    """
    years_cols = list(financials.columns)  # plus récent en premier
    n_years = len(years_cols)
    latest, oldest = years_cols[0], years_cols[-1]

    revenue = get_row(financials, "Total Revenue", "Operating Revenue")
    ebitda = get_row(financials, "EBITDA", "Normalized EBITDA")
    ebit = get_row(financials, "EBIT", "Operating Income", "Total Operating Income As Reported")
    net_income = get_row(financials, "Net Income", "Net Income Common Stockholders")
    tax_rate = get_row(financials, "Tax Rate For Calcs")

    total_debt = get_row(balance_sheet, "Total Debt")
    cash = get_row(balance_sheet, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments")
    equity = get_row(balance_sheet, "Stockholders Equity", "Common Stock Equity")

    op_cash_flow = get_row(cashflow, "Operating Cash Flow")
    capex = get_row(cashflow, "Capital Expenditure")

    net_debt_latest = total_debt[latest] - cash[latest]
    economic_assets_latest = equity[latest] + net_debt_latest
    roce = (ebit[latest] * (1 - tax_rate[latest]) / economic_assets_latest) * 100 if economic_assets_latest else 0.0
    roe = (net_income[latest] / equity[latest]) * 100 if equity[latest] else 0.0

    net_debt_ebitda = net_debt_latest / ebitda[latest] if ebitda[latest] else 0.0
    icr = ebit[latest] / (total_debt[latest] * 0.03) if total_debt[latest] else 10.0  # proxy frais financiers si non isolés

    cagr_ca = _cagr(revenue[oldest], revenue[latest], n_years - 1)
    cagr_ebitda = _cagr(ebitda[oldest], ebitda[latest], n_years - 1)

    fcf = op_cash_flow[latest] + capex[latest]  # capex déjà négatif dans yfinance
    fcf_conversion = (fcf / ebitda[latest]) * 100 if ebitda[latest] else 0.0

    ev_ebitda_by_year, pe_by_year = [], []
    for col in years_cols:
        price = closes_by_year.get(col)
        if price is None or not ebitda[col] or not net_income[col]:
            continue
        market_cap = price * shares_outstanding
        net_debt_year = total_debt[col] - cash[col]
        ev_ebitda_by_year.append((market_cap + net_debt_year) / ebitda[col])
        pe_by_year.append(market_cap / net_income[col])

    current_ev_ebitda = ev_ebitda_by_year[0] if ev_ebitda_by_year else 0.0
    avg_ev_ebitda_5y = sum(ev_ebitda_by_year) / len(ev_ebitda_by_year) if ev_ebitda_by_year else 0.0
    current_pe = pe_by_year[0] if pe_by_year else 0.0
    avg_pe_5y = sum(pe_by_year) / len(pe_by_year) if pe_by_year else 0.0

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

**Known risk (already flagged in the spec):** the ICR calculation above
uses a 3%-of-total-debt proxy for net interest expense because yfinance
does not always expose a clean "Interest Expense" line net of interest
income. Task 9's manual verification step will confirm whether a direct
`Interest Expense` row is available for the 5 pilot tickers and switch
to it if so (replace the proxy line with
`interest_expense = get_row(financials, "Interest Expense")[latest]`
and `icr = ebit[latest] / interest_expense`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "get_row or extract_ratios" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): extraction des ratios depuis les états financiers yfinance"
```

---

### Task 9: Récupération des données yfinance (glue) + vérification manuelle sur les 5 pilotes

**Files:**
- Modify: `indices_score.py`

**Interfaces:**
- Consumes: `extract_ratios`, `get_row` (Task 8)
- Produces: `fetch_company_financials(ticker: str) -> dict` (returns the same dict shape as `extract_ratios`, plus `"sector"`)

This task has no unit test (it's a thin network-calling wrapper) — it
is verified by manual execution against the real pilot tickers, which
is where the yfinance line-item risk flagged in the spec gets resolved
for real.

- [ ] **Step 1: Implement `fetch_company_financials`**

Add to `indices_score.py`:
```python
def fetch_company_financials(ticker: str) -> dict:
    if yf is None:
        raise RuntimeError("yfinance n'est pas installé (pip install yfinance)")
    t = yf.Ticker(ticker)
    financials = t.financials
    balance_sheet = t.balance_sheet
    cashflow = t.cashflow
    info = t.info

    shares_outstanding = info.get("sharesOutstanding") or 0.0
    history = t.history(period="6y")["Close"]
    closes_by_year = {}
    for col in financials.columns:
        target_date = col.date() if hasattr(col, "date") else col
        window = history[history.index.date <= target_date] if hasattr(history.index, "date") else history
        if len(window):
            closes_by_year[col] = float(window.iloc[-1])

    ratios = extract_ratios(financials, balance_sheet, cashflow, closes_by_year, shares_outstanding)
    ratios["sector"] = info.get("sector")
    return ratios
```

Add near the top of `indices_score.py` (alongside the other imports):
```python
try:
    import yfinance as yf
except ImportError:
    yf = None
```

- [ ] **Step 2: Run against the 5 real pilot tickers and fix any missing aliases**

Run this ad-hoc from the repo root (not a permanent test — a one-time
verification, per the risk noted in the spec):
```bash
python3 -c "
from indices_score import COMPANIES, fetch_company_financials
for c in COMPANIES:
    try:
        r = fetch_company_financials(c['ticker'])
        print(c['ticker'], 'OK', r)
    except KeyError as e:
        print(c['ticker'], 'MISSING LINE ITEM:', e)
"
```
For any `KeyError`, open a Python REPL, inspect
`yf.Ticker(ticker).financials.index` / `.balance_sheet.index` /
`.cashflow.index` for that specific ticker, and add the real label as an
extra alias to the corresponding `get_row(...)` call in `extract_ratios`
(Task 8). Re-run until all 5 pilots succeed without a `KeyError`.

- [ ] **Step 3: Commit**

```bash
git add indices_score.py
git commit -m "feat(indices): récupération des données financières yfinance par entreprise"
```

---

### Task 10: Récupération et parsing des news (RSS)

**Files:**
- Modify: `indices_score.py`
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Produces: `parse_news_rss(xml_bytes: bytes) -> list[dict]` (pure, tested), `fetch_news(company_name: str) -> list[dict]` (thin glue, not unit-tested)

- [ ] **Step 1: Write the failing test for the pure parsing function**

Append to `tests/test_indices_score.py`:
```python
from indices_score import parse_news_rss

SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Google News</title>
<item>
  <title>LVMH annonce une hausse de ses ventes</title>
  <link>https://example.com/article1</link>
  <pubDate>Thu, 04 Sep 2026 10:00:00 GMT</pubDate>
</item>
<item>
  <title>LVMH ouvre un nouveau magasin</title>
  <link>https://example.com/article2</link>
  <pubDate>Wed, 03 Sep 2026 08:00:00 GMT</pubDate>
</item>
</channel></rss>
"""


def test_parse_news_rss_extracts_title_date_link():
    items = parse_news_rss(SAMPLE_RSS)
    assert len(items) == 2
    assert items[0]["title"] == "LVMH annonce une hausse de ses ventes"
    assert items[0]["link"] == "https://example.com/article1"
    assert items[0]["date"] == "2026-09-04"


def test_parse_news_rss_limits_to_five_items():
    many_items = b"<rss><channel>" + b"".join(
        f"<item><title>Titre {i}</title><link>https://example.com/{i}</link>"
        f"<pubDate>Thu, 04 Sep 2026 10:00:00 GMT</pubDate></item>".encode()
        for i in range(10)
    ) + b"</channel></rss>"
    items = parse_news_rss(many_items)
    assert len(items) == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_indices_score.py -k parse_news_rss -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `parse_news_rss` and `fetch_news`**

Add to `indices_score.py`:
```python
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import requests


NEWS_RSS_URL = "https://news.google.com/rss/search"
NEWS_MAX_ITEMS = 5


def parse_news_rss(xml_bytes: bytes) -> list[dict]:
    """Extrait titre/date/lien des N premiers <item> d'un flux RSS Google News."""
    root = ET.fromstring(xml_bytes)
    items = []
    for item in root.findall(".//item")[:NEWS_MAX_ITEMS]:
        title = item.findtext("title", default="")
        link = item.findtext("link", default="")
        pub_date_raw = item.findtext("pubDate", default="")
        try:
            date_str = parsedate_to_datetime(pub_date_raw).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            date_str = ""
        items.append({"title": title, "date": date_str, "link": link})
    return items


def fetch_news(company_name: str) -> list[dict]:
    params = {"q": company_name, "hl": "fr", "gl": "FR", "ceid": "FR:fr"}
    resp = requests.get(NEWS_RSS_URL, params=params, timeout=15)
    resp.raise_for_status()
    return parse_news_rss(resp.content)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k parse_news_rss -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): récupération et parsing des news (flux RSS Google News)"
```

---

### Task 11: Orchestration — `build_company_entry` et `main()`

**Files:**
- Modify: `indices_score.py`

**Interfaces:**
- Consumes: everything from Tasks 1-10
- Produces: `build_company_entry(ticker: str, name: str) -> dict`, `main() -> None`

- [ ] **Step 1: Implement `build_company_entry`**

Add to `indices_score.py`:
```python
import json
import os
from datetime import datetime


OUTPUT_JSON_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "docs", "indices.json"
)

# Approximation simplifiée du coût du capital (pas de calcul de bêta désendetté
# au v1) : taux sans risque + prime de risque marché, cf. section "hors périmètre"
# de la méthodologie.
COST_OF_CAPITAL_PROXY = 8.0  # %


def build_company_entry(ticker: str, name: str) -> dict:
    data = fetch_company_financials(ticker)
    sector = data["sector"]

    factors = [
        score_rentabilite(data["roce"], data["roe"], COST_OF_CAPITAL_PROXY),
        score_structure_financiere(data["net_debt_ebitda"], data["icr"], sector),
        score_croissance(data["cagr_ca"], data["cagr_ebitda"]),
        score_generation_cash(data["fcf_conversion"]),
        score_valorisation(
            data["current_ev_ebitda"], data["avg_ev_ebitda_5y"],
            data["current_pe"], data["avg_pe_5y"], data["cagr_ebitda"],
        ),
    ]
    composite = compute_composite(factors)
    news = fetch_news(name)

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

- [ ] **Step 2: Implement `main()`**

Add to `indices_score.py`:
```python
def main():
    companies = []
    for company in COMPANIES:
        try:
            companies.append(build_company_entry(company["ticker"], company["name"]))
        except Exception as e:
            print(f"Erreur pour {company['ticker']} ({company['name']}) : {e}")

    payload = {
        "updated": datetime.today().strftime("%Y-%m-%d"),
        "companies": companies,
    }

    os.makedirs(os.path.dirname(OUTPUT_JSON_PATH), exist_ok=True)
    with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print(f"Données exportées vers : {OUTPUT_JSON_PATH}")
    for c in companies:
        print(f"  {c['ticker']:<8} {c['name']:<20} score {c['score']:+.1f}  ({c['interpretation']})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run manually end-to-end**

```bash
python3 indices_score.py
```
Expected: prints a score line for each of the 5 pilots and writes
`docs/indices.json`. If any ticker errors, it's printed but does not
stop the other companies from being processed (matches `gold_score.py`'s
tolerance for partial failures).

- [ ] **Step 4: Commit**

```bash
git add indices_score.py
git commit -m "feat(indices): orchestration complète et génération de docs/indices.json"
```

---

### Task 12: Workflow GitHub Actions quotidien

**Files:**
- Create: `.github/workflows/indices.yml`

**Interfaces:**
- Consumes: `indices_score.py` (Task 11), secrets already configured for the repo (none new needed — no API key required for yfinance or Google News RSS)

- [ ] **Step 1: Create the workflow file**

```yaml
name: Score fondamental Indices (CAC 40 pilote)

on:
  schedule:
    - cron: "0 6 * * *"   # une fois par jour, 06h00 UTC
  workflow_dispatch: {}

concurrency:
  group: score-indices
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

      - name: Calculer le score des entreprises pilotes
        run: python3 indices_score.py

      - name: Publier les données mises à jour sur le site
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add docs/indices.json
          git diff --staged --quiet || git commit -m "Mise à jour du score Indices"
          git pull --rebase --autostash
          git push
```
Save as `.github/workflows/indices.yml`.

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/indices.yml
git commit -m "ci(indices): workflow quotidien pour le score fondamental CAC 40"
```

- [ ] **Step 3: Push the branch and trigger a manual run to verify**

```bash
git push origin indice
```
Then, via `gh workflow run indices.yml -R Alexandreauq/analyse-or --ref indice`
(or the Actions tab), trigger a manual run and confirm it completes
successfully and that `docs/indices.json` is created/updated with real
data for the 5 pilots.

---

### Task 13: Front-end — activer l'onglet "Indices"

**Files:**
- Modify: `docs/index.html`

**Interfaces:**
- Consumes: `docs/indices.json` (Task 11/12's output), existing CSS classes `tab`, `toggle-btn`, `section-wrap`, `notice`, `panel`, `alert-row`, `back-link`
- Produces: a working `#indices` pane with a company list and per-company detail, hash-routed as `#indices` (list) / `#indices/<ticker>` (detail)

This task modifies existing routing code. The current `docs/index.html`
has exactly two panes (`#home`, `#or`) with `showOr(animate)` /
`showHome(animate)` functions and a binary `currentRoute()`. This task
generalizes that to N panes sliding over `#home`.

- [ ] **Step 1: Enable the "Indices" tab button**

In `docs/index.html`, find:
```html
<button class="tab disabled" disabled>Indices</button>
```
Replace with:
```html
<button class="tab" data-route="indices">Indices</button>
```

- [ ] **Step 2: Add the `#indices` pane markup**

Find the closing `</div>` of `<div id="or" class="screen app" hidden>...</div>`
and insert immediately after it:
```html
<div id="indices" class="screen app" hidden>
  <header>
    <a href="#home" class="back-link">← IA Investment</a>
    <div class="eyebrow">Analyse — phase pilote</div>
    <h1>Indices</h1>
    <p class="updated" id="indicesUpdated"></p>
  </header>

  <main id="indicesContent">
    <div class="empty">Chargement…</div>
  </main>
</div>
```

- [ ] **Step 3: Replace the binary routing functions with a generalized N-pane version**

Find `showOr`, `showHome`, `currentRoute`, and `renderRoute` in the
`<script>` block. Replace the whole block (from `function currentRoute()`
through the final `renderRoute(false);` call) with:
```js
const SLIDING_PANES = ['or', 'indices'];
let indicesLoaded = false;
let indicesData = null;

function parseRoute() {
  const hash = location.hash.slice(1);
  if (hash === 'or') return { screen: 'or' };
  if (hash === 'indices') return { screen: 'indices', ticker: null };
  if (hash.startsWith('indices/')) return { screen: 'indices', ticker: hash.slice('indices/'.length) };
  return { screen: 'home' };
}

function showPane(id, animate) {
  const el = document.getElementById(id);
  const alreadyVisible = !el.hidden && el.classList.contains('screen-in');
  el.hidden = false;
  if (alreadyVisible) return;
  if (!animate) {
    el.classList.add('no-anim');
    el.classList.add('screen-in');
    requestAnimationFrame(() => el.classList.remove('no-anim'));
  } else {
    requestAnimationFrame(() => el.classList.add('screen-in'));
  }
}

function hidePane(id, animate) {
  const el = document.getElementById(id);
  if (el.hidden) return;
  if (!animate) {
    el.classList.remove('screen-in');
    el.hidden = true;
    return;
  }
  el.classList.remove('screen-in');
  const onEnd = (e) => {
    if (e.propertyName !== 'transform') return;
    el.hidden = true;
    el.removeEventListener('transitionend', onEnd);
  };
  el.addEventListener('transitionend', onEnd);
}

function renderRoute(animate) {
  const route = parseRoute();
  document.querySelectorAll('.tab[data-route]').forEach(tab => {
    tab.classList.toggle('active', tab.dataset.route === route.screen);
  });
  SLIDING_PANES.forEach(id => {
    if (id === route.screen) showPane(id, animate);
    else hidePane(id, animate);
  });
  if (route.screen === 'or' && !scoreLoaded) {
    scoreLoaded = true;
    loadScore();
  }
  if (route.screen === 'indices') {
    loadIndices(route.ticker);
  }
}

document.querySelectorAll('.tab[data-route]').forEach(tab => {
  tab.addEventListener('click', () => { location.hash = '#' + tab.dataset.route; });
});
window.addEventListener('hashchange', () => renderRoute(true));
renderRoute(false);
```

**Note:** this removes the old `showOr`/`showHome`/`currentRoute`
functions entirely (their logic is now `showPane`/`hidePane`/`parseRoute`,
generalized to any pane id) — do not leave the old functions in place,
they'd conflict with the new `#or` handling.

- [ ] **Step 4: Implement `loadIndices` and rendering (list + detail)**

Add this new function to the `<script>` block, right before the
`renderRoute`/pane functions added in Step 3 (so `renderRoute` can call it):
```js
async function loadIndices(ticker) {
  const content = document.getElementById('indicesContent');
  if (!indicesData) {
    try {
      const res = await fetch('indices.json?t=' + Date.now());
      if (!res.ok) throw new Error('indices.json introuvable');
      indicesData = await res.json();
    } catch (e) {
      content.innerHTML = `<div class="empty">Données indisponibles pour l'instant.<br>${e.message}</div>`;
      return;
    }
  }
  document.getElementById('indicesUpdated').textContent = 'Mis à jour le ' + indicesData.updated;

  if (!ticker) {
    renderIndicesList(indicesData.companies);
  } else {
    const company = indicesData.companies.find(c => c.ticker === ticker);
    if (!company) {
      content.innerHTML = `<div class="empty">Entreprise inconnue : ${ticker}</div>`;
      return;
    }
    renderCompanyDetail(company);
  }
}

function renderIndicesList(companies) {
  const rows = companies.map(c => `
    <a class="company-row" href="#indices/${c.ticker}">
      <div class="company-row-left">
        <span class="company-name">${c.name}</span>
        <span class="company-ticker">${c.ticker}</span>
      </div>
      <span class="company-score" style="color:${c.score >= 0 ? 'var(--gold)' : 'var(--rust)'};">
        ${c.score > 0 ? '+' : ''}${c.score}
      </span>
    </a>`).join('');
  document.getElementById('indicesContent').innerHTML = `
    <div class="panel">${rows}</div>
  `;
}

function renderCompanyDetail(company) {
  const factorsHtml = company.factors.map(f => {
    const pct = Math.min(50, Math.abs(f.score) / 10 * 50);
    const positive = f.score >= 0;
    return `
      <div class="factor-row">
        <div class="factor-head">
          <span class="factor-name">${f.name}</span>
          <span class="factor-weight">${Math.round(f.weight * 100)}%</span>
        </div>
        <div class="bar-track">
          <div class="bar-center"></div>
          <div class="bar-fill" style="left:${positive ? 50 : 50 - pct}%; width:${pct}%; background:${positive ? 'var(--gold)' : 'var(--rust)'};"></div>
        </div>
        <p class="factor-note">${f.raw_value}</p>
      </div>`;
  }).join('');

  const newsHtml = (company.news || []).map(n => `
    <div class="cal-row">
      <div class="cal-left">
        <span class="cal-date">${n.date}</span>
        <a href="${n.link}" target="_blank" rel="noopener">${n.title}</a>
      </div>
    </div>`).join('');

  document.getElementById('indicesContent').innerHTML = `
    <a href="#indices" class="back-link">← Indices</a>
    <section class="hero">
      <div class="hero-number">${company.score > 0 ? '+' : ''}${company.score}</div>
      <div class="hero-label">${company.name} — ${company.interpretation}</div>
    </section>
    <div class="divider"></div>

    <div class="btn-row">
      <button class="toggle-btn" id="toggleCompanyActu" type="button" aria-expanded="false">
        Actu <span class="chevron">▾</span>
      </button>
      <button class="toggle-btn" id="toggleCompanyDetail" type="button" aria-expanded="false">
        Détail du calcul <span class="chevron">▾</span>
      </button>
    </div>

    <div class="section-wrap" id="companyActuWrap" hidden>
      <h2>Actualités récentes</h2>
      ${newsHtml || '<p class="hero-sub">Aucune actualité récente.</p>'}
    </div>

    <div class="section-wrap" id="companyDetailWrap" hidden>
      <div class="notice">
        Score composite sur l'échelle -100/+100, construit à partir de 5
        facteurs pondérés (rentabilité, structure financière, croissance,
        génération de cash, valorisation), chacun noté de -10 à +10.
        Profil sectoriel : ${company.sector_profile} (${company.sector}).
      </div>
      ${factorsHtml}
    </div>
  `;

  const toggles = [
    ['toggleCompanyActu', 'companyActuWrap'],
    ['toggleCompanyDetail', 'companyDetailWrap'],
  ];
  toggles.forEach(([btnId, wrapId]) => {
    document.getElementById(btnId).addEventListener('click', (e) => {
      const btn = e.currentTarget;
      const wrap = document.getElementById(wrapId);
      const open = wrap.hidden;
      toggles.forEach(([otherBtnId, otherWrapId]) => {
        if (otherWrapId === wrapId) return;
        document.getElementById(otherWrapId).hidden = true;
        document.getElementById(otherBtnId).setAttribute('aria-expanded', 'false');
      });
      wrap.hidden = !open;
      btn.setAttribute('aria-expanded', String(open));
    });
  });
}
```

- [ ] **Step 5: Add the small amount of new CSS for the company list rows**

In the `<style>` block, right after the existing `.cal-row` rules, add:
```css
.company-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px 0; border-bottom: 1px solid var(--border);
  color: var(--text);
}
.company-row:last-child { border-bottom: none; }
.company-row-left { display: flex; flex-direction: column; }
.company-name { font-weight: 500; font-size: 14px; }
.company-ticker { color: var(--muted); font-size: 12px; }
.company-score { font-family: 'Fraunces', serif; font-size: 20px; }
```

- [ ] **Step 6: Manual verification in a browser**

```bash
cd docs && python3 -m http.server 8800
```
Open `http://localhost:8800/index.html#indices`, confirm the 5 pilot
companies list with their scores, click one, confirm the detail view
renders with the "Actu"/"Détail du calcul" accordion behaving exactly
like the "Or" screen's (mutually exclusive, opens/closes correctly), and
confirm the "← Indices" and "← IA Investment" back links both work.
Check the browser console for errors.

- [ ] **Step 7: Commit**

```bash
git add docs/index.html
git commit -m "feat(indices): active l'onglet Indices (liste + détail par entreprise)"
```

---

## Post-implementation notes (not separate tasks, just context for the executor)

- Task 9's manual-verification step is the one genuinely uncertain part
  of this plan — yfinance's exact line-item labels can only be confirmed
  against the live API, which was blocked during planning by a local
  machine SSL/certificate issue unrelated to this project (documented in
  the spec). CI (GitHub Actions) does not have this problem.
- Once the pilot's manual verification (Task 9) and browser check (Task
  13) both pass, this branch (`indice`) is ready to merge into `main`
  the same way the previous UI work was — ask the user before merging,
  since that's what triggers the live GitHub Pages deploy.
