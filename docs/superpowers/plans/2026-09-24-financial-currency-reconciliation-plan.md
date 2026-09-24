# Réconciliation devise de référence / devise de cotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Réconcilier la devise de référence des comptes annuels (`financialCurrency`) avec la devise de cotation (`currency`) dans `fetch_company_financials`, pour que `market_cap = price * shares_outstanding` (devise de cotation) ne se mélange plus avec des comptes exprimés dans une autre devise (P/E d'AIA affiché à 129.2x au lieu de ~16.5x avant ce correctif).

**Architecture:** Une nouvelle fonction pure `fetch_fx_rate(from_currency, to_currency)` compose l'existante `fetch_fx_rate_to_usd` via un pivot USD. Câblée dans `fetch_company_financials`, juste après le bloc de conversion pence/livre existant : si `financialCurrency` diffère de la devise de cotation (déjà normalisée pence→livre à ce stade), les 3 DataFrames de comptes (financials, balance_sheet, cashflow) sont multipliés par le taux — à la source, pour que tout calcul en aval reste inchangé. Si le taux est introuvable, repli sur `shares_outstanding = 0.0` (mécanisme déjà en place depuis le constat C5, fait tomber proprement les ratios prix/comptes en "indisponible" sans invalider les ratios purement comptables).

**Tech Stack:** Python 3.12, pytest, pandas — même stack que le reste d'`indices_score.py`.

**Spec:** `docs/superpowers/specs/2026-09-24-financial-currency-reconciliation-design.md`

## Global Constraints

