# Suivi des dividendes du portefeuille — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Donner à l'onglet Portefeuille → Actions → Analyse trois métriques de dividendes — cumul réellement encaissé, revenu projeté (annuel + mensuel moyen), rendement sur coût par ligne.

**Architecture:** `indices_score.py` persiste désormais l'historique complet des dividendes par entreprise (déjà récupéré via `fetch_dividend_history`/yfinance pour le critère Graham, jusqu'ici jeté après usage) dans un nouveau fichier public `docs/dividend_history.json`, sans downsampling ni fenêtre de rétention. Le navigateur combine ce fichier avec les positions (ouvertes + clôturées, stockage local) pour calculer les 3 métriques, entièrement côté client, et les affiche dans une nouvelle sous-section "Dividendes" de `portfolioAnalysisHtml`.

**Tech Stack:** Python 3 + yfinance (déjà en place), JavaScript vanilla testé via `node docs/portfolio.test.js` (pas de framework, pas de DOM).

**Spec:** `docs/superpowers/specs/2026-09-25-portfolio-dividend-tracking-design.md`

## Global Constraints

- Pas de conversion de devise, jamais — deux totaux séparés EUR/USD (cohérent avec `computePortfolioTotals`/`computePortfolioPerformanceCurves` déjà en place).
- `docs/dividend_history.json` : entrées `{date, ticker, amount}`, `amount` en devise de cotation native. **Pas de downsampling, pas de fenêtre de rétention** (contrairement à `docs/price_history.json`) — voir spec §3.3 : tronquer sous-compterait le cumul réellement encaissé d'une position ancienne.
- `update_dividend_history` déduplique par `(ticker, date)` — en cas de doublon, la valeur du dernier appel l'emporte. Dégrade toujours vers `[]` sur erreur (fichier illisible, `NaN` détecté via `allow_nan=False`), ne fait jamais échouer `main()`.
- Cumul reçu (`computeDividendsReceived`) = positions **ouvertes ET clôturées**. Revenu projeté (`computeProjectedDividendIncome`) et rendement sur coût (`computeYieldOnCost`) = positions **ouvertes uniquement**.
- Fenêtre TTM (trailing twelve months) = `]today - 365 jours, today]`, jamais d'extrapolation au-delà de ce qui est réellement dans l'historique.
- Une position non-payeuse (TTM = 0) est **exclue** du tableau par position de `computeYieldOnCost`, jamais affichée à 0%.
- Une position dont le ticker n'est pas dans `companiesByTicker` (retiré de l'indice suivi) est ignorée par toutes les fonctions de calcul — même convention que `computePortfolioTotals`/`computePortfolioAttribution`.
- Aucune donnée de position ne quitte jamais le navigateur — le calcul reste entièrement côté client, même contrainte que le reste du Portefeuille.
- Conventions de tests déjà établies dans ce dépôt : `tmp_path`/chemins explicites en Python (jamais un chemin par défaut de module dans un test), `node docs/portfolio.test.js` côté JS (aucun framework, aucun accès DOM). Aucun outillage navigateur/Node-DOM n'est disponible dans cet environnement d'exécution : la Task 4 (UI) n'a pas de vérification visuelle automatisée — relecture de code uniquement, à confirmer par l'utilisateur une fois le site redéployé.

---

## Task 1 : `indices_score.py` — persistance de `docs/dividend_history.json`

