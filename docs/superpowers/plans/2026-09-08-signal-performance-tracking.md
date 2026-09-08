# Suivi de performance des signaux — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Suivre automatiquement, à partir d'aujourd'hui, la performance réelle qu'aurait eue un investisseur suivant à la lettre chaque signal "entrée" émis par le volet Indices — avec deux benchmarks (indice, et "tenir 6 mois pleins") pour juger si le signal apporte une vraie valeur ajoutée.

**Architecture:** Une nouvelle fonction `update_signal_tracking()` dans `indices_score.py`, appelée depuis `main()` juste après `_attach_alerts_and_update_history()` (dont elle réutilise directement `newly_triggered_entree`), lit/écrit un nouveau fichier `docs/signal_tracking.json` (liste de positions fictives). Le frontend (`docs/index.html`) ajoute une nouvelle route `#indices/suivi` qui affiche les stats agrégées et le détail des positions.

**Tech Stack:** Python (yfinance, dateutil), fichier JSON statique, JS vanilla (déjà en place, pas de framework).

**Spec:** `docs/superpowers/specs/2026-09-08-signal-performance-tracking-design.md`

## Global Constraints

- Stop-loss : -20% (`SIGNAL_STOP_LOSS_PCT = -20.0`).
- Délai max avant clôture forcée / date du benchmark fantôme : 6 mois calendaires (`SIGNAL_SHADOW_DELAY_MONTHS = 6`, via `dateutil.relativedelta`, pas une approximation en jours).
- Ordre de priorité des conditions de clôture : stop-loss → objectif atteint → délai max.
- Une seule position `"open"` à la fois par ticker.
- `docs/signal_tracking.json` (sous `docs/`, servi statiquement comme `docs/indices.json` — PAS `indices_history.json`, qui est à la racine et non servi au frontend).
- Aucune fonction de ce chantier ne doit lever d'exception qui ferait échouer `main()` — toujours dégrader vers "ne rien changer" (même contrat que `_attach_alerts_and_update_history`).
- `python-dateutil` doit être ajouté à `requirements.txt`.

---

## Task 1: Constantes, dépendance, et I/O du fichier `signal_tracking.json`

**Files:**
- Modify: `requirements.txt`
- Modify: `indices_score.py` (ajouter après le bloc `INDICES_HISTORY_PATH`/`HISTORY_RETENTION_PER_TICKER`, vers la ligne 1485)
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Produces: `SIGNAL_TRACKING_PATH: str`, `INDEX_YFINANCE_TICKERS: dict[str, str]`, `SIGNAL_STOP_LOSS_PCT: float`, `SIGNAL_SHADOW_DELAY_MONTHS: int`, `load_signal_tracking() -> list[dict]`, `save_signal_tracking(positions: list[dict]) -> None`

- [ ] **Step 1: Ajouter `python-dateutil` à requirements.txt**

Ouvrir `requirements.txt` et ajouter une ligne :

```
requests
yfinance
pandas
trafilatura
python-dateutil
```

- [ ] **Step 2: Ajouter l'import dateutil en haut de `indices_score.py`**

Juste après `from email.utils import parsedate_to_datetime` (ligne 28) :

```python
from dateutil.relativedelta import relativedelta
```

- [ ] **Step 3: Écrire les tests (qui échouent) pour `load_signal_tracking`/`save_signal_tracking`**

Ajouter à la fin de `tests/test_indices_score.py` :

```python
def test_load_signal_tracking_returns_empty_list_when_file_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(indices_score, "SIGNAL_TRACKING_PATH", str(tmp_path / "does_not_exist.json"))
    assert indices_score.load_signal_tracking() == []


def test_load_signal_tracking_returns_empty_list_on_corrupted_json(monkeypatch, tmp_path):
    path = tmp_path / "signal_tracking.json"
    path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(indices_score, "SIGNAL_TRACKING_PATH", str(path))
    assert indices_score.load_signal_tracking() == []


def test_load_signal_tracking_returns_positions_list(monkeypatch, tmp_path):
    path = tmp_path / "signal_tracking.json"
    path.write_text(json.dumps({"positions": [{"id": "BN.PA-2026-09-08"}]}), encoding="utf-8")
    monkeypatch.setattr(indices_score, "SIGNAL_TRACKING_PATH", str(path))
    assert indices_score.load_signal_tracking() == [{"id": "BN.PA-2026-09-08"}]


def test_save_signal_tracking_writes_positions_wrapped_in_object(monkeypatch, tmp_path):
    path = tmp_path / "signal_tracking.json"
    monkeypatch.setattr(indices_score, "SIGNAL_TRACKING_PATH", str(path))
    indices_score.save_signal_tracking([{"id": "BN.PA-2026-09-08"}])
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written == {"positions": [{"id": "BN.PA-2026-09-08"}]}


def test_save_signal_tracking_creates_parent_directory(monkeypatch, tmp_path):
    """docs/ peut ne pas exister sur une éventuelle exécution locale
    from scratch — même garde-fou que le payload principal
    (os.makedirs(..., exist_ok=True) dans main())."""
    path = tmp_path / "nested" / "signal_tracking.json"
    monkeypatch.setattr(indices_score, "SIGNAL_TRACKING_PATH", str(path))
    indices_score.save_signal_tracking([])
    assert path.exists()
```

`json` et `indices_score` sont déjà importés en haut de `tests/test_indices_score.py` (voir les tests existants de `load_previous_alert_kinds` qui suivent le même schéma).

- [ ] **Step 4: Run tests to verify they fail**

Run: `python -m pytest tests/test_indices_score.py -k signal_tracking -v`
Expected: FAIL avec `AttributeError: module 'indices_score' has no attribute 'load_signal_tracking'` (ou `SIGNAL_TRACKING_PATH`).

- [ ] **Step 5: Implémenter les constantes et les 2 fonctions**

Dans `indices_score.py`, juste après le bloc existant :

```python
INDICES_HISTORY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "indices_history.json"
)
HISTORY_RETENTION_PER_TICKER = 730  # ~2 ans, une entrée par jour et par ticker
```

ajouter :

