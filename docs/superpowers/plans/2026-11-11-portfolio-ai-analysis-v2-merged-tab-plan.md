# Analyse IA du portefeuille v2 — fusion d'onglet + cartes + courbe d'évolution — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Réécrire le contrat JSON de l'analyse IA du portefeuille (score
`sante_globale`, `analyse` par ligne au lieu d'une phrase, suppression de
`points_attention`), exposer l'historique complet, et fusionner les deux
sections carrousel "Analyse"/"Analyse IA" en une seule avec cartes par
entreprise et courbe d'évolution du jugement IA.

**Architecture:** Même serveur (`assistant_ia/`), même séparation calcul JS
pur / jugement IA Python que la v1. Les deux routes existantes évoluent
(`/last` renvoie maintenant le résultat complet) et une route de lecture
s'ajoute (`/history`, légère, pas d'appel IA). Côté site, la section
carrousel "Analyse IA" disparaît, son contenu rejoint "Analyse".

**Tech Stack:** Python/FastAPI (`assistant_ia/`), SDK `anthropic`, JS natif
sans framework (`docs/index.html`), tests via `pytest` et `node
docs/portfolio.test.js`.

**Spec:** `docs/superpowers/specs/2026-11-11-portfolio-ai-analysis-v2-merged-tab-design.md`
(étend `docs/superpowers/specs/2026-11-11-portfolio-ai-analysis-design.md`, v1, déjà en prod)

## Global Constraints

- Contrat JSON v2 du résultat IA : `resume_global` (str), `sante_globale`
  (int -100..100, optionnel — `None` si absent/invalide, jamais une erreur
  de parsing globale), `par_ligne` (liste de `{ticker, reco, analyse}`,
  `analyse` remplace `justification`), `comparaison_precedente`
  (str|None), `horodatage` (ajouté par le serveur). `points_attention` est
  **supprimé** du contrat (son contenu vit désormais dans `analyse` de
  chaque ligne).
- `GET /portfolio-analysis/last` renvoie `{"disponible": bool, "resultat":
  dict|None}` (plus l'ancien `{"horodatage": ...}`) — rétro-incompatible,
  acceptable (aucun client externe).
- `GET /portfolio-analysis/history` (nouvelle) renvoie `{"historique":
  [{"horodatage": str, "sante_globale": int|None}, ...]}`, triée
  chronologiquement (plus ancienne en premier), lecture seule, pas d'appel
  IA, token requis, pas de limite de débit dédiée au-delà du jeton.
- `last_analysis_timestamp` est supprimée (remplacée partout par
  `derniere_analyse_complete`).
- Aucune fonction de `docs/portfolio.js` ne change dans ce chantier.
- `docs/index.html` : les fonctions de rendu/branchement DOM ne sont
  **pas** testées unitairement (convention déjà établie) — vérification
  manuelle en navigateur à la place.
- Travail sur la branche `feature/portfolio-ai-analysis-v2-merged-tab`
  (worktree déjà en place), jamais sur `main`.

---

### Task 1: Nouveau contrat dans `assistant_ia/portfolio_analysis.py`

**Files:**
- Modify: `assistant_ia/portfolio_analysis.py`
- Modify: `tests/assistant_ia/test_portfolio_analysis.py`

**Interfaces:**
- Consomme : rien de nouveau (mêmes dépendances que v1 : `tools`,
  `data_sources`, `usage`).
- Produit (consommé par la Task 2) :
  `derniere_analyse_complete(path: str = HISTORY_PATH) -> dict | None`
  (le `resultat` complet, pas seulement l'horodatage) ;
  `historique_sante(path: str = HISTORY_PATH, limite: int = 200) -> list[dict]`
  (liste chronologique de `{"horodatage": str, "sante_globale": int | None}`).
  `_parse_resultat`/`_build_prompt`/`run_portfolio_analysis` gardent leurs
  signatures existantes, mais `_parse_resultat` ne produit plus
  `points_attention` et valide/normalise `sante_globale` ;
  `_build_prompt` demande `analyse` (pas `justification`) et
  `sante_globale` dans le bloc JSON.

- [ ] **Step 1: Mettre à jour les tests obsolètes de `_parse_resultat` et ajouter les tests du nouveau champ `sante_globale`**

Dans `tests/assistant_ia/test_portfolio_analysis.py`, remplacer
`test_parse_resultat_fills_in_missing_optional_keys` par :

```python
def test_parse_resultat_fills_in_missing_optional_keys():
    texte = '```json\n{"resume_global": "ok", "par_ligne": []}\n```'
    resultat = portfolio_analysis._parse_resultat(texte)
    assert resultat["comparaison_precedente"] is None
    assert resultat["sante_globale"] is None


def test_parse_resultat_keeps_a_valid_sante_globale():
    texte = '```json\n{"resume_global": "ok", "par_ligne": [], "sante_globale": 42}\n```'
    resultat = portfolio_analysis._parse_resultat(texte)
    assert resultat["sante_globale"] == 42


def test_parse_resultat_replaces_a_non_integer_sante_globale_with_none():
    texte = '```json\n{"resume_global": "ok", "par_ligne": [], "sante_globale": "haute"}\n```'
    resultat = portfolio_analysis._parse_resultat(texte)
    assert resultat["sante_globale"] is None


def test_parse_resultat_replaces_a_boolean_sante_globale_with_none():
    # bool est un sous-type de int en Python -- piege deja connu dans ce
    # fichier (voir les gardes isinstance(x, (int, float)) and not
    # isinstance(x, bool) ailleurs) : True/False ne doivent jamais passer
    # pour un score valide.
    texte = '```json\n{"resume_global": "ok", "par_ligne": [], "sante_globale": true}\n```'
    resultat = portfolio_analysis._parse_resultat(texte)
    assert resultat["sante_globale"] is None
```

Modifier `test_parse_resultat_extracts_the_last_json_block` pour retirer la
clé `points_attention` du texte d'entrée (elle n'est plus un champ du
contrat) :

```python
def test_parse_resultat_extracts_the_last_json_block():
    texte = (
        "Du texte de raisonnement avant.\n"
        "```json\n"
        '{"resume_global": "ok", "par_ligne": []}\n'
        "```\n"
    )
    resultat = portfolio_analysis._parse_resultat(texte)
    assert resultat["resume_global"] == "ok"
    assert resultat["par_ligne"] == []
