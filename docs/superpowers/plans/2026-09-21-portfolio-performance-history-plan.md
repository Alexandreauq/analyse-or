# Historique de performance du portefeuille + comparaison à un indice — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Donner à l'onglet Portefeuille → Actions → Analyse une courbe de rendement (%) du portefeuille dans le temps, comparée à un indice de référence choisi par l'utilisateur.

**Architecture:** Le job quotidien (`indices_score.py`) persiste désormais l'historique de cours (déjà récupéré via yfinance, jusqu'ici jeté après usage) dans un nouveau fichier public `docs/price_history.json`, couvrant les entreprises suivies et les 10 indices benchmark, avec un downsampling hebdomadaire au-delà de 30 jours. Le navigateur combine ce fichier avec les positions (ouvertes + nouvellement "clôturées", stockées en local) pour calculer et afficher la courbe, entièrement côté client.

**Tech Stack:** Python 3 + yfinance (déjà en place), JavaScript vanilla testé via `node docs/portfolio.test.js` (pas de framework, pas de DOM), SVG inline fait main pour le graphique (pas de nouvelle dépendance JS).

**Spec:** `docs/superpowers/specs/2026-09-20-portfolio-performance-history-design.md`

## Global Constraints

- Aucune donnée de position ne quitte jamais le navigateur — le calcul de la courbe reste entièrement côté client (spec §2/§3.2).
- Pas de conversion de change : courbes séparées EUR/USD, jamais mélangées (cohérent avec `computePortfolioTotals` existant).
- Rendement en **%**, pondéré par le capital investi (`pnlPct = sum(value-cost)/sum(cost)*100`), jamais une moyenne simple des % de chaque ligne, jamais une valeur brute en €.
- La courbe benchmark réutilise EXACTEMENT la même fonction d'agrégation et la même liste de positions que la courbe réelle — seule la source de prix change (spec §5). Ne jamais dupliquer la logique d'agrégation entre les deux.
- `docs/price_history.json` : entrées `{date, ticker, price}`, `ticker` soit un ticker d'entreprise (identique à `docs/indices.json`), soit une valeur de `INDEX_YFINANCE_TICKERS` (`^FCHI`, `^GDAXI`, `^NDX`, `^DJI`, `^FTSE`, `^SSMI`, `^IBEX`, `FTSEMIB.MI`, `^N225`, `^HSI`). Downsampling : quotidien dans les 30 derniers jours, hebdomadaire (1/semaine ISO) au-delà, rétention totale 2190 jours (6 ans), par ticker indépendamment. Règle idempotente, jamais de `allow_nan=False` violée, jamais d'exception qui fait échouer `main()`.
- `removePosition` (suppression pure, erreurs de saisie) reste inchangée et distincte de la nouvelle `closePosition` (vente réelle, garde une trace) — ne jamais fusionner les deux.
- Niveau de rigueur : fonctionnalité non sécurité-sensible (pas de nouveau secret, pas de nouveau service déployé) mais architecturalement significative (nouvelle étape de pipeline de données + nouveau calcul client + nouvelle UI) — revue de tâche normale et approfondie, plus une revue finale de branche solide ; ni sur- ni sous-scrutiner par rapport à ce niveau.
- Conventions de tests déjà établies dans ce dépôt : `tmp_path`/chemins explicites en Python (jamais un chemin par défaut de module dans un test — incident réel déjà survenu cette session), `node docs/portfolio.test.js` côté JS (aucun framework, aucun accès DOM — `docs/portfolio.js` documente lui-même "ne touche jamais au DOM"), vérification visuelle de l'UI via un vrai navigateur Playwright (pas de runner JS DOM disponible dans cet environnement).

---

## Task 1 : `indices_score.py` — downsampling et écriture de `docs/price_history.json`

**Files:**
- Modify: `indices_score.py`
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes : rien (fonctions pures/isolées, testées avec des entrées synthétiques — pas encore branchées sur yfinance, c'est la Task 2).
- Produces : `PRICE_HISTORY_PATH`, `PRICE_HISTORY_RECENT_DAYS = 30`, `PRICE_HISTORY_RETENTION_DAYS = 2190`, `_downsample_price_entries(entries: list[dict], today: date) -> list[dict]`, `load_price_history(path=PRICE_HISTORY_PATH) -> list[dict]`, `update_price_history(new_entries: list[dict], path=PRICE_HISTORY_PATH, today: date | None = None) -> list[dict]` — utilisées par la Task 2.

**Note d'implémentation (résout une question laissée ouverte par la spec §4.1) :** la spec distingue "backfill initial" (première apparition d'un ticker) et "ajout quotidien" comme deux phases. En pratique, `_downsample_price_entries` ci-dessous **dédoublonne par date** (dans la fenêtre récente) et **par semaine ISO** (au-delà) — elle est donc idempotente que `new_entries` contienne un seul point du jour ou tout l'historique disponible. La Task 2 profite de cette propriété : elle passe systématiquement l'historique complet déjà en mémoire (`fetch_company_financials` le récupère de toute façon chaque jour pour le scoring), sans avoir besoin de détecter "ticker déjà suivi ou nouveau" — plus simple et tout aussi correct que le découpage en deux phases de la spec.

- [ ] **Step 1 : Écrire les tests de `_downsample_price_entries`**

Ajoute dans `tests/test_indices_score.py`, à la suite des tests `update_nikkei_hangseng_price_history` déjà existants (autour de la ligne 2172) :

```python
from datetime import date as _date


def test_downsample_keeps_daily_entries_within_the_recent_window():
    today = _date(2026, 9, 21)
    entries = [
        {"date": "2026-09-01", "ticker": "MC.PA", "price": 100.0},
        {"date": "2026-09-02", "ticker": "MC.PA", "price": 101.0},
        {"date": "2026-09-21", "ticker": "MC.PA", "price": 120.0},
    ]
    result = indices_score._downsample_price_entries(entries, today)
    dates = sorted(e["date"] for e in result)
    assert "2026-09-01" in dates
    assert "2026-09-02" in dates
    assert "2026-09-21" in dates


def test_downsample_reduces_entries_older_than_30_days_to_one_per_iso_week():
    today = _date(2026, 9, 21)
    entries = [
        {"date": "2026-01-05", "ticker": "MC.PA", "price": 90.0},  # lundi semaine 2
        {"date": "2026-01-06", "ticker": "MC.PA", "price": 91.0},  # mardi meme semaine
        {"date": "2026-01-09", "ticker": "MC.PA", "price": 93.0},  # vendredi meme semaine (le plus recent)
    ]
    result = indices_score._downsample_price_entries(entries, today)
    assert len(result) == 1
    assert result[0]["date"] == "2026-01-09"
    assert result[0]["price"] == 93.0


def test_downsample_drops_entries_older_than_six_years_retention():
    today = _date(2026, 9, 21)
    entries = [
        {"date": "2019-01-01", "ticker": "MC.PA", "price": 50.0},  # > 2190 jours avant today
        {"date": "2026-09-21", "ticker": "MC.PA", "price": 120.0},
    ]
    result = indices_score._downsample_price_entries(entries, today)
    dates = [e["date"] for e in result]
    assert "2019-01-01" not in dates
    assert "2026-09-21" in dates


def test_downsample_deduplicates_same_day_duplicate_entries_in_the_recent_window():
    today = _date(2026, 9, 21)
    entries = [
        {"date": "2026-09-20", "ticker": "MC.PA", "price": 118.0},
        {"date": "2026-09-20", "ticker": "MC.PA", "price": 119.0},
    ]
    result = indices_score._downsample_price_entries(entries, today)
    assert len(result) == 1
    assert result[0]["price"] == 119.0


def test_downsample_returns_entries_sorted_by_date():
    today = _date(2026, 9, 21)
    entries = [
        {"date": "2026-09-15", "ticker": "MC.PA", "price": 110.0},
        {"date": "2019-06-01", "ticker": "MC.PA", "price": 40.0},
        {"date": "2026-01-09", "ticker": "MC.PA", "price": 93.0},
    ]
    result = indices_score._downsample_price_entries(entries, today)
    dates = [e["date"] for e in result]
    assert dates == sorted(dates)
```

- [ ] **Step 2 : Lancer les tests, vérifier qu'ils échouent**

Run: `python -m pytest tests/test_indices_score.py -k downsample -v`
Expected: FAIL — `AttributeError: module 'indices_score' has no attribute '_downsample_price_entries'`

- [ ] **Step 3 : Implémenter `_downsample_price_entries`**