```python
SIGNAL_TRACKING_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "docs", "signal_tracking.json"
)
# Indices utilisés comme benchmark de chaque position (voir "index" sur
# chaque société — CAC40/DAX) : tickers yfinance correspondants.
INDEX_YFINANCE_TICKERS = {"CAC40": "^FCHI", "DAX": "^GDAXI"}
SIGNAL_STOP_LOSS_PCT = -20.0     # % perte déclenchant une clôture anticipée
SIGNAL_SHADOW_DELAY_MONTHS = 6   # délai max avant clôture forcée du signal
                                  # ET date du benchmark "tenir 6 mois pleins"
                                  # (même valeur, volontairement — voir
                                  # docs/superpowers/specs/2026-09-08-signal-performance-tracking-design.md)


def load_signal_tracking() -> list[dict]:
    """Positions de suivi des signaux (ouvertes et clôturées). [] si le
    fichier n'existe pas encore ou est corrompu — jamais d'exception."""
    if not os.path.exists(SIGNAL_TRACKING_PATH):
        return []
    try:
        with open(SIGNAL_TRACKING_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("positions", [])
    except (json.JSONDecodeError, AttributeError):
        return []


def save_signal_tracking(positions: list[dict]) -> None:
    """Écrit docs/signal_tracking.json — même dossier que docs/indices.json
    (servi statiquement au frontend), pas indices_history.json (racine,
    non servi)."""
    os.makedirs(os.path.dirname(SIGNAL_TRACKING_PATH), exist_ok=True)
    with open(SIGNAL_TRACKING_PATH, "w", encoding="utf-8") as fh:
        json.dump({"positions": positions}, fh, ensure_ascii=False, indent=2)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_indices_score.py -k signal_tracking -v`
Expected: 5 PASS.

- [ ] **Step 7: Run the full suite to confirm no regression**

Run: `python -m pytest tests/test_indices_score.py -q`
Expected: tous les tests passent (241 + 5 = 246 attendus à ce stade).

- [ ] **Step 8: Commit**

```bash
git add requirements.txt indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): fichier signal_tracking.json (lecture/ecriture)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: `fetch_index_prices()` — niveau du jour de CAC40/DAX

**Files:**
- Modify: `indices_score.py` (juste après les fonctions ajoutées en Task 1)
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes: `INDEX_YFINANCE_TICKERS` (Task 1)
- Produces: `fetch_index_prices() -> dict[str, float | None]` — ex. `{"CAC40": 7850.0, "DAX": 19230.0}`

- [ ] **Step 1: Écrire les tests (qui échouent)**

```python
def test_fetch_index_prices_returns_latest_close_per_index(monkeypatch):
    class FakeHistory:
        def __getitem__(self, key):
            assert key == "Close"
            import pandas as pd
            return pd.Series([7800.0, 7850.0])

    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, period):
            assert period == "5d"
            return FakeHistory()

    monkeypatch.setattr(indices_score.yf, "Ticker", FakeTicker)
    result = indices_score.fetch_index_prices()
    assert result == {"CAC40": 7850.0, "DAX": 7850.0}


def test_fetch_index_prices_degrades_to_none_per_index_on_failure(monkeypatch):
    """Une panne sur un seul indice ne doit pas empêcher de récupérer
    l'autre, ni lever d'exception."""
    class FailingTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, period):
            if self.symbol == "^FCHI":
                raise RuntimeError("panne réseau")
            import pandas as pd
            return {"Close": pd.Series([19230.0])}

    monkeypatch.setattr(indices_score.yf, "Ticker", FailingTicker)
    result = indices_score.fetch_index_prices()
    assert result["CAC40"] is None
    assert result["DAX"] == 19230.0


def test_fetch_index_prices_returns_all_none_when_yfinance_unavailable(monkeypatch):
    monkeypatch.setattr(indices_score, "yf", None)
    assert indices_score.fetch_index_prices() == {"CAC40": None, "DAX": None}
```

Note : `import pandas as pd` est déjà fait au niveau module dans `indices_score.py` (voir `_get_row_or_nan`) — les tests peuvent aussi `import pandas as pd` localement comme montré ci-dessus, `pandas` est déjà une dépendance du projet.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_indices_score.py -k fetch_index_prices -v`
Expected: FAIL avec `AttributeError: module 'indices_score' has no attribute 'fetch_index_prices'`.

- [ ] **Step 3: Implémenter**

```python
def fetch_index_prices() -> dict:
    """Niveau du jour de chaque indice suivi (voir INDEX_YFINANCE_TICKERS)
    — utilisé comme benchmark des positions de suivi des signaux. Une clé
    à None si son fetch échoue individuellement, ou si yfinance n'est pas
    installé — ne fait jamais échouer les autres indices ni lever
    d'exception."""
    if yf is None:
        return {index_key: None for index_key in INDEX_YFINANCE_TICKERS}
    prices = {}
    for index_key, yf_ticker in INDEX_YFINANCE_TICKERS.items():
        try:
            history = yf.Ticker(yf_ticker).history(period="5d")["Close"]
            prices[index_key] = float(history.iloc[-1]) if len(history) else None
        except Exception:
            prices[index_key] = None
    return prices
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_indices_score.py -k fetch_index_prices -v`
Expected: 3 PASS.

- [ ] **Step 5: Run full suite**

Run: `python -m pytest tests/test_indices_score.py -q`
Expected: tous les tests passent (246 + 3 = 249 attendus).

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): fetch_index_prices pour le benchmark indice du suivi de signaux

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: Ouverture des nouvelles positions

**Files:**
- Modify: `indices_score.py`
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes: `SIGNAL_SHADOW_DELAY_MONTHS` (Task 1), `relativedelta` (Task 1)
- Produces: `_open_new_signal_positions(positions: list[dict], newly_triggered_entree: list[dict], index_prices: dict, today: str) -> list[dict]`
  - `today` au format `"YYYY-MM-DD"`.
  - Chaque élément de `newly_triggered_entree` est un dict société complet (même forme que ceux de `companies`/`_attach_alerts_and_update_history`) : au minimum `ticker`, `name`, `index`, `current_price`, `exit_price`.

