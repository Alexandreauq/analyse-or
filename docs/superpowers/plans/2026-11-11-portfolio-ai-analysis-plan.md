# Analyse IA du portefeuille manuel (onglet "Actions") — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter une analyse IA à la demande du portefeuille manuel (onglet
"Actions"), avec reco par ligne et historique append-only côté serveur.

**Architecture:** Un nouveau module Python (`assistant_ia/portfolio_analysis.py`)
fait un seul appel structuré à Claude (patron de `gold_bot/ai_shadow.py`),
exposé par deux nouvelles routes sur l'assistant IA existant
(`assistant_ia/api.py`, port 8445). Le calcul arithmétique du portefeuille
reste entièrement côté navigateur (`docs/portfolio.js`, fonctions
existantes, aucune dupliquée en Python) ; le serveur n'ajoute que le
jugement qualitatif IA (recherche web, reco, comparaison temporelle).

**Tech Stack:** Python/FastAPI (`assistant_ia/`), SDK `anthropic`
(modèle `claude-sonnet-5-5`), JS natif sans framework (`docs/portfolio.js`,
`docs/index.html`), tests Python via `pytest`, tests JS via `node
docs/portfolio.test.js` (assertions natives, sans framework).

**Spec:** `docs/superpowers/specs/2026-11-11-portfolio-ai-analysis-design.md`

## Global Constraints

- Jeton réutilisé : `AI_ASSISTANT_API_TOKEN` / en-tête `X-Bot-Token` — aucun
  nouveau secret.
- Modèle : `claude-sonnet-5-5` (= `usage.MODEL_SONNET_5_5`), même plafond de
  dépense quotidien partagé que `/ask` (`usage.budget_exceeded()` /
  `usage.record_usage()`), pas de budget séparé.
- `positions` envoyées à l'API = positions **ouvertes** uniquement, max 50
  (`MAX_POSITIONS`), sinon `400`.
- Réponse HTTP toujours `200` quand jeton/débit/concurrence passent ;
  l'échec de l'appel IA lui-même est signalé dans le corps
  (`{"disponible": false, "erreur": "...", "resultat": null}`), jamais par
  un code d'erreur HTTP.
- `assistant_ia/portfolio_analysis_history.jsonl` : append-only, jamais
  commité (ajouté à `.gitignore`), jamais une seconde source de calcul —
  seul le texte de l'analyse y est archivé, jamais les métriques chiffrées.
- Aucune fonction de calcul de `docs/portfolio.js` n'est dupliquée côté
  Python (totaux, concentration, santé, attribution, dividendes restent
  calculés uniquement en JS, transmis tels quels dans le payload).
- Pas de nouvelle constante d'URL dans `docs/index.html` — URLs littérales
  `https://goldbot.fr:8445/portfolio-analysis` et `/portfolio-analysis/last`,
  cohérent avec le style déjà en place pour `/ask`/`/financial-analysis`.