```

Dans les trois tests `test_parse_resultat_normalizes_reco_case_and_whitespace`,
`test_parse_resultat_replaces_an_invalid_reco_value_with_a_verifier` et
`test_parse_resultat_removes_non_dict_par_ligne_entries`, remplacer la clé
`"justification"` par `"analyse"` dans le texte JSON d'entrée (et dans
l'assertion de `test_parse_resultat_removes_non_dict_par_ligne_entries`
qui compare le dict attendu) :

```python
def test_parse_resultat_normalizes_reco_case_and_whitespace():
    texte = (
        '```json\n{"resume_global": "ok", "par_ligne": '
        '[{"ticker": "MC.PA", "reco": " Garder ", "analyse": "x"}]}\n```'
    )
    resultat = portfolio_analysis._parse_resultat(texte)
    assert resultat["par_ligne"][0]["reco"] == "garder"


def test_parse_resultat_replaces_an_invalid_reco_value_with_a_verifier():
    texte = (
        '```json\n{"resume_global": "ok", "par_ligne": '
        '[{"ticker": "MC.PA", "reco": "acheter_plus", "analyse": "x"}]}\n```'
    )
    resultat = portfolio_analysis._parse_resultat(texte)
    assert resultat["par_ligne"][0]["reco"] == "a_verifier"


def test_parse_resultat_removes_non_dict_par_ligne_entries():
    texte = (
        '```json\n{"resume_global": "ok", "par_ligne": '
        '[{"ticker": "MC.PA", "reco": "garder", "analyse": "x"}, "entree parasite"]}\n```'
    )
    resultat = portfolio_analysis._parse_resultat(texte)
    assert resultat["par_ligne"] == [{"ticker": "MC.PA", "reco": "garder", "analyse": "x"}]
    assert "entree parasite" not in resultat["par_ligne"]
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `pytest tests/assistant_ia/test_portfolio_analysis.py -v -k parse_resultat`
Expected: FAIL (les tests `sante_globale` échouent avec `KeyError`/`AssertionError` ; `test_parse_resultat_fills_in_missing_optional_keys` échoue avec `KeyError: 'sante_globale'`)

- [ ] **Step 3: Modifier `_parse_resultat` dans `assistant_ia/portfolio_analysis.py`**

Remplacer la fonction actuelle par :

```python
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
    parsed.setdefault("comparaison_precedente", None)
    sante = parsed.get("sante_globale")
    if not isinstance(sante, (int, float)) or isinstance(sante, bool):
        sante = None
    elif isinstance(sante, float):
        sante = int(sante)
    parsed["sante_globale"] = sante
    if not isinstance(parsed["par_ligne"], list):
        return None
    parsed["par_ligne"] = [ligne for ligne in parsed["par_ligne"] if isinstance(ligne, dict)]
    for ligne in parsed["par_ligne"]:
        reco = ligne.get("reco")
        if isinstance(reco, str):
            reco = reco.strip().lower()
            ligne["reco"] = reco if reco in RECO_VALIDES else "a_verifier"
        else:
            ligne["reco"] = "a_verifier"
    return parsed
```