- [ ] **Step 1: Écrire les tests (qui échouent)**

```python
def test_open_new_signal_positions_creates_position_for_newly_triggered_company():
    company = {
        "ticker": "BN.PA", "name": "Danone", "index": "CAC40",
        "current_price": 100.0, "exit_price": 130.0,
    }
    positions = indices_score._open_new_signal_positions(
        [], [company], {"CAC40": 7850.0, "DAX": 19230.0}, today="2026-09-08",
    )
    assert len(positions) == 1
    p = positions[0]
    assert p["id"] == "BN.PA-2026-09-08"
    assert p["ticker"] == "BN.PA"
    assert p["name"] == "Danone"
    assert p["index"] == "CAC40"
    assert p["status"] == "open"
    assert p["entry_date"] == "2026-09-08"
    assert p["entry_price"] == 100.0
    assert p["target_exit_price"] == 130.0
    assert p["index_price_at_entry"] == 7850.0
    assert p["close_date"] is None
    assert p["close_reason"] is None
    assert p["shadow_close_date"] == "2027-03-08"
    assert p["shadow_resolved"] is False
    assert p["shadow_price"] is None


def test_open_new_signal_positions_skips_ticker_with_already_open_position():
    company = {
        "ticker": "BN.PA", "name": "Danone", "index": "CAC40",
        "current_price": 100.0, "exit_price": 130.0,
    }
    existing = [{"ticker": "BN.PA", "status": "open"}]
    positions = indices_score._open_new_signal_positions(
        existing, [company], {"CAC40": 7850.0}, today="2026-09-08",
    )
    assert len(positions) == 1  # pas de doublon, la position existante reste seule
    assert positions[0] is existing[0]


def test_open_new_signal_positions_allows_new_position_after_previous_closed():
    company = {
        "ticker": "BN.PA", "name": "Danone", "index": "CAC40",
        "current_price": 100.0, "exit_price": 130.0,
    }
    existing = [{"ticker": "BN.PA", "status": "closed"}]
    positions = indices_score._open_new_signal_positions(
        existing, [company], {"CAC40": 7850.0}, today="2026-09-08",
    )
    assert len(positions) == 2
    assert positions[1]["status"] == "open"


def test_open_new_signal_positions_skips_company_with_missing_price_data():
    """Donnée incomplète (ex: current_price/exit_price manquants) : ne
    doit jamais lever, la société est simplement ignorée pour aujourd'hui."""
    company = {
        "ticker": "BN.PA", "name": "Danone", "index": "CAC40",
        "current_price": None, "exit_price": 130.0,
    }
    positions = indices_score._open_new_signal_positions(
        [], [company], {"CAC40": 7850.0}, today="2026-09-08",
    )
    assert positions == []


def test_open_new_signal_positions_uses_none_index_price_when_index_fetch_failed():
    company = {
        "ticker": "BN.PA", "name": "Danone", "index": "CAC40",
        "current_price": 100.0, "exit_price": 130.0,
    }
    positions = indices_score._open_new_signal_positions(
        [], [company], {"CAC40": None, "DAX": None}, today="2026-09-08",
    )
    assert positions[0]["index_price_at_entry"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_indices_score.py -k open_new_signal_positions -v`
Expected: FAIL avec `AttributeError`.

- [ ] **Step 3: Implémenter**

```python
def _open_new_signal_positions(
    positions: list[dict], newly_triggered_entree: list[dict],
    index_prices: dict, today: str,
) -> list[dict]:
    """Ouvre une position pour chaque société dont le signal "entree"
    vient d'apparaître aujourd'hui (newly_triggered_entree, déjà calculé
    par _attach_alerts_and_update_history) — sauf si une position est
    déjà "open" sur ce ticker (une seule à la fois). Modifie et renvoie
    `positions`."""
    open_tickers = {p["ticker"] for p in positions if p["status"] == "open"}
    shadow_close_date = (
        datetime.strptime(today, "%Y-%m-%d").date()
        + relativedelta(months=SIGNAL_SHADOW_DELAY_MONTHS)
    ).strftime("%Y-%m-%d")
    for company in newly_triggered_entree:
        ticker = company["ticker"]
        if ticker in open_tickers:
            continue
        if company.get("current_price") is None or company.get("exit_price") is None:
            continue
        positions.append({
            "id": f"{ticker}-{today}",
            "ticker": ticker,
            "name": company["name"],
            "index": company["index"],
            "status": "open",
            "entry_date": today,
            "entry_price": company["current_price"],
            "target_exit_price": company["exit_price"],
            "index_price_at_entry": index_prices.get(company["index"]),
            "close_date": None,
            "close_price": None,
            "close_reason": None,
            "return_pct": None,
            "index_price_at_close": None,
            "index_return_pct": None,
            "shadow_close_date": shadow_close_date,
            "shadow_resolved": False,
            "shadow_price": None,
            "shadow_return_pct": None,
        })
        open_tickers.add(ticker)
    return positions
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_indices_score.py -k open_new_signal_positions -v`
Expected: 5 PASS.

- [ ] **Step 5: Run full suite**

Run: `python -m pytest tests/test_indices_score.py -q`
Expected: 249 + 5 = 254 attendus, tous PASS.

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): ouverture des positions de suivi sur signal entree

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: Clôture des positions éligibles

**Files:**
- Modify: `indices_score.py`
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes: `SIGNAL_STOP_LOSS_PCT` (Task 1)
- Produces: `_close_eligible_positions(positions: list[dict], companies_by_ticker: dict, index_prices: dict, today: str) -> list[dict]`
  - `companies_by_ticker` : `{ticker: company_dict}`, `company_dict` a au moins `current_price`.

- [ ] **Step 1: Écrire les tests (qui échouent)**

