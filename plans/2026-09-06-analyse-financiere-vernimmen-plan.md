# Analyse financière par entreprise (Vernimmen) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Générer, pour chaque entreprise pilote, une analyse financière écrite (comptes annuels + trimestriels, façon Vernimmen) via Claude Opus 5, régénérée uniquement quand un nouveau trimestre est publié (pas quotidiennement), exposée sur une page dédiée accessible depuis sa fiche.

**Architecture:** `fetch_company_financials` expose un nouveau contexte textuel multi-années/trimestres et la date du dernier trimestre publié. `generate_financial_analysis` (Claude Opus 5, thinking adaptatif, effort élevé) transforme ce contexte en analyse structurée JSON `{"analysis_html": "..."}`. Avant d'écraser `docs/indices.json`, `main()` relit son propre contenu de la veille (`load_previous_company_analyses`) ; `build_company_entry` compare la date de trimestre courante à celle stockée hier et ne régénère que si elle diffère (ou est absente), sinon recopie l'analyse existante — 0 appel API la plupart des jours. Frontend : bouton + route dédiée par entreprise.

**Tech Stack:** Même pipeline existant — pure Python, `requests` déjà utilisé pour l'API Anthropic (`summarize_news_item`). Aucune nouvelle dépendance, aucun nouveau secret (réutilise `ANTHROPIC_API_KEY`).

**Spec:** `specs/2026-09-06-analyse-financiere-vernimmen-design.md`

## Global Constraints

- `generate_financial_analysis`, `build_financial_narrative_context`, `latest_quarter_date`, `load_previous_company_analyses` ne lèvent jamais d'exception (guard clauses / `try/except Exception`, jamais de propagation) — même contrat que le reste du fichier.
- Modèle exact : `ANTHROPIC_MODEL_ANALYSIS = "claude-opus-5"` (distinct de `ANTHROPIC_MODEL` déjà utilisé pour les actus, ne pas le modifier).
- `generate_financial_analysis` : `thinking: {"type": "adaptive"}`, `output_config: {"effort": "high"}`, `max_tokens: 8000`, `timeout: 120`.
- Format de sortie : uniquement `<h3>`, `<p>`, `<ul>`, `<li>`, `<strong>` — encodé dans le prompt système, pas vérifié/filtré côté code (confiance dans le modèle, même niveau que le reste du pipeline).
- Nouvelles clés top-level dans chaque entrée `docs/indices.json["companies"]` : `"financial_analysis_html": str | None`, `"financial_analysis_quarter": str | None`.
- `build_company_entry` gagne un 4e paramètre obligatoire `previous_analyses: dict`, résolu une seule fois dans `main()` (pas par entreprise), **avant** que `docs/indices.json` soit écrasé.
- Aucun nouveau secret CI — `ANTHROPIC_API_KEY` (déjà `secrets.CLAUDE_API_KEY` dans `indices.yml`) est réutilisé tel quel.

---

## Task 1: Contexte financier textuel + date du dernier trimestre

**Files:**
- Modify: `indices_score.py` (ajouter 2 fonctions avant `fetch_company_financials` ligne 491 ; modifier `fetch_company_financials` lignes 517-525)
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Produces: `build_financial_narrative_context(financials, balance_sheet, cashflow, quarterly_financials) -> str`, `latest_quarter_date(quarterly_financials) -> str | None`. `fetch_company_financials(...)`'s dict gagne `"financial_context": str` et `"latest_quarter_date": str | None`. Utilisées par la Tâche 2 (contexte) et la Tâche 3 (date, pour la comparaison carry-forward/régénération).

- [ ] **Step 1: Write the failing tests**

Ajouter à la fin de `tests/test_indices_score.py` :

