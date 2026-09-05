# Méthode de notation v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 2 weighted factors to the Indices CAC40 composite score — "Dynamique récente" (price-vs-MA200 trend + quarterly-results acceleration vs the 5-year trend) and "Actualité récente" (average sentiment of recent news, classified by Claude in the same call already used for summaries) — rebalancing the 5 existing factors' weights proportionally so the total stays 100%.

**Architecture:** Two new pure scoring functions (`score_dynamique_recente`, `score_actualite_recente`) follow the existing `score_*` pattern exactly. `summarize_news_item` (from the prior "actus enrichies" sub-project) changes its return type from `str` to a `dict` carrying both the summary and a sentiment classification, still in one API call — no new network cost. `fetch_company_financials` gains two new raw data points (price-vs-MA200 deviation, quarterly YoY revenue growth). `build_company_entry` reorders its steps so news (with sentiment) is fetched before the factors list is built, since the new "Actualité récente" factor depends on it.

**Tech Stack:** Same as the existing pipeline — Python, `yfinance`, `requests`, `pandas` (via yfinance), Anthropic Messages API (already wired). No new dependencies.

**Spec:** `specs/2026-09-05-methode-notation-v2-design.md`

## Global Constraints

- `WEIGHTS` must total exactly `1.0`: `rentabilite=0.24`, `structure_financiere=0.20`, `croissance=0.16`, `generation_cash=0.12`, `valorisation=0.08`, `dynamique_recente=0.10`, `actualite_recente=0.10`.
- `score_dynamique_recente` and `score_actualite_recente` must never raise — missing/insufficient data degrades to a neutral `0.0` score, matching every other `score_*` function's convention.
- `summarize_news_item`'s "never raise" contract is unchanged: any failure returns `{"summary": "", "sentiment": 0}` (previously `""`).
- `PRICE_MOMENTUM_SCALE = 20.0`, `QUARTERLY_ACCEL_SCALE = 10.0`, `NEWS_SENTIMENT_WINDOW_DAYS = 14` — exact constant names and values.
- No new pip dependency. No new front-end (`docs/index.html`) changes — `renderCompanyDetail` already renders `company.factors` generically by `name`/`weight`/`score`/`raw_value`.

---

## Task 1: Rebalance `WEIGHTS` and update the methodology doc

**Files:**
- Modify: `indices_score.py:32-38` (`WEIGHTS`)
- Modify: `Methodologie_Analyse_Indices.md` (weight headers §1-§5, new §6/§7)
- Modify: `tests/test_indices_score.py` (5 hardcoded weight assertions)

**Interfaces:**
- Produces: `WEIGHTS` dict with 7 keys (2 new: `dynamique_recente`, `actualite_recente`), consumed by Tasks 3 and 6.

- [ ] **Step 1: Update `WEIGHTS`**

In `indices_score.py`, replace:

```python
WEIGHTS = {
    "rentabilite": 0.30,
    "structure_financiere": 0.25,
    "croissance": 0.20,
    "generation_cash": 0.15,
    "valorisation": 0.10,
}
```

with:

```python
WEIGHTS = {
    "rentabilite": 0.24,
    "structure_financiere": 0.20,
    "croissance": 0.16,
    "generation_cash": 0.12,
    "valorisation": 0.08,
    "dynamique_recente": 0.10,
    "actualite_recente": 0.10,
}
```

- [ ] **Step 2: Update the 5 existing hardcoded weight assertions in `tests/test_indices_score.py`**

These 5 tests assert the OLD weight values directly (not via `WEIGHTS[...]`) and will fail once Step 1 lands. Update each:

In `test_score_rentabilite_above_cost_of_capital_is_positive` (around line 29): change `assert result.weight == 0.30` to `assert result.weight == 0.24`.

In `test_score_structure_financiere_comfortable_standard_profile` (around line 57): change `assert result.weight == 0.25` to `assert result.weight == 0.20`.

In `test_score_croissance_strong_aligned_growth` (around line 102): change `assert result.weight == 0.20` to `assert result.weight == 0.16`.

In `test_score_generation_cash_full_conversion` (around line 129): change `assert result.weight == 0.15` to `assert result.weight == 0.12`.

In `test_score_valorisation_trading_at_discount_is_positive` (around line 154): change `assert result.weight == 0.10` to `assert result.weight == 0.08`.

(`test_compute_composite_all_max_positive` and `test_interpret_bands` use their own self-contained fixture weights unrelated to the real `WEIGHTS` dict — do NOT touch those.)

- [ ] **Step 3: Run the affected tests to verify they still pass with the new values**