```python
def _fake_open_position(**overrides):
    position = {
        "id": "BN.PA-2026-06-08", "ticker": "BN.PA", "name": "Danone", "index": "CAC40",
        "status": "open", "entry_date": "2026-06-08", "entry_price": 100.0,
        "target_exit_price": 130.0, "index_price_at_entry": 7500.0,
        "close_date": None, "close_price": None, "close_reason": None, "return_pct": None,
        "index_price_at_close": None, "index_return_pct": None,
        "shadow_close_date": "2026-12-08", "shadow_resolved": False,
        "shadow_price": None, "shadow_return_pct": None,
    }
    position.update(overrides)
    return position


def test_close_eligible_positions_closes_on_stop_loss():
    position = _fake_open_position(entry_price=100.0)
    companies_by_ticker = {"BN.PA": {"current_price": 79.0}}  # -21%, sous le seuil -20%
    result = indices_score._close_eligible_positions(
        [position], companies_by_ticker, {"CAC40": 7600.0}, today="2026-09-08",
    )
    p = result[0]
    assert p["status"] == "closed"
    assert p["close_reason"] == "stop_loss"
    assert p["close_price"] == 79.0
    assert p["return_pct"] == pytest.approx(-21.0)
    assert p["index_price_at_close"] == 7600.0
    assert p["index_return_pct"] == pytest.approx((7600.0 - 7500.0) / 7500.0 * 100)


def test_close_eligible_positions_closes_on_target_reached():
    position = _fake_open_position(entry_price=100.0, target_exit_price=130.0)
    companies_by_ticker = {"BN.PA": {"current_price": 131.0}}
    result = indices_score._close_eligible_positions(
        [position], companies_by_ticker, {"CAC40": 7600.0}, today="2026-09-08",
    )
    assert result[0]["close_reason"] == "objectif_atteint"
    assert result[0]["return_pct"] == pytest.approx(31.0)


def test_close_eligible_positions_closes_on_delai_max_and_resolves_shadow_immediately():
    position = _fake_open_position(
        entry_price=100.0, target_exit_price=130.0, shadow_close_date="2026-09-08",
    )
    companies_by_ticker = {"BN.PA": {"current_price": 110.0}}
    result = indices_score._close_eligible_positions(
        [position], companies_by_ticker, {"CAC40": 7600.0}, today="2026-09-08",
    )
    p = result[0]
    assert p["close_reason"] == "delai_max"
    assert p["return_pct"] == pytest.approx(10.0)
    # Clôture par délai max == date fantôme atteinte le même jour : résolu tout de suite.
    assert p["shadow_resolved"] is True
    assert p["shadow_price"] == 110.0
    assert p["shadow_return_pct"] == pytest.approx(10.0)


def test_close_eligible_positions_stop_loss_takes_priority_over_target():
    """Cas limite improbable mais à couvrir explicitement : si les deux
    conditions sont vraies le même jour (n'arrive normalement jamais vu
    les seuils -20%/objectif > entrée), stop-loss est vérifié en premier
    dans l'ordre de priorité de la spec."""
    position = _fake_open_position(entry_price=100.0, target_exit_price=70.0)  # objectif sous l'entrée
    companies_by_ticker = {"BN.PA": {"current_price": 79.0}}  # <= objectif ET <= stop-loss
    result = indices_score._close_eligible_positions(
        [position], companies_by_ticker, {"CAC40": 7600.0}, today="2026-09-08",
    )
    assert result[0]["close_reason"] == "stop_loss"


def test_close_eligible_positions_leaves_open_when_no_condition_met():
    position = _fake_open_position(entry_price=100.0, target_exit_price=130.0)
    companies_by_ticker = {"BN.PA": {"current_price": 105.0}}
    result = indices_score._close_eligible_positions(
        [position], companies_by_ticker, {"CAC40": 7600.0}, today="2026-09-08",
    )
    assert result[0]["status"] == "open"


def test_close_eligible_positions_leaves_open_when_ticker_not_in_companies():
    """Ticker sorti de l'indice (ex: recomposition DAX) : pas de cours
    disponible aujourd'hui, position laissée intacte plutôt que
    clôturée sur une donnée périmée ou une exception."""
    position = _fake_open_position()
    result = indices_score._close_eligible_positions(
        [position], {}, {"CAC40": 7600.0}, today="2026-09-08",
    )
    assert result[0]["status"] == "open"


def test_close_eligible_positions_ignores_already_closed_positions():
    position = _fake_open_position(status="closed", close_price=140.0)
    companies_by_ticker = {"BN.PA": {"current_price": 79.0}}  # aurait déclenché stop-loss si "open"
    result = indices_score._close_eligible_positions(
        [position], companies_by_ticker, {"CAC40": 7600.0}, today="2026-09-08",
    )
    assert result[0]["close_price"] == 140.0  # inchangé


def test_close_eligible_positions_leaves_index_return_none_when_index_fetch_failed():
    position = _fake_open_position(entry_price=100.0, target_exit_price=130.0)
    companies_by_ticker = {"BN.PA": {"current_price": 131.0}}
    result = indices_score._close_eligible_positions(
        [position], companies_by_ticker, {"CAC40": None}, today="2026-09-08",
    )
    assert result[0]["index_price_at_close"] is None
    assert result[0]["index_return_pct"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_indices_score.py -k close_eligible_positions -v`
Expected: FAIL avec `AttributeError`.

- [ ] **Step 3: Implémenter**