(`points_attention` n'est plus défini/retiré : `parsed.setdefault("points_attention", [])`
a disparu.)

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `pytest tests/assistant_ia/test_portfolio_analysis.py -v -k parse_resultat`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add assistant_ia/portfolio_analysis.py tests/assistant_ia/test_portfolio_analysis.py
git commit -m "feat(assistant-ia): contrat v2 -- sante_globale, analyse par ligne, retrait de points_attention"
```

- [ ] **Step 6: Mettre à jour les tests de `_build_prompt` pour le nouveau format de consigne**

Ajouter à `tests/assistant_ia/test_portfolio_analysis.py` :

```python
def test_build_prompt_requests_sante_globale_and_analyse_not_justification():
    prompt = portfolio_analysis._build_prompt([], {}, None)
    assert "sante_globale" in prompt
    assert '"analyse"' in prompt
    assert '"justification"' not in prompt
    assert '"points_attention"' not in prompt
```

- [ ] **Step 7: Lancer le test pour vérifier qu'il échoue**

Run: `pytest tests/assistant_ia/test_portfolio_analysis.py -v -k requests_sante_globale`
Expected: FAIL (le prompt actuel contient encore `"justification"` et `"points_attention"`, jamais `sante_globale`/`"analyse"`)

- [ ] **Step 8: Modifier `_build_prompt`**

Remplacer, dans `assistant_ia/portfolio_analysis.py`, le bloc qui va de
`"Pour chaque position, donne une recommandation..."` jusqu'à la fin de la
fonction, par :

```python
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
        "Pour chaque position, donne :\n"
        "- une recommandation parmi exactement ces quatre valeurs : \"garder\", "
        "\"renforcer\", \"alleger\", \"vendre\" ;\n"
        "- une analyse complete (plusieurs phrases, pas une seule) qui repond "
        "explicitement a la question : au prix d'achat de l'utilisateur compare au "
        "prix actuel, est-ce toujours une bonne idee de garder cette ligne a ce "
        "niveau ? Mentionne les actualites negatives recentes que tu as trouvees "
        "s'il y en a, et dis explicitement \"aucune actualite negative notable "
        "trouvee\" si ta recherche n'en a pas trouve. Mentionne aussi les points "
        "positifs pertinents (resultats, catalyseurs) s'il y en a.\n\n"
        "Donne aussi :\n"
        "- un score global \"sante_globale\", un entier de -100 (portefeuille tres "
        "risque ou mal positionne) a +100 (portefeuille sain et bien positionne), "
        "ta propre appreciation globale ;\n"
        "- un resume global de 2 a 3 phrases sur l'equilibre du portefeuille "
        "(concentration, risque, qualite generale).\n\n"
        "Termine ta reponse par un bloc JSON seul, delimite par des balises de bloc de "
        "code json (```json puis ```), exactement sous cette forme :\n"
        "```json\n"
        "{\n"
        "  \"resume_global\": \"...\",\n"
        "  \"sante_globale\": 0,\n"
        "  \"par_ligne\": [{\"ticker\": \"...\", \"reco\": \"garder\", \"analyse\": \"...\"}],\n"
        "  \"comparaison_precedente\": \"...\"\n"
        "}\n"
        "```"
    )
```

- [ ] **Step 9: Lancer tous les tests de `_build_prompt` pour vérifier qu'ils passent**

Run: `pytest tests/assistant_ia/test_portfolio_analysis.py -v -k build_prompt`
Expected: PASS (tous, y compris le nouveau)

- [ ] **Step 10: Commit**

```bash
git add assistant_ia/portfolio_analysis.py tests/assistant_ia/test_portfolio_analysis.py
git commit -m "feat(assistant-ia): prompt v2 -- demande sante_globale et une analyse complete par ligne"
```

- [ ] **Step 11: Remplacer `last_analysis_timestamp` par `derniere_analyse_complete` et ajouter `historique_sante`**

Dans `tests/assistant_ia/test_portfolio_analysis.py`, supprimer
`test_last_analysis_timestamp_returns_none_without_history` et
`test_last_analysis_timestamp_returns_the_last_horodatage`, et les
remplacer par :

```python
def test_derniere_analyse_complete_returns_none_without_history(tmp_path):
    chemin = str(tmp_path / "absent.jsonl")
    assert portfolio_analysis.derniere_analyse_complete(chemin) is None


