# Assistant de recherche IA (écran d'accueil) — Plan d'implémentation

> **Pour les exécutants :** SOUS-COMPÉTENCE REQUISE : utiliser
> superpowers:subagent-driven-development (recommandé) ou
> superpowers:executing-plans pour implémenter ce plan tâche par tâche.

**Goal:** barre de recherche sur l'écran d'accueil capable de répondre en
langage naturel à des questions sur les données du site (scores
d'entreprises, statut des bots, portefeuille), avec navigation instantanée
pour les requêtes simples et conversation argumentée (Claude Opus 5.5,
boucle d'outils) pour le reste.

**Architecture:** nouveau service VPS en lecture seule (`assistant_ia/`,
FastAPI, port 8445, protégé par jeton) qui lit les fichiers publiés du
site et les API Bot Or/Bot Actions existantes, orchestre une boucle
d'outils avec Claude, et streame la réponse. Le frontend ajoute un filtre
instantané côté client (aucun réseau) et, en repli, un panneau de
conversation qui consomme ce service en streaming.

**Tech Stack:** Python 3.14, FastAPI, SDK `anthropic` (Python), pytest,
JS vanilla (pas de framework).

**Spec:** `docs/superpowers/specs/2026-10-03-ai-search-assistant-design.md`

## Global Constraints

- Modèle : `claude-opus-5-5` (décision explicite de l'utilisateur, voir
  spec §12).
- Convention de prix (en dur, pas d'appel réseau pour les connaître) :
  entrée 4,00 $/MTok, sortie 20,00 $/MTok pour `claude-opus-5-5`.
- Plafond quotidien de dépense par défaut : **5,00 $/jour**, réinitialisé
  à minuit UTC.
- Plafond de boucle d'outils : **6 itérations**.
- Source de vérité des données du site : les fichiers **publiés**
  (`https://alexandreauq.github.io/analyse-or/indices.json`), jamais un
  checkout VPS local (spec §3).
- Statuts/positions des bots : toujours via LEURS API existantes
  (`/dashboard` de `gold_bot`/`ibkr_bot`), jamais une deuxième lecture
  directe de leurs fichiers (spec §3).
- Ce service est en lecture seule : aucune route de mutation, jamais
  d'écriture dans `positions.json`/`state.json`/aucun fichier d'un autre
  module.
- Jeton dédié `AI_ASSISTANT_API_TOKEN`, jamais réutilisé d'un autre
  service — même garde-fou à échec fermé et comparaison à temps constant
  que `gold_bot/api.py::_check_token`/`ibkr_bot/api.py::_check_token`.
- `python -m pytest tests/assistant_ia/ -q` doit rester à 0 échec après
  chaque tâche.

---

### Task 1: Journal de coût quotidien (`assistant_ia/usage.py`)

**Files:**
- Create: `assistant_ia/usage.py`
- Test: `tests/assistant_ia/test_usage.py`
- Create (vide, pour que le package soit importable) : `assistant_ia/__init__.py`
- Create (vide) : `tests/assistant_ia/__init__.py`

**Interfaces:**
- Consomme : rien (premier module, aucune dépendance interne).
- Produit : `record_usage(input_tokens: int, output_tokens: int, model: str = MODEL_OPUS_5_5, path: str = USAGE_PATH) -> float` (renvoie le total du jour en dollars, après ajout) ; `budget_exceeded(path: str = USAGE_PATH, daily_budget_usd: float = DAILY_BUDGET_USD) -> bool`. Les tâches 4 et 5 appellent ces deux fonctions avec leur signature exacte.

- [ ] **Step 1: Écrire le test qui échoue pour `record_usage`**

Créer `tests/assistant_ia/__init__.py` (fichier vide) puis
`tests/assistant_ia/test_usage.py` :

```python
# tests/assistant_ia/test_usage.py
import json

import assistant_ia.usage as usage


def test_record_usage_computes_cost_from_token_counts(tmp_path):
    path = str(tmp_path / "usage_today.json")

    total = usage.record_usage(1_000_000, 0, model="claude-opus-5-5", path=path)

    assert total == 4.0  # 1M tokens d'entree a 4.00 $/MTok


def test_record_usage_accumulates_across_calls(tmp_path):
    path = str(tmp_path / "usage_today.json")

    usage.record_usage(1_000_000, 0, model="claude-opus-5-5", path=path)
    total = usage.record_usage(0, 500_000, model="claude-opus-5-5", path=path)

    assert total == 4.0 + 10.0  # +500k tokens de sortie a 20.00 $/MTok


def test_record_usage_resets_on_a_new_utc_day(tmp_path, monkeypatch):
    path = str(tmp_path / "usage_today.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"date": "2020-01-01", "total_usd": 999.0}, fh)

    total = usage.record_usage(1_000_000, 0, model="claude-opus-5-5", path=path)

    assert total == 4.0  # l'ancien total d'un jour different est ignore


def test_budget_exceeded_is_false_under_the_cap(tmp_path):
    path = str(tmp_path / "usage_today.json")
    usage.record_usage(100_000, 0, model="claude-opus-5-5", path=path)  # 0.40 $

    assert usage.budget_exceeded(path=path, daily_budget_usd=5.0) is False


def test_budget_exceeded_is_true_at_or_above_the_cap(tmp_path):
    path = str(tmp_path / "usage_today.json")
    usage.record_usage(1_500_000, 0, model="claude-opus-5-5", path=path)  # 6.00 $

    assert usage.budget_exceeded(path=path, daily_budget_usd=5.0) is True


def test_budget_exceeded_is_false_when_the_usage_file_is_absent(tmp_path):
    path = str(tmp_path / "usage_today.json")

    assert usage.budget_exceeded(path=path, daily_budget_usd=5.0) is False


def test_load_usage_degrades_to_zero_on_corrupt_file(tmp_path):
    path = str(tmp_path / "usage_today.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not json")

    data = usage.load_usage(path=path)

    assert data["total_usd"] == 0.0
```

- [ ] **Step 2: Lancer les tests, vérifier qu'ils échouent**

Run: `python -m pytest tests/assistant_ia/test_usage.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'assistant_ia'`

- [ ] **Step 3: Implémenter `assistant_ia/usage.py`**

Créer `assistant_ia/__init__.py` (fichier vide), puis
`assistant_ia/usage.py` :

```python
# assistant_ia/usage.py
# Plafond de depense quotidien de l'assistant IA : un garde-fou, pas une
# limite d'usage normal (voir spec §8) — protege contre un bug cote
# client qui boucle les appels, jamais contre un usage legitime. Les
# prix sont en dur (pas d'appel reseau pour les connaitre, ils bougent
# rarement et une panne de lookup ne doit jamais empecher de savoir ce
# qu'on a deja depense aujourd'hui).
import json
import os
from datetime import datetime, timezone

MODEL_OPUS_5_5 = "claude-opus-5-5"
DAILY_BUDGET_USD = 5.0
PRICE_PER_MTOK_USD = {
    MODEL_OPUS_5_5: {"input": 4.0, "output": 20.0},
}
USAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "usage_today.json")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_usage(path: str = USAGE_PATH) -> dict:
    """Dernier total de depense connu. Fichier absent/corrompu -> total a
    zero pour aujourd'hui, jamais d'exception (meme contrat que
    ibkr_bot.journal.load_account_snapshot)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {"date": _today(), "total_usd": 0.0}
    if not isinstance(data, dict) or data.get("date") != _today():
        return {"date": _today(), "total_usd": 0.0}
    total = data.get("total_usd")
    if not isinstance(total, (int, float)) or isinstance(total, bool):
        return {"date": _today(), "total_usd": 0.0}
    return {"date": _today(), "total_usd": float(total)}


def _save_usage(data: dict, path: str = USAGE_PATH) -> None:
    """Ecriture atomique, degrade silencieusement sur echec (meme
    philosophie que journal.append_run : une panne disque sur ce journal
    ne doit jamais faire planter une requete reelle). Un echec d'ecriture
    signifie seulement que le total pourrait etre legerement sous-estime
    au prochain redemarrage, jamais une raison de bloquer la requete en
    cours."""
    try:
        dirname = os.path.dirname(path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception as e:
        print(f"Erreur ecriture journal d'usage IA : {e}")


def record_usage(
    input_tokens: int, output_tokens: int, model: str = MODEL_OPUS_5_5,
    path: str = USAGE_PATH,
) -> float:
    """Ajoute le cout d'un appel au total du jour (reinitialise
    automatiquement si le fichier date d'un autre jour UTC) et renvoie
    le nouveau total en dollars."""
    prices = PRICE_PER_MTOK_USD[model]
    cout = (input_tokens / 1_000_000) * prices["input"] + (output_tokens / 1_000_000) * prices["output"]
    current = load_usage(path)
    nouveau_total = current["total_usd"] + cout
    _save_usage({"date": _today(), "total_usd": nouveau_total}, path)
    return nouveau_total


def budget_exceeded(path: str = USAGE_PATH, daily_budget_usd: float = DAILY_BUDGET_USD) -> bool:
    """True si le total du jour atteint ou depasse le plafond. Fichier
    absent -> False (aucune depense connue aujourd'hui)."""
    return load_usage(path)["total_usd"] >= daily_budget_usd
```

- [ ] **Step 4: Lancer les tests, vérifier qu'ils passent**

Run: `python -m pytest tests/assistant_ia/test_usage.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add assistant_ia/__init__.py assistant_ia/usage.py tests/assistant_ia/__init__.py tests/assistant_ia/test_usage.py
git commit -m "feat(assistant_ia): journal de cout quotidien + plafond"
```

---

### Task 2: Sources de données (`assistant_ia/data_sources.py`)

**Files:**
- Create: `assistant_ia/data_sources.py`
- Test: `tests/assistant_ia/test_data_sources.py`

**Interfaces:**
- Consomme : rien de la Task 1.
- Produit : `fetch_indices_data(http_get=requests.get, now_fn=time.time, cache: dict | None = None) -> dict` (renvoie le contenu de `indices.json`, mis en cache 10 min) ; `fetch_bot_dashboard(nom: str, http_get=requests.get) -> dict` (`nom` dans `{"or", "actions"}`, renvoie le JSON de `/dashboard` du bot concerné, ou `{"erreur": "..."}` en cas d'echec — jamais d'exception non capturee). La Task 3 appelle ces deux fonctions avec ces signatures exactes.

- [ ] **Step 1: Écrire le test qui échoue**

```python
# tests/assistant_ia/test_data_sources.py
import pytest

import assistant_ia.data_sources as data_sources


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


def test_fetch_indices_data_calls_the_public_url():
    appels = []

    def fake_get(url, timeout=None):
        appels.append(url)
        return _FakeResponse(json_data={"companies": []})

    data_sources.fetch_indices_data(http_get=fake_get, cache={})

    assert appels == ["https://alexandreauq.github.io/analyse-or/indices.json"]


def test_fetch_indices_data_reuses_a_fresh_cache_entry():
    appels = []

    def fake_get(url, timeout=None):
        appels.append(url)
        return _FakeResponse(json_data={"companies": ["premier_appel"]})

    cache = {}
    horloge = {"t": 1000.0}
    premier = data_sources.fetch_indices_data(http_get=fake_get, now_fn=lambda: horloge["t"], cache=cache)
    horloge["t"] += 60.0  # bien avant les 600s de TTL
    second = data_sources.fetch_indices_data(http_get=fake_get, now_fn=lambda: horloge["t"], cache=cache)

    assert len(appels) == 1  # un seul appel reseau, le second est servi par le cache
    assert premier == second == {"companies": ["premier_appel"]}


def test_fetch_indices_data_refetches_after_the_cache_ttl_expires():
    appels = []

    def fake_get(url, timeout=None):
        appels.append(url)
        return _FakeResponse(json_data={"companies": [f"appel_{len(appels)}"]})

    cache = {}
    horloge = {"t": 1000.0}
    data_sources.fetch_indices_data(http_get=fake_get, now_fn=lambda: horloge["t"], cache=cache)
    horloge["t"] += 700.0  # au-dela des 600s de TTL
    data_sources.fetch_indices_data(http_get=fake_get, now_fn=lambda: horloge["t"], cache=cache)

    assert len(appels) == 2


def test_fetch_bot_dashboard_or_uses_the_gold_bot_url_and_token(monkeypatch):
    monkeypatch.setenv("BOT_API_TOKEN", "secret-or")
    appels = []

    def fake_get(url, headers=None, timeout=None):
        appels.append((url, headers))
        return _FakeResponse(json_data={"balance": 981.45})

    resultat = data_sources.fetch_bot_dashboard("or", http_get=fake_get)

    assert appels == [("https://goldbot.fr:8443/dashboard", {"X-Bot-Token": "secret-or"})]
    assert resultat == {"balance": 981.45}


def test_fetch_bot_dashboard_actions_uses_the_ibkr_bot_url_and_token(monkeypatch):
    monkeypatch.setenv("IBKR_BOT_API_TOKEN", "secret-actions")
    appels = []

    def fake_get(url, headers=None, timeout=None):
        appels.append((url, headers))
        return _FakeResponse(json_data={"balance": 20.0})

    data_sources.fetch_bot_dashboard("actions", http_get=fake_get)

    assert appels == [("https://goldbot.fr:8444/dashboard", {"X-Bot-Token": "secret-actions"})]


def test_fetch_bot_dashboard_degrades_on_network_failure(monkeypatch):
    monkeypatch.setenv("BOT_API_TOKEN", "secret-or")

    def fake_get(url, headers=None, timeout=None):
        raise RuntimeError("timeout reseau")

    resultat = data_sources.fetch_bot_dashboard("or", http_get=fake_get)

    assert "erreur" in resultat


def test_fetch_bot_dashboard_rejects_an_unknown_bot_name():
    with pytest.raises(ValueError):
        data_sources.fetch_bot_dashboard("inconnu", http_get=lambda *a, **k: None)
```

- [ ] **Step 2: Lancer les tests, vérifier qu'ils échouent**

Run: `python -m pytest tests/assistant_ia/test_data_sources.py -v`
Expected: FAIL avec `AttributeError: module 'assistant_ia.data_sources' has no attribute ...` (le module n'existe pas encore)

- [ ] **Step 3: Implémenter `assistant_ia/data_sources.py`**

```python
# assistant_ia/data_sources.py
# Sources de donnees de l'assistant IA : TOUJOURS les fichiers reellement
# PUBLIES du site (jamais un checkout VPS local, qui pourrait etre sur
# un autre commit que ce qui est en ligne — voir spec §3), et TOUJOURS
# les API protegees existantes de gold_bot/ibkr_bot pour leurs statuts/
# positions (jamais une deuxieme lecture directe de leurs fichiers, pour
# ne jamais faire diverger deux chemins de lecture de la meme donnee).
import time

import requests

SITE_BASE_URL = "https://alexandreauq.github.io/analyse-or/"
INDICES_JSON_URL = SITE_BASE_URL + "indices.json"
CACHE_TTL_SECONDS = 600
REQUEST_TIMEOUT_SECONDS = 15

BOT_DASHBOARDS = {
    "or": {"url": "https://goldbot.fr:8443/dashboard", "token_env": "BOT_API_TOKEN"},
    "actions": {"url": "https://goldbot.fr:8444/dashboard", "token_env": "IBKR_BOT_API_TOKEN"},
}

# Cache module-level par defaut : partage entre toutes les requetes de ce
# processus (le service tourne en un seul process uvicorn), jamais
# recree par appel. Les tests passent leur propre `cache={}` pour rester
# isoles les uns des autres.
_default_cache: dict = {}


def fetch_indices_data(
    http_get=requests.get, now_fn=time.time, cache: dict | None = None,
) -> dict:
    """Contenu de indices.json tel que PUBLIE (pas un checkout local),
    mis en cache en memoire 10 minutes — la donnee ne change qu'une fois
    par jour (run quotidien du workflow indices.yml), un cache court
    evite juste de re-fetcher a chaque question sans jamais servir une
    donnee vraiment perimee."""
    if cache is None:
        cache = _default_cache
    maintenant = now_fn()
    entree = cache.get("indices")
    if entree is not None and (maintenant - entree[0]) < CACHE_TTL_SECONDS:
        return entree[1]
    reponse = http_get(INDICES_JSON_URL, timeout=REQUEST_TIMEOUT_SECONDS)
    reponse.raise_for_status()
    donnees = reponse.json()
    cache["indices"] = (maintenant, donnees)
    return donnees


def fetch_bot_dashboard(nom: str, http_get=requests.get) -> dict:
    """Appelle /dashboard du bot Or ("or") ou Bot Actions ("actions")
    avec son jeton dedie (configure dans le .env de CE service, copie du
    jeton que le bot cible valide lui-meme). Degrade vers
    {"erreur": "..."} sur toute panne reseau/HTTP plutot que de lever —
    c'est a l'outil appelant (Task 3) de decider quoi en dire a Claude."""
    import os

    if nom not in BOT_DASHBOARDS:
        raise ValueError(f"nom de bot inconnu : {nom!r} (attendu : 'or' ou 'actions')")
    config = BOT_DASHBOARDS[nom]
    token = os.environ.get(config["token_env"], "")
    try:
        reponse = http_get(
            config["url"], headers={"X-Bot-Token": token}, timeout=REQUEST_TIMEOUT_SECONDS)
        reponse.raise_for_status()
        return reponse.json()
    except Exception as e:
        return {"erreur": f"impossible de contacter le bot {nom} : {e}"}
```

- [ ] **Step 4: Lancer les tests, vérifier qu'ils passent**

Run: `python -m pytest tests/assistant_ia/test_data_sources.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add assistant_ia/data_sources.py tests/assistant_ia/test_data_sources.py
git commit -m "feat(assistant_ia): sources de donnees publiques + dashboards bots"
```

---

### Task 3: Outils (`assistant_ia/tools.py`)

**Files:**
- Create: `assistant_ia/tools.py`
- Test: `tests/assistant_ia/test_tools.py`

**Interfaces:**
- Consomme : `data_sources.fetch_indices_data(...)`, `data_sources.fetch_bot_dashboard(nom, ...)` (Task 2, signatures exactes ci-dessus).
- Produit : `TOOL_DEFINITIONS: list[dict]` (schema Claude des 7 outils) ; `dispatch_tool(name: str, tool_input: dict, *, indices_source=data_sources.fetch_indices_data, dashboard_source=data_sources.fetch_bot_dashboard) -> dict` (renvoie TOUJOURS un dict JSON-serialisable, jamais une exception — un nom d'outil inconnu renvoie `{"erreur": "outil inconnu : ..."}`). La Task 4 appelle exclusivement `dispatch_tool` et lit `TOOL_DEFINITIONS`.

- [ ] **Step 1: Écrire le test qui échoue**

```python
# tests/assistant_ia/test_tools.py
import assistant_ia.tools as tools

INDICES_FIXTURE = {
    "companies": [
        {"ticker": "MC.PA", "name": "LVMH", "index": "CAC40", "sector": "Consumer Cyclical",
         "score": 72.0, "interpretation": "Tres solide", "stage_label": "Achat",
         "factors": {"roce": 18.0, "dette_nette_ebitda": 1.2}, "alerts": []},
        {"ticker": "KER.PA", "name": "Kering", "index": "CAC40", "sector": "Consumer Cyclical",
         "score": 45.0, "interpretation": "Correct", "stage_label": "Neutre",
         "factors": {"roce": 9.0, "dette_nette_ebitda": 3.1}, "alerts": []},
        {"ticker": "SAN.PA", "name": "Sanofi", "index": "CAC40", "sector": "Healthcare",
         "score": 30.0, "interpretation": "Moyen", "stage_label": "Neutre",
         "factors": {}, "alerts": []},
    ]
}


def _indices_source(**kwargs):
    return INDICES_FIXTURE


def test_fiche_entreprise_returns_the_matching_company():
    resultat = tools.dispatch_tool(
        "fiche_entreprise", {"ticker": "MC.PA"}, indices_source=_indices_source)

    assert resultat["ticker"] == "MC.PA"
    assert resultat["score"] == 72.0
    assert resultat["factors"]["roce"] == 18.0


def test_fiche_entreprise_suggests_close_matches_when_not_found():
    resultat = tools.dispatch_tool(
        "fiche_entreprise", {"ticker": "LVMHH"}, indices_source=_indices_source)

    assert "erreur" in resultat
    assert "MC.PA" in resultat["suggestions"]


def test_comparer_entreprises_returns_both_fiches():
    resultat = tools.dispatch_tool(
        "comparer_entreprises", {"ticker1": "MC.PA", "ticker2": "KER.PA"},
        indices_source=_indices_source)

    assert resultat["entreprise_1"]["ticker"] == "MC.PA"
    assert resultat["entreprise_2"]["ticker"] == "KER.PA"


def test_classement_filters_by_index_and_sorts_by_score_desc():
    resultat = tools.dispatch_tool(
        "classement", {"indice": "CAC40", "n": 2}, indices_source=_indices_source)

    tickers = [c["ticker"] for c in resultat["classement"]]
    assert tickers == ["MC.PA", "KER.PA"]  # 72 puis 45, SAN.PA (30) exclu par n=2


def test_classement_filters_by_sector():
    resultat = tools.dispatch_tool(
        "classement", {"secteur": "Healthcare", "n": 10}, indices_source=_indices_source)

    assert [c["ticker"] for c in resultat["classement"]] == ["SAN.PA"]


def test_statut_bot_dispatches_to_the_dashboard_source():
    appels = []

    def fake_dashboard(nom):
        appels.append(nom)
        return {"balance": 981.45, "balance_fetched_at": "2026-10-03T10:00:00Z"}

    resultat = tools.dispatch_tool("statut_bot", {"nom": "or"}, dashboard_source=fake_dashboard)

    assert appels == ["or"]
    assert resultat["balance"] == 981.45


def test_positions_bot_returns_only_the_positions_list():
    def fake_dashboard(nom):
        return {"balance": 20.0, "positions": [{"ticker": "MC.PA", "pnl_eur": -16.57}]}

    resultat = tools.dispatch_tool("positions_bot", {"nom": "actions"}, dashboard_source=fake_dashboard)

    assert resultat["positions"] == [{"ticker": "MC.PA", "pnl_eur": -16.57}]
    assert "balance" not in resultat


def test_resume_portefeuille_merges_manual_and_bot_positions():
    def fake_dashboard(nom):
        return {"positions": [{"ticker": f"BOT_{nom.upper()}"}]}

    resultat = tools.dispatch_tool(
        "resume_portefeuille",
        {"positions_manuelles": [{"ticker": "MANUELLE.PA", "quantity": 5}]},
        dashboard_source=fake_dashboard)

    tickers = {p["ticker"] for p in resultat["positions"]}
    assert tickers == {"MANUELLE.PA", "BOT_OR", "BOT_ACTIONS"}
    assert resultat["nombre_total"] == 3


def test_proposer_lien_echoes_the_suggestion():
    resultat = tools.dispatch_tool(
        "proposer_lien", {"cible_type": "ticker", "cible_valeur": "MC.PA", "libelle": "Voir LVMH"})

    assert resultat == {
        "cible_type": "ticker", "cible_valeur": "MC.PA", "libelle": "Voir LVMH",
    }


def test_dispatch_tool_returns_an_error_dict_for_an_unknown_tool():
    resultat = tools.dispatch_tool("outil_inexistant", {})

    assert "erreur" in resultat


def test_tool_definitions_lists_all_seven_tools():
    noms = {t["name"] for t in tools.TOOL_DEFINITIONS}

    assert noms == {
        "fiche_entreprise", "comparer_entreprises", "classement",
        "statut_bot", "positions_bot", "resume_portefeuille", "proposer_lien",
    }
```

- [ ] **Step 2: Lancer les tests, vérifier qu'ils échouent**

Run: `python -m pytest tests/assistant_ia/test_tools.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implémenter `assistant_ia/tools.py`**

```python
# assistant_ia/tools.py
# Les 7 outils que Claude peut appeler (voir spec §6). Chaque outil est
# une fonction pure (entree -> dict JSON-serialisable), testable sans
# toucher a l'API Anthropic. dispatch_tool() ne leve JAMAIS — un outil
# inconnu ou une erreur interne renvoie un dict {"erreur": ...}, que la
# boucle d'orchestration (Task 4) renvoie a Claude comme resultat
# d'outil en erreur plutot que de casser tout l'echange.
from difflib import get_close_matches

import assistant_ia.data_sources as data_sources

TOOL_DEFINITIONS = [
    {
        "name": "fiche_entreprise",
        "description": (
            "Renvoie la fiche complete d'une entreprise du site (score, "
            "facteurs detailles, interpretation, stage, alertes) a partir "
            "de son ticker exact. Si le ticker n'existe pas, renvoie des "
            "suggestions de tickers proches a presenter a l'utilisateur "
            "via proposer_lien plutot que de deviner."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string", "description": "Ticker exact, ex. MC.PA"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "comparer_entreprises",
        "description": "Renvoie les fiches completes de deux entreprises, pour un argumentaire comparatif facteur par facteur.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker1": {"type": "string"},
                "ticker2": {"type": "string"},
            },
            "required": ["ticker1", "ticker2"],
        },
    },
    {
        "name": "classement",
        "description": "Renvoie les N entreprises les mieux notees, filtrees optionnellement par indice et/ou secteur.",
        "input_schema": {
            "type": "object",
            "properties": {
                "indice": {"type": "string", "description": "Ex. CAC40, NASDAQ"},
                "secteur": {"type": "string"},
                "n": {"type": "integer", "description": "Nombre de resultats, defaut 10"},
            },
        },
    },
    {
        "name": "statut_bot",
        "description": "Renvoie le statut operationnel (solde, derniere execution) du bot Or ou du Bot Actions.",
        "input_schema": {
            "type": "object",
            "properties": {"nom": {"type": "string", "enum": ["or", "actions"]}},
            "required": ["nom"],
        },
    },
    {
        "name": "positions_bot",
        "description": "Renvoie les positions actuellement ouvertes par le bot Or ou le Bot Actions.",
        "input_schema": {
            "type": "object",
            "properties": {"nom": {"type": "string", "enum": ["or", "actions"]}},
            "required": ["nom"],
        },
    },
    {
        "name": "resume_portefeuille",
        "description": "Combine les positions manuelles de l'utilisateur avec les positions des deux bots pour une vue d'ensemble du portefeuille.",
        "input_schema": {
            "type": "object",
            "properties": {
                "positions_manuelles": {
                    "type": "array",
                    "description": "Positions manuelles telles qu'envoyees par le navigateur (peut etre vide)",
                    "items": {"type": "object"},
                },
            },
        },
    },
    {
        "name": "proposer_lien",
        "description": (
            "Suggere a l'utilisateur de naviguer vers une fiche entreprise "
            "ou une section du site, OU propose un choix de "
            "desambiguisation (appeler plusieurs fois pour plusieurs "
            "choix). N'affecte jamais le texte de la reponse : ces "
            "suggestions sont affichees a part, comme des boutons."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cible_type": {"type": "string", "enum": ["ticker", "section"]},
                "cible_valeur": {"type": "string", "description": "Ex. 'MC.PA' ou 'or'/'portefeuille'/'indices'"},
                "libelle": {"type": "string", "description": "Texte du bouton, ex. 'Voir LVMH'"},
            },
            "required": ["cible_type", "cible_valeur", "libelle"],
        },
    },
]


def _find_company(ticker: str, companies: list[dict]) -> dict | None:
    for c in companies:
        if c.get("ticker") == ticker:
            return c
    return None


def fiche_entreprise(ticker: str, *, indices_source=data_sources.fetch_indices_data) -> dict:
    companies = indices_source().get("companies", [])
    company = _find_company(ticker, companies)
    if company is None:
        tous_tickers = [c.get("ticker", "") for c in companies]
        return {
            "erreur": f"ticker introuvable : {ticker!r}",
            "suggestions": get_close_matches(ticker, tous_tickers, n=3),
        }
    return company


def comparer_entreprises(
    ticker1: str, ticker2: str, *, indices_source=data_sources.fetch_indices_data,
) -> dict:
    return {
        "entreprise_1": fiche_entreprise(ticker1, indices_source=indices_source),
        "entreprise_2": fiche_entreprise(ticker2, indices_source=indices_source),
    }


def classement(
    indice: str | None = None, secteur: str | None = None, n: int = 10,
    *, indices_source=data_sources.fetch_indices_data,
) -> dict:
    companies = indices_source().get("companies", [])
    if indice:
        companies = [c for c in companies if c.get("index") == indice]
    if secteur:
        companies = [c for c in companies if c.get("sector") == secteur]
    trie = sorted(companies, key=lambda c: c.get("score") or 0, reverse=True)
    return {"classement": trie[:n]}


def statut_bot(nom: str, *, dashboard_source=data_sources.fetch_bot_dashboard) -> dict:
    data = dashboard_source(nom)
    if "erreur" in data:
        return data
    return {k: v for k, v in data.items() if k != "positions"}


def positions_bot(nom: str, *, dashboard_source=data_sources.fetch_bot_dashboard) -> dict:
    data = dashboard_source(nom)
    if "erreur" in data:
        return data
    return {"positions": data.get("positions", [])}


def resume_portefeuille(
    positions_manuelles: list[dict] | None = None,
    *, dashboard_source=data_sources.fetch_bot_dashboard,
) -> dict:
    toutes = list(positions_manuelles or [])
    for nom in ("or", "actions"):
        data = dashboard_source(nom)
        if "erreur" not in data:
            toutes.extend(data.get("positions", []))
    return {"positions": toutes, "nombre_total": len(toutes)}


def proposer_lien(cible_type: str, cible_valeur: str, libelle: str) -> dict:
    return {"cible_type": cible_type, "cible_valeur": cible_valeur, "libelle": libelle}


_HANDLERS = {
    "fiche_entreprise": lambda i, **deps: fiche_entreprise(i["ticker"], indices_source=deps["indices_source"]),
    "comparer_entreprises": lambda i, **deps: comparer_entreprises(
        i["ticker1"], i["ticker2"], indices_source=deps["indices_source"]),
    "classement": lambda i, **deps: classement(
        i.get("indice"), i.get("secteur"), i.get("n", 10), indices_source=deps["indices_source"]),
    "statut_bot": lambda i, **deps: statut_bot(i["nom"], dashboard_source=deps["dashboard_source"]),
    "positions_bot": lambda i, **deps: positions_bot(i["nom"], dashboard_source=deps["dashboard_source"]),
    "resume_portefeuille": lambda i, **deps: resume_portefeuille(
        i.get("positions_manuelles"), dashboard_source=deps["dashboard_source"]),
    "proposer_lien": lambda i, **deps: proposer_lien(i["cible_type"], i["cible_valeur"], i["libelle"]),
}


def dispatch_tool(
    name: str, tool_input: dict, *,
    indices_source=data_sources.fetch_indices_data,
    dashboard_source=data_sources.fetch_bot_dashboard,
) -> dict:
    """Execute l'outil nomme `name` avec `tool_input`. Ne leve JAMAIS :
    un nom inconnu ou une exception interne (cle manquante dans
    tool_input, etc.) renvoie un dict {"erreur": ...} plutot que de
    laisser une exception remonter jusqu'a la boucle d'orchestration."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"erreur": f"outil inconnu : {name!r}"}
    try:
        return handler(tool_input, indices_source=indices_source, dashboard_source=dashboard_source)
    except Exception as e:
        return {"erreur": f"echec de l'outil {name} : {e}"}
```

- [ ] **Step 4: Lancer les tests, vérifier qu'ils passent**

Run: `python -m pytest tests/assistant_ia/test_tools.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add assistant_ia/tools.py tests/assistant_ia/test_tools.py
git commit -m "feat(assistant_ia): les 7 outils + leur schema Claude"
```

---

### Task 4: Boucle d'orchestration (`assistant_ia/assistant.py`)

**Files:**
- Create: `assistant_ia/assistant.py`
- Test: `tests/assistant_ia/test_assistant.py`

**Interfaces:**
- Consomme : `tools.TOOL_DEFINITIONS`, `tools.dispatch_tool(name, input, ...)` (Task 3) ; `usage.record_usage(input_tokens, output_tokens, model=...)` (Task 1).
- Produit : `run_assistant_loop(question: str, history: list[dict], manual_positions: list[dict] | None = None, *, client=None) -> Iterator[dict]` — generateur d'evenements `{"type": "texte", "texte": str}`, `{"type": "liens", "liens": list[dict]}`, `{"type": "erreur", "detail": str}`, `{"type": "fin"}`. La Task 5 itere exclusivement sur ce generateur.

- [ ] **Step 1: Écrire le test qui échoue**

```python
# tests/assistant_ia/test_assistant.py
import pytest

import assistant_ia.assistant as assistant


@pytest.fixture(autouse=True)
def _no_real_usage_writes(monkeypatch):
    """Empeche TOUT test de ce fichier d'ecrire dans le vrai
    usage_today.json du depot (meme risque que les fonctions update_*
    non mockees dans tests/test_indices_score.py, voir
    [[project-nikkei-hangseng-chart]]) — seul le test dedie au suivi
    d'usage remplace ce stub par sa propre verification."""
    monkeypatch.setattr(assistant.usage, "record_usage", lambda *a, **k: None)


class _FakeBlock:
    def __init__(self, type_, **kwargs):
        self.type = type_
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeUsage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeMessage:
    def __init__(self, content, stop_reason, usage):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = usage


class _FakeStream:
    """Simule client.messages.stream(...) : un context manager iterable
    sur des evenements de texte, avec .get_final_message() a la fin."""

    def __init__(self, text_events, final_message):
        self._text_events = text_events
        self._final_message = final_message

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for texte in self._text_events:
            yield _FakeBlock("text", text=texte)

    def get_final_message(self):
        return self._final_message


class _FakeMessagesEndpoint:
    def __init__(self, scripted_streams):
        self._scripted = list(scripted_streams)
        self.appels = []

    def stream(self, **kwargs):
        self.appels.append(kwargs)
        return self._scripted.pop(0)


class _FakeClient:
    def __init__(self, scripted_streams):
        self.messages = _FakeMessagesEndpoint(scripted_streams)


def test_run_assistant_loop_yields_text_then_fin_when_no_tool_is_called():
    final = _FakeMessage(
        content=[_FakeBlock("text", text="LVMH est solide.")],
        stop_reason="end_turn", usage=_FakeUsage(100, 50))
    client = _FakeClient([_FakeStream(["LVMH est solide."], final)])

    evenements = list(assistant.run_assistant_loop("Pourquoi LVMH ?", [], client=client))

    types = [e["type"] for e in evenements]
    assert types == ["texte", "fin"]
    assert evenements[0]["texte"] == "LVMH est solide."


def test_run_assistant_loop_dispatches_a_tool_call_and_loops():
    tool_use_block = _FakeBlock("tool_use", id="tu_1", name="fiche_entreprise", input={"ticker": "MC.PA"})
    first_final = _FakeMessage(content=[tool_use_block], stop_reason="tool_use", usage=_FakeUsage(200, 20))
    second_final = _FakeMessage(
        content=[_FakeBlock("text", text="LVMH a un score de 72.")],
        stop_reason="end_turn", usage=_FakeUsage(300, 40))
    client = _FakeClient([
        _FakeStream([], first_final),
        _FakeStream(["LVMH a un score de 72."], second_final),
    ])

    evenements = list(assistant.run_assistant_loop(
        "Note de LVMH ?", [], client=client,
        tool_dispatch=lambda name, inp: {"ticker": "MC.PA", "score": 72.0}))

    assert client.messages.appels[1]["messages"][-1]["role"] == "user"
    types = [e["type"] for e in evenements]
    assert "texte" in types and "fin" in types


def test_run_assistant_loop_collects_proposer_lien_calls_separately():
    tool_use_1 = _FakeBlock("tool_use", id="tu_1", name="fiche_entreprise", input={"ticker": "MC.PA"})
    tool_use_2 = _FakeBlock(
        "tool_use", id="tu_2", name="proposer_lien",
        input={"cible_type": "ticker", "cible_valeur": "MC.PA", "libelle": "Voir LVMH"})
    first_final = _FakeMessage(
        content=[tool_use_1, tool_use_2], stop_reason="tool_use", usage=_FakeUsage(200, 20))
    second_final = _FakeMessage(
        content=[_FakeBlock("text", text="Voici.")], stop_reason="end_turn", usage=_FakeUsage(100, 10))
    client = _FakeClient([_FakeStream([], first_final), _FakeStream(["Voici."], second_final)])

    def fake_dispatch(name, tool_input):
        if name == "proposer_lien":
            return dict(tool_input, cible_type=tool_input["cible_type"])
        return {"ticker": "MC.PA"}

    evenements = list(assistant.run_assistant_loop("Q", [], client=client, tool_dispatch=fake_dispatch))

    liens_events = [e for e in evenements if e["type"] == "liens"]
    assert len(liens_events) == 1
    assert liens_events[0]["liens"][0]["cible_valeur"] == "MC.PA"


def test_run_assistant_loop_stops_at_the_iteration_cap():
    tool_use_block = _FakeBlock("tool_use", id="tu_x", name="fiche_entreprise", input={"ticker": "X"})
    boucle_infinie = _FakeMessage(content=[tool_use_block], stop_reason="tool_use", usage=_FakeUsage(10, 5))
    client = _FakeClient([_FakeStream([], boucle_infinie) for _ in range(assistant.MAX_TOOL_ITERATIONS)])

    evenements = list(assistant.run_assistant_loop(
        "Q", [], client=client, tool_dispatch=lambda name, inp: {"ok": True}))

    assert len(client.messages.appels) == assistant.MAX_TOOL_ITERATIONS
    assert evenements[-1]["type"] == "fin"
    assert any(e["type"] == "texte" and "n'ai pas pu" in e["texte"] for e in evenements)


def test_run_assistant_loop_records_usage_for_every_api_call(monkeypatch):
    enregistres = []
    monkeypatch.setattr(assistant.usage, "record_usage", lambda i, o, model=None: enregistres.append((i, o)))
    final = _FakeMessage(content=[_FakeBlock("text", text="ok")], stop_reason="end_turn", usage=_FakeUsage(111, 22))
    client = _FakeClient([_FakeStream(["ok"], final)])

    list(assistant.run_assistant_loop("Q", [], client=client))

    assert enregistres == [(111, 22)]
```

- [ ] **Step 2: Lancer les tests, vérifier qu'ils échouent**

Run: `python -m pytest tests/assistant_ia/test_assistant.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implémenter `assistant_ia/assistant.py`**

```python
# assistant_ia/assistant.py
# Boucle d'outils avec Claude (voir spec §5). Le client Anthropic est
# injectable (parametre `client`) pour que les tests ne fassent jamais
# de vrai appel reseau/paye — meme principe que `gw` injectable dans
# ibkr_bot/gold_bot. `tool_dispatch` est injectable separement de
# `tools.dispatch_tool` pour isoler les tests de cette boucle de la
# logique des outils eux-memes (deja testee dans test_tools.py).
import anthropic

import assistant_ia.tools as tools
import assistant_ia.usage as usage

MODEL = "claude-opus-5-5"
MAX_TOOL_ITERATIONS = 6

SYSTEM_PROMPT = """Tu es l'assistant du site "AI Investment" (analyse-or). \
Tu reponds UNIQUEMENT a partir des outils fournis — n'invente jamais un \
chiffre, un score ou un statut que tu n'as pas obtenu par un outil.

Quand on te demande de comparer ou d'expliquer un score, argumente \
TOUJOURS facteur par facteur (ROCE, dette nette/EBITDA, croissance, etc.) \
en citant les valeurs exactes renvoyees par les outils — jamais juste \
le score brut.

Pour toute suggestion de navigation (voir une fiche, une section) ou \
tout choix a proposer a l'utilisateur en cas d'ambiguite (plusieurs \
entreprises possibles), utilise l'outil proposer_lien — jamais un lien \
ecrit dans le texte de ta reponse. Appelle-le plusieurs fois s'il y a \
plusieurs choix a proposer.

Si une information manque ou qu'une question est ambigue entre plusieurs \
entreprises, dis-le explicitement et propose les choix via proposer_lien \
plutot que de deviner silencieusement."""


def _construit_messages(question: str, history: list[dict]) -> list[dict]:
    return list(history) + [{"role": "user", "content": question}]


def run_assistant_loop(
    question: str, history: list[dict], manual_positions: list[dict] | None = None,
    *, client=None, tool_dispatch=None,
):
    """Genere les evenements de la conversation : {"type": "texte", ...}
    au fil du streaming, {"type": "liens", "liens": [...]} une fois tous
    les appels proposer_lien collectes, puis {"type": "fin"}. N'accede
    jamais au reseau en dehors de l'API Anthropic elle-meme (voir
    tool_dispatch, qui appelle assistant_ia.tools, qui lit les fichiers
    publics/API bots — jamais ce module directement)."""
    if client is None:
        client = anthropic.Anthropic()
    if tool_dispatch is None:
        tool_dispatch = tools.dispatch_tool

    messages = _construit_messages(question, history)
    if manual_positions is not None:
        messages[-1] = {
            "role": "user",
            "content": f"{question}\n\n[positions_manuelles: {manual_positions}]",
        }

    liens_collectes: list[dict] = []

    for iteration in range(MAX_TOOL_ITERATIONS):
        with client.messages.stream(
            model=MODEL,
            max_tokens=4096,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            tools=tools.TOOL_DEFINITIONS,
            output_config={"effort": "high"},
            messages=messages,
        ) as stream:
            for bloc in stream:
                if bloc.type == "text" and bloc.text:
                    yield {"type": "texte", "texte": bloc.text}
            reponse = stream.get_final_message()

        usage.record_usage(reponse.usage.input_tokens, reponse.usage.output_tokens, model=MODEL)

        if reponse.stop_reason != "tool_use":
            if liens_collectes:
                yield {"type": "liens", "liens": liens_collectes}
            yield {"type": "fin"}
            return

        messages.append({"role": "assistant", "content": reponse.content})
        resultats_outils = []
        for bloc in reponse.content:
            if bloc.type != "tool_use":
                continue
            resultat = tool_dispatch(bloc.name, bloc.input)
            if bloc.name == "proposer_lien" and "erreur" not in resultat:
                liens_collectes.append(resultat)
            resultats_outils.append({
                "type": "tool_result", "tool_use_id": bloc.id,
                "content": str(resultat),
            })
        messages.append({"role": "user", "content": resultats_outils})

    if liens_collectes:
        yield {"type": "liens", "liens": liens_collectes}
    yield {
        "type": "texte",
        "texte": "\n\nJe n'ai pas pu rassembler toute l'information necessaire en une fois — essaie de reformuler en plusieurs questions plus precises.",
    }
    yield {"type": "fin"}
```

- [ ] **Step 4: Lancer les tests, vérifier qu'ils passent**

Run: `python -m pytest tests/assistant_ia/test_assistant.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add assistant_ia/assistant.py tests/assistant_ia/test_assistant.py
git commit -m "feat(assistant_ia): boucle d'outils Claude Opus 5.5 avec plafond d'iterations"
```

---

### Task 5: API HTTP (`assistant_ia/api.py`)

**Files:**
- Create: `assistant_ia/api.py`
- Test: `tests/assistant_ia/test_api.py`
- Modify: `requirements-bot.txt` (ajoute `anthropic`)

**Interfaces:**
- Consomme : `assistant.run_assistant_loop(question, history, manual_positions, client=...)` (Task 4) ; `usage.budget_exceeded(path=..., daily_budget_usd=...)` (Task 1).
- Produit : route `POST /ask`, format SSE (`data: {json}\n\n` par evenement). Rien d'autre n'en depend dans ce plan — c'est la derniere piece backend.

- [ ] **Step 1: Écrire le test qui échoue**

```python
# tests/assistant_ia/test_api.py
import pytest
from fastapi.testclient import TestClient

import assistant_ia.api as api


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AI_ASSISTANT_API_TOKEN", "secret-token")
    return TestClient(api.app)


def test_ask_rejects_a_missing_token(client):
    response = client.post("/ask", json={"question": "Bonjour", "history": []})

    assert response.status_code == 401


def test_ask_rejects_a_wrong_token(client):
    response = client.post(
        "/ask", json={"question": "Bonjour", "history": []},
        headers={"X-Bot-Token": "mauvais"})

    assert response.status_code == 401


def test_ask_streams_events_from_the_assistant_loop(client, monkeypatch):
    def fake_loop(question, history, manual_positions=None, **kwargs):
        yield {"type": "texte", "texte": "Bonjour !"}
        yield {"type": "fin"}

    monkeypatch.setattr(api.assistant, "run_assistant_loop", fake_loop)
    monkeypatch.setattr(api.usage, "budget_exceeded", lambda **kwargs: False)

    response = client.post(
        "/ask", json={"question": "Bonjour", "history": []},
        headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 200
    assert "Bonjour !" in response.text
    assert "\"type\": \"fin\"" in response.text or '"type":"fin"' in response.text.replace(" ", "")


def test_ask_refuses_when_the_daily_budget_is_exceeded(client, monkeypatch):
    monkeypatch.setattr(api.usage, "budget_exceeded", lambda **kwargs: True)

    response = client.post(
        "/ask", json={"question": "Bonjour", "history": []},
        headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 429
```

- [ ] **Step 2: Lancer les tests, vérifier qu'ils échouent**

Run: `python -m pytest tests/assistant_ia/test_api.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implémenter `assistant_ia/api.py`**

```python
# assistant_ia/api.py
# Point d'acces HTTPS de l'assistant IA : une seule route, POST /ask,
# protegee par jeton, en streaming SSE. Lecture seule — ce module
# n'ecrit jamais dans positions.json/state.json/aucun fichier d'un
# autre module (voir Global Constraints du plan). Structure calquee sur
# gold_bot/api.py/ibkr_bot/api.py.
import hmac
import json
import os

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

import assistant_ia.assistant as assistant
import assistant_ia.usage as usage

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://alexandreauq.github.io"],
    allow_methods=["POST"],
    allow_headers=["X-Bot-Token", "Content-Type"],
)


def _check_token(x_bot_token: str | None) -> None:
    """Identique a gold_bot.api._check_token : echec ferme, comparaison
    a temps constant sur les octets UTF-8."""
    expected = os.environ.get("AI_ASSISTANT_API_TOKEN")
    if not expected or not hmac.compare_digest(
        (x_bot_token or "").encode("utf-8", "surrogateescape"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Jeton invalide ou absent")


def _formate_evenements_sse(evenements):
    for evenement in evenements:
        yield f"data: {json.dumps(evenement, ensure_ascii=False)}\n\n"


@app.post("/ask")
async def ask(request: Request, x_bot_token: str | None = Header(default=None)):
    _check_token(x_bot_token)
    if usage.budget_exceeded():
        raise HTTPException(status_code=429, detail="Plafond quotidien de l'assistant atteint, reessaie demain")

    payload = await request.json()
    question = payload.get("question", "")
    history = payload.get("history", [])
    manual_positions = payload.get("manual_positions")

    evenements = assistant.run_assistant_loop(question, history, manual_positions)
    return StreamingResponse(_formate_evenements_sse(evenements), media_type="text/event-stream")
```

Ajouter `anthropic` dans `requirements-bot.txt` (ordre alphabétique non
requis, suivre l'ordre existant du fichier — l'ajouter en fin de
liste) :

```
requests
fastapi
uvicorn[standard]
httpx
python-dateutil
ib_async
anthropic
```

- [ ] **Step 4: Lancer les tests, vérifier qu'ils passent**

Run: `python -m pytest tests/assistant_ia/test_api.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Lancer toute la suite `assistant_ia`**

Run: `python -m pytest tests/assistant_ia/ -q`
Expected: PASS, 0 échec (toutes les tâches 1-5)

- [ ] **Step 6: Commit**

```bash
git add assistant_ia/api.py requirements-bot.txt tests/assistant_ia/test_api.py
git commit -m "feat(assistant_ia): endpoint POST /ask en streaming SSE, protege par jeton"
```

---

### Task 6: Déploiement

**Files:**
- Create: `deploy/assistant-ia-api.service`
- Modify: `tests/ibkr_bot/test_deploy_files.py` (ajoute la vérification du nouveau fichier de service)

**Interfaces:**
- Consomme : rien (fichier de déploiement statique).
- Produit : rien dont une autre tâche de ce plan dépende — dernière tâche indépendante avant le frontend.

- [ ] **Step 1: Regarder le test existant pour suivre le même format**

Ouvrir `tests/ibkr_bot/test_deploy_files.py` et repérer le test qui vérifie déjà
`deploy/ibkr-bot-api.service` (même structure : `ExecStart`, port,
certificat). Le nouveau test suit exactement le même moule.

- [ ] **Step 2: Écrire le test qui échoue**

Ajouter dans `tests/ibkr_bot/test_deploy_files.py` :

```python
def test_assistant_ia_api_service_file_targets_the_right_module_and_port():
    with open("deploy/assistant-ia-api.service", encoding="utf-8") as fh:
        contenu = fh.read()

    assert "assistant_ia.api:app" in contenu
    assert "--port 8445" in contenu
    assert "/etc/letsencrypt/live/goldbot.fr/privkey.pem" in contenu
    assert "/etc/letsencrypt/live/goldbot.fr/fullchain.pem" in contenu
```

- [ ] **Step 3: Lancer le test, vérifier qu'il échoue**

Run: `python -m pytest tests/ibkr_bot/test_deploy_files.py -k assistant_ia -v`
Expected: FAIL (`FileNotFoundError`)

- [ ] **Step 4: Créer `deploy/assistant-ia-api.service`**

```ini
[Unit]
Description=Assistant IA - API de recherche intelligente
After=network.target

[Service]
Type=simple
User=assistantia
WorkingDirectory=/home/assistantia/analyse-or
EnvironmentFile=/home/assistantia/analyse-or/.env
ExecStart=/home/assistantia/analyse-or/venv/bin/uvicorn assistant_ia.api:app --host 0.0.0.0 --port 8445 --ssl-keyfile /etc/letsencrypt/live/goldbot.fr/privkey.pem --ssl-certfile /etc/letsencrypt/live/goldbot.fr/fullchain.pem
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 5: Lancer le test, vérifier qu'il passe**

Run: `python -m pytest tests/ibkr_bot/test_deploy_files.py -v`
Expected: PASS (tous les tests du fichier, y compris les existants)

- [ ] **Step 6: Commit**

```bash
git add deploy/assistant-ia-api.service tests/ibkr_bot/test_deploy_files.py
git commit -m "feat(deploy): service systemd pour l'assistant IA (port 8445)"
```

**Note pour le déploiement réel (hors de ce plan, à faire manuellement
sur le VPS une fois ce plan mergé)** : créer l'utilisateur système
`assistantia` (même procédure que `goldbot`/`ibkrbot`), cloner le dépôt,
créer le venv, installer `requirements-bot.txt`, configurer
`ANTHROPIC_API_KEY`/`AI_ASSISTANT_API_TOKEN`/`BOT_API_TOKEN`/
`IBKR_BOT_API_TOKEN` dans son `.env`, `ufw allow 8445`, rejoindre le
groupe `ssl-cert`, ajouter ce service au hook de renouvellement
Let's Encrypt existant — même liste d'étapes que pour `ibkr-bot-api`
(voir `docs/superpowers/specs/2026-09-20-ibkr-bot-api-design.md` §6).

---

### Task 7: Navigation instantanée côté client

**Files:**
- Modify: `docs/index.html` (nouvelle fonction JS, aucune fonction existante modifiée)

**Interfaces:**
- Consomme : `indicesData.companies` (déjà chargé globalement par le site — voir `loadIndices`, `docs/index.html:3351`), `location.hash` (routage existant).
- Produit : `matchInstantNav(query: string) -> string | null` (hash de redirection, ex. `"#indices/MC.PA"`, ou `null` si rien ne matche clairement) ; `ensureIndicesData() -> Promise<void>`. La Task 8 appelle ces deux fonctions par leur nom exact.

- [ ] **Step 1: Ajouter `ensureIndicesData` et `matchInstantNav`**

Dans `docs/index.html`, ajouter ces deux fonctions juste avant
`loadIndices` (ligne ~3351, chercher le commentaire "Pagination carrousel
des sections Portefeuille") :

```javascript
// Charge indicesData si ce n'est pas deja fait (meme fetch que
// loadIndices/le chargement du Portefeuille, voir leurs commentaires) —
// necessaire ici car l'ecran d'accueil peut etre la toute premiere
// chose affichee, avant que l'utilisateur n'ait jamais ouvert Indices
// ou Portefeuille.
async function ensureIndicesData() {
  if (indicesData) return;
  try {
    const res = await fetch('indices.json?t=' + Date.now());
    if (!res.ok) throw new Error('indices.json introuvable');
    indicesData = await res.json();
  } catch (e) {
    console.error('Impossible de charger indices.json pour la recherche', e);
  }
}

// Sections connues du routeur (voir parseRoute) — mots-cles francais
// courants qui doivent rediriger directement sans passer par l'IA.
const INSTANT_NAV_SECTIONS = {
  'or': '#or', 'bot or': '#or', 'scalping': '#or',
  'portefeuille': '#portefeuille', 'portfolio': '#portefeuille',
  'bot actions': '#portefeuille',
  'indices': '#indices',
};

// Correspondance instantanee, sans IA : meme logique de filtrage que la
// recherche existante dans la liste Indices (substring sur nom/ticker en
// minuscules, voir sortedFilteredCompanies) — reutilisee ici pour rester
// coherent avec ce que l'utilisateur voit deja ailleurs sur le site.
// Renvoie un hash de redirection si UNE SEULE entreprise correspond
// clairement, ou le hash d'une section connue — sinon null (la question
// part alors vers l'assistant IA).
function matchInstantNav(query) {
  const q = query.trim().toLowerCase();
  if (!q) return null;
  if (INSTANT_NAV_SECTIONS[q]) return INSTANT_NAV_SECTIONS[q];
  if (!indicesData || !Array.isArray(indicesData.companies)) return null;
  const matches = indicesData.companies.filter(c =>
    c.name.toLowerCase().includes(q) || c.ticker.toLowerCase().includes(q));
  if (matches.length === 1) return `#indices/${encodeURIComponent(matches[0].ticker)}`;
  return null;
}
```

- [ ] **Step 2: Vérification manuelle**

Aucun harnais de test JS dans ce dépôt (voir la Task 8 de
`docs/superpowers/plans/2026-10-03-ibkr-bot-fx-pnl.md` pour le précédent
sur ce point). Ouvrir `docs/index.html` dans un navigateur
(`python -m http.server` depuis `docs/`), ouvrir la console, et vérifier
à la main : `matchInstantNav("LVMH")` renvoie `"#indices/MC.PA"` (après
avoir visité Indices une fois, ou après un appel à `await
ensureIndicesData()` dans la console) ; `matchInstantNav("bot or")`
renvoie `"#or"` ; `matchInstantNav("pourquoi LVMH est mieux note")`
renvoie `null`.

- [ ] **Step 3: Commit**

```bash
git add docs/index.html
git commit -m "feat(site): navigation instantanee cote client pour la barre de recherche IA"
```

---

### Task 8: Panneau de conversation sur l'écran d'accueil

**Files:**
- Modify: `docs/index.html` (nouvel élément HTML dans `.home-hero`, nouvelles fonctions JS)

**Interfaces:**
- Consomme : `matchInstantNav(query)`, `ensureIndicesData()` (Task 7) ; endpoint `POST /ask` de la Task 5 (format SSE, événements `{"type": "texte"|"liens"|"fin", ...}`) ; `loadPortfolio()` de `docs/portfolio.js` (positions manuelles, forme `{id, ticker, quantity, buy_price, buy_date}`, voir `PORTFOLIO_STORAGE_KEY`).
- Produit : rien dont une tâche ultérieure dépende — dernière tâche du plan.

- [ ] **Step 1: Ajouter le jeton et la barre dans `.home-hero`**

Dans `docs/index.html`, juste après `getIbkrBotToken` (ligne ~2461),
ajouter :

```javascript
function getAiAssistantToken() {
  let token = localStorage.getItem('aiAssistantToken');
  if (!token) {
    token = prompt('Jeton d\'acces a l\'assistant de recherche (demande une seule fois, conserve sur cet appareil) :');
    if (token) localStorage.setItem('aiAssistantToken', token);
  }
  return token;
}
```

Dans le bloc `.home-hero` (ligne ~1324-1328), juste après la balise
`<p class="home-tagline">` et avant la fermeture de `.home-hero`,
ajouter :

```html
<div class="home-search">
  <input type="search" id="homeSearchInput" class="list-search"
         placeholder="Demande-moi n'importe quoi sur tes donnees…">
  <div id="homeSearchPanel" class="home-search-panel" hidden></div>
</div>
```

- [ ] **Step 2: Ajouter la logique de recherche/conversation**

Ajouter ces fonctions juste après `matchInstantNav` (Task 7) :

```javascript
let homeSearchHistory = [];

function homeSearchBubbleHtml(texte, liens) {
  const liensHtml = (liens || []).map(l => `
    <button class="toggle-btn home-search-link" type="button"
            data-cible-type="${l.cible_type}" data-cible-valeur="${escHtml(l.cible_valeur)}"
            data-libelle="${escHtml(l.libelle)}">${escHtml(l.libelle)}</button>`).join('');
  return `
    <div class="home-search-bubble">
      <p>${escHtml(texte)}</p>
      ${liensHtml ? `<div class="home-search-links">${liensHtml}</div>` : ''}
    </div>`;
}

function wireHomeSearchLinkButtons(panel) {
  panel.querySelectorAll('.home-search-link').forEach(btn => {
    btn.addEventListener('click', () => {
      const cibleType = btn.dataset.cibleType;
      const cibleValeur = btn.dataset.cibleValeur;
      if (cibleType === 'ticker') {
        location.hash = `#indices/${encodeURIComponent(cibleValeur)}`;
      } else {
        location.hash = INSTANT_NAV_SECTIONS[cibleValeur] || `#${cibleValeur}`;
      }
    });
  });
}

async function askAssistant(question) {
  const panel = document.getElementById('homeSearchPanel');
  panel.hidden = false;
  const token = getAiAssistantToken();
  if (!token) return;

  const positionsManuelles = (typeof loadPortfolio === 'function')
    ? loadPortfolio(localStorage) : [];

  let texteAccumule = '';
  let liensRecus = [];
  const bubbleIndex = panel.children.length;
  panel.insertAdjacentHTML('beforeend', homeSearchBubbleHtml('…', []));

  try {
    const response = await fetch('https://goldbot.fr:8445/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Bot-Token': token },
      body: JSON.stringify({
        question, history: homeSearchHistory, manual_positions: positionsManuelles,
      }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lignes = buffer.split('\n\n');
      buffer = lignes.pop();
      for (const ligne of lignes) {
        if (!ligne.startsWith('data: ')) continue;
        const evenement = JSON.parse(ligne.slice(6));
        if (evenement.type === 'texte') texteAccumule += evenement.texte;
        if (evenement.type === 'liens') liensRecus = evenement.liens;
        panel.children[bubbleIndex].outerHTML = homeSearchBubbleHtml(texteAccumule || '…', liensRecus);
        wireHomeSearchLinkButtons(panel);
      }
    }
    homeSearchHistory.push({ role: 'user', content: question });
    homeSearchHistory.push({ role: 'assistant', content: texteAccumule });
  } catch (e) {
    panel.children[bubbleIndex].outerHTML = homeSearchBubbleHtml(
      "Je n'ai pas pu contacter l'assistant, reessaie dans un instant.", []);
  }
}

function wireHomeSearch() {
  const input = document.getElementById('homeSearchInput');
  if (!input) return;
  input.addEventListener('keydown', async (e) => {
    if (e.key !== 'Enter') return;
    const query = input.value;
    await ensureIndicesData();
    const navHash = matchInstantNav(query);
    if (navHash) {
      location.hash = navHash;
      input.value = '';
      return;
    }
    if (query.trim()) {
      askAssistant(query.trim());
      input.value = '';
    }
  });
}
```

Appeler `wireHomeSearch()` dans la fonction d'initialisation du site
(chercher `document.addEventListener('DOMContentLoaded'` ou l'équivalent
déjà en place, et y ajouter l'appel — ne pas créer un second point
d'entrée).

- [ ] **Step 3: CSS minimal**

Ajouter dans le bloc `<style>` existant, à côté des autres règles
`.home-*` :

```css
.home-search { margin: 16px 0; position: relative; }
.home-search-panel {
  display: flex; flex-direction: column; gap: 10px; margin-top: 10px;
  max-height: 50vh; overflow-y: auto;
}
.home-search-bubble {
  background: var(--bg-2); border: 1px solid var(--border);
  border-radius: var(--radius-lg); padding: 12px 16px;
}
.home-search-links { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
```

- [ ] **Step 4: Vérification manuelle**

Ouvrir `docs/index.html` en local, taper une requête de navigation
("LVMH") → redirection immédiate sans appel réseau. Taper une question
complète avec le backend de la Task 5 lancé localement (`uvicorn
assistant_ia.api:app --port 8445`, avec `AI_ASSISTANT_API_TOKEN` et
`ANTHROPIC_API_KEY` positionnés) → vérifier que la bulle se remplit
progressivement et que les boutons de liens apparaissent et redirigent
correctement au clic, sans erreur dans la console.

- [ ] **Step 5: Commit**

```bash
git add docs/index.html
git commit -m "feat(site): panneau de conversation IA sur l'ecran d'accueil"
```