```python
def _close_eligible_positions(
    positions: list[dict], companies_by_ticker: dict, index_prices: dict, today: str,
) -> list[dict]:
    """Clôture toute position "open" dont une condition est remplie —
    stop-loss (SIGNAL_STOP_LOSS_PCT) -> objectif atteint -> délai max,
    dans cet ordre de priorité. Une position dont le ticker n'est plus
    dans companies_by_ticker (sorti de l'indice) ou sans current_price
    est laissée intacte plutôt que clôturée sur une donnée périmée."""
    today_date = datetime.strptime(today, "%Y-%m-%d").date()
    for position in positions:
        if position["status"] != "open":
            continue
        company = companies_by_ticker.get(position["ticker"])
        if company is None or company.get("current_price") is None:
            continue
        current_price = company["current_price"]
        entry_price = position["entry_price"]

        close_reason = None
        if current_price <= entry_price * (1 + SIGNAL_STOP_LOSS_PCT / 100):
            close_reason = "stop_loss"
        elif current_price >= position["target_exit_price"]:
            close_reason = "objectif_atteint"
        elif today_date >= datetime.strptime(position["shadow_close_date"], "%Y-%m-%d").date():
            close_reason = "delai_max"
        if close_reason is None:
            continue

        position["status"] = "closed"
        position["close_date"] = today
        position["close_price"] = current_price
        position["close_reason"] = close_reason
        position["return_pct"] = (current_price - entry_price) / entry_price * 100

        index_price_at_close = index_prices.get(position["index"])
        position["index_price_at_close"] = index_price_at_close
        index_price_at_entry = position["index_price_at_entry"]
        if index_price_at_close is not None and index_price_at_entry:
            position["index_return_pct"] = (
                (index_price_at_close - index_price_at_entry) / index_price_at_entry * 100
            )

        if close_reason == "delai_max":
            # La date fantôme est la même que le délai max (voir
            # SIGNAL_SHADOW_DELAY_MONTHS) : résolue tout de suite plutôt
            # que d'attendre un jour de plus pour rien.
            position["shadow_resolved"] = True
            position["shadow_price"] = current_price
            position["shadow_return_pct"] = position["return_pct"]
    return positions
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_indices_score.py -k close_eligible_positions -v`
Expected: 8 PASS.

- [ ] **Step 5: Run full suite**

Run: `python -m pytest tests/test_indices_score.py -q`
Expected: 254 + 8 = 262 attendus, tous PASS.

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): cloture des positions de suivi (stop-loss/objectif/delai)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: Résolution du benchmark fantôme ("tenir 6 mois pleins")

**Files:**
- Modify: `indices_score.py`
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Produces: `_resolve_pending_shadow_benchmarks(positions: list[dict], companies_by_ticker: dict, today: str) -> list[dict]`

- [ ] **Step 1: Écrire les tests (qui échouent)**

```python
def test_resolve_pending_shadow_benchmarks_resolves_when_date_reached():
    position = _fake_open_position(
        status="closed", entry_price=100.0, shadow_close_date="2026-09-08", shadow_resolved=False,
    )
    companies_by_ticker = {"BN.PA": {"current_price": 115.0}}
    result = indices_score._resolve_pending_shadow_benchmarks(
        [position], companies_by_ticker, today="2026-09-08",
    )
    p = result[0]
    assert p["shadow_resolved"] is True
    assert p["shadow_price"] == 115.0
    assert p["shadow_return_pct"] == pytest.approx(15.0)


def test_resolve_pending_shadow_benchmarks_resolves_for_still_open_position():
    """Une position encore "open" (pas encore clôturée par le repère de
    sortie/stop-loss) mais dont la date fantôme est déjà atteinte doit
    aussi être résolue — les deux cycles de vie sont indépendants."""
    position = _fake_open_position(
        status="open", entry_price=100.0, shadow_close_date="2026-09-08", shadow_resolved=False,
    )
    companies_by_ticker = {"BN.PA": {"current_price": 90.0}}
    result = indices_score._resolve_pending_shadow_benchmarks(
        [position], companies_by_ticker, today="2026-09-08",
    )
    assert result[0]["shadow_resolved"] is True
    assert result[0]["shadow_return_pct"] == pytest.approx(-10.0)


def test_resolve_pending_shadow_benchmarks_leaves_unresolved_before_date():
    position = _fake_open_position(shadow_close_date="2026-12-08", shadow_resolved=False)
    companies_by_ticker = {"BN.PA": {"current_price": 115.0}}
    result = indices_score._resolve_pending_shadow_benchmarks(
        [position], companies_by_ticker, today="2026-09-08",
    )
    assert result[0]["shadow_resolved"] is False
    assert result[0]["shadow_price"] is None


def test_resolve_pending_shadow_benchmarks_skips_already_resolved():
    position = _fake_open_position(
        shadow_close_date="2026-09-08", shadow_resolved=True, shadow_price=999.0,
    )
    companies_by_ticker = {"BN.PA": {"current_price": 42.0}}
    result = indices_score._resolve_pending_shadow_benchmarks(
        [position], companies_by_ticker, today="2026-09-08",
    )
    assert result[0]["shadow_price"] == 999.0  # inchangé


def test_resolve_pending_shadow_benchmarks_leaves_pending_when_no_price_available():
    """Ticker sorti de l'indice ou sans cours ce jour : retenté le jour
    suivant, jamais d'exception."""
    position = _fake_open_position(shadow_close_date="2026-09-08", shadow_resolved=False)
    result = indices_score._resolve_pending_shadow_benchmarks(
        [position], {}, today="2026-09-08",
    )
    assert result[0]["shadow_resolved"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_indices_score.py -k resolve_pending_shadow -v`
Expected: FAIL avec `AttributeError`.

- [ ] **Step 3: Implémenter**

```python
def _resolve_pending_shadow_benchmarks(
    positions: list[dict], companies_by_ticker: dict, today: str,
) -> list[dict]:
    """Résout le benchmark "tenir les 6 mois pleins" pour toute position
    (ouverte OU déjà clôturée — les deux cycles de vie sont indépendants)
    dont la date fantôme est atteinte et pas encore résolue. Laisse en
    attente (retenté le jour suivant) si le ticker n'a pas de cours
    disponible aujourd'hui — jamais d'exception."""
    today_date = datetime.strptime(today, "%Y-%m-%d").date()
    for position in positions:
        if position["shadow_resolved"]:
            continue
        shadow_date = datetime.strptime(position["shadow_close_date"], "%Y-%m-%d").date()
        if today_date < shadow_date:
            continue
        company = companies_by_ticker.get(position["ticker"])
        if company is None or company.get("current_price") is None:
            continue
        shadow_price = company["current_price"]
        position["shadow_price"] = shadow_price
        position["shadow_return_pct"] = (
            (shadow_price - position["entry_price"]) / position["entry_price"] * 100
        )
        position["shadow_resolved"] = True
    return positions
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_indices_score.py -k resolve_pending_shadow -v`
Expected: 5 PASS.

- [ ] **Step 5: Run full suite**

Run: `python -m pytest tests/test_indices_score.py -q`
Expected: 262 + 5 = 267 attendus, tous PASS.

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): resolution du benchmark tenir-6-mois du suivi de signaux

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6: Orchestration `update_signal_tracking()` + branchement dans `main()`