- Les fonctions de rendu/branchement DOM ajoutées à `docs/index.html` ne
  sont **pas** testées unitairement (convention déjà établie dans ce
  fichier — seule la logique pure de `docs/portfolio.js` l'est).
- Travail réalisé sur la branche `feature/portfolio-ai-analysis-onglet-actions`
  (déjà créée, worktree déjà en place), jamais sur `main`.

---

### Task 1: Module `assistant_ia/portfolio_analysis.py`

**Files:**
- Create: `assistant_ia/portfolio_analysis.py`
- Test: `tests/assistant_ia/test_portfolio_analysis.py`

**Interfaces:**
- Consomme : `assistant_ia.tools.fiche_entreprise(ticker, *, indices_source)`
  (existant, renvoie soit la fiche allégée `{name, index, sector, score,
  alerts, ...}`, soit `{"erreur": str, "suggestions": [...]}`) ;
  `assistant_ia.data_sources.fetch_indices_data` (existant, injecté comme
  valeur par défaut de `indices_source`) ; `assistant_ia.usage.MODEL_SONNET_5_5`,
  `assistant_ia.usage.record_usage(input_tokens, output_tokens, model=...)`,
  `assistant_ia.usage.budget_exceeded()` (tous existants, aucune
  modification).
- Produit (consommé par la Task 2) :
  `run_portfolio_analysis(payload: dict, *, client=None, indices_source=data_sources.fetch_indices_data, history_path: str = HISTORY_PATH) -> dict`
  renvoyant `{"disponible": bool, "erreur": str | None, "resultat": dict | None}` ;
  `last_analysis_timestamp(path: str = HISTORY_PATH) -> str | None` ;
  constante `MAX_POSITIONS = 50` ; constante `HISTORY_PATH`.

- [ ] **Step 1: Écrire les tests de `_parse_resultat` (ils échoueront : le module n'existe pas encore)**

Créer `tests/assistant_ia/test_portfolio_analysis.py` :

```python
import json

import pytest

import assistant_ia.portfolio_analysis as portfolio_analysis


def test_parse_resultat_extracts_the_last_json_block():
    texte = (
        "Du texte de raisonnement avant.\n"
        "```json\n"
        '{"resume_global": "ok", "par_ligne": [], "points_attention": []}\n'
        "```\n"
    )
    resultat = portfolio_analysis._parse_resultat(texte)
    assert resultat["resume_global"] == "ok"
    assert resultat["par_ligne"] == []


def test_parse_resultat_returns_none_when_no_json_block():
    assert portfolio_analysis._parse_resultat("juste du texte, pas de bloc") is None


def test_parse_resultat_returns_none_on_malformed_json():
    texte = "```json\n{\"resume_global\": \"ok\", \n```"
    assert portfolio_analysis._parse_resultat(texte) is None


def test_parse_resultat_returns_none_when_required_keys_missing():
    texte = '```json\n{"points_attention": []}\n```'
    assert portfolio_analysis._parse_resultat(texte) is None


def test_parse_resultat_fills_in_missing_optional_keys():
    texte = '```json\n{"resume_global": "ok", "par_ligne": []}\n```'
    resultat = portfolio_analysis._parse_resultat(texte)
    assert resultat["points_attention"] == []
    assert resultat["comparaison_precedente"] is None


def test_parse_resultat_normalizes_reco_case_and_whitespace():
    texte = (
        '```json\n{"resume_global": "ok", "par_ligne": '
        '[{"ticker": "MC.PA", "reco": " Garder ", "justification": "x"}]}\n```'
    )
    resultat = portfolio_analysis._parse_resultat(texte)
    assert resultat["par_ligne"][0]["reco"] == "garder"


def test_parse_resultat_replaces_an_invalid_reco_value_with_a_verifier():
    texte = (
        '```json\n{"resume_global": "ok", "par_ligne": '
        '[{"ticker": "MC.PA", "reco": "acheter_plus", "justification": "x"}]}\n```'
    )
    resultat = portfolio_analysis._parse_resultat(texte)
    assert resultat["par_ligne"][0]["reco"] == "a_verifier"


def test_parse_resultat_uses_the_last_block_when_several_are_present():
    texte = (
        '```json\n{"resume_global": "premier", "par_ligne": []}\n```\n'
        "texte intermediaire\n"
        '```json\n{"resume_global": "dernier", "par_ligne": []}\n```'
    )
    resultat = portfolio_analysis._parse_resultat(texte)
    assert resultat["resume_global"] == "dernier"
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `pytest tests/assistant_ia/test_portfolio_analysis.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'assistant_ia.portfolio_analysis'`

- [ ] **Step 3: Créer `assistant_ia/portfolio_analysis.py` avec `_parse_resultat` et les constantes de base**

```python
# assistant_ia/portfolio_analysis.py
# Analyse IA du portefeuille manuel (voir spec 2026-11-11) : un appel
# structure, pas une conversation -- un seul appel Claude avec recherche
# web, JSON extrait en fin de reponse (meme esprit defensif que
# gold_bot/ai_shadow.py::_parse_verdict, mais un bloc balise ```json plutot
# qu'une regex a accolades : le JSON ici contient des tableaux d'objets
# imbriques, qu'une regex non recursive ne capturerait pas). Archive dans
# un historique append-only pour permettre la comparaison "depuis la
# derniere fois". Lecture seule sur indices.json -- seul fichier ecrit :
# HISTORY_PATH, jamais commite (voir .gitignore).
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

import assistant_ia.data_sources as data_sources
import assistant_ia.tools as tools
import assistant_ia.usage as usage

MODEL = usage.MODEL_SONNET_5_5
MAX_TOKENS = 8000
REQUEST_TIMEOUT_SECONDS = 60.0
WEB_SEARCH_MAX_USES = 4
MAX_POSITIONS = 50
HISTORY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio_analysis_history.jsonl")

RECO_VALIDES = {"garder", "renforcer", "alleger", "vendre"}
_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_resultat(texte: str) -> dict | None:
    """Extrait le DERNIER bloc ```json...``` du texte (si le modele a
    pense a voix haute avant, ou produit plusieurs blocs par erreur, seul
    le dernier compte). None si absent, JSON invalide, ou si les cles
    obligatoires (resume_global, par_ligne) manquent -- jamais
    d'exception propagee."""
    matches = _JSON_BLOCK_RE.findall(texte)
    if not matches:
        return None
    try:
        parsed = json.loads(matches[-1])
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if "resume_global" not in parsed or "par_ligne" not in parsed:
        return None
    parsed.setdefault("points_attention", [])
    parsed.setdefault("comparaison_precedente", None)
    if not isinstance(parsed["par_ligne"], list):
        return None
    for ligne in parsed["par_ligne"]:
        if not isinstance(ligne, dict):
            continue
        reco = ligne.get("reco")
        if isinstance(reco, str):
            reco = reco.strip().lower()
            ligne["reco"] = reco if reco in RECO_VALIDES else "a_verifier"
        else:
            ligne["reco"] = "a_verifier"
    return parsed
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `pytest tests/assistant_ia/test_portfolio_analysis.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add assistant_ia/portfolio_analysis.py tests/assistant_ia/test_portfolio_analysis.py
git commit -m "feat(assistant-ia): parsing defensif du JSON d'analyse portefeuille"
```

- [ ] **Step 6: Écrire les tests de `_enrichit_positions`**

Ajouter à `tests/assistant_ia/test_portfolio_analysis.py` :

```python
def _indices_source_factory(companies):
    return lambda: {"companies": companies}


def test_enrichit_positions_attaches_the_fiche_for_a_known_ticker():
    companies = [{"ticker": "MC.PA", "name": "LVMH", "index": "CAC40",
                  "sector": "Consumer Cyclical", "score": 42, "alerts": []}]
    positions = [{"ticker": "MC.PA", "quantity": 3, "buy_price": 700.0, "buy_date": "2026-01-01"}]

    enrichies = portfolio_analysis._enrichit_positions(
        positions, indices_source=_indices_source_factory(companies))

    assert enrichies[0]["position"] == positions[0]
    assert enrichies[0]["fiche"]["name"] == "LVMH"


def test_enrichit_positions_keeps_the_position_when_ticker_is_unknown():
    positions = [{"ticker": "INCONNU.XX", "quantity": 1, "buy_price": 10.0, "buy_date": "2026-01-01"}]

    enrichies = portfolio_analysis._enrichit_positions(
        positions, indices_source=_indices_source_factory([]))

    assert enrichies[0]["position"] == positions[0]
    assert "erreur" in enrichies[0]["fiche"]
```

- [ ] **Step 7: Lancer les tests pour vérifier qu'ils échouent**

Run: `pytest tests/assistant_ia/test_portfolio_analysis.py -v -k enrichit`
Expected: FAIL avec `AttributeError: module 'assistant_ia.portfolio_analysis' has no attribute '_enrichit_positions'`

- [ ] **Step 8: Implémenter `_enrichit_positions`**

Ajouter à `assistant_ia/portfolio_analysis.py` (après `_parse_resultat`) :

```python
def _enrichit_positions(positions: list[dict], *, indices_source) -> list[dict]:
    """Associe a chaque position sa fiche societe a jour (score, secteur,
    alertes) -- reutilise fiche_entreprise tel quel, jamais reimplemente.
    Un ticker introuvable (retire du site) donne la fiche d'erreur deja
    renvoyee par fiche_entreprise ; la position est quand meme incluse
    (voir _describe_positions), jamais exclue silencieusement."""
    enrichies = []
    for position in positions:
        fiche = tools.fiche_entreprise(position.get("ticker", ""), indices_source=indices_source)
        enrichies.append({"position": position, "fiche": fiche})
    return enrichies
```

- [ ] **Step 9: Lancer les tests pour vérifier qu'ils passent**

Run: `pytest tests/assistant_ia/test_portfolio_analysis.py -v`
Expected: PASS (10 tests)

- [ ] **Step 10: Commit**

```bash
git add assistant_ia/portfolio_analysis.py tests/assistant_ia/test_portfolio_analysis.py
git commit -m "feat(assistant-ia): enrichit les positions avec la fiche societe du site"
```

- [ ] **Step 11: Écrire les tests de l'historique (`_lit_derniere_analyse`, `last_analysis_timestamp`, `_append_history`)**

Ajouter à `tests/assistant_ia/test_portfolio_analysis.py` :

```python
def test_lit_derniere_analyse_returns_none_when_file_absent(tmp_path):
    chemin = str(tmp_path / "absent.jsonl")
    assert portfolio_analysis._lit_derniere_analyse(chemin) is None


def test_lit_derniere_analyse_returns_the_last_valid_line(tmp_path):
    chemin = tmp_path / "historique.jsonl"
    chemin.write_text(
        json.dumps({"horodatage": "2026-11-01T00:00:00Z", "resultat": {"resume_global": "premiere"}}) + "\n"
        + json.dumps({"horodatage": "2026-11-05T00:00:00Z", "resultat": {"resume_global": "derniere"}}) + "\n",
        encoding="utf-8",
    )
    derniere = portfolio_analysis._lit_derniere_analyse(str(chemin))
    assert derniere["resultat"]["resume_global"] == "derniere"


def test_lit_derniere_analyse_skips_a_corrupted_last_line(tmp_path):
    chemin = tmp_path / "historique.jsonl"
    chemin.write_text(
        json.dumps({"horodatage": "2026-11-01T00:00:00Z", "resultat": {"resume_global": "valide"}}) + "\n"
        + "{ligne corrompue\n",
        encoding="utf-8",
    )
    derniere = portfolio_analysis._lit_derniere_analyse(str(chemin))
    assert derniere["resultat"]["resume_global"] == "valide"


def test_last_analysis_timestamp_returns_none_without_history(tmp_path):
    chemin = str(tmp_path / "absent.jsonl")
    assert portfolio_analysis.last_analysis_timestamp(chemin) is None


def test_last_analysis_timestamp_returns_the_last_horodatage(tmp_path):
    chemin = tmp_path / "historique.jsonl"
    chemin.write_text(
        json.dumps({"horodatage": "2026-11-05T00:00:00Z", "resultat": {}}) + "\n", encoding="utf-8")
    assert portfolio_analysis.last_analysis_timestamp(str(chemin)) == "2026-11-05T00:00:00Z"


def test_append_history_adds_a_line_to_the_file(tmp_path):
    chemin = tmp_path / "historique.jsonl"
    assert portfolio_analysis._append_history({"horodatage": "2026-11-11T00:00:00Z"}, str(chemin)) is True
    contenu = chemin.read_text(encoding="utf-8")
    assert json.loads(contenu.strip())["horodatage"] == "2026-11-11T00:00:00Z"


def test_append_history_returns_false_without_raising_when_write_fails(tmp_path):
    # Un dossier a la place du fichier fait echouer open(..., "a") -- cas
    # improbable en production, mais verifie le contrat "jamais lever".
    chemin = tmp_path / "un_dossier"
    chemin.mkdir()
    assert portfolio_analysis._append_history({"horodatage": "x"}, str(chemin)) is False
```

- [ ] **Step 12: Lancer les tests pour vérifier qu'ils échouent**

Run: `pytest tests/assistant_ia/test_portfolio_analysis.py -v -k "derniere_analyse or last_analysis or append_history"`
Expected: FAIL (`AttributeError` sur `_lit_derniere_analyse`/`last_analysis_timestamp`/`_append_history`)

- [ ] **Step 13: Implémenter les fonctions d'historique**

Ajouter à `assistant_ia/portfolio_analysis.py` :

```python
def _lit_derniere_analyse(path: str = HISTORY_PATH) -> dict | None:
    """Derniere ligne valide du journal, ou None si absent/vide/toutes les
    lignes corrompues -- jamais d'exception (meme contrat que
    ibkr_bot.journal.read_runs)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lignes = fh.readlines()
    except Exception:
        return None
    for ligne in reversed(lignes):
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            return json.loads(ligne)
        except json.JSONDecodeError:
            continue
    return None


def last_analysis_timestamp(path: str = HISTORY_PATH) -> str | None:
    derniere = _lit_derniere_analyse(path)
    return derniere.get("horodatage") if derniere else None


def _append_history(entry: dict, path: str = HISTORY_PATH) -> bool:
    """Ajoute une ligne JSON au journal append-only. Renvoie False (sans
    jamais lever) si l'ecriture echoue -- meme contrat que
    ibkr_bot.journal.append_run."""
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        print(f"Erreur journalisation analyse portefeuille : {e}")
        return False
```

- [ ] **Step 14: Lancer les tests pour vérifier qu'ils passent**

Run: `pytest tests/assistant_ia/test_portfolio_analysis.py -v`
Expected: PASS (16 tests)

- [ ] **Step 15: Commit**

```bash
git add assistant_ia/portfolio_analysis.py tests/assistant_ia/test_portfolio_analysis.py
git commit -m "feat(assistant-ia): historique append-only des analyses de portefeuille"
```

- [ ] **Step 16: Écrire les tests de `_build_prompt`**

Ajouter à `tests/assistant_ia/test_portfolio_analysis.py` :

```python
def test_build_prompt_includes_each_position_ticker_and_quantity():
    positions_enrichies = [{
        "position": {"ticker": "MC.PA", "quantity": 3, "buy_price": 700.0, "buy_date": "2026-01-01"},
        "fiche": {"name": "LVMH", "index": "CAC40", "sector": "Consumer Cyclical", "score": 42, "alerts": []},
    }]
    prompt = portfolio_analysis._build_prompt(positions_enrichies, {}, None)
    assert "MC.PA" in prompt
    assert "quantite=3" in prompt


def test_build_prompt_marks_unknown_ticker_data_as_unavailable():
    positions_enrichies = [{
        "position": {"ticker": "INCONNU.XX", "quantity": 1, "buy_price": 10.0, "buy_date": "2026-01-01"},
        "fiche": {"erreur": "ticker introuvable"},
    }]
    prompt = portfolio_analysis._build_prompt(positions_enrichies, {}, None)
    assert "donnee indice indisponible" in prompt


def test_build_prompt_tells_the_model_to_output_null_comparison_without_history():
    prompt = portfolio_analysis._build_prompt([], {}, None)
    assert "comparaison_precedente" in prompt
    assert "null" in prompt


def test_build_prompt_includes_the_previous_summary_when_history_exists():
    derniere_analyse = {
        "horodatage": "2026-11-05T00:00:00Z",
        "resultat": {"resume_global": "portefeuille equilibre, aucune alerte"},
    }
    prompt = portfolio_analysis._build_prompt([], {}, derniere_analyse)
    assert "portefeuille equilibre, aucune alerte" in prompt


def test_build_prompt_requests_a_json_code_block():
    prompt = portfolio_analysis._build_prompt([], {}, None)
    assert "```json" in prompt
    assert '"par_ligne"' in prompt
```

- [ ] **Step 17: Lancer les tests pour vérifier qu'ils échouent**

Run: `pytest tests/assistant_ia/test_portfolio_analysis.py -v -k build_prompt`
Expected: FAIL avec `AttributeError: module 'assistant_ia.portfolio_analysis' has no attribute '_build_prompt'`

- [ ] **Step 18: Implémenter `_build_prompt` (et ses deux fonctions auxiliaires de description)**

Ajouter à `assistant_ia/portfolio_analysis.py` :

```python
def _describe_positions(positions_enrichies: list[dict]) -> str:
    lignes = []
    for item in positions_enrichies:
        position = item["position"]
        fiche = item["fiche"]
        ticker = position.get("ticker", "?")
        if "erreur" in fiche:
            lignes.append(
                f"- {ticker} : quantite={position.get('quantity')}, "
                f"prix d'achat={position.get('buy_price')}, "
                f"date d'achat={position.get('buy_date')} -- donnee indice "
                f"indisponible (ticker retire du site)."
            )
            continue
        alertes = fiche.get("alerts") or []
        alertes_txt = ", ".join(a.get("kind", "?") for a in alertes) if alertes else "aucune"
        lignes.append(
            f"- {ticker} ({fiche.get('name')}, {fiche.get('index')}, "
            f"secteur {fiche.get('sector')}) : quantite={position.get('quantity')}, "
            f"prix d'achat={position.get('buy_price')}, "
            f"date d'achat={position.get('buy_date')}, "
            f"score actuel={fiche.get('score')}, alertes actives={alertes_txt}."
        )
    return "\n".join(lignes) if lignes else "Aucune position."


def _describe_metriques(metriques: dict) -> str:
    lignes = []
    totals = metriques.get("totals")
    if isinstance(totals, dict):
        for devise in ("EUR", "USD"):
            t = totals.get(devise)
            if isinstance(t, dict) and (t.get("value") or t.get("pnlAbs")):
                lignes.append(
                    f"Valeur totale {devise} : {t.get('value')}, "
                    f"P&L latent {devise} : {t.get('pnlAbs')}"
                )
    health = metriques.get("health")
    if isinstance(health, dict):
        lignes.append(
            f"Score moyen du portefeuille (parts egales) : {health.get('avgScore')}, "
            f"positions avec alerte risque : {health.get('riskAlertCount')}, "
            f"positions avec actu majeure : {health.get('majorNewsAlertCount')}"
        )
    concentration = metriques.get("concentration")
    if isinstance(concentration, dict):
        if concentration.get("byIndex"):
            lignes.append(f"Repartition par indice : {concentration.get('byIndex')}")
        if concentration.get("bySector"):
            lignes.append(f"Repartition par secteur : {concentration.get('bySector')}")
    dividendes = metriques.get("dividends_received")
    if isinstance(dividendes, dict) and (dividendes.get("EUR") or dividendes.get("USD")):
        lignes.append(f"Dividendes deja encaisses : {dividendes}")
    projection = metriques.get("projected_dividend_income")
    if isinstance(projection, dict):
        lignes.append(f"Revenu de dividendes projete (TTM) : {projection}")
    return "\n".join(lignes) if lignes else "Aucune metrique transmise."


def _build_prompt(positions_enrichies: list[dict], metriques: dict, derniere_analyse: dict | None) -> str:
    if derniere_analyse:
        resume_precedent = derniere_analyse.get("resultat", {}).get("resume_global", "")
        comparaison_consigne = (
            f"Voici le resume de ta derniere analyse de ce portefeuille, du "
            f"{derniere_analyse.get('horodatage')} : \"{resume_precedent}\". Compare "
            f"la situation actuelle a cette derniere analyse dans le champ "
            f"\"comparaison_precedente\" (ce qui a change : nouvelles lignes, lignes "
            f"sorties, evolution du risque ou de l'actualite)."
        )
    else:
        comparaison_consigne = (
            "Aucune analyse precedente de ce portefeuille n'existe : mets le champ "
            "\"comparaison_precedente\" a null."
        )
    return (
        "Tu analyses le portefeuille actions personnel d'un utilisateur du site "
        "\"AI Investment\" (analyse-or). Voici ses positions actuellement detenues, "
        "avec les donnees du site pour chacune :\n\n"
        f"{_describe_positions(positions_enrichies)}\n\n"
        "Metriques globales deja calculees pour ce portefeuille :\n"
        f"{_describe_metriques(metriques)}\n\n"
        f"{comparaison_consigne}\n\n"
        "Pour les lignes qui te semblent les plus exposees (alerte active, forte "
        "concentration, mouvement de prix marque), utilise web_search pour verifier "
        "s'il y a une actualite recente pertinente -- ne cherche pas systematiquement "
        "sur chaque ligne, concentre les recherches sur ce qui le merite vraiment.\n\n"
        "Pour chaque position, donne une recommandation parmi exactement ces quatre "
        "valeurs : \"garder\", \"renforcer\", \"alleger\", \"vendre\", avec une "
        "justification d'une phrase. Donne aussi un resume global de 2 a 3 phrases sur "
        "l'equilibre du portefeuille (concentration, risque, qualite generale), et une "
        "liste des points d'attention (lignes avec une alerte active ou une actualite "
        "negative trouvee).\n\n"
        "Termine ta reponse par un bloc JSON seul, delimite par des balises de bloc de "
        "code json (```json puis ```), exactement sous cette forme :\n"
        "```json\n"
        "{\n"
        "  \"resume_global\": \"...\",\n"
        "  \"points_attention\": [{\"ticker\": \"...\", \"raison\": \"...\"}],\n"
        "  \"par_ligne\": [{\"ticker\": \"...\", \"reco\": \"garder\", \"justification\": \"...\"}],\n"
        "  \"comparaison_precedente\": \"...\"\n"
        "}\n"
        "```"
    )
```

- [ ] **Step 19: Lancer les tests pour vérifier qu'ils passent**

Run: `pytest tests/assistant_ia/test_portfolio_analysis.py -v`
Expected: PASS (21 tests)

- [ ] **Step 20: Commit**

```bash
git add assistant_ia/portfolio_analysis.py tests/assistant_ia/test_portfolio_analysis.py
git commit -m "feat(assistant-ia): construit le prompt d'analyse du portefeuille"
```

- [ ] **Step 21: Écrire les tests de `run_portfolio_analysis`**

Ajouter à `tests/assistant_ia/test_portfolio_analysis.py` :

```python
class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeUsage:
    def __init__(self, input_tokens=100, output_tokens=200):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResponse:
    def __init__(self, content, usage=None):
        self.content = content
        self.usage = usage or _FakeUsage()


class _FakeMessages:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._response


class _FakeAnthropicClient:
    def __init__(self, response=None, error=None):
        self.messages = _FakeMessages(response=response, error=error)


_PAYLOAD_SIMPLE = {
    "positions": [{"ticker": "MC.PA", "quantity": 3, "buy_price": 700.0, "buy_date": "2026-01-01"}],
    "totals": {}, "concentration": {}, "health": {}, "attribution": [],
    "dividends_received": {}, "projected_dividend_income": {},
}


def _reponse_valide(resume="ok"):
    texte = (
        f'```json\n{{"resume_global": "{resume}", "par_ligne": '
        '[{"ticker": "MC.PA", "reco": "garder", "justification": "stable"}]}\n```'
    )
    return _FakeResponse([_FakeTextBlock(texte)])


def test_run_portfolio_analysis_returns_disponible_true_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_analysis.usage, "record_usage", lambda *a, **k: None)
    client = _FakeAnthropicClient(response=_reponse_valide())
    historique = str(tmp_path / "historique.jsonl")

    resultat = portfolio_analysis.run_portfolio_analysis(
        _PAYLOAD_SIMPLE, client=client, indices_source=lambda: {"companies": []},
        history_path=historique)

    assert resultat["disponible"] is True
    assert resultat["resultat"]["resume_global"] == "ok"
    assert resultat["resultat"]["horodatage"]