Ajoute dans `indices_score.py`, juste après la définition de `PRICE_HISTORY_RETENTION_PER_TICKER` (ligne ~2752, avant `INDEX_YFINANCE_TICKERS`) :

```python
PRICE_HISTORY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "price_history.json")
PRICE_HISTORY_RECENT_DAYS = 30  # entrees quotidiennes dans cette fenetre
PRICE_HISTORY_RETENTION_DAYS = 2190  # 6 ans, au-dela l'entree la plus ancienne est supprimee


def _downsample_price_entries(entries: list[dict], today) -> list[dict]:
    """Regle de densite/retention pour docs/price_history.json (spec 3.3),
    reappliquee integralement a chaque run, jamais dependante d'un etat
    "deja downsample". Dedoublonne par date dans les PRICE_HISTORY_RECENT_DAYS
    derniers jours (une entree par jour), reduit a une entree par semaine
    ISO (la plus recente) au-dela, tronque a PRICE_HISTORY_RETENTION_DAYS.
    Idempotente : `entries` peut contenir un seul point du jour ou tout
    l'historique disponible, le resultat est le meme au global pres."""
    cutoff_recent = today - timedelta(days=PRICE_HISTORY_RECENT_DAYS)
    cutoff_retention = today - timedelta(days=PRICE_HISTORY_RETENTION_DAYS)
    recent_by_date: dict[str, dict] = {}
    older_by_week: dict[tuple, dict] = {}
    for entry in entries:
        entry_date = datetime.strptime(entry["date"], "%Y-%m-%d").date()
        if entry_date < cutoff_retention:
            continue
        if entry_date >= cutoff_recent:
            recent_by_date[entry["date"]] = entry
            continue
        week_key = entry_date.isocalendar()[:2]
        existing = older_by_week.get(week_key)
        if existing is None or entry["date"] > existing["date"]:
            older_by_week[week_key] = entry
    result = list(older_by_week.values()) + list(recent_by_date.values())
    result.sort(key=lambda e: e["date"])
    return result
```

- [ ] **Step 4 : Lancer les tests, vérifier qu'ils passent**

Run: `python -m pytest tests/test_indices_score.py -k downsample -v`
Expected: 5 PASSED

- [ ] **Step 5 : Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "Ajoute la regle de downsampling de l'historique de prix (docs/price_history.json)"
```

- [ ] **Step 6 : Écrire les tests de `load_price_history`/`update_price_history`**

Ajoute à la suite dans `tests/test_indices_score.py` :

```python
def test_load_price_history_returns_empty_list_when_file_is_absent(tmp_path):
    assert indices_score.load_price_history(str(tmp_path / "absent.json")) == []


def test_load_price_history_degrades_to_empty_list_on_corrupt_json(tmp_path):
    path = tmp_path / "corrompu.json"
    path.write_text("pas du json", encoding="utf-8")
    assert indices_score.load_price_history(str(path)) == []


def test_update_price_history_writes_new_entries_to_a_fresh_file(tmp_path):
    path = str(tmp_path / "price_history.json")
    entries = [{"date": "2026-09-21", "ticker": "MC.PA", "price": 620.4}]
    result = indices_score.update_price_history(entries, path=path, today=_date(2026, 9, 21))
    assert result == entries
    assert indices_score.load_price_history(path) == entries


def test_update_price_history_accumulates_across_calls_and_downsamples(tmp_path):
    path = str(tmp_path / "price_history.json")
    indices_score.update_price_history(
        [{"date": "2026-01-05", "ticker": "MC.PA", "price": 90.0}], path=path, today=_date(2026, 1, 5))
    indices_score.update_price_history(
        [{"date": "2026-09-21", "ticker": "MC.PA", "price": 120.0}], path=path, today=_date(2026, 9, 21))
    result = indices_score.load_price_history(path)
    dates = [e["date"] for e in result]
    assert "2026-01-05" in dates  # seule entree de cette semaine ISO, conservee
    assert "2026-09-21" in dates


def test_update_price_history_trims_independently_per_ticker(tmp_path):
    path = str(tmp_path / "price_history.json")
    entries = [
        {"date": "2026-09-21", "ticker": "MC.PA", "price": 620.4},
        {"date": "2026-09-21", "ticker": "SAP.DE", "price": 210.0},
    ]
    result = indices_score.update_price_history(entries, path=path, today=_date(2026, 9, 21))
    tickers = {e["ticker"] for e in result}
    assert tickers == {"MC.PA", "SAP.DE"}


def test_update_price_history_never_writes_nan(tmp_path):
    path = str(tmp_path / "price_history.json")
    entries = [{"date": "2026-09-21", "ticker": "MC.PA", "price": float("nan")}]
    result = indices_score.update_price_history(entries, path=path, today=_date(2026, 9, 21))
    assert result == []
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    assert "NaN" not in content


def test_update_price_history_degrades_to_empty_list_on_unexpected_failure(tmp_path, monkeypatch):
    path = str(tmp_path / "sous_dossier_impossible" / "price_history.json")
    fichier_bloquant = tmp_path / "sous_dossier_impossible"
    fichier_bloquant.write_text("x", encoding="utf-8")
    result = indices_score.update_price_history(
        [{"date": "2026-09-21", "ticker": "MC.PA", "price": 620.4}], path=path, today=_date(2026, 9, 21))
    assert result == []
