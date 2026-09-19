# API HTTPS en lecture seule pour le Bot Actions (ibkr_bot) — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Donner au panneau "Bot Actions" (`docs/index.html`) un tableau de bord protégé par jeton — solde du compte, positions ouvertes (sans restriction de mode) et historique des actions récentes — via un nouveau module `ibkr_bot/api.py`, sans jamais rouvrir de route de mutation ni de connexion live à IB Gateway.

**Architecture:** Un unique endpoint `GET /dashboard`, calqué sur `gold_bot/api.py` (mêmes garde-fous : échec fermé, comparaison à temps constant, CORS restreint, pas de doc Swagger). Il lit trois fichiers déjà écrits par le batch quotidien (`ibkr_bot/latest_account.json`, `ibkr_bot/positions.json`, `ibkr_bot/real_trading_log.jsonl`) via deux nouvelles fonctions de lecture dans `ibkr_bot/journal.py` et la fonction déjà existante `portfolio.load_positions()`. Déployé comme un nouveau service systemd (port 8444, même certificat TLS que le Bot Or). Le frontend ajoute un bloc optionnel, débloqué par jeton, sous le statut public déjà en place.

**Tech Stack:** Python 3, FastAPI + `fastapi.testclient.TestClient` (déjà une dépendance du projet, voir `requirements-bot.txt`), pytest/monkeypatch, JavaScript vanilla (`docs/index.html`), Playwright (Python, déjà installé localement) pour la vérification frontend faute de runner JS dans ce dépôt.

**Spec:** `docs/superpowers/specs/2026-09-20-ibkr-bot-api-design.md`

## Global Constraints

- Un seul endpoint : `GET /dashboard`. **Aucune route de mutation** (pas de `/kill`, `/resume`, `/profile`, ni équivalent) — le coupe-circuit reste exclusivement SSH-only (`ibkr_bot/state.json`), décision non remise en question ici.
- `ibkr_bot/api.py` n'importe **jamais** `ibkr_bot/gateway.py` — aucune connexion live à IB Gateway déclenchée par une requête API, seulement des fichiers déjà écrits par le dernier batch réussi.
- Jeton dédié `IBKR_BOT_API_TOKEN` (jamais réutilisé depuis `BOT_API_TOKEN` de gold_bot). `_check_token` échoue fermé (401) si la variable d'environnement est absente, comparaison à temps constant sur les octets UTF-8 (`hmac.compare_digest`, encodage `surrogateescape` côté jeton reçu) — copié à l'identique de `gold_bot/api.py::_check_token`.
- `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)`. CORS : `allow_origins=["https://alexandreauq.github.io"]`, `allow_methods=["GET"]` uniquement, `allow_headers=["X-Bot-Token"]`.
- Toute valeur numérique flottante placée dans la réponse JSON doit passer par un `_sanitize_number` (NaN/Inf → `None`) avant sérialisation — FastAPI n'utilise PAS `allow_nan=False` par défaut, et ce dépôt a déjà eu un incident de production (2026-09-19) causé par un `NaN` non filtré cassant un `JSON.parse()` côté navigateur (voir `docs/superpowers/specs/2026-09-20-ibkr-bot-api-design.md` §5 et la mémoire `project_infrastructure_workflows`).
- **Pas de restriction dry_run/réel sur les positions servies par `/dashboard`** (contrairement à `docs/ibkr_bot_status.json`) — c'est délibéré (spec §4.3), ne pas ajouter de contrôle de mode ici.
- Positions publiées : `{ticker, name, index, quantite, prix_entree, date_entree, target_exit_price}`. Jamais `conid`.
- Le fichier public `docs/ibkr_bot_status.json` / `ibkr_bot/status_publish.py` **n'est ni modifié ni dupliqué** par ce plan — les deux mécanismes coexistent, aucune route `/status` n'est recréée ici.
- Port du nouveau service : **8444** (8443 est déjà pris par `gold-bot-api.service`). Même certificat TLS que le Bot Or (`goldbot.fr`), même profil `User=ibkrbot`, `WorkingDirectory=/home/ibkrbot/analyse-or`.
- Toute modification de `docs/index.html` doit être accompagnée d'un incrément de `CACHE_NAME` dans `docs/service-worker.js` (convention établie cette session — sinon les navigateurs continuent de servir l'ancienne coquille indéfiniment).
- **Niveau de rigueur** : cette fonctionnalité est lecture seule de bout en bout (aucun chemin de passage d'ordre nulle part dans ce périmètre) — elle ne nécessite PAS la discipline "vérification maximale à chaque étape" appliquée aux plans Bot Or Plan B / ibkr_bot Plan A/B (passage d'ordres réels). Une revue de tâche normale, plus une revue finale de branche solide, sont le bon niveau ici — ne pas sur-analyser, ne pas non plus bâcler.
- Tests Python : conventions TDD déjà établies dans ce dépôt — `tmp_path`, aucun accès disque/réseau réel, jamais les chemins par défaut des modules dans un test (un run `main()`/`dashboard()` non mocké a déjà, une fois cette session, écrit pour de vrai dans un fichier réel du dépôt faute de monkeypatch — toujours passer un chemin explicite ou monkeypatcher la constante lue par la fonction testée).
- Pas de Node.js disponible localement pour tester `docs/index.html` — la vérification frontend se fait avec un vrai navigateur piloté par Playwright (paquet Python déjà installé) + `python -m http.server` servant `docs/`, pas seulement par lecture du code.

---

## Task 1 : `ibkr_bot/journal.py` — deux nouveaux lecteurs

**Files:**
- Modify: `ibkr_bot/journal.py`
- Test: `tests/ibkr_bot/test_journal.py`