def test_run_portfolio_analysis_appends_to_history_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_analysis.usage, "record_usage", lambda *a, **k: None)
    client = _FakeAnthropicClient(response=_reponse_valide())
    historique = str(tmp_path / "historique.jsonl")

    portfolio_analysis.run_portfolio_analysis(
        _PAYLOAD_SIMPLE, client=client, indices_source=lambda: {"companies": []},
        history_path=historique)

    assert portfolio_analysis.last_analysis_timestamp(historique) is not None


def test_run_portfolio_analysis_returns_disponible_false_on_api_error(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_analysis.usage, "record_usage", lambda *a, **k: None)
    client = _FakeAnthropicClient(error=RuntimeError("timeout"))
    historique = str(tmp_path / "historique.jsonl")

    resultat = portfolio_analysis.run_portfolio_analysis(
        _PAYLOAD_SIMPLE, client=client, indices_source=lambda: {"companies": []},
        history_path=historique)

    assert resultat["disponible"] is False
    assert "RuntimeError" in resultat["erreur"]
    assert portfolio_analysis.last_analysis_timestamp(historique) is None


def test_run_portfolio_analysis_returns_disponible_false_when_response_not_parseable(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_analysis.usage, "record_usage", lambda *a, **k: None)
    client = _FakeAnthropicClient(response=_FakeResponse([_FakeTextBlock("pas de JSON ici")]))
    historique = str(tmp_path / "historique.jsonl")

    resultat = portfolio_analysis.run_portfolio_analysis(
        _PAYLOAD_SIMPLE, client=client, indices_source=lambda: {"companies": []},
        history_path=historique)

    assert resultat["disponible"] is False
    assert portfolio_analysis.last_analysis_timestamp(historique) is None


