# Bot Actions — P&L en euros sur positions multi-devises — Plan d'implémentation

> **Pour les exécutants :** SOUS-COMPÉTENCE REQUISE : utiliser
> superpowers:subagent-driven-development pour implémenter ce plan tâche par
> tâche.

**Objectif :** calculer et afficher, pour chaque position ouverte par
`ibkr_bot` dans une devise autre que l'euro, un P&L latent traduit en euros —
aujourd'hui invisible nulle part (le bot ne suit que le prix d'exécution brut,
jamais de conversion).

**Architecture :** le taux de change à l'entrée est désormais enregistré sur
la fiche de position elle-même (`positions.json`), pas seulement dans la
ligne de journal de l'ordre. Chaque batch quotidien revalorise ensuite les
positions encore ouvertes avec le prix du jour (déjà disponible via
`docs/indices.json`) et un taux de change du jour (déjà fetché/caché par
devise), sans aucune connexion réseau supplémentaire. Le résultat est exposé
par les deux canaux existants (`/dashboard` protégé par jeton, et le statut
public en mode simulation) puis affiché dans le panneau "Bot Actions" du
site.

**Tech Stack :** Python 3.14, pytest, FastAPI/TestClient, JS vanilla (pas de
framework, pas de runner de test JS dans ce dépôt).

**Spec :** aucun document de spec séparé — ce plan découle directement d'une
discussion de brainstorming (bounded) avec l'utilisateur le 2026-10-03.
Contexte retenu de cette discussion :

- Le site affiche déjà, ailleurs (`docs/portfolio.js`), une règle explicite
  "le site ne convertit jamais entre devises" pour le suivi manuel/MT5
  multi-courtiers — ce plan s'en écarte **délibérément** pour `ibkr_bot`
  uniquement : il s'agit ici d'argent réel dans une seule devise de
  référence (EUR), pas d'un choix d'affichage multi-comptes.
- **Correction importante faite pendant la préparation de ce plan :** l'idée
  initiale d'ajouter un plafond de diversification devise pour Nikkei225
  (JPY) et HangSeng (HKD) dans `ibkr_bot/portfolio.py::INDEX_ZONE` a été
  **abandonnée** après vérification du code — `ibkr_bot/signals.py:20`
  exclut volontairement ces deux indices du périmètre réel
  (`INDICES_IN_SCOPE`), donc le bot n'y ouvre et n'y ouvrira jamais de
  position. Les 8 indices réellement tradés (CAC40/DAX/IBEX35/FTSEMIB=EUR,
  NASDAQ/DOW=USD, FTSE=GBP, SMI=CHF) ont déjà chacun une zone dans
  `INDEX_ZONE`, donc **aucun plafond devise ne manque** pour le périmètre
  réel — ce plan ne contient donc AUCUNE tâche sur `INDEX_ZONE`.
- Le tableau de bord protégé par jeton (`ibkr_bot/api.py::/dashboard`,
  `docs/superpowers/specs/2026-09-20-ibkr-bot-api-design.md` §4.3) expose
  déjà les positions **sans restriction dry_run/réel** — c'est le canal
  principal pour voir ce P&L en argent réel. Le statut public
  (`docs/ibkr_bot_status.json`) continue, lui, à omettre les positions en
  mode réel (décision de sécurité du 2026-09-20, non remise en question
  ici) ; ce plan ajoute donc les champs de P&L à ce fichier uniquement pour
  la branche `dry_run` déjà publiée.

## Global Constraints

- Unité de prix : `prix_execution_reference` et `current_price` (venant de
  `docs/indices.json`) sont tous deux dans la **devise de compte**
  (`devise`/`devise_compte` — livres pour le LSE, jamais pence) — voir
  `ibkr_bot/sizing.py` module docstring. Ne jamais mélanger avec
  `prix_execution_cotation` (pence pour le LSE).
- Convention de taux : `taux_de_change` / `_taux_de_change()` renvoie
  **EUR → devise** (ex. 0.86 pour EUR/GBP : 1 EUR = 0.86 GBP). Pour
  convertir un montant de la devise vers l'euro : **diviser** par ce taux.
  Un taux de `0.0` ou absent signifie "indisponible aujourd'hui" (jamais
  deviné) — toute valorisation doit alors être laissée inchangée (dernière
  valeur connue conservée), jamais mise à zéro ni effacée.