```python
import pandas as pd


def _fake_annual_df(rows: dict, cols: list) -> pd.DataFrame:
    return pd.DataFrame(rows, index=cols).T


def test_build_financial_narrative_context_formats_years_and_quarters():
    cols = [pd.Timestamp("2025-12-31"), pd.Timestamp("2024-12-31")]
    financials = _fake_annual_df(
        {"Total Revenue": [1000.0, 900.0], "EBITDA": [200.0, 180.0],
         "EBIT": [150.0, 130.0], "Net Income": [90.0, 80.0]}, cols,
    )
    balance_sheet = _fake_annual_df(
        {"Stockholders Equity": [500.0, 450.0], "Total Debt": [300.0, 280.0],
         "Cash And Cash Equivalents": [50.0, 40.0]}, cols,
    )
    cashflow = _fake_annual_df(
        {"Operating Cash Flow": [180.0, 160.0], "Capital Expenditure": [-60.0, -55.0]}, cols,
    )
    q_cols = [pd.Timestamp("2025-09-30"), pd.Timestamp("2025-06-30")]
    quarterly_financials = _fake_annual_df({"Total Revenue": [260.0, 250.0]}, q_cols)

    result = indices_score.build_financial_narrative_context(
        financials, balance_sheet, cashflow, quarterly_financials
    )

    assert "Comptes annuels" in result
    assert "2025-12-31" in result
    assert "CA 1,000" in result
    assert "dette nette 250" in result  # 300 - 50
    assert "FCF 120" in result  # 180 + (-60)
    assert "Derniers trimestres publiés" in result
    assert "2025-09-30" in result


def test_build_financial_narrative_context_handles_missing_values():
    cols = [pd.Timestamp("2025-12-31")]
    financials = _fake_annual_df(
        {"Total Revenue": [1000.0], "EBITDA": [float("nan")],
         "EBIT": [150.0], "Net Income": [90.0]}, cols,
    )
    balance_sheet = _fake_annual_df(
        {"Stockholders Equity": [500.0], "Total Debt": [300.0],
         "Cash And Cash Equivalents": [50.0]}, cols,
    )
    cashflow = _fake_annual_df(
        {"Operating Cash Flow": [180.0], "Capital Expenditure": [-60.0]}, cols,
    )
    quarterly_financials = _fake_annual_df({"Total Revenue": [260.0]}, cols)

    result = indices_score.build_financial_narrative_context(
        financials, balance_sheet, cashflow, quarterly_financials
    )

    assert "EBITDA non disponible" in result


def test_latest_quarter_date_returns_iso_string():
    cols = [pd.Timestamp("2025-09-30"), pd.Timestamp("2025-06-30")]
    quarterly_financials = _fake_annual_df({"Total Revenue": [260.0, 250.0]}, cols)
    assert indices_score.latest_quarter_date(quarterly_financials) == "2025-09-30"


def test_latest_quarter_date_returns_none_when_no_columns():
    quarterly_financials = pd.DataFrame()
    assert indices_score.latest_quarter_date(quarterly_financials) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k "financial_narrative_context or latest_quarter_date" -v`
Expected: FAIL — `AttributeError: module 'indices_score' has no attribute 'build_financial_narrative_context'`

- [ ] **Step 3: Implement**

Dans `indices_score.py`, juste avant `def fetch_company_financials(ticker: str) -> dict:` (ligne 491), ajouter :

```python
def build_financial_narrative_context(
    financials, balance_sheet, cashflow, quarterly_financials,
) -> str:
    """Formate les séries annuelles (jusqu'à ~4 ans, le plus récent en
    premier) et les derniers trimestres en un texte structuré, destiné à
    être injecté dans le prompt Claude — pas de calcul ici, seulement de
    la mise en forme brute. Une ligne 'non disponible' remplace toute
    valeur manquante plutôt que de faire échouer le formatage."""
    revenue = get_row(financials, "Total Revenue", "Operating Revenue")
    ebitda = get_row(financials, "EBITDA", "Normalized EBITDA")
    ebit = get_row(financials, "EBIT", "Operating Income", "Total Operating Income As Reported")
    net_income = get_row(financials, "Net Income", "Net Income Common Stockholders")
    equity = get_row(balance_sheet, "Stockholders Equity", "Common Stock Equity")
    total_debt = get_row(balance_sheet, "Total Debt")
    cash = get_row(balance_sheet, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments")
    op_cash_flow = get_row(cashflow, "Operating Cash Flow")
    capex = get_row(cashflow, "Capital Expenditure")

    def _fmt(value) -> str:
        return "non disponible" if _is_missing(value) else f"{value:,.0f}"

    lines = ["Comptes annuels (le plus récent en premier) :"]
    for col in financials.columns:
        net_debt = (
            total_debt[col] - cash[col]
            if not _is_missing(total_debt[col]) and not _is_missing(cash[col]) else None
        )
        fcf = (
            op_cash_flow[col] + capex[col]
            if not _is_missing(op_cash_flow[col]) and not _is_missing(capex[col]) else None
        )
        lines.append(
            f"- {col.date() if hasattr(col, 'date') else col} : CA {_fmt(revenue[col])}, "
            f"EBITDA {_fmt(ebitda[col])}, EBIT {_fmt(ebit[col])}, "
            f"résultat net {_fmt(net_income[col])}, capitaux propres {_fmt(equity[col])}, "
            f"dette nette {_fmt(net_debt)}, FCF {_fmt(fcf)}"
        )

    quarterly_revenue = get_row(quarterly_financials, "Total Revenue", "Operating Revenue")
    lines.append("\nDerniers trimestres publiés (le plus récent en premier) :")
    for col in quarterly_financials.columns:
        lines.append(
            f"- {col.date() if hasattr(col, 'date') else col} : CA {_fmt(quarterly_revenue[col])}"
        )

    return "\n".join(lines)


def latest_quarter_date(quarterly_financials) -> str | None:
    """Date du trimestre le plus récent publié, format ISO (YYYY-MM-DD).
    None si aucune colonne (yfinance en panne pour ce ticker)."""
    cols = list(quarterly_financials.columns)
    if not cols:
        return None
    col = cols[0]
    return col.date().isoformat() if hasattr(col, "date") else str(col)
```