```

- [ ] **Step 7 : Lancer les tests, vérifier qu'ils échouent**

Run: `python -m pytest tests/test_indices_score.py -k "price_history and not nikkei" -v`
Expected: FAIL — `AttributeError: module 'indices_score' has no attribute 'load_price_history'`

- [ ] **Step 8 : Implémenter `load_price_history`/`update_price_history`**

Ajoute dans `indices_score.py`, juste après `_downsample_price_entries` :

```python
def load_price_history(path=PRICE_HISTORY_PATH) -> list[dict]:
    """Meme contrat que load_nikkei_hangseng_price_history : [] si le
    fichier est absent ou corrompu, jamais d'exception."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError:
        return []


def update_price_history(new_entries: list[dict], path=PRICE_HISTORY_PATH, today=None) -> list[dict]:
    """Ajoute `new_entries` ({date, ticker, price}) a l'historique deja
    accumule, retrimme chaque ticker independamment via
    _downsample_price_entries (voir sa note d'idempotence), puis ecrit
    le resultat. Degrade toujours vers [] sur erreur (fichier illisible,
    NaN detecte par allow_nan=False...), ne fait jamais echouer main()."""
    today = today or datetime.today().date()
    try:
        history = load_price_history(path)
        history.extend(new_entries)
        by_ticker: dict[str, list[dict]] = {}
        for entry in history:
            by_ticker.setdefault(entry["ticker"], []).append(entry)
        trimmed = []
        for ticker_entries in by_ticker.values():
            trimmed.extend(_downsample_price_entries(ticker_entries, today))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(trimmed, fh, ensure_ascii=False, indent=2, allow_nan=False)
        return trimmed
    except Exception as e:
        print(f"Erreur historique de prix : {e}")
        return []
```

- [ ] **Step 9 : Lancer les tests, vérifier qu'ils passent**

Run: `python -m pytest tests/test_indices_score.py -k "price_history and not nikkei" -v`
Expected: 6 PASSED

- [ ] **Step 10 : Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "Ajoute load_price_history/update_price_history (docs/price_history.json)"
```

---

## Task 2 : Brancher les vraies données (entreprises + indices benchmark)

**Files:**
- Modify: `indices_score.py`
- Modify: `.github/workflows/indices.yml`
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes : `update_price_history` (Task 1), `INDEX_YFINANCE_TICKERS` (déjà existant, ~ligne 2755), `fetch_company_financials`/`build_company_entry`/`main()` (déjà existants).
- Produces : `fetch_company_financials` renvoie désormais aussi une clé interne `_price_history_daily` (liste `{date, ticker, price}`, jamais présente dans `docs/indices.json` publié) ; nouvelle fonction `fetch_index_price_history() -> list[dict]` ; `main()` appelle `update_price_history` avec l'historique combiné entreprises + indices.

- [ ] **Step 1 : Écrire le test de `fetch_company_financials` exposant l'historique de prix**

Ajoute dans `tests/test_indices_score.py`, à la suite de `test_fetch_company_financials_does_not_override_other_tickers` (voir `test_fetch_company_financials_uses_price_history_override_for_mtpa`, ligne 4367, pour le style déjà en place — chaque test y définit sa propre classe `_FakeTicker` locale, monkeypatchée sur `indices_score.yf.Ticker` uniquement, pas sur tout le module `yf` ; `_make_fixture_statements()` et `_fake_annual_df(...)` sont des helpers déjà présents dans ce fichier, à réutiliser tels quels) :

```python
def test_fetch_company_financials_exposes_the_full_price_history_for_persistence(monkeypatch):
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])
    history_index = pd.date_range("2024-01-01", periods=3, freq="D")
    history_close = pd.Series([100.0, 101.0, 102.0], index=history_index)

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

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close})

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)

    result = indices_score.fetch_company_financials("MC.PA")

    assert "_price_history_daily" in result
    entries = result["_price_history_daily"]
    assert len(entries) == 3
    assert all(e["ticker"] == "MC.PA" for e in entries)
    assert all(set(e.keys()) == {"date", "ticker", "price"} for e in entries)
    assert entries == sorted(entries, key=lambda e: e["date"])
    assert entries[-1]["price"] == pytest.approx(102.0)
```

- [ ] **Step 2 : Lancer le test, vérifier qu'il échoue**

Run: `python -m pytest tests/test_indices_score.py -k exposes_the_full_price_history -v`
Expected: FAIL — `KeyError: '_price_history_daily'` ou assertion `in result` qui échoue

- [ ] **Step 3 : Exposer l'historique dans `fetch_company_financials`**

Dans `indices_score.py`, fonction `fetch_company_financials` (ligne 2361), juste avant le `return ratios` final (ligne ~2469, juste après `ratios["beta"] = beta`), ajoute :

```python
    ratios["_price_history_daily"] = [
        {"date": idx.strftime("%Y-%m-%d"), "ticker": ticker, "price": float(val)}
        for idx, val in history.items()
    ]
    return ratios
```

`history` est déjà disponible à ce point de la fonction (variable locale, déjà nettoyée par `history.dropna()` ligne 2415, déjà convertie pence→livres pour les tickers `.L` si besoin). Le préfixe `_` signale que cette clé est interne au pipeline, jamais destinée à `docs/indices.json`.

- [ ] **Step 4 : Lancer le test, vérifier qu'il passe**

Run: `python -m pytest tests/test_indices_score.py -k exposes_the_full_price_history -v`
Expected: PASSED

- [ ] **Step 5 : Lancer toute la suite `fetch_company_financials`, vérifier qu'aucun test existant ne casse**

Run: `python -m pytest tests/test_indices_score.py -k fetch_company_financials -v`
Expected: tous PASSED (aucun test existant ne fait d'égalité stricte sur le dict complet renvoyé — vérifié lors de l'écriture de ce plan — ajouter une clé ne doit rien casser)

- [ ] **Step 6 : Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "fetch_company_financials expose l'historique de prix pour docs/price_history.json"
```

- [ ] **Step 7 : Écrire le test de propagation dans `build_company_entry`**

Cherche `def test_build_company_entry` dans `tests/test_indices_score.py` pour repérer un test existant complet (mocks de `fetch_news`/l'appel Claude pour `financial_analysis_html`/`_fetch_statement_with_retry` en plus du `_FakeTicker` — `build_company_entry` a plus de dépendances que `fetch_company_financials` seul) et reproduis exactement le même jeu de mocks, en réutilisant le `_FakeTicker` du Step 1 (`history()` retournant `history_close`) pour que l'appel n'échoue pas :

```python
def test_build_company_entry_carries_the_price_history_through(monkeypatch):
    # Reprend le meme jeu de mocks que le test existant identifie
    # ci-dessus (_FakeTicker avec history() non vide comme au Step 1,
    # plus les mocks Claude/actualites deja necessaires pour que
    # build_company_entry ne leve pas), en ajoutant seulement la
    # verification de la nouvelle cle.
    entry = indices_score.build_company_entry(
        "MC.PA", "LVMH", risk_free_rate=0.03, previous_analyses={}, index_key="CAC40")
    assert "_price_history_daily" in entry
    assert entry["_price_history_daily"]
```

- [ ] **Step 8 : Lancer le test, vérifier qu'il échoue**

Run: `python -m pytest tests/test_indices_score.py -k build_company_entry_carries -v`
Expected: FAIL — `KeyError: '_price_history_daily'`

- [ ] **Step 9 : Propager la clé dans `build_company_entry`**

Dans `indices_score.py`, fonction `build_company_entry` (ligne 3658), le `return {` littéral (ligne 3818) devient :

```python
    entry = {
        "ticker": ticker,
        "name": name,
        "index": index_key,
        "also_indices": also_indices or [],
        "sector": sector,
        # ... (tous les champs déjà présents, INCHANGÉS — ne pas les retaper,
        # juste renommer `return {` en `entry = {` et ajouter le bloc suivant
        # juste après l'accolade fermante du dict, avant tout autre code) ...
    }
    entry["_price_history_daily"] = data["_price_history_daily"]
    return entry
```

Attention : c'est un renommage de `return {` en `entry = {` (la première ligne du dict), PAS une réécriture du contenu du dict — tous les champs existants entre les deux doivent rester identiques, character pour character.

- [ ] **Step 10 : Lancer le test, vérifier qu'il passe, puis toute la suite `build_company_entry`**

Run: `python -m pytest tests/test_indices_score.py -k build_company_entry -v`
Expected: tous PASSED

- [ ] **Step 11 : Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "build_company_entry propage l'historique de prix jusqu'a main()"
```

- [ ] **Step 12 : Écrire les tests de `fetch_index_price_history`**

Même style que `test_fetch_index_prices_returns_latest_close_per_index` (ligne 4523) et `test_fetch_index_prices_degrades_to_none_per_index_on_failure` (ligne 4547) — `Ticker.history(period)` renvoie ici un vrai `pandas.Series` multi-points (pas juste le dernier close) puisque `fetch_index_price_history` garde tout l'historique, pas seulement `iloc[-1]` :

```python
def test_fetch_index_price_history_returns_entries_for_each_tracked_index(monkeypatch):
    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, period):
            assert period == "6y"
            return {"Close": pd.Series([7800.0, 7850.0], index=pd.to_datetime(["2026-09-20", "2026-09-21"]))}

    monkeypatch.setattr(indices_score.yf, "Ticker", FakeTicker)
    result = indices_score.fetch_index_price_history()
    tickers = {e["ticker"] for e in result}
    assert tickers == set(indices_score.INDEX_YFINANCE_TICKERS.values())
    fchi_entries = [e for e in result if e["ticker"] == "^FCHI"]
    assert len(fchi_entries) == 2


def test_fetch_index_price_history_skips_an_index_whose_fetch_fails(monkeypatch):
    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, period):
            if self.symbol == "^FCHI":
                raise RuntimeError("panne réseau")
            return {"Close": pd.Series([19230.0], index=pd.to_datetime(["2026-09-21"]))}

    monkeypatch.setattr(indices_score.yf, "Ticker", FakeTicker)
    result = indices_score.fetch_index_price_history()
    tickers = {e["ticker"] for e in result}
    assert "^FCHI" not in tickers
    assert "^GDAXI" in tickers


def test_fetch_index_price_history_returns_empty_list_when_yfinance_unavailable(monkeypatch):
    monkeypatch.setattr(indices_score, "yf", None)
    assert indices_score.fetch_index_price_history() == []
```

- [ ] **Step 13 : Lancer les tests, vérifier qu'ils échouent**

Run: `python -m pytest tests/test_indices_score.py -k fetch_index_price_history -v`
Expected: FAIL — `AttributeError: module 'indices_score' has no attribute 'fetch_index_price_history'`

- [ ] **Step 14 : Implémenter `fetch_index_price_history`**

Ajoute dans `indices_score.py`, juste après `fetch_index_prices` (ligne 2789-2805) :

```python
def fetch_index_price_history() -> list[dict]:
    """Historique complet (period='6y') du niveau de chaque indice
    benchmark suivi (INDEX_YFINANCE_TICKERS) — alimente la courbe de
    comparaison du portefeuille (docs/price_history.json). Distinct de
    fetch_index_prices() qui ne renvoie qu'un instantane du jour (5
    jours) pour index_price_at_entry — ne pas fusionner les deux, cette
    derniere a un contrat different pour ses appelants existants. Une
    liste vide par indice dont le fetch echoue individuellement, ou []
    si yfinance n'est pas installe — ne fait jamais echouer les autres
    indices ni lever d'exception."""
    if yf is None:
        return []
    entries = []
    for yf_ticker in INDEX_YFINANCE_TICKERS.values():
        try:
            history = yf.Ticker(yf_ticker).history(period="6y")["Close"].dropna()
            entries.extend(
                {"date": idx.strftime("%Y-%m-%d"), "ticker": yf_ticker, "price": float(val)}
                for idx, val in history.items()
            )
        except Exception:
            continue
    return entries