**Interfaces:**
- Consumes : `journal.LATEST_ACCOUNT_PATH`, `journal.REAL_TRADING_LOG_PATH` (constantes déjà existantes, `ibkr_bot/journal.py:24-27`).
- Produces :
  - `journal.load_account_snapshot(path: str = LATEST_ACCOUNT_PATH) -> dict` — symétrique de `save_account_snapshot`, dégrade vers `{}` (jamais d'exception).
  - `journal.read_recent_actions(path: str = REAL_TRADING_LOG_PATH, limit: int = 50) -> list[dict]` — aplatit `entrees` + `sorties` de TOUTES les lignes du journal (pas seulement le jour), ne garde que `statut in ("execute", "simule")`, ajoute un champ `"date"` (repris de `run["date"]` — aucun enregistrement individuel de `build_order_record` ne porte d'horodatage propre), trie du plus ancien au plus récent, tronque aux `limit` dernières. Utilisé par `ibkr_bot/api.py` en Task 2.

- [ ] **Step 1 : Écrire les tests de `load_account_snapshot`**

```python
def test_load_account_snapshot_reads_back_what_save_account_snapshot_wrote(tmp_path):
    path = str(tmp_path / "latest_account.json")
    journal.save_account_snapshot({"base_cash": 8000.0}, path)
    data = journal.load_account_snapshot(path)
    assert data["base_cash"] == 8000.0
    assert data["fetched_at"].endswith("Z")


def test_load_account_snapshot_degrades_to_empty_dict_when_file_absent_or_corrupt(tmp_path):
    assert journal.load_account_snapshot(str(tmp_path / "absent.json")) == {}
    corrompu = tmp_path / "corrompu.json"
    corrompu.write_text("pas du json", encoding="utf-8")
    assert journal.load_account_snapshot(str(corrompu)) == {}


def test_load_account_snapshot_degrades_to_empty_dict_when_top_level_is_not_a_dict(tmp_path):
    path = tmp_path / "liste.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert journal.load_account_snapshot(str(path)) == {}
```

Ajoute ces trois tests à `tests/ibkr_bot/test_journal.py` (le fichier existe déjà — ajoute-les après `test_save_account_snapshot_writes_json_and_never_raises`, dernier test du fichier).

- [ ] **Step 2 : Lancer les tests, vérifier qu'ils échouent**

Run: `python -m pytest tests/ibkr_bot/test_journal.py -k load_account_snapshot -v`
Expected: FAIL — `AttributeError: module 'ibkr_bot.journal' has no attribute 'load_account_snapshot'`

- [ ] **Step 3 : Implémenter `load_account_snapshot`**

Ajoute dans `ibkr_bot/journal.py`, après `save_account_snapshot` (fin de fichier actuelle) :

```python
def load_account_snapshot(path: str = LATEST_ACCOUNT_PATH) -> dict:
    """Dernier instantane du compte ecrit par save_account_snapshot.
    Fichier absent/corrompu -> {}, jamais d'exception (meme contrat que
    portfolio.load_positions)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}
```

- [ ] **Step 4 : Lancer les tests, vérifier qu'ils passent**

Run: `python -m pytest tests/ibkr_bot/test_journal.py -k load_account_snapshot -v`
Expected: 3 PASSED

- [ ] **Step 5 : Commit**

```bash
git add ibkr_bot/journal.py tests/ibkr_bot/test_journal.py
git commit -m "Ajoute journal.load_account_snapshot, symetrique de save_account_snapshot"
```

- [ ] **Step 6 : Écrire les tests de `read_recent_actions`**

```python
def test_read_recent_actions_flattens_entrees_and_sorties_and_tags_the_run_date(tmp_path):
    path = tmp_path / "real_trading_log.jsonl"
    path.write_text(
        json.dumps({"date": "2026-09-18", "entrees": [
            {"ticker": "MC.PA", "sens": "BUY", "quantite": 5, "statut": "execute"}
        ], "sorties": [
            {"ticker": "SAP.DE", "sens": "SELL", "quantite": 2, "statut": "simule"}
        ]}) + "\n",
        encoding="utf-8",
    )
    actions = journal.read_recent_actions(str(path))
    assert [(a["ticker"], a["date"]) for a in actions] == [("MC.PA", "2026-09-18"), ("SAP.DE", "2026-09-18")]


def test_read_recent_actions_excludes_annule_interruption_and_other_non_taken_statuts(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text(
        json.dumps({"date": "2026-09-18", "entrees": [
            {"ticker": "A", "statut": "execute"},
            {"ticker": "B", "statut": "annule_interruption"},
            {"ticker": "C", "statut": "erreur"},
        ], "sorties": []}) + "\n",
        encoding="utf-8",
    )
    actions = journal.read_recent_actions(str(path))
    assert [a["ticker"] for a in actions] == ["A"]


def test_read_recent_actions_orders_chronologically_across_multiple_days_and_truncates_to_limit(tmp_path):
    path = tmp_path / "log.jsonl"
    lignes = []
    for jour in range(60):
        lignes.append(json.dumps({
            "date": f"2026-09-{jour + 1:02d}" if jour < 28 else f"2026-10-{jour - 27:02d}",
            "entrees": [{"ticker": f"T{jour}", "statut": "execute"}],
            "sorties": [],
        }))
    path.write_text("\n".join(lignes) + "\n", encoding="utf-8")

    actions = journal.read_recent_actions(str(path), limit=50)

    assert len(actions) == 50
    assert [a["ticker"] for a in actions] == [f"T{i}" for i in range(10, 60)]


def test_read_recent_actions_skips_corrupt_lines_and_non_dict_records(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text(
        "pas du json\n"
        + json.dumps({"date": "x", "entrees": "pas une liste", "sorties": None}) + "\n"
        + json.dumps({"date": "2026-09-18", "entrees": [5, {"ticker": "OK", "statut": "execute"}], "sorties": []}) + "\n",
        encoding="utf-8",
    )
    actions = journal.read_recent_actions(str(path))
    assert [a["ticker"] for a in actions] == ["OK"]


def test_read_recent_actions_returns_empty_list_when_file_is_absent(tmp_path):
    assert journal.read_recent_actions(str(tmp_path / "absent.jsonl")) == []
```

Ajoute ces cinq tests après ceux de `load_account_snapshot`.

- [ ] **Step 7 : Lancer les tests, vérifier qu'ils échouent**

Run: `python -m pytest tests/ibkr_bot/test_journal.py -k read_recent_actions -v`
Expected: FAIL — `AttributeError: module 'ibkr_bot.journal' has no attribute 'read_recent_actions'`

- [ ] **Step 8 : Implémenter `read_recent_actions`**

Ajoute dans `ibkr_bot/journal.py`, à la suite de `load_account_snapshot` :

```python
def read_recent_actions(path: str = REAL_TRADING_LOG_PATH, limit: int = 50) -> list[dict]:
    """Les `limit` dernieres actions (achats/ventes) reellement executees
    ou simulees par le bot, tous jours confondus, triees du plus ancien au
    plus recent. Parcourt TOUT le fichier avant de tronquer (comme
    gold_bot.api._read_recent_decisions) : ce fichier grossit lentement
    (~1 ligne de run par jour), pas de souci de performance a moyen terme.

    Chaque entree aplatie ajoute un champ "date" (repris de run["date"]) :
    journal.build_order_record ne porte aucun horodatage propre (une seule
    ligne de run par jour), donc c'est le seul moyen pour l'appelant de
    savoir QUAND une action a ete prise. Une ligne, un run non-dict, ou un
    enregistrement individuel malforme est ignore sans jamais faire
    echouer le reste (meme contrat que gold_bot.api._read_recent_decisions)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except Exception:
        return []
    actions: list[dict] = []
    for ligne in lines:
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            run = json.loads(ligne)
        except json.JSONDecodeError:
            continue
        if not isinstance(run, dict):
            continue
        for cle in ("entrees", "sorties"):
            records = run.get(cle)
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict):
                    continue
                if record.get("statut") not in ("execute", "simule"):
                    continue
                action = dict(record)
                action["date"] = run.get("date")
                actions.append(action)
    return actions[-limit:]
```

- [ ] **Step 9 : Lancer les tests, vérifier qu'ils passent**

Run: `python -m pytest tests/ibkr_bot/test_journal.py -v`
Expected: tous PASSED (anciens + 8 nouveaux)

- [ ] **Step 10 : Commit**

```bash
git add ibkr_bot/journal.py tests/ibkr_bot/test_journal.py
git commit -m "Ajoute journal.read_recent_actions, historique aplati des actions du bot"
```

---

## Task 2 : `ibkr_bot/api.py` — endpoint `GET /dashboard`

**Files:**
- Create: `ibkr_bot/api.py`
- Test: `tests/ibkr_bot/test_api.py` (nouveau fichier)

**Interfaces:**
- Consumes : `journal.load_account_snapshot`, `journal.read_recent_actions` (Task 1), `portfolio.load_positions` (déjà existant, `ibkr_bot/portfolio.py:38-49`), `journal.LATEST_ACCOUNT_PATH`, `journal.REAL_TRADING_LOG_PATH`, `portfolio.POSITIONS_PATH`.
- Produces : `app` (instance FastAPI), route `GET /dashboard` renvoyant `{"balance", "balance_fetched_at", "positions": [...], "actions": [...]}`. Constantes de module `api.ACCOUNT_SNAPSHOT_PATH`, `api.POSITIONS_PATH`, `api.REAL_TRADING_LOG_PATH` (utilisées par les tests pour rediriger vers `tmp_path`).

**Note d'implémentation (résolution d'une ambiguïté de la spec) :** la spec §4.5 montre `journal.load_account_snapshot()` et `portfolio.load_positions()` appelés SANS argument. En Python, l'argument par défaut d'une fonction (`path: str = LATEST_ACCOUNT_PATH`) est figé au moment de la DÉFINITION de la fonction — patcher `journal.LATEST_ACCOUNT_PATH` après coup dans un test ne changerait donc rien à ce que `load_account_snapshot()` lit sans argument explicite. `gold_bot/api.py` évite ce piège en dupliquant ses propres constantes de chemin (`LATEST_BALANCE_PATH` etc., lues comme variables globales à CHAQUE appel, pas comme argument par défaut) plutôt que de dépendre des constantes de `gold_bot/loop.py`. Ce plan reprend exactement ce motif : `ibkr_bot/api.py` définit ses trois propres constantes de chemin (initialisées depuis celles de `journal`/`portfolio`) et les passe explicitement à chaque appel — un test de cohérence (`test_api_path_constants_match_source_modules`) garde les deux jeux de constantes synchronisés, comme le fait déjà `test_dashboard_cache_path_constants_match_loop_module` côté gold_bot.

- [ ] **Step 1 : Écrire tous les tests de `ibkr_bot/api.py`**

Crée `tests/ibkr_bot/test_api.py` avec le contenu ci-dessous en une fois (autorisation/structure + assemblage complet de `/dashboard` — écrire tous les tests avant toute implémentation, plutôt qu'en deux vagues, pour un cycle TDD propre) :

```python
import json

import pytest
from fastapi.testclient import TestClient

import ibkr_bot.api as api
import ibkr_bot.journal as journal
import ibkr_bot.portfolio as portfolio


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("IBKR_BOT_API_TOKEN", "secret-token")
    return TestClient(api.app)


def test_dashboard_requires_valid_token(client):
    response = client.get("/dashboard")
    assert response.status_code == 401


def test_dashboard_rejects_wrong_token(client):
    response = client.get("/dashboard", headers={"X-Bot-Token": "wrong-token"})
    assert response.status_code == 401


def test_dashboard_rejected_when_ibkr_bot_api_token_not_configured(monkeypatch):
    monkeypatch.delenv("IBKR_BOT_API_TOKEN", raising=False)
    test_client = TestClient(api.app)
    response = test_client.get("/dashboard", headers={"X-Bot-Token": "anything"})
    assert response.status_code == 401


def test_dashboard_only_exposes_the_get_method_no_mutation_route_exists(client):
    response = client.post("/dashboard", headers={"X-Bot-Token": "secret-token"})
    assert response.status_code in (405, 404)
    methods = [route.methods for route in api.app.routes if hasattr(route, "methods")]
    all_methods = set().union(*methods) if methods else set()
    assert "POST" not in all_methods
    assert "DELETE" not in all_methods


def test_gateway_module_is_never_referenced_in_api_source():
    import inspect
    source = inspect.getsource(api)
    assert "gateway" not in source


def test_api_path_constants_match_source_modules():
    assert api.ACCOUNT_SNAPSHOT_PATH == journal.LATEST_ACCOUNT_PATH
    assert api.POSITIONS_PATH == portfolio.POSITIONS_PATH
    assert api.REAL_TRADING_LOG_PATH == journal.REAL_TRADING_LOG_PATH


def _seed(monkeypatch, tmp_path, *, account=None, positions=None, actions_lines=None):
    monkeypatch.setattr(api, "ACCOUNT_SNAPSHOT_PATH", str(tmp_path / "latest_account.json"))
    monkeypatch.setattr(api, "POSITIONS_PATH", str(tmp_path / "positions.json"))
    monkeypatch.setattr(api, "REAL_TRADING_LOG_PATH", str(tmp_path / "real_trading_log.jsonl"))
    if account is not None:
        with open(api.ACCOUNT_SNAPSHOT_PATH, "w", encoding="utf-8") as fh:
            json.dump(account, fh)
    if positions is not None:
        with open(api.POSITIONS_PATH, "w", encoding="utf-8") as fh:
            json.dump({"positions": positions}, fh)
    if actions_lines is not None:
        with open(api.REAL_TRADING_LOG_PATH, "w", encoding="utf-8") as fh:
            for entry in actions_lines:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def test_dashboard_returns_full_body_when_all_sources_present(client, monkeypatch, tmp_path):
    _seed(
        monkeypatch, tmp_path,
        account={"base_cash": 8123.45, "fetched_at": "2026-09-20T14:45:03Z"},
        positions=[{
            "id": "MC.PA-2026-09-15", "ticker": "MC.PA", "name": "LVMH", "index": "CAC40",
            "conid": 17275, "quantite": 5, "prix_execution_reference": 90.5,
            "date_entree": "2026-09-15", "target_exit_price": 120.0,
        }],
        actions_lines=[{
            "date": "2026-09-18", "entrees": [{
                "ticker": "MC.PA", "sens": "BUY", "quantite": 5, "prix_execution": 90.5,
                "statut": "execute",
            }], "sorties": [],
        }],
    )

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["balance"] == 8123.45
    assert body["balance_fetched_at"] == "2026-09-20T14:45:03Z"
    assert body["positions"] == [{
        "ticker": "MC.PA", "name": "LVMH", "index": "CAC40", "quantite": 5,
        "prix_entree": 90.5, "date_entree": "2026-09-15", "target_exit_price": 120.0,
    }]
    assert body["actions"] == [{
        "ticker": "MC.PA", "sens": "BUY", "quantite": 5, "prix_execution": 90.5,
        "statut": "execute", "date": "2026-09-18",
    }]


def test_dashboard_degrades_gracefully_when_all_source_files_are_absent(client, monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["balance"] is None
    assert body["balance_fetched_at"] is None
    assert body["positions"] == []
    assert body["actions"] == []


def test_dashboard_sanitizes_non_finite_balance(client, monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, account={"base_cash": float("nan"), "fetched_at": "2026-09-20T14:45:03Z"})

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 200
    body = response.json()
    assert body["balance"] is None
    assert body["balance_fetched_at"] == "2026-09-20T14:45:03Z"


def test_dashboard_sanitizes_non_finite_position_fields(client, monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, positions=[{
        "ticker": "MC.PA", "name": "LVMH", "index": "CAC40", "quantite": 5,
        "prix_execution_reference": float("nan"), "date_entree": "2026-09-15",
        "target_exit_price": float("inf"),
    }])

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    positions = response.json()["positions"]
    assert positions[0]["prix_entree"] is None
    assert positions[0]["target_exit_price"] is None


def test_dashboard_sanitizes_non_finite_action_fields(client, monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, actions_lines=[{
        "date": "2026-09-18", "entrees": [{
            "ticker": "MC.PA", "sens": "BUY", "statut": "execute",
            "prix_execution": float("nan"), "ecart_paper_pct": 2.0,
        }], "sorties": [],
    }])

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    action = response.json()["actions"][0]
    assert action["prix_execution"] is None
    assert action["ecart_paper_pct"] == 2.0


def test_dashboard_excludes_annule_interruption_actions(client, monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, actions_lines=[{
        "date": "2026-09-18", "entrees": [
            {"ticker": "A", "statut": "execute"},
            {"ticker": "B", "statut": "annule_interruption"},
        ], "sorties": [],
    }])

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    assert [a["ticker"] for a in response.json()["actions"]] == ["A"]


def test_dashboard_respects_the_recent_actions_limit(client, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "ACCOUNT_SNAPSHOT_PATH", str(tmp_path / "latest_account.json"))
    monkeypatch.setattr(api, "POSITIONS_PATH", str(tmp_path / "positions.json"))
    lignes = [
        json.dumps({"date": f"2026-09-{(i % 28) + 1:02d}", "entrees": [
            {"ticker": f"T{i}", "statut": "execute"}], "sorties": []})
        for i in range(60)
    ]
    log_path = tmp_path / "real_trading_log.jsonl"
    log_path.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    monkeypatch.setattr(api, "REAL_TRADING_LOG_PATH", str(log_path))

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    actions = response.json()["actions"]
    assert len(actions) == 50
    assert [a["ticker"] for a in actions] == [f"T{i}" for i in range(10, 60)]


def test_dashboard_skips_non_dict_position_entries(client, monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, positions=[
        None, 5, {"ticker": "MC.PA", "name": "LVMH", "index": "CAC40", "quantite": 5,
                  "prix_execution_reference": 90.5, "date_entree": "2026-09-15",
                  "target_exit_price": 120.0},
    ])

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    positions = response.json()["positions"]
    assert len(positions) == 1
    assert positions[0]["ticker"] == "MC.PA"
```

- [ ] **Step 2 : Lancer les tests, vérifier qu'ils échouent**

Run: `python -m pytest tests/ibkr_bot/test_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ibkr_bot.api'`

- [ ] **Step 3 : Créer `ibkr_bot/api.py`**

```python
# ibkr_bot/api.py
# Point d'acces HTTPS en LECTURE SEULE du Bot Actions : une unique route,
# GET /dashboard, protegee par jeton. Contrairement a gold_bot/api.py, ce
# module n'a AUCUNE route de mutation (pas de /kill, /resume, /profile) :
# le coupe-circuit d'urgence reste exclusivement SSH-only
# (ibkr_bot/state.json), decision non remise en question par
# docs/superpowers/specs/2026-09-20-ibkr-bot-api-design.md. Ce module
# n'importe jamais ibkr_bot/gateway.py (jamais de connexion live a IB
# Gateway a la demande) : toutes les donnees viennent de fichiers ecrits
# par le dernier batch quotidien reussi (voir spec 4.2).
import hmac
import math
import os

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import ibkr_bot.journal as journal
import ibkr_bot.portfolio as portfolio

# Constantes dupliquees ici plutot que lues via les arguments par defaut de
# journal.load_account_snapshot()/read_recent_actions() et
# portfolio.load_positions() : ces fonctions figent leur argument `path`
# par defaut AU MOMENT DE LEUR DEFINITION (comportement standard de
# Python), donc patcher journal.LATEST_ACCOUNT_PATH apres coup ne
# changerait pas ce que load_account_snapshot() lit sans argument. Meme
# motif que gold_bot/api.py, qui duplique ses propres LATEST_BALANCE_PATH
# etc. plutot que de dependre des constantes de gold_bot/loop.py — voir
# test_api_path_constants_match_source_modules, qui garde les deux jeux de
# constantes synchronises.
ACCOUNT_SNAPSHOT_PATH = journal.LATEST_ACCOUNT_PATH
POSITIONS_PATH = portfolio.POSITIONS_PATH
REAL_TRADING_LOG_PATH = journal.REAL_TRADING_LOG_PATH
RECENT_ACTIONS_LIMIT = 50

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://alexandreauq.github.io"],
    allow_methods=["GET"],
    allow_headers=["X-Bot-Token"],
)


def _check_token(x_bot_token: str | None) -> None:
    """Identique a gold_bot.api._check_token : echec ferme si
    IBKR_BOT_API_TOKEN n'est pas configure (pas seulement si le jeton
    fourni est faux), comparaison a temps constant sur les octets UTF-8
    (surrogateescape cote jeton recu, puisque Starlette decode les
    en-tetes en latin-1 et qu'un octet non-ASCII ferait sinon lever un
    TypeError non capture)."""
    expected = os.environ.get("IBKR_BOT_API_TOKEN")
    if not expected or not hmac.compare_digest(
        (x_bot_token or "").encode("utf-8", "surrogateescape"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Jeton invalide ou absent")


def _sanitize_number(v):
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


@app.get("/dashboard")
def dashboard(x_bot_token: str | None = Header(default=None)):
    _check_token(x_bot_token)

    account = journal.load_account_snapshot(ACCOUNT_SNAPSHOT_PATH)
    raw_positions = portfolio.load_positions(POSITIONS_PATH)
    raw_actions = journal.read_recent_actions(REAL_TRADING_LOG_PATH, RECENT_ACTIONS_LIMIT)

    positions = [
        {
            "ticker": p.get("ticker"),
            "name": p.get("name"),
            "index": p.get("index"),
            "quantite": p.get("quantite"),
            "prix_entree": _sanitize_number(p.get("prix_execution_reference")),
            "date_entree": p.get("date_entree"),
            "target_exit_price": _sanitize_number(p.get("target_exit_price")),
        }
        for p in raw_positions
        if isinstance(p, dict)
    ]

    # Sanitisation generique (pas seulement les deux champs deja
    # explicitement lus ci-dessus pour les positions) : les actions
    # transportent plusieurs autres champs numeriques issus de calculs
    # reels (ecart_paper_pct, taux_de_change...) qui pourraient un jour
    # produire NaN/Inf. FastAPI ne passe PAS allow_nan=False par defaut
    # (voir la note en tete de fichier et Global Constraints du plan).
    actions = [
        {k: _sanitize_number(v) for k, v in a.items()}
        for a in raw_actions
    ]

    return {
        "balance": _sanitize_number(account.get("base_cash")),
        "balance_fetched_at": account.get("fetched_at"),
        "positions": positions,
        "actions": actions,
    }
```

- [ ] **Step 4 : Lancer les tests, vérifier qu'ils passent**

Run: `python -m pytest tests/ibkr_bot/test_api.py -v`
Expected: 14 PASSED

- [ ] **Step 5 : Commit**

```bash
git add ibkr_bot/api.py tests/ibkr_bot/test_api.py
git commit -m "Ajoute ibkr_bot/api.py, endpoint GET /dashboard protege par jeton"
```

---

## Task 3 : Déploiement — nouveau service systemd

**Files:**
- Create: `deploy/ibkr-bot-api.service`
- Create: `deploy/setup-tls-ibkr.sh`
- Modify: `deploy/harden-vps-firewall.sh`
- Test: `tests/ibkr_bot/test_deploy_files.py`

**Interfaces:**
- Consumes : aucune (fichiers statiques, pas de code Python).
- Produces : les trois fichiers de déploiement, vérifiés par 4 nouveaux tests dans `tests/ibkr_bot/test_deploy_files.py` (fichier existant).

- [ ] **Step 1 : Écrire les tests des fichiers de déploiement**

Ajoute à `tests/ibkr_bot/test_deploy_files.py`, avant `test_the_gold_readme_points_to_the_ibkr_one` (dernier test du fichier) :

```python
def test_the_api_service_runs_as_the_dedicated_user_on_its_own_port():
    contenu = _lire("ibkr-bot-api.service")
    assert "User=ibkrbot" in contenu
    assert "User=goldbot" not in contenu
    assert "--port 8444" in contenu
    assert "ibkr_bot.api:app" in contenu
    assert "EnvironmentFile=/home/ibkrbot/analyse-or/.env" in contenu
    assert "Restart=on-failure" in contenu


def test_the_api_service_reuses_the_goldbot_fr_certificate():
    contenu = _lire("ibkr-bot-api.service")
    assert "/etc/letsencrypt/live/goldbot.fr/privkey.pem" in contenu
    assert "/etc/letsencrypt/live/goldbot.fr/fullchain.pem" in contenu


def test_the_firewall_script_opens_the_api_port():
    contenu = _lire("harden-vps-firewall.sh")
    assert "ufw allow 8444" in contenu


def test_the_tls_setup_script_adds_ibkrbot_to_the_ssl_cert_group_and_a_renewal_hook():
    contenu = _lire("setup-tls-ibkr.sh")
    assert "usermod -aG ssl-cert ibkrbot" in contenu
    assert "systemctl restart ibkr-bot-api" in contenu
```

- [ ] **Step 2 : Lancer les tests, vérifier qu'ils échouent**

Run: `python -m pytest tests/ibkr_bot/test_deploy_files.py -v`
Expected: FAIL — `FileNotFoundError` sur `deploy/ibkr-bot-api.service` (et les deux autres)

- [ ] **Step 3 : Créer `deploy/ibkr-bot-api.service`**

```ini
[Unit]
Description=Bot Actions IBKR - API tableau de bord
After=network.target

[Service]
Type=simple
User=ibkrbot
WorkingDirectory=/home/ibkrbot/analyse-or
EnvironmentFile=/home/ibkrbot/analyse-or/.env
ExecStart=/home/ibkrbot/analyse-or/venv/bin/uvicorn ibkr_bot.api:app --host 0.0.0.0 --port 8444 --ssl-keyfile /etc/letsencrypt/live/goldbot.fr/privkey.pem --ssl-certfile /etc/letsencrypt/live/goldbot.fr/fullchain.pem
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4 : Créer `deploy/setup-tls-ibkr.sh`**

Ce script ne redemande PAS de nouveau certificat (celui de `goldbot.fr` existe déjà et couvre n'importe quel port de ce domaine) — il ajoute seulement le hook de redémarrage et l'accès au certificat pour l'utilisateur `ibkrbot`, en plus (pas à la place) de `deploy/setup-tls.sh` déjà existant pour `goldbot`/`gold-bot-api` :

```bash
set -e

mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/restart-ibkr-bot-api.sh <<'HOOK'
#!/bin/bash
systemctl restart ibkr-bot-api
HOOK
chmod +x /etc/letsencrypt/renewal-hooks/deploy/restart-ibkr-bot-api.sh

groupadd -f ssl-cert
usermod -aG ssl-cert ibkrbot
chgrp -R ssl-cert /etc/letsencrypt/archive /etc/letsencrypt/live
chmod -R g+rX /etc/letsencrypt/archive /etc/letsencrypt/live

echo "DONE"
```

- [ ] **Step 5 : Étendre `deploy/harden-vps-firewall.sh`**

Ajoute une ligne après `ufw allow 8443` (garde le reste du fichier identique) :

```
ufw allow 8443
ufw allow 8444
```

- [ ] **Step 6 : Lancer les tests, vérifier qu'ils passent**

Run: `python -m pytest tests/ibkr_bot/test_deploy_files.py -v`
Expected: tous PASSED (anciens + 4 nouveaux)

- [ ] **Step 7 : Commit**

```bash
git add deploy/ibkr-bot-api.service deploy/setup-tls-ibkr.sh deploy/harden-vps-firewall.sh tests/ibkr_bot/test_deploy_files.py
git commit -m "Ajoute le service systemd et les scripts de deploiement de l'API Bot Actions"
```

---

## Task 4 : Frontend `docs/index.html` — bloc tableau de bord protégé

**Files:**
- Modify: `docs/index.html`
- Modify: `docs/service-worker.js`

**Interfaces:**
- Consumes : endpoint `GET https://goldbot.fr:8444/dashboard` (Task 2, réponse `{balance, balance_fetched_at, positions: [{ticker,name,index,quantite,prix_entree,date_entree,target_exit_price}], actions: [{ticker,sens,quantite,prix_execution,statut,date,...}]}`), fonctions déjà existantes `ibkrBotPositionsHtml(positions)`, `formatFetchedAgo(iso)`, `formatPrice(v)`, `formatPriceForIndex(v, indexKey)`, `escHtml(s)`, variable globale `indicesData`.
- Produces : `IBKR_BOT_API_BASE_URL`, `getIbkrBotToken()`, `fetchIbkrBotDashboard()`, `renderIbkrBotDashboardBlock()`, `ibkrBotBalanceHtml(data)`, `ibkrBotActionsHtml(actions)`, `ibkrBotUnlockHtml()`.

**Décision de conception (comble une zone non précisée par la spec §7) :** contrairement au Bot Or, où `fetchBotDashboard()` appelle `getBotToken()` — donc ouvre un `prompt()` — dès l'affichage de la section, ce plan ne force JAMAIS de `prompt()` à l'ouverture du panneau Bot Actions. Raison : le panneau Bot Actions a toujours eu un contenu public utile sans jeton (`docs/ibkr_bot_status.json`), contrairement au Bot Or qui n'a jamais eu de mode non protégé — forcer un `prompt()` systématique casserait ce fonctionnement existant pour tout visiteur qui n'a pas de jeton. Le tableau de bord protégé devient donc une amélioration optionnelle : silencieuse si un jeton est déjà mémorisé en `localStorage`, déclenchée par un clic sur un bouton "Voir solde, positions et historique complets" sinon. Ceci concrétise directement l'exigence de repli de la spec §7 ("si le jeton est absent/invalide, retomber sur l'affichage public existant plutôt que de bloquer tout le panneau").

- [ ] **Step 1 : Ajouter la constante d'URL de l'API**

Dans `docs/index.html`, trouve la ligne `const BOT_API_BASE_URL = 'https://goldbot.fr:8443';` (aux alentours de la ligne 2966) et ajoute juste après :

```js
const BOT_API_BASE_URL = 'https://goldbot.fr:8443';
const IBKR_BOT_API_BASE_URL = 'https://goldbot.fr:8444';
```

- [ ] **Step 2 : Ajouter la variable d'état du tableau de bord**

Trouve `let ibkrBotStatusData = null;` (aux alentours de la ligne 2113) et ajoute juste après :

```js
let ibkrBotStatusData = null;
let ibkrBotDashboardData = null;
```

- [ ] **Step 3 : Ajouter le conteneur du bloc protégé dans la coquille**

Remplace le corps de `ibkrBotDashboardSectionHtml()` (aux alentours de la ligne 2115) :

```js
function ibkrBotDashboardSectionHtml() {
  return `
    <main id="ibkrBotContent">
      <div class="skeleton">
        <div class="skeleton-line"></div>
        <div class="skeleton-line"></div>
        <div class="skeleton-line"></div>
      </div>
    </main>
    <div id="ibkrBotDashboardBlock"></div>`;
}
```

- [ ] **Step 4 : Masquer les positions publiques en double quand le tableau de bord protégé est actif**

Dans `renderIbkrBotStatus()` (aux alentours de la ligne 2218), remplace :

```js
    ${s.positions ? `<h2>Positions ouvertes</h2>${ibkrBotPositionsHtml(s.positions)}` : ''}
```

par :

```js
    ${s.positions && !ibkrBotDashboardData ? `<h2>Positions ouvertes</h2>${ibkrBotPositionsHtml(s.positions)}` : ''}
```

- [ ] **Step 5 : Remplacer `wireIbkrBotDashboardSection` et ajouter les nouvelles fonctions**

Remplace entièrement (aux alentours des lignes 2233-2235) :

```js
function wireIbkrBotDashboardSection() {
  loadIbkrBotStatus();
}
```

par :

```js
function getIbkrBotToken() {
  let token = localStorage.getItem('ibkrBotToken');
  if (!token) {
    token = prompt('Jeton d\'accès au tableau de bord Bot Actions (demandé une seule fois, conservé sur cet appareil) :');
    if (token) localStorage.setItem('ibkrBotToken', token);
  }
  return token;
}

function ibkrBotBalanceHtml(data) {
  if (!data || data.balance == null) {
    return `<div class="empty">Solde indisponible pour l'instant.</div>`;
  }
  const freshness = formatFetchedAgo(data.balance_fetched_at);
  return `
    <div class="portfolio-position-row">
      <div class="portfolio-position-main">
        <span class="company-name">${formatPrice(data.balance)}</span>
        <span class="hero-sub">Solde disponible</span>
        ${freshness ? `<span class="hero-sub">${freshness}</span>` : ''}
      </div>
    </div>`;
}