Puis, dans `fetch_company_financials`, remplacer :

```python
    ratios = extract_ratios(financials, balance_sheet, cashflow, closes_by_year, shares_outstanding)
    ratios["sector"] = info.get("sector")
    ratios["ecart_pct_ma200"] = ecart_pct_ma200
    ratios["quarterly_yoy_growth_ca"] = extract_quarterly_growth(quarterly_financials)
```

par :

```python
    ratios = extract_ratios(financials, balance_sheet, cashflow, closes_by_year, shares_outstanding)
    ratios["sector"] = info.get("sector")
    ratios["ecart_pct_ma200"] = ecart_pct_ma200
    ratios["quarterly_yoy_growth_ca"] = extract_quarterly_growth(quarterly_financials)
    ratios["financial_context"] = build_financial_narrative_context(
        financials, balance_sheet, cashflow, quarterly_financials
    )
    ratios["latest_quarter_date"] = latest_quarter_date(quarterly_financials)
```

(Les lignes suivantes de `fetch_company_financials` — `ratios["current_price"]`, `ratios["ma200"]`, etc. — restent inchangées.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "financial_narrative_context or latest_quarter_date" -v`
Expected: PASS (4/4)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (139/139 — 135 existants + 4 nouveaux)

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): contexte financier textuel multi-années/trimestres pour l'analyse Vernimmen"
```

---

## Task 2: Génération de l'analyse via Claude Opus 5

**Files:**
- Modify: `indices_score.py` (ajouter constante + fonction, juste après `summarize_news_item`, avant la ligne `OUTPUT_JSON_PATH = ...`)
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes: `ANTHROPIC_API_URL` (existant), rien d'autre de nouveau.
- Produces: `ANTHROPIC_MODEL_ANALYSIS = "claude-opus-5"`, `generate_financial_analysis(company_name, financial_context, ratios_summary) -> str | None`. Utilisée par la Tâche 3.

- [ ] **Step 1: Write the failing tests**

Ajouter à la fin de `tests/test_indices_score.py` :

```python
def test_generate_financial_analysis_returns_none_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def fail_if_called(*a, **k):
        raise AssertionError("no network call expected without an API key")

    monkeypatch.setattr(indices_score.requests, "post", fail_if_called)
    assert indices_score.generate_financial_analysis("Danone", "contexte", "ratios") is None


def test_generate_financial_analysis_extracts_text_block_after_thinking_block(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "content": [
                    {"type": "thinking", "thinking": ""},
                    {"type": "text", "text": '{"analysis_html": "<h3>Diagnostic</h3><p>Solide.</p>"}'},
                ]
            }

    monkeypatch.setattr(indices_score.requests, "post", lambda *a, **k: _FakeResponse())
    result = indices_score.generate_financial_analysis("Danone", "contexte", "ratios")
    assert result == "<h3>Diagnostic</h3><p>Solide.</p>"


def test_generate_financial_analysis_strips_code_fences(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "content": [
                    {"type": "text", "text": '```json\n{"analysis_html": "<p>OK</p>"}\n```'},
                ]
            }

    monkeypatch.setattr(indices_score.requests, "post", lambda *a, **k: _FakeResponse())
    assert indices_score.generate_financial_analysis("Danone", "contexte", "ratios") == "<p>OK</p>"


def test_generate_financial_analysis_returns_none_on_malformed_json(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"content": [{"type": "text", "text": "pas du json valide"}]}

    monkeypatch.setattr(indices_score.requests, "post", lambda *a, **k: _FakeResponse())
    assert indices_score.generate_financial_analysis("Danone", "contexte", "ratios") is None


def test_generate_financial_analysis_returns_none_on_request_exception(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def raise_error(*a, **k):
        raise requests.RequestException("boom")

    monkeypatch.setattr(indices_score.requests, "post", raise_error)
    assert indices_score.generate_financial_analysis("Danone", "contexte", "ratios") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k "generate_financial_analysis" -v`
Expected: FAIL — `AttributeError: module 'indices_score' has no attribute 'generate_financial_analysis'`

- [ ] **Step 3: Implement**

Dans `indices_score.py`, juste après la fin de `summarize_news_item` (avant `OUTPUT_JSON_PATH = ...`), ajouter :

```python
ANTHROPIC_MODEL_ANALYSIS = "claude-opus-5"

FINANCIAL_ANALYSIS_SYSTEM_PROMPT = """Tu es un analyste financier qui \
applique la méthode du Vernimmen (synthèse du diagnostic financier : \
rentabilité économique et financière, structure financière et \
solvabilité, analyse de la trésorerie et du free cash-flow, dynamique \
récente) à une entreprise cotée. Rédige une analyse structurée en \
français, factuelle, sans conseil d'investissement ni recommandation \
d'achat/vente, à partir des seules données fournies."""


def generate_financial_analysis(
    company_name: str, financial_context: str, ratios_summary: str,
) -> str | None:
    """Génère l'analyse financière via Claude Opus 5 (thinking adaptatif,
    effort élevé — tâche de raisonnement/rédaction, pas de classification
    simple). None si la clé API est absente ou en cas d'échec — jamais
    d'exception, même contrat que summarize_news_item."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    prompt = (
        f"Entreprise : {company_name}\n\n{financial_context}\n\n"
        f"Ratios déjà calculés (ne pas les recalculer, les interpréter) :\n"
        f"{ratios_summary}\n\n"
        "Rédige une analyse structurée avec ces sections, dans cet "
        "ordre : diagnostic global (2-3 phrases), structure financière "
        "et solvabilité, rentabilité économique et financière, analyse "
        "de la trésorerie et du free cash-flow, dynamique récente "
        "(dernier trimestre vs tendance), synthèse.\n\n"
        "Réponds uniquement avec un objet JSON valide, sans texte "
        'autour, de la forme : {"analysis_html": "..."} où la valeur '
        "est le texte de l'analyse en HTML, en utilisant uniquement "
        "les balises <h3>, <p>, <ul>, <li>, <strong> (aucune autre "
        "balise, aucun style inline, aucun script)."
    )

    try:
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": ANTHROPIC_MODEL_ANALYSIS,
                "max_tokens": 8000,
                "system": FINANCIAL_ANALYSIS_SYSTEM_PROMPT,
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "high"},
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        text_block = next(
            (b["text"] for b in data["content"] if b.get("type") == "text"), None
        )
        if text_block is None:
            return None
        text = text_block.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if "\n" in text:
                text = text.split("\n", 1)[1]
        parsed = json.loads(text)
        return parsed.get("analysis_html")
    except Exception:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "generate_financial_analysis" -v`
Expected: PASS (5/5)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (144/144 — 139 après Tâche 1 + 5 nouveaux)

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): génération de l'analyse financière via Claude Opus 5"
```

---

## Task 3: Carry-forward au trimestre inchangé, régénération sinon

**Files:**
- Modify: `indices_score.py` (ajouter 1 fonction avant `build_company_entry` ligne 1011 ; modifier `build_company_entry` et `main()`)
- Test: `tests/test_indices_score.py` (`_fake_ratios()`, 4 tests existants mis à jour, 5 nouveaux)

**Interfaces:**
- Consumes: `generate_financial_analysis(...)` (Tâche 2), `data["latest_quarter_date"]`/`data["financial_context"]` (Tâche 1).
- Produces: `load_previous_company_analyses() -> dict`. `build_company_entry(ticker, name, risk_free_rate, previous_analyses) -> dict` — gagne un 4e paramètre obligatoire ; le dict retourné gagne `"financial_analysis_html"` et `"financial_analysis_quarter"`.

- [ ] **Step 1: Update `_fake_ratios()`**

Dans `tests/test_indices_score.py`, `_fake_ratios()` gagne 2 clés :

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
        "financial_context": "Comptes annuels (le plus récent en premier) :\n- ...",
        "latest_quarter_date": "2026-06-30",
    }
```

