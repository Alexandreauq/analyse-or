# Historique de score & alertes par entreprise (Indices) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Donner à chacune des 5 entreprises pilotes un historique quotidien de son score composite et 3 types d'alertes de franchissement de seuil dérivées de cet historique, affichées sur sa page détail — miroir du mécanisme déjà en place côté Or (`score_history.json` / `compute_alerts`), adapté aux signaux disponibles côté Indices.

**Architecture:** Un nouveau fichier `indices_history.json` (racine du repo, jamais copié dans `docs/`) stocke une liste plate d'entrées `{date, ticker, composite}`, retrimée indépendamment par ticker à chaque écriture. Une fonction pure `compute_company_alerts(...)` dérive 3 types d'alertes (veille, chute rapide, conditions d'entrée réunies) à partir du sous-historique d'un ticker. Un nouvel helper `_attach_alerts_and_update_history(companies)` — appelé une fois dans `main()`, après la boucle de construction des `company_entry` — attache `company["alerts"]` à chaque entreprise et enregistre le score du jour, le tout protégé par un `try/except` qui dégrade vers `alerts=[]` plutôt que de faire échouer la publication du score déjà calculé.

**Tech Stack:** Même pipeline existant — pure Python, `json`/`datetime` déjà importés dans `indices_score.py`. Aucune nouvelle dépendance.

**Spec:** `specs/2026-09-06-historique-alertes-indices-design.md`

## Global Constraints

- Fichier d'historique : `indices_history.json` à la racine du repo (à côté de `indices_score.py`), **jamais copié dans `docs/`** — usage interne uniquement.
- Rétention : `HISTORY_RETENTION_PER_TICKER = 730` entrées, retrimées **indépendamment par ticker** (pas un cap global sur la liste entière).
- Constantes exactes : `RAPID_DROP_POINTS = 20`, `RAPID_DROP_DAYS = 5` (identiques au volet Or), `NEAR_ENTRY_PCT = 5.0` (spécifique à Indices, différent du `NEAR_SUPPORT_PCT = 1.0` de l'Or).
- `compute_company_alerts` et les fonctions d'historique ne lèvent jamais d'exception à elles seules (guard clauses uniquement) ; le câblage dans `main()` est en plus protégé par un `try/except` de dégradation.
- Nouvelle clé top-level `"alerts": [...]` dans chaque entrée `docs/indices.json["companies"]`, même forme que les alertes de l'Or (`kind`/`title`/`detail`/`date`).
- Pas de flag `daily_snapshot` (Indices tourne déjà une seule fois par jour) et pas d'email pour ces alertes.

---

## Task 1: Stockage de l'historique par ticker

**Files:**
- Modify: `indices_score.py` (ajouter constantes + 2 fonctions, juste après `OUTPUT_JSON_PATH` ligne 664-666)
- Test: `tests/test_indices_score.py` (nouveaux tests, en fin de fichier)

**Interfaces:**
- Produces: `load_indices_history() -> list[dict]`, `append_indices_history(entries: list[dict]) -> list[dict]`. Utilisées par la Tâche 3.

- [ ] **Step 1: Write the failing tests**

Ajouter à la fin de `tests/test_indices_score.py` :

```python
def test_load_indices_history_returns_empty_list_when_file_absent(tmp_path):
    missing_path = tmp_path / "does_not_exist.json"
    assert indices_score.load_indices_history(path=str(missing_path)) == []


def test_load_indices_history_returns_empty_list_on_corrupted_json(tmp_path):
    corrupted_path = tmp_path / "corrupted.json"
    corrupted_path.write_text("{not valid json", encoding="utf-8")
    assert indices_score.load_indices_history(path=str(corrupted_path)) == []


def test_append_indices_history_adds_new_entries(tmp_path):
    path = tmp_path / "history.json"
    result = indices_score.append_indices_history(
        [{"date": "2026-09-06", "ticker": "MC.PA", "composite": 42.0}],
        path=str(path),
    )
    assert result == [{"date": "2026-09-06", "ticker": "MC.PA", "composite": 42.0}]
    assert indices_score.load_indices_history(path=str(path)) == result


def test_append_indices_history_trims_independently_per_ticker(tmp_path):
    """Ajouter une entrée au ticker A ne doit jamais tronquer l'historique
    du ticker B — chaque ticker garde sa propre fenêtre de rétention."""
    import json
    path = tmp_path / "history.json"
    existing = (
        [{"date": f"2020-01-{i:02d}", "ticker": "MC.PA", "composite": float(i)} for i in range(1, 10)]
        + [{"date": f"2020-01-{i:02d}", "ticker": "TTE.PA", "composite": float(i)} for i in range(1, 5)]
    )
    path.write_text(json.dumps(existing), encoding="utf-8")

    result = indices_score.append_indices_history(
        [{"date": "2026-09-06", "ticker": "MC.PA", "composite": 99.0}],
        path=str(path),
    )
    tte_entries = [e for e in result if e["ticker"] == "TTE.PA"]
    mc_entries = [e for e in result if e["ticker"] == "MC.PA"]
    assert len(tte_entries) == 4  # inchangé
    assert len(mc_entries) == 10  # 9 existantes + 1 nouvelle
    assert mc_entries[-1] == {"date": "2026-09-06", "ticker": "MC.PA", "composite": 99.0}


def test_append_indices_history_retains_only_last_730_entries_per_ticker(tmp_path):
    import json
    path = tmp_path / "history.json"
    existing = [
        {"date": f"2020-{(i % 12) + 1:02d}-01", "ticker": "MC.PA", "composite": float(i)}
        for i in range(735)
    ]
    path.write_text(json.dumps(existing), encoding="utf-8")

    result = indices_score.append_indices_history(
        [{"date": "2026-09-06", "ticker": "MC.PA", "composite": 42.0}],
        path=str(path),
    )
    mc_entries = [e for e in result if e["ticker"] == "MC.PA"]
    assert len(mc_entries) == 730
    assert mc_entries[-1]["composite"] == 42.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k "indices_history" -v`
Expected: FAIL — `AttributeError: module 'indices_score' has no attribute 'load_indices_history'`

- [ ] **Step 3: Implement**

Dans `indices_score.py`, juste après le bloc :

```python
OUTPUT_JSON_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "docs", "indices.json"
)
```

ajouter :

```python
INDICES_HISTORY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "indices_history.json"
)
HISTORY_RETENTION_PER_TICKER = 730  # ~2 ans, une entrée par jour et par ticker


def load_indices_history(path=INDICES_HISTORY_PATH) -> list[dict]:
    """Historique quotidien du score composite par entreprise. []  si le
    fichier n'existe pas encore ou est corrompu — jamais d'exception."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError:
            return []


def append_indices_history(entries: list[dict], path=INDICES_HISTORY_PATH) -> list[dict]:
    """Ajoute les entrées du jour (une par entreprise) et retrimme chaque
    ticker indépendamment à HISTORY_RETENTION_PER_TICKER, pour que l'ajout
    d'une entreprise ne tronque jamais l'historique d'une autre."""
    history = load_indices_history(path)
    history.extend(entries)
    by_ticker: dict[str, list[dict]] = {}
    for entry in history:
        by_ticker.setdefault(entry["ticker"], []).append(entry)
    trimmed = []
    for ticker_entries in by_ticker.values():
        trimmed.extend(ticker_entries[-HISTORY_RETENTION_PER_TICKER:])
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(trimmed, fh, ensure_ascii=False, indent=2)
    return trimmed
```

(`indices_score` est déjà importé au niveau module plus haut dans
`tests/test_indices_score.py` — les 5 tests du Step 1 l'utilisent
directement via `indices_score.load_indices_history`/
`indices_score.append_indices_history`, sans import supplémentaire.
2 de ces tests ont besoin de `json.dumps` : import local `import json`
en début de fonction, comme le fait déjà une fonction existante plus
haut dans ce fichier — pas d'import `json` au niveau module ici.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "indices_history" -v`
Expected: PASS (5/5)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (121/121 — 116 existants + 5 nouveaux)

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): stockage de l'historique de score par ticker"
```

---

## Task 2: Calcul des alertes par entreprise

**Files:**
- Modify: `indices_score.py` (ajouter constantes + 1 fonction, juste après `append_indices_history`)
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes: rien de nouveau (fonction pure, prend ses arguments directement).
- Produces: `compute_company_alerts(ticker, composite, current_price, entry_price, previous_history) -> list[dict]`. Utilisée par la Tâche 3.

- [ ] **Step 1: Write the failing tests**

Ajouter à la fin de `tests/test_indices_score.py` :

```python
def test_compute_company_alerts_returns_info_when_nothing_triggers():
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=5.0, current_price=100.0, entry_price=50.0,
        previous_history=[],
    )
    assert len(alerts) == 1
    assert alerts[0]["kind"] == "info"


def test_compute_company_alerts_watch_when_score_crosses_15_upward():
    previous_history = [{"date": "2026-09-05", "ticker": "BN.PA", "composite": 10.0}]
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=20.0, current_price=100.0, entry_price=50.0,
        previous_history=previous_history,
    )
    kinds = [a["kind"] for a in alerts]
    assert "watch" in kinds