def test_derniere_analyse_complete_returns_the_last_resultat(tmp_path):
    chemin = tmp_path / "historique.jsonl"
    chemin.write_text(
        json.dumps({"horodatage": "2026-11-05T00:00:00Z",
                    "resultat": {"resume_global": "ok", "sante_globale": 42}}) + "\n",
        encoding="utf-8")
    resultat = portfolio_analysis.derniere_analyse_complete(str(chemin))
    assert resultat == {"resume_global": "ok", "sante_globale": 42}


def test_historique_sante_returns_empty_list_without_history(tmp_path):
    chemin = str(tmp_path / "absent.jsonl")
    assert portfolio_analysis.historique_sante(chemin) == []


def test_historique_sante_returns_points_in_chronological_order(tmp_path):
    chemin = tmp_path / "historique.jsonl"
    chemin.write_text(
        json.dumps({"horodatage": "2026-11-01T00:00:00Z", "resultat": {"sante_globale": 10}}) + "\n"
        + json.dumps({"horodatage": "2026-11-05T00:00:00Z", "resultat": {"sante_globale": 30}}) + "\n",
        encoding="utf-8")
    points = portfolio_analysis.historique_sante(str(chemin))
    assert points == [
        {"horodatage": "2026-11-01T00:00:00Z", "sante_globale": 10},
        {"horodatage": "2026-11-05T00:00:00Z", "sante_globale": 30},
    ]


def test_historique_sante_uses_none_when_sante_globale_is_missing(tmp_path):
    chemin = tmp_path / "historique.jsonl"
    chemin.write_text(
        json.dumps({"horodatage": "2026-11-01T00:00:00Z", "resultat": {"resume_global": "ancien format"}}) + "\n",
        encoding="utf-8")
    points = portfolio_analysis.historique_sante(str(chemin))
    assert points == [{"horodatage": "2026-11-01T00:00:00Z", "sante_globale": None}]


def test_historique_sante_skips_a_corrupted_line(tmp_path):
    chemin = tmp_path / "historique.jsonl"
    chemin.write_text(
        "{ligne corrompue\n"
        + json.dumps({"horodatage": "2026-11-05T00:00:00Z", "resultat": {"sante_globale": 30}}) + "\n",
        encoding="utf-8")
    points = portfolio_analysis.historique_sante(str(chemin))
    assert points == [{"horodatage": "2026-11-05T00:00:00Z", "sante_globale": 30}]
```

Remplacer, dans les quatre tests de `run_portfolio_analysis` qui
vérifient l'archivage via `portfolio_analysis.last_analysis_timestamp(historique)`
(`test_run_portfolio_analysis_appends_to_history_on_success`,
`test_run_portfolio_analysis_returns_disponible_false_on_api_error`,
`test_run_portfolio_analysis_returns_disponible_false_when_response_not_parseable`,
`test_run_portfolio_analysis_returns_disponible_false_when_indices_source_raises`),
chaque appel `portfolio_analysis.last_analysis_timestamp(historique)` par
`portfolio_analysis.derniere_analyse_complete(historique)` (la comparaison
`is not None`/`is None` reste identique, seul le nom de la fonction
change). Par exemple :

```python
def test_run_portfolio_analysis_appends_to_history_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_analysis.usage, "record_usage", lambda *a, **k: None)
    client = _FakeAnthropicClient(response=_reponse_valide())
    historique = str(tmp_path / "historique.jsonl")

    portfolio_analysis.run_portfolio_analysis(
        _PAYLOAD_SIMPLE, client=client, indices_source=lambda: {"companies": []},
        history_path=historique)

    assert portfolio_analysis.derniere_analyse_complete(historique) is not None
```

Appliquer le même remplacement `last_analysis_timestamp` → `derniere_analyse_complete`
dans les trois autres tests listés (mêmes lignes `assert ... is None`,
seul le nom de fonction change).

Remplacer aussi, dans `_reponse_valide` (la fabrique de réponse factice
utilisée par tous les tests de `run_portfolio_analysis`), la clé
`"justification"` par `"analyse"` et ajouter `"sante_globale"` :

```python
def _reponse_valide(resume="ok"):
    texte = (
        f'```json\n{{"resume_global": "{resume}", "sante_globale": 10, "par_ligne": '
        '[{"ticker": "MC.PA", "reco": "garder", "analyse": "stable"}]}\n```'
    )
    return _FakeResponse([_FakeTextBlock(texte)])