- [ ] **Step 2: Update the 4 existing `test_build_company_entry_*` tests**

Chacun des 4 appels `indices_score.build_company_entry("BN.PA", "Danone", risk_free_rate=X)` devient
`indices_score.build_company_entry("BN.PA", "Danone", risk_free_rate=X, previous_analyses=_carried_forward_analysis())`
où `_carried_forward_analysis()` (nouvelle fonction utilitaire de test, définie juste avant ces 4 tests) simule un trimestre
identique à hier — pour qu'aucun de ces 4 tests existants ne déclenche un vrai appel à `generate_financial_analysis` :

```python
def _carried_forward_analysis():
    return {
        "BN.PA": {
            "financial_analysis_html": "<p>Analyse existante.</p>",
            "financial_analysis_quarter": "2026-06-30",
        }
    }
```

Dans `test_build_company_entry_degrades_gracefully_when_news_fetch_fails` :

```python
    entry = indices_score.build_company_entry(
        "BN.PA", "Danone", risk_free_rate=3.68, previous_analyses=_carried_forward_analysis(),
    )
```

(+ ajouter à la fin de ce test, après les assertions existantes) :

```python
    assert entry["financial_analysis_html"] == "<p>Analyse existante.</p>"
    assert entry["financial_analysis_quarter"] == "2026-06-30"
```