```

- [ ] **Step 15 : Lancer les tests, vérifier qu'ils passent**

Run: `python -m pytest tests/test_indices_score.py -k fetch_index_price_history -v`
Expected: 3 PASSED

- [ ] **Step 16 : Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "Ajoute fetch_index_price_history pour les 10 indices benchmark"
```

- [ ] **Step 17 : Écrire le test de branchement dans `main()`**

```python
def test_main_persists_price_history_from_companies_and_indices(monkeypatch, tmp_path):
    # Reprend les memes mocks que les autres tests de main() deja
    # existants dans ce fichier (chercher `def test_main_` pour le
    # gabarit complet — mocks de fetch_risk_free_rate, COMPANIES reduit a
    # une seule entreprise, send_daily_digest_email, update_signal_tracking,
    # update_nikkei_hangseng_price_history, OUTPUT_JSON_PATH vers tmp_path).
    captured = {}
    def _fake_update_price_history(entries, **kwargs):
        captured["entries"] = entries
        return entries
    monkeypatch.setattr(indices_score, "update_price_history", _fake_update_price_history)
    monkeypatch.setattr(indices_score, "fetch_index_price_history", lambda: [
        {"date": "2026-09-21", "ticker": "^FCHI", "price": 7850.2}])
    # ... (branche les mocks habituels de main(), voir les tests
    # test_main_* existants pour le patron exact) ...
    indices_score.main()
    tickers = {e["ticker"] for e in captured["entries"]}
    assert "^FCHI" in tickers
    assert "MC.PA" in tickers  # ou le ticker de l'entreprise factice utilisee par les autres tests main()


def test_main_never_writes_the_internal_price_history_key_to_indices_json(monkeypatch, tmp_path):
    # Meme gabarit de mocks que le test precedent, plus la redirection de
    # OUTPUT_JSON_PATH vers tmp_path deja pratiquee par les tests main()
    # existants.
    monkeypatch.setattr(indices_score, "update_price_history", lambda entries, **kwargs: entries)
    monkeypatch.setattr(indices_score, "fetch_index_price_history", lambda: [])
    # ... (mocks habituels + OUTPUT_JSON_PATH -> tmp_path) ...
    indices_score.main()
    with open(indices_score.OUTPUT_JSON_PATH, encoding="utf-8") as fh:
        payload = json.load(fh)
    for company in payload["companies"]:
        assert "_price_history_daily" not in company
```

Complète les deux tests avec exactement les mêmes mocks que les tests `test_main_*` déjà présents dans ce fichier pour `update_signal_tracking`/`update_nikkei_hangseng_price_history`/`send_daily_digest_email`/`COMPANIES` (indispensable — un test `main()` non mocké a déjà, une fois cette session, écrit pour de vrai dans des fichiers réels du dépôt faute de monkeypatch complet).

- [ ] **Step 18 : Lancer les tests, vérifier qu'ils échouent**