**Files:**
- Modify: `indices_score.py`
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes : rien (fonctions pures/isolées, testées avec des entrées synthétiques — pas encore branchées sur yfinance, c'est la Task 2).
- Produces : `DIVIDEND_HISTORY_PATH`, `load_dividend_history(path=DIVIDEND_HISTORY_PATH) -> list[dict]`, `update_dividend_history(new_entries: list[dict], path=DIVIDEND_HISTORY_PATH) -> list[dict]` — utilisées par la Task 2.

- [ ] **Step 1 : Écrire les tests de `load_dividend_history`**

Ajoute dans `tests/test_indices_score.py`, juste après `test_update_price_history_rounds_prices_and_writes_compact_json` (ligne ~3553, avant `test_last_confirmed_regime_returns_none_for_empty_history`) :

```python
def test_load_dividend_history_returns_empty_list_when_file_is_absent(tmp_path):
    assert indices_score.load_dividend_history(str(tmp_path / "absent.json")) == []


def test_load_dividend_history_degrades_to_empty_list_on_corrupt_json(tmp_path):
    path = tmp_path / "corrompu.json"
    path.write_text("pas du json", encoding="utf-8")
    assert indices_score.load_dividend_history(str(path)) == []
```

- [ ] **Step 2 : Vérifier que les tests échouent**

Run: `python -m pytest tests/test_indices_score.py -k test_load_dividend_history -v`
Expected: FAIL avec `AttributeError: module 'indices_score' has no attribute 'load_dividend_history'`

- [ ] **Step 3 : Implémenter `DIVIDEND_HISTORY_PATH` et `load_dividend_history`**

Ajoute dans `indices_score.py`, juste après le bloc `PRICE_HISTORY_PATH`/`PRICE_HISTORY_RECENT_DAYS`/`PRICE_HISTORY_RETENTION_DAYS` (ligne ~3262, avant `_downsample_price_entries`) :

```python
DIVIDEND_HISTORY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "docs", "dividend_history.json"
)


def load_dividend_history(path=DIVIDEND_HISTORY_PATH) -> list[dict]:
    """Meme contrat que load_price_history : [] si le fichier est absent
    ou corrompu, jamais d'exception."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError:
        return []
```

- [ ] **Step 4 : Vérifier que les tests passent**

Run: `python -m pytest tests/test_indices_score.py -k test_load_dividend_history -v`
Expected: PASS (2 tests)

- [ ] **Step 5 : Écrire les tests de `update_dividend_history`**

Ajoute juste après les deux tests précédents :

```python
def test_update_dividend_history_writes_new_entries_to_a_fresh_file(tmp_path):
    path = str(tmp_path / "dividend_history.json")
    entries = [{"date": "2026-06-15", "ticker": "MC.PA", "amount": 3.55}]
    result = indices_score.update_dividend_history(entries, path=path)
    assert result == entries
    assert indices_score.load_dividend_history(path) == entries


def test_update_dividend_history_accumulates_across_calls(tmp_path):
    path = str(tmp_path / "dividend_history.json")
    indices_score.update_dividend_history(
        [{"date": "2023-06-15", "ticker": "MC.PA", "amount": 3.0}], path=path)
    indices_score.update_dividend_history(
        [{"date": "2026-06-15", "ticker": "MC.PA", "amount": 3.55}], path=path)
    result = indices_score.load_dividend_history(path)
    dates = [e["date"] for e in result]
    assert "2023-06-15" in dates
    assert "2026-06-15" in dates
    assert len(result) == 2


def test_update_dividend_history_deduplicates_by_ticker_and_date_keeping_the_latest_call(tmp_path):
    path = str(tmp_path / "dividend_history.json")
    indices_score.update_dividend_history(
        [{"date": "2026-06-15", "ticker": "MC.PA", "amount": 3.40}], path=path)
    result = indices_score.update_dividend_history(
        [{"date": "2026-06-15", "ticker": "MC.PA", "amount": 3.55}], path=path)
    assert len(result) == 1
    assert result[0]["amount"] == 3.55


def test_update_dividend_history_keeps_tickers_independent(tmp_path):
    path = str(tmp_path / "dividend_history.json")
    entries = [
        {"date": "2026-06-15", "ticker": "MC.PA", "amount": 3.55},
        {"date": "2026-05-10", "ticker": "SAP.DE", "amount": 2.10},
    ]
    result = indices_score.update_dividend_history(entries, path=path)
    tickers = {e["ticker"] for e in result}
    assert tickers == {"MC.PA", "SAP.DE"}


def test_update_dividend_history_never_writes_nan(tmp_path):
    path = str(tmp_path / "dividend_history.json")
    entries = [{"date": "2026-06-15", "ticker": "MC.PA", "amount": float("nan")}]
    result = indices_score.update_dividend_history(entries, path=path)
    assert result == []
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    assert "NaN" not in content


def test_update_dividend_history_degrades_to_empty_list_on_unexpected_failure(tmp_path):
    path = str(tmp_path / "sous_dossier_impossible" / "dividend_history.json")
    fichier_bloquant = tmp_path / "sous_dossier_impossible"
    fichier_bloquant.write_text("x", encoding="utf-8")
    result = indices_score.update_dividend_history(
        [{"date": "2026-06-15", "ticker": "MC.PA", "amount": 3.55}], path=path)
    assert result == []


def test_update_dividend_history_rounds_amounts_and_writes_compact_json(tmp_path):
    path = str(tmp_path / "dividend_history.json")
    entries = [{"date": "2026-06-15", "ticker": "MC.PA", "amount": 3.554321987}]
    result = indices_score.update_dividend_history(entries, path=path)
    assert result[0]["amount"] == 3.5543
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    assert "\n  " not in content  # pas d'indentation
    assert "3.5543" in content
```

- [ ] **Step 6 : Vérifier que les tests échouent**

Run: `python -m pytest tests/test_indices_score.py -k test_update_dividend_history -v`
Expected: FAIL avec `AttributeError: module 'indices_score' has no attribute 'update_dividend_history'`

- [ ] **Step 7 : Implémenter `update_dividend_history`**

Ajoute juste après `load_dividend_history` :

```python
def update_dividend_history(new_entries: list[dict], path=DIVIDEND_HISTORY_PATH) -> list[dict]:
    """Ajoute `new_entries` ({date, ticker, amount}) a l'historique deja
    accumule, deduplique par (ticker, date) -- en cas de doublon, la
    derniere valeur de new_entries l'emporte (une re-recuperation yfinance
    plus recente est preferee a l'ancienne, meme motif que le reste du
    pipeline qui fait toujours confiance a la donnee la plus fraiche).
    Pas de downsampling ni de fenetre de retention (voir spec §3.3).
    Degrade toujours vers [] sur erreur, ne fait jamais echouer main()."""
    try:
        history = load_dividend_history(path)
        by_key = {(e["ticker"], e["date"]): e for e in history}
        for entry in new_entries:
            by_key[(entry["ticker"], entry["date"])] = entry
        merged = sorted(by_key.values(), key=lambda e: (e["ticker"], e["date"]))
        rounded = [{**entry, "amount": round(entry["amount"], 4)} for entry in merged]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rounded, fh, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        return rounded
    except Exception as e:
        print(f"Erreur historique de dividendes : {e}")
        return []
```

- [ ] **Step 8 : Vérifier que les tests passent**

Run: `python -m pytest tests/test_indices_score.py -k "dividend_history" -v`
Expected: PASS (9 tests au total, `load_dividend_history` + `update_dividend_history`)

- [ ] **Step 9 : Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "Ajoute la persistance de docs/dividend_history.json (indices_score.py)"
```

---

## Task 2 : `indices_score.py` — câblage dans `fetch_company_financials`/`build_company_entry`/`main()`

**Files:**
- Modify: `indices_score.py`
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes : `update_dividend_history` (Task 1), la variable locale `dividends` déjà calculée dans `fetch_company_financials` (ligne ~2909 : `dividends = fetch_dividend_history(t)`) — aucun nouvel appel réseau.
- Produces : `ratios["_dividend_history"]` (clé interne sur le retour de `fetch_company_financials`), `entry["_dividend_history"]` (clé interne sur le retour de `build_company_entry`), `docs/dividend_history.json` alimenté à chaque exécution de `main()` — consommés par la Task 4 (frontend).

**Note d'implémentation :** cette task modifie 3 fonctions existantes (`fetch_company_financials`, `build_company_entry`, `main`) et 2 fixtures de test partagées par des dizaines de tests déjà existants (`_fake_ratios()` ligne ~1821, `_fake_financial_ratios()` ligne ~5397). L'ordre des steps ci-dessous écrit d'abord les nouveaux tests, puis implémente les 4 changements de production ensemble (ils sont interdépendants), puis fait tourner **toute la suite** — pas seulement les nouveaux tests — avant de commit, pour s'assurer qu'aucun test existant n'a été cassé par le changement de fixture.

- [ ] **Step 1 : Écrire le test de `fetch_company_financials`**

Ajoute dans `tests/test_indices_score.py`, juste après `test_fetch_company_financials_exposes_the_full_price_history_for_persistence` (ligne ~6952, avant `test_load_signal_tracking_returns_empty_list_when_file_absent`) :

```python
def test_fetch_company_financials_exposes_the_full_dividend_history_for_persistence(monkeypatch):
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])
    history_index = pd.date_range("2024-01-01", periods=3, freq="D")
    history_close = pd.Series([100.0, 101.0, 102.0], index=history_index)
    fake_dividends = pd.Series(
        [3.40, 3.55],
        index=pd.DatetimeIndex(["2025-06-15", "2026-06-15"]),
    )

    class _FakeTicker:
        def __init__(self, ticker):
            self._ticker = ticker

        @property
        def financials(self):
            return financials

        @property
        def balance_sheet(self):
            return balance_sheet

        @property
        def cashflow(self):
            return cashflow

        @property
        def quarterly_financials(self):
            return quarterly

        @property
        def info(self):
            return {"sharesOutstanding": 1000.0, "beta": 0.9, "sector": "Basic Materials"}

        @property
        def dividends(self):
            return fake_dividends

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close})

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)

    result = indices_score.fetch_company_financials("MC.PA")

    assert "_dividend_history" in result
    entries = result["_dividend_history"]
    assert len(entries) == 2
    assert all(e["ticker"] == "MC.PA" for e in entries)
    assert all(set(e.keys()) == {"date", "ticker", "amount"} for e in entries)
    assert entries == sorted(entries, key=lambda e: e["date"])
    assert entries[0] == {"date": "2025-06-15", "ticker": "MC.PA", "amount": 3.40}
    assert entries[1] == {"date": "2026-06-15", "ticker": "MC.PA", "amount": 3.55}