def test_compute_company_alerts_no_watch_when_already_above_15():
    """Ne doit se déclencher qu'au franchissement, pas rester actif en continu."""
    previous_history = [{"date": "2026-09-05", "ticker": "BN.PA", "composite": 20.0}]
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=22.0, current_price=100.0, entry_price=50.0,
        previous_history=previous_history,
    )
    kinds = [a["kind"] for a in alerts]
    assert "watch" not in kinds


def test_compute_company_alerts_risque_on_rapid_drop():
    from datetime import datetime, timedelta
    recent_date = (datetime.today() - timedelta(days=2)).strftime("%Y-%m-%d")
    previous_history = [{"date": recent_date, "ticker": "BN.PA", "composite": 40.0}]
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=15.0, current_price=100.0, entry_price=50.0,
        previous_history=previous_history,
    )
    kinds = [a["kind"] for a in alerts]
    assert "risque" in kinds


def test_compute_company_alerts_no_risque_when_drop_outside_window():
    from datetime import datetime, timedelta
    old_date = (datetime.today() - timedelta(days=30)).strftime("%Y-%m-%d")
    previous_history = [{"date": old_date, "ticker": "BN.PA", "composite": 40.0}]
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=15.0, current_price=100.0, entry_price=50.0,
        previous_history=previous_history,
    )
    kinds = [a["kind"] for a in alerts]
    assert "risque" not in kinds