- Un échec de fetch de taux de change (`_taux_de_change` renvoie déjà 0.0 et
  journalise lui-même l'erreur dans `run["erreurs"]`) ne doit jamais faire
  lever d'exception plus haut dans le batch.
- Toute nouvelle fonction suit le style déjà en place dans le fichier
  qu'elle rejoint (docstrings expliquant le POURQUOI, jamais le QUOI —
  voir les fonctions existantes de `ibkr_bot/daily.py` pour le ton).
- Lancer `python -m pytest tests/ibkr_bot/ -q` après chaque tâche — doit
  rester à 0 échec.

---

### Task 1: Taux de change à l'entrée + valorisation EUR quotidienne

**Files:**
- Modify: `ibkr_bot/journal.py:140-154` (`build_position_record`)
- Modify: `ibkr_bot/daily.py` (nouvelle fonction après `_taux_de_change`,
  ligne 419 ; appel dans `run_batch` entre la boucle de sorties et la
  section "--- 7. Entrees ---", lignes ~800-821)
- Test: `tests/ibkr_bot/test_journal.py`
- Test: `tests/ibkr_bot/test_daily.py`

**Interfaces:**
- Consomme : `plan["taux_de_change"]` (déjà produit par
  `sizing.compute_quantity`, voir `ibkr_bot/sizing.py:105`) ;
  `_taux_de_change(gw, base_url, devise, cache, run) -> float` (déjà
  existant, `ibkr_bot/daily.py:405`).
- Produit : la fiche de position porte désormais, en plus des champs
  existants, `taux_de_change_entree: float | None` (écrit une fois, à la
  création) et, mis à jour à CHAQUE batch tant que la position reste
  ouverte : `prix_actuel: float | None`, `taux_de_change_actuel: float |
  None`, `valeur_actuelle_eur: float | None`, `cout_entree_eur: float |
  None`, `pnl_eur: float | None`, `pnl_eur_pct: float | None`. Les tâches 2
  et 3 consomment ces 6 nouveaux champs par leur nom exact.

- [ ] **Step 1: Écrire le test de `build_position_record` qui échoue**

Ajouter dans `tests/ibkr_bot/test_journal.py`, à la suite de
`test_build_position_record_produces_exactly_what_portfolio_needs` :

```python
def test_build_position_record_stores_the_entry_fx_rate():
    """Le taux de change a l'entree doit desormais vivre SUR la position
    elle-meme (positions.json), pas seulement dans la ligne de journal de
    l'ordre (build_order_record) — Task 1 du plan FX P&L 2026-10-03."""
    signal = {"id": "III.L-2026-09-15", "ticker": "III.L", "name": "3i Group",
              "index": "FTSE", "currency": "GBP", "sector": "Financial Services",
              "entry_date": "2026-09-15",
              "paper_entry_price": 28.0, "target_exit_price": 35.0,
              "score": 40.0, "current_price": 29.5}
    plan = {"ticker": "III.L", "quantite": 20, "devise_cotation": "GBp",
            "devise_compte": "GBP", "taux_de_change": 0.86, "budget_converti": 43000.0,
            "prix_unitaire_cotation": 2950.0, "cout_estime_devise_compte": 590.0,
            "motif": None}
    contrat = {"ticker": "III.L", "conid": 98765, "exchange": "LSE",
               "currency": "GBP", "motif": None, "detail": "resolu"}

    position = journal.build_position_record(
        signal, plan, contrat, quantite=20, prix_execution_reference=29.5,
        today="2026-09-15")

    assert position["taux_de_change_entree"] == 0.86
```

- [ ] **Step 2: Lancer le test, verifier qu'il echoue**

Run: `python -m pytest tests/ibkr_bot/test_journal.py::test_build_position_record_stores_the_entry_fx_rate -v`
Expected: FAIL avec `KeyError: 'taux_de_change_entree'`

- [ ] **Step 3: Ajouter le champ dans `build_position_record`**

Dans `ibkr_bot/journal.py`, modifier le dict renvoye par
`build_position_record` (lignes 140-154) pour ajouter un champ, juste
apres `"devise": plan["devise_compte"],` :

```python
        "devise": plan["devise_compte"],
        "taux_de_change_entree": plan.get("taux_de_change"),
```

Mettre egalement a jour la docstring de la fonction pour mentionner ce
nouveau champ (une ligne, pas un paragraphe) :

