# Automatisation IBKR actions — Plan A : moteur de décision (aucun ordre réel) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construire le « cerveau » du bot actions IBKR — lecture des nouveaux signaux « entrée » du jour, dimensionnement à 500 € avec garde-fou pence/livre, résolution des contrats IBKR, plafond de 10 positions, règles de sortie identiques au paper-trading, réconciliation, et client HTTP du Gateway IBKR — le tout testable sans réseau, et **sans qu'aucun ordre réel ne puisse partir**.

**Architecture:** Un nouveau paquet `ibkr_bot/` de 6 petits modules (`state.py`, `signals.py`, `sizing.py`, `gateway.py`, `contracts.py`, `portfolio.py`), chacun testé unitairement. `gateway.py` est le seul module qui parle à IBKR : ses fonctions de passage d'ordre sont écrites et testées (mockées), mais **aucun autre module de ce Plan A ne les appelle** — un test structurel le vérifie mécaniquement. `signals.py`, `sizing.py`, `contracts.py` et `portfolio.py` décident sans jamais toucher au réseau (les appels IBKR leur sont injectés sous forme de callables). Le Plan B (plan séparé, ultérieur) ajoutera l'orchestrateur `daily.py`, la journalisation, les emails, le service systemd du Gateway et le déploiement VPS.

**Tech Stack:** Python 3.12, `requests` et `python-dateutil` (déjà dans `requirements.txt`), pytest avec `monkeypatch`/`tmp_path` (conventions établies du dépôt — voir `tests/gold_bot/test_broker.py` et `tests/gold_bot/test_state.py`).

**Spec:** `docs/superpowers/specs/2026-09-14-ibkr-equities-automation-design.md`

## Global Constraints

- **Ce plan ne passe jamais un ordre réel.** `gateway.place_market_order()` et `gateway.confirm_reply()` sont écrites et testées avec des faux objets réponse, mais aucun autre module de `ibkr_bot/` ne doit les référencer. La tâche 4 ajoute un test qui scanne les sources du paquet et échoue si un autre module les mentionne. Le Plan B ajoutera le chemin réel — ne l'ajoutez pas ici même si cela paraît trivial.
- **REVUE LA PLUS STRICTE POSSIBLE sur les tâches 3 (`sizing.py`), 4 (`gateway.py`) et 5 (`contracts.py`).** Ce sont les modules où un bug silencieux coûte de l'argent réel : une erreur d'unité de prix, un mauvais endpoint ou un mauvais contrat achète le mauvais instrument ou la mauvaise quantité, sans exception ni trace. Même discipline qu'en son temps sur `gold_bot/confluence.py`, `gold_bot/risk.py` et `gold_bot/broker.py`. Relire chaque assertion chiffrée à la main avant d'approuver la tâche.
- **Budget par position : 500 € fixes** (`BUDGET_EUR = 500.0`), pas un pourcentage du capital (spec 3.1).
- **Arrondi : partie entière inférieure** (`math.floor`) du budget converti divisé par le prix unitaire. Le reliquat reste en cash, jamais réinvesti (spec 3.3).
- **Plafond : 10 positions ouvertes par le bot** (`MAX_POSITIONS = 10`), pas 10 positions sur le compte (spec 3.4 / 9.5).
- **Règles de sortie, ordre de priorité strict, première condition remplie gagne** (spec 3.6) : 1) `prix_courant <= prix_entree * (1 - 0.20)` → `stop_loss` ; 2) `prix_courant >= target_exit_price` → `objectif_atteint` ; 3) `aujourd_hui >= date_limite` (entrée + 6 mois) → `delai_max`. Constantes : `STOP_LOSS_PCT = -20.0`, `DELAY_MONTHS = 6`.
- **`target_exit_price` est figé à l'ouverture**, repris verbatim du paper-trading, jamais recalculé (spec 3.6).
- **Le stop-loss est relatif au prix d'exécution RÉEL du bot**, pas au prix d'entrée du paper-trading (spec 3.6) — divergence assumée et documentée.
- **Aucune décision sur donnée absente** : ticker disparu ou prix courant manquant/NaN → position laissée intacte, jamais vendue (spec 3.6 / 5.5). Utiliser un garde `_is_missing()` qui couvre `None` **et** `float('nan')`.
- **Toutes les décisions (entrée comme sortie) se prennent sur les prix de `docs/indices.json`** (source yfinance), jamais sur les prix IBKR. Les prix IBKR ne servent qu'à l'exécution. Cela préserve la comparabilité avec le paper-trading (spec 1, 4.5, 6) et supprime une classe entière de bug d'unité.
- **Garde-fou pence/livre (spec 4.7, point 1)** : `docs/indices.json` contient les cours londoniens en **LIVRES (GBP)** — `indices_score.py` les divise par 100 à la source (`history = history / 100.0`, ligne ~2380). IBKR cote et exécute le LSE en **PENCE (GBp)**. Tout calcul de quantité sur un ticker `.L` doit convertir explicitement livres → pence, sous peine d'un facteur 100. Tests dédiés obligatoires.
- **Périmètre v1 : 8 indices** — `CAC40, DAX, NASDAQ, DOW, FTSE, SMI, IBEX35, FTSEMIB`. **NIKKEI225 et HANGSENG sont exclus** (spec 2) : un signal sur ces indices est rejeté avec le motif `index_hors_perimetre`.
- **4 devises** : EUR (CAC40, DAX, IBEX35, FTSEMIB), USD (NASDAQ, DOW), GBP (FTSE), CHF (SMI) — lues dans `docs/indices.json`, clé `index_currency`.
- **Endpoints IBKR réels et vérifiés uniquement.** Les chemins, méthodes HTTP et noms de champs de la tâche 4 ont été vérifiés le 2026-09-14 contre la documentation publique du Client Portal Web API et contre le client open-source `Voyz/ibind` (qui les exerce en production). **Ne pas les modifier, ne pas en inventer d'autres.** Base locale du Gateway : `https://127.0.0.1:5000` + préfixe `/v1/api`.
- **Le Gateway IBKR présente un certificat auto-signé sur localhost** : tous les appels `requests` passent `verify=False` et le module désactive l'avertissement `urllib3` correspondant. C'est sûr ici parce que la connexion ne quitte jamais la machine (spec 4.4).
- **Vocabulaire des motifs en français**, repris tel quel de la spec 4.9 pour que le journal du Plan B soit directement conforme : `signal_ignore_plafond_atteint`, `signal_ignore_prix_unitaire_superieur_au_budget`, `contrat_non_resolu`, `donnees_perimees`, `cloturee_hors_bot`.
- **Aucune nouvelle dépendance.** `requests`, `python-dateutil` et `pytest` suffisent et sont déjà déclarés (`requirements.txt`, `requirements-dev.txt`).
- **Ce plan ne modifie ni `indices_score.py`, ni `docs/signal_tracking.json`, ni `docs/indices.json`** — lecture seule (spec 1, 4.9).
- Lancer les tests depuis la racine du dépôt (`C:\Users\alexa\OneDrive\Documents\GitHub\analyse-or`) : `python -m pytest`.

---

### Task 1 : `ibkr_bot/state.py` — interrupteur d'urgence et mode simulation

**Files:**
- Create: `ibkr_bot/__init__.py` (vide)
- Create: `ibkr_bot/state.py`
- Create: `tests/ibkr_bot/__init__.py` (vide)
- Test: `tests/ibkr_bot/test_state.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: rien.
- Produces: `STATE_PATH: str`, `load_state(path: str = STATE_PATH) -> dict` (renvoie toujours un dict avec les clés `kill_switch: bool` et `dry_run: bool`), `save_state(state: dict, path: str = STATE_PATH) -> None`.

Copie conforme de `gold_bot/state.py` (lisez-le d'abord en entier) : même interface, mêmes garanties (jamais d'exception au chargement, écriture atomique), seul le chemin par défaut change. La spec 5.2/5.3 impose `dry_run: true` par défaut.

- [ ] **Step 1 : Écrire les tests qui échouent**

Créer `tests/ibkr_bot/__init__.py` (fichier vide).

Créer `tests/ibkr_bot/test_state.py` :

```python
import json
import os

import ibkr_bot.state as state


def test_load_state_returns_fallback_when_file_absent(tmp_path):
    path = str(tmp_path / "does_not_exist" / "state.json")
    result = state.load_state(path)
    assert result == {"kill_switch": False, "dry_run": True}


def test_load_state_returns_fallback_on_corrupted_json(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not valid json", encoding="utf-8")
    result = state.load_state(str(path))
    assert result == {"kill_switch": False, "dry_run": True}


def test_load_state_returns_fallback_when_json_is_not_a_dict(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    result = state.load_state(str(path))
    assert result == {"kill_switch": False, "dry_run": True}


def test_load_state_merges_partial_data_with_defaults(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"dry_run": False}), encoding="utf-8")
    result = state.load_state(str(path))
    assert result == {"kill_switch": False, "dry_run": False}


def test_load_state_coerces_non_bool_values_to_bool(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"kill_switch": 1, "dry_run": 0}), encoding="utf-8")
    result = state.load_state(str(path))
    assert result == {"kill_switch": True, "dry_run": False}


def test_save_state_then_load_state_round_trips(tmp_path):
    path = str(tmp_path / "nested" / "state.json")
    state.save_state({"kill_switch": True, "dry_run": False}, path)
    assert state.load_state(path) == {"kill_switch": True, "dry_run": False}


def test_save_state_is_atomic_no_tmp_file_left_behind(tmp_path):
    path = str(tmp_path / "state.json")
    state.save_state({"kill_switch": True, "dry_run": False}, path)
    assert not os.path.exists(path + ".tmp")
    assert state.load_state(path) == {"kill_switch": True, "dry_run": False}


def test_save_state_with_bare_relative_filename_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state.save_state({"kill_switch": False, "dry_run": True}, "bare_state.json")
    assert state.load_state("bare_state.json") == {"kill_switch": False, "dry_run": True}


def test_default_state_path_points_inside_ibkr_bot_package():
    assert state.STATE_PATH.replace("\\", "/").endswith("ibkr_bot/state.json")
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/ibkr_bot/test_state.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'ibkr_bot'`

- [ ] **Step 3 : Écrire l'implémentation minimale**

Créer `ibkr_bot/__init__.py` (fichier vide).

Créer `ibkr_bot/state.py` :

```python
# ibkr_bot/state.py
# État persistant local du bot actions IBKR (interrupteur d'urgence,
# mode simulation). Copie conforme de gold_bot/state.py — même interface,
# même sémantique, chemin par défaut différent. L'interrupteur est un
# fichier modifié en SSH, pas une route API (voir spec 5.2).
import json
import os

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")


def load_state(path: str = STATE_PATH) -> dict:
    """État de repli si le fichier n'existe pas encore, est illisible,
    ou contient du JSON qui parse mais n'est pas le dict attendu — les
    champs kill_switch/dry_run sont toujours présents et de type bool en
    sortie, jamais None ou absents. Jamais d'exception au démarrage du
    batch. dry_run vaut True par défaut (voir spec 5.3)."""
    defaults = {"kill_switch": False, "dry_run": True}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return dict(defaults)
    if not isinstance(data, dict):
        return dict(defaults)
    merged = dict(defaults)
    merged.update(data)
    merged["kill_switch"] = bool(merged.get("kill_switch", False))
    merged["dry_run"] = bool(merged.get("dry_run", True))
    return merged


def save_state(state: dict, path: str = STATE_PATH) -> None:
    """Écriture atomique (fichier temporaire puis renommage) pour qu'un
    crash en pleine écriture ne puisse jamais laisser un fichier tronqué
    que load_state lirait comme un état valide mais faux."""
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)
```

- [ ] **Step 4 : Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/ibkr_bot/test_state.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5 : Ignorer les fichiers générés à l'exécution**

Ajouter à la fin de `.gitignore` (les 5 fichiers listés en spec 4.3) :

```
ibkr_bot/state.json
ibkr_bot/positions.json
ibkr_bot/real_trading_log.jsonl
ibkr_bot/conid_cache.json
ibkr_bot/latest_account.json
```

- [ ] **Step 6 : Vérifier que rien d'autre n'est cassé**

Run: `python -m pytest`
Expected: PASS, aucune régression sur la suite existante.

- [ ] **Step 7 : Commit**

```bash
git add ibkr_bot/__init__.py ibkr_bot/state.py tests/ibkr_bot/__init__.py tests/ibkr_bot/test_state.py .gitignore
git commit -m "feat(ibkr_bot): etat local kill_switch/dry_run, dry_run par defaut

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2 : `ibkr_bot/signals.py` — nouveaux signaux du jour et rapprochement des scores

**Files:**
- Create: `ibkr_bot/signals.py`
- Test: `tests/ibkr_bot/test_signals.py`

**Interfaces:**
- Consumes: rien des tâches précédentes.
- Produces:
  - `INDICES_PATH: str`, `SIGNAL_TRACKING_PATH: str`, `INDICES_IN_SCOPE: tuple[str, ...]`
  - `_is_missing(value) -> bool`
  - `load_indices(path: str = INDICES_PATH) -> dict` (`{}` si absent/corrompu)
  - `load_signal_tracking(path: str = SIGNAL_TRACKING_PATH) -> list[dict]` (`[]` si absent/corrompu)
  - `indices_are_fresh(indices: dict, today: str) -> bool`
  - `collect_new_signals(indices: dict, positions: list[dict], today: str) -> tuple[list[dict], list[dict]]` → `(signaux, rejets)`
  - **Enregistrement « signal »** consommé par les tâches 3 et 6, clés exactes : `id: str`, `ticker: str`, `name: str`, `index: str`, `currency: str`, `entry_date: str`, `paper_entry_price: float`, `target_exit_price: float`, `score: float`, `current_price: float`.
  - **Enregistrement « rejet »** : `{"ticker": str | None, "raison": str}` avec `raison` ∈ `{"donnees_perimees", "index_hors_perimetre", "score_indisponible", "prix_indisponible"}`.

Ce module **ne réimplémente pas** la détection de nouveauté (spec 4.6) : il lit dans `docs/signal_tracking.json` les positions `status == "open"` et `entry_date == today`, qui sont par construction exactement les nouveaux signaux du jour, déjà dédoublonnés par le paper-trading. Il y récupère aussi le `target_exit_price` figé, à reprendre verbatim. Le score composite et le prix courant sont rapprochés depuis `docs/indices.json` par `ticker`.

Formes réelles des deux fichiers (relevées le 2026-09-14) — `docs/indices.json` est un dict avec les clés `updated`, `index_names`, `index_currency`, `index_prices`, `companies`, `health` ; chaque entrée de `companies` porte `ticker`, `name`, `index`, `score`, `current_price`, `entry_price`, `exit_price`, `alerts`, … `docs/signal_tracking.json` est un dict `{"positions": [...]}` où chaque position porte `id`, `ticker`, `name`, `index`, `status`, `entry_date`, `entry_price`, `target_exit_price`, `shadow_close_date`, …

- [ ] **Step 1 : Écrire les tests qui échouent**

Créer `tests/ibkr_bot/test_signals.py` :