def test_compute_company_alerts_entree_when_score_favorable_and_price_near_entry():
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=20.0, current_price=102.0, entry_price=100.0,
        previous_history=[],
    )
    kinds = [a["kind"] for a in alerts]
    assert "entree" in kinds


def test_compute_company_alerts_no_entree_when_price_far_from_entry():
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=20.0, current_price=130.0, entry_price=100.0,
        previous_history=[],
    )
    kinds = [a["kind"] for a in alerts]
    assert "entree" not in kinds


def test_compute_company_alerts_no_entree_when_score_not_favorable():
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=5.0, current_price=101.0, entry_price=100.0,
        previous_history=[],
    )
    kinds = [a["kind"] for a in alerts]
    assert "entree" not in kinds


def test_compute_company_alerts_handles_missing_current_or_entry_price():
    """Ne doit jamais lever, même si le cours ou le repère d'entrée est
    manquant (yfinance en panne, valorisation non calculable ce jour-là)."""
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=20.0, current_price=None, entry_price=None,
        previous_history=[],
    )
    assert isinstance(alerts, list)
    assert len(alerts) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k "compute_company_alerts" -v`
Expected: FAIL — `AttributeError: module 'indices_score' has no attribute 'compute_company_alerts'`

- [ ] **Step 3: Implement**

Dans `indices_score.py`, juste après `append_indices_history` :

```python
RAPID_DROP_POINTS = 20   # même seuil que le volet Or
RAPID_DROP_DAYS = 5      # même fenêtre que le volet Or
NEAR_ENTRY_PCT = 5.0     # écart max (%) au repère d'entrée pour "conditions réunies"