Run: `python -m pytest tests/test_indices_score.py -k "main_persists_price_history or main_never_writes_the_internal" -v`
Expected: FAIL (le nouvel appel n'existe pas encore dans `main()`, donc `captured["entries"]` reste vide/KeyError, ou la clé interne apparaît bien dans `payload` puisqu'elle n'est pas encore retirée)

- [ ] **Step 19 : Brancher dans `main()`**

Dans `indices_score.py`, fonction `main()` (ligne 4114), juste après la boucle `for company in COMPANIES: ...` (qui se termine ligne 4141, juste avant `newly_triggered_entree, newly_triggered_major_news = _attach_alerts_and_update_history(companies)` ligne 4143), insère :

```python
    price_history_entries = []
    for c in companies:
        price_history_entries.extend(c.pop("_price_history_daily", []))
    price_history_entries.extend(fetch_index_price_history())
    update_price_history(price_history_entries)

```

Le `.pop(...)` retire la clé interne de chaque dict de `companies` avant que ce même `companies` ne serve à construire `payload`/`docs/indices.json` plus bas (ligne 4148-4171) — elle ne doit jamais apparaître dans le JSON public.

- [ ] **Step 20 : Lancer les tests, vérifier qu'ils passent**

Run: `python -m pytest tests/test_indices_score.py -k "main_persists_price_history or main_never_writes_the_internal" -v`
Expected: 2 PASSED

- [ ] **Step 21 : Lancer toute la suite `test_indices_score.py` pour détecter une régression**

Run: `python -m pytest tests/test_indices_score.py -v`
Expected: tous PASSED

- [ ] **Step 22 : Étendre `.github/workflows/indices.yml`**

Trouve la ligne existante (autour de la ligne 45) :
```
          [ -f docs/nikkei_hangseng_price_history.json ] && git add docs/nikkei_hangseng_price_history.json || true
```
Ajoute juste après :
```
          [ -f docs/price_history.json ] && git add docs/price_history.json || true
```

- [ ] **Step 23 : Commit**

```bash
git add indices_score.py tests/test_indices_score.py .github/workflows/indices.yml
git commit -m "Branche la persistance de docs/price_history.json dans main()"
```

---

## Task 3 : `docs/portfolio.js` — positions clôturées

**Files:**
- Modify: `docs/portfolio.js`
- Test: `docs/portfolio.test.js`

**Interfaces:**
- Consumes : `_resolveStorage`, `loadPortfolio`/`savePortfolio` (existants, lignes 45-81), `validatePositionInput` (existant, ligne 19).
- Produces : `PORTFOLIO_CLOSED_STORAGE_KEY`, `loadClosedPortfolio(storage)`, `saveClosedPortfolio(positions, storage)`, `closePosition(id, sellPrice, sellDate, storage) -> {ok, error} | {ok, positions, closedPositions}` — utilisées par la Task 4 (calcul de courbe) et la Task 5 (UI "Vendre").

- [ ] **Step 1 : Écrire les tests**

Ajoute dans `docs/portfolio.test.js`, après les tests existants pour `removePosition` (chercher `function test_remove_position` ou équivalent pour le point d'ancrage et le style exact des faux `storage` déjà utilisés dans ce fichier — un objet `{ getItem, setItem }` en mémoire, pas de vrai `localStorage`) :

```javascript
function test_close_position_moves_it_from_open_to_closed() {
  const storage = fakeStorage();
  const { positions } = addPosition('MC.PA', 5, 90.0, '2026-01-15', storage);
  const id = positions[0].id;

  const result = closePosition(id, 95.5, '2026-09-15', storage);

  assert.strictEqual(result.ok, true);
  assert.strictEqual(result.positions.length, 0);
  assert.strictEqual(result.closedPositions.length, 1);
  assert.strictEqual(result.closedPositions[0].sell_price, 95.5);
  assert.strictEqual(result.closedPositions[0].sell_date, '2026-09-15');
  assert.strictEqual(result.closedPositions[0].ticker, 'MC.PA');
  assert.strictEqual(loadPortfolio(storage).length, 0);
  assert.strictEqual(loadClosedPortfolio(storage).length, 1);
}

function test_close_position_rejects_non_positive_sell_price() {
  const storage = fakeStorage();
  const { positions } = addPosition('MC.PA', 5, 90.0, '2026-01-15', storage);
  const result = closePosition(positions[0].id, -1, '2026-09-15', storage);
  assert.strictEqual(result.ok, false);
  assert.strictEqual(loadPortfolio(storage).length, 1);
}

function test_close_position_returns_error_for_unknown_id() {
  const storage = fakeStorage();
  const result = closePosition('id-inconnu', 95.5, '2026-09-15', storage);
  assert.strictEqual(result.ok, false);
}

function test_remove_position_never_touches_the_closed_list() {
  const storage = fakeStorage();
  const { positions } = addPosition('MC.PA', 5, 90.0, '2026-01-15', storage);
  removePosition(positions[0].id, storage);
  assert.strictEqual(loadPortfolio(storage).length, 0);
  assert.strictEqual(loadClosedPortfolio(storage).length, 0);
}

function test_load_closed_portfolio_returns_empty_array_when_storage_absent() {
  assert.deepStrictEqual(loadClosedPortfolio(null), []);
}

function test_load_closed_portfolio_degrades_to_empty_array_on_corrupt_json() {
  const storage = fakeStorage();
  storage.setItem(PORTFOLIO_CLOSED_STORAGE_KEY, 'pas du json');
  assert.deepStrictEqual(loadClosedPortfolio(storage), []);
}
```

Utilise le helper `fakeStorage()` déjà défini dans ce fichier pour les tests existants de `loadPortfolio`/`savePortfolio` (chercher sa définition exacte en tête de fichier et le réutiliser tel quel, ne pas en recréer un).

Ajoute les appels à ces 6 nouvelles fonctions dans la liste des tests exécutés par le `main()`/la séquence d'appels en bas du fichier (même motif que les tests déjà présents).

- [ ] **Step 2 : Lancer les tests, vérifier qu'ils échouent**

Run: `node docs/portfolio.test.js`
Expected: erreur `closePosition is not defined` (ou équivalent) sur le premier nouveau test

- [ ] **Step 3 : Implémenter**

Ajoute dans `docs/portfolio.js`, juste après `removePosition` (ligne 113) :

```javascript
const PORTFOLIO_CLOSED_STORAGE_KEY = 'analyse-or-portfolio-closed';

/**
 * Repli sur [] si storage est absent, la clé n'existe pas, le JSON est
 * invalide, ou la valeur stockée n'est pas un tableau — même contrat
 * que loadPortfolio.
 */
function loadClosedPortfolio(storage) {
  const s = _resolveStorage(storage);
  if (!s) return [];
  try {
    const raw = s.getItem(PORTFOLIO_CLOSED_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (e) {
    return [];
  }
}

function saveClosedPortfolio(positions, storage) {
  const s = _resolveStorage(storage);
  if (!s) return false;
  try {
    s.setItem(PORTFOLIO_CLOSED_STORAGE_KEY, JSON.stringify(positions));
    return true;
  } catch (e) {
    return false;
  }
}

/**
 * Déplace une position ouverte vers la liste clôturée (garde une trace
 * de la vente : sell_price, sell_date), plutôt que de la supprimer —
 * removePosition reste la vraie suppression (erreurs de saisie), reste
 * inchangée, ne touche jamais cette liste.
 */
function closePosition(id, sellPrice, sellDate, storage) {
  if (!Number.isFinite(sellPrice) || sellPrice <= 0) {
    return { ok: false, error: 'Le prix de vente doit être un nombre positif.' };
  }
  const openPositions = loadPortfolio(storage);
  const idx = openPositions.findIndex(p => p.id === id);
  if (idx === -1) return { ok: false, error: 'Position introuvable.' };
  const [position] = openPositions.splice(idx, 1);
  const closedPosition = { ...position, sell_price: sellPrice, sell_date: sellDate };
  const closedPositions = loadClosedPortfolio(storage);
  closedPositions.push(closedPosition);
  if (!savePortfolio(openPositions, storage) || !saveClosedPortfolio(closedPositions, storage)) {
    return { ok: false, error: "La sauvegarde a échoué (stockage local indisponible ou plein)." };
  }
  return { ok: true, positions: openPositions, closedPositions };
}
```

Ajoute aussi `loadClosedPortfolio`, `saveClosedPortfolio`, `closePosition` au bloc d'export `module.exports` en bas du fichier (même motif que les fonctions déjà exportées — vérifie la liste actuelle et ajoute les trois nouveaux noms).

- [ ] **Step 4 : Lancer les tests, vérifier qu'ils passent**

Run: `node docs/portfolio.test.js`
Expected: tous les tests passent, y compris les 6 nouveaux (le fichier affiche généralement un résumé du nombre de tests exécutés/réussis — vérifie qu'aucun message d'échec n'apparaît)

- [ ] **Step 5 : Commit**

```bash
git add docs/portfolio.js docs/portfolio.test.js
git commit -m "Ajoute la notion de position cloturee (closePosition) a docs/portfolio.js"
```

---

## Task 4 : `docs/portfolio.js` — calcul de la courbe de performance

**Files:**
- Modify: `docs/portfolio.js`
- Test: `docs/portfolio.test.js`

**Interfaces:**
- Consumes : `closePosition`/`loadClosedPortfolio` (Task 3), `docs/price_history.json` (Task 2, format `[{date, ticker, price}]`).
- Produces : `groupPriceHistoryByTicker(priceHistory) -> {ticker: [{date,price}, ...]}`, `priceAtOrBefore(priceHistoryByTicker, ticker, date) -> number|null`, `isPositionActiveOn(position, date) -> boolean`, `computePerformanceCurve(positions, priceHistoryByTicker, priceForPosition) -> [{date, pnlPct}]`, `computePortfolioPerformanceCurves(openPositions, closedPositions, companiesByTicker, currencyByIndex, priceHistoryByTicker) -> {EUR: [...], USD: [...]}`, `computeBenchmarkPerformanceCurve(positions, indexTicker, priceHistoryByTicker) -> [{date, pnlPct}]` — utilisées par la Task 5 (UI).

- [ ] **Step 1 : Écrire les tests**

Ajoute dans `docs/portfolio.test.js` :

```javascript
function test_group_price_history_by_ticker_sorts_each_group_by_date() {
  const history = [
    { date: '2026-09-15', ticker: 'MC.PA', price: 110 },
    { date: '2026-09-10', ticker: 'MC.PA', price: 105 },
    { date: '2026-09-12', ticker: '^FCHI', price: 7800 },
  ];
  const grouped = groupPriceHistoryByTicker(history);
  assert.deepStrictEqual(grouped['MC.PA'].map(e => e.date), ['2026-09-10', '2026-09-15']);
  assert.strictEqual(grouped['^FCHI'].length, 1);
}

function test_price_at_or_before_returns_the_latest_entry_not_after_the_date() {
  const grouped = { 'MC.PA': [{ date: '2026-09-10', price: 100 }, { date: '2026-09-15', price: 110 }] };
  assert.strictEqual(priceAtOrBefore(grouped, 'MC.PA', '2026-09-12'), 100);
  assert.strictEqual(priceAtOrBefore(grouped, 'MC.PA', '2026-09-20'), 110);
}

function test_price_at_or_before_never_looks_into_the_future() {
  const grouped = { 'MC.PA': [{ date: '2026-09-10', price: 100 }] };
  assert.strictEqual(priceAtOrBefore(grouped, 'MC.PA', '2026-09-05'), null);
}

function test_price_at_or_before_returns_null_for_an_unknown_ticker() {
  assert.strictEqual(priceAtOrBefore({}, 'INCONNU.PA', '2026-09-12'), null);
}

function test_is_position_active_on_respects_buy_and_sell_dates() {
  const open = { buy_date: '2026-01-15' };
  assert.strictEqual(isPositionActiveOn(open, '2026-01-14'), false);
  assert.strictEqual(isPositionActiveOn(open, '2026-01-15'), true);
  assert.strictEqual(isPositionActiveOn(open, '2026-09-21'), true);

  const closed = { buy_date: '2026-01-15', sell_date: '2026-06-01' };
  assert.strictEqual(isPositionActiveOn(closed, '2026-05-31'), true);
  assert.strictEqual(isPositionActiveOn(closed, '2026-06-01'), true);
  assert.strictEqual(isPositionActiveOn(closed, '2026-06-02'), false);
}

function test_compute_performance_curve_weights_by_invested_capital_not_naive_average() {
  const positions = [
    { ticker: 'A', buy_date: '2026-01-01', quantity: 100, buy_price: 1.0 },   // cout 100
    { ticker: 'B', buy_date: '2026-01-01', quantity: 1, buy_price: 1000.0 },  // cout 1000
  ];
  const priceHistoryByTicker = {
    A: [{ date: '2026-02-01', price: 2.0 }],   // +100%
    B: [{ date: '2026-02-01', price: 1010.0 }], // +1%
  };
  const curve = computePerformanceCurve(positions, priceHistoryByTicker, realPriceForPosition);
  assert.strictEqual(curve.length, 1);
  // pondere : (100 + 10) / (100 + 1000) * 100 = 10.0, PAS (100+1)/2 = 50.5 (moyenne naive)
  assert.ok(Math.abs(curve[0].pnlPct - 10.0) < 0.001);
}

function test_compute_performance_curve_excludes_a_position_with_no_known_price_on_a_date_without_dropping_the_date() {
  const positions = [
    { ticker: 'A', buy_date: '2026-01-01', quantity: 10, buy_price: 10.0 },
    { ticker: 'B', buy_date: '2026-03-01', quantity: 10, buy_price: 20.0 }, // pas encore actif au 02-01
  ];
  const priceHistoryByTicker = {
    A: [{ date: '2026-02-01', price: 11.0 }],
    B: [{ date: '2026-02-01', price: 21.0 }],
  };
  const curve = computePerformanceCurve(positions, priceHistoryByTicker, realPriceForPosition);
  assert.strictEqual(curve.length, 1);
  // seule A compte au 02-01 : (11-10)*10 / (10*10) * 100 = 10%
  assert.ok(Math.abs(curve[0].pnlPct - 10.0) < 0.001);
}

function test_compute_portfolio_performance_curves_splits_by_currency_and_skips_empty_currency() {
  const openPositions = [{ id: '1', ticker: 'MC.PA', buy_date: '2026-01-01', quantity: 5, buy_price: 90.0 }];
  const companiesByTicker = { 'MC.PA': { index: 'CAC40' } };
  const currencyByIndex = { CAC40: 'EUR' };
  const priceHistoryByTicker = { 'MC.PA': [{ date: '2026-02-01', price: 100.0 }] };
  const curves = computePortfolioPerformanceCurves([], [], companiesByTicker, currencyByIndex, priceHistoryByTicker);
  assert.deepStrictEqual(curves, { EUR: [], USD: [] });

  const curvesWithPosition = computePortfolioPerformanceCurves(
    openPositions, [], companiesByTicker, currencyByIndex, priceHistoryByTicker);
  assert.strictEqual(curvesWithPosition.EUR.length, 1);
  assert.deepStrictEqual(curvesWithPosition.USD, []);
}

function test_compute_benchmark_performance_curve_reuses_the_same_positions_and_dates() {
  const positions = [{ ticker: 'MC.PA', buy_date: '2026-01-01', quantity: 5, buy_price: 90.0 }];
  const priceHistoryByTicker = {
    'MC.PA': [{ date: '2026-02-01', price: 90.0 }],  // 0% sur le titre reel
    '^FCHI': [{ date: '2026-02-01', price: 8000.0 }],
  };
  const benchmarkCurve = computeBenchmarkPerformanceCurve(positions, '^FCHI', priceHistoryByTicker);
  assert.strictEqual(benchmarkCurve.length, 1);
  assert.strictEqual(benchmarkCurve[0].date, '2026-02-01');
  // valeur benchmark = 8000 * 5 = 40000, cout = 90*5 = 450 -> gros % positif attendu, different de 0%
  assert.notStrictEqual(benchmarkCurve[0].pnlPct, 0);
}
```

- [ ] **Step 2 : Lancer les tests, vérifier qu'ils échouent**

Run: `node docs/portfolio.test.js`
Expected: erreur `groupPriceHistoryByTicker is not defined` (ou équivalent) sur le premier nouveau test

- [ ] **Step 3 : Implémenter**

Ajoute dans `docs/portfolio.js`, après `closePosition` (fin de la Task 3) :

```javascript
/**
 * Regroupe docs/price_history.json (liste plate {date, ticker, price})
 * par ticker, trie chaque groupe par date croissante.
 */
function groupPriceHistoryByTicker(priceHistory) {
  const byTicker = {};
  priceHistory.forEach(entry => {
    (byTicker[entry.ticker] = byTicker[entry.ticker] || []).push(entry);
  });
  Object.values(byTicker).forEach(entries => entries.sort((a, b) => (a.date < b.date ? -1 : 1)));
  return byTicker;
}

/**
 * Prix connu d'un ticker à la date D : la dernière entrée dont la date
 * est <= D (jamais d'extrapolation future). null si aucune n'existe.
 */
function priceAtOrBefore(priceHistoryByTicker, ticker, date) {
  const entries = priceHistoryByTicker[ticker];
  if (!entries || !entries.length) return null;
  let result = null;
  for (const entry of entries) {
    if (entry.date > date) break;
    result = entry.price;
  }
  return result;
}

/**
 * Une position (ouverte ou clôturée) est active à la date D si
 * buy_date <= D et (sell_date absent OU sell_date >= D).
 */
function isPositionActiveOn(position, date) {
  if (position.buy_date > date) return false;
  if (position.sell_date && position.sell_date < date) return false;
  return true;
}

function realPriceForPosition(position, date, priceHistoryByTicker) {
  return priceAtOrBefore(priceHistoryByTicker, position.ticker, date);
}

function benchmarkPriceForPosition(indexTicker) {
  return (position, date, priceHistoryByTicker) => priceAtOrBefore(priceHistoryByTicker, indexTicker, date);
}

/**
 * Courbe de rendement en % pour un ensemble de positions sur toutes les
 * dates disponibles dans priceHistoryByTicker pour les tickers de ces
 * positions. `priceForPosition(position, date, priceHistoryByTicker)`
 * fournit le prix à utiliser — le portefeuille réel l'appelle avec
 * realPriceForPosition (prix de CHAQUE position), la courbe benchmark
 * avec benchmarkPriceForPosition(indexTicker) (même prix d'indice pour
 * TOUTES les positions) : même fonction d'agrégation dans les deux cas.
 *
 * pnlPct(D) = somme(value(D) - cost) / somme(cost) * 100 — pondéré par
 * le capital investi, jamais une moyenne des % de chaque ligne.
 */
function computePerformanceCurve(positions, priceHistoryByTicker, priceForPosition) {
  const dates = new Set();
  positions.forEach(p => {
    const entries = priceHistoryByTicker[p.ticker];
    if (entries) entries.forEach(e => dates.add(e.date));
  });
  return Array.from(dates).sort().map(date => {
    let totalCost = 0;
    let totalPnlAbs = 0;
    positions.forEach(position => {
      if (!isPositionActiveOn(position, date)) return;
      const price = priceForPosition(position, date, priceHistoryByTicker);
      if (!Number.isFinite(price)) return;
      const cost = position.buy_price * position.quantity;
      const value = price * position.quantity;
      totalCost += cost;
      totalPnlAbs += value - cost;
    });
    return { date, pnlPct: totalCost !== 0 ? (totalPnlAbs / totalCost) * 100 : null };
  }).filter(point => point.pnlPct !== null);
}

/**
 * { EUR: [...], USD: [...] } — tableau vide pour une devise sans
 * aucune position (ouverte ou clôturée), jamais de conversion entre
 * devises (cohérent avec computePortfolioTotals).
 */
function computePortfolioPerformanceCurves(openPositions, closedPositions, companiesByTicker, currencyByIndex, priceHistoryByTicker) {
  const allPositions = [...openPositions, ...closedPositions];
  const byCurrency = { EUR: [], USD: [] };
  allPositions.forEach(position => {
    const company = companiesByTicker[position.ticker];
    if (!company) return;
    const currency = currencyByIndex[company.index] === 'USD' ? 'USD' : 'EUR';
    byCurrency[currency].push(position);
  });
  const curves = {};
  Object.keys(byCurrency).forEach(currency => {
    curves[currency] = byCurrency[currency].length
      ? computePerformanceCurve(byCurrency[currency], priceHistoryByTicker, realPriceForPosition)
      : [];
  });
  return curves;
}

/**
 * Courbe benchmark pour un ensemble de positions donné : réutilise
 * EXACTEMENT les mêmes positions (mêmes buy_date/sell_date/cost) que
 * computePortfolioPerformanceCurves pour cette devise, seule la source
 * de prix change (indexTicker au lieu du ticker de chaque position) —
 * comparaison apples-to-apples.
 */
function computeBenchmarkPerformanceCurve(positions, indexTicker, priceHistoryByTicker) {
  return computePerformanceCurve(positions, priceHistoryByTicker, benchmarkPriceForPosition(indexTicker));
}
```

Ajoute les 7 nouveaux noms (`groupPriceHistoryByTicker`, `priceAtOrBefore`, `isPositionActiveOn`, `realPriceForPosition`, `benchmarkPriceForPosition`, `computePerformanceCurve`, `computePortfolioPerformanceCurves`, `computeBenchmarkPerformanceCurve` — 8 au total) au bloc `module.exports`.

- [ ] **Step 4 : Lancer les tests, vérifier qu'ils passent**

Run: `node docs/portfolio.test.js`
Expected: tous les tests passent, y compris les 9 nouveaux

- [ ] **Step 5 : Commit**

```bash
git add docs/portfolio.js docs/portfolio.test.js
git commit -m "Ajoute le calcul de la courbe de performance et de sa comparaison a un indice"
```

---

## Task 5 : UI — graphique et bouton "Vendre"

**Files:**
- Modify: `docs/index.html`
- Modify: `docs/service-worker.js`

**Interfaces:**
- Consumes : `computePortfolioPerformanceCurves`/`computeBenchmarkPerformanceCurve`/`closePosition`/`loadClosedPortfolio` (Tasks 3-4), `INDEX_YFINANCE_TICKERS`-équivalent côté frontend (à vérifier : le frontend a-t-il déjà cette liste, ou faut-il la dupliquer — voir Step 1), `INDEX_DISPLAY_NAMES` (déjà existant, utilisé ligne 1751/1908), `formatPct`/`pctClass`/`portfolioAnalysisHtml` (déjà existants).
- Produces : chargement de `docs/price_history.json`, fonction `performanceCurveSvg(curve, benchmarkCurve)`, section "Performance" ajoutée en tête de `portfolioAnalysisHtml`, bouton "Vendre" dans `portfolioPositionsHtml`/`wirePortfolioPositionActions`.

- [ ] **Step 1 : Vérifier la liste des indices benchmark côté frontend**

Avant d'écrire le sélecteur, cherche dans `docs/index.html` si une liste des 10 clés d'indices (`CAC40`, `DAX`, `NASDAQ`, `DOW`, `FTSE`, `SMI`, `IBEX35`, `FTSEMIB`, `NIKKEI225`, `HANGSENG`) et leur ticker yfinance correspondant (`^FCHI`, `^GDAXI`...) existe déjà côté JS (`grep -n "INDEX_YFINANCE_TICKERS\|CAC40.*FCHI" docs/index.html`). Si non, ajoute une petite constante miroir de celle d'`indices_score.py` (ligne ~2755) — même clés, mêmes valeurs, à placer près de `INDEX_DISPLAY_NAMES` :

```javascript
const INDEX_BENCHMARK_TICKERS = {
  CAC40: '^FCHI', DAX: '^GDAXI', NASDAQ: '^NDX', DOW: '^DJI',
  FTSE: '^FTSE', SMI: '^SSMI', IBEX35: '^IBEX', FTSEMIB: 'FTSEMIB.MI',
  NIKKEI225: '^N225', HANGSENG: '^HSI',
};
```

- [ ] **Step 2 : Charger `docs/price_history.json`**

Trouve `loadPortfolioScreen`'s fetch sequence (chercher `real_portfolio_mt5.json` dans `docs/index.html`, le fetch juste après est le bon point d'ancrage — motif `if (!xData) { try { const res = await fetch('....json?t=' + Date.now()); xData = res.ok ? await res.json() : ...; } catch (e) { ... } }`). Ajoute, avec le même motif, une variable module `let priceHistoryData = null;` et son chargement :

```javascript
if (!priceHistoryData) {
  try {
    const res = await fetch('price_history.json?t=' + Date.now());
    priceHistoryData = res.ok ? await res.json() : [];
  } catch (e) {
    priceHistoryData = [];
  }
}
```

Reproduis exactement l'endroit et le style du fetch déjà là pour `real_portfolio_mt5.json` (même fonction englobante, même ordre d'awaits).

- [ ] **Step 3 : Écrire `performanceCurveSvg`**

Ajoute dans `docs/index.html`, près de `portfolioAnalysisHtml` (ligne 1743) :

```javascript
function performanceCurveSvg(curve, benchmarkCurve, benchmarkLabel) {
  if (!curve.length) {
    return '<div class="empty">Pas assez de données pour tracer une courbe (au moins un cours connu depuis l\'achat est nécessaire).</div>';
  }
  const width = 600, height = 200, padding = 28;
  const allPoints = curve.concat(benchmarkCurve);
  const minPct = Math.min(0, ...allPoints.map(p => p.pnlPct));
  const maxPct = Math.max(0, ...allPoints.map(p => p.pnlPct));
  const range = maxPct - minPct || 1;
  const plotWidth = width - padding * 2;
  const plotHeight = height - padding * 2;
  const minDate = curve[0].date;
  const maxDate = curve[curve.length - 1].date;
  const dateSpanMs = new Date(maxDate) - new Date(minDate) || 1;
  const x = date => padding + ((new Date(date) - new Date(minDate)) / dateSpanMs) * plotWidth;
  const y = pct => padding + plotHeight - ((pct - minPct) / range) * plotHeight;
  const toPolyline = points => points.map(p => `${x(p.date).toFixed(1)},${y(p.pnlPct).toFixed(1)}`).join(' ');
  const lastPortfolio = curve[curve.length - 1];
  const portfolioColor = lastPortfolio.pnlPct >= 0 ? 'var(--green-500)' : 'var(--rust)';
  const zeroY = y(0).toFixed(1);
  return `
    <svg viewBox="0 0 ${width} ${height}" class="portfolio-perf-chart" role="img" aria-label="Courbe de performance du portefeuille">
      <line x1="${padding}" y1="${zeroY}" x2="${width - padding}" y2="${zeroY}" stroke="var(--muted)" stroke-dasharray="4 3" />
      ${benchmarkCurve.length ? `<polyline points="${toPolyline(benchmarkCurve)}" fill="none" stroke="var(--taupe-500)" stroke-width="1.5" />` : ''}
      <polyline points="${toPolyline(curve)}" fill="none" stroke="${portfolioColor}" stroke-width="2" />
    </svg>
    <div class="portfolio-perf-legend">
      <span style="color:${portfolioColor}">● Ton portefeuille (${formatPct(lastPortfolio.pnlPct)})</span>
      ${benchmarkCurve.length ? `<span style="color:var(--taupe-500)">● ${escHtml(benchmarkLabel)} (${formatPct(benchmarkCurve[benchmarkCurve.length - 1].pnlPct)})</span>` : ''}
    </div>`;
}
```

- [ ] **Step 4 : Ajouter le bloc "Performance" en tête de `portfolioAnalysisHtml`**

Modifie `portfolioAnalysisHtml` (ligne 1743-1779) : après la ligne `const attribution = computePortfolioAttribution(positions, companiesByTicker);` (ligne 1749) et avant `const diagnosis = ...` (ligne 1750), insère :

```javascript
  const closedPositions = loadClosedPortfolio();
  const priceHistoryByTicker = groupPriceHistoryByTicker(priceHistoryData || []);
  const performanceCurves = computePortfolioPerformanceCurves(positions, closedPositions, companiesByTicker, currencyByIndex, priceHistoryByTicker);
  const indexNamesForBenchmark = { ...INDEX_DISPLAY_NAMES, ...(indicesData.index_names || {}) };
  const benchmarkSelectHtml = currency => `
    <select class="list-sort portfolio-benchmark-select" data-currency="${currency}">
      ${Object.entries(INDEX_BENCHMARK_TICKERS).map(([key, yfTicker]) => `
        <option value="${yfTicker}" ${key === 'CAC40' ? 'selected' : ''}>${indexNamesForBenchmark[key] || key}</option>`).join('')}
    </select>`;
  const performanceHtml = ['EUR', 'USD'].filter(currency => performanceCurves[currency].length).map(currency => {
    const defaultBenchmarkTicker = INDEX_BENCHMARK_TICKERS.CAC40;
    const positionsForCurrency = [...positions, ...closedPositions].filter(p => {
      const company = companiesByTicker[p.ticker];
      return company && (currencyByIndex[company.index] === 'USD' ? 'USD' : 'EUR') === currency;
    });
    const benchmarkCurve = computeBenchmarkPerformanceCurve(positionsForCurrency, defaultBenchmarkTicker, priceHistoryByTicker);
    return `
      <h3 class="portfolio-analysis-title">Performance (${currency})</h3>
      <div class="portfolio-perf-block" data-currency="${currency}">
        ${benchmarkSelectHtml(currency)}
        <div class="portfolio-perf-chart-container">${performanceCurveSvg(performanceCurves[currency], benchmarkCurve, 'CAC40')}</div>
      </div>`;
  }).join('');
```

Puis, dans le `return` (ligne 1762-1778), ajoute `${performanceHtml}` juste avant `${diagnosis}`.

- [ ] **Step 5 : Câbler le changement de benchmark**

Ajoute une fonction `wirePortfolioPerformanceBenchmarkSelectors(positions, closedPositions, companiesByTicker, currencyByIndex, priceHistoryByTicker)` juste après `performanceCurveSvg` :

```javascript
function wirePortfolioPerformanceBenchmarkSelectors(positions, closedPositions, companiesByTicker, currencyByIndex, priceHistoryByTicker) {
  document.querySelectorAll('.portfolio-benchmark-select').forEach(select => {
    select.addEventListener('change', () => {
      const currency = select.dataset.currency;
      const curve = computePortfolioPerformanceCurves(positions, closedPositions, companiesByTicker, currencyByIndex, priceHistoryByTicker)[currency];
      const positionsForCurrency = [...positions, ...closedPositions].filter(p => {
        const company = companiesByTicker[p.ticker];
        return company && (currencyByIndex[company.index] === 'USD' ? 'USD' : 'EUR') === currency;
      });
      const benchmarkCurve = computeBenchmarkPerformanceCurve(positionsForCurrency, select.value, priceHistoryByTicker);
      const label = select.options[select.selectedIndex].textContent;
      const container = select.closest('.portfolio-perf-block').querySelector('.portfolio-perf-chart-container');
      container.innerHTML = performanceCurveSvg(curve, benchmarkCurve, label);
    });
  });
}
```

Câble-la depuis l'endroit où `portfolioAnalysisHtml` est utilisé dans la section "Analyse" de `portfolioActionsSections` (chercher `title: 'Analyse'` dans `renderPortfolioActionsTab`, ligne ~1809) : ajoute un `wire: () => wirePortfolioPerformanceBenchmarkSelectors(positions, loadClosedPortfolio(), companiesByTicker, currencyByIndex, groupPriceHistoryByTicker(priceHistoryData || []))` à cette entrée (elle n'a actuellement pas de `wire`).