// Ticker -> entreprise resolu ici, cote frontend, plutot que duplique par
// l'API (voir spec 4.4) : indicesData est deja charge pour toute la page.
function ibkrBotActionsHtml(actions) {
  if (!actions || actions.length === 0) {
    return `<div class="empty">Aucune action récente.</div>`;
  }
  const companiesByTicker = {};
  (indicesData.companies || []).forEach(c => { companiesByTicker[c.ticker] = c; });
  return actions.slice().reverse().map(a => {
    const label = a.sens === 'BUY' ? 'Achat' : a.sens === 'SELL' ? 'Vente' : '—';
    const color = a.sens === 'BUY' ? 'var(--gold)' : a.sens === 'SELL' ? 'var(--rust)' : 'var(--muted)';
    const company = companiesByTicker[a.ticker];
    const name = company ? company.name : a.ticker;
    const price = a.prix_execution != null
      ? (company ? formatPriceForIndex(a.prix_execution, company.index) : formatPrice(a.prix_execution))
      : '—';
    return `
    <div class="portfolio-position-row">
      <div class="portfolio-position-main">
        <a class="company-name" href="#indices/${encodeURIComponent(a.ticker)}" style="color:${color}">${label}</a>
        <span class="hero-sub">${escHtml(name)}${a.quantite != null ? ` · ${a.quantite} titre${a.quantite > 1 ? 's' : ''}` : ''}${a.date ? ` · ${a.date}` : ''}</span>
      </div>
      <div class="portfolio-position-pnl">
        <span class="price-value">${price}</span>
      </div>
    </div>`;
  }).join('');
}