```python
import json

import ibkr_bot.signals as signals


def _indices(updated="2026-09-14", companies=None, currencies=None):
    return {
        "updated": updated,
        "index_currency": currencies or {
            "CAC40": "EUR", "DAX": "EUR", "NASDAQ": "USD", "DOW": "USD",
            "FTSE": "GBP", "SMI": "CHF", "IBEX35": "EUR", "FTSEMIB": "EUR",
            "NIKKEI225": "JPY", "HANGSENG": "HKD",
        },
        "companies": companies if companies is not None else [],
    }


def _paper_position(**overrides):
    position = {
        "id": "GLE.PA-2026-09-14",
        "ticker": "GLE.PA",
        "name": "Societe Generale",
        "index": "CAC40",
        "status": "open",
        "entry_date": "2026-09-14",
        "entry_price": 73.63,
        "target_exit_price": 105.19,
    }
    position.update(overrides)
    return position


def test_collect_new_signals_keeps_only_today_open_positions():
    positions = [
        _paper_position(id="GLE.PA-2026-09-14", ticker="GLE.PA", entry_date="2026-09-14"),
        _paper_position(id="BN.PA-2026-09-13", ticker="BN.PA", entry_date="2026-09-13"),
        _paper_position(id="MC.PA-2026-09-14", ticker="MC.PA", entry_date="2026-09-14",
                        status="closed"),
    ]
    indices = _indices(companies=[
        {"ticker": "GLE.PA", "index": "CAC40", "score": 12.4, "current_price": 74.1},
        {"ticker": "BN.PA", "index": "CAC40", "score": 30.0, "current_price": 60.0},
        {"ticker": "MC.PA", "index": "CAC40", "score": 26.9, "current_price": 415.0},
    ])
    found, rejets = signals.collect_new_signals(indices, positions, today="2026-09-14")

    assert [s["ticker"] for s in found] == ["GLE.PA"]
    assert rejets == []


def test_collect_new_signals_does_not_rebuy_a_signal_opened_yesterday():
    """Une alerte 'entree' reste affichee plusieurs jours : la position
    paper ouverte hier est toujours 'open' aujourd'hui, elle ne doit pas
    ressortir comme un nouveau signal (voir spec 4.6)."""
    positions = [_paper_position(id="GLE.PA-2026-09-13", entry_date="2026-09-13")]
    indices = _indices(companies=[
        {"ticker": "GLE.PA", "index": "CAC40", "score": 12.4, "current_price": 74.1},
    ])
    found, rejets = signals.collect_new_signals(indices, positions, today="2026-09-14")
    assert found == []
    assert rejets == []


def test_collect_new_signals_attaches_score_currency_and_current_price():
    positions = [_paper_position()]
    indices = _indices(companies=[
        {"ticker": "GLE.PA", "index": "CAC40", "score": 12.4, "current_price": 74.1},
    ])
    found, _ = signals.collect_new_signals(indices, positions, today="2026-09-14")

    assert found[0] == {
        "id": "GLE.PA-2026-09-14",
        "ticker": "GLE.PA",
        "name": "Societe Generale",
        "index": "CAC40",
        "currency": "EUR",
        "entry_date": "2026-09-14",
        "paper_entry_price": 73.63,
        "target_exit_price": 105.19,
        "score": 12.4,
        "current_price": 74.1,
    }


def test_collect_new_signals_returns_nothing_when_indices_are_stale():
    """Donnees du jour perimees : aucun ordre ce jour-la, ni entree ni
    sortie (voir spec 4.5)."""
    positions = [_paper_position()]
    indices = _indices(updated="2026-09-13", companies=[
        {"ticker": "GLE.PA", "index": "CAC40", "score": 12.4, "current_price": 74.1},
    ])
    found, rejets = signals.collect_new_signals(indices, positions, today="2026-09-14")

    assert found == []
    assert rejets == [{"ticker": None, "raison": "donnees_perimees"}]


def test_collect_new_signals_rejects_nikkei_and_hangseng():
    positions = [
        _paper_position(id="7203.T-2026-09-14", ticker="7203.T", index="NIKKEI225"),
        _paper_position(id="0005.HK-2026-09-14", ticker="0005.HK", index="HANGSENG"),
    ]
    indices = _indices(companies=[
        {"ticker": "7203.T", "index": "NIKKEI225", "score": 40.0, "current_price": 2800.0},
        {"ticker": "0005.HK", "index": "HANGSENG", "score": 35.0, "current_price": 65.0},
    ])
    found, rejets = signals.collect_new_signals(indices, positions, today="2026-09-14")

    assert found == []
    assert rejets == [
        {"ticker": "7203.T", "raison": "index_hors_perimetre"},
        {"ticker": "0005.HK", "raison": "index_hors_perimetre"},
    ]


def test_collect_new_signals_rejects_ticker_absent_from_indices():
    positions = [_paper_position(ticker="DELISTED.PA", id="DELISTED.PA-2026-09-14")]
    indices = _indices(companies=[])
    found, rejets = signals.collect_new_signals(indices, positions, today="2026-09-14")

    assert found == []
    assert rejets == [{"ticker": "DELISTED.PA", "raison": "score_indisponible"}]


def test_collect_new_signals_rejects_missing_or_nan_score():
    positions = [
        _paper_position(id="A.PA-2026-09-14", ticker="A.PA"),
        _paper_position(id="B.PA-2026-09-14", ticker="B.PA"),
    ]
    indices = _indices(companies=[
        {"ticker": "A.PA", "index": "CAC40", "score": None, "current_price": 10.0},
        {"ticker": "B.PA", "index": "CAC40", "score": float("nan"), "current_price": 10.0},
    ])
    found, rejets = signals.collect_new_signals(indices, positions, today="2026-09-14")

    assert found == []
    assert rejets == [
        {"ticker": "A.PA", "raison": "score_indisponible"},
        {"ticker": "B.PA", "raison": "score_indisponible"},
    ]


def test_collect_new_signals_rejects_missing_or_nan_current_price():
    positions = [
        _paper_position(id="A.PA-2026-09-14", ticker="A.PA"),
        _paper_position(id="B.PA-2026-09-14", ticker="B.PA"),
    ]
    indices = _indices(companies=[
        {"ticker": "A.PA", "index": "CAC40", "score": 12.0, "current_price": None},
        {"ticker": "B.PA", "index": "CAC40", "score": 12.0, "current_price": float("nan")},
    ])
    found, rejets = signals.collect_new_signals(indices, positions, today="2026-09-14")

    assert found == []
    assert rejets == [
        {"ticker": "A.PA", "raison": "prix_indisponible"},
        {"ticker": "B.PA", "raison": "prix_indisponible"},
    ]


def test_collect_new_signals_rejects_missing_target_exit_price():
    positions = [_paper_position(target_exit_price=None)]
    indices = _indices(companies=[
        {"ticker": "GLE.PA", "index": "CAC40", "score": 12.4, "current_price": 74.1},
    ])
    found, rejets = signals.collect_new_signals(indices, positions, today="2026-09-14")

    assert found == []
    assert rejets == [{"ticker": "GLE.PA", "raison": "prix_indisponible"}]


def test_indices_are_fresh_compares_updated_to_today():
    assert signals.indices_are_fresh({"updated": "2026-09-14"}, "2026-09-14") is True
    assert signals.indices_are_fresh({"updated": "2026-09-13"}, "2026-09-14") is False
    assert signals.indices_are_fresh({}, "2026-09-14") is False


def test_load_indices_degrades_to_empty_dict(tmp_path):
    assert signals.load_indices(str(tmp_path / "absent.json")) == {}
    corrupted = tmp_path / "indices.json"
    corrupted.write_text("{not json", encoding="utf-8")
    assert signals.load_indices(str(corrupted)) == {}


def test_load_indices_reads_a_valid_file(tmp_path):
    path = tmp_path / "indices.json"
    path.write_text(json.dumps(_indices()), encoding="utf-8")
    assert signals.load_indices(str(path))["updated"] == "2026-09-14"


def test_load_signal_tracking_degrades_to_empty_list(tmp_path):
    assert signals.load_signal_tracking(str(tmp_path / "absent.json")) == []
    corrupted = tmp_path / "signal_tracking.json"
    corrupted.write_text("[[[", encoding="utf-8")
    assert signals.load_signal_tracking(str(corrupted)) == []


def test_load_signal_tracking_reads_positions_key(tmp_path):
    path = tmp_path / "signal_tracking.json"
    path.write_text(json.dumps({"positions": [_paper_position()]}), encoding="utf-8")
    result = signals.load_signal_tracking(str(path))
    assert [p["ticker"] for p in result] == ["GLE.PA"]


def test_is_missing_covers_none_and_nan():
    assert signals._is_missing(None) is True
    assert signals._is_missing(float("nan")) is True
    assert signals._is_missing(0.0) is False
    assert signals._is_missing(12.4) is False
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/ibkr_bot/test_signals.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'ibkr_bot.signals'`

- [ ] **Step 3 : Écrire l'implémentation minimale**

Créer `ibkr_bot/signals.py` :

```python
# ibkr_bot/signals.py
# Lecture des nouveaux signaux "entree" du jour et rapprochement du score
# composite. Ce module NE REIMPLEMENTE PAS la detection de nouveaute : le
# paper-trading (indices_score.update_signal_tracking) l'a deja faite, et
# les positions "open" ouvertes aujourd'hui dans docs/signal_tracking.json
# sont par construction exactement les nouveaux signaux du jour, deja
# dedoublonnes (voir spec 4.6). Lecture seule : ce module n'ecrit jamais
# dans docs/.
import json
import math
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDICES_PATH = os.path.join(_REPO_ROOT, "docs", "indices.json")
SIGNAL_TRACKING_PATH = os.path.join(_REPO_ROOT, "docs", "signal_tracking.json")

# Perimetre v1 : Nikkei 225 et Hang Seng sont volontairement exclus du
# passage au reel (JPY/HKD + session asiatique hors de la fenetre du
# batch) — ils continuent d'etre scores et paper-trades (voir spec 2).
INDICES_IN_SCOPE = ("CAC40", "DAX", "NASDAQ", "DOW", "FTSE", "SMI", "IBEX35", "FTSEMIB")


def _is_missing(value) -> bool:
    """True si une valeur numerique est absente ou NaN. Meme garde que
    indices_score._is_missing : un NaN qui passe silencieusement rend
    toutes les comparaisons de prix fausses sans lever d'exception."""
    try:
        return math.isnan(value)
    except TypeError:
        return value is None


def load_indices(path: str = INDICES_PATH) -> dict:
    """Contenu de docs/indices.json. {} si le fichier est absent ou
    corrompu — jamais d'exception."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_signal_tracking(path: str = SIGNAL_TRACKING_PATH) -> list[dict]:
    """Positions de paper-trading (ouvertes et cloturees). [] si le
    fichier est absent ou corrompu — jamais d'exception."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    positions = data.get("positions", [])
    return positions if isinstance(positions, list) else []


def indices_are_fresh(indices: dict, today: str) -> bool:
    """Garde de fraicheur non negociable (spec 4.5) : trader sur les
    scores d'hier reviendrait a ouvrir des positions sur des signaux deja
    consommes par le paper-trading."""
    return indices.get("updated") == today


def collect_new_signals(
    indices: dict, positions: list[dict], today: str,
) -> tuple[list[dict], list[dict]]:
    """Nouveaux signaux du jour, enrichis du score composite, de la devise
    de l'indice et du prix courant. Renvoie (signaux, rejets) — chaque
    rejet porte son motif, pour que le journal du batch (Plan B) rende
    visible tout signal ecarte plutot que de le perdre silencieusement."""
    if not indices_are_fresh(indices, today):
        return [], [{"ticker": None, "raison": "donnees_perimees"}]

    currencies = indices.get("index_currency", {})
    companies_by_ticker = {c["ticker"]: c for c in indices.get("companies", [])}

    found: list[dict] = []
    rejets: list[dict] = []
    for position in positions:
        if position.get("status") != "open" or position.get("entry_date") != today:
            continue
        ticker = position["ticker"]
        if position.get("index") not in INDICES_IN_SCOPE:
            rejets.append({"ticker": ticker, "raison": "index_hors_perimetre"})
            continue
        company = companies_by_ticker.get(ticker)
        if company is None or _is_missing(company.get("score")):
            rejets.append({"ticker": ticker, "raison": "score_indisponible"})
            continue
        if (_is_missing(company.get("current_price"))
                or _is_missing(position.get("target_exit_price"))
                or _is_missing(position.get("entry_price"))):
            rejets.append({"ticker": ticker, "raison": "prix_indisponible"})
            continue
        found.append({
            "id": position["id"],
            "ticker": ticker,
            "name": position.get("name", ""),
            "index": position["index"],
            "currency": currencies.get(position["index"], ""),
            "entry_date": position["entry_date"],
            "paper_entry_price": position["entry_price"],
            "target_exit_price": position["target_exit_price"],
            "score": company["score"],
            "current_price": company["current_price"],
        })
    return found, rejets
```

- [ ] **Step 4 : Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/ibkr_bot/test_signals.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5 : Vérifier contre les données réelles du dépôt**

Run:
```bash
python -c "import ibkr_bot.signals as s; i=s.load_indices(); p=s.load_signal_tracking(); print('updated=', i.get('updated')); print('positions=', len(p)); print(s.collect_new_signals(i, p, i.get('updated','')))"
```
Expected: `updated=` une date réelle, `positions=` un entier non nul, et un tuple `([...], [...])` sans exception. C'est une vérification de forme (les chemins par défaut pointent bien sur les vrais fichiers), pas une assertion de contenu.

- [ ] **Step 6 : Commit**

```bash
git add ibkr_bot/signals.py tests/ibkr_bot/test_signals.py
git commit -m "feat(ibkr_bot): lecture des nouveaux signaux du jour et rapprochement des scores

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3 : `ibkr_bot/sizing.py` — conversion de devise, arrondi entier, garde-fou pence/livre

> **REVUE LA PLUS STRICTE POSSIBLE.** C'est le module où un bug silencieux achète 100 fois trop d'actions. Relire chaque assertion chiffrée à la main : refaire le calcul soi-même, ne pas se contenter de voir le test passer.

**Files:**
- Create: `ibkr_bot/sizing.py`
- Test: `tests/ibkr_bot/test_sizing.py`

**Interfaces:**
- Consumes: l'enregistrement « signal » de la tâche 2 (clés `ticker`, `currency`, `current_price`) — mais les fonctions prennent des scalaires, pas le dict, pour rester testables isolément.
- Produces:
  - `BUDGET_EUR: float = 500.0`, `PENCE_SUFFIX: str = ".L"`, `PENCE_PER_POUND: float = 100.0`
  - `is_pence_quoted(ticker: str) -> bool`
  - `quotation_currency(index_currency: str, ticker: str) -> str`
  - `to_quotation_price(price_indices: float, ticker: str) -> float` (GBP → GBp pour `.L`, identité sinon)
  - `from_quotation_price(price_quotation: float, ticker: str) -> float` (GBp → GBP pour `.L`, identité sinon)
  - `compute_quantity(ticker: str, index_currency: str, unit_price_indices: float, fx_rate: float, budget_eur: float = BUDGET_EUR) -> dict`
  - **Enregistrement « plan »** consommé par la tâche 6, clés exactes : `ticker: str`, `quantite: int`, `devise_cotation: str`, `devise_compte: str`, `taux_de_change: float`, `budget_converti: float` (en devise de cotation), `prix_unitaire_cotation: float` (en devise de cotation), `cout_estime_devise_compte: float`, `motif: str | None`.
  - Motifs possibles : `None`, `"signal_ignore_prix_unitaire_superieur_au_budget"`, `"prix_ou_taux_invalide"`.

**Le piège, en une phrase** : `docs/indices.json` exprime les cours LSE en **livres** (`indices_score.py` fait `history = history / 100.0`, ligne ~2380, avec un long commentaire d'explication — allez le lire), IBKR les exprime en **pence**. Si le budget est converti en pence mais le prix laissé en livres, la quantité est **100× trop grande**. Si c'est l'inverse, elle est 100× trop petite. La règle appliquée ici : **budget et prix sont toujours ramenés à la même unité, et cette unité est celle d'IBKR (la devise de cotation)** — pence pour `.L`, la devise de l'indice partout ailleurs.

Deux devises coexistent donc dans l'enregistrement « plan », et il ne faut jamais les confondre :
- `devise_cotation` = l'unité dans laquelle IBKR cote et exécute (`GBp` pour `.L`, sinon identique à la devise de l'indice) → sert au calcul de quantité et à la comparaison avec `mktPrice` IBKR.
- `devise_compte` = la devise du ledger IBKR (`GBP` pour `.L`, c'est-à-dire toujours la devise de l'indice) → sert au garde-fou de solde (spec 9.9).

- [ ] **Step 1 : Écrire les tests qui échouent**

Créer `tests/ibkr_bot/test_sizing.py` :

```python
import math

import pytest

import ibkr_bot.sizing as sizing


# --- helpers d'unite -------------------------------------------------

def test_is_pence_quoted_only_for_london_suffix():
    assert sizing.is_pence_quoted("III.L") is True
    assert sizing.is_pence_quoted("ABDN.L") is True
    assert sizing.is_pence_quoted("MC.PA") is False
    assert sizing.is_pence_quoted("ADBE") is False
    assert sizing.is_pence_quoted("ABBN.SW") is False


def test_quotation_currency_maps_gbp_to_pence_only_for_london():
    assert sizing.quotation_currency("GBP", "III.L") == "GBp"
    assert sizing.quotation_currency("EUR", "MC.PA") == "EUR"
    assert sizing.quotation_currency("USD", "ADBE") == "USD"
    assert sizing.quotation_currency("CHF", "ABBN.SW") == "CHF"


def test_to_quotation_price_multiplies_by_100_only_for_london():
    assert sizing.to_quotation_price(2.45, "III.L") == pytest.approx(245.0)
    assert sizing.to_quotation_price(415.0, "MC.PA") == 415.0
    assert sizing.to_quotation_price(50.0, "ADBE") == 50.0


def test_from_quotation_price_is_the_exact_inverse():
    assert sizing.from_quotation_price(245.0, "III.L") == pytest.approx(2.45)
    assert sizing.from_quotation_price(415.0, "MC.PA") == 415.0
    for price in (2.45, 26.6, 0.9412):
        round_tripped = sizing.from_quotation_price(
            sizing.to_quotation_price(price, "III.L"), "III.L")
        assert round_tripped == pytest.approx(price)


# --- arrondi entier et reliquat --------------------------------------

def test_compute_quantity_floors_and_leaves_the_remainder_in_cash():
    """500 EUR / 415 EUR = 1.204... -> 1 action, le reliquat (85 EUR)
    reste en cash, jamais reinvesti (voir spec 3.3)."""
    plan = sizing.compute_quantity("MC.PA", "EUR", 415.0, 1.0)

    assert plan["quantite"] == 1
    assert plan["devise_cotation"] == "EUR"
    assert plan["devise_compte"] == "EUR"
    assert plan["budget_converti"] == pytest.approx(500.0)
    assert plan["prix_unitaire_cotation"] == pytest.approx(415.0)
    assert plan["cout_estime_devise_compte"] == pytest.approx(415.0)
    assert plan["motif"] is None
    assert plan["ticker"] == "MC.PA"


def test_compute_quantity_floors_a_non_integer_ratio():
    plan = sizing.compute_quantity("BN.PA", "EUR", 120.0, 1.0)
    assert plan["quantite"] == 4  # 500 / 120 = 4.166...
    assert plan["cout_estime_devise_compte"] == pytest.approx(480.0)


def test_compute_quantity_on_an_exact_boundary_does_not_round_up():
    plan = sizing.compute_quantity("BN.PA", "EUR", 250.0, 1.0)
    assert plan["quantite"] == 2  # 500 / 250 = exactement 2


# --- conversion de devise --------------------------------------------

def test_compute_quantity_converts_to_usd():
    plan = sizing.compute_quantity("ADBE", "USD", 50.0, 1.08)
    assert plan["budget_converti"] == pytest.approx(540.0)
    assert plan["quantite"] == 10  # 540 / 50 = 10.8
    assert plan["devise_cotation"] == "USD"
    assert plan["taux_de_change"] == pytest.approx(1.08)
    assert plan["cout_estime_devise_compte"] == pytest.approx(500.0)


