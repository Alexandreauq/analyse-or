# Méthode Stage Analysis (Weinstein) — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Classer chaque société suivie en phase Weinstein (Achat/Déclin/Neutre) à partir de la MM30 semaines et de sa pente, et s'en servir pour exclure le repère technique d'entrée/sortie quand une société est en déclin confirmé — plutôt que de proposer un point d'entrée sur un titre qui chute.

**Architecture:** Tout dans `indices_score.py` (fichier unique existant, convention du dépôt). Nouvelle fonction pure `classify_weinstein_stage`, wirée dans `fetch_company_financials` (qui capture désormais aussi le Volume, absent aujourd'hui) puis dans `estimate_entry_exit_prices` (nouveau paramètre optionnel, défaut `None` = comportement actuel inchangé) et dans l'email d'alerte "entrée".

**Tech Stack:** Python, pandas (déjà utilisé partout dans ce fichier), pytest.

**Spec:** `docs/superpowers/specs/2026-09-23-weinstein-stage-analysis-design.md`

## Global Constraints

- Aucune exception ne doit jamais remonter d'un calcul lié à Weinstein — toujours un repli gracieux (`stage=None`, `stage_label="Neutre"`, `volume_confirme=False`), même contrat que le reste du pipeline (`data_available=False`).
- `score_dynamique_recente` et le score composite restent strictement inchangés — hors périmètre (voir spec §2).
- Aucun filtrage du déclenchement des alertes "entrée" sur la base de la phase — la phase est affichée, jamais bloquante (voir spec §2, §7).
- Les paramètres/champs nouveaux doivent avoir un défaut qui préserve le comportement actuel pour tout appelant/test existant qui ne les fournit pas — ne pas forcer un sweep de tous les tests existants du fichier (`tests/test_indices_score.py` en compte plus de 350).

---

### Task 1: `classify_weinstein_stage` — classification pure

**Files:**
- Modify: `indices_score.py` (nouvelles constantes + fonction, à ajouter juste avant `def estimate_entry_exit_prices` à la ligne 3465 — donc autour de la ligne 3464)
- Test: `tests/test_indices_score.py` (nouveau bloc de tests, à ajouter juste avant `from indices_score import estimate_entry_exit_prices` — cherche cette ligne d'import existante pour te positionner)

**Interfaces:**
- Produces: `classify_weinstein_stage(weekly_closes: pd.Series, weekly_volumes: pd.Series) -> dict` avec les clés `{"stage": int | None, "stage_label": str, "volume_confirme": bool}`. `weekly_closes`/`weekly_volumes` sont des `pd.Series` déjà rééchantillonnées à la semaine (une valeur par semaine), même longueur, index aligné (voir Task 2 pour comment elles sont produites en amont — ce Task 1 ne construit PAS ces séries, il les consomme telles quelles).
- Produces (constantes réutilisées par les tâches suivantes) : `WEINSTEIN_MA_WEEKS = 30`, `WEINSTEIN_SLOPE_LOOKBACK_WEEKS = 4`, `WEINSTEIN_MIN_WEEKS = 34`, `WEINSTEIN_VOLUME_LOOKBACK_WEEKS = 30`.

- [ ] **Step 1: Écrire les tests qui échouent**

Ajoute ce bloc dans `tests/test_indices_score.py`, juste avant la ligne `from indices_score import estimate_entry_exit_prices` (cherche cette ligne exacte pour te positionner — ne duplique pas l'import, ajoute le bloc juste au-dessus) :

```python
from indices_score import classify_weinstein_stage


def _weekly_series(values, start="2020-01-05"):
    """Série pandas hebdomadaire (une valeur par dimanche, comme le
    ferait .resample('W')) à partir d'une liste de valeurs, la plus
    ancienne en premier."""
    index = pd.date_range(start=start, periods=len(values), freq="W")
    return pd.Series(values, index=index, dtype=float)


def test_classify_weinstein_stage_returns_none_when_history_too_short():
    closes = _weekly_series([100.0] * 33)  # 33 < WEINSTEIN_MIN_WEEKS (34)
    volumes = _weekly_series([1000.0] * 33)
    result = classify_weinstein_stage(closes, volumes)
    assert result == {"stage": None, "stage_label": "Neutre", "volume_confirme": False}


def test_classify_weinstein_stage_detects_achat_phase():
    # MM30s clairement montante (prix croissant sur toute la fenêtre) et
    # prix courant au-dessus de la MM30s -> Phase 2 (Achat).
    closes = _weekly_series([100.0 + i * 2.0 for i in range(40)])
    volumes = _weekly_series([1000.0] * 40)
    result = classify_weinstein_stage(closes, volumes)
    assert result["stage"] == 2
    assert result["stage_label"] == "Achat"


def test_classify_weinstein_stage_detects_declin_phase():
    # MM30s clairement descendante et prix courant en dessous -> Phase 4 (Déclin).
    closes = _weekly_series([300.0 - i * 2.0 for i in range(40)])
    volumes = _weekly_series([1000.0] * 40)
    result = classify_weinstein_stage(closes, volumes)
    assert result["stage"] == 4
    assert result["stage_label"] == "Déclin"


def test_classify_weinstein_stage_flat_ma_is_neutre():
    # Prix constant sur toute la fenêtre -> MM30s parfaitement plate -> Neutre.
    closes = _weekly_series([100.0] * 40)
    volumes = _weekly_series([1000.0] * 40)
    result = classify_weinstein_stage(closes, volumes)
    assert result["stage_label"] == "Neutre"
    assert result["stage"] in (1, 3)  # best-effort interne, jamais exposé comme label


def test_classify_weinstein_stage_price_ma_disagreement_is_neutre():
    # Prix au-dessus d'une MM30s qui descend encore (transition typique,
    # ni Achat ni Déclin au sens strict de la méthode) -> Neutre.
    closes = _weekly_series([300.0 - i * 2.0 for i in range(36)] + [250.0, 260.0, 270.0, 280.0])
    volumes = _weekly_series([1000.0] * 40)
    result = classify_weinstein_stage(closes, volumes)
    assert result["stage_label"] == "Neutre"


def test_classify_weinstein_stage_volume_confirmed_when_spike_above_prior_average():
    closes = _weekly_series([100.0 + i * 2.0 for i in range(40)])
    volumes = _weekly_series([1000.0] * 39 + [2000.0])  # dernière semaine : 2x la moyenne des 30 précédentes
    result = classify_weinstein_stage(closes, volumes)
    assert result["volume_confirme"] is True


def test_classify_weinstein_stage_volume_not_confirmed_below_multiple():
    closes = _weekly_series([100.0 + i * 2.0 for i in range(40)])
    volumes = _weekly_series([1000.0] * 39 + [1200.0])  # 1.2x, sous le multiple de 1.5x
    result = classify_weinstein_stage(closes, volumes)
    assert result["volume_confirme"] is False


def test_classify_weinstein_stage_current_week_volume_excluded_from_its_own_baseline():
    """Point de correction identifié à la relecture de la spec : un
    volume extrême sur la semaine courante ne doit pas gonfler sa propre
    moyenne de référence."""
    closes = _weekly_series([100.0 + i * 2.0 for i in range(40)])
    # Moyenne des 30 semaines précédentes = 1000 ; dernière semaine = 10000
    # (10x) -> doit rester confirmé, pas dilué par sa propre valeur extrême
    # dans le calcul de la moyenne.
    volumes = _weekly_series([1000.0] * 39 + [10000.0])
    result = classify_weinstein_stage(closes, volumes)
    assert result["volume_confirme"] is True


def test_classify_weinstein_stage_missing_volume_degrades_gracefully():
    closes = _weekly_series([100.0 + i * 2.0 for i in range(40)])
    volumes = _weekly_series([float("nan")] * 40)
    result = classify_weinstein_stage(closes, volumes)
    assert result["stage"] == 2  # le calcul de phase ne dépend pas du volume
    assert result["volume_confirme"] is False


def test_classify_weinstein_stage_never_raises_on_empty_series():
    closes = pd.Series([], dtype=float)
    volumes = pd.Series([], dtype=float)
    result = classify_weinstein_stage(closes, volumes)
    assert result == {"stage": None, "stage_label": "Neutre", "volume_confirme": False}
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `python -m pytest tests/test_indices_score.py -k classify_weinstein_stage -v`
Expected: FAIL avec `ImportError: cannot import name 'classify_weinstein_stage'`

- [ ] **Step 3: Implémenter**

Dans `indices_score.py`, juste avant `def estimate_entry_exit_prices` (ligne 3465 actuelle — vérifie avec `grep -n "def estimate_entry_exit_prices" indices_score.py`, insère juste au-dessus), ajoute :

```python
# Stage Analysis (Stan Weinstein, "Secrets for Profiting in Bull and
# Bear Markets") — voir docs/superpowers/specs/2026-09-23-weinstein-stage-analysis-design.md.
# Conseillée par des professionnels de la finance consultés par
# l'utilisateur, pour affiner les repères d'entrée/sortie (§6 de la
# spec) sans toucher au score composite (score_dynamique_recente reste
# sur la MM200, inchangé — hors périmètre, voir spec §2).
WEINSTEIN_MA_WEEKS = 30
WEINSTEIN_SLOPE_LOOKBACK_WEEKS = 4
WEINSTEIN_MIN_WEEKS = WEINSTEIN_MA_WEEKS + WEINSTEIN_SLOPE_LOOKBACK_WEEKS  # 34
WEINSTEIN_SLOPE_NOISE_FLOOR_PCT = 0.5  # % sur 4 semaines, sous ce seuil -> MM30s "plate"
WEINSTEIN_VOLUME_LOOKBACK_WEEKS = 30
WEINSTEIN_VOLUME_CONFIRMATION_MULTIPLE = 1.5


def classify_weinstein_stage(weekly_closes: pd.Series, weekly_volumes: pd.Series) -> dict:
    """Classe une société en phase Weinstein à partir de sa MM30 semaines
    et de sa pente (mesurée sur WEINSTEIN_SLOPE_LOOKBACK_WEEKS semaines) :

    - Phase 2 "Achat" : prix > MM30s ET pente montante.
    - Phase 4 "Déclin" : prix < MM30s ET pente descendante.
    - Tout le reste (MM30s plate, ou prix/pente en désaccord — une
      transition typique) : `stage_label="Neutre"`. `stage` reçoit
      quand même une valeur interne best-effort (1 si le prix est dans
      la moitié basse de son range 52 semaines, sinon 3) mais cette
      distinction n'est JAMAIS exposée comme label "Base"/"Distribution"
      tant qu'elle n'est pas fiable — voir spec §5.3.

    `stage=None, stage_label="Neutre"` si `weekly_closes` a moins de
    WEINSTEIN_MIN_WEEKS entrées (historique insuffisant) — jamais
    d'exception, même en cas de série vide.

    `volume_confirme` (informatif, n'affecte jamais `stage`) : True si
    le volume de la semaine courante dépasse WEINSTEIN_VOLUME_CONFIRMATION_MULTIPLE
    fois la moyenne des WEINSTEIN_VOLUME_LOOKBACK_WEEKS semaines
    PRÉCÉDENTES (la semaine courante est explicitement exclue de sa
    propre moyenne de référence, sinon un volume extrême gonflerait la
    moyenne à laquelle on le compare — point de correction identifié à
    la relecture de la spec, voir son §5.4)."""
    result = {"stage": None, "stage_label": "Neutre", "volume_confirme": False}
    if len(weekly_closes) < WEINSTEIN_MIN_WEEKS:
        return result

    ma30w = weekly_closes.rolling(WEINSTEIN_MA_WEEKS).mean()
    current_price = float(weekly_closes.iloc[-1])
    current_ma = float(ma30w.iloc[-1])
    prior_ma = float(ma30w.iloc[-1 - WEINSTEIN_SLOPE_LOOKBACK_WEEKS])
    if _is_missing(current_price) or _is_missing(current_ma) or _is_missing(prior_ma) or prior_ma == 0:
        return result

    slope_pct = (current_ma - prior_ma) / abs(prior_ma) * 100
    rising = slope_pct > WEINSTEIN_SLOPE_NOISE_FLOOR_PCT
    falling = slope_pct < -WEINSTEIN_SLOPE_NOISE_FLOOR_PCT
    above = current_price > current_ma
    below = current_price < current_ma

    if above and rising:
        result["stage"], result["stage_label"] = 2, "Achat"
    elif below and falling:
        result["stage"], result["stage_label"] = 4, "Déclin"
    else:
        window_52w = weekly_closes.tail(52)
        low_52w, high_52w = float(window_52w.min()), float(window_52w.max())
        midpoint = (low_52w + high_52w) / 2
        result["stage"] = 1 if current_price <= midpoint else 3
        # stage_label reste "Neutre" (défaut déjà posé ci-dessus)

    if len(weekly_volumes) >= WEINSTEIN_VOLUME_LOOKBACK_WEEKS + 1:
        recent_volume = weekly_volumes.iloc[-1]
        baseline_volume = weekly_volumes.iloc[-(WEINSTEIN_VOLUME_LOOKBACK_WEEKS + 1):-1].mean()
        if not _is_missing(recent_volume) and not _is_missing(baseline_volume) and baseline_volume > 0:
            result["volume_confirme"] = recent_volume > baseline_volume * WEINSTEIN_VOLUME_CONFIRMATION_MULTIPLE

    return result
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `python -m pytest tests/test_indices_score.py -k classify_weinstein_stage -v`
Expected: 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "Ajoute classify_weinstein_stage (Stage Analysis, phase 2/4 + confirmation volume)"
```

---

### Task 2: Capture du Volume + wiring dans `fetch_company_financials`

**Files:**
- Modify: `indices_score.py:2361-2429` environ (fonction `fetch_company_financials` — relis-la en entier avant de modifier, elle a beaucoup de commentaires de contexte à préserver)
- Test: `tests/test_indices_score.py` (nouveaux tests dédiés, ne PAS toucher aux fixtures `_FakeTicker.history()` existantes qui ne renvoient qu'une colonne "Close" — voir Global Constraints)

**Interfaces:**
- Consumes: `classify_weinstein_stage(weekly_closes, weekly_volumes) -> dict` (Task 1).
- Produces: `fetch_company_financials(ticker)` renvoie désormais aussi `ratios["stage"]`, `ratios["stage_label"]`, `ratios["volume_confirme"]` (mêmes types que la sortie de `classify_weinstein_stage`).

- [ ] **Step 1: Écrire un test qui échoue**

Ajoute dans `tests/test_indices_score.py`, à côté des tests existants de `fetch_company_financials` (cherche `def test_fetch_company_financials_ignores_market_cap_override_for_other_tickers` pour te positionner juste après) :

```python
def test_fetch_company_financials_exposes_weinstein_stage_from_weekly_volume_history(monkeypatch):
    """Historique construit pour donner une MM30 semaines nettement
    montante (Phase 2 Achat), avec Volume disponible dans la réponse
    yfinance — vérifie le branchement complet Volume -> hebdomadaire ->
    classify_weinstein_stage, pas seulement la fonction pure du Task 1."""
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])

    # 300 jours ouvrés (~43 semaines), prix croissant -> MM30s montante.
    history_index = pd.bdate_range("2025-01-01", periods=300)
    history_close = pd.Series([100.0 + i * 0.5 for i in range(300)], index=history_index)
    history_volume = pd.Series([1000.0] * 300, index=history_index)

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

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close, "Volume": history_volume})

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)

    ratios = indices_score.fetch_company_financials("TEST.PA")

    assert ratios["stage"] == 2
    assert ratios["stage_label"] == "Achat"
    assert ratios["volume_confirme"] is False  # volume constant, pas de pic de cassure