function ibkrBotUnlockHtml() {
  return `<button class="toggle-btn" type="button" id="ibkrBotUnlockBtn">Voir solde, positions et historique complets (jeton requis)</button>`;
}

function renderIbkrBotDashboardBlock() {
  const el = document.getElementById('ibkrBotDashboardBlock');
  if (!el) return;
  if (!ibkrBotDashboardData) {
    el.innerHTML = ibkrBotUnlockHtml();
    const unlockBtn = document.getElementById('ibkrBotUnlockBtn');
    if (unlockBtn) unlockBtn.addEventListener('click', () => {
      const token = getIbkrBotToken();
      if (token) fetchIbkrBotDashboard();
    });
    return;
  }
  el.innerHTML = `
    <h2>Solde</h2>
    ${ibkrBotBalanceHtml(ibkrBotDashboardData)}
    <h2>Positions ouvertes</h2>
    ${ibkrBotPositionsHtml(ibkrBotDashboardData.positions)}
    <h2>Actions récentes</h2>
    ${ibkrBotActionsHtml(ibkrBotDashboardData.actions)}
    <button class="toggle-btn" type="button" id="ibkrBotDashboardRefreshBtn">Actualiser</button>
  `;
  const refreshBtn = document.getElementById('ibkrBotDashboardRefreshBtn');
  if (refreshBtn) refreshBtn.addEventListener('click', () => fetchIbkrBotDashboard());
}