Run: `pytest tests/test_indices_score.py -k "rentabilite or structure_financiere or croissance or generation_cash or valorisation" -v`
Expected: PASS (weight assertions now match 0.24/0.20/0.16/0.12/0.08)

- [ ] **Step 4: Update the methodology doc's weight headers**

In `Methodologie_Analyse_Indices.md`, change each section header:
- `### 1. Rentabilité / création de valeur — poids 30%` → `### 1. Rentabilité / création de valeur — poids 24%`
- `### 2. Structure financière / solvabilité — poids 25%` → `### 2. Structure financière / solvabilité — poids 20%`
- `### 3. Croissance — poids 20%` → `### 3. Croissance — poids 16%`
- `### 4. Génération de cash — poids 15%` → `### 4. Génération de cash — poids 12%`
- `### 5. Valorisation relative — poids 10%` → `### 5. Valorisation relative — poids 8%`

- [ ] **Step 5: Add new §6 and §7 to the methodology doc**

In `Methodologie_Analyse_Indices.md`, immediately after the "### 5. Valorisation relative — poids 8%" section's last paragraph (which ends "...qui rendrait ce multiple difficile à justifier.") and before the `## Interprétation du score composite` heading, insert:

```markdown
### 6. Dynamique récente — poids 10%

- **Tendance du cours** : écart entre le cours actuel et sa moyenne
  mobile 200 jours (%), même logique déjà utilisée pour l'or dans
  gold_score.py — un cours durablement au-dessus de sa MM200 signale une
  tendance de marché haussière, en dessous une tendance baissière.
- **Accélération des résultats** : croissance du chiffre d'affaires du
  dernier trimestre publié par rapport au même trimestre l'an dernier,
  comparée au CAGR 5 ans déjà calculé (facteur Croissance) — un trimestre
  qui croît plus vite que la tendance de fond signale une accélération,
  plus lentement une décélération.
- Les deux sous-signaux sont mis à l'échelle indépendamment puis
  moyennés (unités différentes : un écart de cours en %, un écart de
  croissance en points). Si l'un des deux est indisponible (ex : moins
  de 5 trimestres publiés chez yfinance), le score ne porte que sur le
  sous-signal disponible ; si aucun n'est disponible, le facteur est
  neutre.

### 7. Actualité récente — poids 10%

- Moyenne du sentiment (favorable/neutre/défavorable pour l'entreprise)
  des actualités publiées dans les 14 derniers jours, tel que classé par
  Claude au moment de la génération du résumé de chaque actu (sous-projet
  "actus enrichies") — pas d'appel IA supplémentaire.
- Neutre par défaut si aucune actualité récente n'est disponible ou
  exploitable, plutôt qu'un biais optimiste ou pessimiste implicite.

> **Limite héritée (sous-projet actus enrichies).** Le sentiment est
> classé à partir du titre de l'actu (et du résumé "titre seul" déjà
> généré dans la majorité des cas) plutôt que du contenu réel de
> l'article — le scraping réel des liens Google News a été tenté puis
> abandonné (écran de consentement RGPD suivi d'une résolution d'URL
> côté JavaScript, cf. `specs/2026-09-05-actus-resume-ia-design.md`). La
> précision du signal de sentiment hérite donc de cette même limite,
> déjà actée.
```

- [ ] **Step 6: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (52/52 — no count change yet, this task only touches weights/docs)

- [ ] **Step 7: Commit**

```bash
git add indices_score.py Methodologie_Analyse_Indices.md tests/test_indices_score.py
git commit -m "feat(indices): rééquilibrer les poids pour les 2 nouveaux facteurs"
```

---

## Task 2: `extract_quarterly_growth` — croissance trimestrielle YoY