def test_fetch_company_financials_degrades_gracefully_when_volume_column_absent(monkeypatch):
    """Les fixtures existantes de ce fichier ne renvoient qu'une colonne
    "Close" (pas de "Volume") — le code doit s'en accommoder sans
    exception, stage_label retombant sur "Neutre" au pire (jamais un
    crash), conformément aux Global Constraints du plan."""
    financials, balance_sheet, cashflow, _ = _make_fixture_statements()
    financials.columns = pd.to_datetime(financials.columns)
    balance_sheet.columns = pd.to_datetime(balance_sheet.columns)
    cashflow.columns = pd.to_datetime(cashflow.columns)
    quarterly = _fake_annual_df({"Diluted Average Shares": [100.0]}, [pd.Timestamp("2025-09-30")])
    history_index = pd.date_range("2024-01-01", periods=250, freq="D")
    history_close = pd.Series([4.80] * 250, index=history_index)

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

        def history(self, period=None):
            return pd.DataFrame({"Close": history_close})  # pas de colonne "Volume"

    monkeypatch.setattr(indices_score.yf, "Ticker", _FakeTicker)
    monkeypatch.setattr(indices_score.time, "sleep", lambda s: None)

    ratios = indices_score.fetch_company_financials("TEST2.PA")  # ne doit pas lever

    assert ratios["volume_confirme"] is False
    assert ratios["stage_label"] in ("Achat", "Déclin", "Neutre")
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `python -m pytest tests/test_indices_score.py -k "exposes_weinstein_stage or degrades_gracefully_when_volume" -v`
Expected: FAIL avec `KeyError: 'stage'` (le champ n'existe pas encore)

- [ ] **Step 3: Implémenter**

Dans `indices_score.py`, fonction `fetch_company_financials` (ligne ~2375 actuelle) :

Remplace :
```python
    history = history_source.history(period="6y")["Close"]
```
par :
```python
    history_df = history_source.history(period="6y")
    history = history_df["Close"]
    daily_volumes = (
        history_df["Volume"] if "Volume" in history_df.columns
        else pd.Series(dtype=float, index=history_df.index)
    )
```

Juste après le bloc existant `history = history.dropna()` (ligne ~2415 actuelle), ajoute :
```python
    # Même index de dates que le Close déjà nettoyé (purge NaN + conversion
    # pence/livres FTSE ci-dessus) — le Volume n'a ni l'un ni l'autre besoin
    # (pas de notion de "pence" pour un volume), mais doit rester aligné sur
    # les mêmes dates, sinon les deux séries se désynchronisent silencieusement.
    daily_volumes = daily_volumes.reindex(history.index)
```

Puis, juste avant la ligne existante `if ticker in SHARES_OUTSTANDING_FROM_MARKET_CAP_TICKERS:` (cherche cette ligne exacte), ajoute :
```python
    weekly_closes = history.resample("W").last().dropna()
    weekly_volumes = daily_volumes.resample("W").sum()
    weinstein = classify_weinstein_stage(weekly_closes, weekly_volumes)
```

Enfin, juste après la ligne existante `ratios["ecart_pct_ma200"] = ecart_pct_ma200` (cherche cette ligne exacte), ajoute :
```python
    ratios["stage"] = weinstein["stage"]
    ratios["stage_label"] = weinstein["stage_label"]
    ratios["volume_confirme"] = weinstein["volume_confirme"]
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `python -m pytest tests/test_indices_score.py -k "weinstein or fetch_company_financials" -v`
Expected: tous PASS, y compris les tests `fetch_company_financials` déjà existants (non modifiés — confirme que le graceful degradation fonctionne pour les fixtures sans colonne "Volume")

- [ ] **Step 5: Lancer toute la suite du fichier pour détecter une régression**

Run: `python -m pytest tests/test_indices_score.py -q`
Expected: tous PASS (350+ tests, aucune régression)

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "Capture le Volume et calcule la phase Weinstein dans fetch_company_financials"
```

---

### Task 3: Intégration dans `estimate_entry_exit_prices`

**Files:**
- Modify: `indices_score.py:3465-3492` (`estimate_entry_exit_prices`) et `indices_score.py:3730-3732` (son appel dans `estimate_valuation_targets`) — vérifie les numéros exacts avec `grep -n "def estimate_entry_exit_prices\|estimate_entry_exit_prices(" indices_score.py`, ils peuvent avoir bougé après les Tasks 1-2.
- Test: `tests/test_indices_score.py` (nouveaux tests à côté de `test_estimate_entry_exit_prices_*` existants)

**Interfaces:**
- Consumes: rien de nouveau des tâches précédentes (ce Task ne dépend que de la présence du champ `stage_label` dans le dict `data` passé à `estimate_valuation_targets`, déjà garanti par le Task 2).
- Produces: `estimate_entry_exit_prices(fair_value, ma200, beta, ecart_pct_ma200, stage_label=None)` — nouveau 5e paramètre optionnel, défaut `None` (= comportement actuel inchangé, comme "Neutre"). Renvoie toujours `{"entry": float|None, "exit": float|None}` (signature de sortie inchangée).

- [ ] **Step 1: Écrire les tests qui échouent**

Ajoute dans `tests/test_indices_score.py`, juste après `test_estimate_entry_exit_prices_returns_none_for_both_when_nothing_available` (cherche cette fonction) :

```python
def test_estimate_entry_exit_prices_excludes_technical_candidate_in_declin_phase():
    """Phase 4 (Déclin) : le repère technique (MM200) est exclu, seule
    la valorisation reste — ne jamais proposer un point d'entrée sur un
    titre en déclin confirmé même si son prix paraît bon marché."""
    with_declin = estimate_entry_exit_prices(
        fair_value=100.0, ma200=90.0, beta=1.0, ecart_pct_ma200=0.0, stage_label="Déclin",
    )
    without_stage = estimate_entry_exit_prices(
        fair_value=100.0, ma200=90.0, beta=1.0, ecart_pct_ma200=0.0,
    )
    # Sans la phase Déclin, la MM200 pèse dans la moyenne -> résultat différent.
    assert with_declin != without_stage
    # Avec Déclin, seule la valorisation compte -> même résultat que ma200=None.
    valuation_only = estimate_entry_exit_prices(
        fair_value=100.0, ma200=None, beta=1.0, ecart_pct_ma200=0.0,
    )
    assert with_declin == valuation_only


def test_estimate_entry_exit_prices_returns_none_in_declin_phase_without_valuation():
    result = estimate_entry_exit_prices(
        fair_value=None, ma200=90.0, beta=1.0, ecart_pct_ma200=0.0, stage_label="Déclin",
    )
    assert result == {"entry": None, "exit": None}


def test_estimate_entry_exit_prices_keeps_technical_candidate_in_achat_phase():
    """Phase 2 (Achat) : comportement inchangé par rapport à aujourd'hui."""
    with_achat = estimate_entry_exit_prices(
        fair_value=100.0, ma200=90.0, beta=1.0, ecart_pct_ma200=0.0, stage_label="Achat",
    )
    without_stage = estimate_entry_exit_prices(
        fair_value=100.0, ma200=90.0, beta=1.0, ecart_pct_ma200=0.0,
    )
    assert with_achat == without_stage


def test_estimate_entry_exit_prices_keeps_technical_candidate_when_neutre():
    with_neutre = estimate_entry_exit_prices(
        fair_value=100.0, ma200=90.0, beta=1.0, ecart_pct_ma200=0.0, stage_label="Neutre",
    )
    without_stage = estimate_entry_exit_prices(
        fair_value=100.0, ma200=90.0, beta=1.0, ecart_pct_ma200=0.0,
    )
    assert with_neutre == without_stage
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `python -m pytest tests/test_indices_score.py -k "declin_phase or achat_phase or keeps_technical_candidate_when_neutre" -v`
Expected: FAIL avec `TypeError: estimate_entry_exit_prices() got an unexpected keyword argument 'stage_label'`

- [ ] **Step 3: Implémenter**

Dans `indices_score.py`, remplace la signature et le corps de `estimate_entry_exit_prices` :

```python
def estimate_entry_exit_prices(
    fair_value: float | None, ma200: float | None,
    beta: float | None, ecart_pct_ma200: float | None,
    stage_label: str | None = None,
) -> dict:
    """Combine repère de valorisation (juste valeur ± marge pondérée par le
    bêta) et repère technique (MM200 décalée selon la dynamique récente) en
    moyennant ceux disponibles. Renvoie {"entry": float | None,
    "exit": float | None} — None des deux côtés si ni la valorisation ni la
    MM200 ne sont disponibles.

    `stage_label` (voir classify_weinstein_stage) : en phase "Déclin", le
    repère technique est exclu des candidats — proposer un point d'entrée
    fondé sur une MM200 alors que la MM30 semaines confirme un déclin
    n'a pas de sens, même si le titre paraît bon marché par la
    valorisation seule. `None` (défaut, comportement d'avant ce
    correctif) ou toute autre valeur ("Achat", "Neutre") ne change rien
    au calcul existant."""
    valuation_margin = _risk_adjusted_margin(
        beta, VALUATION_MARGIN_BASE, VALUATION_MARGIN_MIN, VALUATION_MARGIN_MAX
    )
    technical_margin = _risk_adjusted_margin(
        beta, TECHNICAL_MARGIN_BASE, TECHNICAL_MARGIN_MIN, TECHNICAL_MARGIN_MAX
    )
    momentum_adjustment = _momentum_adjustment(ecart_pct_ma200)

    entry_candidates = []
    exit_candidates = []
    if fair_value is not None:
        entry_candidates.append(fair_value * (1 - valuation_margin))
        exit_candidates.append(fair_value * (1 + valuation_margin))
    if ma200 is not None and not _is_missing(ma200) and stage_label != "Déclin":
        entry_candidates.append(ma200 * (1 + momentum_adjustment))
        exit_candidates.append(ma200 * (1 + technical_margin + momentum_adjustment))
    entry = sum(entry_candidates) / len(entry_candidates) if entry_candidates else None
    exit_price = sum(exit_candidates) / len(exit_candidates) if exit_candidates else None
    return {"entry": entry, "exit": exit_price}
```

(seul changement dans le corps : `stage_label` ajouté au paramètre, et `and stage_label != "Déclin"` ajouté à la condition du `if ma200 is not None and not _is_missing(ma200):` existant)

Puis dans `estimate_valuation_targets`, remplace :
```python
    entry_exit = estimate_entry_exit_prices(
        fair_value, data["ma200"], data["beta"], data["ecart_pct_ma200"]
    )
```
par :
```python
    entry_exit = estimate_entry_exit_prices(
        fair_value, data["ma200"], data["beta"], data["ecart_pct_ma200"],
        stage_label=data.get("stage_label"),
    )
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `python -m pytest tests/test_indices_score.py -k "estimate_entry_exit_prices or estimate_valuation_targets" -v`
Expected: tous PASS, y compris tous les tests existants de `estimate_entry_exit_prices` (non modifiés — confirme le défaut non-cassant)

- [ ] **Step 5: Lancer toute la suite du fichier**

Run: `python -m pytest tests/test_indices_score.py -q`
Expected: tous PASS

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "estimate_entry_exit_prices exclut le repère technique en phase Déclin (Weinstein)"
```

---

### Task 4: Exposer `stage`/`stage_label`/`volume_confirme` dans l'entrée finale

**Files:**
- Modify: `indices_score.py` (dict `entry` construit dans `build_company_entry`, autour de la ligne 3988-4010 actuelle — vérifie avec `grep -n "entry = {" indices_score.py`)
- Test: `tests/test_indices_score.py` (nouveau test à côté des tests existants de `build_company_entry`)

**Interfaces:**
- Consumes: `data["stage"]`, `data["stage_label"]`, `data["volume_confirme"]` (produits par `fetch_company_financials`, Task 2 — `data` est déjà le résultat de cet appel dans `build_company_entry`).
- Produces: l'entrée finale d'une société (celle publiée dans `docs/indices.json`) contient désormais les clés `"stage"`, `"stage_label"`, `"volume_confirme"`.

**Important — trouvé à la relecture du plan** : `build_company_entry` est
testée dans ce fichier en mockant `fetch_company_financials` avec des
dicts construits à la main (`_fake_ratios()` ligne 1157,
`_fake_financial_ratios()` ligne 4078, `_fake_trust_ratios()` ligne
4118 — réutilisés par une douzaine de tests). Aucun de ces 3 fixtures
n'a de clé `"stage"`/`"stage_label"`/`"volume_confirme"`. Un accès
direct `data["stage"]` dans le dict `entry` casserait ces ~12 tests
avec un `KeyError`. Utilise donc `.get()` avec repli, pas un accès
direct — conforme aux Global Constraints du plan (défaut non-cassant).

- [ ] **Step 1: Écrire un test qui échoue**

Ajoute dans `tests/test_indices_score.py`, juste après
`test_build_company_entry_uses_financial_factors_for_financial_sector_tickers`
(ligne 4097, réutilise le même monkeypatch de `fetch_company_financials`
via `_fake_financial_ratios()`) :

```python
def test_build_company_entry_exposes_weinstein_stage_fields(monkeypatch):
    monkeypatch.setattr(indices_score, "fetch_company_financials", lambda ticker: _fake_financial_ratios())
    monkeypatch.setattr(indices_score, "fetch_news", lambda name, prev=None: [])
    monkeypatch.setattr(indices_score, "generate_financial_analysis", lambda *a, **k: "<p>Analyse.</p>")

    entry = indices_score.build_company_entry("BNP.PA", "BNP Paribas", 3.0, {}, index_key="CAC40")

    # _fake_financial_ratios() ne fournit pas ces clés -> repli attendu.
    assert entry["stage"] is None
    assert entry["stage_label"] == "Neutre"
    assert entry["volume_confirme"] is False
```

- [ ] **Step 2: Vérifier que le test échoue**

Run: `python -m pytest tests/test_indices_score.py -k exposes_weinstein_stage_fields -v`
Expected: FAIL avec `KeyError: 'stage'`

- [ ] **Step 3: Implémenter**

Dans `indices_score.py`, fonction `build_company_entry`, dans le dict littéral `entry = { ... }`, ajoute 3 clés (juste après `"wacc": cost_of_capital,`) :

```python
        "wacc": cost_of_capital,
        "stage": data.get("stage"),
        "stage_label": data.get("stage_label", "Neutre"),
        "volume_confirme": data.get("volume_confirme", False),
        "financial_analysis_html": financial_analysis_html,
```

(insère les 3 nouvelles lignes entre la ligne `"wacc": cost_of_capital,` déjà existante et la ligne `"financial_analysis_html": financial_analysis_html,` déjà existante — ne réordonne pas le reste ; `.get()` avec repli, jamais un accès direct `data["stage"]`, pour ne pas casser `_fake_ratios()`/`_fake_financial_ratios()`/`_fake_trust_ratios()` qui n'ont pas ces clés)

- [ ] **Step 4: Vérifier que le test passe**

Run: `python -m pytest tests/test_indices_score.py -k exposes_weinstein_stage_fields -v`
Expected: PASS

- [ ] **Step 5: Lancer toute la suite du fichier**

Run: `python -m pytest tests/test_indices_score.py -q`
Expected: tous PASS — en particulier les ~12 tests qui mockent
`fetch_company_financials` via `_fake_ratios()`/`_fake_financial_ratios()`/
`_fake_trust_ratios()` (`grep -n 'fetch_company_financials.*lambda' tests/test_indices_score.py`
pour les lister) ne doivent PAS lever `KeyError` — c'est la preuve que
le `.get()` du Step 3 fonctionne. Vérifie aussi qu'aucun test existant
ne fait une égalité stricte `assert entry == {...}` sur le dict complet
(`grep -n "assert entry == {" tests/test_indices_score.py`) ; le cas
échéant, ajoute les 3 nouvelles clés à son dict attendu plutôt que de
le laisser rouge.

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "Expose stage/stage_label/volume_confirme dans l'entrée de chaque société"
```

---

### Task 5: Phase Weinstein dans l'email d'alerte "entrée"

**Files:**
- Modify: `indices_score.py:4087-4112` environ (`_entry_alert_context`) — vérifie le numéro exact avec `grep -n "_entry_alert_context" indices_score.py`
- Test: `tests/test_indices_score.py` (cherche les tests existants de `_entry_alert_context` pour connaître leurs fixtures `company`)

**Important — trouvé à la relecture du plan** : `test_entry_alert_context_empty_when_no_data`
(ligne 2858) attend `_entry_alert_context(company) == ""` pour un
`company` sans facteurs/actus. Son fixture `_fake_entry_alert_company`
(ligne 2818) ne pose jamais de clé `"stage_label"`. Si la ligne "Phase
Weinstein" s'affichait inconditionnellement (comme le suggère une
lecture rapide de la spec §7), ce test casserait — le contexte ne
serait plus vide. La bonne lecture : la ligne s'affiche quand
`stage_label` est réellement présent dans `company` (toujours vrai en
production après la Task 4, qui pose `.get("stage_label", "Neutre")`
sur CHAQUE entrée), mais reste absente quand `company` ne porte
vraiment aucune information dessus (le cas de ce test, qui construit
`company` à la main sans passer par `build_company_entry`) — cohérent
avec le contrat "vide si pas de donnée" déjà établi. Utilise donc
`company.get("stage_label")` **sans repli par défaut**, pas
`company.get("stage_label", "Neutre")`.