Dans `test_build_company_entry_includes_news_when_fetch_succeeds`,
`test_build_company_entry_falls_back_to_proxy_wacc_when_beta_missing`, et
`test_build_company_entry_falls_back_to_proxy_wacc_when_risk_free_rate_missing` : même changement d'appel
(`previous_analyses=_carried_forward_analysis()`), aucune autre assertion à ajouter dans ces 3-là.

- [ ] **Step 3: Write the 5 new tests**

Ajouter à la fin de `tests/test_indices_score.py` :

```python
def test_load_previous_company_analyses_returns_empty_dict_when_file_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(tmp_path / "does_not_exist.json"))
    assert indices_score.load_previous_company_analyses() == {}


def test_load_previous_company_analyses_returns_empty_dict_on_corrupted_json(monkeypatch, tmp_path):
    path = tmp_path / "corrupted.json"
    path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(path))
    assert indices_score.load_previous_company_analyses() == {}


def test_load_previous_company_analyses_indexes_by_ticker(monkeypatch, tmp_path):
    path = tmp_path / "indices.json"
    path.write_text(json.dumps({
        "companies": [
            {"ticker": "BN.PA", "financial_analysis_html": "<p>A</p>", "financial_analysis_quarter": "2026-06-30"},
            {"ticker": "MC.PA", "financial_analysis_html": "<p>B</p>", "financial_analysis_quarter": "2026-03-31"},
        ]
    }), encoding="utf-8")
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(path))
    result = indices_score.load_previous_company_analyses()
    assert result["BN.PA"] == {"financial_analysis_html": "<p>A</p>", "financial_analysis_quarter": "2026-06-30"}
    assert result["MC.PA"] == {"financial_analysis_html": "<p>B</p>", "financial_analysis_quarter": "2026-03-31"}


def test_build_company_entry_carries_forward_analysis_when_quarter_unchanged(monkeypatch):
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: _fake_ratios())
    monkeypatch.setattr(indices_score, "fetch_news", lambda name: [])

    def fail_if_called(*a, **k):
        raise AssertionError("generate_financial_analysis ne doit pas être appelée si le trimestre est inchangé")

    monkeypatch.setattr(indices_score, "generate_financial_analysis", fail_if_called)

    entry = indices_score.build_company_entry(
        "BN.PA", "Danone", risk_free_rate=3.68,
        previous_analyses={"BN.PA": {
            "financial_analysis_html": "<p>Analyse existante.</p>",
            "financial_analysis_quarter": "2026-06-30",  # identique à _fake_ratios()
        }},
    )

    assert entry["financial_analysis_html"] == "<p>Analyse existante.</p>"
    assert entry["financial_analysis_quarter"] == "2026-06-30"


def test_build_company_entry_regenerates_analysis_when_quarter_changed(monkeypatch):
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: _fake_ratios())
    monkeypatch.setattr(indices_score, "fetch_news", lambda name: [])
    monkeypatch.setattr(
        indices_score, "generate_financial_analysis",
        lambda company_name, financial_context, ratios_summary: "<p>Nouvelle analyse.</p>",
    )

    entry = indices_score.build_company_entry(
        "BN.PA", "Danone", risk_free_rate=3.68,
        previous_analyses={"BN.PA": {
            "financial_analysis_html": "<p>Ancienne analyse.</p>",
            "financial_analysis_quarter": "2026-03-31",  # différent de _fake_ratios() (2026-06-30)
        }},
    )

    assert entry["financial_analysis_html"] == "<p>Nouvelle analyse.</p>"
    assert entry["financial_analysis_quarter"] == "2026-06-30"
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k "build_company_entry or previous_company_analyses" -v`
Expected: FAIL — `TypeError: build_company_entry() missing 1 required positional argument: 'previous_analyses'` (et `AttributeError` pour `load_previous_company_analyses`)