// Jamais de prompt() force a l'ouverture du panneau (contrairement au Bot
// Or) : le statut public reste utile sans jeton (spec 7, dernier point),
// donc le tableau de bord protege est une amelioration optionnelle,
// declenchee par clic sur ibkrBotUnlockBtn ou silencieusement si un
// jeton est deja memorise.
async function fetchIbkrBotDashboard() {
  const token = localStorage.getItem('ibkrBotToken');
  if (!token) {
    ibkrBotDashboardData = null;
    renderIbkrBotDashboardBlock();
    return;
  }
  try {
    const res = await fetch(`${IBKR_BOT_API_BASE_URL}/dashboard`, { headers: { 'X-Bot-Token': token } });
    if (!res.ok) {
      if (res.status === 401) localStorage.removeItem('ibkrBotToken');
      throw new Error('échec de la requête (' + res.status + ')');
    }
    ibkrBotDashboardData = await res.json();
  } catch (e) {
    ibkrBotDashboardData = null;
  }
  if (!document.getElementById('ibkrBotDashboardBlock')) return;
  if (ibkrBotStatusData) renderIbkrBotStatus();
  renderIbkrBotDashboardBlock();
}

function wireIbkrBotDashboardSection() {
  loadIbkrBotStatus();
  fetchIbkrBotDashboard();
}
```

- [ ] **Step 6 : Bumper le cache du service worker**

Dans `docs/service-worker.js`, trouve `const CACHE_NAME = "analyse-or-shell-v84";` et remplace par :

```js
const CACHE_NAME = "analyse-or-shell-v85";
```

(Si un autre commit a déjà avancé ce numéro entre-temps, prends le numéro suivant celui alors en place — la règle est "toujours +1 sur tout commit touchant `docs/index.html`", pas un numéro figé.)

- [ ] **Step 7 : Vérification par un vrai navigateur (Playwright)**

Il n'y a pas de runner JS dans ce dépôt — ce script, à exécuter depuis le scratchpad (pas committé), sert `docs/` localement et pilote un vrai Chromium pour vérifier le flux complet : bouton de déblocage visible sans jeton, `prompt()` intercepté, requête vers l'API interceptée et remplacée par une réponse fixe, rendu final contient le solde, le lien vers la fiche entreprise et l'action récente.

```python
import json
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