**Interfaces:**
- Consumes: `company.get("stage_label")`, `company.get("volume_confirme")` (Task 4 — toujours présents sur une entrée réelle issue de `build_company_entry` ; absents par construction dans les fixtures de test existantes qui ne posent pas ces clés).
- Produces: `_entry_alert_context(company) -> str` inclut une ligne "Phase Weinstein : ..." quand `stage_label` est présent dans `company` (signature inchangée).

- [ ] **Step 1: Écrire les tests qui échouent**

Ajoute dans `tests/test_indices_score.py`, juste après `test_entry_alert_context_empty_when_no_data` (ligne 2858) :

```python
def test_entry_alert_context_mentions_weinstein_phase():
    company = _fake_entry_alert_company(stage_label="Achat", volume_confirme=True)
    context = indices_score._entry_alert_context(company)
    assert "Phase Weinstein : Achat" in context
    assert "volume confirmé" in context


def test_entry_alert_context_mentions_neutre_phase_without_volume_suffix():
    company = _fake_entry_alert_company(stage_label="Neutre", volume_confirme=False)
    context = indices_score._entry_alert_context(company)
    assert "Phase Weinstein : Neutre" in context
    assert "volume confirmé" not in context


def test_entry_alert_context_omits_weinstein_line_when_stage_label_absent():
    """Ne casse pas le contrat existant "contexte vide si pas de
    donnée" (voir test_entry_alert_context_empty_when_no_data,
    juste au-dessus) : company sans stage_label -> pas de ligne."""
    company = _fake_entry_alert_company(factors=[], news=[])
    context = indices_score._entry_alert_context(company)
    assert "Phase Weinstein" not in context
    assert context == ""
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `python -m pytest tests/test_indices_score.py -k "mentions_weinstein_phase or mentions_neutre_phase or omits_weinstein_line" -v`
Expected: les deux premiers FAIL (assertion `"Phase Weinstein" in context` échoue — la ligne n'existe pas encore) ; le troisième PASS déjà (rien à casser avant l'implémentation — c'est normal, il documente le contrat à préserver)

- [ ] **Step 3: Implémenter**

Dans `indices_score.py`, fonction `_entry_alert_context`, juste après le bloc `if dynamique and dynamique.get("raw_value"):` existant (avant la ligne `recent_news = [...]`), ajoute :

```python
    stage_label = company.get("stage_label")
    if stage_label is not None:
        volume_suffix = " (volume confirmé)" if company.get("volume_confirme") else ""
        parts.append(
            f'<p style="color:#edeef3;font-size:13px;line-height:1.6;margin:0 0 14px;">'
            f'Phase Weinstein : {stage_label}{volume_suffix}</p>'
        )
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `python -m pytest tests/test_indices_score.py -k "entry_alert_context" -v`
Expected: tous PASS, y compris `test_entry_alert_context_empty_when_no_data` et les autres tests `_entry_alert_context`/`_entry_alert_item_html` déjà existants — aucun ne doit être modifié pour passer.

- [ ] **Step 5: Lancer toute la suite du fichier une dernière fois**

Run: `python -m pytest tests/test_indices_score.py -q`
Expected: tous PASS

- [ ] **Step 6: Lancer toute la suite du dépôt**

Run: `python -m pytest -q`
Expected: tous PASS (aucune régression ailleurs — gold_bot/ibkr_bot ne sont pas concernés par ce plan mais partagent la même commande de suite)

- [ ] **Step 7: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "Mentionne la phase Weinstein dans le contexte de l'email d'alerte entrée"
```