**Files:**
- Modify: `indices_score.py` (nouvelle fonction juste après `_resolve_pending_shadow_benchmarks`, puis modifier `main()`)
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes: `load_signal_tracking`, `save_signal_tracking` (Task 1), `fetch_index_prices` (Task 2), `_open_new_signal_positions` (Task 3), `_close_eligible_positions` (Task 4), `_resolve_pending_shadow_benchmarks` (Task 5)
- Produces: `update_signal_tracking(companies: list[dict], newly_triggered_entree: list[dict]) -> list[dict]`

- [ ] **Step 1: Écrire les tests (qui échouent)**

```python
def test_update_signal_tracking_opens_closes_and_saves(monkeypatch, tmp_path):
    """Test bout en bout : preuve que update_signal_tracking cable bien
    les 3 sous-fonctions et écrit le fichier — pas seulement qu'elles
    existent en isolation."""
    path = tmp_path / "signal_tracking.json"
    monkeypatch.setattr(indices_score, "SIGNAL_TRACKING_PATH", str(path))
    monkeypatch.setattr(indices_score, "fetch_index_prices", lambda: {"CAC40": 7600.0, "DAX": 19000.0})

    company = {
        "ticker": "BN.PA", "name": "Danone", "index": "CAC40",
        "current_price": 100.0, "exit_price": 130.0,
    }
    result = indices_score.update_signal_tracking([company], [company])

    assert len(result) == 1
    assert result[0]["ticker"] == "BN.PA"
    assert result[0]["status"] == "open"
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["positions"][0]["ticker"] == "BN.PA"


def test_update_signal_tracking_degrades_gracefully_on_failure(monkeypatch, tmp_path):
    """Une panne (ex: fichier illisible, fetch_index_prices qui lève)
    ne doit jamais faire échouer main() — renvoie [] plutôt que de
    propager l'exception."""
    monkeypatch.setattr(indices_score, "fetch_index_prices", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    result = indices_score.update_signal_tracking([], [])
    assert result == []


def test_update_signal_tracking_reuses_newly_triggered_entree_from_alerts(monkeypatch, tmp_path):
    """Ne doit PAS re-détecter lui-même les signaux "entree" nouveaux —
    doit utiliser tel quel ce que _attach_alerts_and_update_history a
    déjà calculé, transmis en paramètre."""
    path = tmp_path / "signal_tracking.json"
    monkeypatch.setattr(indices_score, "SIGNAL_TRACKING_PATH", str(path))
    monkeypatch.setattr(indices_score, "fetch_index_prices", lambda: {"CAC40": 7600.0, "DAX": 19000.0})

    all_companies = [
        {"ticker": "BN.PA", "name": "Danone", "index": "CAC40", "current_price": 100.0, "exit_price": 130.0},
        {"ticker": "MC.PA", "name": "LVMH", "index": "CAC40", "current_price": 500.0, "exit_price": 600.0},
    ]
    # Seul BN.PA est dans newly_triggered_entree -> seul BN.PA doit avoir une position.
    result = indices_score.update_signal_tracking(all_companies, [all_companies[0]])
    tickers_with_position = {p["ticker"] for p in result}
    assert tickers_with_position == {"BN.PA"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_indices_score.py -k update_signal_tracking -v`
Expected: FAIL avec `AttributeError`.

- [ ] **Step 3: Implémenter**

```python
def update_signal_tracking(companies: list[dict], newly_triggered_entree: list[dict]) -> list[dict]:
    """Met à jour docs/signal_tracking.json : ouvre les nouvelles
    positions du jour (à partir de newly_triggered_entree, déjà calculé
    par _attach_alerts_and_update_history — pas re-détecté ici), clôture
    celles éligibles, résout les benchmarks fantômes arrivés à échéance,
    sauvegarde. Dégrade toujours vers [] en cas d'erreur — ne fait jamais
    échouer main(). Renvoie la liste des positions (utile aux tests/logs)."""
    try:
        positions = load_signal_tracking()
        companies_by_ticker = {c["ticker"]: c for c in companies}
        index_prices = fetch_index_prices()
        today = datetime.today().strftime("%Y-%m-%d")

        positions = _open_new_signal_positions(positions, newly_triggered_entree, index_prices, today)
        positions = _close_eligible_positions(positions, companies_by_ticker, index_prices, today)
        positions = _resolve_pending_shadow_benchmarks(positions, companies_by_ticker, today)

        save_signal_tracking(positions)
        return positions
    except Exception as e:
        print(f"Erreur suivi de performance des signaux : {e}")
        return []
```

Puis, dans `main()`, juste après la ligne existante :

```python
    newly_triggered_entree, newly_triggered_major_news = _attach_alerts_and_update_history(companies)
    send_entry_alert_email(newly_triggered_entree)
    send_major_news_alert_email(newly_triggered_major_news)
```

ajouter :

```python
    update_signal_tracking(companies, newly_triggered_entree)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_indices_score.py -k update_signal_tracking -v`
Expected: 3 PASS.

- [ ] **Step 5: Écrire un test bout-en-bout prouvant le câblage dans `main()`**

Sur le modèle de `test_main_writes_alerts_key_for_every_company` déjà présent dans le fichier :

```python
def test_main_calls_update_signal_tracking(monkeypatch, tmp_path):
    """Preuve que main() appelle réellement update_signal_tracking —
    si l'appel était supprimé de main(), ce test doit échouer."""
    monkeypatch.setattr(indices_score, "fetch_risk_free_rate", lambda: 3.68)
    monkeypatch.setattr(indices_score, "load_previous_company_analyses", lambda: {})
    monkeypatch.setattr(
        indices_score, "build_company_entry",
        lambda ticker, name, risk_free_rate, previous_analyses, index_key="CAC40": {
            "ticker": ticker, "name": name, "index": index_key,
            "score": 10.0, "interpretation": "Neutre",
            "current_price": 50.0, "entry_price": 50.0,
        },
    )
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: [])
    monkeypatch.setattr(indices_score, "append_indices_history", lambda entries: entries)
    output_path = tmp_path / "indices.json"
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(output_path))

    called_with = {}

    def _fake_update_signal_tracking(companies, newly_triggered_entree):
        called_with["companies"] = companies
        called_with["newly_triggered_entree"] = newly_triggered_entree
        return []

    monkeypatch.setattr(indices_score, "update_signal_tracking", _fake_update_signal_tracking)

    indices_score.main()

    assert "companies" in called_with
    assert len(called_with["companies"]) == len(indices_score.COMPANIES)
```