- [ ] **Step 5: Implement**

Dans `indices_score.py`, juste avant `def build_company_entry(...)` (ligne 1011), ajouter :

```python
def load_previous_company_analyses() -> dict:
    """Lit le docs/indices.json du run précédent (déjà commité) pour en
    extraire, par ticker, l'analyse financière et la date de trimestre
    qu'elle couvre. {} si le fichier n'existe pas encore ou est
    illisible — jamais d'exception."""
    if not os.path.exists(OUTPUT_JSON_PATH):
        return {}
    try:
        with open(OUTPUT_JSON_PATH, encoding="utf-8") as fh:
            previous = json.load(fh)
        return {
            c["ticker"]: {
                "financial_analysis_html": c.get("financial_analysis_html"),
                "financial_analysis_quarter": c.get("financial_analysis_quarter"),
            }
            for c in previous.get("companies", [])
        }
    except Exception:
        return {}
```

Puis remplacer la signature et le corps de `build_company_entry` :

```python
def build_company_entry(
    ticker: str, name: str, risk_free_rate: float | None, previous_analyses: dict,
) -> dict:
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

    previous = previous_analyses.get(ticker, {})
    current_quarter = data["latest_quarter_date"]
    if (
        current_quarter is not None
        and current_quarter == previous.get("financial_analysis_quarter")
        and previous.get("financial_analysis_html")
    ):
        financial_analysis_html = previous["financial_analysis_html"]
        financial_analysis_quarter = previous["financial_analysis_quarter"]
    else:
        ratios_summary = (
            f"ROCE {data['roce']:.1f}%, ROE {data['roe']:.1f}%, "
            f"dette nette/EBITDA {data['net_debt_ebitda']:.1f}x, "
            f"ICR {data['icr']:.1f}x, CAGR CA {data['cagr_ca']:+.1f}%/an, "
            f"CAGR EBITDA {data['cagr_ebitda']:+.1f}%/an, "
            f"conversion FCF/EBITDA {data['fcf_conversion']:.0f}%, "
            f"coût du capital {cost_of_capital:.1f}%"
        )
        financial_analysis_html = generate_financial_analysis(
            name, data["financial_context"], ratios_summary
        )
        financial_analysis_quarter = current_quarter

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
        "financial_analysis_html": financial_analysis_html,
        "financial_analysis_quarter": financial_analysis_quarter,
    }
```

Enfin, dans `main()`, remplacer :

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

par :

```python
def main():
    risk_free_rate = fetch_risk_free_rate()
    previous_analyses = load_previous_company_analyses()
    companies = []
    for company in COMPANIES:
        try:
            companies.append(
                build_company_entry(
                    company["ticker"], company["name"], risk_free_rate, previous_analyses,
                )
            )
        except Exception as e:
            print(f"Erreur pour {company['ticker']} ({company['name']}) : {e}")
```