```

- [ ] **Step 12: Lancer les tests pour vérifier qu'ils échouent**

Run: `pytest tests/assistant_ia/test_portfolio_analysis.py -v`
Expected: FAIL avec `AttributeError: module 'assistant_ia.portfolio_analysis' has no attribute 'derniere_analyse_complete'` (et `has no attribute 'historique_sante'`)

- [ ] **Step 13: Implémenter `derniere_analyse_complete` et `historique_sante`, retirer `last_analysis_timestamp`**

Dans `assistant_ia/portfolio_analysis.py`, remplacer la fonction
`last_analysis_timestamp` (actuellement juste après `_lit_derniere_analyse`)
par :

```python
def derniere_analyse_complete(path: str = HISTORY_PATH) -> dict | None:
    """Le resultat complet (pas seulement l'horodatage) de la derniere
    analyse reussie, ou None si aucune n'existe encore. Reutilise
    _lit_derniere_analyse, deja tolerante aux fichiers absents/corrompus."""
    derniere = _lit_derniere_analyse(path)
    return derniere.get("resultat") if derniere else None


def historique_sante(path: str = HISTORY_PATH, limite: int = 200) -> list[dict]:
    """Liste chronologique (plus ancienne en premier) de
    {"horodatage": str, "sante_globale": int | None} pour chaque analyse
    du journal -- lecture seule, jamais les champs lourds (texte, snapshot
    de positions). Une ligne corrompue est ignoree (meme contrat que
    _lit_derniere_analyse), jamais une exception. `limite` borne le volume
    renvoye (les plus recentes), pas de pagination necessaire pour un usage
    personnel a cette frequence."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lignes = fh.readlines()
    except Exception:
        return []
    points = []
    for ligne in lignes:
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            entree = json.loads(ligne)
        except json.JSONDecodeError:
            continue
        if not isinstance(entree, dict):
            continue
        resultat = entree.get("resultat") or {}
        points.append({
            "horodatage": entree.get("horodatage"),
            "sante_globale": resultat.get("sante_globale") if isinstance(resultat, dict) else None,
        })
    return points[-limite:]
```

- [ ] **Step 14: Lancer tous les tests du fichier pour vérifier qu'ils passent**

Run: `pytest tests/assistant_ia/test_portfolio_analysis.py -v`
Expected: PASS (tous)

- [ ] **Step 15: Commit**

```bash
git add assistant_ia/portfolio_analysis.py tests/assistant_ia/test_portfolio_analysis.py
git commit -m "feat(assistant-ia): derniere_analyse_complete + historique_sante, retrait de last_analysis_timestamp"
```

---

### Task 2: Routes `assistant_ia/api.py` — `/last` repensée + nouvelle `/history`

**Files:**
- Modify: `assistant_ia/api.py`
- Modify: `tests/assistant_ia/test_api.py`

**Interfaces:**
- Consomme (Task 1) : `portfolio_analysis.derniere_analyse_complete() -> dict | None`,
  `portfolio_analysis.historique_sante() -> list[dict]`.
- Produit : contrat de réponse des deux routes, consommé par la Task 3
  (frontend).

- [ ] **Step 1: Remplacer les deux tests obsolètes de `/last` et ajouter les tests de `/history`**

Dans `tests/assistant_ia/test_api.py`, remplacer
`test_portfolio_analysis_last_returns_null_without_history` et
`test_portfolio_analysis_last_returns_the_stored_timestamp` par :

```python
def test_portfolio_analysis_last_returns_disponible_false_without_history(client, monkeypatch):
    monkeypatch.setattr(api.portfolio_analysis, "derniere_analyse_complete", lambda **kwargs: None)
    response = client.get("/portfolio-analysis/last", headers={"X-Bot-Token": "secret-token"})
    assert response.status_code == 200
    assert response.json() == {"disponible": False, "resultat": None}


def test_portfolio_analysis_last_returns_the_stored_resultat(client, monkeypatch):
    resultat_stocke = {"resume_global": "ok", "sante_globale": 42, "par_ligne": [], "comparaison_precedente": None, "horodatage": "2026-11-11T00:00:00Z"}
    monkeypatch.setattr(api.portfolio_analysis, "derniere_analyse_complete", lambda **kwargs: resultat_stocke)
    response = client.get("/portfolio-analysis/last", headers={"X-Bot-Token": "secret-token"})
    assert response.json() == {"disponible": True, "resultat": resultat_stocke}
```

Ajouter (après les tests de `/last`) :

```python
def test_portfolio_analysis_history_rejects_a_missing_token(client):
    response = client.get("/portfolio-analysis/history")
    assert response.status_code == 401


def test_portfolio_analysis_history_returns_the_points(client, monkeypatch):
    points = [{"horodatage": "2026-11-01T00:00:00Z", "sante_globale": 10}]
    monkeypatch.setattr(api.portfolio_analysis, "historique_sante", lambda **kwargs: points)
    response = client.get("/portfolio-analysis/history", headers={"X-Bot-Token": "secret-token"})
    assert response.status_code == 200
    assert response.json() == {"historique": points}
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `pytest tests/assistant_ia/test_api.py -v -k "portfolio_analysis_last or portfolio_analysis_history"`
Expected: FAIL (les tests `/last` échouent car la route renvoie encore
`{"horodatage": ...}` et appelle `last_analysis_timestamp`, qui n'existe
plus -> `AttributeError` ; les tests `/history` échouent en `404`, route
inexistante)

- [ ] **Step 3: Modifier les routes dans `assistant_ia/api.py`**

Remplacer la route `portfolio_analysis_last` actuelle par :

```python
@app.get("/portfolio-analysis/last")
def portfolio_analysis_last(x_bot_token: str | None = Header(default=None)):
    _check_token(x_bot_token)
    resultat = portfolio_analysis.derniere_analyse_complete()
    return {"disponible": resultat is not None, "resultat": resultat}


@app.get("/portfolio-analysis/history")
def portfolio_analysis_history(x_bot_token: str | None = Header(default=None)):
    _check_token(x_bot_token)
    return {"historique": portfolio_analysis.historique_sante()}
```

- [ ] **Step 4: Lancer tous les tests de `test_api.py` pour vérifier qu'ils passent**

Run: `pytest tests/assistant_ia/test_api.py -v`
Expected: PASS (tous)

- [ ] **Step 5: Commit**

```bash
git add assistant_ia/api.py tests/assistant_ia/test_api.py
git commit -m "feat(assistant-ia): /portfolio-analysis/last renvoie le resultat complet, nouvelle route /history"
```

---

### Task 3: Fusion des onglets dans `docs/index.html`

**Files:**
- Modify: `docs/index.html`

**Interfaces:**
- Consomme (Task 2) : nouveau contrat `{disponible, resultat}` de
  `GET /portfolio-analysis/last` ; `{historique: [{horodatage, sante_globale}]}`
  de `GET /portfolio-analysis/history` ; contrat `par_ligne[].analyse`
  (plus `.justification`) et `sante_globale` de `POST /portfolio-analysis`.
- Produit : rien consommé par une tâche suivante (dernière tâche du plan).

Pas de test unitaire (convention déjà établie pour ce fichier).
Vérification manuelle en navigateur au Step 3.

- [ ] **Step 1: Retirer la section carrousel "Analyse IA" et fusionner son contenu dans "Analyse"**

Dans `docs/index.html`, repérer `function renderPortfolioActionsTab` et
son tableau `portfolioActionsSections`. Remplacer les deux entrées
`'Analyse'` et `'Analyse IA'` actuelles par une seule :

```js
    {
      title: 'Analyse',
      desc: 'Concentration, santé du portefeuille selon le modèle, attribution de performance, dividendes, et l\'avis de l\'assistant IA sur chaque ligne.',
      html: portfolioAnalysisHtml(positions, companiesByTicker, currencyByIndex, priceHistoryByTicker, dividendHistoryByTicker) + portfolioAiSubsectionHtml(),
      wire: () => {
        wirePortfolioPerformanceBenchmarkSelectors(positions, closedPositions, companiesByTicker, currencyByIndex, priceHistoryByTicker);
        wirePortfolioAiSubsection(positions, closedPositions, companiesByTicker, currencyByIndex, dividendHistoryByTicker);
      },
    },
```

(`portfolioAnalysisHtml` elle-même n'est pas modifiée.)

- [ ] **Step 2: Remplacer les fonctions de rendu/branchement IA**

Supprimer entièrement les fonctions `portfolioAiAnalysisSectionHtml`,
`_portfolioAiFreshnessLabel`, `_portfolioAiRecoLabel`,
`_portfolioAiAnalysisResultHtml`, `wirePortfolioAiAnalysisSection`
(actuellement juste après `portfolioAnalysisHtml`, avant
`renderPortfolioActionsTab`). Les remplacer, au même endroit, par :

```js
function portfolioAiSubsectionHtml() {
  return `
    <h3 class="portfolio-analysis-title">Avis de l'IA</h3>
    <div id="portfolioAiFreshness" class="hero-sub"></div>
    <button class="toggle-btn" type="button" id="portfolioAiAnalysisBtn">Lancer l'analyse IA</button>
    <div id="portfolioAiChart"></div>
    <div id="portfolioAiAnalysisResult"></div>
  `;
}

function _portfolioAiRecoLabel(reco) {
  const labels = { garder: 'Garder', renforcer: 'Renforcer', alleger: 'Alléger', vendre: 'Vendre', a_verifier: 'À vérifier' };
  return labels[reco] || reco;
}

function _portfolioAiSanteLabel(sante) {
  if (typeof sante !== 'number') return '';
  const color = sante >= 0 ? 'var(--green-500)' : 'var(--rust)';
  return `<span class="price-value" style="color:${color}">${sante > 0 ? '+' : ''}${sante}</span>`;
}

function _portfolioAiCardsHtml(parLigne, positionsByTicker, companiesByTicker, currencyByIndex) {
  if (!parLigne.length) {
    return '<div class="list-empty">Aucune recommandation disponible.</div>';
  }
  return parLigne.map(l => {
    const position = positionsByTicker.get(l.ticker);
    const company = companiesByTicker[l.ticker];
    const currency = company ? (currencyByIndex[company.index] || 'EUR') : 'EUR';
    const symbol = CURRENCY_SYMBOL[currency] || currency;
    const prixHtml = position && company
      ? `<span class="hero-sub">Achat ${position.buy_price.toLocaleString('fr-FR')} ${symbol} — actuel ${(company.current_price ?? 0).toLocaleString('fr-FR')} ${symbol}</span>`
      : '';
    return `
      <div class="portfolio-total-card">
        <div class="portfolio-position-main">
          <a class="company-name" href="#indices/${encodeURIComponent(l.ticker)}">${escHtml(company ? company.name : l.ticker)}</a>
          ${prixHtml}
        </div>
        <span class="price-value">${escHtml(_portfolioAiRecoLabel(l.reco))}</span>
        <p class="hero-sub">${escHtml(l.analyse || l.justification || '')}</p>
      </div>`;
  }).join('');
}

function _portfolioAiAnalysisResultHtml(resultat, positionsByTicker, companiesByTicker, currencyByIndex) {
  const comparaisonHtml = resultat.comparaison_precedente
    ? `<h3 class="portfolio-analysis-title">Depuis la dernière fois</h3><div class="hero-sub">${escHtml(resultat.comparaison_precedente)}</div>`
    : '';
  return `
    <div class="portfolio-totals">
      <div class="portfolio-total-card">
        <span class="portfolio-total-label">Résumé</span>
        <div class="hero-sub">${escHtml(resultat.resume_global || '')}</div>
        ${typeof resultat.sante_globale === 'number' ? `<span class="portfolio-total-label">Santé jugée par l'IA</span>${_portfolioAiSanteLabel(resultat.sante_globale)}` : ''}
      </div>
    </div>
    <h3 class="portfolio-analysis-title">Par entreprise</h3>
    <div class="portfolio-ai-cards">${_portfolioAiCardsHtml(resultat.par_ligne || [], positionsByTicker, companiesByTicker, currencyByIndex)}</div>
    ${comparaisonHtml}
  `;
}

function _portfolioAiFreshnessLabel(horodatage) {
  if (!horodatage) return 'Aucune analyse encore.';
  const jours = Math.floor((Date.now() - new Date(horodatage).getTime()) / 86400000);
  if (jours <= 0) return 'Dernière analyse : aujourd\'hui.';
  return `Dernière analyse : il y a ${jours} jour${jours > 1 ? 's' : ''}.`;
}

function aiJudgmentCurveSvg(points) {
  const usable = points.filter(p => typeof p.sante_globale === 'number' && p.horodatage);
  if (usable.length < 2) {
    return '<div class="empty">Pas encore assez d\'analyses passées pour tracer une évolution (au moins deux nécessaires).</div>';
  }
  const width = 600, height = 160, padding = 28;
  const minVal = -100, maxVal = 100, range = maxVal - minVal;
  const minDate = usable[0].horodatage, maxDate = usable[usable.length - 1].horodatage;
  const dateSpanMs = new Date(maxDate) - new Date(minDate) || 1;
  const x = d => padding + ((new Date(d) - new Date(minDate)) / dateSpanMs) * (width - padding * 2);
  const y = v => padding + (height - padding * 2) - ((v - minVal) / range) * (height - padding * 2);
  const pointsAttr = usable.map(p => `${x(p.horodatage).toFixed(1)},${y(p.sante_globale).toFixed(1)}`).join(' ');
  const last = usable[usable.length - 1];
  const color = last.sante_globale >= 0 ? 'var(--green-500)' : 'var(--rust)';
  const zeroY = y(0).toFixed(1);
  return `
    <svg viewBox="0 0 ${width} ${height}" class="portfolio-perf-chart" role="img" aria-label="Évolution du jugement IA sur le portefeuille">
      <line x1="${padding}" y1="${zeroY}" x2="${width - padding}" y2="${zeroY}" stroke="var(--muted)" stroke-dasharray="4 3" />
      <polyline points="${pointsAttr}" fill="none" stroke="${color}" stroke-width="2" />
    </svg>
    <div class="portfolio-perf-legend"><span style="color:${color}">● Santé jugée par l'IA (${last.sante_globale > 0 ? '+' : ''}${last.sante_globale})</span></div>`;
}

function wirePortfolioAiSubsection(positions, closedPositions, companiesByTicker, currencyByIndex, dividendHistoryByTicker) {
  const freshnessEl = document.getElementById('portfolioAiFreshness');
  const chartEl = document.getElementById('portfolioAiChart');
  const btn = document.getElementById('portfolioAiAnalysisBtn');
  const resultEl = document.getElementById('portfolioAiAnalysisResult');
  if (!btn || !resultEl) return;

  const positionsByTicker = new Map(positions.map(p => [p.ticker, p]));

  const renderResult = (resultat) => {
    resultEl.innerHTML = _portfolioAiAnalysisResultHtml(resultat, positionsByTicker, companiesByTicker, currencyByIndex);
    if (freshnessEl) freshnessEl.textContent = _portfolioAiFreshnessLabel(resultat.horodatage);
  };

  const refreshChart = async (token) => {
    if (!chartEl) return;
    try {
      const res = await fetch('https://goldbot.fr:8445/portfolio-analysis/history', { headers: { 'X-Bot-Token': token } });
      if (!res.ok) return;
      const data = await res.json();
      chartEl.innerHTML = aiJudgmentCurveSvg(data.historique || []);
    } catch (e) { /* le reste de la sous-section reste utilisable sans le graphique */ }
  };

  const tokenDejaMemorise = localStorage.getItem('aiAssistantToken');
  if (tokenDejaMemorise) {
    fetch('https://goldbot.fr:8445/portfolio-analysis/last', { headers: { 'X-Bot-Token': tokenDejaMemorise } })
      .then(res => res.ok ? res.json() : null)
      .then(data => {
        if (data && data.disponible) {
          renderResult(data.resultat);
          btn.textContent = 'Relancer l\'analyse IA';
        } else if (freshnessEl) {
          freshnessEl.textContent = 'Aucune analyse encore.';
        }
      })
      .catch(() => { if (freshnessEl) freshnessEl.textContent = ''; });
    refreshChart(tokenDejaMemorise);
  } else if (freshnessEl) {
    freshnessEl.textContent = 'Aucune analyse encore.';
  }

  btn.addEventListener('click', async () => {
    const jeton = getAiAssistantToken();
    if (!jeton) return;
    btn.disabled = true;
    const texteInitial = btn.textContent;
    btn.textContent = 'Analyse en cours…';
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
      renderResult(data.resultat);
      btn.textContent = 'Relancer l\'analyse IA';
      refreshChart(jeton);
    } catch (e) {
      resultEl.innerHTML = '<div class="empty">Analyse indisponible pour le moment.</div>';
    } finally {
      btn.disabled = false;
      if (btn.textContent === 'Analyse en cours…') btn.textContent = texteInitial;
    }
  });
}
```

- [ ] **Step 3: Vérification manuelle**

Servir `docs/` localement (`python -m http.server` depuis `docs/`), ouvrir
`index.html#portefeuille` dans un navigateur, aller sur l'onglet "Actions"
→ section "Analyse" (il n'y a plus de section "Analyse IA" séparée dans le
carrousel) → vérifier que :
- la section "Analyse" contient maintenant, à la suite du contenu
  existant (performance, concentration, dividendes), le bloc "Avis de
  l'IA" ;
- sans jeton mémorisé (`localStorage` vide), l'indicateur affiche "Aucune
  analyse encore." sans jamais déclencher de `prompt()` ;
- le clic sur "Lancer l'analyse IA" demande le jeton puis tente l'appel
  (échoue proprement si le backend n'est pas encore redéployé avec ce
  contrat — message d'erreur affiché, pas d'exception JS, bouton
  réactivé) ;
- en simulant une réponse `disponible: true` (ou si le backend v2 est
  déjà déployé et qu'une vraie analyse aboutit), les cartes par
  entreprise s'affichent avec prix d'achat/actuel, badge de reco, et le
  paragraphe d'analyse ; le score `sante_globale` s'affiche s'il est
  présent.

- [ ] **Step 4: Commit**

```bash
git add docs/index.html
git commit -m "feat(portfolio): fusion Analyse + Analyse IA, cartes par entreprise, courbe d'evolution du jugement IA"
```

---

## Vérification finale

```bash
pytest tests/ -q
node docs/portfolio.test.js
```

Puis attendre la décision de l'utilisateur avant tout déploiement VPS ou
fusion dans `main` (même règle que le chantier v1 — branche dédiée,
jamais de merge/déploiement automatique).