- [ ] **Step 6: Run this test to verify it passes**

Run: `python -m pytest tests/test_indices_score.py -k test_main_calls_update_signal_tracking -v`
Expected: PASS.

- [ ] **Step 7: Run full suite**

Run: `python -m pytest tests/test_indices_score.py -q`
Expected: 267 + 3 + 1 = 271 attendus, tous PASS.

- [ ] **Step 8: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): cable update_signal_tracking dans main()

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 7: Frontend — page "Suivi des signaux"

**Files:**
- Modify: `docs/index.html`

**Interfaces:**
- Consumes: `docs/signal_tracking.json` (`{"positions": [...]}`, mêmes champs que le modèle de données de la spec), `INDEX_DISPLAY_NAMES` (déjà présent), `formatPrice` (déjà présent, ligne ~753)

- [ ] **Step 1: Ajouter la route `#indices/suivi`**

Dans `parseRoute()` (ligne ~933), AVANT le `if (hash.startsWith('indices/'))` existant, insérer un cas spécial (le hash `suivi` n'est jamais un ticker réel, tous les tickers ont un suffixe `.PA`/`.DE`) :

```javascript
function parseRoute() {
  const hash = location.hash.slice(1);
  if (hash === 'or') return { screen: 'or' };
  if (hash === 'indices') return { screen: 'indices', ticker: null };
  if (hash === 'indices/suivi') return { screen: 'indices', ticker: null, view: 'suivi' };
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

- [ ] **Step 2: Ajouter la branche dans `loadIndices`**

Dans `loadIndices(ticker, view)` (ligne ~571), le bloc `if (!ticker) { renderIndicesList(...) } else { ... }` devient :

```javascript
  if (view === 'suivi') {
    loadSignalTracking();
  } else if (!ticker) {
    renderIndicesList(indicesData.companies);
  } else {
    const company = indicesData.companies.find(c => c.ticker === ticker);
    if (!company) {
      content.innerHTML = `<div class="empty">Entreprise inconnue : ${ticker}</div>`;
      return;
    }
    if (view === 'analyse') {
      renderCompanyAnalysis(company);
    } else {
      renderCompanyDetail(company);
    }
  }
```

- [ ] **Step 3: Ajouter le lien de navigation dans la liste des indices**

Dans `renderIndicesList` (repérer le bloc `document.getElementById('indicesContent').innerHTML = healthHtml + ...` déjà en place), juste avant `<div class="index-card-list">`, ajouter :

```javascript
  document.getElementById('indicesContent').innerHTML = `
    ${healthHtml}
    <a href="#indices/suivi" class="signal-tracking-link">Suivi des signaux — performance réelle des conseils →</a>
    <div class="index-card-list">${cardsHtml}</div>
  `;
```

- [ ] **Step 4: Ajouter le CSS**

Dans le `<style>`, juste après le bloc `.list-filter-toggle` existant (ligne ~208), ajouter :

```css
  .signal-tracking-link {
    display: block; background: var(--panel); border: 1px solid var(--border);
    border-radius: 10px; padding: 14px 16px; margin: 4px 0 16px;
    color: var(--gold); font-size: 13px; font-weight: 500; text-align: center;
  }

  .signal-stats { display: flex; flex-direction: column; gap: 2px; margin: 0 0 20px; }
  .signal-table-wrap { overflow-x: auto; margin: 0 0 20px; }
  .signal-table { width: 100%; border-collapse: collapse; font-size: 12px; white-space: nowrap; }
  .signal-table th {
    text-align: left; color: var(--muted); font-weight: 500; padding: 8px 10px;
    border-bottom: 1px solid var(--border);
  }
  .signal-table td { padding: 8px 10px; border-bottom: 1px solid var(--border); }
  .signal-return-positive { color: var(--gold); }
  .signal-return-negative { color: var(--rust); }
  .signal-status-open { color: var(--muted); }
```

- [ ] **Step 5: Écrire `loadSignalTracking` et `renderSignalTracking`**

Juste après la fonction `formatPrice` existante (ligne ~753), ajouter :

```javascript
function formatPct(v) {
  if (v == null) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toLocaleString('fr-FR', {maximumFractionDigits:1})}%`;
}

function pctClass(v) {
  if (v == null) return '';
  return v >= 0 ? 'signal-return-positive' : 'signal-return-negative';
}

let signalTrackingData = null;

async function loadSignalTracking() {
  const content = document.getElementById('indicesContent');
  try {
    const res = await fetch('signal_tracking.json?t=' + Date.now());
    if (!res.ok) throw new Error('signal_tracking.json introuvable');
    signalTrackingData = await res.json();
  } catch (e) {
    content.innerHTML = `<a href="#indices" class="back-link">← Indices</a><div class="empty">Suivi indisponible pour l'instant.<br>${e.message}</div>`;
    return;
  }
  renderSignalTracking(signalTrackingData.positions || []);
}