def compute_company_alerts(
    ticker: str, composite: float, current_price: float | None,
    entry_price: float | None, previous_history: list[dict],
) -> list[dict]:
    """Alertes de franchissement de seuil pour une entreprise, à partir de
    son propre sous-historique (déjà filtré par ticker par l'appelant).
    Ne lève jamais d'exception ; renvoie toujours au moins une alerte
    (`info` neutre si rien ne se déclenche)."""
    today_str = datetime.today().strftime("%d/%m/%Y")
    alerts = []

    prev_composite = previous_history[-1]["composite"] if previous_history else None

    if prev_composite is not None and prev_composite <= 15 < composite:
        alerts.append({
            "kind": "watch",
            "title": "Score composite a franchi +15",
            "detail": "Surveillance active enclenchée pour cette entreprise.",
            "date": today_str,
        })

    cutoff = datetime.today().date() - timedelta(days=RAPID_DROP_DAYS)
    recent = [
        e for e in previous_history
        if datetime.strptime(e["date"], "%Y-%m-%d").date() >= cutoff
    ]
    if recent:
        max_recent = max(e["composite"] for e in recent)
        drop = composite - max_recent
        if drop <= -RAPID_DROP_POINTS:
            alerts.append({
                "kind": "risque",
                "title": "Chute rapide du score composite",
                "detail": f"Repricing de {drop:+.1f} points en moins de {RAPID_DROP_DAYS} jours.",
                "date": today_str,
            })

    near_entry = (
        current_price is not None and entry_price is not None and entry_price > 0
        and abs(current_price - entry_price) / entry_price * 100 < NEAR_ENTRY_PCT
    )
    if composite > 15 and near_entry:
        alerts.append({
            "kind": "entree",
            "title": "Conditions d'entrée réunies",
            "detail": f"Score favorable, cours à moins de {NEAR_ENTRY_PCT:.0f}% du repère d'entrée.",
            "date": today_str,
        })

    if not alerts:
        alerts.append({
            "kind": "info",
            "title": "Pas de signal actif",
            "detail": "Aucune des conditions de veille, d'entrée ou de risque n'est réunie aujourd'hui.",
            "date": today_str,
        })

    return alerts
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "compute_company_alerts" -v`
Expected: PASS (9/9)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (130/130 — 121 après Tâche 1 + 9 nouveaux)

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): calcul des alertes de franchissement de seuil par entreprise"
```

---

## Task 3: Câblage dans `main()` — attacher les alertes, enregistrer l'historique

**Files:**
- Modify: `indices_score.py:958-968` (juste avant `def main():`, puis le corps de `main()`)
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes: `load_indices_history()`, `append_indices_history(entries)` (Tâche 1), `compute_company_alerts(...)` (Tâche 2).
- Produces: `_attach_alerts_and_update_history(companies: list[dict]) -> None` (mute `companies` en place, ajoute `"alerts"` à chaque élément). Utilisée directement dans `main()`.

- [ ] **Step 1: Write the failing tests**

Ajouter à la fin de `tests/test_indices_score.py` :