**Files:**
- Modify: `indices_score.py` (add function near `_cagr`/`extract_ratios`, after `extract_ratios`'s closing, i.e. after the current line ~386 `return {...}` block and before `def fetch_company_financials`)
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes: `get_row` (existing), `_is_missing` (existing).
- Produces: `extract_quarterly_growth(quarterly_financials) -> float | None`, used by Task 4.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_indices_score.py`, after the existing `extract_ratios`/`_cagr` test block (after the `test_extract_ratios_cagr_is_neutral_zero_when_whole_old_window_is_missing` test, before the `parse_news_rss` import section):

```python
from indices_score import extract_quarterly_growth


def test_extract_quarterly_growth_computes_yoy_growth_with_five_quarters():
    quarterly_financials = pd.DataFrame(
        {
            "2026-06-30": [110],
            "2026-03-31": [100],
            "2025-12-31": [95],
            "2025-09-30": [90],
            "2025-06-30": [100],  # même trimestre il y a un an (cols[4])
        },
        index=["Total Revenue"],
    )
    result = extract_quarterly_growth(quarterly_financials)
    assert result == 10.0  # (110/100 - 1) * 100


def test_extract_quarterly_growth_returns_none_with_fewer_than_five_quarters():
    quarterly_financials = pd.DataFrame(
        {
            "2026-06-30": [110],
            "2026-03-31": [100],
            "2025-12-31": [95],
            "2025-09-30": [90],
        },
        index=["Total Revenue"],
    )
    assert extract_quarterly_growth(quarterly_financials) is None


def test_extract_quarterly_growth_returns_none_when_year_ago_value_missing():
    quarterly_financials = pd.DataFrame(
        {
            "2026-06-30": [110],
            "2026-03-31": [100],
            "2025-12-31": [95],
            "2025-09-30": [90],
            "2025-06-30": [float("nan")],
        },
        index=["Total Revenue"],
    )
    assert extract_quarterly_growth(quarterly_financials) is None


def test_extract_quarterly_growth_returns_none_when_year_ago_value_is_zero_or_negative():
    quarterly_financials = pd.DataFrame(
        {
            "2026-06-30": [110],
            "2026-03-31": [100],
            "2025-12-31": [95],
            "2025-09-30": [90],
            "2025-06-30": [0.0],
        },
        index=["Total Revenue"],
    )
    assert extract_quarterly_growth(quarterly_financials) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k "extract_quarterly_growth" -v`
Expected: FAIL — `ImportError: cannot import name 'extract_quarterly_growth'`

- [ ] **Step 3: Implement**

In `indices_score.py`, add this function immediately after `extract_ratios`'s closing `return {...}` block (before `def fetch_company_financials`):

```python
def extract_quarterly_growth(quarterly_financials) -> float | None:
    """CA du dernier trimestre publié vs le même trimestre il y a un an
    (%). None si moins de 5 trimestres sont disponibles (yfinance
    n'expose généralement que les 4-5 derniers) ou si une des deux
    valeurs est manquante/NaN/nulle ou négative.

    Simplification assumée : `cols[4]` est traité comme "le même
    trimestre il y a un an" en supposant une cadence trimestrielle
    régulière sans trou. Si le calendrier fiscal d'une entreprise est
    irrégulier, `cols[4]` pourrait être un trimestre différent —
    dégradation silencieuse vers une comparaison légèrement inexacte,
    jugé acceptable pour un signal secondaire à 10% de poids.
    """
    cols = list(quarterly_financials.columns)
    if len(cols) < 5:
        return None
    revenue = get_row(quarterly_financials, "Total Revenue", "Operating Revenue")
    latest, year_ago = revenue[cols[0]], revenue[cols[4]]
    if _is_missing(latest) or _is_missing(year_ago) or year_ago <= 0:
        return None
    return (latest / year_ago - 1) * 100
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "extract_quarterly_growth" -v`
Expected: PASS (4/4)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (56/56)

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): extraire la croissance trimestrielle YoY (extract_quarterly_growth)"
```

---

## Task 3: `score_dynamique_recente` — facteur combiné cours + trimestriel

**Files:**
- Modify: `indices_score.py` (add function near the other `score_*` functions, e.g. after `score_valorisation`)
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes: `_clamp` (existing), `WEIGHTS["dynamique_recente"]` (Task 1), `FactorResult` (existing dataclass).
- Produces: `score_dynamique_recente(ecart_pct_ma200, quarterly_yoy_growth_ca, cagr_ca) -> FactorResult`, used by Task 7.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_indices_score.py`, after the `score_valorisation` test block (after `test_score_valorisation_at_historical_average_is_neutral`, before the `compute_composite`/`interpret` import):

```python
from indices_score import score_dynamique_recente


def test_score_dynamique_recente_averages_both_subsignals():
    # écart MM200 de +10% -> sous-score 5.0 (10/20*10) ; accélération de
    # +5pt (croissance trim 11% vs tendance 5 ans 6%) -> sous-score 5.0 (5/10*10)
    result = score_dynamique_recente(
        ecart_pct_ma200=10.0, quarterly_yoy_growth_ca=11.0, cagr_ca=6.0
    )
    assert result.name == "Dynamique récente"
    assert result.weight == 0.10
    assert result.score == 5.0


def test_score_dynamique_recente_uses_only_price_when_quarterly_unavailable():
    result = score_dynamique_recente(
        ecart_pct_ma200=10.0, quarterly_yoy_growth_ca=None, cagr_ca=6.0
    )
    assert result.score == 5.0


def test_score_dynamique_recente_uses_only_quarterly_when_price_unavailable():
    result = score_dynamique_recente(
        ecart_pct_ma200=None, quarterly_yoy_growth_ca=11.0, cagr_ca=6.0
    )
    assert result.score == 5.0


def test_score_dynamique_recente_neutral_when_no_subsignal_available():
    result = score_dynamique_recente(
        ecart_pct_ma200=None, quarterly_yoy_growth_ca=None, cagr_ca=6.0
    )
    assert result.score == 0.0
    assert result.raw_value == "Données insuffisantes"


def test_score_dynamique_recente_clamps_extreme_price_deviation():
    result = score_dynamique_recente(
        ecart_pct_ma200=100.0, quarterly_yoy_growth_ca=None, cagr_ca=0.0
    )
    assert result.score == 10.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k "score_dynamique_recente" -v`
Expected: FAIL — `ImportError: cannot import name 'score_dynamique_recente'`

- [ ] **Step 3: Implement**

In `indices_score.py`, `score_valorisation` currently ends at line 246, immediately followed by `def compute_composite(...)` at line 247. Insert this new code between them (after line 246, before line 247):

```python
PRICE_MOMENTUM_SCALE = 20.0    # % d'écart vs MM200 pour un score plein
QUARTERLY_ACCEL_SCALE = 10.0   # points d'écart de croissance pour un score plein


def score_dynamique_recente(
    ecart_pct_ma200: float | None,
    quarterly_yoy_growth_ca: float | None,
    cagr_ca: float,
) -> FactorResult:
    """Moyenne de 2 sous-signaux (chacun mis à l'échelle -10/+10
    indépendamment avant moyenne, car unités différentes) : tendance du
    cours vs MM200, et accélération du dernier trimestre publié vs la
    tendance 5 ans déjà calculée (cagr_ca). Si un sous-signal manque, la
    moyenne ne porte que sur celui disponible ; si aucun n'est
    disponible, score neutre 0.0."""
    sub_scores = []
    raw_parts = []
    if ecart_pct_ma200 is not None:
        sub_scores.append(_clamp(ecart_pct_ma200 / PRICE_MOMENTUM_SCALE * 10))
        raw_parts.append(f"Cours {ecart_pct_ma200:+.1f}% vs MM200")
    if quarterly_yoy_growth_ca is not None:
        acceleration = quarterly_yoy_growth_ca - cagr_ca
        sub_scores.append(_clamp(acceleration / QUARTERLY_ACCEL_SCALE * 10))
        raw_parts.append(
            f"CA dernier trim. {quarterly_yoy_growth_ca:+.1f}% vs an dernier "
            f"(tendance 5 ans {cagr_ca:+.1f}%)"
        )
    score = sum(sub_scores) / len(sub_scores) if sub_scores else 0.0
    raw_value = " — ".join(raw_parts) if raw_parts else "Données insuffisantes"
    return FactorResult("Dynamique récente", score, WEIGHTS["dynamique_recente"], raw_value)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "score_dynamique_recente" -v`
Expected: PASS (5/5)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (61/61)

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): facteur Dynamique récente (cours vs MM200 + accélération trimestrielle)"
```

---

## Task 4: Wire price/MA200 and quarterly growth into `fetch_company_financials`

**Files:**
- Modify: `indices_score.py:389-409` (`fetch_company_financials`)

**Interfaces:**
- Consumes: `extract_quarterly_growth` (Task 2).
- Produces: `fetch_company_financials(ticker) -> dict` now also includes `"ecart_pct_ma200": float | None` and `"quarterly_yoy_growth_ca": float | None` in its returned dict, consumed by Task 7.

No new automated test for this task: `fetch_company_financials` does live yfinance I/O and has never had a direct unit test in this codebase (every test that needs it monkeypatches the whole function) — consistent with existing precedent, verified instead by live post-merge inspection like the rest of the yfinance-integration code.

- [ ] **Step 1: Implement**

In `indices_score.py`, replace the body of `fetch_company_financials`:

```python
def fetch_company_financials(ticker: str) -> dict:
    if yf is None:
        raise RuntimeError("yfinance n'est pas installé (pip install yfinance)")
    t = yf.Ticker(ticker)
    financials = t.financials
    balance_sheet = t.balance_sheet
    cashflow = t.cashflow
    quarterly_financials = t.quarterly_financials
    info = t.info

    shares_outstanding = info.get("sharesOutstanding") or 0.0
    history = t.history(period="6y")["Close"]
    closes_by_year = {}
    for col in financials.columns:
        target_date = col.date() if hasattr(col, "date") else col
        window = history[history.index.date <= target_date] if hasattr(history.index, "date") else history
        if len(window):
            closes_by_year[col] = float(window.iloc[-1])

    current_price = float(history.iloc[-1]) if len(history) else None
    ma200 = float(history.tail(200).mean()) if len(history) else None
    ecart_pct_ma200 = (
        (current_price - ma200) / ma200 * 100
        if current_price is not None and ma200 else None
    )

    ratios = extract_ratios(financials, balance_sheet, cashflow, closes_by_year, shares_outstanding)
    ratios["sector"] = info.get("sector")
    ratios["ecart_pct_ma200"] = ecart_pct_ma200
    ratios["quarterly_yoy_growth_ca"] = extract_quarterly_growth(quarterly_financials)
    return ratios
```

- [ ] **Step 2: Run the full suite to confirm nothing else broke**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (61/61 — same count as after Task 3; this task adds no new tests, per the note above)

- [ ] **Step 3: Commit**

```bash
git add indices_score.py
git commit -m "feat(indices): récupérer cours/MM200 et croissance trimestrielle dans fetch_company_financials"
```

---

## Task 5: `summarize_news_item` renvoie un résumé + un sentiment

**Files:**
- Modify: `indices_score.py:471-523` (`summarize_news_item`)
- Modify: `tests/test_indices_score.py` (5 existing tests updated, 3 new tests added)

**Interfaces:**
- Produces: `summarize_news_item(title, company_name, article_text) -> dict` — was `-> str`. Returns `{"summary": str, "sentiment": int}` (`sentiment` always `-1`, `0`, or `1`), `{"summary": "", "sentiment": 0}` on any failure. Used by Task 7.

This is a breaking change to an existing, already-shipped function (from the "actus enrichies" sub-project) — the 5 existing tests below must be updated in place, not left as-is.

- [ ] **Step 1: Update the 5 existing tests and add 3 new ones**

In `tests/test_indices_score.py`, replace the entire block from `def test_summarize_news_item_returns_empty_when_api_key_missing` through `def test_summarize_news_item_returns_empty_on_malformed_response` (its full body) with:

```python
def test_summarize_news_item_returns_empty_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def fail_if_called(*a, **k):
        raise AssertionError("no network call expected without an API key")

    monkeypatch.setattr(indices_score.requests, "post", fail_if_called)
    assert summarize_news_item("Titre", "LVMH", "Texte de l'article") == {"summary": "", "sentiment": 0}


def test_summarize_news_item_uses_article_text_when_available(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["json"] = json
        return _FakeAnthropicResponse(
            {"content": [{"text": '{"summary": "Résumé généré.", "sentiment": 1}'}]}
        )

    monkeypatch.setattr(indices_score.requests, "post", fake_post)
    result = summarize_news_item("Titre", "LVMH", "Contenu réel de l'article")
    assert result == {"summary": "Résumé généré.", "sentiment": 1}
    assert "Contenu réel de l'article" in captured["json"]["messages"][0]["content"]
    assert captured["json"]["model"] == "claude-haiku-4-5-20251001"


def test_summarize_news_item_uses_headline_only_prompt_when_article_text_missing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["json"] = json
        return _FakeAnthropicResponse(
            {"content": [{"text": '{"summary": "Contexte prudent.", "sentiment": 0}'}]}
        )

    monkeypatch.setattr(indices_score.requests, "post", fake_post)
    result = summarize_news_item("Titre", "LVMH", None)
    assert result == {"summary": "Contexte prudent.", "sentiment": 0}
    assert "suggère" in captured["json"]["messages"][0]["content"]


def test_summarize_news_item_returns_empty_on_http_failure(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def fake_post(*a, **k):
        raise requests.RequestException("boom")

    monkeypatch.setattr(indices_score.requests, "post", fake_post)
    assert summarize_news_item("Titre", "LVMH", "texte") == {"summary": "", "sentiment": 0}


def test_summarize_news_item_returns_empty_on_malformed_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(
        indices_score.requests, "post",
        lambda *a, **k: _FakeAnthropicResponse({"unexpected": "shape"})
    )
    assert summarize_news_item("Titre", "LVMH", "texte") == {"summary": "", "sentiment": 0}


def test_summarize_news_item_strips_markdown_code_fences_before_parsing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(
        indices_score.requests, "post",
        lambda *a, **k: _FakeAnthropicResponse(
            {"content": [{"text": '```json\n{"summary": "Texte.", "sentiment": -1}\n```'}]}
        )
    )
    result = summarize_news_item("Titre", "LVMH", "texte")
    assert result == {"summary": "Texte.", "sentiment": -1}


def test_summarize_news_item_returns_empty_when_response_is_not_valid_json(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(
        indices_score.requests, "post",
        lambda *a, **k: _FakeAnthropicResponse({"content": [{"text": "Ceci n'est pas du JSON."}]})
    )
    assert summarize_news_item("Titre", "LVMH", "texte") == {"summary": "", "sentiment": 0}


def test_summarize_news_item_defaults_invalid_sentiment_value_to_zero(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(
        indices_score.requests, "post",
        lambda *a, **k: _FakeAnthropicResponse(
            {"content": [{"text": '{"summary": "Texte.", "sentiment": 5}'}]}
        )
    )
    result = summarize_news_item("Titre", "LVMH", "texte")
    assert result == {"summary": "Texte.", "sentiment": 0}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k "summarize_news_item" -v`
Expected: FAIL — the updated assertions expect a dict but the current implementation still returns a plain string.

- [ ] **Step 3: Implement**

In `indices_score.py`, replace `summarize_news_item`'s signature, docstring, and body:

```python
def summarize_news_item(title: str, company_name: str, article_text: str | None) -> dict:
    """Génère un résumé/contexte (1-2 phrases en français) et une
    classification de sentiment pour l'entreprise via l'API Anthropic, en
    un seul appel. Renvoie {"summary": "", "sentiment": 0} sur tout échec
    (clé API absente, erreur réseau, réponse HTTP non-200, JSON malformé)
    — ne lève jamais, même justification que fetch_article_text (dialogue
    avec un service tiers dont on ne peut pas énumérer précisément tous
    les modes d'échec)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"summary": "", "sentiment": 0}

    if article_text:
        prompt = (
            f"Voici un article de presse concernant l'entreprise {company_name}, "
            f"titré « {title} ».\n\nContenu de l'article :\n{article_text}\n\n"
            "En 1 à 2 phrases en français, résume le contexte et l'enjeu "
            "principal de cet article pour cette entreprise. Sois factuel et "
            "neutre, sans donner de conseil d'investissement."
        )
    else:
        prompt = (
            f"Voici uniquement le titre d'un article de presse concernant "
            f"l'entreprise {company_name} : « {title} ».\n\n"
            "Le contenu de l'article n'est pas disponible. En 1 phrase en "
            "français, propose un contexte prudent et hypothétique à partir "
            "de ce titre seul (formule-le comme une supposition, par exemple "
            "« Cet article suggère que... », sans jamais affirmer de faits "
            "que le titre seul ne permet pas de confirmer)."
        )

    prompt += (
        "\n\nRéponds uniquement avec un objet JSON valide, sans texte "
        "autour, de la forme : "
        '{"summary": "...", "sentiment": -1|0|1} '
        "où sentiment vaut -1 si l'actu est plutôt défavorable pour "
        "l'entreprise, 0 si neutre ou mixte, 1 si plutôt favorable."
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
                "model": ANTHROPIC_MODEL,
                "max_tokens": 150,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["content"][0]["text"].strip()
        if text.startswith("```"):
            text = text.strip("`")
            if "\n" in text:
                text = text.split("\n", 1)[1]
        parsed = json.loads(text)
        summary = str(parsed.get("summary", "")).strip()
        sentiment = parsed.get("sentiment", 0)
        if sentiment not in (-1, 0, 1):
            sentiment = 0
        return {"summary": summary, "sentiment": sentiment}
    except Exception:
        return {"summary": "", "sentiment": 0}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "summarize_news_item" -v`
Expected: PASS (8/8)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (64/64 — 61 from Task 3/4 minus the 5 old tests plus 8 new = 61-5+8=64)

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): summarize_news_item renvoie aussi une classification de sentiment"
```

---

## Task 6: `score_actualite_recente` — agrégation du sentiment récent

**Files:**
- Modify: `indices_score.py` (add function after `score_dynamique_recente`)
- Modify: `indices_score.py` import line (add `timedelta`)
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes: `_clamp` (existing), `WEIGHTS["actualite_recente"]` (Task 1), `FactorResult` (existing).
- Produces: `score_actualite_recente(news_items: list[dict]) -> FactorResult`, used by Task 7. Reads each item's `"date"` (`"%Y-%m-%d"` string) and `"sentiment"` (int, defaults to `0` via `.get` if absent).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_indices_score.py`, after the Task 3 (`score_dynamique_recente`) tests, before the `compute_composite`/`interpret` import block:

```python
from indices_score import score_actualite_recente
from datetime import timedelta


def _days_ago(n):
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


def test_score_actualite_recente_averages_recent_sentiments():
    news = [
        {"date": _days_ago(1), "sentiment": 1},
        {"date": _days_ago(2), "sentiment": 1},
        {"date": _days_ago(3), "sentiment": -1},
    ]
    result = score_actualite_recente(news)
    assert result.name == "Actualité récente"
    assert result.weight == 0.10
    # moyenne (1+1-1)/3 = 0.333... -> score 3.33...
    assert 3.0 < result.score < 3.5


def test_score_actualite_recente_ignores_old_news():
    news = [
        {"date": _days_ago(1), "sentiment": 1},
        {"date": _days_ago(30), "sentiment": -1},  # hors fenêtre de 14 jours
    ]
    result = score_actualite_recente(news)
    assert result.score == 10.0  # seule l'actu récente (sentiment 1) compte


def test_score_actualite_recente_neutral_when_no_news():
    result = score_actualite_recente([])
    assert result.score == 0.0
    assert result.raw_value == "Aucune actualité récente exploitable"


def test_score_actualite_recente_neutral_when_all_news_are_old():
    news = [{"date": _days_ago(30), "sentiment": 1}]
    result = score_actualite_recente(news)
    assert result.score == 0.0


def test_score_actualite_recente_handles_missing_sentiment_key_as_neutral():
    news = [{"date": _days_ago(1)}]  # pas de clé "sentiment"
    result = score_actualite_recente(news)
    assert result.score == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k "score_actualite_recente" -v`
Expected: FAIL — `ImportError: cannot import name 'score_actualite_recente'`

- [ ] **Step 3: Implement**

In `indices_score.py`, add `timedelta` to the existing `datetime` import at the top of the file:

```python
from datetime import datetime, timedelta
```

Then add this function after `score_dynamique_recente`:

```python
NEWS_SENTIMENT_WINDOW_DAYS = 14


def score_actualite_recente(news_items: list[dict]) -> FactorResult:
    """Moyenne du sentiment des actus datées de moins de 14 jours, mise à
    l'échelle -10/+10. Neutre (0.0) si aucune actu récente exploitable —
    ni erreur, ni biais optimiste/pessimiste par défaut."""
    cutoff = datetime.now() - timedelta(days=NEWS_SENTIMENT_WINDOW_DAYS)
    recent_sentiments = []
    for item in news_items:
        try:
            item_date = datetime.strptime(item["date"], "%Y-%m-%d")
        except (ValueError, TypeError, KeyError):
            continue
        if item_date >= cutoff:
            recent_sentiments.append(item.get("sentiment", 0))
    if not recent_sentiments:
        return FactorResult(
            "Actualité récente", 0.0, WEIGHTS["actualite_recente"],
            "Aucune actualité récente exploitable",
        )
    avg_sentiment = sum(recent_sentiments) / len(recent_sentiments)
    score = _clamp(avg_sentiment * 10)
    return FactorResult(
        "Actualité récente", score, WEIGHTS["actualite_recente"],
        f"Ton moyen des {len(recent_sentiments)} actualités récentes : {avg_sentiment:+.2f}",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "score_actualite_recente" -v`
Expected: PASS (5/5)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (69/69)

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): facteur Actualité récente (sentiment moyen des news)"
```

---

## Task 7: Brancher les 2 nouveaux facteurs dans `build_company_entry`

**Files:**
- Modify: `indices_score.py:433-441` (`fetch_news`)
- Modify: `indices_score.py:536-575` (`build_company_entry`)
- Modify: `tests/test_indices_score.py` (`_fake_ratios`, 2 `test_build_company_entry_*` tests, 1 `test_fetch_news_*` test)

**Interfaces:**
- Consumes: `score_dynamique_recente` (Task 3), `score_actualite_recente` (Task 6), `summarize_news_item` returning a dict (Task 5).
- Produces: `build_company_entry`'s returned `"factors"` list now has 7 entries (index 5 = "Dynamique récente", index 6 = "Actualité récente"). `fetch_news`'s returned items now carry `"sentiment"` in addition to `"summary"`.

- [ ] **Step 1: Update `fetch_news` to attach sentiment**

In `indices_score.py`, replace `fetch_news`'s body:

```python
def fetch_news(company_name: str) -> list[dict]:
    params = {"q": company_name, "hl": "fr", "gl": "FR", "ceid": "FR:fr"}
    resp = requests.get(NEWS_RSS_URL, params=params, timeout=15)
    resp.raise_for_status()
    items = parse_news_rss(resp.content)
    for item in items:
        article_text = fetch_article_text(item["link"])
        result = summarize_news_item(item["title"], company_name, article_text)
        item["summary"] = result["summary"]
        item["sentiment"] = result["sentiment"]
    return items