(`load_previous_company_analyses()` s'exécute avant l'écriture de
`docs/indices.json` plus bas dans `main()` — elle lit donc bien le
contenu de la veille, pas celui du run en cours.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "build_company_entry or previous_company_analyses" -v`
Expected: PASS (9/9 — 4 tests existants mis à jour + 5 nouveaux)

- [ ] **Step 7: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (149/149 — 144 après Tâche 2 + 5 nouveaux ; les 4 tests existants sont modifiés en place, pas ajoutés)

- [ ] **Step 8: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): carry-forward de l'analyse financière au trimestre inchangé, régénération sinon"
```

---

## Task 4: Bouton + page dédiée (frontend)

**Files:**
- Modify: `docs/index.html` (`renderCompanyDetail`, `parseRoute`, `renderRoute`, nouvelle fonction `renderCompanyAnalysis`)

**Interfaces:** Consomme `company.financial_analysis_html` (Tâche 3). Pas de suite de tests automatisés (même précédent que les autres changements frontend de ce projet) — vérification manuelle décrite au Step 4.

- [ ] **Step 1: Ajouter le bouton sur la fiche entreprise**

Dans `docs/index.html`, `renderCompanyDetail`, le bloc `.btn-row` actuel contient 2 boutons (`toggleCompanyActu`, `toggleCompanyDetail`). Ajouter un 3e bouton, qui navigue directement (pas un toggle repliable — une vraie navigation vers une autre route) :

```html
        <div class="btn-row">
          <button class="toggle-btn" id="toggleCompanyActu" type="button" aria-expanded="false">
            Actu <span class="chevron">▾</span>
          </button>
          <button class="toggle-btn" id="toggleCompanyDetail" type="button" aria-expanded="false">
            Détail du calcul <span class="chevron">▾</span>
          </button>
        </div>
        <div class="btn-row">
          <button class="toggle-btn" id="goToAnalysis" type="button">
            Analyse financière complète →
          </button>
        </div>
```

(Nouveau `<div class="btn-row">` séparé plutôt que d'ajouter le bouton
au premier, pour ne pas perturber la logique de fermeture mutuelle des
2 toggles existants, qui itère spécifiquement sur `toggles` — voir
Step 3.)

- [ ] **Step 2: Écouteur de clic pour la navigation**

Toujours dans `renderCompanyDetail`, juste après le bloc `toggles.forEach(...)` existant (qui attache les écouteurs des 2 boutons repliables), ajouter :

```javascript
  document.getElementById('goToAnalysis').addEventListener('click', () => {
    location.hash = '#indices/' + encodeURIComponent(company.ticker) + '/analyse';
  });
```

- [ ] **Step 3: Étendre `parseRoute()` pour la nouvelle sous-route**

Dans `docs/index.html`, `parseRoute()` contient actuellement :

```javascript
function parseRoute() {
  const hash = location.hash.slice(1);
  if (hash === 'or') return { screen: 'or' };
  if (hash === 'indices') return { screen: 'indices', ticker: null };
  if (hash.startsWith('indices/')) return { screen: 'indices', ticker: hash.slice('indices/'.length) };
  return { screen: 'home' };
}
```

Remplacer par :

```javascript
function parseRoute() {
  const hash = location.hash.slice(1);
  if (hash === 'or') return { screen: 'or' };
  if (hash === 'indices') return { screen: 'indices', ticker: null };
  if (hash.startsWith('indices/')) {
    const rest = hash.slice('indices/'.length);
    if (rest.endsWith('/analyse')) {
      return { screen: 'indices', ticker: decodeURIComponent(rest.slice(0, -'/analyse'.length)), view: 'analyse' };
    }
    return { screen: 'indices', ticker: decodeURIComponent(rest), view: 'detail' };
  }
  return { screen: 'home' };
}
```

- [ ] **Step 4: Ajouter `renderCompanyAnalysis` et brancher `loadIndices`**

Chercher la fonction `loadIndices` (qui appelle déjà `renderCompanyDetail` selon le `ticker` de la route) — sa signature exacte et son corps doivent être lus directement dans `docs/index.html` avant modification, car ils ne sont pas reproduits ici (fonction pré-existante non listée dans ce plan). Ajouter, juste après `renderCompanyDetail` :

```javascript
function renderCompanyAnalysis(company) {
  const html = company.financial_analysis_html;
  document.getElementById('indicesContent').innerHTML = `
    <a href="#indices/${encodeURIComponent(company.ticker)}" class="back-link">← ${company.name}</a>
    <h2>Analyse financière complète</h2>
    ${html || '<p class="hero-sub">Analyse non disponible pour le moment.</p>'}
  `;
}
```

Puis, dans `loadIndices` (ou l'équivalent qui décide d'appeler
`renderCompanyDetail` selon `route.ticker`), utiliser `route.view` pour
choisir entre les deux rendus : si `route.view === 'analyse'`, appeler
`renderCompanyAnalysis(company)` au lieu de `renderCompanyDetail(company)`
une fois `company` résolu (même recherche par ticker que l'existant).
L'implémenteur doit lire le corps actuel de la fonction qui fait ce
routage avant d'y ajouter cette branche, pour respecter exactement sa
structure existante (gestion d'erreur si le ticker est introuvable,
etc.) plutôt que de la deviner.

- [ ] **Step 5: Vérification manuelle locale**

Ouvrir `docs/index.html` dans un navigateur avec un `docs/indices.json`
local temporairement édité pour ajouter
`"financial_analysis_html": "<h3>Test</h3><p>Contenu de test.</p>"` sur
une entreprise, et confirmer : le bouton "Analyse financière complète →"
navigue bien vers la page dédiée, le contenu s'affiche, le lien retour
fonctionne. Tester aussi le cas `financial_analysis_html: null` (message
neutre, pas d'erreur JS). Remettre `docs/indices.json` inchangé après
vérification (ne pas committer un JSON de test).

- [ ] **Step 6: Commit**

```bash
git add docs/index.html
git commit -m "feat(indices): bouton et page dédiée pour l'analyse financière complète"
```

---

## Task 5: Documentation méthodologique

**Files:**
- Modify: `Methodologie_Analyse_Indices.md`

**Interfaces:** Aucune — tâche de documentation uniquement, aucun changement de code.

- [ ] **Step 1: Ajouter une nouvelle section, après "Historique de score & alertes"**

Ajouter dans `Methodologie_Analyse_Indices.md`, juste après la section
"Historique de score & alertes" (ajoutée par le plan précédent) et
avant "Hors périmètre (v1)" :

```markdown
## Analyse financière complète (Vernimmen)

Une page dédiée par entreprise (accessible par un bouton sur sa fiche)
propose une analyse financière écrite, structurée selon la synthèse du
diagnostic financier Vernimmen : diagnostic global, structure
financière et solvabilité, rentabilité économique et financière,
analyse de la trésorerie et du free cash-flow, dynamique récente
(dernier trimestre vs tendance), synthèse.

Générée par Claude Opus 5 à partir des comptes annuels (jusqu'à ~4 ans)
et des derniers trimestres publiés, ainsi que des ratios déjà calculés
par ailleurs (ROCE, ROE, dette nette/EBITDA, ICR, CAGR, conversion FCF,
coût du capital) — le modèle interprète, il ne recalcule pas ces
chiffres.

**Régénérée uniquement quand un nouveau trimestre est publié** (pas
quotidiennement) : le run compare la date du dernier trimestre connu à
celle stockée la veille et ne régénère que si elle a changé, sinon
recopie l'analyse existante. Ce choix est délibéré : les comptes ne
changent que quelques fois par an, contrairement au score composite qui
réagit chaque jour au cours et aux actus — régénérer quotidiennement
coûterait ~90x plus cher pour un résultat identique la plupart du
temps.
```

- [ ] **Step 2: Commit**

```bash
git add Methodologie_Analyse_Indices.md
git commit -m "docs(indices): documenter l'analyse financière Vernimmen et son rythme de régénération"
```

---

## Post-merge verification (not a task — informational)

Au premier run après merge : `previous_analyses` sera vide pour les 5
entreprises (première génération), donc 5 appels Opus 5 le même jour —
attendu, coût ponctuel. Confirmer dans `docs/indices.json` que
`financial_analysis_html` est non-null pour les 5 entreprises et
contient un texte structuré plausible (pas une erreur JSON échouée), et
que `financial_analysis_quarter` correspond bien à une date de
trimestre récente. Au run suivant (lendemain), confirmer dans les logs
CI qu'aucun nouvel appel Opus 5 n'est déclenché (le texte doit être
recopié à l'identique) — c'est le signal que le mécanisme de
carry-forward fonctionne réellement en production, pas seulement en
test.