```

- [ ] **Step 2 : Écrire le test de `build_company_entry`**

Ajoute juste après `test_build_company_entry_carries_the_price_history_through` (ligne ~5582, avant `test_financial_sector_tickers_are_in_companies`) :

```python
def test_build_company_entry_carries_the_dividend_history_through(monkeypatch):
    """Meme cablage que _price_history_daily (voir
    test_build_company_entry_carries_the_price_history_through) applique a
    _dividend_history : build_company_entry doit la faire remonter jusqu'a
    son dict de sortie, pour que main() puisse ensuite la retirer (.pop)
    avant d'ecrire docs/indices.json et la rediriger vers
    update_dividend_history (docs/dividend_history.json)."""
    fake_ratios = _fake_financial_ratios()
    fake_ratios["_dividend_history"] = [
        {"date": "2026-06-15", "ticker": "BNP.PA", "amount": 2.10},
    ]
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: fake_ratios)
    monkeypatch.setattr(indices_score, "fetch_news", lambda name, prev=None: [])
    monkeypatch.setattr(indices_score, "generate_financial_analysis", lambda *a, **k: "<p>Analyse.</p>")

    entry = indices_score.build_company_entry(
        "BNP.PA", "BNP Paribas", risk_free_rate=0.03, previous_analyses={}, index_key="CAC40")

    assert "_dividend_history" in entry
    assert entry["_dividend_history"] == fake_ratios["_dividend_history"]
    assert entry["ticker"] == "BNP.PA"
    assert entry["index"] == "CAC40"
```

- [ ] **Step 3 : Écrire les tests de `main()`**

Ajoute juste après `test_main_never_writes_the_internal_price_history_key_to_indices_json` (fin de la fonction, ligne ~7677) :

```python
def test_main_persists_dividend_history_from_companies(monkeypatch, tmp_path):
    """Preuve que main() recupere _dividend_history (propage par
    build_company_entry) et le transmet a update_dividend_history — sans
    ca, docs/dividend_history.json ne serait jamais alimente en
    production."""
    monkeypatch.setattr(indices_score, "COMPANIES", [
        {"ticker": "MC.PA", "name": "LVMH", "index": "CAC40"},
    ])
    monkeypatch.setattr(indices_score, "fetch_risk_free_rate", lambda series_id: 3.68)
    monkeypatch.setattr(indices_score, "fetch_fx_rate_to_usd", lambda currency: 1.0)
    monkeypatch.setattr(indices_score, "load_previous_company_analyses", lambda: {})
    monkeypatch.setattr(
        indices_score, "build_company_entry",
        lambda ticker, name, risk_free_rate, previous_analyses, index_key="CAC40", also_indices=None, fx_rate_to_usd=1.0: {
            "ticker": ticker, "name": name, "index": index_key,
            "score": 10.0, "interpretation": "Neutre",
            "current_price": 50.0, "entry_price": 50.0,
            "_price_history_daily": [],
            "_dividend_history": [
                {"date": "2026-06-15", "ticker": ticker, "amount": 1.5},
            ],
        },
    )
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: [])
    monkeypatch.setattr(indices_score, "append_indices_history", lambda entries: entries)
    monkeypatch.setattr(indices_score, "update_signal_tracking", lambda companies, newly_triggered_entree: [])
    monkeypatch.setattr(indices_score, "update_nikkei_hangseng_price_history", lambda companies: [])
    monkeypatch.setattr(indices_score, "fetch_index_prices", lambda: {"CAC40": None, "DAX": None, "NASDAQ": None, "DOW": None})
    monkeypatch.setattr(indices_score, "update_price_history", lambda entries, **kwargs: entries)
    monkeypatch.setattr(indices_score, "fetch_index_price_history", lambda: [])
    output_path = tmp_path / "indices.json"
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(output_path))

    captured = {}
    def _fake_update_dividend_history(entries, **kwargs):
        captured["entries"] = entries
        return entries
    monkeypatch.setattr(indices_score, "update_dividend_history", _fake_update_dividend_history)

    indices_score.main()

    assert captured["entries"] == [{"date": "2026-06-15", "ticker": "MC.PA", "amount": 1.5}]


def test_main_never_writes_the_internal_dividend_history_key_to_indices_json(monkeypatch, tmp_path):
    """La cle interne _dividend_history (voir build_company_entry) ne doit
    jamais atteindre docs/indices.json publie — elle est retiree (.pop)
    dans main() avant construction du payload public, redirigee
    exclusivement vers update_dividend_history (docs/dividend_history.json)."""
    monkeypatch.setattr(indices_score, "COMPANIES", [
        {"ticker": "MC.PA", "name": "LVMH", "index": "CAC40"},
    ])
    monkeypatch.setattr(indices_score, "fetch_risk_free_rate", lambda series_id: 3.68)
    monkeypatch.setattr(indices_score, "fetch_fx_rate_to_usd", lambda currency: 1.0)
    monkeypatch.setattr(indices_score, "load_previous_company_analyses", lambda: {})
    monkeypatch.setattr(
        indices_score, "build_company_entry",
        lambda ticker, name, risk_free_rate, previous_analyses, index_key="CAC40", also_indices=None, fx_rate_to_usd=1.0: {
            "ticker": ticker, "name": name, "index": index_key,
            "score": 10.0, "interpretation": "Neutre",
            "current_price": 50.0, "entry_price": 50.0,
            "_price_history_daily": [],
            "_dividend_history": [
                {"date": "2026-06-15", "ticker": ticker, "amount": 1.5},
            ],
        },
    )
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: [])
    monkeypatch.setattr(indices_score, "append_indices_history", lambda entries: entries)
    monkeypatch.setattr(indices_score, "update_signal_tracking", lambda companies, newly_triggered_entree: [])
    monkeypatch.setattr(indices_score, "update_nikkei_hangseng_price_history", lambda companies: [])
    monkeypatch.setattr(indices_score, "fetch_index_prices", lambda: {"CAC40": None, "DAX": None, "NASDAQ": None, "DOW": None})
    monkeypatch.setattr(indices_score, "update_price_history", lambda entries, **kwargs: entries)
    monkeypatch.setattr(indices_score, "fetch_index_price_history", lambda: [])
    monkeypatch.setattr(indices_score, "update_dividend_history", lambda entries, **kwargs: entries)
    output_path = tmp_path / "indices.json"
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(output_path))

    indices_score.main()

    with open(indices_score.OUTPUT_JSON_PATH, encoding="utf-8") as fh:
        payload = json.load(fh)
    for company in payload["companies"]:
        assert "_dividend_history" not in company