- [ ] **Step 6 : Ajouter le CSS**

Ajoute dans le `<style>` de `docs/index.html`, près des règles `.signal-stats`/`.price-row` déjà existantes :

```css
.portfolio-perf-chart { width: 100%; height: auto; margin-top: 8px; }
.portfolio-perf-legend { display: flex; gap: 16px; font-size: var(--text-sm); color: var(--muted); margin-top: 4px; }
.portfolio-perf-block { margin-bottom: 16px; }
.portfolio-benchmark-select { margin-bottom: 8px; }
```

- [ ] **Step 7 : Ajouter le bouton "Vendre"**

Dans `portfolioPositionsHtml` (ligne 1897-1900), remplace :

```javascript
        <div class="portfolio-position-actions">
          <button class="toggle-btn portfolio-edit-btn" type="button" data-id="${position.id}">Modifier</button>
          <button class="toggle-btn portfolio-delete-btn" type="button" data-id="${position.id}">Supprimer</button>
        </div>
```

par :

```javascript
        <div class="portfolio-position-actions">
          <button class="toggle-btn portfolio-sell-btn" type="button" data-id="${position.id}">Vendre</button>
          <button class="toggle-btn portfolio-edit-btn" type="button" data-id="${position.id}">Modifier</button>
          <button class="toggle-btn portfolio-delete-btn" type="button" data-id="${position.id}">Supprimer</button>
        </div>
```

