# Support des ETF dans l'onglet Indices — Plan d'implémentation

> **Pour les exécutants agentiques :** SOUS-COMPÉTENCE REQUISE : utiliser
> superpowers:subagent-driven-development (recommandé) ou
> superpowers:executing-plans pour exécuter ce plan tâche par tâche. Les
> étapes utilisent la syntaxe case à cocher (`- [ ]`) pour le suivi.

**Objectif :** ajouter ~20-25 ETF (Amundi + quelques émetteurs tiers,
MSCI/S&P) comme nouvelle catégorie "ETF" sélectionnable dans l'onglet
Indices, sans score composite inventé — à la place, de vraies métriques
ETF (TER, réplication, distribution, éligibilité PEA, écart de suivi).

**Architecture :** nouveau module Python `etf_score.py` (fetch + calcul,
chemin de données entièrement séparé de la logique actions — aucune
réutilisation de `fetch_company_financials`/`extract_ratios*`, qui
supposent un compte de résultat inexistant pour un ETF) + nouveau fichier
de données statiques `etf_catalog.py` (vérifié par recherche web, jamais
codé en dur de mémoire) + extension ciblée de `indices_score.py::main()`
et de 3 fonctions JS d'affichage de score dans `docs/index.html`.

**Tech Stack :** Python (yfinance, pytest/assert natif selon
`tests/test_indices_score.py`), JS vanilla (`docs/index.html`, pas de
framework, pas de convention de test pour ce fichier).

**Spec :** `docs/superpowers/specs/2026-11-12-etf-support-indices-design.md`

## Global Constraints

- **Aucune valeur financière inventée.** Ticker Euronext exact, ISIN, TER,
  méthode de réplication, politique de distribution, éligibilité PEA,
  ticker de l'indice de référence pour chaque ETF du catalogue — chaque
  champ doit être vérifié par recherche web au moment de l'écrire, jamais
  reconstitué de mémoire. Même règle pour les nouveaux tickers de
  benchmark ajoutés à `INDEX_YFINANCE_TICKERS` (MSCI World/EM/Europe/
  Japan/EMU/Or). Un reviewer qui repère une valeur non sourcée doit la
  traiter comme une erreur bloquante, pas un style à ajuster.
- **Pas de score composite pour les ETF.** Les champs `score`, `factors`,
  `score_raw`, `score_recalibrated`, `interpretation`, `sector`,
  `sector_profile`, `is_financial`, `is_trust`, `alerts` sont **absents**
  (pas présents avec une valeur `null`/`0`/`[]`) des entrées ETF dans
  `docs/indices.json`.
- **Les entrées ETF ne traversent jamais `recalibrate_scores_by_profile`
  ni `_attach_alerts_and_update_history`.** Ces deux fonctions font des
  accès directs (`c["score"]`, `company["score_raw"]`,
  `company["entry_price"]`...) qui lèveraient une `KeyError` sur une
  entrée ETF — vérifié dans le code réel avant ce plan (voir Tâche 6).
  Les entrées ETF ne rejoignent la liste `companies` qu'après ces deux
  appels, juste avant la construction du payload final.
- **ETF hors "Opportunités".** Aucun changement à `findOpportunities` ni à
  son UI. Les entrées ETF ne doivent jamais apparaître dans ce scanner —
  conséquence automatique de l'absence du champ `alerts`, à vérifier
  explicitement en Tâche 9, pas supposée.
- **Devise.** Les ETF retenus doivent être cotés en EUR sur leur place de
  cotation principale (Euronext Paris) — vérifié pendant la Tâche 1. Si un
  candidat n'est pas EUR, soit on le retire de la liste, soit on le signale
  explicitement comme hors scope v1 ; pas de logique de conversion de
  devise à construire pour ce chantier (`convertit_prix_entree` n'est pas
  réutilisée).
- **Dégradation, jamais d'exception qui casse tout le run.** Même
  convention que la boucle actions existante (`main()`, try/except par
  ticker) : un ETF dont le fetch échoue est loggé et absent du jour, le
  reste du run continue.