```

- [ ] **Step 2: Update `build_company_entry` — reorder news fetch before factors, add the 2 new factors**

In `indices_score.py`, replace `build_company_entry`'s body (the stale comment above the old `try: news = fetch_news(name)` block, which said news was "distincte du score fondamental", is removed here since that's no longer accurate — the "Actualité récente" factor now depends on it):

```python
def build_company_entry(ticker: str, name: str) -> dict:
    data = fetch_company_financials(ticker)
    sector = data["sector"]

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
        score_rentabilite(data["roce"], data["roe"], COST_OF_CAPITAL_PROXY),
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

- [ ] **Step 3: Update `_fake_ratios()` to include the 2 new keys**

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
        "sector": "Consumer Defensive",
    }
```

- [ ] **Step 4: Update `test_build_company_entry_degrades_gracefully_when_news_fetch_fails`**

In `tests/test_indices_score.py`, replace this test's body:

```python
def test_build_company_entry_degrades_gracefully_when_news_fetch_fails(monkeypatch):
    """Une panne du flux RSS (fetch_news) ne doit pas faire perdre le score
    déjà calculé pour l'entreprise — seule la liste de news doit être vide,
    et le facteur Actualité récente doit rester neutre plutôt que planter."""
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: _fake_ratios())

    def _raise_news(name):
        raise RuntimeError("flux RSS indisponible")

    monkeypatch.setattr(indices_score, "fetch_news", _raise_news)

    entry = indices_score.build_company_entry("BN.PA", "Danone")

    assert entry["news"] == []
    assert entry["ticker"] == "BN.PA"
    assert entry["name"] == "Danone"
    assert isinstance(entry["score"], float)
    assert len(entry["factors"]) == 7
    assert entry["factors"][6]["name"] == "Actualité récente"
    assert entry["factors"][6]["score"] == 0.0