- [ ] **Step 8 : Câbler le bouton "Vendre"**

Dans `wirePortfolioPositionActions` (ligne 2626-2669), ajoute, après le bloc `.portfolio-delete-btn` (avant le bloc `.portfolio-edit-btn`) :

```javascript
  document.querySelectorAll('.portfolio-sell-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const row = document.querySelector(`.portfolio-position-row[data-id="${btn.dataset.id}"]`);
      const position = loadPortfolio().find(p => p.id === btn.dataset.id);
      if (!position || !row) return;
      const today = new Date().toISOString().slice(0, 10);
      row.innerHTML = `
        <div class="portfolio-form-row">
          <input class="list-search" type="number" placeholder="Prix de vente" id="portfolioSellPrice-${position.id}" min="0" step="any">
          <input class="list-search" type="date" value="${today}" id="portfolioSellDate-${position.id}">
        </div>
        <p class="portfolio-form-error" id="portfolioSellError-${position.id}" hidden></p>
        <div class="portfolio-position-actions">
          <button class="toggle-btn" type="button" id="portfolioSellConfirm-${position.id}">Confirmer la vente</button>
          <button class="toggle-btn" type="button" id="portfolioSellCancel-${position.id}">Annuler</button>
        </div>`;
      document.getElementById(`portfolioSellCancel-${position.id}`).addEventListener('click', () => renderPortfolioScreen());
      document.getElementById(`portfolioSellConfirm-${position.id}`).addEventListener('click', () => {
        const sellPrice = parseFloat(document.getElementById(`portfolioSellPrice-${position.id}`).value);
        const sellDate = document.getElementById(`portfolioSellDate-${position.id}`).value;
        const result = closePosition(position.id, sellPrice, sellDate);
        const errorEl = document.getElementById(`portfolioSellError-${position.id}`);
        if (!result.ok) {
          errorEl.textContent = result.error;
          errorEl.hidden = false;
          return;
        }
        renderPortfolioScreen();
      });
    });
  });
```