DOCS = Path(r"C:\Users\alexa\OneDrive\Documents\GitHub\analyse-or\docs")
PORT = 8199

FAKE_STATUS = {
    "timestamp": "2026-09-20T14:45:03Z", "date": "2026-09-20", "mode": "reel",
    "statut": "termine", "git_pull_ok": True, "preflight_ok": True,
    "entrees_count": 1, "sorties_count": 0, "signaux_rejetes_count": 2,
    "anomalies_count": 0, "erreurs_count": 0,
}
(DOCS / "ibkr_bot_status.json").write_text(json.dumps(FAKE_STATUS), encoding="utf-8")

FAKE_DASHBOARD = {
    "balance": 8123.45, "balance_fetched_at": "2026-09-20T14:45:03Z",
    "positions": [{
        "ticker": "MC.PA", "name": "LVMH", "index": "CAC40", "quantite": 5,
        "prix_entree": 90.5, "date_entree": "2026-09-15", "target_exit_price": 120.0,
    }],
    "actions": [{
        "ticker": "MC.PA", "sens": "BUY", "quantite": 5, "prix_execution": 90.5,
        "statut": "execute", "date": "2026-09-18",
    }],
}

server = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT)], cwd=str(DOCS))
time.sleep(1)

try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.on("dialog", lambda d: d.accept("test-token"))
        page.route("https://goldbot.fr:8444/dashboard", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(FAKE_DASHBOARD)))

        page.goto(f"http://localhost:{PORT}/index.html#portefeuille")
        page.wait_for_function("typeof portfolioActionsSections !== 'undefined' && portfolioActionsSections.length === 5")
        page.evaluate("portfolioActionsSectionIndex = 4; renderPager('pfActions', portfolioActionsSections, 4);")
        page.wait_for_timeout(800)

        assert page.locator("#ibkrBotUnlockBtn").count() == 1, "bouton de deblocage absent avant tout jeton"

        page.click("#ibkrBotUnlockBtn")
        page.wait_for_timeout(800)

        content = page.locator("#ibkrBotDashboardBlock").inner_html()
        assert "€" in content and "8" in content, "solde absent du rendu"
        assert "#indices/MC.PA" in content, "lien vers la fiche entreprise absent"
        assert "Achat" in content, "action recente absente"
        assert "LVMH" in content, "nom d'entreprise (positions) absent"
        print("OK - tableau de bord Bot Actions rendu avec succes")
        browser.close()