```python
def test_attach_alerts_and_update_history_sets_alerts_key(monkeypatch):
    companies = [
        {"ticker": "BN.PA", "score": 20.0, "current_price": 102.0, "entry_price": 100.0},
        {"ticker": "MC.PA", "score": 5.0, "current_price": 200.0, "entry_price": 150.0},
    ]
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: [])
    recorded = {}
    monkeypatch.setattr(
        indices_score, "append_indices_history",
        lambda entries: recorded.setdefault("entries", entries),
    )

    indices_score._attach_alerts_and_update_history(companies)

    assert isinstance(companies[0]["alerts"], list)
    assert len(companies[0]["alerts"]) >= 1
    assert isinstance(companies[1]["alerts"], list)
    assert recorded["entries"] == [
        {"date": recorded["entries"][0]["date"], "ticker": "BN.PA", "composite": 20.0},
        {"date": recorded["entries"][1]["date"], "ticker": "MC.PA", "composite": 5.0},
    ]


def test_attach_alerts_and_update_history_filters_history_per_ticker(monkeypatch):
    """L'historique passé à compute_company_alerts pour une entreprise ne
    doit contenir que les entrées de son propre ticker."""
    companies = [{"ticker": "BN.PA", "score": 20.0, "current_price": 102.0, "entry_price": 100.0}]
    mixed_history = [
        {"date": "2026-09-01", "ticker": "MC.PA", "composite": 99.0},
        {"date": "2026-09-01", "ticker": "BN.PA", "composite": 10.0},
    ]
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: mixed_history)
    monkeypatch.setattr(indices_score, "append_indices_history", lambda entries: entries)

    captured = {}
    original = indices_score.compute_company_alerts

    def _spy(ticker, composite, current_price, entry_price, previous_history):
        captured["previous_history"] = previous_history
        return original(ticker, composite, current_price, entry_price, previous_history)

    monkeypatch.setattr(indices_score, "compute_company_alerts", _spy)

    indices_score._attach_alerts_and_update_history(companies)

    assert captured["previous_history"] == [{"date": "2026-09-01", "ticker": "BN.PA", "composite": 10.0}]


def test_attach_alerts_and_update_history_degrades_gracefully_on_failure(monkeypatch):
    """Une panne de lecture/écriture de l'historique (disque plein,
    permissions...) ne doit jamais faire lever d'exception ni empêcher la
    publication du score déjà calculé pour chaque entreprise."""
    companies = [{"ticker": "BN.PA", "score": 20.0, "current_price": 102.0, "entry_price": 100.0}]

    def _raise():
        raise OSError("disque plein")

    monkeypatch.setattr(indices_score, "load_indices_history", _raise)

    indices_score._attach_alerts_and_update_history(companies)  # ne doit pas lever

    assert companies[0]["alerts"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_indices_score.py -k "attach_alerts_and_update_history" -v`
Expected: FAIL — `AttributeError: module 'indices_score' has no attribute '_attach_alerts_and_update_history'`

- [ ] **Step 3: Implement**

Dans `indices_score.py`, juste avant `def main():` (actuellement précédé de deux lignes vides après `build_company_entry`), ajouter :

```python
def _attach_alerts_and_update_history(companies: list[dict]) -> None:
    """Calcule les alertes de chaque entreprise à partir de son historique
    et enregistre le score du jour. Dégrade vers alerts=[] pour toutes les
    entreprises si l'historique est illisible/inscriptible — ne doit
    jamais faire échouer la publication du score déjà calculé."""
    for company in companies:
        company["alerts"] = []
    try:
        history = load_indices_history()
        today_str = datetime.today().strftime("%Y-%m-%d")
        new_entries = []
        for company in companies:
            ticker_history = [e for e in history if e["ticker"] == company["ticker"]]
            company["alerts"] = compute_company_alerts(
                company["ticker"], company["score"], company["current_price"],
                company["entry_price"], ticker_history,
            )
            new_entries.append({
                "date": today_str, "ticker": company["ticker"], "composite": company["score"],
            })
        append_indices_history(new_entries)
    except Exception as e:
        print(f"Erreur historique/alertes Indices : {e}")
```