def test_compute_quantity_converts_to_chf():
    plan = sizing.compute_quantity("ABBN.SW", "CHF", 100.0, 0.94)
    assert plan["budget_converti"] == pytest.approx(470.0)
    assert plan["quantite"] == 4  # 470 / 100 = 4.7
    assert plan["devise_cotation"] == "CHF"
    assert plan["cout_estime_devise_compte"] == pytest.approx(400.0)


def test_compute_quantity_with_rate_one_for_eur_is_a_no_op_conversion():
    plan = sizing.compute_quantity("SAP.DE", "EUR", 100.0, 1.0)
    assert plan["budget_converti"] == pytest.approx(500.0)
    assert plan["quantite"] == 5


# --- GARDE-FOU PENCE / LIVRE (spec 4.7 point 1, spec 7) --------------

def test_compute_quantity_on_london_ticker_is_not_100x_too_large():
    """indices.json donne 2.45 GBP ; IBKR cote 245 GBp. Budget 500 EUR au
    taux 0.86 = 430 GBP = 43 000 GBp. 43 000 / 245 = 175.5 -> 175 actions.
    Le bug a 100x (budget en pence / prix en livres) donnerait 17 551 ;
    le bug inverse (budget en livres / prix en pence) donnerait 1."""
    plan = sizing.compute_quantity("III.L", "GBP", 2.45, 0.86)

    assert plan["quantite"] == 175
    assert plan["quantite"] != 17551
    assert plan["quantite"] != 1
    assert plan["devise_cotation"] == "GBp"
    assert plan["devise_compte"] == "GBP"
    assert plan["budget_converti"] == pytest.approx(43000.0)
    assert plan["prix_unitaire_cotation"] == pytest.approx(245.0)
    assert plan["cout_estime_devise_compte"] == pytest.approx(175 * 2.45)
    assert plan["motif"] is None


def test_compute_quantity_on_expensive_london_ticker_is_not_100x_too_large():
    """30 GBP = 3 000 GBp. 43 000 / 3 000 = 14.33 -> 14 actions.
    Le bug a 100x donnerait 1 433."""
    plan = sizing.compute_quantity("AZN.L", "GBP", 30.0, 0.86)

    assert plan["quantite"] == 14
    assert plan["quantite"] != 1433
    assert plan["cout_estime_devise_compte"] == pytest.approx(420.0)


def test_compute_quantity_on_london_ticker_above_budget_buys_nothing():
    """500 GBP = 50 000 GBp > 43 000 GBp de budget -> 0 action. Le bug a
    100x donnerait 86 actions, soit ~43 000 GBP engages au lieu de 430."""
    plan = sizing.compute_quantity("XPENSIVE.L", "GBP", 500.0, 0.86)

    assert plan["quantite"] == 0
    assert plan["quantite"] != 86
    assert plan["motif"] == "signal_ignore_prix_unitaire_superieur_au_budget"
    assert plan["cout_estime_devise_compte"] == 0.0


def test_compute_quantity_on_penny_london_ticker_stays_coherent():
    """Cours tres bas (0.9412 GBP = 94.12 GBp) : 43 000 / 94.12 = 456.86
    -> 456 actions."""
    plan = sizing.compute_quantity("LLOY.L", "GBP", 0.9412, 0.86)
    assert plan["quantite"] == 456


# --- cas 0 action et entrees invalides -------------------------------

def test_compute_quantity_returns_zero_and_a_reason_when_price_exceeds_budget():
    """Cas limite explicite de la spec 3.3 : aucun ordre n'est passe, la
    position ne s'ouvre pas, et le motif doit etre journalisable. Ne
    JAMAIS acheter 1 action 'au moins'."""
    plan = sizing.compute_quantity("MC.PA", "EUR", 600.0, 1.0)

    assert plan["quantite"] == 0
    assert plan["motif"] == "signal_ignore_prix_unitaire_superieur_au_budget"
    assert plan["prix_unitaire_cotation"] == pytest.approx(600.0)
    assert plan["budget_converti"] == pytest.approx(500.0)
    assert plan["cout_estime_devise_compte"] == 0.0


@pytest.mark.parametrize("price", [0.0, -3.0, None, float("nan")])
def test_compute_quantity_rejects_invalid_price(price):
    plan = sizing.compute_quantity("MC.PA", "EUR", price, 1.0)
    assert plan["quantite"] == 0
    assert plan["motif"] == "prix_ou_taux_invalide"


@pytest.mark.parametrize("rate", [0.0, -1.0, None, float("nan")])
def test_compute_quantity_rejects_invalid_fx_rate(rate):
    plan = sizing.compute_quantity("ADBE", "USD", 50.0, rate)
    assert plan["quantite"] == 0
    assert plan["motif"] == "prix_ou_taux_invalide"


def test_compute_quantity_rejects_non_positive_budget():
    plan = sizing.compute_quantity("MC.PA", "EUR", 50.0, 1.0, budget_eur=0.0)
    assert plan["quantite"] == 0
    assert plan["motif"] == "prix_ou_taux_invalide"


def test_compute_quantity_never_raises_on_garbage_input():
    plan = sizing.compute_quantity("MC.PA", "EUR", "pas un nombre", 1.0)
    assert plan["quantite"] == 0
    assert plan["motif"] == "prix_ou_taux_invalide"


def test_budget_constant_is_500_eur():
    assert sizing.BUDGET_EUR == 500.0
    assert math.isclose(sizing.PENCE_PER_POUND, 100.0)
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/ibkr_bot/test_sizing.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'ibkr_bot.sizing'`

- [ ] **Step 3 : Écrire l'implémentation minimale**

Créer `ibkr_bot/sizing.py` :

```python
# ibkr_bot/sizing.py
# Conversion du budget fixe de 500 EUR vers la devise locale, arrondi a
# l'action entiere inferieure, et GARDE-FOU PENCE/LIVRE pour le LSE.
#
# LE PIEGE (voir spec 4.7 point 1, deja rencontre et corrige dans ce
# depot le 2026-09-13) : yfinance renvoie les cours londoniens en PENCE
# (GBp) ; indices_score.py les divise par 100 a la source (chercher
# `history = history / 100.0`), donc docs/indices.json contient des
# LIVRES (GBP). IBKR, lui, cote et execute le LSE en PENCE. Melanger les
# deux unites donne une quantite 100x trop grande (budget en pence /
# prix en livres) ou 100x trop petite (l'inverse).
#
# LA REGLE : budget et prix sont TOUJOURS ramenes a la meme unite, et
# cette unite est celle d'IBKR (la "devise de cotation"). On distingue
# donc explicitement :
#   - devise_cotation : GBp pour .L, la devise de l'indice ailleurs.
#     Sert au calcul de quantite et aux comparaisons avec mktPrice IBKR.
#   - devise_compte   : toujours la devise de l'indice (GBP pour .L).
#     Sert au garde-fou de solde par devise (voir spec 9.9).
import math

BUDGET_EUR = 500.0          # budget nominal par position (spec 3.1)
PENCE_SUFFIX = ".L"         # suffixe yfinance du London Stock Exchange
PENCE_PER_POUND = 100.0


def _is_positive_number(value) -> bool:
    """True seulement pour un nombre fini et strictement positif. Un NaN
    passe tous les tests de comparaison sans lever : il doit etre rejete
    explicitement, sinon math.floor(nan) leve plus loin, ou pire, une
    comparaison silencieusement fausse laisse passer un ordre."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if math.isnan(value) or math.isinf(value):
        return False
    return value > 0


def is_pence_quoted(ticker: str) -> bool:
    """True pour les tickers du LSE, cotes en pence (GBp) chez IBKR."""
    return isinstance(ticker, str) and ticker.endswith(PENCE_SUFFIX)


def quotation_currency(index_currency: str, ticker: str) -> str:
    """Devise dans laquelle IBKR cote et execute ce ticker."""
    return "GBp" if is_pence_quoted(ticker) else index_currency


def to_quotation_price(price_indices: float, ticker: str) -> float:
    """Prix de docs/indices.json (livres pour le LSE) converti dans
    l'unite de cotation IBKR (pence pour le LSE)."""
    if is_pence_quoted(ticker):
        return price_indices * PENCE_PER_POUND
    return price_indices


def from_quotation_price(price_quotation: float, ticker: str) -> float:
    """Inverse exact de to_quotation_price : ramene un prix IBKR dans
    l'unite de docs/indices.json, la seule unite dans laquelle les
    regles de sortie comparent quoi que ce soit."""
    if is_pence_quoted(ticker):
        return price_quotation / PENCE_PER_POUND
    return price_quotation


def compute_quantity(
    ticker: str, index_currency: str, unit_price_indices: float,
    fx_rate: float, budget_eur: float = BUDGET_EUR,
) -> dict:
    """Nombre entier d'actions achetables avec `budget_eur` euros.

    `fx_rate` est le taux EUR -> devise de l'indice (ex. 0.86 pour
    EUR/GBP), tel que renvoye par gateway.exchange_rate(). Le reliquat
    reste en cash (spec 3.3). Si le prix unitaire depasse le budget
    converti, la quantite vaut 0 et le motif est renseigne : AUCUN ordre
    ne doit etre passe dans ce cas, et surtout pas "1 action au moins".
    """
    devise_cotation = quotation_currency(index_currency, ticker)
    plan = {
        "ticker": ticker,
        "quantite": 0,
        "devise_cotation": devise_cotation,
        "devise_compte": index_currency,
        "taux_de_change": fx_rate if _is_positive_number(fx_rate) else 0.0,
        "budget_converti": 0.0,
        "prix_unitaire_cotation": 0.0,
        "cout_estime_devise_compte": 0.0,
        "motif": None,
    }

    if not (_is_positive_number(unit_price_indices)
            and _is_positive_number(fx_rate)
            and _is_positive_number(budget_eur)):
        plan["motif"] = "prix_ou_taux_invalide"
        return plan

    # Les deux grandeurs sont ramenees a la MEME unite (devise de
    # cotation) avant la moindre division — c'est tout le garde-fou.
    budget_compte = budget_eur * fx_rate
    budget_cotation = to_quotation_price(budget_compte, ticker)
    prix_cotation = to_quotation_price(unit_price_indices, ticker)

    plan["budget_converti"] = budget_cotation
    plan["prix_unitaire_cotation"] = prix_cotation

    quantite = math.floor(budget_cotation / prix_cotation)
    if quantite < 1:
        plan["motif"] = "signal_ignore_prix_unitaire_superieur_au_budget"
        return plan

    plan["quantite"] = int(quantite)
    plan["cout_estime_devise_compte"] = quantite * unit_price_indices
    return plan
```

- [ ] **Step 4 : Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/ibkr_bot/test_sizing.py -v`
Expected: PASS (24 tests, paramétrages compris)

- [ ] **Step 5 : Relire à la main les quatre calculs pence/livre**

Vérifier au crayon, sans exécuter le code :
- `III.L` : 500 × 0.86 = 430 GBP → 43 000 GBp ; 2.45 GBP → 245 GBp ; 43 000 / 245 = 175.51 → **175**.
- `AZN.L` : 43 000 GBp ; 30 GBP → 3 000 GBp ; 43 000 / 3 000 = 14.33 → **14**.
- `XPENSIVE.L` : 43 000 GBp ; 500 GBP → 50 000 GBp ; 43 000 / 50 000 = 0.86 → **0** + motif.
- `LLOY.L` : 43 000 GBp ; 0.9412 GBP → 94.12 GBp ; 43 000 / 94.12 = 456.86 → **456**.

Si l'un de ces quatre résultats ne tombe pas juste à la main, ne pas committer : le bug est dans l'implémentation, pas dans le test.

- [ ] **Step 6 : Commit**

```bash
git add ibkr_bot/sizing.py tests/ibkr_bot/test_sizing.py
git commit -m "feat(ibkr_bot): sizing 500 EUR, arrondi entier et garde-fou pence/livre LSE

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4 : `ibkr_bot/gateway.py` — client HTTP du Client Portal Web API Gateway

> **REVUE LA PLUS STRICTE POSSIBLE.** Un mauvais endpoint ou un mauvais nom de champ dans le corps d'un ordre envoie une instruction fausse à un compte-titres réel. Vérifier chaque chemin contre la liste d'endpoints vérifiés ci-dessous avant d'approuver.

**Files:**
- Create: `ibkr_bot/gateway.py`
- Test: `tests/ibkr_bot/test_gateway.py`

**Interfaces:**
- Consumes: rien des tâches précédentes.
- Produces:
  - `DEFAULT_GATEWAY_URL: str = "https://127.0.0.1:5000"`, `API_PREFIX: str = "/v1/api"`, `TIMEOUT: int = 15`, `POSITIONS_PAGE_SIZE: int = 100`
  - `build_url(base_url: str, path: str) -> str`
  - `auth_status(base_url: str) -> dict`
  - `is_authenticated(base_url: str) -> bool` (ne lève jamais)
  - `tickle(base_url: str) -> dict`
  - `reauthenticate(base_url: str) -> dict`
  - `brokerage_accounts(base_url: str) -> dict`
  - `search_contract(base_url: str, symbol: str) -> list[dict]`
  - `contract_info(base_url: str, conid: int | str) -> dict`
  - `exchange_rate(base_url: str, source: str, target: str) -> float`
  - `ledger(base_url: str, account_id: str) -> dict`
  - `cash_by_currency(base_url: str, account_id: str) -> dict[str, float]`
  - `positions(base_url: str, account_id: str) -> list[dict]`
  - `place_market_order(base_url: str, account_id: str, conid: int, side: str, quantity: int) -> list[dict]` — **jamais appelée hors tests dans ce Plan A**
  - `confirm_reply(base_url: str, reply_id: str, confirmed: bool = True) -> list[dict]` — **jamais appelée hors tests dans ce Plan A**
  - `order_status(base_url: str, order_id: str) -> dict`

**Endpoints réels, vérifiés le 2026-09-14** (documentation publique IBKR Client Portal Web API + client open-source `Voyz/ibind`, qui les exerce en production). Base : `{base_url}/v1/api`.

| Fonction | Méthode | Chemin | Notes |
|---|---|---|---|
| `auth_status` | POST | `/iserver/auth/status` | réponse `{"authenticated": bool, "connected": bool, "competing": bool}` |
| `tickle` | POST | `/tickle` | maintient la session vivante |
| `reauthenticate` | POST | `/iserver/reauthenticate` | relance la session courtage |
| `brokerage_accounts` | GET | `/iserver/accounts` | **doit être appelé au moins une fois par session avant tout ordre** |
| `search_contract` | GET | `/iserver/secdef/search?symbol=…&secType=STK` | réponse : liste de `{"conid", "companyName", "symbol", "description", "sections": [{"secType": …}]}` — `description` porte la bourse principale |
| `contract_info` | GET | `/iserver/secdef/info?conid=…&secType=STK` | réponse porte `currency`, `listingExchange`, `exchange`, `symbol` |
| `exchange_rate` | GET | `/iserver/exchangerate?source=EUR&target=GBP` | réponse `{"rate": 0.8612}` |
| `ledger` | GET | `/portfolio/{accountId}/ledger` | réponse indexée par devise : `{"EUR": {"cashbalance": …, "settledcash": …, "currency": "EUR"}, "BASE": {…}}` |
| `positions` | GET | `/portfolio/{accountId}/positions/{pageId}` | 100 positions par page ; champs `conid`, `position`, `currency`, `mktPrice`, `avgPrice`, `contractDesc`, `assetClass` |
| `place_market_order` | POST | `/iserver/account/{accountId}/orders` | corps `{"orders": [{"conid", "orderType": "MKT", "side", "quantity", "tif": "DAY", "acctId"}]}` |
| `confirm_reply` | POST | `/iserver/reply/{replyId}` | corps `{"confirmed": true}` — répond aux questions de confirmation renvoyées par l'ordre |
| `order_status` | GET | `/iserver/account/order/status/{orderId}` | état d'un ordre soumis |

Le Gateway présente un certificat auto-signé sur localhost : tous les appels passent `verify=False`, et le module désactive l'avertissement `urllib3` correspondant (la connexion ne quitte jamais la machine — spec 4.4).

- [ ] **Step 1 : Écrire les tests qui échouent (partie 1 — session, comptes, contrats, change)**

Créer `tests/ibkr_bot/test_gateway.py` :

```python
import pytest

import ibkr_bot.gateway as gateway

BASE = "https://127.0.0.1:5000"


class _FakeIbkrResponse:
    """Faux objet reponse HTTP — meme convention que
    tests/gold_bot/test_broker.py."""

    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise gateway.requests.exceptions.HTTPError(
                f"{self.status_code} Client Error", response=self
            )

    def json(self):
        return self._json_data


def test_build_url_inserts_the_v1_api_prefix():
    assert gateway.build_url(BASE, "/iserver/auth/status") == (
        "https://127.0.0.1:5000/v1/api/iserver/auth/status"
    )
    assert gateway.build_url(BASE + "/", "iserver/auth/status") == (
        "https://127.0.0.1:5000/v1/api/iserver/auth/status"
    )


def test_auth_status_posts_to_the_right_url_and_disables_cert_check(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None, verify=None):
        captured["url"] = url
        captured["timeout"] = timeout
        captured["verify"] = verify
        return _FakeIbkrResponse({"authenticated": True, "connected": True, "competing": False})

    monkeypatch.setattr(gateway.requests, "post", fake_post)
    result = gateway.auth_status(BASE)

    assert result["authenticated"] is True
    assert captured["url"] == "https://127.0.0.1:5000/v1/api/iserver/auth/status"
    assert captured["timeout"] == gateway.TIMEOUT
    assert captured["verify"] is False