- **`docs/index.html` n'a pas de convention de test.** Les tâches qui le
  touchent se terminent par une vérification manuelle dans un vrai
  navigateur (serveur local `python -m http.server` + capture d'écran),
  pas par un test automatisé.

## Task 1: Catalogue ETF vérifié (`etf_catalog.py`)

**Fichiers :**
- Créer : `etf_catalog.py`
- Test : `tests/test_etf_catalog.py`

**Interfaces :**
- Produit : `ETF_CATALOG` (liste de dicts), forme exacte :
  ```python
  {
      "ticker": str,            # ticker Euronext exact (ex. "CW8.PA")
      "name": str,
      "isin": str,
      "benchmark_name": str,     # ex. "MSCI World"
      "benchmark_ticker": str,   # ticker yfinance de l'indice suivi
      "ter_pct": float,          # frais annuels, en %, ex. 0.38
      "replication": "physique" | "synthetique",
      "distribution": "capitalisant" | "distribuant",
      "pea_eligible": bool,
  }
  ```
  Consommé par la Tâche 4 (`build_etf_entry`) et la Tâche 6 (boucle
  `main()`).

- [ ] **Étape 1 : Rechercher et vérifier chaque ETF retenu**

  Par recherche web (site de l'émetteur, Boursorama, justETF ou
  équivalent — recouper au moins 2 sources par ETF pour TER/réplication/
  PEA), constituer une liste de 18 à 25 ETF couvrant :
  - Larges/géographiques : MSCI World, S&P 500, Nasdaq-100, MSCI Emerging
    Markets, MSCI Europe, MSCI Japan (1 ETF par exposition, Amundi en
    priorité)
  - Zone euro/France : CAC 40, Euro Stoxx 50, MSCI EMU
  - Matières premières : Or physique
  - 2-3 sectoriels (santé, technologie)
  - Au moins 1 ETF d'un émetteur autre qu'Amundi (iShares ou équivalent)
    suivant un indice déjà présent dans la liste, pour la diversité
    d'émetteur demandée explicitement par l'utilisateur.

  Pour chaque ETF retenu, noter la source consultée dans le message de
  commit (pas besoin de les garder dans le code, juste de pouvoir
  justifier chaque valeur si on te les redemande).

- [ ] **Étape 2 : Écrire `ETF_CATALOG`**

  Un dict par ETF, forme exacte ci-dessus. Vérifier qu'aucun ticker n'est
  dupliqué et que `benchmark_ticker` est un vrai ticker yfinance valide
  (format `^XXX` pour un indice, ou le ticker d'un ETF/future servant de
  proxy si l'indice lui-même n'a pas de ticker yfinance direct — à
  documenter en commentaire au-dessus de l'entrée concernée si c'est le
  cas).

- [ ] **Étape 3 : Écrire le test structurel**

  ```python
  # tests/test_etf_catalog.py
  from etf_catalog import ETF_CATALOG

  def test_etf_catalog_has_between_18_and_25_entries():
      assert 18 <= len(ETF_CATALOG) <= 25

  def test_etf_catalog_tickers_are_unique():
      tickers = [e["ticker"] for e in ETF_CATALOG]
      assert len(tickers) == len(set(tickers))

  def test_etf_catalog_entries_have_required_fields():
      required = {
          "ticker", "name", "isin", "benchmark_name", "benchmark_ticker",
          "ter_pct", "replication", "distribution", "pea_eligible",
      }
      for entry in ETF_CATALOG:
          assert required.issubset(entry.keys()), entry["ticker"]

  def test_etf_catalog_enum_fields_are_valid():
      for entry in ETF_CATALOG:
          assert entry["replication"] in ("physique", "synthetique"), entry["ticker"]
          assert entry["distribution"] in ("capitalisant", "distribuant"), entry["ticker"]
          assert isinstance(entry["pea_eligible"], bool), entry["ticker"]
          assert isinstance(entry["ter_pct"], (int, float)) and 0 <= entry["ter_pct"] < 2, entry["ticker"]
  ```

- [ ] **Étape 4 : Lancer les tests**

  Run : `python -m pytest tests/test_etf_catalog.py -v`
  Attendu : PASS (4 tests)

- [ ] **Étape 5 : Commit**

  ```bash
  git add etf_catalog.py tests/test_etf_catalog.py
  git commit -m "feat(etf): catalogue statique vérifié des ETF suivis"
  ```

## Task 2: `etf_score.py::compute_tracking_difference`

**Fichiers :**
- Créer : `etf_score.py`
- Test : `tests/test_etf_score.py`

**Interfaces :**
- Consomme : rien (fonction pure).
- Produit : `compute_tracking_difference(etf_prices, benchmark_prices)
  -> float | None`, utilisé par la Tâche 4.

- [ ] **Étape 1 : Écrire le test qui échoue**

  ```python
  # tests/test_etf_score.py
  from etf_score import compute_tracking_difference

  def test_compute_tracking_difference_positive_when_etf_outperforms():
      # ETF +10% sur la période, indice +6% -> écart de +4 points
      etf_prices = [100.0, 110.0]
      benchmark_prices = [1000.0, 1060.0]
      result = compute_tracking_difference(etf_prices, benchmark_prices)
      assert result == pytest.approx(4.0, abs=0.01)

  def test_compute_tracking_difference_negative_when_etf_underperforms():
      etf_prices = [100.0, 103.0]
      benchmark_prices = [1000.0, 1060.0]
      result = compute_tracking_difference(etf_prices, benchmark_prices)
      assert result == pytest.approx(-3.0, abs=0.01)

  def test_compute_tracking_difference_none_when_series_too_short():
      assert compute_tracking_difference([100.0], [1000.0]) is None
      assert compute_tracking_difference([], []) is None

  def test_compute_tracking_difference_none_when_benchmark_starts_at_zero():
      assert compute_tracking_difference([100.0, 110.0], [0.0, 10.0]) is None
  ```

  (ajouter `import pytest` en tête du fichier si absent)

- [ ] **Étape 2 : Lancer le test, vérifier l'échec**

  Run : `python -m pytest tests/test_etf_score.py -v`
  Attendu : FAIL (`ModuleNotFoundError: No module named 'etf_score'`)

- [ ] **Étape 3 : Implémenter**

  ```python
  # etf_score.py
  """Pipeline de donnees pour les ETF suivis dans l'onglet Indices --
  chemin entierement separe de la logique de score des actions
  (fetch_company_financials/extract_ratios*) : un ETF n'a ni compte de
  resultat ni bilan exploitable de la meme facon, voir
  docs/superpowers/specs/2026-11-12-etf-support-indices-design.md."""


  def compute_tracking_difference(etf_prices: list[float], benchmark_prices: list[float]) -> float | None:
      """Performance de l'ETF moins performance de son indice de reference
      sur la meme periode (premier point vs dernier point des deux series),
      en points de pourcentage. None si l'une des deux series a moins de 2
      points ou si le prix de depart de l'indice est nul/negatif -- jamais
      une valeur approximee (voir Global Constraints)."""
      if len(etf_prices) < 2 or len(benchmark_prices) < 2:
          return None
      if benchmark_prices[0] <= 0 or etf_prices[0] <= 0:
          return None
      etf_perf_pct = (etf_prices[-1] / etf_prices[0] - 1.0) * 100
      benchmark_perf_pct = (benchmark_prices[-1] / benchmark_prices[0] - 1.0) * 100
      return etf_perf_pct - benchmark_perf_pct
  ```

- [ ] **Étape 4 : Lancer le test, vérifier le succès**

  Run : `python -m pytest tests/test_etf_score.py -v`
  Attendu : PASS (4 tests)

- [ ] **Étape 5 : Commit**

  ```bash
  git add etf_score.py tests/test_etf_score.py
  git commit -m "feat(etf): calcul de l'ecart de suivi (tracking difference)"
  ```

## Task 3: `etf_score.py::fetch_etf_data`

**Fichiers :**
- Modifier : `etf_score.py`
- Test : `tests/test_etf_score.py`

**Interfaces :**
- Consomme : `yfinance` (`yf.Ticker`), importé dans `etf_score.py` comme
  `import yfinance as yf` en tête de fichier (mock de test cible
  `etf_score.yf.Ticker`, **pas** `indices_score.yf` — modules distincts).
- Produit : `fetch_etf_data(ticker: str) -> dict` avec les clés
  `current_price: float`, `price_history: list[dict]` (forme
  `{"date": "YYYY-MM-DD", "close": float}`), `dividend_yield: float | None`.
  Consommé par la Tâche 4.
- Convention d'erreur : lève l'exception telle quelle en cas d'échec
  réseau/donnée manquante (même convention que
  `fetch_company_financials` dans `indices_score.py`, qui ne capture
  jamais ses propres erreurs — c'est l'appelant, dans la boucle `main()`,
  qui capture par ticker). Ne pas ajouter de try/except interne à cette
  fonction.

- [ ] **Étape 1 : Écrire les tests qui échouent**

  Même convention de mock que `tests/test_indices_score.py` (confirmée
  avant ce plan) : `monkeypatch.setattr(etf_score.yf, "Ticker", _FakeTicker)`
  avec une classe factice minimale n'exposant que `.info`/`.history(...)`.

  ```python
  # tests/test_etf_score.py (suite)
  import etf_score
  from etf_score import fetch_etf_data
  import pandas as pd

  class _FakeEtfTicker:
      def __init__(self, info, history_df):
          self.info = info
          self._history_df = history_df

      def history(self, period=None):
          return self._history_df

  def _fake_history_df(rows):
      # rows: list of (date_str, close)
      import pandas as pd
      idx = pd.to_datetime([r[0] for r in rows])
      return pd.DataFrame({"Close": [r[1] for r in rows]}, index=idx)

  def test_fetch_etf_data_returns_price_and_history(monkeypatch):
      history_df = _fake_history_df([("2026-11-01", 100.0), ("2026-11-02", 101.5)])
      fake = _FakeEtfTicker({"dividendYield": 0.015}, history_df)
      monkeypatch.setattr(etf_score.yf, "Ticker", lambda t: fake)
      result = fetch_etf_data("CW8.PA")
      assert result["current_price"] == pytest.approx(101.5)
      assert result["price_history"] == [
          {"date": "2026-11-01", "close": pytest.approx(100.0)},
          {"date": "2026-11-02", "close": pytest.approx(101.5)},
      ]
      assert result["dividend_yield"] == pytest.approx(0.015)

  def test_fetch_etf_data_dividend_yield_none_for_capitalizing_etf(monkeypatch):
      history_df = _fake_history_df([("2026-11-01", 100.0), ("2026-11-02", 101.5)])
      fake = _FakeEtfTicker({}, history_df)  # pas de cle dividendYield
      monkeypatch.setattr(etf_score.yf, "Ticker", lambda t: fake)
      result = fetch_etf_data("CW8.PA")
      assert result["dividend_yield"] is None

  def test_fetch_etf_data_raises_when_history_empty(monkeypatch):
      fake = _FakeEtfTicker({}, _fake_history_df([]))
      monkeypatch.setattr(etf_score.yf, "Ticker", lambda t: fake)
      with pytest.raises(ValueError):
          fetch_etf_data("CW8.PA")
  ```

- [ ] **Étape 2 : Lancer les tests, vérifier l'échec**

  Run : `python -m pytest tests/test_etf_score.py -v`
  Attendu : les 3 nouveaux tests FAIL (`AttributeError`/`ImportError` --
  `fetch_etf_data` n'existe pas encore)

- [ ] **Étape 3 : Implémenter**

  ```python
  # etf_score.py (ajouter en tête)
  import yfinance as yf

  # (compute_tracking_difference déjà présent, inchangé)

  def fetch_etf_data(ticker: str) -> dict:
      """Donnees dynamiques d'un ETF (prix, historique, rendement du
      dividende si distribuant) -- chemin independant de
      fetch_company_financials, qui suppose un compte de resultat
      inexistant pour un ETF. Leve l'exception telle quelle en cas
      d'echec (meme convention que fetch_company_financials) ; c'est a
      l'appelant (boucle main()) de capturer par ticker."""
      t = yf.Ticker(ticker)
      info = t.info
      history_df = t.history(period="2y")
      if history_df.empty:
          raise ValueError(f"Historique de prix vide pour {ticker}")
      price_history = [
          {"date": ts.strftime("%Y-%m-%d"), "close": float(close)}
          for ts, close in history_df["Close"].items()
      ]
      dividend_yield = info.get("dividendYield")
      return {
          "current_price": price_history[-1]["close"],
          "price_history": price_history,
          "dividend_yield": float(dividend_yield) if dividend_yield is not None else None,
      }
  ```

- [ ] **Étape 4 : Lancer les tests, vérifier le succès**

  Run : `python -m pytest tests/test_etf_score.py -v`
  Attendu : PASS (7 tests au total)

- [ ] **Étape 5 : Commit**

  ```bash
  git add etf_score.py tests/test_etf_score.py
  git commit -m "feat(etf): fetch_etf_data -- prix, historique, dividende"
  ```

## Task 4: `etf_score.py::build_etf_entry`

**Fichiers :**
- Modifier : `etf_score.py`
- Test : `tests/test_etf_score.py`

**Interfaces :**
- Consomme : `fetch_etf_data` (Tâche 3), `compute_tracking_difference`
  (Tâche 2), une entrée de `ETF_CATALOG` (Tâche 1).
- Produit : `build_etf_entry(catalog_entry: dict, benchmark_price_history:
  list[dict] | None) -> dict`, forme exacte :
  ```python
  {
      "ticker": str, "name": str, "index": "ETF",
      "current_price": float, "news": [],
      "is_etf": True,
      "etf_info": {
          "benchmark_name": str, "ter_pct": float, "replication": str,
          "distribution": str, "pea_eligible": bool,
          "dividend_yield": float | None,
          "tracking_difference_pct": float | None,
      },
      "_price_history_daily": [...],  # forme compatible avec
                                       # update_price_history, voir Tâche 6
  }
  ```
  `news` reste une liste vide en v1 (pas de pipeline d'actualités dédié
  aux ETF dans ce chantier — absent du périmètre de la spec, pas un
  oubli). Consommé par la Tâche 6.

- [ ] **Étape 1 : Écrire les tests qui échouent**

  ```python
  # tests/test_etf_score.py (suite)
  from etf_score import build_etf_entry

  _CATALOG_ENTRY = {
      "ticker": "CW8.PA", "name": "Amundi MSCI World", "isin": "LU1681043599",
      "benchmark_name": "MSCI World", "benchmark_ticker": "URTH",
      "ter_pct": 0.38, "replication": "physique",
      "distribution": "capitalisant", "pea_eligible": True,
  }

  def test_build_etf_entry_basic_shape(monkeypatch):
      monkeypatch.setattr(etf_score, "fetch_etf_data", lambda ticker: {
          "current_price": 450.0,
          "price_history": [{"date": "2026-11-01", "close": 440.0}, {"date": "2026-11-02", "close": 450.0}],
          "dividend_yield": None,
      })
      entry = build_etf_entry(_CATALOG_ENTRY, benchmark_price_history=None)
      assert entry["ticker"] == "CW8.PA"
      assert entry["index"] == "ETF"
      assert entry["is_etf"] is True
      assert entry["current_price"] == 450.0
      assert entry["news"] == []
      assert "score" not in entry
      assert "factors" not in entry
      assert entry["etf_info"]["ter_pct"] == 0.38
      assert entry["etf_info"]["pea_eligible"] is True
      assert entry["etf_info"]["dividend_yield"] is None
      assert entry["etf_info"]["tracking_difference_pct"] is None  # pas de benchmark fourni

  def test_build_etf_entry_computes_tracking_difference_when_benchmark_given(monkeypatch):
      monkeypatch.setattr(etf_score, "fetch_etf_data", lambda ticker: {
          "current_price": 110.0,
          "price_history": [{"date": "2026-11-01", "close": 100.0}, {"date": "2026-11-02", "close": 110.0}],
          "dividend_yield": None,
      })
      benchmark_history = [{"date": "2026-11-01", "close": 1000.0}, {"date": "2026-11-02", "close": 1060.0}]
      entry = build_etf_entry(_CATALOG_ENTRY, benchmark_price_history=benchmark_history)
      assert entry["etf_info"]["tracking_difference_pct"] == pytest.approx(4.0, abs=0.01)

  def test_build_etf_entry_includes_price_history_for_pipeline(monkeypatch):
      monkeypatch.setattr(etf_score, "fetch_etf_data", lambda ticker: {
          "current_price": 450.0,
          "price_history": [{"date": "2026-11-01", "close": 440.0}],
          "dividend_yield": None,
      })
      entry = build_etf_entry(_CATALOG_ENTRY, benchmark_price_history=None)
      assert entry["_price_history_daily"] == [
          {"ticker": "CW8.PA", "date": "2026-11-01", "close": 440.0}
      ]
  ```

  La forme exacte de `_price_history_daily` (liste de dicts
  `{"ticker", "date", "close"}`) doit être confirmée contre la forme
  réellement produite par `build_company_entry`/`update_price_history`
  dans `indices_score.py` avant d'écrire ce test — lire le code réel
  (`data["_price_history_daily"]` et `update_price_history`) plutôt que
  de supposer cette forme correcte par construction.

- [ ] **Étape 2 : Lancer les tests, vérifier l'échec**

  Run : `python -m pytest tests/test_etf_score.py -v`
  Attendu : les 3 nouveaux tests FAIL

- [ ] **Étape 3 : Implémenter**

  ```python
  # etf_score.py (ajouter)
  def build_etf_entry(catalog_entry: dict, benchmark_price_history: list[dict] | None) -> dict:
      """Assemble l'entree finale d'un ETF pour docs/indices.json. Jamais
      de score/factors (voir Global Constraints du plan) -- etf_info
      porte les metriques reelles a la place."""
      data = fetch_etf_data(catalog_entry["ticker"])
      etf_prices = [p["close"] for p in data["price_history"]]
      benchmark_prices = (
          [p["close"] for p in benchmark_price_history] if benchmark_price_history else None
      )
      tracking_difference = (
          compute_tracking_difference(etf_prices, benchmark_prices)
          if benchmark_prices else None
      )
      return {
          "ticker": catalog_entry["ticker"],
          "name": catalog_entry["name"],
          "index": "ETF",
          "current_price": data["current_price"],
          "news": [],
          "is_etf": True,
          "etf_info": {
              "benchmark_name": catalog_entry["benchmark_name"],
              "ter_pct": catalog_entry["ter_pct"],
              "replication": catalog_entry["replication"],
              "distribution": catalog_entry["distribution"],
              "pea_eligible": catalog_entry["pea_eligible"],
              "dividend_yield": data["dividend_yield"],
              "tracking_difference_pct": tracking_difference,
          },
          "_price_history_daily": [
              {"ticker": catalog_entry["ticker"], "date": p["date"], "close": p["close"]}
              for p in data["price_history"]
          ],
      }
  ```

  Ajuster la forme de `_price_history_daily` si la lecture du code réel
  (Étape 1) montre une forme différente de celle supposée ici.

- [ ] **Étape 4 : Lancer les tests, vérifier le succès**

  Run : `python -m pytest tests/test_etf_score.py -v`
  Attendu : PASS (10 tests au total)

- [ ] **Étape 5 : Commit**

  ```bash
  git add etf_score.py tests/test_etf_score.py
  git commit -m "feat(etf): build_etf_entry -- assemble l'entree finale sans score invente"
  ```

## Task 5: Benchmarks manquants + clés d'index (`indices_score.py`)

**Fichiers :**
- Modifier : `indices_score.py`

**Interfaces :**
- Consomme : aucune des tâches précédentes.
- Produit : `INDEX_YFINANCE_TICKERS`, `INDEX_NAMES`, `INDEX_CURRENCY`
  étendus — consommés par la Tâche 6 et par `fetch_index_prices()`/
  `fetch_index_price_history()` (inchangées, génériques sur ces dicts).

- [ ] **Étape 1 : Vérifier l'innocuité de l'extension**

  Chercher tous les usages de `INDEX_YFINANCE_TICKERS` et `INDEX_CURRENCY`
  dans `indices_score.py` (`grep -n "INDEX_YFINANCE_TICKERS\|INDEX_CURRENCY"`)
  et confirmer qu'aucun ne suppose qu'une clé de ces dicts a forcément des
  entreprises constituantes dans `COMPANIES` (risque identifié avant ce
  plan, pas vérifié formellement). Si un tel usage existe, l'adapter pour
  ignorer silencieusement une clé sans société associée plutôt que de
  continuer sans vérifier.

- [ ] **Étape 2 : Rechercher les tickers yfinance manquants**

  Par recherche web, trouver un ticker yfinance utilisable comme proxy de
  niveau d'indice pour chaque benchmark de la Tâche 1 pas déjà couvert par
  `INDEX_YFINANCE_TICKERS` (probablement : MSCI World, MSCI Emerging
  Markets, MSCI Europe, MSCI Japan, MSCI EMU, Or). Un indice MSCI n'a
  souvent pas de ticker `^...` direct chez yfinance — un ETF représentatif
  très liquide (ex. `URTH` pour MSCI World, `GLD` pour l'or) est un proxy
  acceptable ; documenter ce choix en commentaire à côté de l'entrée.

- [ ] **Étape 3 : Étendre les dicts**

  Ajouter les clés manquantes à `INDEX_YFINANCE_TICKERS` (trouvé Tâche 1
  du plan de recherche initial, ligne à relocaliser par grep au moment de
  l'implémentation plutôt que de supposer un numéro de ligne figé), et
  `"ETF": "ETF"` à `INDEX_NAMES`, `"ETF": "EUR"` à `INDEX_CURRENCY` (sous
  réserve de la contrainte devise du plan — à ajuster si un ETF retenu en
  Tâche 1 n'est finalement pas EUR).

- [ ] **Étape 4 : Vérifier qu'aucun test existant ne casse**

  Run : `python -m pytest tests/test_indices_score.py -v`
  Attendu : aucune régression (les nouvelles clés sont additives)

- [ ] **Étape 5 : Commit**

  ```bash
  git add indices_score.py
  git commit -m "feat(etf): etend INDEX_YFINANCE_TICKERS/INDEX_NAMES/INDEX_CURRENCY pour les benchmarks ETF"
  ```

## Task 6: Brancher les ETF dans `main()` (`indices_score.py`)

**Fichiers :**
- Modifier : `indices_score.py`
- Test : `tests/test_indices_score.py`

**Interfaces :**
- Consomme : `ETF_CATALOG` (Tâche 1), `build_etf_entry` (Tâche 4).
- Produit : `_merge_etf_entries(companies, etf_companies) -> list[dict]`
  (helper testable isolément — voir pourquoi ci-dessous), branché dans
  `main()`.

**Contexte critique (vérifié dans le code réel avant ce plan, à ne pas
re-découvrir par essai-erreur) :** dans `main()`, après la boucle
`COMPANIES` (où `companies` est peuplée), trois appels successifs lisent
des clés obligatoires absentes d'une entrée ETF et lèveraient une
`KeyError` si une entrée ETF s'y trouvait :
- `recalibrate_scores_by_profile(companies)` fait `c["score"]` (accès
  direct, sans `.get`).
- `_attach_alerts_and_update_history(companies)` appelle
  `compute_company_alerts(..., company["score_raw"], ...,
  company["entry_price"], ...)`.
- `_compute_health_summary(companies)` compare `companies` à `COMPANIES`
  (liste actions uniquement) — une entrée ETF y apparaîtrait comme un
  ticker "inattendu", faussant le résumé de complétude sans planter.

**Conséquence de conception :** les entrées ETF doivent être construites
dans une liste séparée (`etf_companies`), collectées pour
`_price_history_daily` en même temps que les actions (pour profiter du
pipeline `update_price_history` existant), mais **fusionnées dans
`companies` seulement après** les trois appels ci-dessus — jamais avant.

- [ ] **Étape 1 : Écrire le test qui échoue pour le helper de fusion**

  ```python
  # tests/test_indices_score.py (ajouter)
  from indices_score import _merge_etf_entries

  def test_merge_etf_entries_appends_without_mutating_inputs():
      companies = [{"ticker": "MC.PA", "score": 10}]
      etf_companies = [{"ticker": "CW8.PA", "is_etf": True}]
      result = _merge_etf_entries(companies, etf_companies)
      assert result == [{"ticker": "MC.PA", "score": 10}, {"ticker": "CW8.PA", "is_etf": True}]
      assert companies == [{"ticker": "MC.PA", "score": 10}]  # inchangee
      assert etf_companies == [{"ticker": "CW8.PA", "is_etf": True}]  # inchangee
  ```

- [ ] **Étape 2 : Lancer le test, vérifier l'échec**

  Run : `python -m pytest tests/test_indices_score.py -k merge_etf -v`
  Attendu : FAIL (`ImportError`, `_merge_etf_entries` n'existe pas)

- [ ] **Étape 3 : Implémenter le helper**

  ```python
  # indices_score.py (ajouter près de _compute_health_summary)
  def _merge_etf_entries(companies: list[dict], etf_companies: list[dict]) -> list[dict]:
      """Fusionne les entrees ETF dans la liste finale -- a n'appeler
      qu'APRES recalibrate_scores_by_profile/_attach_alerts_and_update_history/
      _compute_health_summary (voir Tache 6 du plan ETF) : ces trois
      fonctions font des acces directs a des cles absentes d'une entree
      ETF (score, score_raw, entry_price) et leveraient une KeyError."""
      return companies + etf_companies
  ```

- [ ] **Étape 4 : Lancer le test, vérifier le succès**

  Run : `python -m pytest tests/test_indices_score.py -k merge_etf -v`
  Attendu : PASS

- [ ] **Étape 5 : Brancher dans `main()`**

  Localiser par grep (`def main()`, puis la boucle `for company in
  COMPANIES:` suivie de `price_history_entries = []`) plutôt que de se
  fier à un numéro de ligne figé. Insérer, immédiatement après la boucle
  actions existante et avant `price_history_entries = []` :

  ```python
      etf_companies = []
      for etf in ETF_CATALOG:
          try:
              benchmark_history = None  # cf. note ci-dessous
              etf_companies.append(build_etf_entry(etf, benchmark_history))
          except Exception as e:
              print(f"Erreur pour {etf['ticker']} ({etf['name']}) : {e}")
              traceback.print_exc()
  ```

  Modifier la collecte de `price_history_entries` pour inclure aussi
  `etf_companies` :

  ```python
      price_history_entries = []
      for c in companies:
          price_history_entries.extend(c.pop("_price_history_daily", []))
      for c in etf_companies:
          price_history_entries.extend(c.pop("_price_history_daily", []))
      price_history_entries.extend(fetch_index_price_history())
      update_price_history(price_history_entries)
  ```

  Laisser `recalibrate_scores_by_profile(companies)`,
  `_attach_alerts_and_update_history(companies)` et
  `_compute_health_summary(companies)` **inchangés** (ils continuent de ne
  voir que `companies`, jamais `etf_companies`).

  Juste avant la construction de `payload`, fusionner :

  ```python
      companies = _merge_etf_entries(companies, etf_companies)
  ```

  puis `"companies": companies` dans `payload` reste inchangé (il contient
  maintenant actions + ETF).

  **Note sur `benchmark_history` ci-dessus** : pour le v1, lire l'historique
  de prix de l'indice déjà présent en mémoire à ce stade de `main()` si un
  mécanisme existant l'expose facilement (vérifier `fetch_index_price_history()`,
  déjà appelée juste en dessous) ; sinon, passer `None` pour tous les ETF
  dont le benchmark n'a pas d'historique déjà chargé à ce point du run, ce
  qui fait simplement remonter `tracking_difference_pct: None` pour ces
  ETF (dégradation prévue par `build_etf_entry`, pas un bug) plutôt que de
  construire une requête réseau supplémentaire dans ce chantier.

- [ ] **Étape 6 : Import en tête de fichier**

  Ajouter `from etf_catalog import ETF_CATALOG` et
  `from etf_score import build_etf_entry` en tête de `indices_score.py`,
  à côté des imports existants.

- [ ] **Étape 7 : Lancer toute la suite de tests**

  Run : `python -m pytest tests/ -v`
  Attendu : aucune régression, nouveaux tests PASS.

- [ ] **Étape 8 : Commit**

  ```bash
  git add indices_score.py tests/test_indices_score.py
  git commit -m "feat(etf): branche les ETF dans main() sans jamais les exposer au scoring actions"
  ```

## Task 7: Frontend — rendre les listes/tri sûrs pour une entrée sans score

**Fichiers :**
- Modifier : `docs/index.html`

**Interfaces :**
- Consomme : `company.is_etf`, `company.etf_info` (Tâche 6, une fois
  `docs/indices.json` régénéré — en local, peut être simulé en injectant
  une entrée ETF factice dans `indicesData.companies` via la console
  navigateur pour tester sans attendre un vrai run du pipeline).

- [ ] **Étape 1 : Sécuriser le tri par score**

  Dans `sortedFilteredCompanies` (chercher par le nom de fonction, pas par
  numéro de ligne), remplacer :

  ```js
  if (s.sort === 'score_desc') visible.sort((a, b) => b.score - a.score);
  else if (s.sort === 'score_asc') visible.sort((a, b) => a.score - b.score);
  ```

  par :

  ```js
  if (s.sort === 'score_desc') visible.sort((a, b) => (b.score ?? -Infinity) - (a.score ?? -Infinity));
  else if (s.sort === 'score_asc') visible.sort((a, b) => (a.score ?? Infinity) - (b.score ?? Infinity));
  ```

  (une entrée sans score trie toujours en dernier, peu importe le sens —
  pas d'ordre indéfini sur un comparateur `NaN`)

- [ ] **Étape 2 : Corriger le badge de score dans `peekRowsHtml`**

  Remplacer le corps de la fonction (chercher `function peekRowsHtml`) :

  ```js
  function peekRowsHtml(companies) {
    const top3 = sortedFilteredCompanies(companies).slice(0, 3);
    if (!top3.length) return '<p class="index-card-peek-empty">Aucun résultat pour ce filtre.</p>';
    return top3.map(c => `
      <a class="index-card-peek-row" href="#indices/${c.ticker}">
        <span class="index-card-peek-name">${c.name}</span>
        ${typeof c.score === 'number'
          ? `<span class="index-card-peek-score" style="color:${c.score >= 0 ? 'var(--green-500)' : 'var(--rust)'}">${c.score > 0 ? '+' : ''}${c.score}</span>`
          : (c.is_etf ? `<span class="index-card-peek-score" style="color:var(--muted)">TER ${c.etf_info.ter_pct}%</span>` : '')}
      </a>`).join('');
  }
  ```

- [ ] **Étape 3 : Corriger le badge de score dans `renderIndicesRows`**

  Remplacer (chercher `function renderIndicesRows`) :

  ```js
  <span class="company-score" style="color:${c.score >= 0 ? 'var(--green-500)' : 'var(--rust)'};">
    ${c.score > 0 ? '+' : ''}${c.score}
  </span>
  ```

  par :

  ```js
  ${typeof c.score === 'number'
    ? `<span class="company-score" style="color:${c.score >= 0 ? 'var(--green-500)' : 'var(--rust)'};">${c.score > 0 ? '+' : ''}${c.score}</span>`
    : (c.is_etf ? `<span class="company-score" style="color:var(--muted);font-size:var(--text-xs);">TER ${c.etf_info.ter_pct}%</span>` : '')}
  ```

- [ ] **Étape 4 : Vérification manuelle**

  Démarrer un serveur local (`python -m http.server` depuis `docs/`),
  injecter une fausse entrée ETF via la console navigateur
  (`indicesData.companies.push({ticker:"TEST.ETF", name:"Test ETF",
  index:"ETF", current_price: 100, news: [], is_etf: true, etf_info:
  {ter_pct: 0.3, pea_eligible: true, replication: "physique",
  distribution: "capitalisant", benchmark_name: "MSCI World",
  tracking_difference_pct: null}})`), ré-appeler `renderIndicesList`, et
  confirmer : pas de "undefined" affiché, le groupe "ETF" apparaît, le
  badge montre "TER 0.3%" en couleur neutre plutôt qu'un score rouge.
  Capturer un screenshot dans les deux thèmes (clair/sombre).

- [ ] **Étape 5 : Commit**

  ```bash
  git add docs/index.html
  git commit -m "fix(indices): rend le tri et les badges de score surs pour une entree sans score (ETF)"
  ```

## Task 8: Frontend — fiche détail ETF

**Fichiers :**
- Modifier : `docs/index.html`

**Interfaces :**
- Consomme : `company.is_etf`, `company.etf_info` (comme Tâche 7),
  `loadRealPriceChart(company)` (fonction existante, réutilisée telle
  quelle), le bloc `newsHtml` existant (réutilisé tel quel, `company.news`
  vaut `[]` pour un ETF en v1 — affiche "Aucune actualité récente.").

- [ ] **Étape 1 : Lire le début réel de `renderCompanyDetail`**

  Chercher `function renderCompanyDetail(company)` et lire ses toutes
  premières lignes dans le fichier réel (pas supposer leur contenu) pour
  confirmer le point d'insertion exact — le branchement `is_etf` doit être
  la toute première instruction de la fonction, avant tout accès à
  `company.factors` (qui plante immédiatement sur une entrée ETF, déjà
  vérifié avant ce plan).

- [ ] **Étape 2 : Écrire `renderEtfDetail(company)`**

  Nouvelle fonction, juste avant `renderCompanyDetail` :

  ```js
  function renderEtfDetail(company) {
    const info = company.etf_info || {};
    const newsHtml = (company.news || []).map(n => `
      <div class="news-item">
        <a class="news-title" href="${n.link}" target="_blank" rel="noopener">${n.title}</a>
        <div class="news-meta">${[n.source, n.date].filter(Boolean).join(' · ')}</div>
        ${n.summary ? `<p class="news-summary">${n.summary}</p>` : ''}
      </div>`).join('');

    document.getElementById('indicesContent').innerHTML = `
      <button class="back-link" onclick="history.back()">← Retour</button>
      <h1>${company.name}</h1>
      <p class="hero-sub">${company.ticker} — ETF</p>
      <div id="etfPriceChart" class="chart-container"></div>
      <h2>Caractéristiques</h2>
      <div class="signal-stats">
        <div class="price-row"><span class="price-label">Indice suivi</span><span class="price-value">${info.benchmark_name || '—'}</span></div>
        <div class="price-row"><span class="price-label">Frais de gestion (TER)</span><span class="price-value">${info.ter_pct != null ? info.ter_pct + '%' : '—'}</span></div>
        <div class="price-row"><span class="price-label">Réplication</span><span class="price-value">${info.replication || '—'}</span></div>
        <div class="price-row"><span class="price-label">Distribution</span><span class="price-value">${info.distribution || '—'}</span></div>
        <div class="price-row"><span class="price-label">Éligible PEA</span><span class="price-value">${info.pea_eligible ? 'Oui' : 'Non'}</span></div>
        <div class="price-row"><span class="price-label">Écart de suivi</span><span class="price-value ${pctClass(info.tracking_difference_pct)}">${info.tracking_difference_pct != null ? formatPct(info.tracking_difference_pct) : 'Indisponible'}</span></div>
      </div>
      <h2>Actualités récentes</h2>
      ${newsHtml || '<p class="hero-sub">Aucune actualité récente.</p>'}
    `;
    loadRealPriceChart(company);
  }
  ```

  Adapter les classes CSS exactes (`.chart-container`, `.back-link`, etc.)
  à ce qui existe réellement dans `docs/index.html` pour le reste de la
  fiche action — lire `renderCompanyDetail` avant d'écrire cette fonction
  pour copier les classes réellement utilisées plutôt que d'en inventer.

- [ ] **Étape 3 : Brancher l'early return**

  Première ligne du corps de `renderCompanyDetail` :

  ```js
  function renderCompanyDetail(company) {
    if (company.is_etf) { renderEtfDetail(company); return; }
    // ... reste de la fonction inchangé
  ```

- [ ] **Étape 4 : Vérification manuelle**

  Même méthode qu'à la Tâche 7 (injection d'une fausse entrée ETF dans
  `indicesData.companies` via la console), naviguer vers sa fiche
  (`#indices/TEST.ETF`), confirmer : pas de plantage, pas de jauge de
  score ni de barres de facteurs affichées, le panneau Caractéristiques
  montre les bonnes valeurs, le graphique de prix se charge. Capturer un
  screenshot dans les deux thèmes.

- [ ] **Étape 5 : Commit**

  ```bash
  git add docs/index.html
  git commit -m "feat(indices): fiche detail dediee pour un ETF (sans score invente)"
  ```

## Task 9: Revue finale + vérification de bout en bout

- [ ] **Étape 1 : Revue de branche complète**

  Dispatcher une revue de code sur l'ensemble du diff de la branche
  (`superpowers:requesting-code-review`, modèle le plus capable
  disponible) — vérifier en particulier : aucune valeur du catalogue ETF
  non sourcée, aucun chemin où une entrée ETF atteint
  `recalibrate_scores_by_profile`/`_attach_alerts_and_update_history`,
  aucun `undefined` résiduel affichable côté frontend.

- [ ] **Étape 2 : Run réel limité**

  Lancer `indices_score.py` en limitant temporairement `COMPANIES` à
  quelques tickers (ou via un flag de dry-run s'il en existe déjà un — à
  vérifier avant d'improviser une limite manuelle) pour confirmer que la
  boucle ETF tourne sans exception sur au moins 3 vrais tickers du
  catalogue, puis annuler la limitation avant de merger (ne jamais
  commiter une liste `COMPANIES` tronquée).

- [ ] **Étape 3 : Vérification manuelle complète dans un vrai navigateur**

  Avec un vrai `docs/indices.json` régénéré (ou la donnée de test
  injectée si le run complet est trop long/coûteux pour cette étape) :
  onglet Indices → catégorie "ETF" apparaît dans le sélecteur, liste
  correcte, fiche détail correcte, et confirmation qu'aucun ETF
  n'apparaît dans "Opportunités". Deux thèmes, capture d'écran de chaque
  état pour le compte-rendu final.

- [ ] **Étape 4 : Nettoyage de l'espace de travail du plan**

  Une fois la revue finale propre, suivre
  `superpowers:finishing-a-development-branch` pour merger/pousser (avec
  l'accord explicite de l'utilisateur à ce moment-là, même convention que
  les chantiers précédents de cette session) et supprimer l'espace de
  travail `.superpowers/sdd/` dédié à ce plan.