finally:
    server.terminate()
    server.wait()
    (DOCS / "ibkr_bot_status.json").unlink(missing_ok=True)
```

Run ce script (par exemple `python scratch_verify_ibkr_dashboard.py` depuis le dossier scratchpad). Expected: `OK - tableau de bord Bot Actions rendu avec succes` imprimé, aucune assertion levée.

- [ ] **Step 8 : Commit**

```bash
git add docs/index.html docs/service-worker.js
git commit -m "Ajoute le tableau de bord protege par jeton au panneau Bot Actions"
```

---

## Étapes manuelles de déploiement (NE PAS déléguer à un subagent)

Ces étapes touchent le VPS de production et un secret réel — elles relèvent des quatre exceptions qui arrêtent l'exécution automatisée (action de sécurité, effet de bord hors de ce dépôt). Une fois les 4 tâches ci-dessus mergées et la revue finale de branche passée, la session contrôleuse (pas un subagent) les exécute avec l'utilisateur, typiquement via SSH comme pour les déploiements précédents de cette session :

1. `git pull` sur `/home/ibkrbot/analyse-or` (le VPS a déjà ce dépôt cloné, voir `deploy/setup-user-repo.sh`/`deploy/update-repo.sh`).
2. Générer un jeton aléatoire dédié (ex. `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`), l'ajouter comme `IBKR_BOT_API_TOKEN=...` dans `/home/ibkrbot/analyse-or/.env` (`chmod 600`, jamais commité) — **différent du `BOT_API_TOKEN` de gold_bot**.
3. Exécuter `deploy/setup-tls-ibkr.sh` en root (ajoute `ibkrbot` au groupe `ssl-cert`, installe le hook de renouvellement).
4. Exécuter `ufw allow 8444` (ou relancer `deploy/harden-vps-firewall.sh`).
5. Copier `deploy/ibkr-bot-api.service` vers `/etc/systemd/system/`, puis `systemctl daemon-reload && systemctl enable --now ibkr-bot-api`.
6. Vérifier : `curl -s -o /dev/null -w "%{http_code}" https://goldbot.fr:8444/dashboard` doit renvoyer `401` (pas de jeton), puis `curl -H "X-Bot-Token: <jeton>" https://goldbot.fr:8444/dashboard` doit renvoyer `200` avec un JSON valide.
7. Communiquer le nouveau jeton à l'utilisateur pour qu'il le saisisse une fois dans le panneau Bot Actions du site (bouton "Voir solde, positions et historique complets").