def test_run_portfolio_analysis_includes_previous_summary_in_the_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_analysis.usage, "record_usage", lambda *a, **k: None)
    historique = tmp_path / "historique.jsonl"
    historique.write_text(
        json.dumps({"horodatage": "2026-11-05T00:00:00Z",
                    "resultat": {"resume_global": "analyse precedente unique"}}) + "\n",
        encoding="utf-8")
    client = _FakeAnthropicClient(response=_reponse_valide())

    portfolio_analysis.run_portfolio_analysis(
        _PAYLOAD_SIMPLE, client=client, indices_source=lambda: {"companies": []},
        history_path=str(historique))

    prompt_envoye = client.messages.last_kwargs["messages"][0]["content"]
    assert "analyse precedente unique" in prompt_envoye


def test_run_portfolio_analysis_calls_record_usage_with_the_response_tokens(tmp_path, monkeypatch):
    enregistres = []
    monkeypatch.setattr(portfolio_analysis.usage, "record_usage",
                         lambda i, o, model=None: enregistres.append((i, o, model)))
    client = _FakeAnthropicClient(response=_reponse_valide())
    historique = str(tmp_path / "historique.jsonl")

    portfolio_analysis.run_portfolio_analysis(
        _PAYLOAD_SIMPLE, client=client, indices_source=lambda: {"companies": []},
        history_path=historique)

    assert enregistres == [(100, 200, portfolio_analysis.MODEL)]