function renderSignalTracking(positions) {
  const closed = positions.filter(p => p.status === 'closed');
  const withReturn = closed.filter(p => p.return_pct != null);
  const winCount = withReturn.filter(p => p.return_pct > 0).length;
  const avg = arr => arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : null;

  const avgReturn = avg(withReturn.map(p => p.return_pct));
  const withIndex = closed.filter(p => p.return_pct != null && p.index_return_pct != null);
  const avgAlpha = avg(withIndex.map(p => p.return_pct - p.index_return_pct));
  const withShadow = closed.filter(p => p.return_pct != null && p.shadow_resolved && p.shadow_return_pct != null);
  const avgVsHold = avg(withShadow.map(p => p.return_pct - p.shadow_return_pct));

  const statsHtml = `
    <div class="signal-stats">
      <div class="price-row"><span class="price-label">Positions clôturées</span><span class="price-value">${closed.length}</span></div>
      <div class="price-row"><span class="price-label">Taux de réussite</span><span class="price-value">${withReturn.length ? formatPct(winCount / withReturn.length * 100) : '—'}</span></div>
      <div class="price-row"><span class="price-label">Rendement moyen</span><span class="price-value ${pctClass(avgReturn)}">${formatPct(avgReturn)}</span></div>
      <div class="price-row"><span class="price-label">Vs indice (alpha moyen)</span><span class="price-value ${pctClass(avgAlpha)}">${formatPct(avgAlpha)}</span></div>
      <div class="price-row"><span class="price-label">Vs tenir 6 mois pleins</span><span class="price-value ${pctClass(avgVsHold)}">${formatPct(avgVsHold)}</span></div>
    </div>`;

  const rowsHtml = positions.length ? positions.map(p => `
    <tr>
      <td>${p.name} <span class="hero-sub">${p.ticker}</span></td>
      <td>${p.entry_date}</td>
      <td>${formatPrice(p.entry_price)}</td>
      <td>${p.status === 'open' ? '<span class="signal-status-open">en cours</span>' : p.close_date}</td>
      <td>${p.status === 'open' ? '—' : formatPrice(p.close_price)}</td>
      <td>${p.close_reason || '—'}</td>
      <td class="${pctClass(p.return_pct)}">${formatPct(p.return_pct)}</td>
      <td class="${pctClass(p.return_pct != null && p.index_return_pct != null ? p.return_pct - p.index_return_pct : null)}">${p.index_return_pct != null ? formatPct(p.return_pct - p.index_return_pct) : '—'}</td>
    </tr>`).join('') : '<tr><td colspan="8" class="list-empty">Aucune position pour l\'instant.</td></tr>';

  document.getElementById('indicesContent').innerHTML = `
    <a href="#indices" class="back-link">← Indices</a>
    <div class="eyebrow">Indices</div>
    <h1>Suivi des signaux</h1>
    <p class="hero-sub" style="margin:0 0 20px;">Performance réelle des signaux "entrée" depuis leur mise en place — comparée à l'indice et à un simple achat-conservation. Pas un conseil d'investissement.</p>
    ${statsHtml}
    <div class="signal-table-wrap">
      <table class="signal-table">
        <thead><tr>
          <th>Entreprise</th><th>Entrée</th><th>Prix entrée</th><th>Clôture</th>
          <th>Prix clôture</th><th>Raison</th><th>Rendement</th><th>Vs indice</th>
        </tr></thead>
        <tbody>${rowsHtml}</tbody>
      </table>
    </div>
  `;
}
```

- [ ] **Step 6: Vérification manuelle (pas d'outillage de test frontend dans ce dépôt)**

Ce dépôt n'a pas de suite de tests JS. Vérifier à la place :
1. `python -m pytest tests/test_indices_score.py -q` passe toujours (aucun test Python ne doit être cassé par ce changement HTML/JS).
2. Relire le diff de `docs/index.html` à la recherche d'une balise non fermée, d'une accolade manquante dans les template literals, ou d'une classe CSS référencée mais non définie.
3. Ce sera vérifié en conditions réelles à la Task 8 (déploiement GitHub Pages sur la branche de travail).

- [ ] **Step 7: Commit**

```bash
git add docs/index.html
git commit -m "feat(indices): page Suivi des signaux (performance reelle vs indice/hold)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 8: Vérification en conditions réelles et merge

**Files:** aucun (git/gh uniquement)

- [ ] **Step 1: Créer la branche et pousser tout le travail**

Si les tasks précédentes n'ont pas déjà été faites sur une branche dédiée :

```bash
git checkout -b signal-performance-tracking
git push -u origin signal-performance-tracking
```

(Si une branche existe déjà avec les commits des tasks 1-7, sauter directement à l'étape suivante.)

- [ ] **Step 2: Déclencher un run réel sur la branche**

```bash
gh workflow run indices.yml --ref signal-performance-tracking
gh run list --workflow=indices.yml --branch=signal-performance-tracking --limit 1
```

Récupérer l'ID du run affiché, puis :

```bash
gh run watch <RUN_ID> --exit-status
```

- [ ] **Step 3: Vérifier le résultat**

```bash
gh run view <RUN_ID> --log | grep -i "signal\|erreur\|traceback"
```

Vérifier aussi le fichier produit :

```bash
git fetch origin signal-performance-tracking
git show origin/signal-performance-tracking:docs/signal_tracking.json
```

Attendu : au moins une position ouverte si une entreprise a un signal "entree" actif ce jour-là (sinon `{"positions": []}` est un résultat valide — pas d'échec).

- [ ] **Step 4: Vérifier la page frontend**

Ouvrir `https://alexandreauq.github.io/analyse-or/#indices/suivi` une fois la branche mergée (GitHub Pages sert `main`, pas les branches — donc ce point ne peut être vérifié qu'après le merge à l'étape 6, ou en servant `docs/` localement avec `python -m http.server` depuis `docs/` et en ouvrant `http://localhost:8000/#indices/suivi`).

- [ ] **Step 5: Synchroniser la branche locale avec le remote avant de merger**

```bash
git checkout signal-performance-tracking
git pull --ff-only origin signal-performance-tracking
git checkout main
git pull --ff-only origin main
```

- [ ] **Step 6: Merger**

```bash
git merge signal-performance-tracking --no-edit
```

En cas de conflit sur `docs/indices.json`/`indices_history.json` (fichiers auto-générés par le cron, peuvent avoir avancé entre-temps) :

```bash
git checkout --theirs docs/indices.json indices_history.json
git add docs/indices.json indices_history.json
git commit --no-edit
```

- [ ] **Step 7: Tests finaux et push**

```bash
python -m pytest tests/test_indices_score.py -q
git push origin main
```

- [ ] **Step 8: Nettoyer la branche**

```bash
git branch -d signal-performance-tracking
git push origin --delete signal-performance-tracking
```