- `fetch_fx_rate(from_currency, to_currency) -> float | None` : `1.0` si les deux devises sont identiques (aucun appel réseau) ; sinon composé via `fetch_fx_rate_to_usd` (pivot USD) ; `None` si l'une des deux devises n'est pas gérée ou si un appel réseau échoue — jamais d'exception.
- `FX_TICKER_TO_USD` gagne `"CNY": ("CNY=X", "divide")` (même convention que JPY/HKD).
- La conversion des comptes se fait dans `fetch_company_financials`, juste après le bloc `if info.get("currency") == "GBp":` existant, avant la purge des NaN de fin de série.
- `financialCurrency` absent (`None`) : aucune conversion tentée, comportement actuel inchangé (pas de régression sur le cas majoritaire).
- Taux introuvable (mismatch réel mais non réconciliable) : `shares_outstanding = 0.0` — ne touche à rien d'autre.
- Aucune modification d'`extract_ratios`/`extract_ratios_financial`/du WACC — ils consomment déjà des comptes/un `shares_outstanding` cohérents une fois ce correctif en place.
- Les prix affichés (cours, repères d'entrée/sortie) restent dans la devise de cotation réelle, jamais convertis.

---

### Task 1: `fetch_fx_rate` + câblage dans `fetch_company_financials`

**Files:**
- Modify: `indices_score.py` (nouvelle fonction `fetch_fx_rate` — cherche `def fetch_fx_rate_to_usd(currency: str) -> float | None:` et insère juste après sa fin ; nouvelle entrée dans `FX_TICKER_TO_USD` — cherche `FX_TICKER_TO_USD = {` ; câblage dans `fetch_company_financials` — cherche `if info.get("currency") == "GBp":`)
- Test: `tests/test_indices_score.py` (nouveau bloc, à ajouter juste après `test_fetch_fx_rate_to_usd_returns_none_when_yfinance_unavailable` — cherche cette fonction et insère juste après, avant `from indices_score import _size_premium, estimate_wacc, estimate_cost_of_equity` ; deux nouveaux tests `fetch_company_financials`, à ajouter juste après `test_fetch_company_financials_leaves_lse_non_gbp_prices_unconverted` — cherche cette fonction)

**Interfaces:**
- Produces: `fetch_fx_rate(from_currency: str, to_currency: str) -> float | None`.
- Produces: `fetch_company_financials(ticker)` renvoie désormais un `ratios["shares_outstanding"]` correctement dégradé à `0.0` quand la devise des comptes n'a pas pu être réconciliée avec la devise de cotation ; les DataFrames de comptes utilisés pour construire `ratios` sont déjà convertis vers la devise de cotation quand `financialCurrency` diffère et qu'un taux a pu être obtenu.

- [ ] **Step 1: Écrire les tests qui échouent pour `fetch_fx_rate`**

Ajoute ce bloc dans `tests/test_indices_score.py`, juste après `test_fetch_fx_rate_to_usd_returns_none_when_yfinance_unavailable()` (avant `from indices_score import _size_premium, estimate_wacc, estimate_cost_of_equity`) :

```python
def test_fetch_fx_rate_same_currency_returns_1_without_network_call(monkeypatch):
    def _should_not_be_called(*a, **k):
        raise AssertionError("devises identiques : aucun appel réseau attendu")
    monkeypatch.setattr(indices_score, "fetch_fx_rate_to_usd", _should_not_be_called)
    assert indices_score.fetch_fx_rate("USD", "USD") == 1.0


def test_fetch_fx_rate_composes_via_usd_pivot(monkeypatch):
    """USD/HKD proche du cas réel AIA : financialCurrency=USD,
    quote_currency=HKD, taux HKD/USD ≈ 7.8 -> convertir un montant USD
    vers HKD doit multiplier par ≈7.8."""
    rates = {"USD": 1.0, "HKD": 1 / 7.8}  # fetch_fx_rate_to_usd("HKD") : HKD=X coté indirect, déjà divisé

    def _fake_fetch_fx_rate_to_usd(currency):
        return rates.get(currency)

    monkeypatch.setattr(indices_score, "fetch_fx_rate_to_usd", _fake_fetch_fx_rate_to_usd)
    result = indices_score.fetch_fx_rate("USD", "HKD")
    assert result == pytest.approx(7.8, rel=0.01)


def test_fetch_fx_rate_returns_none_when_from_currency_unhandled(monkeypatch):
    monkeypatch.setattr(indices_score, "fetch_fx_rate_to_usd", lambda c: None if c == "XYZ" else 1.0)
    assert indices_score.fetch_fx_rate("XYZ", "USD") is None


def test_fetch_fx_rate_returns_none_when_to_currency_unhandled(monkeypatch):
    monkeypatch.setattr(indices_score, "fetch_fx_rate_to_usd", lambda c: None if c == "XYZ" else 1.0)
    assert indices_score.fetch_fx_rate("USD", "XYZ") is None
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `python -m pytest tests/test_indices_score.py -k test_fetch_fx_rate_ -v`
Expected: FAIL avec `AttributeError` (`fetch_fx_rate` n'existe pas encore)

- [ ] **Step 3: Implémenter `fetch_fx_rate` et l'entrée CNY**

Dans `indices_score.py`, juste après la fin de `fetch_fx_rate_to_usd` (cherche son dernier `return`/`except Exception: return None`, insère juste après, avant `MARKET_RISK_PREMIUM = ...`), ajoute :

```python
def fetch_fx_rate(from_currency: str, to_currency: str) -> float | None:
    """Taux de change pour convertir un montant de `from_currency` vers
    `to_currency`, composé à partir de fetch_fx_rate_to_usd (pivot USD —
    toutes les devises gérées sont cotées contre USD, pratique de marché
    standard). None si l'une des deux devises n'est pas gérée ou si
    l'appel réseau échoue — jamais d'exception (fetch_fx_rate_to_usd ne
    lève déjà jamais)."""
    if from_currency == to_currency:
        return 1.0
    rate_from_usd = fetch_fx_rate_to_usd(from_currency)
    rate_to_usd = fetch_fx_rate_to_usd(to_currency)
    if rate_from_usd is None or rate_to_usd is None:
        return None
    return rate_from_usd / rate_to_usd
```

Puis, dans `FX_TICKER_TO_USD` (cherche `FX_TICKER_TO_USD = {`), ajoute une entrée après `"HKD": ("HKD=X", "divide"),` :

```python
    "CNY": ("CNY=X", "divide"),  # cotation indirecte comme JPY/HKD (constat C1 — Hang Seng reportant en CNY)
```

Puis, juste avant `def fetch_company_financials(ticker: str) -> dict:`, ajoute cette constante (utilisée par le câblage du Step 7) :

```python
# Lignes de `financials` qui ne sont PAS des montants monétaires — ne
# doivent jamais être multipliées par un taux de change (voir le
# câblage de la conversion devise dans fetch_company_financials,
# constat C1). "Tax Rate For Calcs" est un ratio (0.25 = 25%), pas une
# somme d'argent.
NON_MONETARY_FINANCIALS_ROWS = {"Tax Rate For Calcs"}
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `python -m pytest tests/test_indices_score.py -k test_fetch_fx_rate_ -v`
Expected: 4 tests PASS

- [ ] **Step 5: Écrire les tests qui échouent pour le câblage dans `fetch_company_financials`**

Ajoute dans `tests/test_indices_score.py`, juste après `test_fetch_company_financials_leaves_lse_non_gbp_prices_unconverted` (reprends le même style de `_FakeTicker` que ce test — cherche-le pour voir le pattern complet) :

```python
def test_fetch_company_financials_converts_statements_when_financial_currency_differs(monkeypatch):
    """Inspiré du cas réel AIA (1299.HK) : comptes en USD
    (financialCurrency), cotation en HKD (currency) — sans conversion,
    market_cap (en HKD) combiné à des capitaux propres en USD faussait
    P/B d'un facteur ~7.8. Un taux de 2.0 (valeur simple pour le test)
    doit multiplier Stockholders Equity par 2.0.

    NOTE : utilise un ticker fictif hors FINANCIAL_SECTOR_TICKERS/
    TRUST_TICKERS ("TEST3.PA", pas "1299.HK") pour router vers
    extract_ratios (profil standard) plutôt qu'extract_ratios_financial
    — AIA elle-même est dans FINANCIAL_SECTOR_TICKERS, mais son profil
    financier a des exigences de fixture différentes (ex: ligne "Total
    Assets" absente de _make_fixture_statements()) sans rapport avec ce
    qu'on teste ici ; le mécanisme de conversion devise est identique
    pour les deux profils (câblé en amont, dans fetch_company_financials,
    avant le branchement extract_ratios vs extract_ratios_financial).
    "equity" est utilisé pour l'assertion (pas "net_income", qui n'est
    exposé dans AUCUN des deux dicts de retour) — "equity" l'est dans
    les deux, donc le choix reste valable quel que soit le profil."""
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])
    history_index = pd.date_range("2024-01-01", periods=250, freq="D")
    history_close = pd.Series([100.0] * 250, index=history_index)
    equity_before_conversion = balance_sheet.loc["Stockholders Equity"].copy()
    tax_rate_before_conversion = financials.loc["Tax Rate For Calcs"].copy()

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
            return {
                "sharesOutstanding": 1000.0, "beta": 0.9, "sector": "Industrials",
                "currency": "HKD", "financialCurrency": "USD",
            }

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close})

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)
    monkeypatch.setattr(indices_score, "fetch_fx_rate", lambda f, t: 2.0)

    ratios = indices_score.fetch_company_financials("TEST3.PA")

    # equity (le plus récent exercice) doit refléter la conversion x2.0
    # appliquée aux DataFrames de comptes.
    assert ratios["equity"] == pytest.approx(equity_before_conversion.iloc[0] * 2.0)
    # tax_rate est un RATIO (0.25 = 25%), pas un montant monétaire -- ne
    # doit surtout PAS être multiplié par le taux de change (sinon
    # 0.25*2.0=0.5, un taux d'imposition de 50% inventé de toutes pièces).
    assert ratios["tax_rate"] == pytest.approx(tax_rate_before_conversion.iloc[0])


def test_fetch_company_financials_degrades_shares_outstanding_when_fx_rate_unavailable(monkeypatch):
    """financialCurrency diffère de currency, mais fetch_fx_rate ne peut
    pas obtenir de taux (devise non gérée ou panne réseau) : repli sur
    shares_outstanding=0.0 (mécanisme du constat C5, déjà en place) —
    les ratios prix/comptes tombent proprement à "indisponible" sans
    inventer un taux. Ticker fictif hors FINANCIAL_SECTOR_TICKERS/
    TRUST_TICKERS (même raison que le test précédent — évite un profil
    dont la fixture standard ne couvre pas toutes les lignes requises)."""
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])
    history_index = pd.date_range("2024-01-01", periods=250, freq="D")
    history_close = pd.Series([100.0] * 250, index=history_index)

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
            return {
                "sharesOutstanding": 1000.0, "beta": 0.9, "sector": "Industrials",
                "currency": "HKD", "financialCurrency": "USD",
            }

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close})

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)
    monkeypatch.setattr(indices_score, "fetch_fx_rate", lambda f, t: None)

    ratios = indices_score.fetch_company_financials("TEST3.PA")

    assert ratios["shares_outstanding"] == 0.0


def test_fetch_company_financials_no_conversion_when_financial_currency_absent(monkeypatch):
    """financialCurrency absent (None) : aucune conversion tentée,
    comportement actuel inchangé — ne doit pas dégrader shares_outstanding
    ni appeler fetch_fx_rate (cas majoritaire, pas de régression)."""
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])
    history_index = pd.date_range("2024-01-01", periods=250, freq="D")
    history_close = pd.Series([100.0] * 250, index=history_index)

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
            return {"sharesOutstanding": 1000.0, "beta": 0.9, "sector": "Technology", "currency": "USD"}

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close})

    def _should_not_be_called(*a, **k):
        raise AssertionError("financialCurrency absent : fetch_fx_rate ne doit pas être appelé")

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)
    monkeypatch.setattr(indices_score, "fetch_fx_rate", _should_not_be_called)

    ratios = indices_score.fetch_company_financials("MSFT")

    assert ratios["shares_outstanding"] == 1000.0
```

- [ ] **Step 6: Vérifier que les tests échouent**

Run: `python -m pytest tests/test_indices_score.py -k "converts_statements_when_financial_currency or degrades_shares_outstanding_when_fx_rate or no_conversion_when_financial_currency_absent" -v`
Expected: FAIL (le câblage n'existe pas encore — `financialCurrency` est ignoré, `shares_outstanding` reste toujours `1000.0`, `equity` n'est jamais multiplié)

- [ ] **Step 7: Implémenter le câblage**

Dans `indices_score.py`, fonction `fetch_company_financials`, juste après le bloc existant (cherche `if info.get("currency") == "GBp":` ... `history = history / 100.0`) et avant le commentaire `# Purge les lignes NaN en fin de serie`, ajoute :

```python
    quote_currency = "GBP" if info.get("currency") == "GBp" else info.get("currency")
    financial_currency = info.get("financialCurrency")
    currency_mismatch_unresolved = False
    if financial_currency and quote_currency and financial_currency != quote_currency:
        # Devise de référence des comptes ≠ devise de cotation (audit
        # 2026-09-24, constat C1) : cas des doubles cotations / sociétés
        # dont le siège de reporting diffère de la place de cotation
        # principale (ex: AIA 1299.HK cote en HKD, comptes en USD — P/E
        # affiché à 129.2x au lieu de ~16.5x avant ce correctif, écart
        # correspondant exactement au taux HKD/USD). Convertit les 3
        # DataFrames de comptes vers la devise de cotation, à la source,
        # même principe que la conversion pence/livre juste au-dessus :
        # tout calcul en aval (market_cap = price * shares_outstanding
        # combiné à ces comptes) reste cohérent sans replâtrage
        # consommateur par consommateur.
        fx_rate = fetch_fx_rate(financial_currency, quote_currency)
        if fx_rate is not None:
            # "Tax Rate For Calcs" (ligne de `financials`) est un ratio
            # (ex: 0.25 = 25%), PAS un montant monétaire — la multiplier
            # par le taux de change la corromprait (0.25 * 7.8 = 1.95,
            # un taux d'imposition de 195%), ce qui fausserait ensuite
            # ROCE et le coût de la dette après impôt du WACC pour
            # TOUTES les sociétés à devise non réconciliée, pas
            # seulement celles visées par ce correctif. `balance_sheet`
            # et `cashflow` ne portent aucune ligne de ce type (toutes
            # leurs lignes extraites en aval — dette, cash, capitaux
            # propres, flux de trésorerie — sont des montants) : converties
            # intégralement.
            monetary_rows = ~financials.index.isin(NON_MONETARY_FINANCIALS_ROWS)
            financials.loc[monetary_rows] = financials.loc[monetary_rows] * fx_rate
            balance_sheet = balance_sheet * fx_rate
            cashflow = cashflow * fx_rate
        else:
            currency_mismatch_unresolved = True

    if currency_mismatch_unresolved:
        # Devises non réconciliables (taux introuvable) : dégrade vers
        # shares_outstanding=0.0, qui fait déjà tomber proprement les
        # ratios prix/comptes (P/E, P/B, EV/EBITDA, valorisation — voir
        # extract_ratios, constat C5, déjà corrigé) sans invalider les
        # ratios purement comptables (ROE, ROCE, levier, croissance),
        # qui restent corrects même non convertis — ce sont des ratios
        # internes aux comptes, cohérents quelle que soit la devise.
        shares_outstanding = 0.0
```

**Attention :** `financials`/`balance_sheet`/`cashflow` sont utilisés par le reste de la fonction (extraction de `net_income`, `equity`, etc. plus loin) — vérifie que ce bloc s'exécute bien AVANT ces usages (il doit se situer juste après le bloc GBp existant, largement avant l'appel à `extract_ratios`/`extract_ratios_financial` en fin de fonction). `financials` contient une ligne `"Tax Rate For Calcs"` qui est un RATIO, pas un montant — `NON_MONETARY_FINANCIALS_ROWS` l'exclut explicitement de la multiplication (voir Step 3). `balance_sheet`/`cashflow` ne portent aucune ligne de ce type parmi celles extraites en aval (dette, cash, capitaux propres, flux de trésorerie — toutes des montants), donc converties intégralement sans exclusion.

- [ ] **Step 8: Vérifier que les tests passent**

Run: `python -m pytest tests/test_indices_score.py -k "converts_statements_when_financial_currency or degrades_shares_outstanding_when_fx_rate or no_conversion_when_financial_currency_absent" -v`
Expected: 3 tests PASS

- [ ] **Step 9: Lancer toute la suite du fichier**

Run: `python -m pytest tests/test_indices_score.py -q`
Expected: tous PASS (aucune régression — le nouveau bloc ne s'active que si `financialCurrency` est présent ET diffère de la devise de cotation ; tous les fixtures/tests existants n'ont pas ce champ dans leur `info`, donc `financial_currency` vaut `None` et le bloc entier est sauté, comme le confirme le test `no_conversion_when_financial_currency_absent`)

- [ ] **Step 10: Lancer toute la suite du dépôt**

Run: `python -m pytest -q` (depuis la racine du dépôt)
Expected: tous PASS (aucune régression ailleurs — gold_bot/ibkr_bot non concernés par ce plan)

- [ ] **Step 11: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "Réconcilie devise de référence des comptes et devise de cotation (audit C1)"
```