```

- [ ] **Step 22: Lancer les tests pour vérifier qu'ils échouent**

Run: `pytest tests/assistant_ia/test_portfolio_analysis.py -v -k run_portfolio_analysis`
Expected: FAIL avec `AttributeError: module 'assistant_ia.portfolio_analysis' has no attribute 'run_portfolio_analysis'`

- [ ] **Step 23: Implémenter `run_portfolio_analysis`**

Ajouter à `assistant_ia/portfolio_analysis.py` :

```python
def run_portfolio_analysis(
    payload: dict, *, client=None, indices_source=data_sources.fetch_indices_data,
    history_path: str = HISTORY_PATH,
) -> dict:
    """Point d'entree appele par la route POST /portfolio-analysis.
    `client` injectable (jamais de vrai appel reseau dans les tests, meme
    principe que assistant.run_assistant_loop). Toute panne reseau/API est
    capturee ici, jamais propagee -- la route n'a jamais a gerer une
    exception venant de ce module."""
    positions = payload.get("positions") or []
    metriques = {
        "totals": payload.get("totals"),
        "concentration": payload.get("concentration"),
        "health": payload.get("health"),
        "attribution": payload.get("attribution"),
        "dividends_received": payload.get("dividends_received"),
        "projected_dividend_income": payload.get("projected_dividend_income"),
    }
    positions_enrichies = _enrichit_positions(positions, indices_source=indices_source)
    derniere_analyse = _lit_derniere_analyse(history_path)
    prompt = _build_prompt(positions_enrichies, metriques, derniere_analyse)

    if client is None:
        import anthropic
        client = anthropic.Anthropic(timeout=REQUEST_TIMEOUT_SECONDS, max_retries=0)

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            output_config={"effort": "medium"},
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": WEB_SEARCH_MAX_USES}],
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        return {"disponible": False, "erreur": f"{type(e).__name__}: {e}", "resultat": None}

    usage.record_usage(response.usage.input_tokens, response.usage.output_tokens, model=MODEL)
    texte = "".join(bloc.text for bloc in response.content if bloc.type == "text")
    resultat = _parse_resultat(texte)
    if resultat is None:
        return {"disponible": False, "erreur": "reponse IA non interpretable, reessaie", "resultat": None}

    resultat["horodatage"] = now_iso()
    _append_history({
        "horodatage": resultat["horodatage"],
        "positions_snapshot": positions,
        "resultat": resultat,
    }, history_path)
    return {"disponible": True, "erreur": None, "resultat": resultat}
```

- [ ] **Step 24: Lancer tous les tests du fichier pour vérifier qu'ils passent**

Run: `pytest tests/assistant_ia/test_portfolio_analysis.py -v`
Expected: PASS (27 tests)

- [ ] **Step 25: Commit**

```bash
git add assistant_ia/portfolio_analysis.py tests/assistant_ia/test_portfolio_analysis.py
git commit -m "feat(assistant-ia): run_portfolio_analysis -- point d'entree de l'analyse IA du portefeuille"
```

---

### Task 2: Routes `POST /portfolio-analysis` et `GET /portfolio-analysis/last`

**Files:**
- Modify: `assistant_ia/api.py`
- Modify: `tests/assistant_ia/test_api.py`

**Interfaces:**
- Consomme (Task 1) : `portfolio_analysis.run_portfolio_analysis(payload, *, client=None, indices_source=..., history_path=...) -> dict`,
  `portfolio_analysis.last_analysis_timestamp(path=...) -> str | None`,
  `portfolio_analysis.MAX_POSITIONS`.
- Produit : routes HTTP `POST /portfolio-analysis` et
  `GET /portfolio-analysis/last`, consommées par la Task 4 (frontend).

- [ ] **Step 1: Écrire les tests de route (ils échoueront : les routes n'existent pas encore)**

Ajouter à `tests/assistant_ia/test_api.py` :

```python
def test_portfolio_analysis_rejects_a_missing_token(client):
    response = client.post("/portfolio-analysis", json={"positions": [{"ticker": "MC.PA"}]})
    assert response.status_code == 401


def test_portfolio_analysis_rejects_a_wrong_token(client):
    response = client.post(
        "/portfolio-analysis", json={"positions": [{"ticker": "MC.PA"}]},
        headers={"X-Bot-Token": "mauvais"})
    assert response.status_code == 401


def test_portfolio_analysis_rejects_empty_positions(client, monkeypatch):
    monkeypatch.setattr(api.usage, "budget_exceeded", lambda **kwargs: False)
    response = client.post(
        "/portfolio-analysis", json={"positions": []},
        headers={"X-Bot-Token": "secret-token"})
    assert response.status_code == 400


def test_portfolio_analysis_rejects_missing_positions_field(client, monkeypatch):
    monkeypatch.setattr(api.usage, "budget_exceeded", lambda **kwargs: False)
    response = client.post(
        "/portfolio-analysis", json={}, headers={"X-Bot-Token": "secret-token"})
    assert response.status_code == 400


def test_portfolio_analysis_rejects_too_many_positions(client, monkeypatch):
    monkeypatch.setattr(api.usage, "budget_exceeded", lambda **kwargs: False)
    trop = [{"ticker": f"T{i}"} for i in range(api.portfolio_analysis.MAX_POSITIONS + 1)]
    response = client.post(
        "/portfolio-analysis", json={"positions": trop},
        headers={"X-Bot-Token": "secret-token"})
    assert response.status_code == 400


def test_portfolio_analysis_refuses_when_the_daily_budget_is_exceeded(client, monkeypatch):
    monkeypatch.setattr(api.usage, "budget_exceeded", lambda **kwargs: True)
    response = client.post(
        "/portfolio-analysis", json={"positions": [{"ticker": "MC.PA"}]},
        headers={"X-Bot-Token": "secret-token"})
    assert response.status_code == 429