```

- [ ] **Step 4 : Vérifier que les 4 nouveaux tests échouent**

Run: `python -m pytest tests/test_indices_score.py -k "dividend_history_for_persistence or carries_the_dividend_history or persists_dividend_history or never_writes_the_internal_dividend" -v`
Expected: FAIL (4 tests — `_dividend_history` n'existe encore nulle part en production)

- [ ] **Step 5 : Câbler `fetch_company_financials`**

Dans `indices_score.py`, juste après la ligne existante (~2962) :
```python
    ratios["_price_history_daily"] = [
        {"date": idx.strftime("%Y-%m-%d"), "ticker": ticker, "price": float(val)}
        for idx, val in history.items()
    ]
```
ajoute :
```python
    ratios["_dividend_history"] = [
        {"date": idx.strftime("%Y-%m-%d"), "ticker": ticker, "amount": float(val)}
        for idx, val in dividends.items()
    ]
```
(`dividends` est la variable déjà assignée ligne ~2909 : `dividends = fetch_dividend_history(t)`.)

- [ ] **Step 6 : Câbler `build_company_entry`**

Juste après la ligne existante (~4881) :
```python
    entry["_price_history_daily"] = data["_price_history_daily"]
```
ajoute :
```python
    entry["_dividend_history"] = data["_dividend_history"]
```

- [ ] **Step 7 : Mettre à jour les fixtures de test partagées**

Ces deux fixtures construisent un `ratios` complet consommé par `build_company_entry` dans des dizaines de tests déjà existants — sans cet ajout, le Step 6 ci-dessus casse tout test qui les utilise avec une `KeyError: '_dividend_history'`.

Dans `tests/test_indices_score.py`, fonction `_fake_ratios()` (ligne ~1821), juste après la ligne existante `"_price_history_daily": [],` (ligne ~1854) :
```python
        "_dividend_history": [],
```

Dans `_fake_financial_ratios()` (ligne ~5397), même ajout juste après la ligne existante `"_price_history_daily": [],` (ligne ~5412) :
```python
        "_dividend_history": [],
```
(`_fake_trust_ratios()`, ligne ~5505, appelle `_fake_financial_ratios()` en interne — elle hérite automatiquement de cet ajout, aucune modification nécessaire.)

- [ ] **Step 8 : Câbler `main()`**

Dans `indices_score.py`, juste après le bloc existant (~5205-5209) :
```python
    price_history_entries = []
    for c in companies:
        price_history_entries.extend(c.pop("_price_history_daily", []))
    price_history_entries.extend(fetch_index_price_history())
    update_price_history(price_history_entries)
```
ajoute :
```python
    dividend_history_entries = []
    for c in companies:
        dividend_history_entries.extend(c.pop("_dividend_history", []))
    update_dividend_history(dividend_history_entries)
```

- [ ] **Step 9 : Vérifier que les 4 nouveaux tests passent**

Run: `python -m pytest tests/test_indices_score.py -k "dividend_history_for_persistence or carries_the_dividend_history or persists_dividend_history or never_writes_the_internal_dividend" -v`
Expected: PASS (4 tests)

- [ ] **Step 10 : Faire tourner toute la suite Python**

Run: `python -m pytest tests/test_indices_score.py -v`
Expected: PASS intégral (aucune régression sur les tests existants qui utilisent `_fake_ratios()`/`_fake_financial_ratios()`/`_fake_trust_ratios()` via `build_company_entry`)

- [ ] **Step 11 : Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "Cable la persistance de docs/dividend_history.json dans fetch_company_financials/build_company_entry/main()"
```

---

## Task 3 : `docs/portfolio.js` — fonctions de calcul pures + tests

**Files:**
- Modify: `docs/portfolio.js`
- Test: `docs/portfolio.test.js`

**Interfaces:**
- Consumes : `isPositionActiveOn(position, date)` (déjà existant, ligne ~204) — position active à une date donnée. `docs/dividend_history.json` (produit par la Task 1/2), forme `{date, ticker, amount}[]`.
- Produces : `groupDividendHistoryByTicker(dividendHistory) -> {ticker: entries[]}`, `computeDividendsReceived(positions, dividendHistoryByTicker, companiesByTicker, currencyByIndex) -> {EUR: number, USD: number}`, `computeProjectedDividendIncome(openPositions, dividendHistoryByTicker, companiesByTicker, currencyByIndex, today?) -> {EUR: {annual, monthly}, USD: {annual, monthly}}`, `computeYieldOnCost(openPositions, dividendHistoryByTicker, companiesByTicker, today?) -> [{ticker, ttmPerShare, yieldOnCost, projectedAnnual}]` — consommées par la Task 4 (UI).

- [ ] **Step 1 : Écrire le test de `groupDividendHistoryByTicker`**

Ajoute dans `docs/portfolio.test.js`, juste après `test_group_price_history_by_ticker_sorts_each_group_by_date` (ligne ~418, avant `test_price_at_or_before_returns_the_latest_entry_not_after_the_date`) :

```js
function test_group_dividend_history_by_ticker_sorts_each_group_by_date() {
  const history = [
    { date: '2026-06-15', ticker: 'MC.PA', amount: 3.55 },
    { date: '2025-06-15', ticker: 'MC.PA', amount: 3.40 },
    { date: '2026-03-01', ticker: 'AAPL', amount: 0.25 },
  ];
  const grouped = groupDividendHistoryByTicker(history);
  assert.deepStrictEqual(grouped['MC.PA'].map(e => e.date), ['2025-06-15', '2026-06-15']);
  assert.strictEqual(grouped['AAPL'].length, 1);
  console.log('OK: test_group_dividend_history_by_ticker_sorts_each_group_by_date');
}
```

- [ ] **Step 2 : Vérifier que le test échoue**

Run: `node docs/portfolio.test.js`
Expected: FAIL avec `groupDividendHistoryByTicker is not defined` (le require en haut de fichier échouera d'abord — voir Step 17 pour l'ajout au require/exports ; en attendant, appelle temporairement la fonction depuis le corps du fichier de test pour valider ce step isolément si besoin, ou passe directement au Step 3 puis relance)

- [ ] **Step 3 : Implémenter `groupDividendHistoryByTicker`**

Ajoute dans `docs/portfolio.js`, juste après `closePosition` (ligne ~170, avant `groupPriceHistoryByTicker`) :

