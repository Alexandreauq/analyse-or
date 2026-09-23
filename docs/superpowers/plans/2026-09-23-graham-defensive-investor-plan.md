# Badge "Investisseur défensif" (Graham) — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Calculer un badge "Investisseur défensif" (Benjamin Graham) par société suivie, à partir de 6-7 critères chiffrés adaptés aux données réellement disponibles, exposé comme information indépendante du score composite.

**Architecture:** Tout dans `indices_score.py` (fichier unique existant, convention du dépôt). Nouvelles fonctions pures pour le streak de dividendes et la stabilité des bénéfices, extension de `extract_ratios`/`extract_ratios_financial` (P/B universel, ratio de liquidité), un nouvel appel réseau (`Ticker.dividends`) wiré dans `fetch_company_financials`, et un combinateur final wiré dans `build_company_entry`.

**Tech Stack:** Python, pandas, yfinance, pytest.

**Spec:** `docs/superpowers/specs/2026-09-23-graham-defensive-investor-design.md`

## Global Constraints

- Aucune exception ne doit jamais remonter d'un calcul lié à Graham — toujours un repli gracieux, même contrat que Weinstein (`data_available=False` pattern déjà établi dans ce fichier).
- Le badge est purement informatif : aucun impact sur le score composite (`score_dynamique_recente` et `compute_composite` restent inchangés) ni sur le déclenchement des alertes (`compute_company_alerts` reste inchangée).
- Le ratio de liquidité (`current_ratio`) n'est calculé QUE pour le profil standard (`extract_ratios`) — jamais pour financier/trust (`extract_ratios_financial`), qui n'ont pas cette notion au sens classique.
- `dividend_streak_years`/`no_loss_years` sont calculés pour LES DEUX profils.
- Le badge est un test strict : `eligible = all(criteria.values())`, jamais un score compté sur N — décision explicite de l'utilisateur pour ne pas reproduire le problème de calibrage déjà identifié sur le score composite principal.
- `graham_defensive.criteria` ne doit jamais contenir la clé `structure_financiere` pour une société financière/trust — 5 clés pour ce profil, 6 pour le profil standard (voir spec §4.2).
- Les paramètres/champs nouveaux doivent avoir un défaut qui préserve le comportement actuel pour tout appelant/test existant qui ne les fournit pas — ne pas forcer un sweep de tous les tests existants du fichier (`tests/test_indices_score.py` en compte plus de 370).

---

### Task 1: Fonctions pures — streak de dividendes et stabilité des bénéfices