def test_portfolio_analysis_returns_the_result_from_run_portfolio_analysis(client, monkeypatch):
    monkeypatch.setattr(api.usage, "budget_exceeded", lambda **kwargs: False)
    attendu = {"disponible": True, "erreur": None, "resultat": {"resume_global": "ok"}}
    monkeypatch.setattr(api.portfolio_analysis, "run_portfolio_analysis", lambda payload: attendu)

    response = client.post(
        "/portfolio-analysis", json={"positions": [{"ticker": "MC.PA"}]},
        headers={"X-Bot-Token": "secret-token"})

    assert response.status_code == 200
    assert response.json() == attendu


def test_portfolio_analysis_last_rejects_a_missing_token(client):
    response = client.get("/portfolio-analysis/last")
    assert response.status_code == 401


def test_portfolio_analysis_last_returns_null_without_history(client, monkeypatch):
    monkeypatch.setattr(api.portfolio_analysis, "last_analysis_timestamp", lambda **kwargs: None)
    response = client.get("/portfolio-analysis/last", headers={"X-Bot-Token": "secret-token"})
    assert response.status_code == 200
    assert response.json() == {"horodatage": None}


def test_portfolio_analysis_last_returns_the_stored_timestamp(client, monkeypatch):
    monkeypatch.setattr(api.portfolio_analysis, "last_analysis_timestamp", lambda **kwargs: "2026-11-11T00:00:00Z")
    response = client.get("/portfolio-analysis/last", headers={"X-Bot-Token": "secret-token"})
    assert response.json() == {"horodatage": "2026-11-11T00:00:00Z"}
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `pytest tests/assistant_ia/test_api.py -v -k portfolio_analysis`
Expected: FAIL avec des `404` (routes inexistantes) ou `AttributeError: module 'assistant_ia.api' has no attribute 'portfolio_analysis'`

- [ ] **Step 3: Ajouter les routes dans `assistant_ia/api.py`**

Ajouter l'import en haut du fichier, après les imports `assistant_ia.*`
existants (après la ligne `import assistant_ia.usage as usage`) :

```python
import assistant_ia.portfolio_analysis as portfolio_analysis
```

Ajouter après le bloc `_concurrence_analyse`/`_frequence_analyse` existant
(après la ligne `_frequence_analyse = limits.RateLimiter(ANALYSE_RATE_MAX_PAR_MINUTE, 60)`) :

```python
# Garde-fous dedies a /portfolio-analysis -- separes de /ask et
# /financial-analysis (profil d'abus different : un seul utilisateur
# legitime derriere le jeton, mais un appel plus lourd -- plusieurs
# tickers, recherche web multiple -- qu'une question simple de /ask).
PORTFOLIO_MAX_CONCURRENT = int(os.environ.get("AI_PORTFOLIO_ANALYSIS_MAX_CONCURRENT", "1"))
PORTFOLIO_RATE_MAX_PAR_MINUTE = int(os.environ.get("AI_PORTFOLIO_ANALYSIS_RATE_PAR_MINUTE", "5"))

_concurrence_portefeuille = limits.ConcurrencyLimiter(PORTFOLIO_MAX_CONCURRENT)
_frequence_portefeuille = limits.RateLimiter(PORTFOLIO_RATE_MAX_PAR_MINUTE, 60)
```

Ajouter les deux routes en fin de fichier, après la route
`financial_analysis` existante :

```python
@app.post("/portfolio-analysis")
async def portfolio_analysis_route(request: Request, x_bot_token: str | None = Header(default=None)):
    _check_token(x_bot_token)
    if usage.budget_exceeded():
        raise HTTPException(status_code=429, detail="Plafond quotidien de l'assistant atteint, reessaie demain")
    adresse = request.client.host if request.client else "inconnue"
    if not _frequence_portefeuille.autorise(adresse):
        raise HTTPException(status_code=429, detail="Trop de demandes en peu de temps : reessaie dans une minute.")
    payload = await request.json()
    positions = payload.get("positions")
    if not isinstance(positions, list) or not positions:
        raise HTTPException(status_code=400, detail="Aucune position fournie.")
    if len(positions) > portfolio_analysis.MAX_POSITIONS:
        raise HTTPException(status_code=400, detail=f"Trop de positions ({portfolio_analysis.MAX_POSITIONS} maximum).")
    if not _concurrence_portefeuille.tente():
        raise HTTPException(status_code=503, detail="Une analyse est deja en cours : reessaie dans un instant.")
    try:
        return portfolio_analysis.run_portfolio_analysis(payload)
    finally:
        _concurrence_portefeuille.libere()


@app.get("/portfolio-analysis/last")
def portfolio_analysis_last(x_bot_token: str | None = Header(default=None)):
    _check_token(x_bot_token)
    return {"horodatage": portfolio_analysis.last_analysis_timestamp()}
```

- [ ] **Step 4: Lancer tous les tests de `test_api.py` pour vérifier qu'ils passent**

Run: `pytest tests/assistant_ia/test_api.py -v`
Expected: PASS (tous les tests existants + les 10 nouveaux)

- [ ] **Step 5: Commit**

```bash
git add assistant_ia/api.py tests/assistant_ia/test_api.py
git commit -m "feat(assistant-ia): routes POST /portfolio-analysis et GET /portfolio-analysis/last"
```

---

### Task 3: `buildPortfolioAnalysisPayload` dans `docs/portfolio.js`

**Files:**
- Modify: `docs/portfolio.js`
- Modify: `docs/portfolio.test.js`

**Interfaces:**
- Consomme : rien de nouveau (fonction pure sans dépendance).
- Produit (consommé par la Task 4) :
  `buildPortfolioAnalysisPayload(positions, totals, concentration, health, attribution, dividendsReceived, projectedDividendIncome) -> object`
  renvoyant `{positions, totals, concentration, health, attribution, dividends_received, projected_dividend_income}`.