```js
/**
 * Regroupe docs/dividend_history.json ({date, ticker, amount}) par
 * ticker, trie chaque groupe par date croissante. Meme forme que
 * groupPriceHistoryByTicker.
 */
function groupDividendHistoryByTicker(dividendHistory) {
  const byTicker = {};
  dividendHistory.forEach(entry => {
    (byTicker[entry.ticker] = byTicker[entry.ticker] || []).push(entry);
  });
  Object.values(byTicker).forEach(entries => entries.sort((a, b) => (a.date < b.date ? -1 : 1)));
  return byTicker;
}
```

- [ ] **Step 4 : Écrire les tests de `computeDividendsReceived`**

Ajoute à la fin de `docs/portfolio.test.js` juste avant `function main() {` (ligne ~517) :

```js
function test_compute_dividends_received_only_counts_payments_within_holding_period() {
  const positions = [{ id: '1', ticker: 'MC.PA', buy_date: '2026-01-15', quantity: 10 }];
  const dividendHistoryByTicker = {
    'MC.PA': [
      { date: '2025-12-01', ticker: 'MC.PA', amount: 3.0 },  // avant l'achat, exclu
      { date: '2026-06-15', ticker: 'MC.PA', amount: 3.55 }, // apres l'achat, inclus
    ],
  };
  const companiesByTicker = { 'MC.PA': { index: 'CAC40' } };
  const currencyByIndex = { CAC40: 'EUR' };
  const received = computeDividendsReceived(positions, dividendHistoryByTicker, companiesByTicker, currencyByIndex);
  assert.ok(Math.abs(received.EUR - 35.5) < 0.001); // 3.55 * 10
  assert.strictEqual(received.USD, 0);
  console.log('OK: test_compute_dividends_received_only_counts_payments_within_holding_period');
}

function test_compute_dividends_received_excludes_payment_after_a_closed_position_was_sold() {
  const closedPositions = [{ id: '1', ticker: 'MC.PA', buy_date: '2026-01-15', sell_date: '2026-03-01', quantity: 10 }];
  const dividendHistoryByTicker = {
    'MC.PA': [
      { date: '2026-02-01', ticker: 'MC.PA', amount: 3.0 },  // pendant la detention, inclus
      { date: '2026-06-15', ticker: 'MC.PA', amount: 3.55 }, // apres la vente, exclu
    ],
  };
  const companiesByTicker = { 'MC.PA': { index: 'CAC40' } };
  const currencyByIndex = { CAC40: 'EUR' };
  const received = computeDividendsReceived(closedPositions, dividendHistoryByTicker, companiesByTicker, currencyByIndex);
  assert.ok(Math.abs(received.EUR - 30.0) < 0.001); // 3.0 * 10
  console.log('OK: test_compute_dividends_received_excludes_payment_after_a_closed_position_was_sold');
}

function test_compute_dividends_received_splits_by_currency() {
  const positions = [
    { id: '1', ticker: 'MC.PA', buy_date: '2026-01-01', quantity: 10 },
    { id: '2', ticker: 'AAPL', buy_date: '2026-01-01', quantity: 5 },
  ];
  const dividendHistoryByTicker = {
    'MC.PA': [{ date: '2026-06-15', ticker: 'MC.PA', amount: 3.0 }],
    'AAPL': [{ date: '2026-06-15', ticker: 'AAPL', amount: 0.25 }],
  };
  const companiesByTicker = { 'MC.PA': { index: 'CAC40' }, 'AAPL': { index: 'NASDAQ' } };
  const currencyByIndex = { CAC40: 'EUR', NASDAQ: 'USD' };
  const received = computeDividendsReceived(positions, dividendHistoryByTicker, companiesByTicker, currencyByIndex);
  assert.ok(Math.abs(received.EUR - 30.0) < 0.001);
  assert.ok(Math.abs(received.USD - 1.25) < 0.001);
  console.log('OK: test_compute_dividends_received_splits_by_currency');
}

function test_compute_dividends_received_skips_position_with_unknown_ticker() {
  const positions = [{ id: '1', ticker: 'RETIRE.PA', buy_date: '2026-01-01', quantity: 10 }];
  const dividendHistoryByTicker = { 'RETIRE.PA': [{ date: '2026-06-15', ticker: 'RETIRE.PA', amount: 3.0 }] };
  const received = computeDividendsReceived(positions, dividendHistoryByTicker, {}, {});
  assert.deepStrictEqual(received, { EUR: 0, USD: 0 });
  console.log('OK: test_compute_dividends_received_skips_position_with_unknown_ticker');
}
```

- [ ] **Step 5 : Vérifier que les tests échouent**

Run: `node docs/portfolio.test.js`
Expected: FAIL — `computeDividendsReceived` n'existe pas encore

- [ ] **Step 6 : Implémenter `computeDividendsReceived`**

Ajoute dans `docs/portfolio.js`, juste après `groupDividendHistoryByTicker` :

```js
/**
 * Cumul de dividendes reellement encaisses, positions OUVERTES et
 * CLOTUREES (un paiement recu pendant la detention reste un
 * encaissement reel meme si la position est cloturee depuis). Un
 * paiement compte pour une position si isPositionActiveOn(position,
 * paiement.date). Agrege par devise ({ EUR: montant, USD: montant }),
 * jamais converti/melange (meme convention que computePortfolioTotals).
 * Une position dont le ticker n'est pas dans companiesByTicker (retiree
 * de l'indice suivi) est ignoree.
 */
function computeDividendsReceived(positions, dividendHistoryByTicker, companiesByTicker, currencyByIndex) {
  const totals = { EUR: 0, USD: 0 };
  positions.forEach(position => {
    const company = companiesByTicker[position.ticker];
    if (!company) return;
    const currency = currencyByIndex[company.index] === 'USD' ? 'USD' : 'EUR';
    const payments = dividendHistoryByTicker[position.ticker] || [];
    payments.forEach(payment => {
      if (!isPositionActiveOn(position, payment.date)) return;
      totals[currency] += payment.amount * position.quantity;
    });
  });
  return totals;
}
```

- [ ] **Step 7 : Vérifier que les tests passent**