- [ ] **Step 9 : Bumper le cache du service worker**

Dans `docs/service-worker.js`, trouve `const CACHE_NAME = "analyse-or-shell-vNN";` et incrémente le numéro de 1 par rapport à sa valeur actuelle (vérifie la valeur réellement présente dans le fichier au moment de ce step, ne suppose pas un numéro fixe — d'autres commits ont pu l'avancer depuis l'écriture de ce plan).

- [ ] **Step 10 : Vérification par un vrai navigateur (Playwright)**

Il n'y a pas de runner JS DOM dans ce dépôt — ce script (à exécuter depuis le scratchpad, pas committé) sert `docs/` localement, seed un `docs/price_history.json` et un `localStorage` avec une position ouverte et une position clôturée, puis vérifie que le graphique se rend avec les deux courbes et que "Vendre" fonctionne :

```python
import json
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

DOCS = Path(r"C:\Users\alexa\OneDrive\Documents\GitHub\analyse-or\docs")  # adapte au worktree reel
PORT = 8201

FAKE_PRICE_HISTORY = [
    {"date": "2026-06-01", "ticker": "MC.PA", "price": 600.0},
    {"date": "2026-09-21", "ticker": "MC.PA", "price": 650.0},
    {"date": "2026-06-01", "ticker": "^FCHI", "price": 7500.0},
    {"date": "2026-09-21", "ticker": "^FCHI", "price": 7800.0},
]
(DOCS / "price_history.json").write_text(json.dumps(FAKE_PRICE_HISTORY), encoding="utf-8")

server = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT)], cwd=str(DOCS))
time.sleep(1)

try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(f"http://localhost:{PORT}/index.html")
        page.evaluate("""() => {
          localStorage.setItem('analyse-or-portfolio', JSON.stringify([
            {id: 'pos-1', ticker: 'MC.PA', quantity: 5, buy_price: 600.0, buy_date: '2026-06-01'}
          ]));
        }""")
        page.goto(f"http://localhost:{PORT}/index.html#portefeuille")
        page.wait_for_function("typeof portfolioActionsSections !== 'undefined' && portfolioActionsSections.length >= 4")
        page.evaluate("portfolioActionsSectionIndex = 3; renderPager('pfActions', portfolioActionsSections, 3);")
        page.wait_for_timeout(800)

        chart = page.locator(".portfolio-perf-chart")
        assert chart.count() >= 1, "graphique de performance absent"
        legend = page.locator(".portfolio-perf-legend").first.inner_text()
        assert "Ton portefeuille" in legend, "légende du portefeuille absente"
        assert "CAC40" in legend, "légende du benchmark absente"

        print("OK - graphique de performance rendu avec succes")
        browser.close()
finally:
    server.terminate()
    server.wait()
    (DOCS / "price_history.json").unlink(missing_ok=True)
```

Run ce script depuis le scratchpad (`python scratch_verify_performance_chart.py`). Expected: `OK - graphique de performance rendu avec succes` imprimé, aucune assertion levée. Si l'index "Analyse" (3) ne correspond pas à la structure réelle de `portfolioActionsSections` au moment de l'implémentation (une section a pu être ajoutée/retirée entre-temps), ajuste l'index avant de lancer — vérifie via `portfolioActionsSections.map(s => s.title)` en console si besoin.

- [ ] **Step 11 : Commit**

```bash
git add docs/index.html docs/service-worker.js
git commit -m "Ajoute le graphique de performance du portefeuille et le bouton Vendre"
```

---

## Étapes manuelles après merge

Aucune — contrairement au plan précédent (API Bot Actions), cette fonctionnalité ne touche à aucun secret ni service déployé. Le prochain run de `indices.yml` (déclenché par cron ou manuellement) suffit à faire apparaître `docs/price_history.json` et à commencer le backfill pour toutes les entreprises suivies.