```

- [ ] **Step 5: Update `test_build_company_entry_includes_news_when_fetch_succeeds`**

In `tests/test_indices_score.py`, replace this test's body:

```python
def test_build_company_entry_includes_news_when_fetch_succeeds(monkeypatch):
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: _fake_ratios())
    monkeypatch.setattr(
        indices_score, "fetch_news",
        lambda name: [
            {"title": "Titre", "date": "2026-09-04", "link": "https://example.com", "sentiment": 1}
        ],
    )

    entry = indices_score.build_company_entry("BN.PA", "Danone")

    assert entry["news"] == [
        {"title": "Titre", "date": "2026-09-04", "link": "https://example.com", "sentiment": 1}
    ]
    assert entry["factors"][5]["name"] == "Dynamique récente"
```

- [ ] **Step 6: Update `test_fetch_news_attaches_source_and_summary_and_isolates_per_item_failures`**

In `tests/test_indices_score.py`, this test currently mocks `summarize_news_item` to return a plain string — update the mock and assertions for the new dict return shape. Replace from `monkeypatch.setattr(\n        indices_score, "summarize_news_item",` through the end of the test:

```python
    monkeypatch.setattr(
        indices_score, "summarize_news_item",
        lambda title, name, text: {
            "summary": f"Résumé pour {title} (article={text})",
            "sentiment": 1 if text is None else -1,
        }
    )

    items = fetch_news("Test SA")

    assert len(items) == 2
    assert items[0]["source"] == "Source A"
    assert items[0]["summary"] == "Résumé pour Titre A (article=None)"
    assert items[0]["sentiment"] == 1
    assert items[1]["source"] == "Source B"
    assert items[1]["summary"] == "Résumé pour Titre B (article=Texte B)"
    assert items[1]["sentiment"] == -1
```

- [ ] **Step 7: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (69/69 — this task updates existing tests in place, adds none)

- [ ] **Step 8: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): brancher Dynamique récente et Actualité récente dans build_company_entry"
```

---

## Post-merge verification (not a task — informational)

Once this branch is merged and `indices.yml` runs for real: confirm in `docs/indices.json` that every company now has 7 factors (not 5), that "Dynamique récente" and "Actualité récente" show plausible non-degenerate values (not every company landing on exactly 0.0, which would suggest quarterly/price data or sentiment classification silently failing across the board — the same category of issue caught during the "actus enrichies" sub-project's post-merge check). Also spot-check that the composite scores moved by a sensible amount given the new factors' 20% combined weight, and that `interpret()`'s bands still produce reasonable labels.