Run: `node docs/portfolio.test.js`
Expected: les 4 tests `computeDividendsReceived` passent (les tests plus loin dans le fichier échoueront encore — fonctions pas encore définies, c'est attendu à ce stade)

- [ ] **Step 8 : Écrire les tests de `computeProjectedDividendIncome`**

Ajoute juste après les 4 tests précédents :

```js
function test_compute_projected_dividend_income_uses_a_365_day_ttm_window() {
  const openPositions = [{ id: '1', ticker: 'MC.PA', buy_date: '2020-01-01', quantity: 10, buy_price: 90.0 }];
  const dividendHistoryByTicker = {
    'MC.PA': [
      { date: '2025-01-01', ticker: 'MC.PA', amount: 3.0 },  // > 365 jours avant today, exclu
      { date: '2026-06-15', ticker: 'MC.PA', amount: 3.55 }, // dans la fenetre, inclus
    ],
  };
  const companiesByTicker = { 'MC.PA': { index: 'CAC40' } };
  const currencyByIndex = { CAC40: 'EUR' };
  const income = computeProjectedDividendIncome(
    openPositions, dividendHistoryByTicker, companiesByTicker, currencyByIndex, '2026-09-21');
  assert.ok(Math.abs(income.EUR.annual - 35.5) < 0.001); // 3.55 * 10
  console.log('OK: test_compute_projected_dividend_income_uses_a_365_day_ttm_window');
}

function test_compute_projected_dividend_income_monthly_is_annual_over_twelve() {
  const openPositions = [{ id: '1', ticker: 'MC.PA', buy_date: '2020-01-01', quantity: 10, buy_price: 90.0 }];
  const dividendHistoryByTicker = { 'MC.PA': [{ date: '2026-06-15', ticker: 'MC.PA', amount: 12.0 }] };
  const companiesByTicker = { 'MC.PA': { index: 'CAC40' } };
  const currencyByIndex = { CAC40: 'EUR' };
  const income = computeProjectedDividendIncome(
    openPositions, dividendHistoryByTicker, companiesByTicker, currencyByIndex, '2026-09-21');
  assert.ok(Math.abs(income.EUR.monthly - income.EUR.annual / 12) < 0.0001);
  assert.ok(Math.abs(income.EUR.annual - 120.0) < 0.001); // 12.0 * 10
  console.log('OK: test_compute_projected_dividend_income_monthly_is_annual_over_twelve');
}

function test_compute_projected_dividend_income_ignores_ticker_with_no_dividend_history() {
  const openPositions = [{ id: '1', ticker: 'GROWTH.PA', buy_date: '2020-01-01', quantity: 10, buy_price: 50.0 }];
  const companiesByTicker = { 'GROWTH.PA': { index: 'CAC40' } };
  const currencyByIndex = { CAC40: 'EUR' };
  const income = computeProjectedDividendIncome(openPositions, {}, companiesByTicker, currencyByIndex, '2026-09-21');
  assert.deepStrictEqual(income, { EUR: { annual: 0, monthly: 0 }, USD: { annual: 0, monthly: 0 } });
  console.log('OK: test_compute_projected_dividend_income_ignores_ticker_with_no_dividend_history');
}
```

- [ ] **Step 9 : Vérifier que les tests échouent**

Run: `node docs/portfolio.test.js`
Expected: FAIL — `computeProjectedDividendIncome` n'existe pas encore

- [ ] **Step 10 : Implémenter `_dateMinusDays` et `computeProjectedDividendIncome`**

Ajoute dans `docs/portfolio.js`, juste après `computeDividendsReceived` :

```js
/**
 * `dateStr` ('YYYY-MM-DD') moins `days` jours, au format 'YYYY-MM-DD'.
 * Utilise Date en UTC pour eviter tout decalage de fuseau horaire.
 */
function _dateMinusDays(dateStr, days) {
  const d = new Date(dateStr + 'T00:00:00Z');
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

/**
 * Revenu projete : pour chaque position OUVERTE, somme des paiements par
 * action des 365 derniers jours (TTM, trailing twelve months, fenetre
 * ]today - 365 jours, today]) x quantite detenue. Agrege par devise.
 * `today` est injectable (tests), 'YYYY-MM-DD', vaut la date du jour par
 * defaut. Retourne { EUR: { annual, monthly }, USD: { annual, monthly } }
 * -- monthly = annual / 12 (moyenne, pas un calendrier reel).
 */
function computeProjectedDividendIncome(openPositions, dividendHistoryByTicker, companiesByTicker, currencyByIndex, today) {
  today = today || new Date().toISOString().slice(0, 10);
  const cutoff = _dateMinusDays(today, 365);
  const annual = { EUR: 0, USD: 0 };
  openPositions.forEach(position => {
    const company = companiesByTicker[position.ticker];
    if (!company) return;
    const currency = currencyByIndex[company.index] === 'USD' ? 'USD' : 'EUR';
    const payments = dividendHistoryByTicker[position.ticker] || [];
    const ttmPerShare = payments
      .filter(p => p.date > cutoff && p.date <= today)
      .reduce((sum, p) => sum + p.amount, 0);
    annual[currency] += ttmPerShare * position.quantity;
  });
  return {
    EUR: { annual: annual.EUR, monthly: annual.EUR / 12 },
    USD: { annual: annual.USD, monthly: annual.USD / 12 },
  };
}
```

- [ ] **Step 11 : Vérifier que les tests passent**

Run: `node docs/portfolio.test.js`
Expected: les 3 tests `computeProjectedDividendIncome` passent

- [ ] **Step 12 : Écrire les tests de `computeYieldOnCost`**

Ajoute juste après les 3 tests précédents :

```js
function test_compute_yield_on_cost_computes_ttm_dividend_over_buy_price() {
  const openPositions = [{ id: '1', ticker: 'MC.PA', buy_date: '2020-01-01', quantity: 10, buy_price: 100.0 }];
  const dividendHistoryByTicker = { 'MC.PA': [{ date: '2026-06-15', ticker: 'MC.PA', amount: 5.0 }] };
  const companiesByTicker = { 'MC.PA': { index: 'CAC40' } };
  const result = computeYieldOnCost(openPositions, dividendHistoryByTicker, companiesByTicker, '2026-09-21');
  assert.strictEqual(result.length, 1);
  assert.strictEqual(result[0].ticker, 'MC.PA');
  assert.ok(Math.abs(result[0].ttmPerShare - 5.0) < 0.001);
  assert.ok(Math.abs(result[0].yieldOnCost - 5.0) < 0.001); // 5.0 / 100.0 * 100
  assert.ok(Math.abs(result[0].projectedAnnual - 50.0) < 0.001); // 5.0 * 10
  console.log('OK: test_compute_yield_on_cost_computes_ttm_dividend_over_buy_price');
}

function test_compute_yield_on_cost_excludes_positions_with_no_ttm_dividend() {
  const openPositions = [{ id: '1', ticker: 'GROWTH.PA', buy_date: '2020-01-01', quantity: 10, buy_price: 50.0 }];
  const companiesByTicker = { 'GROWTH.PA': { index: 'CAC40' } };
  const result = computeYieldOnCost(openPositions, {}, companiesByTicker, '2026-09-21');
  assert.deepStrictEqual(result, []);
  console.log('OK: test_compute_yield_on_cost_excludes_positions_with_no_ttm_dividend');
}

function test_compute_yield_on_cost_skips_position_with_unknown_ticker() {
  const openPositions = [{ id: '1', ticker: 'RETIRE.PA', buy_date: '2020-01-01', quantity: 10, buy_price: 50.0 }];
  const dividendHistoryByTicker = { 'RETIRE.PA': [{ date: '2026-06-15', ticker: 'RETIRE.PA', amount: 5.0 }] };
  const result = computeYieldOnCost(openPositions, dividendHistoryByTicker, {}, '2026-09-21');
  assert.deepStrictEqual(result, []);
  console.log('OK: test_compute_yield_on_cost_skips_position_with_unknown_ticker');
}
```

- [ ] **Step 13 : Vérifier que les tests échouent**

Run: `node docs/portfolio.test.js`
Expected: FAIL — `computeYieldOnCost` n'existe pas encore

- [ ] **Step 14 : Implémenter `computeYieldOnCost`**

Ajoute dans `docs/portfolio.js`, juste après `computeProjectedDividendIncome` :

```js
/**
 * Rendement sur cout par position OUVERTE : dividende TTM par action /
 * buy_price * 100. Ne retourne que les positions dont le TTM > 0 (les
 * non-payeuses sont exclues, pas affichees a 0% -- evite le bruit dans
 * la liste). `today` injectable (tests), meme fenetre TTM que
 * computeProjectedDividendIncome. Une position dont le ticker n'est pas
 * dans companiesByTicker (retiree de l'indice suivi) est ignoree, meme
 * convention que les autres fonctions de ce fichier. Tableau
 * [{ ticker, ttmPerShare, yieldOnCost, projectedAnnual }, ...],
 * projectedAnnual = ttmPerShare * quantity (revenu annuel projete de
 * cette ligne).
 */
function computeYieldOnCost(openPositions, dividendHistoryByTicker, companiesByTicker, today) {
  today = today || new Date().toISOString().slice(0, 10);
  const cutoff = _dateMinusDays(today, 365);
  const results = [];
  openPositions.forEach(position => {
    if (!companiesByTicker[position.ticker]) return;
    const payments = dividendHistoryByTicker[position.ticker] || [];
    const ttmPerShare = payments
      .filter(p => p.date > cutoff && p.date <= today)
      .reduce((sum, p) => sum + p.amount, 0);
    if (ttmPerShare <= 0) return;
    results.push({
      ticker: position.ticker,
      ttmPerShare,
      yieldOnCost: (ttmPerShare / position.buy_price) * 100,
      projectedAnnual: ttmPerShare * position.quantity,
    });
  });
  return results;
}
```

- [ ] **Step 15 : Vérifier que les tests passent**

Run: `node docs/portfolio.test.js`
Expected: les 3 tests `computeYieldOnCost` passent

- [ ] **Step 16 : Enregistrer les nouveaux tests dans `main()`**

Dans `docs/portfolio.test.js`, ajoute les 11 nouveaux appels dans `function main()` (ligne ~517), juste après `test_group_price_history_by_ticker_sorts_each_group_by_date();` (ligne ~559) et avant `test_price_at_or_before_returns_the_latest_entry_not_after_the_date();` :

```js
  test_group_dividend_history_by_ticker_sorts_each_group_by_date();
```

puis à la toute fin, juste avant `console.log('Tous les tests portfolio.test.js sont passés.');` (ligne ~569) :

```js
  test_compute_dividends_received_only_counts_payments_within_holding_period();
  test_compute_dividends_received_excludes_payment_after_a_closed_position_was_sold();
  test_compute_dividends_received_splits_by_currency();
  test_compute_dividends_received_skips_position_with_unknown_ticker();
  test_compute_projected_dividend_income_uses_a_365_day_ttm_window();
  test_compute_projected_dividend_income_monthly_is_annual_over_twelve();
  test_compute_projected_dividend_income_ignores_ticker_with_no_dividend_history();
  test_compute_yield_on_cost_computes_ttm_dividend_over_buy_price();
  test_compute_yield_on_cost_excludes_positions_with_no_ttm_dividend();
  test_compute_yield_on_cost_skips_position_with_unknown_ticker();
```

- [ ] **Step 17 : Exporter les nouvelles fonctions**

Dans `docs/portfolio.js`, le bloc `module.exports` (ligne ~433) :
```js
    groupPriceHistoryByTicker, priceAtOrBefore, isPositionActiveOn, realValueForPosition,
    benchmarkValueForPosition, computePerformanceCurve, computePortfolioPerformanceCurves,
    computeBenchmarkPerformanceCurve,
  };
```
devient :
```js
    groupPriceHistoryByTicker, priceAtOrBefore, isPositionActiveOn, realValueForPosition,
    benchmarkValueForPosition, computePerformanceCurve, computePortfolioPerformanceCurves,
    computeBenchmarkPerformanceCurve,
    groupDividendHistoryByTicker, computeDividendsReceived, computeProjectedDividendIncome,
    computeYieldOnCost,
  };
```

Dans `docs/portfolio.test.js`, le `require` en haut de fichier (ligne ~6-15) :
```js
  benchmarkValueForPosition, computePerformanceCurve, computePortfolioPerformanceCurves,
  computeBenchmarkPerformanceCurve,
} = require('./portfolio.js');
```
devient :
```js
  benchmarkValueForPosition, computePerformanceCurve, computePortfolioPerformanceCurves,
  computeBenchmarkPerformanceCurve,
  groupDividendHistoryByTicker, computeDividendsReceived, computeProjectedDividendIncome,
  computeYieldOnCost,
} = require('./portfolio.js');
```

- [ ] **Step 18 : Faire tourner toute la suite JS**

Run: `node docs/portfolio.test.js`
Expected: `Tous les tests portfolio.test.js sont passés.` (aucune régression, tous les nouveaux tests inclus)

- [ ] **Step 19 : Commit**

```bash
git add docs/portfolio.js docs/portfolio.test.js
git commit -m "Ajoute les fonctions de calcul des dividendes du portefeuille (docs/portfolio.js)"
```

---

## Task 4 : `docs/index.html` — sous-section "Dividendes" dans l'Analyse Portefeuille

**Files:**
- Modify: `docs/index.html`

**Interfaces:**
- Consumes : `groupDividendHistoryByTicker`, `computeDividendsReceived`, `computeProjectedDividendIncome`, `computeYieldOnCost` (Task 3), `docs/dividend_history.json` (Task 1/2, servi statiquement comme `docs/price_history.json`).
- Produces : rendu HTML uniquement, rien consommé par une task suivante (dernière task du plan).

- [ ] **Step 1 : Ajouter la variable globale et le fetch de `dividend_history.json`**

Dans `docs/index.html`, juste après `let priceHistoryData = null;` (ligne ~3227) :
```js
let dividendHistoryData = null;
```

Dans `loadPortfolioScreen()`, juste après le bloc existant (~1587-1594) :
```js
  if (!priceHistoryData) {
    try {
      const res = await fetch('price_history.json?t=' + Date.now());
      priceHistoryData = res.ok ? await res.json() : [];
    } catch (e) {
      priceHistoryData = [];
    }
  }
```
ajoute :
```js
  if (!dividendHistoryData) {
    try {
      const res = await fetch('dividend_history.json?t=' + Date.now());
      dividendHistoryData = res.ok ? await res.json() : [];
    } catch (e) {
      dividendHistoryData = [];
    }
  }
```

- [ ] **Step 2 : Calculer `dividendHistoryByTicker` et le transmettre à `portfolioAnalysisHtml`**

Dans `renderPortfolioActionsTab` (ligne ~1885), juste après la ligne existante (~1889) :
```js
  const priceHistoryByTicker = groupPriceHistoryByTicker(priceHistoryData || []);
```
ajoute :
```js
  const dividendHistoryByTicker = groupDividendHistoryByTicker(dividendHistoryData || []);
```

Puis modifie l'appel existant (ligne ~1914) :
```js
      html: portfolioAnalysisHtml(positions, companiesByTicker, currencyByIndex, priceHistoryByTicker),
```
en :
```js
      html: portfolioAnalysisHtml(positions, companiesByTicker, currencyByIndex, priceHistoryByTicker, dividendHistoryByTicker),
```

- [ ] **Step 3 : Étendre la signature de `portfolioAnalysisHtml` et calculer les 3 métriques**

La signature existante (ligne ~1821) :
```js
function portfolioAnalysisHtml(positions, companiesByTicker, currencyByIndex, priceHistoryByTicker) {
  const closedPositions = loadClosedPortfolio();
```
devient :
```js
function portfolioAnalysisHtml(positions, companiesByTicker, currencyByIndex, priceHistoryByTicker, dividendHistoryByTicker) {
  const closedPositions = loadClosedPortfolio();
```

Puis, juste après la ligne existante `const attribution = computePortfolioAttribution(positions, companiesByTicker);` (ligne ~1828), ajoute :
```js
  const dividendsReceived = computeDividendsReceived(
    [...positions, ...closedPositions], dividendHistoryByTicker, companiesByTicker, currencyByIndex);
  const projectedIncome = computeProjectedDividendIncome(
    positions, dividendHistoryByTicker, companiesByTicker, currencyByIndex);
  const yieldOnCostRows = computeYieldOnCost(positions, dividendHistoryByTicker, companiesByTicker);
```

- [ ] **Step 4 : Construire le HTML de la sous-section "Dividendes"**

Juste avant le `return` final de `portfolioAnalysisHtml` (avant la ligne `return \`` existante, ~ligne 1862), ajoute :
```js
  const dividendCurrencies = ['EUR', 'USD'].filter(
    currency => dividendsReceived[currency] > 0 || projectedIncome[currency].annual > 0);
  const dividendSummaryHtml = dividendCurrencies.length ? dividendCurrencies.map(currency => `
    <div class="signal-stats">
      <div class="price-row"><span class="price-label">Cumul reçu (${currency})</span><span class="price-value">${dividendsReceived[currency].toLocaleString('fr-FR', {maximumFractionDigits:0})} ${CURRENCY_SYMBOL[currency] || currency}</span></div>
      <div class="price-row"><span class="price-label">Revenu annuel projeté (${currency})</span><span class="price-value">${projectedIncome[currency].annual.toLocaleString('fr-FR', {maximumFractionDigits:0})} ${CURRENCY_SYMBOL[currency] || currency}</span></div>
      <div class="price-row"><span class="price-label">Revenu mensuel moyen projeté (${currency})</span><span class="price-value">${projectedIncome[currency].monthly.toLocaleString('fr-FR', {maximumFractionDigits:0})} ${CURRENCY_SYMBOL[currency] || currency}</span></div>
    </div>`).join('') : '<div class="list-empty">Aucun dividende encaissé ni position versant un dividende.</div>';
  const dividendRowsHtml = yieldOnCostRows.length ? yieldOnCostRows.map(row => {
    const company = companiesByTicker[row.ticker];
    const currency = company ? (currencyByIndex[company.index] === 'USD' ? 'USD' : 'EUR') : 'EUR';
    const symbol = CURRENCY_SYMBOL[currency] || currency;
    return `
    <div class="price-row">
      <span class="price-label">${company ? company.name : row.ticker}</span>
      <span class="price-value">${row.ttmPerShare.toLocaleString('fr-FR', {maximumFractionDigits:2})} ${symbol}/action — ${formatPct(row.yieldOnCost)} sur coût — ${row.projectedAnnual.toLocaleString('fr-FR', {maximumFractionDigits:0})} ${symbol}/an</span>
    </div>`;
  }).join('') : '<div class="list-empty">Aucune position ne verse actuellement de dividende.</div>';
```

- [ ] **Step 5 : Insérer la sous-section dans le template retourné**

Dans le `return` de `portfolioAnalysisHtml` (ligne ~1862-1879), juste après le dernier bloc existant :
```js
    <h3 class="portfolio-analysis-title">Attribution de performance</h3>
    <div class="signal-stats">${attributionHtml}</div>
  `;
}
```
devient :
```js
    <h3 class="portfolio-analysis-title">Attribution de performance</h3>
    <div class="signal-stats">${attributionHtml}</div>
    <h3 class="portfolio-analysis-title">Dividendes</h3>
    ${dividendSummaryHtml}
    <div class="signal-stats">${dividendRowsHtml}</div>
  `;
}
```