def test_is_authenticated_true_only_when_authenticated_and_connected(monkeypatch):
    monkeypatch.setattr(
        gateway.requests, "post",
        lambda *a, **k: _FakeIbkrResponse({"authenticated": True, "connected": True}))
    assert gateway.is_authenticated(BASE) is True

    monkeypatch.setattr(
        gateway.requests, "post",
        lambda *a, **k: _FakeIbkrResponse({"authenticated": True, "connected": False}))
    assert gateway.is_authenticated(BASE) is False


def test_is_authenticated_returns_false_instead_of_raising(monkeypatch):
    """Le preflight du batch (Plan B) appelle cette fonction : un Gateway
    eteint doit donner False, pas une exception."""
    def boom(*args, **kwargs):
        raise gateway.requests.exceptions.ConnectionError("gateway eteint")

    monkeypatch.setattr(gateway.requests, "post", boom)
    assert gateway.is_authenticated(BASE) is False


def test_tickle_and_reauthenticate_use_their_documented_paths(monkeypatch):
    captured = []

    def fake_post(url, json=None, timeout=None, verify=None):
        captured.append(url)
        return _FakeIbkrResponse({"session": "abc"})

    monkeypatch.setattr(gateway.requests, "post", fake_post)
    gateway.tickle(BASE)
    gateway.reauthenticate(BASE)

    assert captured == [
        "https://127.0.0.1:5000/v1/api/tickle",
        "https://127.0.0.1:5000/v1/api/iserver/reauthenticate",
    ]


def test_brokerage_accounts_uses_iserver_accounts(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None, verify=None):
        captured["url"] = url
        return _FakeIbkrResponse({"accounts": ["U1234567"], "selectedAccount": "U1234567"})

    monkeypatch.setattr(gateway.requests, "get", fake_get)
    result = gateway.brokerage_accounts(BASE)

    assert result["accounts"] == ["U1234567"]
    assert captured["url"] == "https://127.0.0.1:5000/v1/api/iserver/accounts"