**Files:**
- Modify: `indices_score.py` (nouvelles fonctions, à insérer juste après `_window_average` et juste avant `def extract_ratios` — cherche `def extract_ratios(financials, balance_sheet, cashflow, closes_by_year, shares_outstanding: float) -> dict:` pour te positionner, insère juste au-dessus)
- Test: `tests/test_indices_score.py` (nouveau bloc, à ajouter juste avant `def extract_ratios(...)` n'existe pas dans les tests — cherche plutôt `def test_extract_ratios_computes_expected_keys` et insère juste avant cette fonction)

**Interfaces:**
- Produces: `compute_dividend_streak_years(dividends: pd.Series, today: "date | None" = None) -> int` — `dividends` est une Series indexée par dates (comme `yfinance.Ticker.dividends`), valeur = montant versé (peu importe, seul l'index/les années comptent). `today` injectable pour les tests.
- Produces: `_compute_no_loss_years(net_income: pd.Series, years_cols: list) -> bool` — `net_income` est une ligne yfinance (Series indexée par date d'exercice, comme celle déjà extraite par `get_row(financials, "Net Income", ...)`), `years_cols` la liste des colonnes/exercices à vérifier (même valeur que `years_cols` déjà calculé dans `extract_ratios`/`extract_ratios_financial` : `list(financials.columns)`).

- [ ] **Step 1: Écrire les tests qui échouent**

Ajoute ce bloc dans `tests/test_indices_score.py`, juste avant `def test_extract_ratios_computes_expected_keys():` :

```python
from datetime import date as _date


def _dividend_series(years_with_dividend: list[int]) -> pd.Series:
    """Une Series indexée par une date arbitraire dans chaque année listée
    (15 juin, sans incidence — seule l'année compte), valeur = 1.0
    (peu importe le montant pour ces tests)."""
    dates = [pd.Timestamp(f"{y}-06-15") for y in years_with_dividend]
    return pd.Series([1.0] * len(dates), index=pd.DatetimeIndex(dates))


def test_compute_dividend_streak_years_counts_consecutive_years_including_current():
    dividends = _dividend_series([2020, 2021, 2022, 2023, 2024, 2025])
    result = compute_dividend_streak_years(dividends, today=_date(2026, 3, 1))
    # 2025 payé, mais pas encore 2026 (le dividende de l'année en cours
    # n'est peut-être pas encore tombé) -> démarre à 2025, remonte
    # jusqu'à 2020 incluse = 6 années consécutives.
    assert result == 6


def test_compute_dividend_streak_years_counts_current_year_if_already_paid():
    dividends = _dividend_series([2024, 2025, 2026])
    result = compute_dividend_streak_years(dividends, today=_date(2026, 9, 1))
    assert result == 3


def test_compute_dividend_streak_years_zero_when_broken_last_two_years():
    dividends = _dividend_series([2015, 2016, 2017, 2018, 2019, 2020])  # ancien historique, rompu depuis
    result = compute_dividend_streak_years(dividends, today=_date(2026, 3, 1))
    assert result == 0


def test_compute_dividend_streak_years_ignores_old_gap_before_current_streak():
    # Trou en 2018 (aucun versement), mais streak récent ininterrompu
    # depuis 2019 -> ne doit compter que le streak récent, pas être
    # cassé par le trou ancien.
    dividends = _dividend_series([2010, 2011, 2019, 2020, 2021, 2022, 2023, 2024, 2025])
    result = compute_dividend_streak_years(dividends, today=_date(2026, 3, 1))
    assert result == 7  # 2019..2025 inclus


def test_compute_dividend_streak_years_empty_series_returns_zero():
    result = compute_dividend_streak_years(pd.Series(dtype=float), today=_date(2026, 3, 1))
    assert result == 0


def test_compute_no_loss_years_true_when_all_positive():
    years = ["2025-12-31", "2024-12-31", "2023-12-31"]
    net_income = pd.Series([100.0, 90.0, 80.0], index=years)
    assert _compute_no_loss_years(net_income, years) is True


def test_compute_no_loss_years_false_when_one_year_negative():
    years = ["2025-12-31", "2024-12-31", "2023-12-31"]
    net_income = pd.Series([100.0, -10.0, 80.0], index=years)
    assert _compute_no_loss_years(net_income, years) is False


def test_compute_no_loss_years_false_when_one_year_missing():
    years = ["2025-12-31", "2024-12-31", "2023-12-31"]
    net_income = pd.Series([100.0, float("nan"), 80.0], index=years)
    assert _compute_no_loss_years(net_income, years) is False
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `python -m pytest tests/test_indices_score.py -k "compute_dividend_streak_years or compute_no_loss_years" -v`
Expected: FAIL avec `ImportError` ou `NameError` (les fonctions n'existent pas encore) — il faudra aussi ajouter les imports `compute_dividend_streak_years` et `_compute_no_loss_years` depuis `indices_score` en haut du fichier de test ou juste avant ce bloc, comme le fait déjà le fichier pour d'autres fonctions (cherche `from indices_score import` pour voir le style, ex. `from indices_score import _cagr` à la ligne 485).

- [ ] **Step 3: Implémenter**

Dans `indices_score.py`, juste avant `def extract_ratios(financials, balance_sheet, cashflow, closes_by_year, shares_outstanding: float) -> dict:`, ajoute :

```python
# Badge "Investisseur défensif" (Benjamin Graham, L'investisseur
# intelligent) — voir docs/superpowers/specs/2026-09-23-graham-defensive-investor-design.md.
# Conseillé par des professionnels de la finance consultés par
# l'utilisateur. N'affecte ni le score composite ni le déclenchement
# des alertes (badge purement informatif, même philosophie que le
# badge de phase Weinstein).
GRAHAM_DIVIDEND_STREAK_MIN_YEARS = 10
GRAHAM_CURRENT_RATIO_MIN = 2.0
GRAHAM_EARNINGS_GROWTH_CAGR_MIN_PCT = 2.9  # ≈ (1.33)^(1/10) - 1, annualisé (Graham : +33% sur 10 ans)
GRAHAM_PE_MAX = 15.0
GRAHAM_NUMBER_MAX = 22.5  # P/E x P/B


def compute_dividend_streak_years(dividends: pd.Series, today: "date | None" = None) -> int:
    """Nombre d'années consécutives avec au moins un versement de
    dividende, en remontant depuis l'année en cours. Tolère que le
    dividende de l'année en cours ne soit pas encore tombé : démarre le
    décompte à l'année en cours SI elle a déjà un versement, sinon à
    l'année dernière — mais si NI l'année en cours NI l'année dernière
    n'ont de versement, le streak est 0 (série considérée rompue), même
    s'il y a eu des versements par le passé. Un trou ancien avant le
    streak récent ne casse pas ce dernier (le décompte s'arrête dès la
    première année sans versement en remontant, peu importe ce qui se
    trouve plus loin). `today` injectable pour les tests — défaut :
    date du jour réelle."""
    today = today or date.today()
    years_paid = {d.year for d in dividends.index}
    start_year = today.year if today.year in years_paid else today.year - 1
    streak = 0
    year = start_year
    while year in years_paid:
        streak += 1
        year -= 1
    return streak


def _compute_no_loss_years(net_income: pd.Series, years_cols: list) -> bool:
    """True si `net_income` est positif sur TOUS les exercices de
    `years_cols` (aucune perte) — False si un seul exercice est négatif
    OU manquant (donnée manquante traitée comme un échec du critère,
    jamais comme une réussite silencieuse — même philosophie que les
    autres critères Graham de ce fichier, ex. valorisation_pe qui exige
    current_pe > 0 plutôt que de laisser passer une donnée à 0/manquante)."""
    return all(
        not _is_missing(net_income[col]) and net_income[col] > 0
        for col in years_cols
    )
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `python -m pytest tests/test_indices_score.py -k "compute_dividend_streak_years or compute_no_loss_years" -v`
Expected: 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "Ajoute compute_dividend_streak_years et _compute_no_loss_years (badge Graham)"
```

---

### Task 2: Récupération des dividendes + wiring dans `fetch_company_financials`

**Files:**
- Modify: `indices_score.py` (fonction `fetch_company_financials` — relis-la en entier avant de modifier, cherche `def fetch_company_financials(ticker: str) -> dict:`)
- Test: `tests/test_indices_score.py` (nouveaux tests dédiés, ne PAS toucher aux fixtures `_FakeTicker` existantes qui n'ont pas de propriété `dividends` — voir Global Constraints)

**Interfaces:**
- Consumes: `compute_dividend_streak_years(dividends, today=None) -> int` (Task 1).
- Produces: `fetch_company_financials(ticker)` renvoie désormais aussi `ratios["dividend_streak_years"]` (int).
- Produces: nouvelle fonction `fetch_dividend_history(ticker_obj) -> pd.Series`.

- [ ] **Step 1: Écrire les tests qui échouent**

Ajoute dans `tests/test_indices_score.py`, à côté des tests existants de `fetch_company_financials` (cherche `def test_fetch_company_financials_degrades_gracefully_when_volume_column_absent` et insère juste après) :

```python
def test_fetch_dividend_history_returns_dividends_property(monkeypatch):
    class _FakeTickerWithDividends:
        @property
        def dividends(self):
            return pd.Series([1.0, 1.1], index=pd.DatetimeIndex(["2025-06-15", "2026-06-15"]))

    result = indices_score.fetch_dividend_history(_FakeTickerWithDividends())
    assert len(result) == 2


def test_fetch_dividend_history_returns_empty_series_on_exception():
    class _FailingTicker:
        @property
        def dividends(self):
            raise RuntimeError("panne réseau")

    result = indices_score.fetch_dividend_history(_FailingTicker())
    assert len(result) == 0


Cette dernière fonction a besoin d'une date figée : `fetch_company_financials` appelle `compute_dividend_streak_years(dividends)` sans lui passer `today` explicitement (voir Task 2 Step 3 ci-dessous), donc le vrai `date.today()` du module `indices_score` est utilisé — sans le figer, le test serait non déterministe (le résultat dépendrait du jour réel d'exécution). Ajoute cette classe utilitaire dans `tests/test_indices_score.py`, juste avant `test_fetch_company_financials_exposes_dividend_streak_years` — réutilise l'alias `_date` déjà introduit au Task 1 (`from datetime import date as _date`), ne le réimporte pas une deuxième fois :

```python
class _FrozenDate(_date):
    """Sous-classe de datetime.date dont .today() renvoie une date figée —
    permet de monkeypatcher `indices_score.date` (la classe entière, pas
    une instance) pour un test déterministe, indépendant du jour réel
    d'exécution."""
    @classmethod
    def today(cls):
        return _date(2026, 9, 23)


def test_fetch_company_financials_exposes_dividend_streak_years(monkeypatch):
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])
    history_index = pd.date_range("2024-01-01", periods=250, freq="D")
    history_close = pd.Series([4.80] * 250, index=history_index)
    fake_dividends = pd.Series(
        [1.0, 1.0, 1.0],
        index=pd.DatetimeIndex(["2023-06-15", "2024-06-15", "2025-06-15"]),
    )

    class _FakeTicker:
        def __init__(self, ticker):
            pass

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
            return {"sharesOutstanding": 1000.0, "marketCap": None, "beta": 1.0, "sector": "Technology"}

        @property
        def dividends(self):
            return fake_dividends

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close})

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)
    monkeypatch.setattr(indices_score, "date", _FrozenDate)

    ratios = indices_score.fetch_company_financials("TEST3.PA")

    assert ratios["dividend_streak_years"] == 3  # 2023, 2024, 2025 consécutifs, 2026 pas encore tombé
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `python -m pytest tests/test_indices_score.py -k "fetch_dividend_history or exposes_dividend_streak_years" -v`
Expected: FAIL avec `AttributeError` (fonction absente) puis `KeyError: 'dividend_streak_years'`

- [ ] **Step 3: Implémenter**

Dans `indices_score.py`, ajoute cette fonction juste avant `def fetch_company_financials(ticker: str) -> dict:` :

```python
def fetch_dividend_history(ticker_obj) -> pd.Series:
    """Historique complet des dividendes versés (yfinance
    Ticker.dividends, Series indexée par date d'ex-dividende, valeur =
    montant versé). Série vide si l'entreprise n'a jamais versé de
    dividende, ou si l'appel échoue — jamais d'exception."""
    try:
        return ticker_obj.dividends
    except Exception:
        return pd.Series(dtype=float)
```

Puis, dans `fetch_company_financials`, juste après le bloc existant :
```python
    except Exception:
        weinstein = {"stage": None, "stage_label": "Neutre", "volume_confirme": False}
```
(le `except` qui clôt le try/except Weinstein) et juste avant la ligne existante :
```python
    if ticker in SHARES_OUTSTANDING_FROM_MARKET_CAP_TICKERS:
```
ajoute :
```python
    dividends = fetch_dividend_history(t)
    dividend_streak_years = compute_dividend_streak_years(dividends)
```

Et juste après la ligne existante :
```python
    ratios["volume_confirme"] = weinstein["volume_confirme"]
```
ajoute :
```python
    ratios["dividend_streak_years"] = dividend_streak_years
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `python -m pytest tests/test_indices_score.py -k "fetch_dividend_history or exposes_dividend_streak_years" -v`
Expected: 3 tests PASS

- [ ] **Step 5: Lancer toute la suite du fichier**

Run: `python -m pytest tests/test_indices_score.py -q`
Expected: tous PASS (aucune régression — les fixtures `_FakeTicker` existantes sans propriété `dividends` doivent lever `AttributeError` à l'intérieur du `try/except Exception` de `fetch_dividend_history`, donc dégrader vers une série vide sans casser le test)

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "Récupère l'historique des dividendes et calcule le streak dans fetch_company_financials"
```

---

### Task 3: P/B universel + ratio de liquidité + stabilité des bénéfices (profil standard)

**Files:**
- Modify: `indices_score.py` (fonction `extract_ratios` — cherche `def extract_ratios(financials, balance_sheet, cashflow, closes_by_year, shares_outstanding: float) -> dict:`)
- Test: `tests/test_indices_score.py` (étend `test_extract_ratios_computes_expected_keys` existant + nouveaux tests)

**Interfaces:**
- Consumes: `_compute_no_loss_years(net_income, years_cols) -> bool` (Task 1).
- Produces: `extract_ratios(...)` renvoie désormais aussi `current_pb: float`, `avg_pb_5y: float`, `current_ratio: float`, `no_loss_years: bool`.

- [ ] **Step 1: Écrire les tests qui échouent**

Modifie `test_extract_ratios_computes_expected_keys` (cherche cette fonction) pour ajouter les 4 nouvelles clés à la liste vérifiée :

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
        "tax_rate", "total_debt",
        "current_pb", "avg_pb_5y", "current_ratio", "no_loss_years",
    ]:
        assert key in ratios, f"clé manquante : {key}"
    # Revenu croît régulièrement de 800 à 1000 sur 5 ans ; CAGR lissé
    # (moyenne des 2 exercices récents vs moyenne des 2 plus anciens,
    # cf. test dédié ci-dessous) ~ 5.7%/an sur cette série linéaire.
    assert 5.0 < ratios["cagr_ca"] < 6.5
```

Ajoute ensuite, à côté (même zone du fichier) :

```python
def test_extract_ratios_computes_current_pb():
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )
    # market_cap = 100.0 (prix) * 10.0 (actions) = 1000.0 ; equity la plus
    # récente (2025) = 300.0 -> P/B = 1000/300 ≈ 3.33x.
    assert ratios["current_pb"] == pytest.approx(1000.0 / 300.0)
    assert ratios["avg_pb_5y"] > 0


def test_extract_ratios_computes_current_ratio():
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    balance_sheet.loc["Current Assets"] = [400, 380, 360, 340, 320]
    balance_sheet.loc["Current Liabilities"] = [150, 145, 140, 135, 130]
    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )
    # Dernier exercice (2025) : 400 / 150 ≈ 2.67
    assert ratios["current_ratio"] == pytest.approx(400.0 / 150.0)


def test_extract_ratios_current_ratio_zero_when_rows_absent():
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    # Pas de lignes Current Assets/Current Liabilities dans ce fixture.
    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )
    assert ratios["current_ratio"] == 0.0


def test_extract_ratios_no_loss_years_true_for_all_positive_fixture():
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    # _make_fixture_statements a un Net Income positif sur les 5 exercices
    # (140, 130, 120, 108, 96).
    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )
    assert ratios["no_loss_years"] is True


def test_extract_ratios_no_loss_years_false_with_one_loss_year():
    financials, balance_sheet, cashflow, closes_by_year = _make_fixture_statements()
    years = list(financials.columns)
    financials.loc["Net Income", years[2]] = -50.0  # une perte sur l'exercice du milieu
    ratios = extract_ratios(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=10.0
    )
    assert ratios["no_loss_years"] is False
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `python -m pytest tests/test_indices_score.py -k "extract_ratios" -v`
Expected: FAIL — `test_extract_ratios_computes_expected_keys` échoue sur les clés manquantes, les 4 nouveaux tests échouent avec `KeyError`

- [ ] **Step 3: Implémenter**

Dans `indices_score.py`, fonction `extract_ratios`, remplace le bloc de la boucle et du calcul P/E/EV-EBITDA :

```python
    ev_ebitda_by_year, pe_by_year = [], []
    for col in years_cols:
        price = closes_by_year.get(col)
        total_debt_value = _safe_value(total_debt, col)
        cash_value = _safe_value(cash, col)
        if (
            price is None
            or not ebitda[col]
            or not net_income[col]
            or _is_missing(ebitda[col])
            or _is_missing(net_income[col])
            or _is_missing(total_debt_value)
            or _is_missing(cash_value)
        ):
            continue
        market_cap = price * shares_outstanding
        net_debt_year = total_debt_value - cash_value
        ev_ebitda_by_year.append((market_cap + net_debt_year) / ebitda[col])
        pe_by_year.append(market_cap / net_income[col])
```

par (ajoute `pb_by_year` et le calcul de `equity_value` par exercice) :

```python
    ev_ebitda_by_year, pe_by_year, pb_by_year = [], [], []
    for col in years_cols:
        price = closes_by_year.get(col)
        total_debt_value = _safe_value(total_debt, col)
        cash_value = _safe_value(cash, col)
        equity_value = _safe_value(equity, col)
        if (
            price is None
            or not ebitda[col]
            or not net_income[col]
            or _is_missing(ebitda[col])
            or _is_missing(net_income[col])
            or _is_missing(total_debt_value)
            or _is_missing(cash_value)
        ):
            continue
        market_cap = price * shares_outstanding
        net_debt_year = total_debt_value - cash_value
        ev_ebitda_by_year.append((market_cap + net_debt_year) / ebitda[col])
        pe_by_year.append(market_cap / net_income[col])
        # Critère Graham (P/B universel — voir spec §6.7) : gardé sur une
        # liste séparée, PAS bloqué par la même condition que ev_ebitda/pe
        # ci-dessus au-delà de ce qui est déjà vérifié (price/net_income) —
        # un exercice avec equity manquante est juste exclu de pb_by_year,
        # sans empêcher ev_ebitda_by_year/pe_by_year de recevoir cet
        # exercice s'ils sont par ailleurs valides.
        if equity_value and not _is_missing(equity_value):
            pb_by_year.append(market_cap / equity_value)
```

Puis remplace :
```python
    current_ev_ebitda = ev_ebitda_by_year[0] if ev_ebitda_by_year else 0.0
    avg_ev_ebitda_5y = sum(ev_ebitda_by_year) / len(ev_ebitda_by_year) if ev_ebitda_by_year else 0.0
    current_pe = pe_by_year[0] if pe_by_year else 0.0
    avg_pe_5y = sum(pe_by_year) / len(pe_by_year) if pe_by_year else 0.0
```
par (ajoute le calcul `current_pb`/`avg_pb_5y`, `current_ratio`, `no_loss_years`) :
```python
    current_ev_ebitda = ev_ebitda_by_year[0] if ev_ebitda_by_year else 0.0
    avg_ev_ebitda_5y = sum(ev_ebitda_by_year) / len(ev_ebitda_by_year) if ev_ebitda_by_year else 0.0
    current_pe = pe_by_year[0] if pe_by_year else 0.0
    avg_pe_5y = sum(pe_by_year) / len(pe_by_year) if pe_by_year else 0.0
    current_pb = pb_by_year[0] if pb_by_year else 0.0
    avg_pb_5y = sum(pb_by_year) / len(pb_by_year) if pb_by_year else 0.0

    # Critère Graham (structure financière — profil standard uniquement,
    # voir spec §6.2) : actif circulant / passif circulant du dernier
    # exercice. _get_row_or_nan (pas get_row) : cette ligne est réellement
    # absente chez certaines entreprises, pas un alias manquant à ajouter.
    current_assets_row = _get_row_or_nan(balance_sheet, "Current Assets", "Total Current Assets")
    current_liabilities_row = _get_row_or_nan(balance_sheet, "Current Liabilities", "Total Current Liabilities")
    current_assets_latest = _safe_value(current_assets_row, latest)
    current_liabilities_latest = _safe_value(current_liabilities_row, latest)
    current_ratio = (
        current_assets_latest / current_liabilities_latest
        if (
            not _is_missing(current_assets_latest)
            and not _is_missing(current_liabilities_latest)
            and current_liabilities_latest
        )
        else 0.0
    )

    # Critère Graham (stabilité des bénéfices, voir spec §6.3) : aucune
    # perte sur les exercices disponibles.
    no_loss_years = _compute_no_loss_years(net_income, years_cols)
```

Enfin, dans le `return {...}` de `extract_ratios`, ajoute les 4 nouvelles clés (juste après `"avg_pe_5y": avg_pe_5y,`) :
```python
        "current_pe": current_pe,
        "avg_pe_5y": avg_pe_5y,
        "current_pb": current_pb,
        "avg_pb_5y": avg_pb_5y,
        "current_ratio": current_ratio,
        "no_loss_years": no_loss_years,
        "valuation_available": valuation_available,
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `python -m pytest tests/test_indices_score.py -k "extract_ratios" -v`
Expected: tous PASS

- [ ] **Step 5: Lancer toute la suite du fichier**

Run: `python -m pytest tests/test_indices_score.py -q`
Expected: tous PASS (aucune régression — `pe_by_year`/`ev_ebitda_by_year` gardent exactement le même contenu qu'avant, seul `pb_by_year` est nouveau et n'affecte aucun calcul existant)

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "Ajoute P/B, ratio de liquidité et stabilité des bénéfices au profil standard (critères Graham)"
```

---

### Task 4: Stabilité des bénéfices (profil financier/trust)

**Files:**
- Modify: `indices_score.py` (fonction `extract_ratios_financial` — cherche `def extract_ratios_financial(financials, balance_sheet, cashflow, closes_by_year, shares_outstanding: float) -> dict:`)
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes: `_compute_no_loss_years(net_income, years_cols) -> bool` (Task 1) — même fonction que la Task 3, pas de duplication.
- Produces: `extract_ratios_financial(...)` renvoie désormais aussi `no_loss_years: bool`. **Ne produit PAS** `current_ratio` (hors périmètre pour ce profil, voir Global Constraints) — `current_pb`/`avg_pb_5y` existent déjà dans cette fonction, aucun changement à leur sujet.

- [ ] **Step 1: Écrire les tests qui échouent**

Ajoute dans `tests/test_indices_score.py`, juste après `test_extract_ratios_financial_computes_expected_keys` (cherche cette fonction, ligne ~4071) :

```python
def test_extract_ratios_financial_exposes_no_loss_years():
    financials, balance_sheet, cashflow, closes_by_year = _make_financial_fixture_statements()
    ratios = indices_score.extract_ratios_financial(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=100.0
    )
    assert "no_loss_years" in ratios
    # _make_financial_fixture_statements a un Net Income positif sur les
    # 4 exercices (300.0, 280.0, 260.0, 240.0).
    assert ratios["no_loss_years"] is True


def test_extract_ratios_financial_no_loss_years_false_with_one_loss_year():
    financials, balance_sheet, cashflow, closes_by_year = _make_financial_fixture_statements()
    years = list(financials.columns)
    financials.loc["Net Income", years[1]] = -50.0
    ratios = indices_score.extract_ratios_financial(
        financials, balance_sheet, cashflow, closes_by_year, shares_outstanding=100.0
    )
    assert ratios["no_loss_years"] is False
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `python -m pytest tests/test_indices_score.py -k "extract_ratios_financial_exposes_no_loss_years or extract_ratios_financial_no_loss_years_false" -v`
Expected: FAIL avec `AssertionError: assert 'no_loss_years' in ratios` (ou `KeyError` selon la forme exacte) pour les deux tests

- [ ] **Step 3: Implémenter**

Dans `indices_score.py`, fonction `extract_ratios_financial`, juste avant son `return {...}` (cherche `return {` à l'intérieur de cette fonction), ajoute :

```python
    # Critère Graham (stabilité des bénéfices, voir spec §6.3) : aucune
    # perte sur les exercices disponibles — même calcul que le profil
    # standard (extract_ratios), fonction partagée.
    no_loss_years = _compute_no_loss_years(net_income, years_cols)
```

Puis dans le `return {...}` de cette même fonction, ajoute la clé (n'importe où dans le dict, par exemple juste après `"cagr_net_income": cagr_net_income,` si cette clé existe, sinon à la fin juste avant la parenthèse fermante) :
```python
        "no_loss_years": no_loss_years,
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `python -m pytest tests/test_indices_score.py -k "extract_ratios_financial_exposes_no_loss_years or extract_ratios_financial_no_loss_years_false" -v`
Expected: 2 tests PASS

- [ ] **Step 5: Lancer toute la suite du fichier**

Run: `python -m pytest tests/test_indices_score.py -q`
Expected: tous PASS

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "Ajoute la stabilité des bénéfices au profil financier/trust (critère Graham)"
```

---

### Task 5: Combinateur du badge + exposition dans `build_company_entry`

**Files:**
- Modify: `indices_score.py` (nouvelle fonction `compute_graham_defensive_badge` + son wiring dans `build_company_entry` — cherche `def build_company_entry(`)
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes : tous les champs produits par les Tasks 2-4 sur le dict `ratios`/`data` : `dividend_streak_years`, `current_ratio` (standard seulement), `no_loss_years`, `cagr_net_income`, `current_pe`, `current_pb`.
- Produces: `compute_graham_defensive_badge(ratios: dict, is_financial: bool, is_trust: bool) -> dict` renvoyant `{"eligible": bool, "criteria": dict}`. L'entrée finale de chaque société (`docs/indices.json`) gagne un champ `"graham_defensive"` avec ce contenu, plus `"dividend_streak_years"` exposé séparément (utile même hors badge, pour un futur affichage).

- [ ] **Step 1: Écrire les tests qui échouent**

Ajoute dans `tests/test_indices_score.py`, à un endroit logique (par exemple juste avant les tests de `build_company_entry`, ou juste après le bloc de constantes/fonctions Graham du Task 1 — cherche `from indices_score import` pour ajouter l'import de la nouvelle fonction au même endroit que les autres) :

```python
from indices_score import compute_graham_defensive_badge


def _graham_eligible_ratios(**overrides):
    """Ratios standard qui satisfont TOUS les critères Graham par
    défaut — chaque test override le(s) champ(s) qui doit échouer."""
    base = {
        "current_ratio": 2.5,
        "no_loss_years": True,
        "dividend_streak_years": 12,
        "cagr_net_income": 4.0,
        "current_pe": 12.0,
        "current_pb": 1.5,
    }
    base.update(overrides)
    return base


def test_compute_graham_defensive_badge_eligible_when_all_criteria_met():
    ratios = _graham_eligible_ratios()
    result = compute_graham_defensive_badge(ratios, is_financial=False, is_trust=False)
    assert result["eligible"] is True
    assert len(result["criteria"]) == 6
    assert result["criteria"]["structure_financiere"] is True


def test_compute_graham_defensive_badge_not_eligible_when_one_criterion_fails():
    ratios = _graham_eligible_ratios(current_pe=25.0)  # dépasse GRAHAM_PE_MAX
    result = compute_graham_defensive_badge(ratios, is_financial=False, is_trust=False)
    assert result["eligible"] is False
    assert result["criteria"]["valorisation_pe"] is False
    # Les autres critères restent corrects individuellement -> un seul
    # échec suffit à invalider le badge global, pas les autres clés.
    assert result["criteria"]["stabilite_benefices"] is True


def test_compute_graham_defensive_badge_excludes_structure_financiere_for_financial_profile():
    ratios = _graham_eligible_ratios()
    result = compute_graham_defensive_badge(ratios, is_financial=True, is_trust=False)
    assert "structure_financiere" not in result["criteria"]
    assert len(result["criteria"]) == 5


def test_compute_graham_defensive_badge_excludes_structure_financiere_for_trust_profile():
    ratios = _graham_eligible_ratios()
    result = compute_graham_defensive_badge(ratios, is_financial=False, is_trust=True)
    assert "structure_financiere" not in result["criteria"]
    assert len(result["criteria"]) == 5


def test_compute_graham_defensive_badge_graham_number_criterion():
    # P/E 14 x P/B 1.5 = 21 <= 22.5 -> vrai
    ratios = _graham_eligible_ratios(current_pe=14.0, current_pb=1.5)
    result = compute_graham_defensive_badge(ratios, is_financial=False, is_trust=False)
    assert result["criteria"]["valorisation_graham_number"] is True

    # P/E 14 x P/B 2.0 = 28 > 22.5 -> faux
    ratios2 = _graham_eligible_ratios(current_pe=14.0, current_pb=2.0)
    result2 = compute_graham_defensive_badge(ratios2, is_financial=False, is_trust=False)
    assert result2["criteria"]["valorisation_graham_number"] is False


def test_compute_graham_defensive_badge_missing_fields_degrade_to_false():
    result = compute_graham_defensive_badge({}, is_financial=False, is_trust=False)
    assert result["eligible"] is False
    assert all(v is False for v in result["criteria"].values())
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `python -m pytest tests/test_indices_score.py -k compute_graham_defensive_badge -v`
Expected: FAIL avec `ImportError`

- [ ] **Step 3: Implémenter**

Dans `indices_score.py`, ajoute cette fonction juste avant `def build_company_entry(` :

```python
def compute_graham_defensive_badge(ratios: dict, is_financial: bool, is_trust: bool) -> dict:
    """Combine les critères Graham applicables au profil de la société en
    un badge "Investisseur défensif" — test strict (tous les critères
    présents doivent être vrais), pas un score compté sur N (voir spec
    §6.8). Une donnée manquante dans `ratios` (dict incomplet) dégrade
    chaque critère concerné vers False plutôt que de lever une exception
    ou de compter comme une réussite."""
    criteria = {}
    if not (is_financial or is_trust):
        criteria["structure_financiere"] = (
            ratios.get("current_ratio", 0.0) >= GRAHAM_CURRENT_RATIO_MIN
        )
    criteria["stabilite_benefices"] = ratios.get("no_loss_years", False)
    criteria["dividendes"] = (
        ratios.get("dividend_streak_years", 0) >= GRAHAM_DIVIDEND_STREAK_MIN_YEARS
    )
    criteria["croissance_benefices"] = (
        ratios.get("cagr_net_income", 0.0) >= GRAHAM_EARNINGS_GROWTH_CAGR_MIN_PCT
    )
    current_pe = ratios.get("current_pe", 0.0)
    criteria["valorisation_pe"] = 0 < current_pe <= GRAHAM_PE_MAX
    current_pb = ratios.get("current_pb", 0.0)
    graham_number = current_pe * current_pb if current_pb > 0 else None
    criteria["valorisation_graham_number"] = (
        graham_number is not None and 0 < graham_number <= GRAHAM_NUMBER_MAX
    )
    return {"eligible": all(criteria.values()), "criteria": criteria}
```

Puis, dans `build_company_entry`, juste avant le `entry = {` existant (cherche `entry = {` — vérifie qu'il s'agit bien du dict final, pas d'un autre dict local), ajoute :

```python
    graham_defensive = compute_graham_defensive_badge(data, data["is_financial"], data["is_trust"])
```

Et dans le dict `entry = {...}`, ajoute deux clés juste après `"volume_confirme": data.get("volume_confirme", False),` :
```python
        "volume_confirme": data.get("volume_confirme", False),
        "graham_defensive": graham_defensive,
        "dividend_streak_years": data.get("dividend_streak_years", 0),
        "financial_analysis_html": financial_analysis_html,
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `python -m pytest tests/test_indices_score.py -k "compute_graham_defensive_badge or build_company_entry" -v`
Expected: tous PASS

- [ ] **Step 5: Lancer toute la suite du fichier**

Run: `python -m pytest tests/test_indices_score.py -q`
Expected: tous PASS — en particulier les tests qui mockent `fetch_company_financials` avec `_fake_ratios()`/`_fake_financial_ratios()`/`_fake_trust_ratios()` (dicts sans les nouveaux champs) ne doivent PAS lever d'exception grâce à `.get()` avec défauts dans `compute_graham_defensive_badge` et dans l'accès à `data.get("dividend_streak_years", 0)`.

- [ ] **Step 6: Lancer toute la suite du dépôt**

Run: `python -m pytest -q` (depuis la racine du dépôt)
Expected: tous PASS (aucune régression ailleurs — gold_bot/ibkr_bot non concernés par ce plan)

- [ ] **Step 7: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "Combine les critères Graham en un badge investisseur défensif par société"
```