- [ ] **Step 6 : Vérifier la non-régression des tests JS existants**

Run: `node docs/portfolio.test.js`
Expected: `Tous les tests portfolio.test.js sont passés.` (`docs/index.html` n'a pas de test automatisé — cette étape vérifie seulement que la Task 3, dont dépend ce câblage, n'a pas régressé)

- [ ] **Step 7 : Relecture manuelle du diff**

Relis `git diff docs/index.html` en entier : vérifie que `dividendHistoryByTicker` est bien passé à chaque appel de `portfolioAnalysisHtml`, qu'aucune des 4 fonctions importées (`groupDividendHistoryByTicker`, `computeDividendsReceived`, `computeProjectedDividendIncome`, `computeYieldOnCost` — déjà globales via `<script src="portfolio.js">`, pas de nouvel import à ajouter dans `index.html`) n'est mal orthographiée, et que le HTML généré reste bien formé (balises `<div>` équilibrées).

**Limite connue, à disclosurer à l'utilisateur** : aucun outillage navigateur/Node-DOM n'est disponible dans cet environnement d'exécution — ce step est une relecture de code, pas un test visuel réel. La vérification visuelle finale reste à faire par l'utilisateur une fois le site redéployé (GitHub Pages).

- [ ] **Step 8 : Commit**

```bash
git add docs/index.html
git commit -m "Affiche le suivi des dividendes dans l'Analyse du Portefeuille (docs/index.html)"
```