```python
    """Etat de travail d'une position ouverte par le bot (spec 4.9).

    `prix_execution_reference` est le prix d'execution reel, deja ramene
    en devise de COMPTE (livres pour le LSE, pas pence) : c'est la
    reference du stop-loss, comparee par portfolio.exit_reason au
    current_price de docs/indices.json, qui est lui aussi en livres.
    `target_exit_price` est repris VERBATIM du paper-trading et jamais
    recalcule (spec 3.6). `taux_de_change_entree` (EUR -> devise de
    compte, voir sizing.compute_quantity) est conserve pour calculer le
    P&L en euros a chaque batch (voir daily._valoriser_positions).
    """
```

- [ ] **Step 4: Lancer le test, verifier qu'il passe**

Run: `python -m pytest tests/ibkr_bot/test_journal.py -v`
Expected: PASS (tous les tests du fichier, y compris les existants —
aucune regression attendue puisque c'est un ajout de champ pur)

- [ ] **Step 5: Ecrire le test unitaire de `_valoriser_positions` qui echoue**

Ajouter dans `tests/ibkr_bot/test_daily.py`, a la suite des tests
existants de `_taux_de_change` (chercher la section correspondante, sinon
en fin de fichier avant `# --- run_batch` si une telle section existe) :

```python
class _GwTaux:
    def exchange_rate(self, base_url, source, target):
        return 1.0 if source == target else {"USD": 1.08, "GBP": 0.86}[target]


def test_valoriser_positions_computes_eur_pnl_for_a_foreign_position():
    positions = [{
        "ticker": "III.L", "devise": "GBP", "quantite": 20,
        "prix_execution_reference": 29.5, "taux_de_change_entree": 0.80,
    }]
    companies = {"III.L": {"current_price": 31.0}}
    run = {"erreurs": []}

    daily._valoriser_positions(positions, companies, _GwTaux(), "url", {}, run)

    position = positions[0]
    assert position["prix_actuel"] == 31.0
    assert position["taux_de_change_actuel"] == 0.86
    # valeur actuelle = 20 * 31.0 / 0.86 = 720.930...
    assert position["valeur_actuelle_eur"] == pytest.approx(720.93, abs=0.01)
    # cout d'entree = 20 * 29.5 / 0.80 = 737.5
    assert position["cout_entree_eur"] == pytest.approx(737.5, abs=0.01)
    # pnl = 720.93 - 737.5 = -16.57
    assert position["pnl_eur"] == pytest.approx(-16.57, abs=0.01)
    assert position["pnl_eur_pct"] == pytest.approx(-2.25, abs=0.01)
    assert run["erreurs"] == []


def test_valoriser_positions_leaves_a_position_unchanged_when_the_fx_rate_fails():
    class _GwEchec:
        def exchange_rate(self, base_url, source, target):
            raise RuntimeError("indisponible")

    positions = [{
        "ticker": "III.L", "devise": "GBP", "quantite": 20,
        "prix_execution_reference": 29.5, "taux_de_change_entree": 0.80,
        "valeur_actuelle_eur": 700.0,  # derniere valeur connue (hier)
    }]
    companies = {"III.L": {"current_price": 31.0}}
    run = {"erreurs": []}

    daily._valoriser_positions(positions, companies, _GwEchec(), "url", {}, run)

    assert positions[0]["valeur_actuelle_eur"] == 700.0  # inchange
    assert "prix_actuel" not in positions[0]
    assert len(run["erreurs"]) == 1  # journalise par _taux_de_change lui-meme


def test_valoriser_positions_leaves_a_position_unchanged_when_the_ticker_has_no_current_price():
    positions = [{
        "ticker": "DELISTED.PA", "devise": "EUR", "quantite": 5,
        "prix_execution_reference": 100.0, "taux_de_change_entree": 1.0,
    }]
    run = {"erreurs": []}

    daily._valoriser_positions(positions, {}, _GwTaux(), "url", {}, run)

    assert "prix_actuel" not in positions[0]
    assert run["erreurs"] == []
```

- [ ] **Step 6: Lancer les tests, verifier qu'ils echouent**

Run: `python -m pytest tests/ibkr_bot/test_daily.py -k valoriser_positions -v`
Expected: FAIL avec `AttributeError: module 'ibkr_bot.daily' has no attribute '_valoriser_positions'`

- [ ] **Step 7: Implementer `_valoriser_positions`**

Dans `ibkr_bot/daily.py`, juste apres la fonction `_taux_de_change`
(ligne 419, avant `_executer_sortie`) :

```python
def _valoriser_positions(positions: list[dict], companies: dict, gw,
                         base_url: str, taux_cache: dict, run: dict) -> None:
    """Valorise en euros chaque position encore ouverte en fin de batch
    (P&L latent, invisible jusqu'ici — seul le prix d'execution brut
    etait suivi). Mute chaque dict de `positions` EN PLACE.

    Une societe absente de `companies` (panne transitoire de donnees
    indices.json) ou un taux de change indisponible aujourd'hui
    (_taux_de_change renvoie 0.0 et journalise deja l'erreur) laisse la
    position INCHANGEE — la derniere valorisation connue reste affichee
    plutot que de disparaitre ou de retomber a zero, meme philosophie que
    le reemploi de l'analyse financiere a trimestre inchange
    (indices_score.build_company_entry)."""
    for position in positions:
        company = companies.get(position.get("ticker")) or {}
        prix_actuel = company.get("current_price")
        if not isinstance(prix_actuel, (int, float)) or isinstance(prix_actuel, bool):
            continue
        devise = position.get("devise")
        if not devise:
            continue
        taux_actuel = _taux_de_change(gw, base_url, devise, taux_cache, run)
        if not taux_actuel:
            continue

        quantite = position.get("quantite")
        position["prix_actuel"] = prix_actuel
        position["taux_de_change_actuel"] = taux_actuel
        valeur_actuelle_eur = round(quantite * prix_actuel / taux_actuel, 2)
        position["valeur_actuelle_eur"] = valeur_actuelle_eur

        prix_entree = position.get("prix_execution_reference")
        taux_entree = position.get("taux_de_change_entree")
        if (isinstance(prix_entree, (int, float)) and not isinstance(prix_entree, bool)
                and isinstance(taux_entree, (int, float)) and not isinstance(taux_entree, bool)
                and taux_entree > 0):
            cout_entree_eur = quantite * prix_entree / taux_entree
            position["cout_entree_eur"] = round(cout_entree_eur, 2)
            pnl_eur = valeur_actuelle_eur - cout_entree_eur
            position["pnl_eur"] = round(pnl_eur, 2)
            position["pnl_eur_pct"] = (
                round(pnl_eur / cout_entree_eur * 100, 2) if cout_entree_eur else None)
```

- [ ] **Step 8: Brancher l'appel dans `run_batch`**

Dans `ibkr_bot/daily.py`, fonction `run_batch`, juste apres la fin de la
boucle `for sortie in sorties_a_traiter:` (immediatement apres la ligne
`_sauver_positions()` qui suit `tickers_indisponibles[...] = "vendu_aujourd_hui"`,
et AVANT le commentaire `# --- 7. Entrees ---`), inserer :

```python
    # --- Valorisation EUR (P&L latent, plan FX P&L 2026-10-03) --------
    # Apres les sorties (positions_ouvertes est deja purge des positions
    # vendues aujourd'hui) et AVANT les entrees : le cache de taux cree
    # ici est reutilise plus bas (section 7) sans second appel reseau
    # pour une devise deja vue aujourd'hui.
    taux_par_devise: dict[str, float] = {}
    _valoriser_positions(positions_ouvertes, companies, gw, base_url,
                         taux_par_devise, run)
    _sauver_positions()

```

Puis supprimer la ligne devenue redondante un peu plus bas (section
"--- 7. Entrees ---"), qui recreait le meme dict :

```python
    taux_par_devise: dict[str, float] = {}
```

(cette ligne disparait purement et simplement — le dict cree ci-dessus
est deja en portee et continue d'etre rempli par le `_taux_de_change`
appele dans la boucle `for signal in signaux:` juste en dessous).

- [ ] **Step 9: Lancer toute la suite ibkr_bot**

Run: `python -m pytest tests/ibkr_bot/ -q`
Expected: PASS, 0 echec (les nouveaux tests de l'etape 5 passent ; aucune
regression sur les tests `run_batch` existants, qui ne portaient aucune
assertion sur l'absence de ces nouveaux champs)

- [ ] **Step 10: Commit**

```bash
git add ibkr_bot/journal.py ibkr_bot/daily.py tests/ibkr_bot/test_journal.py tests/ibkr_bot/test_daily.py
git commit -m "feat(ibkr_bot): valorise chaque position ouverte en euros a chaque batch"
```

---

### Task 2: Exposer le P&L en euros via /dashboard et le statut public (dry_run)

**Files:**
- Modify: `ibkr_bot/api.py:75-87` (shape de `positions` dans `/dashboard`)
- Modify: `ibkr_bot/status_publish.py:31-44` (`_public_position`)
- Test: `tests/ibkr_bot/test_api.py`
- Test: `tests/ibkr_bot/test_status_publish.py`

**Interfaces:**
- Consomme : les 6 champs produits par la Task 1
  (`prix_actuel`/`taux_de_change_actuel`/`valeur_actuelle_eur`/`cout_entree_eur`/`pnl_eur`/`pnl_eur_pct`),
  lus depuis les dicts de `positions.json`.
- Produit : `/dashboard` et (en dry_run uniquement) `docs/ibkr_bot_status.json`
  exposent desormais `prix_actuel`, `valeur_actuelle_eur`, `pnl_eur`,
  `pnl_eur_pct` dans chaque position — consommes tels quels par la Task 3
  cote frontend (memes noms de champs, aucune transformation
  supplementaire attendue cote JS).

- [ ] **Step 1: Ecrire le test de `/dashboard` qui echoue**

Ajouter dans `tests/ibkr_bot/test_api.py`, a la suite de
`test_dashboard_returns_full_body_when_all_sources_present` :

```python
def test_dashboard_includes_eur_pnl_fields(client, monkeypatch, tmp_path):
    _seed(
        monkeypatch, tmp_path,
        positions=[{
            "id": "III.L-2026-09-15", "ticker": "III.L", "name": "3i Group",
            "index": "FTSE", "conid": 98765, "quantite": 20,
            "prix_execution_reference": 29.5, "date_entree": "2026-09-15",
            "target_exit_price": 35.0,
            "prix_actuel": 31.0, "taux_de_change_actuel": 0.86,
            "valeur_actuelle_eur": 720.93, "cout_entree_eur": 737.5,
            "pnl_eur": -16.57, "pnl_eur_pct": -2.25,
        }],
    )

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    position = response.json()["positions"][0]
    assert position["prix_actuel"] == 31.0
    assert position["valeur_actuelle_eur"] == 720.93
    assert position["pnl_eur"] == -16.57
    assert position["pnl_eur_pct"] == -2.25


def test_dashboard_sanitizes_non_finite_pnl_fields(client, monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, positions=[{
        "ticker": "III.L", "name": "3i Group", "index": "FTSE", "quantite": 20,
        "prix_execution_reference": 29.5, "date_entree": "2026-09-15",
        "pnl_eur": float("nan"), "valeur_actuelle_eur": float("inf"),
    }])

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    position = response.json()["positions"][0]
    assert position["pnl_eur"] is None
    assert position["valeur_actuelle_eur"] is None


def test_dashboard_omits_pnl_fields_as_null_for_a_position_never_valued(client, monkeypatch, tmp_path):
    """Une position du jour-meme (ouverte par ce batch) n'a pas encore ete
    valorisee (Task 1 : la valorisation tourne AVANT les entrees du jour,
    donc une position fraichement ouverte attend le batch suivant) —
    /dashboard doit renvoyer null plutot que lever une KeyError."""
    _seed(monkeypatch, tmp_path, positions=[{
        "ticker": "MC.PA", "name": "LVMH", "index": "CAC40", "quantite": 5,
        "prix_execution_reference": 90.5, "date_entree": "2026-09-15",
    }])

    response = client.get("/dashboard", headers={"X-Bot-Token": "secret-token"})

    position = response.json()["positions"][0]
    assert position["prix_actuel"] is None
    assert position["pnl_eur"] is None
```

- [ ] **Step 2: Lancer les tests, verifier qu'ils echouent**

Run: `python -m pytest tests/ibkr_bot/test_api.py -k pnl -v`
Expected: FAIL (`KeyError` ou `AssertionError`, le dict renvoye par
`/dashboard` ne porte pas encore ces champs)

- [ ] **Step 3: Etendre la shape de `/dashboard` dans `ibkr_bot/api.py`**

Remplacer le bloc `positions = [...]` (lignes 75-87) par :

```python
    positions = [
        {
            "ticker": p.get("ticker"),
            "name": p.get("name"),
            "index": p.get("index"),
            "quantite": p.get("quantite"),
            "prix_entree": _sanitize_number(p.get("prix_execution_reference")),
            "date_entree": p.get("date_entree"),
            "target_exit_price": _sanitize_number(p.get("target_exit_price")),
            "prix_actuel": _sanitize_number(p.get("prix_actuel")),
            "valeur_actuelle_eur": _sanitize_number(p.get("valeur_actuelle_eur")),
            "pnl_eur": _sanitize_number(p.get("pnl_eur")),
            "pnl_eur_pct": _sanitize_number(p.get("pnl_eur_pct")),
        }
        for p in raw_positions
        if isinstance(p, dict)
    ]
```

- [ ] **Step 4: Lancer les tests de l'etape 1, verifier qu'ils passent**

Run: `python -m pytest tests/ibkr_bot/test_api.py -v`
Expected: PASS (tous les tests du fichier)

- [ ] **Step 5: Ecrire le test de `_public_position` qui echoue**

Ajouter dans `tests/ibkr_bot/test_status_publish.py` (chercher la section
qui teste `_public_position` ou `build_public_status` avec des
positions — sinon l'ajouter a la suite du test existant montre plus
haut) :

```python
def test_public_position_includes_eur_pnl_fields_when_present():
    position = {
        "ticker": "III.L", "name": "3i Group", "index": "FTSE", "quantite": 20,
        "prix_execution_reference": 29.5, "date_entree": "2026-09-15",
        "prix_actuel": 31.0, "valeur_actuelle_eur": 720.93,
        "pnl_eur": -16.57, "pnl_eur_pct": -2.25,
    }

    public = status_publish._public_position(position)

    assert public["prix_actuel"] == 31.0
    assert public["valeur_actuelle_eur"] == 720.93
    assert public["pnl_eur"] == -16.57
    assert public["pnl_eur_pct"] == -2.25


def test_public_position_defaults_eur_pnl_fields_to_none_when_not_yet_valued():
    position = {
        "ticker": "MC.PA", "name": "LVMH", "index": "CAC40", "quantite": 5,
        "prix_execution_reference": 90.5, "date_entree": "2026-09-15",
    }

    public = status_publish._public_position(position)

    assert public["prix_actuel"] is None
    assert public["pnl_eur"] is None
```

- [ ] **Step 6: Lancer les tests, verifier qu'ils echouent**

Run: `python -m pytest tests/ibkr_bot/test_status_publish.py -k pnl -v`
Expected: FAIL (`KeyError`)

- [ ] **Step 7: Etendre `_public_position`**

Dans `ibkr_bot/status_publish.py`, remplacer le corps de
`_public_position` (lignes 37-44) par :

```python
    return {
        "ticker": position.get("ticker"),
        "name": position.get("name"),
        "index": position.get("index"),
        "quantite": position.get("quantite"),
        "prix_entree": position.get("prix_execution_reference"),
        "date_entree": position.get("date_entree"),
        "prix_actuel": position.get("prix_actuel"),
        "valeur_actuelle_eur": position.get("valeur_actuelle_eur"),
        "pnl_eur": position.get("pnl_eur"),
        "pnl_eur_pct": position.get("pnl_eur_pct"),
    }
```

Mettre a jour la derniere phrase de la docstring de la fonction (ligne
35-36) pour mentionner ces 4 nouveaux champs, dans le meme style concis
que l'existant.

- [ ] **Step 8: Lancer toute la suite ibkr_bot**

Run: `python -m pytest tests/ibkr_bot/ -q`
Expected: PASS, 0 echec

- [ ] **Step 9: Commit**

```bash
git add ibkr_bot/api.py ibkr_bot/status_publish.py tests/ibkr_bot/test_api.py tests/ibkr_bot/test_status_publish.py
git commit -m "feat(ibkr_bot): expose le P&L en euros via /dashboard et le statut dry_run"
```

---

### Task 3: Afficher le P&L en euros dans le panneau "Bot Actions"

**Files:**
- Modify: `docs/index.html:2386-2408` (`ibkrBotPositionsHtml`)

**Interfaces:**
- Consomme : `prix_actuel`, `valeur_actuelle_eur`, `pnl_eur`, `pnl_eur_pct`
  sur chaque objet `position` (produits par la Task 2, memes noms de
  champs que renvoyes par `/dashboard` ET `docs/ibkr_bot_status.json`) ;
  `formatPrice(v)` et `formatPct(v)` (deja definies plus bas dans le
  fichier, lignes 3964/3972) ; variables CSS `--gold`/`--rust` (deja
  utilisees pour ce meme motif gain/perte dans `portfolioPositionsHtml`,
  ligne 2099).
- Pas de nouveau fichier de test (aucun harnais de test JS dans ce
  depot pour `docs/*.html`/`docs/*.js` — verification visuelle
  uniquement, voir Step 3).

- [ ] **Step 1: Remplacer `ibkrBotPositionsHtml`**

Dans `docs/index.html`, remplacer la fonction (lignes 2394-2408) par :

```javascript
// Positions OUVERTES par le bot, memes noms de champs que
// status_publish._public_position cote ibkr_bot (ticker, name, index,
// quantite, prix_entree, date_entree, et depuis le plan FX P&L du
// 2026-10-03 : prix_actuel, valeur_actuelle_eur, pnl_eur, pnl_eur_pct —
// jamais conid). Utilisee pour DEUX sources distinctes : le statut
// public (docs/ibkr_bot_status.json, positions presentes seulement en
// dry_run) et le tableau de bord protege par jeton (/dashboard, positions
// toujours presentes, voir ibkr_bot/api.py — le jeton change le modele
// de menace, voir la spec API §4.3). Chaque ligne renvoie vers la fiche
// entreprise, meme pattern que portfolioPositionsHtml ("Tes positions").
// Les champs de P&L peuvent etre null (position du jour meme, pas encore
// valorisee par un batch — voir daily._valoriser_positions) : la ligne
// retombe alors sur le seul prix d'entree, sans P&L affiche.
function ibkrBotPositionsHtml(positions) {
  if (!positions || positions.length === 0) {
    return `<div class="empty">Aucune position ouverte.</div>`;
  }
  return positions.map(p => `
    <div class="portfolio-position-row">
      <div class="portfolio-position-main">
        <a class="company-name" href="#indices/${encodeURIComponent(p.ticker)}">${escHtml(p.name || p.ticker)}</a>
        <span class="hero-sub">${escHtml(p.ticker)}${p.quantite != null ? ` · ${p.quantite} titre${p.quantite > 1 ? 's' : ''}` : ''}${p.date_entree ? ` · entrée ${p.date_entree}` : ''}</span>
      </div>
      <div class="portfolio-position-pnl">
        <span class="price-value">${p.valeur_actuelle_eur != null ? formatPrice(p.valeur_actuelle_eur) : (p.prix_entree != null ? formatPriceForIndex(p.prix_entree, p.index) : '—')}</span>
        ${p.pnl_eur != null ? `<span style="color:${p.pnl_eur >= 0 ? 'var(--gold)' : 'var(--rust)'}">${p.pnl_eur >= 0 ? '+' : ''}${formatPrice(p.pnl_eur)} (${formatPct(p.pnl_eur_pct)})</span>` : ''}
      </div>
    </div>`).join('');
}
```

- [ ] **Step 2: Verifier qu'aucune autre fonction ne depend de l'ancienne forme**

Run: `grep -n "ibkrBotPositionsHtml" docs/index.html`
Expected : exactement 3 occurrences (la definition + les deux appels
deja existants, `renderIbkrBotStatus` et `renderIbkrBotDashboardBlock`) —
aucun changement de signature, donc aucun appelant a toucher.

- [ ] **Step 3: Verification visuelle**

Ouvrir `docs/index.html` dans un navigateur local (ou `python -m http.server`
depuis `docs/`), naviguer vers Portefeuille → Bot Actions, et verifier
manuellement (avec des donnees de `docs/ibkr_bot_status.json` bidouillees
localement si besoin pour simuler une position avec `pnl_eur` negatif ET
une avec `pnl_eur` positif) que :
  - le montant en euros s'affiche a la place du prix brut quand
    `valeur_actuelle_eur` est present ;
  - le P&L s'affiche en couleur or (`--gold`) si positif, rouille
    (`--rust`) si negatif ;
  - une position sans ces champs (ancien format, ou position du jour
    meme) retombe proprement sur le seul prix d'entree, sans erreur JS
    dans la console.

- [ ] **Step 4: Commit**

```bash
git add docs/index.html
git commit -m "feat(site): affiche le P&L en euros dans le panneau Bot Actions"
```