Puis, dans `main()`, remplacer :

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

    payload = {
```

par :

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

    _attach_alerts_and_update_history(companies)

    payload = {
```

(Le reste de `main()` — écriture de `payload`/`OUTPUT_JSON_PATH` et les
`print` de fin — est inchangé ; chaque `company` porte maintenant une clé
`"alerts"` qui se retrouve automatiquement dans le JSON exporté.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_indices_score.py -k "attach_alerts_and_update_history" -v`
Expected: PASS (3/3)

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/test_indices_score.py -q`
Expected: PASS (133/133 — 130 après Tâche 2 + 3 nouveaux)

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "feat(indices): brancher les alertes et l'historique dans main()"
```

---

## Task 4: Panneau "Alertes" dans la page détail entreprise (frontend)

**Files:**
- Modify: `docs/index.html` (fonction `renderCompanyDetail`, section `.price-panel`)

**Interfaces:** Consomme `company.alerts` (Tâche 3) — même forme que les alertes déjà rendues côté Or (`kind`/`title`/`detail`/`date`). Réutilise les classes CSS `.alert-row`/`.alert-head`/`.alert-date`/`.alert-detail` et la fonction JS `alertColor(kind)` déjà définies sur la page pour l'onglet Or — aucun nouveau style à écrire.

Pas de suite de tests automatisés : `docs/index.html` n'a aucun test dans ce repo (JS inline dans une page statique) — vérification par lecture du rendu généré et, après merge, sur le site en production (mêmes précédents que les autres changements frontend de ce projet : panneau prix, couleur des actus).

- [ ] **Step 1: Localiser le point d'insertion**

Dans `docs/index.html`, la fonction `renderCompanyDetail` contient actuellement (juste après le `.price-panel`) :

```javascript
    <div class="price-panel">
      <div class="price-row">
        <span class="price-label">Cours actuel</span>
        <span class="price-value">${formatPrice(company.current_price)}</span>
      </div>
      <div class="price-row">
        <span class="price-label">Repère d'entrée</span>
        <span class="price-value" style="color:var(--gold)">${formatPrice(company.entry_price)}</span>
      </div>
      <div class="price-row">
        <span class="price-label">Repère de sortie</span>
        <span class="price-value" style="color:var(--rust)">${formatPrice(company.exit_price)}</span>
      </div>
      <p class="price-disclaimer">Repères indicatifs basés sur la valorisation
        historique (DCF, actif net, multiples) et la tendance technique —
        pas un conseil d'investissement.</p>
    </div>
    <div class="divider"></div>
```

- [ ] **Step 2: Construire le HTML des alertes, juste avant le `return` du template**

Dans la même fonction `renderCompanyDetail`, juste avant `document.getElementById('indicesContent').innerHTML = ...` (là où `factorsHtml` et `newsHtml` sont déjà construits), ajouter :

```javascript
  const alertsHtml = (company.alerts || []).map(a => `
    <div class="alert-row" style="border-left-color:${alertColor(a.kind)};">
      <div class="alert-head"><span>${a.title}</span><span class="alert-date">${a.date}</span></div>
      <p class="alert-detail">${a.detail}</p>
    </div>`).join('');
```

- [ ] **Step 3: Insérer le panneau dans le template, après le `.price-panel`**

Remplacer :

```javascript
    <p class="price-disclaimer">Repères indicatifs basés sur la valorisation
        historique (DCF, actif net, multiples) et la tendance technique —
        pas un conseil d'investissement.</p>
    </div>
    <div class="divider"></div>
```

par :

```javascript
    <p class="price-disclaimer">Repères indicatifs basés sur la valorisation
        historique (DCF, actif net, multiples) et la tendance technique —
        pas un conseil d'investissement.</p>
    </div>

    <h2>Alertes</h2>
    ${alertsHtml || '<p class="hero-sub">Aucune alerte.</p>'}

    <div class="divider"></div>
```

(La formulation `${alertsHtml || '...'}` est une sécurité : en pratique,
`company.alerts` contient toujours au moins l'alerte `info` neutre, donc
cette branche ne devrait jamais s'activer — même filet de sécurité que
celui déjà utilisé pour les alertes de l'Or à l'identique.)

- [ ] **Step 4: Vérification manuelle locale**

Ouvrir `docs/index.html` dans un navigateur avec un `docs/indices.json`
local temporairement édité pour ajouter une clé `"alerts"` de test sur
une entreprise (ex : recopier un des 3 types depuis `compute_company_alerts`
ou un mock rapide), et confirmer visuellement que le panneau "Alertes"
s'affiche avec la bonne couleur par type (`risque` en rust, `watch`/`entree`
en gold) sur la page détail de cette entreprise. Remettre `docs/indices.json`
inchangé après vérification (ne pas committer un JSON de test).

- [ ] **Step 5: Commit**

```bash
git add docs/index.html
git commit -m "feat(indices): panneau Alertes sur la page détail entreprise"
```

---

## Task 5: Documentation méthodologique

**Files:**
- Modify: `Methodologie_Analyse_Indices.md`

**Interfaces:** Aucune — tâche de documentation uniquement, aucun changement de code.

- [ ] **Step 1: Ajouter une nouvelle section, après "Interprétation du score composite"**

Dans `Methodologie_Analyse_Indices.md`, la fin du fichier contient actuellement :

```markdown
## Interprétation du score composite

Mêmes bornes que pour l'or, pour la cohérence de lecture dans l'app :

- **> +50** : profil fondamental très solide
- **+15 à +50** : solide
- **-15 à +15** : neutre
- **< -15** : fragile

## Hors périmètre (v1)

- Extension aux 40 valeurs du CAC 40 et aux valeurs financières
  (grille dédiée à construire séparément)
- Bêta désendetté puis réendetté à la structure financière de chaque
  entreprise (nécessiterait un échantillon de comparables) — le WACC
  utilise le bêta yfinance brut
- Comparaison à un échantillon de pairs sectoriels pour la valorisation
- Historique de score / alertes de franchissement de seuil par
  entreprise
```

Remplacer par :

```markdown
## Interprétation du score composite

Mêmes bornes que pour l'or, pour la cohérence de lecture dans l'app :

- **> +50** : profil fondamental très solide
- **+15 à +50** : solide
- **-15 à +15** : neutre
- **< -15** : fragile

## Historique de score & alertes

Chaque entreprise conserve un historique quotidien de son score composite
(`indices_history.json`, ~2 ans de profondeur, indépendant par ticker),
utilisé pour dériver 3 types d'alertes affichées sur sa page détail :

- **Veille** — le score vient de franchir +15 à la hausse.
- **Risque, chute rapide** — chute de 20 points ou plus en moins de 5
  jours (mêmes seuils que le volet Or).
- **Entrée, conditions réunies** — score > +15 et cours actuel à moins de
  5% du repère d'entrée (seuil propre à Indices : le repère d'entrée est
  déjà une moyenne valorisation/technique avec ±30% de marge, contrairement
  à la MM200 de l'Or qui est un niveau technique dur).

Sans déclencheur, une alerte neutre ("Pas de signal actif") est affichée.
Pas d'email pour ces alertes (affichage web uniquement), contrairement au
volet Or.

## Hors périmètre (v1)

- Extension aux 40 valeurs du CAC 40 et aux valeurs financières
  (grille dédiée à construire séparément)
- Bêta désendetté puis réendetté à la structure financière de chaque
  entreprise (nécessiterait un échantillon de comparables) — le WACC
  utilise le bêta yfinance brut
- Comparaison à un échantillon de pairs sectoriels pour la valorisation
```

(Le bullet "Historique de score / alertes de franchissement de seuil par
entreprise" disparaît de "Hors périmètre" — c'est justement ce que cette
section documente comme désormais implémenté.)

- [ ] **Step 2: Commit**

```bash
git add Methodologie_Analyse_Indices.md
git commit -m "docs(indices): documenter l'historique de score et les alertes par entreprise"
```

---

## Post-merge verification (not a task — informational)

Une fois la branche mergée et `indices.yml` exécuté au moins deux fois
(deux jours de suite, ou deux déclenchements manuels à la suite) :
confirmer que `indices_history.json` existe à la racine du repo (commité
par le workflow comme les autres fichiers générés), contient bien 2
entrées par ticker après 2 runs, et que `docs/indices.json` expose une
clé `"alerts"` non vide pour chacune des 5 entreprises (au minimum
l'alerte `info` neutre, le temps que l'historique s'étoffe). Confirmer
aussi côté frontend que le panneau "Alertes" s'affiche correctement sur
au moins une page détail d'entreprise.