def test_search_contract_sends_symbol_and_stk_sectype(monkeypatch):
    captured = {}
    raw = [{
        "conid": "265598", "companyHeader": "APPLE INC - NASDAQ",
        "companyName": "APPLE INC", "symbol": "AAPL", "description": "NASDAQ",
        "sections": [{"secType": "STK"}, {"secType": "OPT"}],
    }]

    def fake_get(url, params=None, timeout=None, verify=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeIbkrResponse(raw)

    monkeypatch.setattr(gateway.requests, "get", fake_get)
    result = gateway.search_contract(BASE, "AAPL")

    assert result == raw
    assert captured["url"] == "https://127.0.0.1:5000/v1/api/iserver/secdef/search"
    assert captured["params"] == {"symbol": "AAPL", "secType": "STK"}


def test_search_contract_returns_empty_list_when_api_returns_a_dict(monkeypatch):
    """Le Gateway renvoie parfois un objet d'erreur la ou la doc annonce
    une liste — ne pas laisser cette forme remonter aux appelants."""
    monkeypatch.setattr(
        gateway.requests, "get",
        lambda *a, **k: _FakeIbkrResponse({"error": "no contracts"}))
    assert gateway.search_contract(BASE, "INCONNU") == []


def test_contract_info_sends_conid_and_stk_sectype(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None, verify=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeIbkrResponse({
            "conid": 265598, "symbol": "AAPL", "currency": "USD",
            "exchange": "NASDAQ", "listingExchange": "NASDAQ",
        })

    monkeypatch.setattr(gateway.requests, "get", fake_get)
    result = gateway.contract_info(BASE, 265598)

    assert result["currency"] == "USD"
    assert captured["url"] == "https://127.0.0.1:5000/v1/api/iserver/secdef/info"
    assert captured["params"] == {"conid": "265598", "secType": "STK"}


def test_exchange_rate_reads_the_rate_field(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None, verify=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeIbkrResponse({"rate": 0.8612})

    monkeypatch.setattr(gateway.requests, "get", fake_get)
    result = gateway.exchange_rate(BASE, "EUR", "GBP")

    assert result == pytest.approx(0.8612)
    assert captured["url"] == "https://127.0.0.1:5000/v1/api/iserver/exchangerate"
    assert captured["params"] == {"source": "EUR", "target": "GBP"}


def test_exchange_rate_short_circuits_identical_currencies(monkeypatch):
    """EUR -> EUR ne doit declencher aucun appel reseau : le Gateway
    refuse la paire degeneree, et le taux est trivialement 1."""
    def boom(*args, **kwargs):
        raise AssertionError("aucun appel reseau attendu pour EUR->EUR")

    monkeypatch.setattr(gateway.requests, "get", boom)
    assert gateway.exchange_rate(BASE, "EUR", "EUR") == 1.0


def test_exchange_rate_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(
        gateway.requests, "get",
        lambda *a, **k: _FakeIbkrResponse({}, status_code=503))
    with pytest.raises(gateway.requests.exceptions.HTTPError):
        gateway.exchange_rate(BASE, "EUR", "CHF")
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/ibkr_bot/test_gateway.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'ibkr_bot.gateway'`

- [ ] **Step 3 : Écrire l'implémentation (partie 1)**

Créer `ibkr_bot/gateway.py` :

```python
# ibkr_bot/gateway.py
# Client HTTP du IBKR Client Portal Web API Gateway (paquet Java lance en
# service systemd, a l'ecoute sur 127.0.0.1 uniquement — voir spec 4.4).
# SEUL MODULE DU PAQUET QUI PARLE A IBKR.
#
# Endpoints verifies le 2026-09-14 contre la documentation publique du
# Client Portal Web API et contre le client open-source Voyz/ibind, qui
# les exerce en production. Ne pas modifier les chemins, les methodes ni
# les noms de champs sans re-verification contre la doc reelle.
#
# PLAN A : place_market_order() et confirm_reply() sont ecrites et
# testees (mockees), mais AUCUN autre module de ibkr_bot/ ne les appelle
# — un test structurel le verifie. Le chemin reel arrive au Plan B.
import urllib3

import requests

DEFAULT_GATEWAY_URL = "https://127.0.0.1:5000"
API_PREFIX = "/v1/api"
TIMEOUT = 15
POSITIONS_PAGE_SIZE = 100

# Le Gateway presente un certificat auto-signe. verify=False est sur ici
# et seulement ici : la connexion ne quitte jamais la machine.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def build_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{API_PREFIX}/{path.lstrip('/')}"


def _get(base_url: str, path: str, params: dict | None = None):
    resp = requests.get(
        build_url(base_url, path), params=params, timeout=TIMEOUT, verify=False,
    )
    resp.raise_for_status()
    return resp.json()


def _post(base_url: str, path: str, payload: dict | None = None):
    resp = requests.post(
        build_url(base_url, path), json=payload, timeout=TIMEOUT, verify=False,
    )
    resp.raise_for_status()
    return resp.json()


# --- session ---------------------------------------------------------

def auth_status(base_url: str = DEFAULT_GATEWAY_URL) -> dict:
    """POST /iserver/auth/status -> {"authenticated", "connected",
    "competing"}."""
    return _post(base_url, "/iserver/auth/status")


def is_authenticated(base_url: str = DEFAULT_GATEWAY_URL) -> bool:
    """Preflight du batch : True seulement si la session est a la fois
    authentifiee et connectee. Ne leve jamais — un Gateway eteint ou une
    reponse inattendue donnent False, que l'appelant traduira en
    'gateway_indisponible'."""
    try:
        status = auth_status(base_url)
    except Exception:
        return False
    if not isinstance(status, dict):
        return False
    return bool(status.get("authenticated")) and bool(status.get("connected"))


def tickle(base_url: str = DEFAULT_GATEWAY_URL) -> dict:
    """POST /tickle — maintient la session vivante."""
    return _post(base_url, "/tickle")


def reauthenticate(base_url: str = DEFAULT_GATEWAY_URL) -> dict:
    """POST /iserver/reauthenticate — relance la session courtage. Ne
    remplace pas une validation 2FA manuelle quand elle est exigee."""
    return _post(base_url, "/iserver/reauthenticate")


def brokerage_accounts(base_url: str = DEFAULT_GATEWAY_URL) -> dict:
    """GET /iserver/accounts — doit avoir ete appele au moins une fois
    dans la session avant tout passage d'ordre."""
    return _get(base_url, "/iserver/accounts")


# --- contrats et change ----------------------------------------------

def search_contract(base_url: str, symbol: str) -> list[dict]:
    """GET /iserver/secdef/search — recherche d'actions par symbole.
    Renvoie toujours une liste : le Gateway substitue parfois un objet
    d'erreur a la liste attendue."""
    data = _get(base_url, "/iserver/secdef/search",
                {"symbol": symbol, "secType": "STK"})
    return data if isinstance(data, list) else []


def contract_info(base_url: str, conid) -> dict:
    """GET /iserver/secdef/info — details du contrat (porte notamment
    currency et listingExchange, les deux champs qui permettent de
    refuser un contrat ambigu plutot que de le deviner)."""
    data = _get(base_url, "/iserver/secdef/info",
                {"conid": str(conid), "secType": "STK"})
    if isinstance(data, list):
        return data[0] if data else {}
    return data if isinstance(data, dict) else {}


def exchange_rate(base_url: str, source: str, target: str) -> float:
    """GET /iserver/exchangerate -> {"rate": ...}. Taux `source` ->
    `target` (EUR -> GBP renvoie des GBP par EUR)."""
    if source == target:
        return 1.0
    return float(_get(base_url, "/iserver/exchangerate",
                      {"source": source, "target": target})["rate"])
```

- [ ] **Step 4 : Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/ibkr_bot/test_gateway.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5 : Commit intermédiaire**

```bash
git add ibkr_bot/gateway.py tests/ibkr_bot/test_gateway.py
git commit -m "feat(ibkr_bot): client Gateway IBKR — session, contrats, taux de change

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 6 : Écrire les tests qui échouent (partie 2 — portefeuille, ordres, garde structurel)**

Ajouter à la fin de `tests/ibkr_bot/test_gateway.py` :

```python
# --- portefeuille ----------------------------------------------------

def test_ledger_uses_the_portfolio_ledger_path(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None, verify=None):
        captured["url"] = url
        return _FakeIbkrResponse({
            "EUR": {"currency": "EUR", "cashbalance": 3120.5, "settledcash": 3120.5},
            "BASE": {"currency": "BASE", "cashbalance": 3400.0},
        })

    monkeypatch.setattr(gateway.requests, "get", fake_get)
    result = gateway.ledger(BASE, "U1234567")

    assert result["EUR"]["cashbalance"] == 3120.5
    assert captured["url"] == "https://127.0.0.1:5000/v1/api/portfolio/U1234567/ledger"


def test_cash_by_currency_keeps_real_currencies_and_drops_base(monkeypatch):
    monkeypatch.setattr(gateway.requests, "get", lambda *a, **k: _FakeIbkrResponse({
        "EUR": {"currency": "EUR", "cashbalance": 3120.5},
        "GBP": {"currency": "GBP", "cashbalance": 430.0},
        "USD": {"currency": "USD", "cashbalance": 0.0},
        "BASE": {"currency": "BASE", "cashbalance": 3900.0},
    }))
    result = gateway.cash_by_currency(BASE, "U1234567")

    assert result == {"EUR": 3120.5, "GBP": 430.0, "USD": 0.0}
    assert "BASE" not in result


def test_cash_by_currency_ignores_entries_without_a_cash_balance(monkeypatch):
    monkeypatch.setattr(gateway.requests, "get", lambda *a, **k: _FakeIbkrResponse({
        "EUR": {"currency": "EUR", "cashbalance": 100.0},
        "CHF": {"currency": "CHF"},
        "JPY": "pas un dict",
    }))
    assert gateway.cash_by_currency(BASE, "U1234567") == {"EUR": 100.0}


def test_positions_fetches_the_first_page(monkeypatch):
    captured = []
    raw = [
        {"conid": 265598, "contractDesc": "AAPL", "position": 10.0,
         "currency": "USD", "mktPrice": 190.0, "assetClass": "STK"},
        {"conid": 4901, "contractDesc": "LVMH", "position": 1.0,
         "currency": "EUR", "mktPrice": 415.0, "assetClass": "STK"},
    ]

    def fake_get(url, params=None, timeout=None, verify=None):
        captured.append(url)
        return _FakeIbkrResponse(raw)

    monkeypatch.setattr(gateway.requests, "get", fake_get)
    result = gateway.positions(BASE, "U1234567")

    assert result == raw
    assert captured == ["https://127.0.0.1:5000/v1/api/portfolio/U1234567/positions/0"]


def test_positions_follows_pagination_until_a_short_page(monkeypatch):
    page_0 = [{"conid": i, "position": 1.0, "currency": "EUR"} for i in range(100)]
    page_1 = [{"conid": 1000, "position": 2.0, "currency": "EUR"}]
    pages = {"0": page_0, "1": page_1}
    captured = []

    def fake_get(url, params=None, timeout=None, verify=None):
        captured.append(url)
        return _FakeIbkrResponse(pages[url.rsplit("/", 1)[-1]])

    monkeypatch.setattr(gateway.requests, "get", fake_get)
    result = gateway.positions(BASE, "U1234567")

    assert len(result) == 101
    assert result[-1]["conid"] == 1000
    assert captured == [
        "https://127.0.0.1:5000/v1/api/portfolio/U1234567/positions/0",
        "https://127.0.0.1:5000/v1/api/portfolio/U1234567/positions/1",
    ]


def test_positions_returns_empty_list_when_api_returns_a_dict(monkeypatch):
    monkeypatch.setattr(
        gateway.requests, "get",
        lambda *a, **k: _FakeIbkrResponse({"error": "not ready"}))
    assert gateway.positions(BASE, "U1234567") == []


# --- ordres (mockes, jamais appeles hors tests dans ce Plan A) --------

def test_place_market_order_sends_the_documented_body(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None, verify=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeIbkrResponse([{"order_id": "1234", "order_status": "Submitted"}])

    monkeypatch.setattr(gateway.requests, "post", fake_post)
    result = gateway.place_market_order(BASE, "U1234567", 4901, "BUY", 6)

    assert result == [{"order_id": "1234", "order_status": "Submitted"}]
    assert captured["url"] == "https://127.0.0.1:5000/v1/api/iserver/account/U1234567/orders"
    assert captured["json"] == {
        "orders": [{
            "conid": 4901,
            "orderType": "MKT",
            "side": "BUY",
            "quantity": 6,
            "tif": "DAY",
            "acctId": "U1234567",
        }]
    }


def test_place_market_order_accepts_sell_side(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None, verify=None):
        captured["json"] = json
        return _FakeIbkrResponse([{"order_id": "1235"}])

    monkeypatch.setattr(gateway.requests, "post", fake_post)
    gateway.place_market_order(BASE, "U1234567", 4901, "SELL", 6)

    assert captured["json"]["orders"][0]["side"] == "SELL"
    assert captured["json"]["orders"][0]["orderType"] == "MKT"


def test_place_market_order_rejects_an_unknown_side():
    with pytest.raises(ValueError, match="BUY.*SELL"):
        gateway.place_market_order(BASE, "U1234567", 4901, "achat", 6)


def test_place_market_order_rejects_a_non_positive_quantity():
    with pytest.raises(ValueError, match="quantite"):
        gateway.place_market_order(BASE, "U1234567", 4901, "BUY", 0)


def test_place_market_order_propagates_http_errors(monkeypatch):
    monkeypatch.setattr(
        gateway.requests, "post",
        lambda *a, **k: _FakeIbkrResponse({}, status_code=400))
    with pytest.raises(gateway.requests.exceptions.HTTPError):
        gateway.place_market_order(BASE, "U1234567", 4901, "BUY", 6)


def test_confirm_reply_posts_confirmed_true(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None, verify=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeIbkrResponse([{"order_id": "1234", "order_status": "Submitted"}])

    monkeypatch.setattr(gateway.requests, "post", fake_post)
    gateway.confirm_reply(BASE, "e1f2a3b4-0000")

    assert captured["url"] == "https://127.0.0.1:5000/v1/api/iserver/reply/e1f2a3b4-0000"
    assert captured["json"] == {"confirmed": True}


def test_order_status_uses_the_documented_path(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None, verify=None):
        captured["url"] = url
        return _FakeIbkrResponse({"order_status": "Filled", "avgPrice": "415.20"})

    monkeypatch.setattr(gateway.requests, "get", fake_get)
    result = gateway.order_status(BASE, "1234")

    assert result["order_status"] == "Filled"
    assert captured["url"] == (
        "https://127.0.0.1:5000/v1/api/iserver/account/order/status/1234"
    )


# --- garde structurel du Plan A --------------------------------------

def test_no_other_plan_a_module_references_the_order_routes():
    """Garantie mecanique du Plan A (voir Global Constraints) : seul
    gateway.py connait les routes de passage d'ordre. Aucun autre module
    du paquet ne doit pouvoir en declencher une, meme par erreur."""
    import pathlib

    package_dir = pathlib.Path(gateway.__file__).parent
    interdits = ("place_market_order", "confirm_reply", "/iserver/account/")
    fautifs = []
    for source in sorted(package_dir.glob("*.py")):
        if source.name == "gateway.py":
            continue
        texte = source.read_text(encoding="utf-8")
        for interdit in interdits:
            if interdit in texte:
                fautifs.append(f"{source.name} mentionne {interdit!r}")

    assert fautifs == [], (
        "Le Plan A interdit tout chemin de passage d'ordre hors gateway.py : "
        + "; ".join(fautifs)
    )
```

- [ ] **Step 7 : Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/ibkr_bot/test_gateway.py -v`
Expected: FAIL avec `AttributeError: module 'ibkr_bot.gateway' has no attribute 'ledger'`

- [ ] **Step 8 : Écrire l'implémentation (partie 2)**

Ajouter à la fin de `ibkr_bot/gateway.py` :

```python
# --- portefeuille ----------------------------------------------------

def ledger(base_url: str, account_id: str) -> dict:
    """GET /portfolio/{accountId}/ledger — soldes indexes par devise.
    La cle "BASE" est un agregat dans la devise de base du compte, pas
    une devise reelle."""
    data = _get(base_url, f"/portfolio/{account_id}/ledger")
    return data if isinstance(data, dict) else {}


def cash_by_currency(base_url: str, account_id: str) -> dict[str, float]:
    """Solde cash disponible par devise reelle. Alimente le garde-fou de
    solde (spec 9.9) : ne retenir que les signaux financables, plutot que
    de declencher une serie de rejets bruyants en fin de classement."""
    soldes: dict[str, float] = {}
    for devise, entree in ledger(base_url, account_id).items():
        if devise == "BASE" or not isinstance(entree, dict):
            continue
        solde = entree.get("cashbalance")
        if isinstance(solde, (int, float)) and not isinstance(solde, bool):
            soldes[devise] = float(solde)
    return soldes


def positions(base_url: str, account_id: str) -> list[dict]:
    """GET /portfolio/{accountId}/positions/{pageId} — toutes les
    positions du compte, pagination suivie jusqu'a une page incomplete.
    Renvoie les positions BRUTES du compte : c'est portfolio.reconcile()
    qui distingue celles du bot de celles de l'utilisateur."""
    toutes: list[dict] = []
    page = 0
    while True:
        lot = _get(base_url, f"/portfolio/{account_id}/positions/{page}")
        if not isinstance(lot, list):
            break
        toutes.extend(lot)
        if len(lot) < POSITIONS_PAGE_SIZE:
            break
        page += 1
    return toutes


# --- ordres ----------------------------------------------------------
# ATTENTION : les deux fonctions ci-dessous sont le seul chemin par
# lequel de l'argent reel peut bouger. Dans ce Plan A, elles ne sont
# appelees QUE par les tests (avec requests.post monkeypatche). Le test
# test_no_other_plan_a_module_references_the_order_routes le verifie.

def place_market_order(base_url: str, account_id: str, conid: int,
                       side: str, quantity: int) -> list[dict]:
    """POST /iserver/account/{accountId}/orders — ordre au marche (MKT),
    valable le jour (DAY), passe dans la fenetre 14:30-15:30 UTC ou
    toutes les places du perimetre sont ouvertes (spec 4.8, 4.5).

    La reponse peut etre une confirmation d'ordre OU une question a
    confirmer via confirm_reply() ; l'appelant (Plan B) doit traiter les
    deux formes."""
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side doit valoir 'BUY' ou 'SELL', recu {side!r}")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
        raise ValueError(f"quantite doit etre un entier >= 1, recue {quantity!r}")
    data = _post(base_url, f"/iserver/account/{account_id}/orders", {
        "orders": [{
            "conid": conid,
            "orderType": "MKT",
            "side": side,
            "quantity": quantity,
            "tif": "DAY",
            "acctId": account_id,
        }]
    })
    return data if isinstance(data, list) else [data]


def confirm_reply(base_url: str, reply_id: str, confirmed: bool = True) -> list[dict]:
    """POST /iserver/reply/{replyId} — repond a une question de
    confirmation renvoyee par place_market_order()."""
    data = _post(base_url, f"/iserver/reply/{reply_id}", {"confirmed": confirmed})
    return data if isinstance(data, list) else [data]


def order_status(base_url: str, order_id: str) -> dict:
    """GET /iserver/account/order/status/{orderId} — etat d'un ordre
    soumis (porte notamment order_status et avgPrice une fois execute)."""
    data = _get(base_url, f"/iserver/account/order/status/{order_id}")
    return data if isinstance(data, dict) else {}
```

- [ ] **Step 9 : Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/ibkr_bot/test_gateway.py -v`
Expected: PASS (25 tests)

- [ ] **Step 10 : Vérifier qu'aucun test n'a pu toucher un Gateway réel**

Run: `python -m pytest tests/ibkr_bot/ -v -p no:randomly`
Expected: PASS, et **aucun test ne doit prendre plus de quelques millisecondes**. Un test qui traîne (timeout de 15 s) signale un appel réseau non mocké : le corriger avant de committer.

- [ ] **Step 11 : Commit**

```bash
git add ibkr_bot/gateway.py tests/ibkr_bot/test_gateway.py
git commit -m "feat(ibkr_bot): client Gateway IBKR — portefeuille, soldes et ordres (mockes)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5 : `ibkr_bot/contracts.py` — résolution ticker yfinance → conid IBKR, avec cache

> **REVUE LA PLUS STRICTE POSSIBLE.** Acheter le bon nombre d'actions du mauvais instrument (ADR, cotation secondaire, dérivé) est la façon la plus discrète de perdre de l'argent. La règle est : refuser et journaliser, jamais deviner.

**Files:**
- Create: `ibkr_bot/contracts.py`
- Test: `tests/ibkr_bot/test_contracts.py`

**Interfaces:**
- Consumes: `gateway.search_contract(base_url, symbol) -> list[dict]` et `gateway.contract_info(base_url, conid) -> dict` de la tâche 4 — **injectés sous forme de callables à un argument**, jamais importés directement, pour que ce module reste testable sans réseau et sans mock HTTP.
- Produces:
  - `CONID_CACHE_PATH: str`, `EXPECTED_VENUE: dict[str, dict]`
  - `split_ticker(ticker: str) -> tuple[str, str]` → `(symbole, suffixe)`
  - `expected_venue(ticker: str) -> dict | None` → `{"exchanges": tuple[str, ...], "currencies": tuple[str, ...]}`
  - `load_cache(path: str = CONID_CACHE_PATH) -> dict`
  - `save_cache(cache: dict, path: str = CONID_CACHE_PATH) -> None`
  - `resolve_conid(ticker: str, search_fn, info_fn, cache: dict | None = None, today: str = "") -> dict`
  - **Enregistrement « contrat »** : `{"ticker": str, "conid": int | None, "exchange": str, "currency": str, "motif": str | None, "detail": str}` — `motif` vaut `None` en cas de succès, `"contrat_non_resolu"` sinon, et `detail` explique pourquoi (texte libre destiné au journal).

Correspondance des suffixes (spec 4.7) et codes bourse IBKR (vérifiés le 2026-09-14 sur les listes publiques de codes de place IBKR) :

| Suffixe yfinance | Bourse | Code IBKR | Devise attendue | Indices |
|---|---|---|---|---|
| `.PA` | Euronext Paris | `SBF` | EUR | CAC40 |
| `.DE` | Xetra | `IBIS`, `IBIS2` | EUR | DAX |
| `.MI` | Borsa Italiana | `BVME` | EUR | FTSEMIB |
| `.MC` | BME Madrid | `BM` | EUR | IBEX35 |
| `.L` | LSE | `LSE` | GBP **ou** GBp | FTSE |
| `.SW` | SIX Zurich | `EBS` | CHF | SMI |
| *(aucun)* | NYSE / Nasdaq / consorts | `NASDAQ`, `NYSE`, `ARCA`, `AMEX`, `BATS` | USD | NASDAQ, DOW |

`.L` accepte `GBP` **et** `GBp` comme devise valide : IBKR annonce la devise du contrat LSE tantôt sous l'une, tantôt sous l'autre forme, tout en cotant en pence. La conversion d'unité, elle, ne dépend jamais de ce champ — elle dépend du suffixe `.L` (tâche 3), qui est déterministe.

Algorithme de résolution, en trois filtres successifs :
1. **Filtre symbole + bourse** sur la réponse de `search_fn` : ne garder que les candidats dont `symbol` égale exactement le symbole cherché, qui portent une section `secType == "STK"`, et dont `description` (la bourse principale) figure dans les codes attendus.
2. **Filtre devise** via `info_fn` sur chaque candidat survivant : ne garder que ceux dont `currency` figure dans les devises attendues.
3. **Unicité** : exactement un survivant → succès. Zéro → `contrat_non_resolu` (« aucun candidat »). Deux ou plus → `contrat_non_resolu` (« ambigu »). **Jamais de choix par défaut, jamais « le premier de la liste ».**

Les succès sont mis en cache ; **les échecs ne le sont jamais** (une résolution peut réussir demain après une correction côté IBKR).

- [ ] **Step 1 : Écrire les tests qui échouent**

Créer `tests/ibkr_bot/test_contracts.py` :

```python
import json

import ibkr_bot.contracts as contracts


def _search_fn(reponses):
    """Faux gateway.search_contract : dict symbole -> liste de candidats.
    Compte ses appels pour verifier l'efficacite du cache."""
    appels = []

    def search(symbol):
        appels.append(symbol)
        return reponses.get(symbol, [])

    search.appels = appels
    return search


def _info_fn(reponses):
    """Faux gateway.contract_info : dict conid (str) -> details."""
    appels = []

    def info(conid):
        appels.append(str(conid))
        return reponses.get(str(conid), {})

    info.appels = appels
    return info


def _candidat(conid, symbol, description, sec_types=("STK",)):
    return {
        "conid": str(conid),
        "companyName": f"{symbol} SA",
        "symbol": symbol,
        "description": description,
        "sections": [{"secType": t} for t in sec_types],
    }


# --- decoupage du ticker ---------------------------------------------

def test_split_ticker_separates_known_suffixes():
    assert contracts.split_ticker("MC.PA") == ("MC", ".PA")
    assert contracts.split_ticker("SAP.DE") == ("SAP", ".DE")
    assert contracts.split_ticker("ABBN.SW") == ("ABBN", ".SW")
    assert contracts.split_ticker("III.L") == ("III", ".L")
    assert contracts.split_ticker("A2A.MI") == ("A2A", ".MI")
    assert contracts.split_ticker("ANA.MC") == ("ANA", ".MC")


def test_split_ticker_treats_us_tickers_as_suffixless():
    assert contracts.split_ticker("ADBE") == ("ADBE", "")


def test_split_ticker_does_not_strip_an_unknown_dotted_suffix():
    """BRK.B est un ticker americain a point, pas un suffixe de place."""
    assert contracts.split_ticker("BRK.B") == ("BRK.B", "")


def test_expected_venue_covers_every_suffix_of_the_v1_perimeter():
    for suffixe in (".PA", ".DE", ".MI", ".MC", ".L", ".SW", ""):
        venue = contracts.expected_venue("X" + suffixe)
        assert venue is not None, suffixe
        assert venue["exchanges"], suffixe
        assert venue["currencies"], suffixe


def test_expected_venue_maps_london_to_lse_and_accepts_both_gbp_spellings():
    venue = contracts.expected_venue("III.L")
    assert venue["exchanges"] == ("LSE",)
    assert set(venue["currencies"]) == {"GBP", "GBp"}


def test_expected_venue_is_none_for_an_out_of_scope_suffix():
    assert contracts.expected_venue("7203.T") is None
    assert contracts.expected_venue("0005.HK") is None


# --- resolution ------------------------------------------------------

def test_resolve_conid_accepts_a_single_matching_contract():
    search = _search_fn({"MC": [_candidat(4901, "MC", "SBF")]})
    info = _info_fn({"4901": {"conid": 4901, "symbol": "MC", "currency": "EUR",
                              "listingExchange": "SBF"}})
    cache = {}

    result = contracts.resolve_conid("MC.PA", search, info, cache, today="2026-09-14")

    assert result["conid"] == 4901
    assert result["exchange"] == "SBF"
    assert result["currency"] == "EUR"
    assert result["motif"] is None
    assert result["ticker"] == "MC.PA"


def test_resolve_conid_accepts_a_london_contract_quoted_in_gbp():
    search = _search_fn({"III": [_candidat(8675, "III", "LSE")]})
    info = _info_fn({"8675": {"conid": 8675, "symbol": "III", "currency": "GBP",
                              "listingExchange": "LSE"}})

    result = contracts.resolve_conid("III.L", search, info, {}, today="2026-09-14")

    assert result["conid"] == 8675
    assert result["currency"] == "GBP"
    assert result["motif"] is None


def test_resolve_conid_accepts_a_london_contract_reported_as_gbp_pence():
    search = _search_fn({"ABDN": [_candidat(9001, "ABDN", "LSE")]})
    info = _info_fn({"9001": {"conid": 9001, "symbol": "ABDN", "currency": "GBp",
                              "listingExchange": "LSE"}})

    result = contracts.resolve_conid("ABDN.L", search, info, {}, today="2026-09-14")

    assert result["conid"] == 9001
    assert result["motif"] is None


def test_resolve_conid_accepts_a_us_contract_on_any_us_venue():
    search = _search_fn({"ADBE": [_candidat(265768, "ADBE", "NASDAQ")]})
    info = _info_fn({"265768": {"conid": 265768, "symbol": "ADBE",
                                "currency": "USD", "listingExchange": "NASDAQ"}})

    result = contracts.resolve_conid("ADBE", search, info, {}, today="2026-09-14")

    assert result["conid"] == 265768
    assert result["exchange"] == "NASDAQ"


def test_resolve_conid_refuses_when_no_candidate_is_on_the_expected_exchange():
    """Cotation secondaire / ADR : le symbole existe, mais pas sur la
    bourse attendue -> on refuse plutot que d'acheter un autre
    instrument (spec 4.7 point 2)."""
    search = _search_fn({"MC": [_candidat(999, "MC", "SWB"),
                                _candidat(998, "MC", "FWB")]})
    info = _info_fn({})

    result = contracts.resolve_conid("MC.PA", search, info, {}, today="2026-09-14")

    assert result["conid"] is None
    assert result["motif"] == "contrat_non_resolu"
    assert "aucun candidat" in result["detail"]


def test_resolve_conid_refuses_when_the_currency_does_not_match():
    search = _search_fn({"MC": [_candidat(4901, "MC", "SBF")]})
    info = _info_fn({"4901": {"conid": 4901, "symbol": "MC", "currency": "USD",
                              "listingExchange": "SBF"}})

    result = contracts.resolve_conid("MC.PA", search, info, {}, today="2026-09-14")

    assert result["conid"] is None
    assert result["motif"] == "contrat_non_resolu"
    assert "aucun candidat" in result["detail"]


def test_resolve_conid_refuses_an_ambiguous_result_instead_of_guessing():
    """Deux contrats survivent aux deux filtres : on refuse. Prendre le
    premier de la liste serait un achat devine."""
    search = _search_fn({"MC": [_candidat(4901, "MC", "SBF"),
                                _candidat(4902, "MC", "SBF")]})
    info = _info_fn({
        "4901": {"conid": 4901, "symbol": "MC", "currency": "EUR", "listingExchange": "SBF"},
        "4902": {"conid": 4902, "symbol": "MC", "currency": "EUR", "listingExchange": "SBF"},
    })

    result = contracts.resolve_conid("MC.PA", search, info, {}, today="2026-09-14")

    assert result["conid"] is None
    assert result["motif"] == "contrat_non_resolu"
    assert "ambigu" in result["detail"]
    assert "4901" in result["detail"] and "4902" in result["detail"]


def test_resolve_conid_ignores_candidates_whose_symbol_differs():
    """La recherche IBKR renvoie aussi des symboles voisins."""
    search = _search_fn({"MC": [_candidat(1, "MCD", "SBF"),
                                _candidat(4901, "MC", "SBF")]})
    info = _info_fn({"4901": {"conid": 4901, "symbol": "MC", "currency": "EUR",
                              "listingExchange": "SBF"}})

    result = contracts.resolve_conid("MC.PA", search, info, {}, today="2026-09-14")

    assert result["conid"] == 4901
    assert "1" not in info.appels


def test_resolve_conid_ignores_candidates_without_a_stock_section():
    search = _search_fn({"MC": [_candidat(4901, "MC", "SBF", sec_types=("OPT", "FOP"))]})
    info = _info_fn({})

    result = contracts.resolve_conid("MC.PA", search, info, {}, today="2026-09-14")

    assert result["conid"] is None
    assert result["motif"] == "contrat_non_resolu"


def test_resolve_conid_refuses_an_out_of_scope_suffix_without_calling_ibkr():
    search = _search_fn({})
    info = _info_fn({})

    result = contracts.resolve_conid("7203.T", search, info, {}, today="2026-09-14")

    assert result["conid"] is None
    assert result["motif"] == "contrat_non_resolu"
    assert "suffixe hors perimetre" in result["detail"]
    assert search.appels == []


def test_resolve_conid_survives_a_search_that_raises():
    def search(symbol):
        raise RuntimeError("gateway injoignable")

    result = contracts.resolve_conid("MC.PA", search, _info_fn({}), {}, today="2026-09-14")

    assert result["conid"] is None
    assert result["motif"] == "contrat_non_resolu"
    assert "gateway injoignable" in result["detail"]


# --- cache -----------------------------------------------------------

def test_resolve_conid_stores_a_success_in_the_cache():
    search = _search_fn({"MC": [_candidat(4901, "MC", "SBF")]})
    info = _info_fn({"4901": {"conid": 4901, "symbol": "MC", "currency": "EUR",
                              "listingExchange": "SBF"}})
    cache = {}

    contracts.resolve_conid("MC.PA", search, info, cache, today="2026-09-14")

    assert cache["MC.PA"] == {
        "conid": 4901, "exchange": "SBF", "currency": "EUR", "resolved_on": "2026-09-14",
    }


def test_resolve_conid_reuses_the_cache_without_calling_ibkr():
    search = _search_fn({"MC": [_candidat(4901, "MC", "SBF")]})
    info = _info_fn({"4901": {"conid": 4901, "symbol": "MC", "currency": "EUR"}})
    cache = {"MC.PA": {"conid": 4901, "exchange": "SBF", "currency": "EUR",
                       "resolved_on": "2026-09-01"}}

    result = contracts.resolve_conid("MC.PA", search, info, cache, today="2026-09-14")

    assert result["conid"] == 4901
    assert result["motif"] is None
    assert search.appels == []
    assert info.appels == []


def test_resolve_conid_never_caches_a_failure():
    search = _search_fn({"MC": []})
    info = _info_fn({})
    cache = {}

    contracts.resolve_conid("MC.PA", search, info, cache, today="2026-09-14")

    assert cache == {}


def test_load_cache_degrades_to_empty_dict(tmp_path):
    assert contracts.load_cache(str(tmp_path / "absent.json")) == {}
    corrupted = tmp_path / "conid_cache.json"
    corrupted.write_text("{nope", encoding="utf-8")
    assert contracts.load_cache(str(corrupted)) == {}


def test_save_cache_then_load_cache_round_trips(tmp_path):
    path = str(tmp_path / "nested" / "conid_cache.json")
    cache = {"MC.PA": {"conid": 4901, "exchange": "SBF", "currency": "EUR",
                       "resolved_on": "2026-09-14"}}
    contracts.save_cache(cache, path)

    assert contracts.load_cache(path) == cache
    assert json.loads(open(path, encoding="utf-8").read()) == cache


def test_default_cache_path_points_inside_ibkr_bot_package():
    assert contracts.CONID_CACHE_PATH.replace("\\", "/").endswith(
        "ibkr_bot/conid_cache.json")
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/ibkr_bot/test_contracts.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'ibkr_bot.contracts'`

- [ ] **Step 3 : Écrire l'implémentation minimale**

Créer `ibkr_bot/contracts.py` :

```python
# ibkr_bot/contracts.py
# Resolution d'un ticker yfinance (MC.PA, SAP.DE, III.L, ADBE...) vers le
# conid numerique IBKR, avec cache local des correspondances deja
# resolues.
#
# PRINCIPE NON NEGOCIABLE (spec 4.7 point 2) : une recherche par symbole
# renvoie souvent plusieurs contrats (cotations multiples, ADR, derives).
# On n'accepte un contrat que si la BOURSE et la DEVISE attendues
# correspondent, et qu'il ne reste qu'un seul candidat. Sinon on refuse
# et on journalise `contrat_non_resolu` — jamais "le premier de la
# liste", jamais un achat devine.
#
# Ce module ne fait aucun appel reseau lui-meme : les fonctions
# gateway.search_contract / gateway.contract_info lui sont injectees.
import json
import os

CONID_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "conid_cache.json")

# Codes de place IBKR verifies le 2026-09-14 sur les listes publiques de
# codes d'echange IBKR. Xetra expose IBIS (actions) et IBIS2 (ETF) : les
# deux sont acceptes, le filtre devise tranche derriere. Le LSE annonce
# sa devise tantot "GBP", tantot "GBp", tout en cotant en pence — les
# deux orthographes sont acceptees ici ; la conversion d'unite, elle, ne
# depend jamais de ce champ mais du suffixe .L (voir sizing.py).
EXPECTED_VENUE = {
    ".PA": {"exchanges": ("SBF",), "currencies": ("EUR",)},
    ".DE": {"exchanges": ("IBIS", "IBIS2"), "currencies": ("EUR",)},
    ".MI": {"exchanges": ("BVME",), "currencies": ("EUR",)},
    ".MC": {"exchanges": ("BM",), "currencies": ("EUR",)},
    ".L": {"exchanges": ("LSE",), "currencies": ("GBP", "GBp")},
    ".SW": {"exchanges": ("EBS",), "currencies": ("CHF",)},
    "": {"exchanges": ("NASDAQ", "NYSE", "ARCA", "AMEX", "BATS"),
         "currencies": ("USD",)},
}


def split_ticker(ticker: str) -> tuple[str, str]:
    """(symbole, suffixe) — le suffixe n'est detache que s'il designe
    une place connue. BRK.B reste ("BRK.B", "") : le point y fait partie
    du symbole, pas d'un code de marche."""
    if "." in ticker:
        symbole, _, fin = ticker.rpartition(".")
        suffixe = f".{fin}"
        if suffixe in EXPECTED_VENUE and symbole:
            return symbole, suffixe
    return ticker, ""


def expected_venue(ticker: str) -> dict | None:
    """Bourse(s) et devise(s) attendues pour ce ticker, ou None si son
    suffixe est hors du perimetre v1 (Nikkei .T, Hang Seng .HK...)."""
    _, suffixe = split_ticker(ticker)
    return EXPECTED_VENUE.get(suffixe)


def load_cache(path: str = CONID_CACHE_PATH) -> dict:
    """Correspondances ticker -> conid deja resolues. {} si le fichier
    est absent ou corrompu — jamais d'exception."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_cache(cache: dict, path: str = CONID_CACHE_PATH) -> None:
    """Ecriture atomique, meme motif que state.save_state."""
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _refus(ticker: str, detail: str) -> dict:
    return {"ticker": ticker, "conid": None, "exchange": "", "currency": "",
            "motif": "contrat_non_resolu", "detail": detail}


def _a_une_section_action(candidat: dict) -> bool:
    sections = candidat.get("sections") or []
    return any(isinstance(s, dict) and s.get("secType") == "STK" for s in sections)


def resolve_conid(ticker: str, search_fn, info_fn,
                  cache: dict | None = None, today: str = "") -> dict:
    """Resout `ticker` en contrat IBKR.

    `search_fn(symbole) -> list[dict]` et `info_fn(conid) -> dict` sont
    typiquement gateway.search_contract / gateway.contract_info lies a
    une base_url. `cache` est le dict renvoye par load_cache() ; il est
    enrichi sur place en cas de succes. Les echecs ne sont jamais mis en
    cache : une resolution peut reussir demain.
    """
    cache = cache if cache is not None else {}
    en_cache = cache.get(ticker)
    if isinstance(en_cache, dict) and en_cache.get("conid"):
        return {"ticker": ticker, "conid": en_cache["conid"],
                "exchange": en_cache.get("exchange", ""),
                "currency": en_cache.get("currency", ""),
                "motif": None, "detail": "cache"}

    venue = expected_venue(ticker)
    if venue is None:
        return _refus(ticker, f"suffixe hors perimetre pour {ticker}")

    symbole, _ = split_ticker(ticker)
    try:
        candidats = search_fn(symbole)
    except Exception as exc:
        return _refus(ticker, f"recherche impossible : {exc}")

    # Filtre 1 : meme symbole, une section action, bourse attendue.
    retenus = [
        c for c in candidats
        if isinstance(c, dict)
        and c.get("symbol") == symbole
        and _a_une_section_action(c)
        and c.get("description") in venue["exchanges"]
    ]

    # Filtre 2 : devise attendue, confirmee contrat par contrat.
    confirmes = []
    for candidat in retenus:
        try:
            details = info_fn(candidat["conid"])
        except Exception as exc:
            return _refus(ticker, f"details indisponibles : {exc}")
        if isinstance(details, dict) and details.get("currency") in venue["currencies"]:
            confirmes.append((candidat, details))

    # Filtre 3 : unicite. Zero ou plusieurs -> on refuse.
    if not confirmes:
        return _refus(
            ticker,
            f"aucun candidat sur {'/'.join(venue['exchanges'])} "
            f"en {'/'.join(venue['currencies'])} pour {symbole} "
            f"({len(candidats)} resultat(s) bruts)",
        )
    if len(confirmes) > 1:
        conids = ", ".join(str(c["conid"]) for c, _ in confirmes)
        return _refus(ticker, f"ambigu : {len(confirmes)} contrats retenus ({conids})")

    candidat, details = confirmes[0]
    conid = int(candidat["conid"])
    exchange = candidat.get("description", "")
    currency = details.get("currency", "")
    cache[ticker] = {"conid": conid, "exchange": exchange,
                     "currency": currency, "resolved_on": today}
    return {"ticker": ticker, "conid": conid, "exchange": exchange,
            "currency": currency, "motif": None, "detail": "resolu"}
```

- [ ] **Step 4 : Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/ibkr_bot/test_contracts.py -v`
Expected: PASS (21 tests)

- [ ] **Step 5 : Vérifier que le garde structurel du Plan A tient toujours**

Run: `python -m pytest tests/ibkr_bot/test_gateway.py::test_no_other_plan_a_module_references_the_order_routes -v`
Expected: PASS — `contracts.py` ne doit mentionner aucune route d'ordre.

- [ ] **Step 6 : Commit**

```bash
git add ibkr_bot/contracts.py tests/ibkr_bot/test_contracts.py
git commit -m "feat(ibkr_bot): resolution ticker->conid IBKR, refus si ambigu, cache local

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6 : `ibkr_bot/portfolio.py` (1/2) — plafond de 10, classement, sélection des entrées

**Files:**
- Create: `ibkr_bot/portfolio.py`
- Test: `tests/ibkr_bot/test_portfolio.py`

**Interfaces:**
- Consumes: l'enregistrement « signal » de la tâche 2 (clés `id`, `ticker`, `index`, `currency`, `score`, `current_price`, `target_exit_price`, `entry_date`, `paper_entry_price`) ; l'enregistrement « plan » de la tâche 3 (clés `ticker`, `quantite`, `devise_compte`, `cout_estime_devise_compte`, `motif`) ; le dict `cash_by_currency` de la tâche 4.
- Produces:
  - `MAX_POSITIONS: int = 10`, `POSITIONS_PATH: str`
  - `_is_missing(value) -> bool`
  - `load_positions(path: str = POSITIONS_PATH) -> list[dict]`
  - `save_positions(positions: list[dict], path: str = POSITIONS_PATH) -> None`
  - `rank_signals(signals: list[dict]) -> list[dict]`
  - `free_slots(open_positions: list[dict]) -> int`
  - `select_entries(signals: list[dict], open_positions: list[dict], plans: dict[str, dict], cash_by_currency: dict[str, float]) -> tuple[list[dict], list[dict]]`
  - **Enregistrement « retenu »** : `{"signal": dict, "plan": dict, "rang": int}`
  - **Enregistrement « rejet »** : `{"ticker": str, "rang": int, "score": float, "raison": str}` avec `raison` ∈ `{"deja_en_portefeuille", "plan_indisponible", "signal_ignore_prix_unitaire_superieur_au_budget", "prix_ou_taux_invalide", "signal_ignore_plafond_atteint", "solde_insuffisant"}`
  - **Enregistrement « position du bot »** (`ibkr_bot/positions.json`), produit par le Plan B et consommé par la tâche 7, clés exactes : `id`, `ticker`, `conid`, `index`, `devise_cotation`, `devise_compte`, `quantite`, `prix_execution_cotation`, `prix_execution_reference`, `paper_entry_price`, `entry_date`, `target_exit_price`, `date_limite`.

**Ordre des filtres, qui est lui-même une règle de la spec** (3.3 et 3.5) : ticker déjà détenu → plan absent → quantité nulle (0 action) → **puis seulement** plafond → puis solde. Le cas 0 action doit être écarté **avant** la consommation d'une place, parce que « la place ainsi libérée sous le plafond de 10 positions reste disponible pour le signal suivant du classement » (spec 3.3). Inverser ces deux filtres perdrait un signal finançable au profit d'un signal inachetable.

- [ ] **Step 1 : Écrire les tests qui échouent**

Créer `tests/ibkr_bot/test_portfolio.py` :

```python
import json

import pytest

import ibkr_bot.portfolio as portfolio


def _signal(ticker, score, **overrides):
    signal = {
        "id": f"{ticker}-2026-09-14",
        "ticker": ticker,
        "name": ticker,
        "index": "CAC40",
        "currency": "EUR",
        "entry_date": "2026-09-14",
        "paper_entry_price": 100.0,
        "target_exit_price": 130.0,
        "score": score,
        "current_price": 100.0,
    }
    signal.update(overrides)
    return signal


def _plan(ticker, quantite=5, devise_compte="EUR", cout=500.0, motif=None):
    return {
        "ticker": ticker,
        "quantite": quantite,
        "devise_cotation": devise_compte,
        "devise_compte": devise_compte,
        "taux_de_change": 1.0,
        "budget_converti": 500.0,
        "prix_unitaire_cotation": 100.0,
        "cout_estime_devise_compte": cout,
        "motif": motif,
    }


def _bot_position(ticker, **overrides):
    position = {
        "id": f"{ticker}-2026-06-14",
        "ticker": ticker,
        "conid": 4901,
        "index": "CAC40",
        "devise_cotation": "EUR",
        "devise_compte": "EUR",
        "quantite": 5,
        "prix_execution_cotation": 100.0,
        "prix_execution_reference": 100.0,
        "paper_entry_price": 99.5,
        "entry_date": "2026-06-14",
        "target_exit_price": 130.0,
        "date_limite": "2026-12-14",
    }
    position.update(overrides)
    return position


_CASH_ILLIMITE = {"EUR": 1e9, "USD": 1e9, "GBP": 1e9, "CHF": 1e9}


# --- classement ------------------------------------------------------

def test_rank_signals_orders_by_descending_score():
    signaux = [_signal("A.PA", 10.0), _signal("B.PA", 48.8), _signal("C.PA", 26.9)]
    assert [s["ticker"] for s in portfolio.rank_signals(signaux)] == [
        "B.PA", "C.PA", "A.PA"]


def test_rank_signals_breaks_ties_by_ticker_for_determinism():
    signaux = [_signal("Z.PA", 20.0), _signal("A.PA", 20.0), _signal("M.PA", 20.0)]
    assert [s["ticker"] for s in portfolio.rank_signals(signaux)] == [
        "A.PA", "M.PA", "Z.PA"]


def test_rank_signals_does_not_mutate_its_input():
    signaux = [_signal("A.PA", 10.0), _signal("B.PA", 48.8)]
    portfolio.rank_signals(signaux)
    assert [s["ticker"] for s in signaux] == ["A.PA", "B.PA"]


# --- plafond ---------------------------------------------------------

def test_free_slots_with_no_open_position():
    assert portfolio.free_slots([]) == 10


def test_free_slots_with_one_open_position():
    assert portfolio.free_slots([_bot_position("A.PA")]) == 9


def test_free_slots_with_exactly_ten_open_positions():
    assert portfolio.free_slots([_bot_position(f"T{i}.PA") for i in range(10)]) == 0


def test_free_slots_never_goes_negative():
    assert portfolio.free_slots([_bot_position(f"T{i}.PA") for i in range(12)]) == 0


def test_max_positions_is_ten():
    assert portfolio.MAX_POSITIONS == 10


# --- selection des entrees -------------------------------------------

def test_select_entries_fills_free_slots_in_rank_order():
    signaux = [_signal("A.PA", 10.0), _signal("B.PA", 48.8), _signal("C.PA", 26.9)]
    plans = {t: _plan(t) for t in ("A.PA", "B.PA", "C.PA")}

    retenus, rejets = portfolio.select_entries(signaux, [], plans, _CASH_ILLIMITE)

    assert [r["signal"]["ticker"] for r in retenus] == ["B.PA", "C.PA", "A.PA"]
    assert [r["rang"] for r in retenus] == [1, 2, 3]
    assert rejets == []


def test_select_entries_drops_surplus_signals_when_the_cap_is_reached():
    """Sursouscription (spec 3.5) : les signaux qui ne rentrent pas sont
    perdus, pas mis en file d'attente, et journalises avec leur rang."""
    ouvertes = [_bot_position(f"OPEN{i}.PA") for i in range(9)]
    signaux = [_signal("A.PA", 10.0), _signal("B.PA", 48.8), _signal("C.PA", 26.9)]
    plans = {t: _plan(t) for t in ("A.PA", "B.PA", "C.PA")}

    retenus, rejets = portfolio.select_entries(signaux, ouvertes, plans, _CASH_ILLIMITE)

    assert [r["signal"]["ticker"] for r in retenus] == ["B.PA"]
    assert rejets == [
        {"ticker": "C.PA", "rang": 2, "score": 26.9,
         "raison": "signal_ignore_plafond_atteint"},
        {"ticker": "A.PA", "rang": 3, "score": 10.0,
         "raison": "signal_ignore_plafond_atteint"},
    ]


def test_select_entries_takes_nothing_when_the_cap_is_already_reached():
    ouvertes = [_bot_position(f"OPEN{i}.PA") for i in range(10)]
    signaux = [_signal("A.PA", 10.0)]

    retenus, rejets = portfolio.select_entries(
        signaux, ouvertes, {"A.PA": _plan("A.PA")}, _CASH_ILLIMITE)

    assert retenus == []
    assert rejets == [{"ticker": "A.PA", "rang": 1, "score": 10.0,
                       "raison": "signal_ignore_plafond_atteint"}]


def test_select_entries_zero_share_signal_does_not_consume_a_slot():
    """Spec 3.3 : la place liberee par un signal a 0 action reste
    disponible pour le signal suivant du classement. Ce test echoue si
    le filtre plafond est applique AVANT le filtre 0 action."""
    ouvertes = [_bot_position(f"OPEN{i}.PA") for i in range(9)]
    signaux = [_signal("CHER.PA", 90.0), _signal("B.PA", 48.8)]
    plans = {
        "CHER.PA": _plan("CHER.PA", quantite=0, cout=0.0,
                         motif="signal_ignore_prix_unitaire_superieur_au_budget"),
        "B.PA": _plan("B.PA"),
    }

    retenus, rejets = portfolio.select_entries(signaux, ouvertes, plans, _CASH_ILLIMITE)

    assert [r["signal"]["ticker"] for r in retenus] == ["B.PA"]
    assert rejets == [{
        "ticker": "CHER.PA", "rang": 1, "score": 90.0,
        "raison": "signal_ignore_prix_unitaire_superieur_au_budget",
    }]


def test_select_entries_skips_a_ticker_already_held_by_the_bot():
    signaux = [_signal("A.PA", 48.8), _signal("B.PA", 10.0)]
    plans = {"A.PA": _plan("A.PA"), "B.PA": _plan("B.PA")}

    retenus, rejets = portfolio.select_entries(
        signaux, [_bot_position("A.PA")], plans, _CASH_ILLIMITE)

    assert [r["signal"]["ticker"] for r in retenus] == ["B.PA"]
    assert rejets == [{"ticker": "A.PA", "rang": 1, "score": 48.8,
                       "raison": "deja_en_portefeuille"}]


def test_select_entries_rejects_a_signal_without_a_plan():
    signaux = [_signal("A.PA", 48.8)]
    retenus, rejets = portfolio.select_entries(signaux, [], {}, _CASH_ILLIMITE)

    assert retenus == []
    assert rejets == [{"ticker": "A.PA", "rang": 1, "score": 48.8,
                       "raison": "plan_indisponible"}]


def test_select_entries_propagates_an_invalid_price_reason():
    signaux = [_signal("A.PA", 48.8)]
    plans = {"A.PA": _plan("A.PA", quantite=0, cout=0.0, motif="prix_ou_taux_invalide")}

    retenus, rejets = portfolio.select_entries(signaux, [], plans, _CASH_ILLIMITE)

    assert retenus == []
    assert rejets[0]["raison"] == "prix_ou_taux_invalide"


# --- garde-fou de solde (spec 9.9) -----------------------------------

def test_select_entries_rejects_a_signal_the_cash_cannot_fund():
    signaux = [_signal("A.PA", 48.8)]
    plans = {"A.PA": _plan("A.PA", cout=500.0)}

    retenus, rejets = portfolio.select_entries(signaux, [], plans, {"EUR": 100.0})

    assert retenus == []
    assert rejets == [{"ticker": "A.PA", "rang": 1, "score": 48.8,
                       "raison": "solde_insuffisant"}]


def test_select_entries_decrements_the_cash_cumulatively():
    signaux = [_signal("A.PA", 48.8), _signal("B.PA", 26.9), _signal("C.PA", 10.0)]
    plans = {t: _plan(t, cout=500.0) for t in ("A.PA", "B.PA", "C.PA")}

    retenus, rejets = portfolio.select_entries(signaux, [], plans, {"EUR": 1100.0})

    assert [r["signal"]["ticker"] for r in retenus] == ["A.PA", "B.PA"]
    assert rejets == [{"ticker": "C.PA", "rang": 3, "score": 10.0,
                       "raison": "solde_insuffisant"}]


def test_select_entries_keeps_currencies_independent():
    """Un EUR epuise ne doit pas bloquer un signal finance en USD."""
    signaux = [
        _signal("A.PA", 48.8, currency="EUR"),
        _signal("ADBE", 26.9, index="NASDAQ", currency="USD"),
    ]
    plans = {
        "A.PA": _plan("A.PA", devise_compte="EUR", cout=500.0),
        "ADBE": _plan("ADBE", devise_compte="USD", cout=540.0),
    }

    retenus, rejets = portfolio.select_entries(
        signaux, [], plans, {"EUR": 100.0, "USD": 1000.0})

    assert [r["signal"]["ticker"] for r in retenus] == ["ADBE"]
    assert rejets[0]["raison"] == "solde_insuffisant"


def test_select_entries_treats_an_unknown_currency_as_zero_cash():
    signaux = [_signal("ABBN.SW", 48.8, index="SMI", currency="CHF")]
    plans = {"ABBN.SW": _plan("ABBN.SW", devise_compte="CHF", cout=470.0)}

    retenus, rejets = portfolio.select_entries(signaux, [], plans, {"EUR": 5000.0})

    assert retenus == []
    assert rejets[0]["raison"] == "solde_insuffisant"


def test_select_entries_accepts_a_cost_exactly_equal_to_the_cash():
    signaux = [_signal("A.PA", 48.8)]
    plans = {"A.PA": _plan("A.PA", cout=500.0)}

    retenus, rejets = portfolio.select_entries(signaux, [], plans, {"EUR": 500.0})

    assert len(retenus) == 1
    assert rejets == []


# --- persistance -----------------------------------------------------

def test_load_positions_degrades_to_empty_list(tmp_path):
    assert portfolio.load_positions(str(tmp_path / "absent.json")) == []
    corrupted = tmp_path / "positions.json"
    corrupted.write_text("nope", encoding="utf-8")
    assert portfolio.load_positions(str(corrupted)) == []


def test_save_positions_then_load_positions_round_trips(tmp_path):
    path = str(tmp_path / "nested" / "positions.json")
    positions = [_bot_position("A.PA")]
    portfolio.save_positions(positions, path)

    assert portfolio.load_positions(path) == positions
    assert json.loads(open(path, encoding="utf-8").read())["positions"][0]["ticker"] == "A.PA"


def test_default_positions_path_points_inside_ibkr_bot_package():
    assert portfolio.POSITIONS_PATH.replace("\\", "/").endswith("ibkr_bot/positions.json")


def test_is_missing_covers_none_and_nan():
    assert portfolio._is_missing(None) is True
    assert portfolio._is_missing(float("nan")) is True
    assert portfolio._is_missing(0.0) is False
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/ibkr_bot/test_portfolio.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'ibkr_bot.portfolio'`

- [ ] **Step 3 : Écrire l'implémentation minimale**

Créer `ibkr_bot/portfolio.py` :

```python
# ibkr_bot/portfolio.py
# Plafond de 10 positions, classement des signaux par score composite,
# selection des entrees sous contrainte de solde. (Les regles de sortie
# et la reconciliation sont ajoutees par la tache suivante.)
#
# Ce module DECIDE, il ne passe aucun ordre et ne touche pas au reseau —
# meme decoupage que gold_bot (un seul module parle au courtier).
import json
import math
import os

MAX_POSITIONS = 10  # positions ouvertes PAR LE BOT, pas sur le compte (spec 3.4 / 9.5)

POSITIONS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "positions.json")


def _is_missing(value) -> bool:
    """True si une valeur numerique est absente ou NaN."""
    try:
        return math.isnan(value)
    except TypeError:
        return value is None


def load_positions(path: str = POSITIONS_PATH) -> list[dict]:
    """Positions ouvertes par le bot. [] si le fichier est absent ou
    corrompu — jamais d'exception."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    positions = data.get("positions", [])
    return positions if isinstance(positions, list) else []


def save_positions(positions: list[dict], path: str = POSITIONS_PATH) -> None:
    """Ecriture atomique, meme motif que state.save_state."""
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump({"positions": positions}, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def rank_signals(signals: list[dict]) -> list[dict]:
    """Signaux du jour classes par score composite decroissant (spec
    3.5). Egalite departagee par le ticker, pour que deux executions du
    meme batch prennent exactement les memes decisions."""
    return sorted(signals, key=lambda s: (-s["score"], s["ticker"]))


def free_slots(open_positions: list[dict]) -> int:
    """Places libres sous le plafond de 10 positions du bot."""
    return max(0, MAX_POSITIONS - len(open_positions))


def select_entries(
    signals: list[dict], open_positions: list[dict],
    plans: dict[str, dict], cash_by_currency: dict[str, float],
) -> tuple[list[dict], list[dict]]:
    """Signaux du jour effectivement retenus a l'achat, et rejets motives.

    ORDRE DES FILTRES, qui est lui-meme une regle de la spec :
    deja detenu -> plan absent -> quantite nulle -> plafond -> solde.
    Le cas 0 action passe AVANT le plafond parce que "la place ainsi
    liberee reste disponible pour le signal suivant du classement"
    (spec 3.3) : inverser les deux perdrait un signal finançable au
    profit d'un signal inachetable.
    """
    tickers_detenus = {p["ticker"] for p in open_positions}
    places = free_slots(open_positions)
    soldes = dict(cash_by_currency)

    retenus: list[dict] = []
    rejets: list[dict] = []
    for rang, signal in enumerate(rank_signals(signals), start=1):
        ticker = signal["ticker"]
        base = {"ticker": ticker, "rang": rang, "score": signal["score"]}

        if ticker in tickers_detenus:
            rejets.append({**base, "raison": "deja_en_portefeuille"})
            continue

        plan = plans.get(ticker)
        if plan is None:
            rejets.append({**base, "raison": "plan_indisponible"})
            continue

        if plan["quantite"] < 1:
            rejets.append({**base, "raison": plan["motif"] or "plan_indisponible"})
            continue

        if places < 1:
            rejets.append({**base, "raison": "signal_ignore_plafond_atteint"})
            continue

        devise = plan["devise_compte"]
        cout = plan["cout_estime_devise_compte"]
        if soldes.get(devise, 0.0) < cout:
            rejets.append({**base, "raison": "solde_insuffisant"})
            continue

        soldes[devise] = soldes.get(devise, 0.0) - cout
        places -= 1
        tickers_detenus.add(ticker)
        retenus.append({"signal": signal, "plan": plan, "rang": rang})

    return retenus, rejets
```

- [ ] **Step 4 : Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/ibkr_bot/test_portfolio.py -v`
Expected: PASS (24 tests)

- [ ] **Step 5 : Commit**

```bash
git add ibkr_bot/portfolio.py tests/ibkr_bot/test_portfolio.py
git commit -m "feat(ibkr_bot): plafond de 10 positions, classement par score et garde-fou de solde

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7 : `ibkr_bot/portfolio.py` (2/2) — règles de sortie et réconciliation

**Files:**
- Modify: `ibkr_bot/portfolio.py` (ajout en fin de fichier + 2 constantes en tête)
- Test: `tests/ibkr_bot/test_portfolio.py` (ajout en fin de fichier)

**Interfaces:**
- Consumes: l'enregistrement « position du bot » et `_is_missing` de la tâche 6 ; les positions brutes IBKR de `gateway.positions()` (tâche 4, champs `conid`, `position`, `currency`, `contractDesc`).
- Produces:
  - `STOP_LOSS_PCT: float = -20.0`, `DELAY_MONTHS: int = 6`
  - `deadline_date(entry_date: str) -> str`
  - `exit_reason(position: dict, company: dict | None, today: str) -> str | None`
  - `positions_to_close(open_positions: list[dict], companies_by_ticker: dict, today: str) -> list[dict]` → chaque élément `{"position": dict, "close_reason": str, "current_price": float}`
  - `reconcile(local_positions: list[dict], ibkr_positions: list[dict]) -> dict` → `{"actives": list[dict], "cloturees_hors_bot": list[dict], "anomalies_quantite": list[dict], "ignorees": list[dict]}`

**Ces fonctions reproduisent fidèlement `_close_eligible_positions()` de `indices_score.py` (lignes ~2808-2857 — allez le lire en entier avant de coder).** Trois écarts volontaires, et trois seulement :
1. Le prix de référence du stop-loss est le **prix d'exécution réel du bot** (`prix_execution_reference`), pas l'`entry_price` du paper-trading (spec 3.6).
2. Le benchmark fantôme n'est **pas** reproduit : c'est un instrument de mesure du signal, pas une règle de trading (spec 3.6).
3. La fonction **décide** seulement (elle renvoie un motif) ; elle ne mute pas la position ni ne calcule de `return_pct` — c'est le Plan B qui exécutera et journalisera.

Tout le reste est identique, y compris les inégalités larges (`<=` et `>=`) et la règle « aucune clôture sur donnée périmée ». Un test de non-régression transversal compare les deux implémentations sur une table de scénarios : c'est la garantie mécanique de la promesse centrale du chantier (spec 7).

`prix_execution_reference` est exprimé dans **l'unité de `docs/indices.json`** (livres pour `.L`), parce que c'est à `company["current_price"]` et à `target_exit_price` — tous deux dans cette même unité — qu'il est comparé. Le champ `prix_execution_cotation` (pence pour `.L`) existe pour le journal et pour comparer au `mktPrice` IBKR, jamais pour les règles de sortie.

- [ ] **Step 1 : Écrire les tests qui échouent (sorties)**

Ajouter à la fin de `tests/ibkr_bot/test_portfolio.py` :

```python
# --- date limite -----------------------------------------------------

def test_deadline_date_adds_six_months():
    assert portfolio.deadline_date("2026-09-09") == "2027-03-09"
    assert portfolio.deadline_date("2026-01-31") == "2026-07-31"


def test_deadline_date_clamps_an_impossible_day_of_month():
    """31 aout + 6 mois = 28 fevrier (relativedelta, comme le
    paper-trading)."""
    assert portfolio.deadline_date("2026-08-31") == "2027-02-28"


def test_delay_and_stop_loss_constants_match_the_paper_trading():
    import indices_score

    assert portfolio.STOP_LOSS_PCT == indices_score.SIGNAL_STOP_LOSS_PCT
    assert portfolio.DELAY_MONTHS == indices_score.SIGNAL_SHADOW_DELAY_MONTHS


# --- regles de sortie ------------------------------------------------

def test_exit_reason_stop_loss_below_minus_twenty_percent():
    position = _bot_position("A.PA", prix_execution_reference=100.0)
    assert portfolio.exit_reason(position, {"current_price": 79.0}, "2026-09-14") == "stop_loss"


def test_exit_reason_stop_loss_is_inclusive_at_exactly_minus_twenty_percent():
    position = _bot_position("A.PA", prix_execution_reference=100.0)
    assert portfolio.exit_reason(position, {"current_price": 80.0}, "2026-09-14") == "stop_loss"


def test_exit_reason_none_just_above_the_stop_loss():
    position = _bot_position("A.PA", prix_execution_reference=100.0)
    assert portfolio.exit_reason(position, {"current_price": 80.01}, "2026-09-14") is None


def test_exit_reason_target_reached_is_inclusive():
    position = _bot_position("A.PA", target_exit_price=130.0)
    assert portfolio.exit_reason(position, {"current_price": 130.0}, "2026-09-14") == "objectif_atteint"


def test_exit_reason_delai_max_on_and_after_the_deadline():
    position = _bot_position("A.PA", date_limite="2026-09-14")
    assert portfolio.exit_reason(position, {"current_price": 110.0}, "2026-09-14") == "delai_max"
    assert portfolio.exit_reason(position, {"current_price": 110.0}, "2026-09-15") == "delai_max"


def test_exit_reason_none_before_the_deadline():
    position = _bot_position("A.PA", date_limite="2026-12-14")
    assert portfolio.exit_reason(position, {"current_price": 110.0}, "2026-09-14") is None


def test_exit_reason_stop_loss_wins_over_target_reached():
    """Cas contrive ou les deux conditions sont vraies : l'ordre de
    priorite strict de la spec 3.6 impose stop_loss."""
    position = _bot_position("A.PA", prix_execution_reference=100.0,
                             target_exit_price=50.0)
    assert portfolio.exit_reason(position, {"current_price": 79.0}, "2026-09-14") == "stop_loss"


def test_exit_reason_target_reached_wins_over_delai_max():
    position = _bot_position("A.PA", prix_execution_reference=100.0,
                             target_exit_price=130.0, date_limite="2026-09-01")
    assert portfolio.exit_reason(position, {"current_price": 131.0}, "2026-09-14") == "objectif_atteint"


def test_exit_reason_uses_the_real_fill_price_not_the_paper_entry_price():
    """Spec 3.6 : le stop-loss est relatif au prix d'execution REEL du
    bot. Ici le prix paper declencherait le stop, pas le prix reel."""
    position = _bot_position("A.PA", prix_execution_reference=90.0,
                             paper_entry_price=100.0, target_exit_price=130.0)
    assert portfolio.exit_reason(position, {"current_price": 79.0}, "2026-09-14") is None

    position_reelle_basse = _bot_position("A.PA", prix_execution_reference=100.0,
                                          paper_entry_price=90.0, target_exit_price=130.0)
    assert portfolio.exit_reason(
        position_reelle_basse, {"current_price": 79.0}, "2026-09-14") == "stop_loss"


def test_exit_reason_on_a_london_position_compares_in_pounds():
    """prix_execution_reference est en LIVRES, comme current_price et
    target_exit_price d'indices.json. Un melange avec les pence de
    prix_execution_cotation donnerait ici un stop_loss absurde."""
    position = _bot_position(
        "III.L", index="FTSE", devise_cotation="GBp", devise_compte="GBP",
        prix_execution_cotation=245.0, prix_execution_reference=2.45,
        target_exit_price=3.60, date_limite="2027-03-14")

    assert portfolio.exit_reason(position, {"current_price": 2.60}, "2026-09-14") is None
    assert portfolio.exit_reason(position, {"current_price": 1.90}, "2026-09-14") == "stop_loss"
    assert portfolio.exit_reason(position, {"current_price": 3.70}, "2026-09-14") == "objectif_atteint"


# --- aucune decision sur donnee absente ------------------------------

def test_exit_reason_none_when_the_ticker_disappeared_from_the_data():
    position = _bot_position("A.PA", prix_execution_reference=100.0)
    assert portfolio.exit_reason(position, None, "2026-09-14") is None


@pytest.mark.parametrize("prix", [None, float("nan")])
def test_exit_reason_none_when_the_current_price_is_missing(prix):
    """Jamais de vente declenchee par une donnee absente (spec 3.6)."""
    position = _bot_position("A.PA", prix_execution_reference=100.0,
                             date_limite="2026-01-01")
    assert portfolio.exit_reason(position, {"current_price": prix}, "2026-09-14") is None


def test_exit_reason_none_when_the_position_has_no_reference_price():
    position = _bot_position("A.PA", prix_execution_reference=None)
    assert portfolio.exit_reason(position, {"current_price": 10.0}, "2026-09-14") is None


# --- positions a cloturer --------------------------------------------

def test_positions_to_close_returns_only_eligible_positions():
    positions = [
        _bot_position("A.PA", prix_execution_reference=100.0),
        _bot_position("B.PA", prix_execution_reference=100.0, target_exit_price=130.0),
        _bot_position("C.PA", prix_execution_reference=100.0, date_limite="2026-09-01"),
    ]
    companies = {
        "A.PA": {"current_price": 79.0},
        "B.PA": {"current_price": 131.0},
        "C.PA": {"current_price": 110.0},
    }

    result = portfolio.positions_to_close(positions, companies, "2026-09-14")

    assert [(r["position"]["ticker"], r["close_reason"], r["current_price"]) for r in result] == [
        ("A.PA", "stop_loss", 79.0),
        ("B.PA", "objectif_atteint", 131.0),
        ("C.PA", "delai_max", 110.0),
    ]


def test_positions_to_close_leaves_untouched_what_has_no_data():
    positions = [
        _bot_position("A.PA", prix_execution_reference=100.0, date_limite="2026-01-01"),
        _bot_position("B.PA", prix_execution_reference=100.0, date_limite="2026-01-01"),
    ]
    companies = {"A.PA": {"current_price": None}}

    assert portfolio.positions_to_close(positions, companies, "2026-09-14") == []


def test_positions_to_close_does_not_mutate_the_positions():
    positions = [_bot_position("A.PA", prix_execution_reference=100.0)]
    portfolio.positions_to_close(positions, {"A.PA": {"current_price": 79.0}}, "2026-09-14")

    assert "close_reason" not in positions[0]
    assert positions[0]["quantite"] == 5


# --- non-regression contre le paper-trading (spec 7) -----------------

_SCENARIOS_SORTIE = [
    # (prix_entree, objectif, prix_courant, date_limite, aujourd_hui, attendu)
    (100.0, 130.0, 79.0, "2027-03-09", "2026-09-14", "stop_loss"),
    (100.0, 130.0, 80.0, "2027-03-09", "2026-09-14", "stop_loss"),
    (100.0, 130.0, 80.01, "2027-03-09", "2026-09-14", None),
    (100.0, 130.0, 131.0, "2027-03-09", "2026-09-14", "objectif_atteint"),
    (100.0, 130.0, 130.0, "2027-03-09", "2026-09-14", "objectif_atteint"),
    (100.0, 130.0, 129.99, "2027-03-09", "2026-09-14", None),
    (100.0, 130.0, 110.0, "2026-09-14", "2026-09-14", "delai_max"),
    (100.0, 130.0, 110.0, "2026-09-13", "2026-09-14", "delai_max"),
    (100.0, 130.0, 110.0, "2026-09-15", "2026-09-14", None),
    (100.0, 50.0, 79.0, "2027-03-09", "2026-09-14", "stop_loss"),
    (100.0, 130.0, 131.0, "2026-09-13", "2026-09-14", "objectif_atteint"),
    (100.0, 130.0, 110.0, "2027-03-09", "2026-09-14", None),
]


@pytest.mark.parametrize(
    "entree,objectif,courant,limite,aujourdhui,attendu", _SCENARIOS_SORTIE)
def test_exit_reason_matches_the_paper_trading_logic(
        entree, objectif, courant, limite, aujourdhui, attendu):
    """Garantie mecanique que la logique de sortie du bot reel et celle
    du paper-trading ne divergent pas (spec 7). A prix identiques, les
    deux doivent produire exactement la meme decision."""
    import indices_score

    bot_position = _bot_position(
        "BN.PA", prix_execution_reference=entree, target_exit_price=objectif,
        date_limite=limite)
    decision_bot = portfolio.exit_reason(
        bot_position, {"current_price": courant}, aujourdhui)

    paper_position = {
        "id": "BN.PA-2026-06-08", "ticker": "BN.PA", "name": "Danone", "index": "CAC40",
        "status": "open", "entry_date": "2026-06-08", "entry_price": entree,
        "target_exit_price": objectif, "index_price_at_entry": 7500.0,
        "close_date": None, "close_price": None, "close_reason": None, "return_pct": None,
        "index_price_at_close": None, "index_return_pct": None,
        "shadow_close_date": limite, "shadow_resolved": False,
        "shadow_price": None, "shadow_return_pct": None,
    }
    resultat_paper = indices_score._close_eligible_positions(
        [paper_position], {"BN.PA": {"current_price": courant}},
        {"CAC40": 7600.0}, today=aujourdhui)
    decision_paper = resultat_paper[0]["close_reason"]

    assert decision_bot == attendu
    assert decision_bot == decision_paper


# --- reconciliation (spec 5.4) ---------------------------------------

def test_reconcile_keeps_positions_still_present_at_ibkr():
    locales = [_bot_position("A.PA", conid=4901, quantite=5)]
    chez_ibkr = [{"conid": 4901, "position": 5.0, "currency": "EUR", "contractDesc": "A"}]

    result = portfolio.reconcile(locales, chez_ibkr)

    assert [p["ticker"] for p in result["actives"]] == ["A.PA"]
    assert result["cloturees_hors_bot"] == []
    assert result["anomalies_quantite"] == []
    assert result["ignorees"] == []


def test_reconcile_marks_a_position_sold_outside_the_bot():
    """Vendue a la main par l'utilisateur : retiree du decompte des 10,
    jamais rouverte par le bot."""
    locales = [_bot_position("A.PA", conid=4901), _bot_position("B.PA", conid=4902)]
    chez_ibkr = [{"conid": 4902, "position": 5.0, "currency": "EUR"}]

    result = portfolio.reconcile(locales, chez_ibkr)

    assert [p["ticker"] for p in result["actives"]] == ["B.PA"]
    assert [p["ticker"] for p in result["cloturees_hors_bot"]] == ["A.PA"]


def test_reconcile_treats_a_zero_quantity_ibkr_position_as_closed():
    locales = [_bot_position("A.PA", conid=4901)]
    chez_ibkr = [{"conid": 4901, "position": 0.0, "currency": "EUR"}]

    result = portfolio.reconcile(locales, chez_ibkr)

    assert result["actives"] == []
    assert [p["ticker"] for p in result["cloturees_hors_bot"]] == ["A.PA"]


def test_reconcile_never_touches_a_position_the_bot_did_not_open():
    """Position de l'utilisateur : ignoree, jamais vendue (spec 5.4,
    9.5). Elle ne compte pas non plus dans le plafond de 10."""
    locales = [_bot_position("A.PA", conid=4901)]
    chez_ibkr = [
        {"conid": 4901, "position": 5.0, "currency": "EUR"},
        {"conid": 77777, "position": 300.0, "currency": "USD", "contractDesc": "TSLA"},
    ]

    result = portfolio.reconcile(locales, chez_ibkr)

    assert [p["ticker"] for p in result["actives"]] == ["A.PA"]
    assert result["ignorees"] == [
        {"conid": 77777, "position": 300.0, "currency": "USD", "contractDesc": "TSLA"}]


def test_reconcile_lets_the_ibkr_quantity_win_and_logs_the_anomaly():
    locales = [_bot_position("A.PA", conid=4901, quantite=5)]
    chez_ibkr = [{"conid": 4901, "position": 3.0, "currency": "EUR"}]

    result = portfolio.reconcile(locales, chez_ibkr)

    assert result["actives"][0]["quantite"] == 3
    assert result["anomalies_quantite"] == [
        {"ticker": "A.PA", "conid": 4901, "quantite_locale": 5, "quantite_ibkr": 3}]


def test_reconcile_does_not_mutate_the_local_positions():
    locales = [_bot_position("A.PA", conid=4901, quantite=5)]
    portfolio.reconcile(locales, [{"conid": 4901, "position": 3.0, "currency": "EUR"}])

    assert locales[0]["quantite"] == 5


def test_reconcile_with_an_empty_ibkr_account_closes_everything_out_of_bot():
    locales = [_bot_position("A.PA", conid=4901), _bot_position("B.PA", conid=4902)]

    result = portfolio.reconcile(locales, [])

    assert result["actives"] == []
    assert len(result["cloturees_hors_bot"]) == 2
    assert result["ignorees"] == []


def test_reconcile_result_feeds_free_slots_correctly():
    """La reconciliation precede toute decision : le plafond se calcule
    sur `actives`, pas sur le journal local brut."""
    locales = [_bot_position(f"T{i}.PA", conid=5000 + i) for i in range(10)]
    chez_ibkr = [{"conid": 5000 + i, "position": 5.0, "currency": "EUR"} for i in range(8)]

    result = portfolio.reconcile(locales, chez_ibkr)

    assert len(result["actives"]) == 8
    assert portfolio.free_slots(result["actives"]) == 2
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/ibkr_bot/test_portfolio.py -v`
Expected: FAIL avec `AttributeError: module 'ibkr_bot.portfolio' has no attribute 'deadline_date'`

- [ ] **Step 3 : Ajouter les constantes et les imports en tête de `ibkr_bot/portfolio.py`**

Remplacer, dans `ibkr_bot/portfolio.py`, le bloc :

```python
import json
import math
import os

MAX_POSITIONS = 10  # positions ouvertes PAR LE BOT, pas sur le compte (spec 3.4 / 9.5)
```

par :

```python
import json
import math
import os
from datetime import datetime

from dateutil.relativedelta import relativedelta

MAX_POSITIONS = 10  # positions ouvertes PAR LE BOT, pas sur le compte (spec 3.4 / 9.5)

# Regles de sortie : valeurs IDENTIQUES a celles du paper-trading
# (indices_score.SIGNAL_STOP_LOSS_PCT / SIGNAL_SHADOW_DELAY_MONTHS). Un
# test de non-regression verifie l'egalite des deux jeux de constantes ET
# l'egalite des decisions produites.
STOP_LOSS_PCT = -20.0
DELAY_MONTHS = 6
```

- [ ] **Step 4 : Écrire l'implémentation (sorties et réconciliation)**

Ajouter à la fin de `ibkr_bot/portfolio.py` :

```python
# --- regles de sortie -------------------------------------------------
# Reproduction fidele de indices_score._close_eligible_positions(). Trois
# ecarts volontaires, et trois seulement :
#   1. le prix de reference du stop-loss est le prix d'execution REEL du
#      bot, pas l'entry_price du paper-trading (spec 3.6) ;
#   2. le benchmark fantome n'est pas reproduit — c'est un instrument de
#      mesure du signal, pas une regle de trading (spec 3.6) ;
#   3. on DECIDE seulement : aucune mutation de la position, aucun calcul
#      de return_pct — le Plan B executera et journalisera.
# Tout le reste est identique, inegalites larges comprises.

def deadline_date(entry_date: str) -> str:
    """Date limite de detention : entree + 6 mois, meme calcul que le
    paper-trading (relativedelta, qui ramene le 31 aout au 28/29
    fevrier plutot que de deborder sur mars)."""
    limite = (datetime.strptime(entry_date, "%Y-%m-%d").date()
              + relativedelta(months=DELAY_MONTHS))
    return limite.strftime("%Y-%m-%d")


def exit_reason(position: dict, company: dict | None, today: str) -> str | None:
    """Motif de cloture de `position` aujourd'hui, ou None si aucune
    condition n'est remplie.

    ORDRE DE PRIORITE STRICT, premiere condition remplie gagne (spec
    3.6) : stop_loss -> objectif_atteint -> delai_max.

    Une position dont le ticker a disparu des donnees du jour, ou dont
    le prix courant manque, est laissee INTACTE et reevaluee demain :
    jamais de vente declenchee par une donnee absente.
    """
    if company is None or _is_missing(company.get("current_price")):
        return None
    prix_reference = position.get("prix_execution_reference")
    if _is_missing(prix_reference):
        return None

    current_price = company["current_price"]

    if current_price <= prix_reference * (1 + STOP_LOSS_PCT / 100):
        return "stop_loss"
    if current_price >= position["target_exit_price"]:
        return "objectif_atteint"
    today_date = datetime.strptime(today, "%Y-%m-%d").date()
    if today_date >= datetime.strptime(position["date_limite"], "%Y-%m-%d").date():
        return "delai_max"
    return None


def positions_to_close(open_positions: list[dict], companies_by_ticker: dict,
                       today: str) -> list[dict]:
    """Positions du bot dont une condition de sortie est remplie
    aujourd'hui, avec leur motif et le prix courant ayant declenche la
    decision. Ne mute rien."""
    a_cloturer = []
    for position in open_positions:
        company = companies_by_ticker.get(position["ticker"])
        raison = exit_reason(position, company, today)
        if raison is None:
            continue
        a_cloturer.append({
            "position": position,
            "close_reason": raison,
            "current_price": company["current_price"],
        })
    return a_cloturer


# --- reconciliation ---------------------------------------------------

def reconcile(local_positions: list[dict], ibkr_positions: list[dict]) -> dict:
    """Rapproche le journal local de l'etat reel du compte IBKR, AVANT
    toute decision (spec 5.4). Le bot ne se fie jamais a son seul etat
    local — c'est aussi ce qui rend le batch idempotent : une relance le
    meme jour apres un plantage ne peut pas racheter une position deja
    ouverte.

    - position du journal absente chez IBKR (ou quantite 0) -> vendue
      hors bot : retiree du decompte des 10, jamais rouverte ;
    - position chez IBKR inconnue du journal -> IGNOREE : c'est une
      position de l'utilisateur, le bot n'y touche jamais (spec 9.5) ;
    - quantite divergente -> la quantite IBKR fait foi, l'ecart est
      remonte comme anomalie.

    Ne mute aucune position d'entree : les positions actives renvoyees
    sont des copies.
    """
    par_conid = {}
    for brute in ibkr_positions:
        conid = brute.get("conid")
        if conid is not None:
            par_conid[conid] = brute

    actives, cloturees, anomalies = [], [], []
    conids_du_bot = set()
    for position in local_positions:
        conid = position.get("conid")
        conids_du_bot.add(conid)
        brute = par_conid.get(conid)
        quantite_ibkr = int(brute.get("position", 0)) if brute else 0
        if brute is None or quantite_ibkr == 0:
            cloturees.append(position)
            continue
        active = dict(position)
        if quantite_ibkr != position.get("quantite"):
            anomalies.append({
                "ticker": position["ticker"],
                "conid": conid,
                "quantite_locale": position.get("quantite"),
                "quantite_ibkr": quantite_ibkr,
            })
            active["quantite"] = quantite_ibkr
        actives.append(active)

    ignorees = [b for conid, b in par_conid.items() if conid not in conids_du_bot]

    return {
        "actives": actives,
        "cloturees_hors_bot": cloturees,
        "anomalies_quantite": anomalies,
        "ignorees": ignorees,
    }
```

- [ ] **Step 5 : Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/ibkr_bot/test_portfolio.py -v`
Expected: PASS (55 tests environ, paramétrages compris) — **les 12 scénarios de `test_exit_reason_matches_the_paper_trading_logic` doivent tous passer**. Si l'un échoue, c'est la logique du bot qui doit s'aligner sur `indices_score.py`, jamais l'inverse.

- [ ] **Step 6 : Lancer la suite complète**

Run: `python -m pytest`
Expected: PASS, aucune régression sur `tests/test_indices_score.py`, `tests/test_portfolio_sync*.py` ni `tests/gold_bot/`.

- [ ] **Step 7 : Vérifier une dernière fois le garde structurel du Plan A**

Run: `python -m pytest tests/ibkr_bot/test_gateway.py::test_no_other_plan_a_module_references_the_order_routes -v`
Expected: PASS — aucun module du paquet en dehors de `gateway.py` ne référence `place_market_order`, `confirm_reply` ou `/iserver/account/`.

- [ ] **Step 8 : Commit**

```bash
git add ibkr_bot/portfolio.py tests/ibkr_bot/test_portfolio.py
git commit -m "feat(ibkr_bot): regles de sortie identiques au paper-trading et reconciliation IBKR

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Ce que ce Plan A ne fait PAS — périmètre du futur Plan B

Aucune tâche de ce plan ne doit toucher aux sujets suivants. Ils feront l'objet d'un **Plan B** séparé, « le câblage réel », sur le modèle de `gold_bot/loop.py` + `notify.py` + `api.py` + `deploy/` :

- `ibkr_bot/daily.py` — l'orchestrateur du batch quotidien (`python -m ibkr_bot.daily`), seul endroit d'où un ordre réel partira, avec revérification de `kill_switch`/`dry_run` en profondeur avant chaque envoi.
- `ibkr_bot/journal.py` — `real_trading_log.jsonl` append-only et mise à jour de `positions.json`.
- `ibkr_bot/notify.py` — résumé quotidien par email et alerte immédiate en cas de Gateway indisponible.
- Le préflight (3 tentatives espacées de 10 minutes, abandon propre), le `git pull` en début de batch, et le traitement des questions de confirmation IBKR (`confirm_reply`).
- `deploy/ibkr-gateway.service`, `deploy/ibkr-bot-daily.service` + `.timer` à 14:45 UTC, l'utilisateur système `ibkrbot`, le `.env` en `chmod 600`, le déploiement VPS.
- Les tests d'orchestration `tests/ibkr_bot/test_daily.py` (dry_run → aucun appel d'ordre ; kill_switch → aucune action ; préflight en échec ; un ordre en échec n'interrompt pas les suivants ; relance le même jour → pas de double achat).

Reste également en suspens, **avant le lancement en réel uniquement** (spec 9.2 / section 6) : la vérification manuelle du coût des abonnements de données de marché par place et du seuil de dispense, plus la question « un ordre au marché peut-il être passé sans abonnement de données sur la place concernée ? ».