- [ ] **Step 1: Écrire le test (il échouera : la fonction n'existe pas encore)**

Ajouter à `docs/portfolio.test.js`, dans l'import en haut du fichier
(ajouter `buildPortfolioAnalysisPayload` à la liste déstructurée existante) :

```js
const {
  PORTFOLIO_STORAGE_KEY, validatePositionInput, createPosition,
  loadPortfolio, savePortfolio, addPosition, updatePosition, removePosition,
  computePositionPnL, computePortfolioTotals, findOpportunities,
  computePortfolioConcentration, computePortfolioHealth, computePortfolioAttribution,
  PORTFOLIO_CLOSED_STORAGE_KEY, loadClosedPortfolio, saveClosedPortfolio, closePosition,
  groupPriceHistoryByTicker, priceAtOrBefore, isPositionActiveOn, realValueForPosition,
  benchmarkValueForPosition, computePerformanceCurve, computePortfolioPerformanceCurves,
  computeBenchmarkPerformanceCurve,
  groupDividendHistoryByTicker, computeDividendsReceived, computeProjectedDividendIncome,
  computeYieldOnCost,
  resampleCurveWeekly, computePeriodicChanges, computePortfolioVolatility,
  computeMaxDrawdown, computeSharpeRatio, computePortfolioRiskMetrics,
  buildPortfolioAnalysisPayload,
} = require('./portfolio.js');
```

Ajouter une fonction de test et son appel dans `main()` (suivre le
patron déjà en place : chaque `test_*` est une fonction appelée
explicitement dans `main()`, voir la fin du fichier) :

```js
function test_build_portfolio_analysis_payload_maps_dividend_keys_to_snake_case() {
  const positions = [{ id: 'p1', ticker: 'MC.PA', quantity: 3, buy_price: 700, buy_date: '2026-01-01' }];
  const totals = { EUR: { value: 2100, pnlAbs: 0 }, USD: { value: 0, pnlAbs: 0 } };
  const concentration = { totalPositions: 1, byIndex: { CAC40: 1 }, bySector: {}, byCurrency: { EUR: 2100 } };
  const health = { avgScore: 10, scoredPositions: 1, riskAlertCount: 0, majorNewsAlertCount: 0 };
  const attribution = [{ ticker: 'MC.PA', name: 'LVMH', index: 'CAC40', pnlAbs: 0, pnlPct: 0 }];
  const dividendsReceived = { EUR: 15, USD: 0 };
  const projectedDividendIncome = { EUR: { annual: 20, monthly: 1.67 }, USD: { annual: 0, monthly: 0 } };

  const payload = buildPortfolioAnalysisPayload(
    positions, totals, concentration, health, attribution,
    dividendsReceived, projectedDividendIncome);

  assert.deepStrictEqual(payload.positions, positions);
  assert.deepStrictEqual(payload.totals, totals);
  assert.deepStrictEqual(payload.concentration, concentration);
  assert.deepStrictEqual(payload.health, health);
  assert.deepStrictEqual(payload.attribution, attribution);
  assert.deepStrictEqual(payload.dividends_received, dividendsReceived);
  assert.deepStrictEqual(payload.projected_dividend_income, projectedDividendIncome);
  assert.strictEqual(payload.dividendsReceived, undefined);
  console.log('OK: test_build_portfolio_analysis_payload_maps_dividend_keys_to_snake_case');
}
```

Ajouter l'appel `test_build_portfolio_analysis_payload_maps_dividend_keys_to_snake_case();`
dans `main()`, à la suite des autres appels `test_*()`.

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

Run: `node docs/portfolio.test.js`
Expected: `TypeError: buildPortfolioAnalysisPayload is not a function`

- [ ] **Step 3: Implémenter `buildPortfolioAnalysisPayload` dans `docs/portfolio.js`**

Ajouter juste avant le bloc `if (typeof module !== 'undefined' ...)` en
fin de fichier :

```js
/**
 * Assemble le payload envoye a POST /portfolio-analysis -- une seule
 * fonction pour que index.html n'ait jamais a construire cet objet a la
 * main (donc jamais a desynchroniser ses cles de celles lues par
 * assistant_ia/portfolio_analysis.py).
 */
function buildPortfolioAnalysisPayload(
  positions, totals, concentration, health, attribution,
  dividendsReceived, projectedDividendIncome,
) {
  return {
    positions, totals, concentration, health, attribution,
    dividends_received: dividendsReceived,
    projected_dividend_income: projectedDividendIncome,
  };
}
```

Ajouter `buildPortfolioAnalysisPayload` à la liste exportée dans le bloc
`module.exports` existant (après `computePortfolioRiskMetrics,`) :

```js
    resampleCurveWeekly, computePeriodicChanges, computePortfolioVolatility,
    computeMaxDrawdown, computeSharpeRatio, computePortfolioRiskMetrics,
    buildPortfolioAnalysisPayload,
```

- [ ] **Step 4: Lancer le test pour vérifier qu'il passe**

Run: `node docs/portfolio.test.js`
Expected: `Tous les tests portfolio.test.js sont passés.`

- [ ] **Step 5: Commit**

```bash
git add docs/portfolio.js docs/portfolio.test.js
git commit -m "feat(portfolio): buildPortfolioAnalysisPayload pour /portfolio-analysis"
```

---

### Task 4: Section carrousel "Analyse IA" dans `docs/index.html`

**Files:**
- Modify: `docs/index.html`

**Interfaces:**
- Consomme (Task 2) : contrat de réponse `POST /portfolio-analysis` et
  `GET /portfolio-analysis/last` (voir spec, §Contrat API) ; (Task 3)
  `buildPortfolioAnalysisPayload(...)` ; (existant) `computePortfolioConcentration`,
  `computePortfolioHealth`, `computePortfolioAttribution`,
  `computeDividendsReceived`, `computeProjectedDividendIncome`,
  `getAiAssistantToken()` (ligne ~3155), `escHtml`, `formatPct`.
- Produit : rien consommé par une tâche suivante (dernière tâche
  fonctionnelle du plan).

Pas de test unitaire pour cette tâche (convention déjà établie : le rendu/
branchement DOM de `index.html` n'est jamais testé unitairement — voir
Global Constraints). La vérification se fait manuellement (Step 3).

- [ ] **Step 1: Ajouter la nouvelle section au carrousel dans `renderPortfolioActionsTab`**

Dans `docs/index.html`, repérer `function renderPortfolioActionsTab` et le
tableau `portfolioActionsSections` qu'elle construit. Insérer la nouvelle
entrée entre la section `'Analyse'` et la section `'Bot Actions'` :

```js
    {
      title: 'Analyse IA',
      desc: 'Lance une analyse par l\'assistant IA : vue d\'ensemble, points d\'attention et une recommandation (garder/renforcer/alléger/vendre) pour chaque ligne détenue.',
      html: portfolioAiAnalysisSectionHtml(),
      wire: () => wirePortfolioAiAnalysisSection(positions, closedPositions, companiesByTicker, currencyByIndex, dividendHistoryByTicker),
    },
```

(`positions`, `closedPositions`, `companiesByTicker`, `currencyByIndex`,
`dividendHistoryByTicker` sont déjà des variables locales de
`renderPortfolioActionsTab`, calculées en haut de la fonction — aucune
nouvelle variable à introduire à cet endroit.)

- [ ] **Step 2: Ajouter les deux nouvelles fonctions, juste après `portfolioAnalysisHtml`**

```js
function portfolioAiAnalysisSectionHtml() {
  return `
    <div id="portfolioAiFreshness" class="hero-sub">Chargement…</div>
    <button class="toggle-btn" type="button" id="portfolioAiAnalysisBtn">Lancer l'analyse IA</button>
    <div id="portfolioAiAnalysisResult"></div>
  `;
}

function _portfolioAiFreshnessLabel(horodatage) {
  if (!horodatage) return 'Aucune analyse encore.';
  const jours = Math.floor((Date.now() - new Date(horodatage).getTime()) / 86400000);
  if (jours <= 0) return 'Dernière analyse : aujourd\'hui.';
  return `Dernière analyse : il y a ${jours} jour${jours > 1 ? 's' : ''}.`;
}

function _portfolioAiRecoLabel(reco) {
  const labels = { garder: 'Garder', renforcer: 'Renforcer', alleger: 'Alléger', vendre: 'Vendre', a_verifier: 'À vérifier' };
  return labels[reco] || reco;
}

function _portfolioAiAnalysisResultHtml(resultat) {
  const pointsHtml = (resultat.points_attention || []).length
    ? (resultat.points_attention || []).map(p => `
        <div class="price-row"><span class="price-label">${escHtml(p.ticker)}</span><span class="price-value">${escHtml(p.raison)}</span></div>`).join('')
    : '<div class="list-empty">Aucun point d\'attention particulier.</div>';
  const ligneHtml = (resultat.par_ligne || []).map(l => `
      <div class="price-row">
        <span class="price-label"><a href="#indices/${encodeURIComponent(l.ticker)}">${escHtml(l.ticker)}</a> — ${_portfolioAiRecoLabel(l.reco)}</span>
        <span class="price-value">${escHtml(l.justification || '')}</span>
      </div>`).join('');
  const comparaisonHtml = resultat.comparaison_precedente
    ? `<h3 class="portfolio-analysis-title">Depuis la dernière fois</h3><div class="hero-sub">${escHtml(resultat.comparaison_precedente)}</div>`
    : '';
  return `
    <h3 class="portfolio-analysis-title">Résumé</h3>
    <div class="hero-sub">${escHtml(resultat.resume_global || '')}</div>
    <h3 class="portfolio-analysis-title">Points d'attention</h3>
    <div class="signal-stats">${pointsHtml}</div>
    <h3 class="portfolio-analysis-title">Recommandations par ligne</h3>
    <div class="signal-stats">${ligneHtml}</div>
    ${comparaisonHtml}
  `;
}

function wirePortfolioAiAnalysisSection(positions, closedPositions, companiesByTicker, currencyByIndex, dividendHistoryByTicker) {
  const freshnessEl = document.getElementById('portfolioAiFreshness');
  const btn = document.getElementById('portfolioAiAnalysisBtn');
  const resultEl = document.getElementById('portfolioAiAnalysisResult');
  if (!btn || !resultEl) return;

  const refreshFreshness = async (token) => {
    if (!freshnessEl) return;
    try {
      const res = await fetch('https://goldbot.fr:8445/portfolio-analysis/last', {
        headers: { 'X-Bot-Token': token },
      });
      if (!res.ok) { freshnessEl.textContent = ''; return; }
      const data = await res.json();
      freshnessEl.textContent = _portfolioAiFreshnessLabel(data.horodatage);
    } catch (e) {
      freshnessEl.textContent = '';
    }
  };

  // Jamais de prompt() force a l'ouverture du panneau (meme convention
  // que fetchIbkrBotDashboard, L3239) : on ne lit le jeton que s'il est
  // deja memorise, le bouton seul declenche getAiAssistantToken().
  const tokenDejaMemorise = localStorage.getItem('aiAssistantToken');
  if (tokenDejaMemorise) refreshFreshness(tokenDejaMemorise);
  else if (freshnessEl) freshnessEl.textContent = 'Aucune analyse encore.';

  btn.addEventListener('click', async () => {
    const jeton = getAiAssistantToken();
    if (!jeton) return;
    btn.disabled = true;
    const texteInitial = btn.textContent;
    btn.textContent = 'Analyse en cours…';
    resultEl.innerHTML = '';
    try {
      const concentration = computePortfolioConcentration(positions, companiesByTicker, currencyByIndex);
      const health = computePortfolioHealth(positions, companiesByTicker);
      const attribution = computePortfolioAttribution(positions, companiesByTicker);
      const dividendsReceived = computeDividendsReceived(
        [...positions, ...closedPositions], dividendHistoryByTicker, companiesByTicker, currencyByIndex);
      const projectedIncome = computeProjectedDividendIncome(
        positions, dividendHistoryByTicker, companiesByTicker, currencyByIndex);
      const totals = computePortfolioTotals(positions, companiesByTicker, currencyByIndex);
      const payload = buildPortfolioAnalysisPayload(
        positions, totals, concentration, health, attribution, dividendsReceived, projectedIncome);

      const res = await fetch('https://goldbot.fr:8445/portfolio-analysis', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Bot-Token': jeton },
        body: JSON.stringify(payload),
      });
      if (res.status === 401) {
        localStorage.removeItem('aiAssistantToken');
        resultEl.innerHTML = '<div class="empty">Ton jeton d\'accès est invalide. Recharge la page pour en saisir un nouveau.</div>';
        return;
      }
      if (res.status === 429 || res.status === 503 || res.status === 400) {
        let detail = "L'analyse n'est pas possible pour le moment.";
        try { const corps = await res.json(); if (corps && corps.detail) detail = corps.detail; } catch (e) { /* message par défaut */ }
        resultEl.innerHTML = `<div class="empty">${escHtml(detail)}</div>`;
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!data.disponible) {
        resultEl.innerHTML = `<div class="empty">${escHtml(data.erreur || "Analyse indisponible pour le moment.")}</div>`;
        return;
      }
      resultEl.innerHTML = _portfolioAiAnalysisResultHtml(data.resultat);
      if (freshnessEl) freshnessEl.textContent = _portfolioAiFreshnessLabel(data.resultat.horodatage);
    } catch (e) {
      resultEl.innerHTML = '<div class="empty">Analyse indisponible pour le moment.</div>';
    } finally {
      btn.disabled = false;
      btn.textContent = texteInitial;
    }
  });
}
```

- [ ] **Step 3: Vérification manuelle**

Ouvrir `docs/index.html` dans un navigateur local (ou via le serveur de
dev déjà utilisé pour ce projet), aller sur l'onglet "Actions" → ajouter
au moins une position → section "Analyse IA" du carrousel → vérifier
que :
- l'indicateur de fraîcheur affiche "Aucune analyse encore." au premier
  passage (pas de jeton encore saisi, ou historique vide) ;
- le clic sur "Lancer l'analyse IA" demande le jeton (`prompt()`) la
  première fois, puis appelle `POST https://goldbot.fr:8445/portfolio-analysis`
  (vérifiable dans l'onglet réseau du navigateur) ;
- un jeton invalide produit le message "jeton d'accès invalide" et
  réinitialise `localStorage.aiAssistantToken` ;
- une réponse `disponible: true` simulée (ou réelle si le service est
  déployé) affiche résumé, points d'attention, et recommandations par
  ligne.

- [ ] **Step 4: Commit**

```bash
git add docs/index.html
git commit -m "feat(portfolio): section Analyse IA dans l'onglet Actions"
```

---

### Task 5: `.gitignore` — historique jamais commité

**Files:**
- Modify: `.gitignore`

**Interfaces:** aucune (fichier de configuration, pas de code).

- [ ] **Step 1: Ajouter l'entrée**

Ajouter à `.gitignore`, à la suite de l'entrée existante
`indices_ai_usage_today.json` :

```
# Historique des analyses IA du portefeuille manuel : genere uniquement
# sur le VPS (assistant_ia/portfolio_analysis.py), jamais une donnee de
# reference a versionner -- meme famille que indices_ai_usage_today.json.
assistant_ia/portfolio_analysis_history.jsonl
```

- [ ] **Step 2: Vérifier qu'un fichier à ce chemin serait bien ignoré**

Run: `touch assistant_ia/portfolio_analysis_history.jsonl && git status --short`
Expected: le fichier n'apparaît PAS dans la sortie (ignoré) ; nettoyer
ensuite avec `rm assistant_ia/portfolio_analysis_history.jsonl`.

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: ignore l'historique des analyses IA du portefeuille"
```

---

## Vérification finale

Après la Task 5, lancer la suite complète pour confirmer qu'aucune
régression n'a été introduite :

```bash
pytest tests/ -q
node docs/portfolio.test.js
```

Puis suivre `superpowers:finishing-a-development-branch` pour décider de
l'intégration de la branche `feature/portfolio-ai-analysis-onglet-actions`
dans `main` (demande explicite de l'utilisateur : jamais un merge
automatique, en discuter avec lui d'abord).
