# Corrections de l'audit Bot Actions (2026-10-02) — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corriger les 2 problèmes critiques et 6 problèmes importants trouvés par l'audit du 2026-10-02 (voir Spec) avant tout passage en réel du bot Actions IBKR.

**Architecture:** Huit correctifs indépendants, chacun dans son propre fichier-cible (`indices_score.py` pour les 2 critiques, `ibkr_bot/portfolio.py`/`signals.py`/`daily.py` pour les 6 importants). Chaque tâche ajoute un garde-fou ou une correction ciblée sans changer la forme des interfaces existantes (paramètres optionnels avec défaut rétrocompatible partout où c'est possible), pour que les appelants existants continuent de fonctionner sans modification.

**Tech Stack:** Python 3.14, pytest. Aucune nouvelle dépendance.

**Spec:** Synthèse des deux audits indépendants du 2026-10-02 (méthodologie du scoring + intégration ibkr_bot), publiée en Artifact : https://claude.ai/artifact/1M2ES5x6H91zTWwEWWYMSx — ce plan couvre les sections "Problèmes critiques" et "Problèmes importants" de ce document. Les points "Mineurs" en sont volontairement exclus (hors scope de ce plan — nécessitent soit une vérification en conditions réelles impossible hors ligne, soit sont de pure cosmétique).

## Global Constraints

- Tous les fichiers touchés ont une suite de tests existante (`tests/test_indices_score.py`, `tests/ibkr_bot/test_portfolio.py`, `tests/ibkr_bot/test_daily.py`, `tests/ibkr_bot/test_signals.py`) — `pytest tests/ -q` doit rester à 0 échec après CHAQUE tâche.
- Tout nouveau paramètre de fonction existante est optionnel avec une valeur par défaut qui reproduit EXACTEMENT le comportement actuel — aucun appelant existant (y compris dans `indices_score.py`/`ibkr_bot/` eux-mêmes) ne doit changer de comportement sans que la tâche ne le modifie explicitement.
- Convention du projet : commentaires en français, expliquant le POURQUOI (pas le quoi), citant la source de la décision (ici : "audit 2026-10-02").
- `dry_run`/`kill_switch` restent la seule autorité pour l'envoi d'un ordre réel — aucune tâche de ce plan ne doit créer un second chemin de décision.
- Commits fréquents, un par tâche minimum, format `fix(scope): description (audit 2026-10-02)`.

---

### Task 1: Plancher percentile sur l'alerte "entree"

**Contexte (Critique n°1 de l'audit) :** `compute_company_alerts` déclenche l'alerte "entree" dès que `composite_raw > HYSTERESIS_BAND` (5.0) — un seuil que 73-89% des sociétés de chaque profil dépassent. Le score percentile recalibré (`company["score"]`, échelle -100/+100, 0 = médiane du profil) n'intervient jamais dans cette décision alors qu'il existe précisément pour normaliser entre profils. Il faut exiger EN PLUS que le score percentile soit au moins à la médiane de son profil — mais seulement pour les profils réellement recalibrés (`score_recalibrated=True`) : un profil resté sur le score brut (pool < 20, ex. trust) n'a pas de score percentile comparable, le nouveau plancher ne doit donc s'appliquer qu'aux profils recalibrés.

**Files:**
- Modify: `indices_score.py:3789-3901` (fonction `compute_company_alerts`), `indices_score.py:4969-4974` (site d'appel dans `_attach_alerts_and_update_history`)
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Consumes: `company["score"]` (déjà calculé par `recalibrate_scores_by_profile`, float, -100 à +100).
- Produces: `compute_company_alerts(..., score: float | None = None)` — nouveau paramètre nommé, optionnel, défaut `None` (reproduit le comportement actuel si omis — AUCUN appelant de test existant qui ne le fournit pas ne doit changer de résultat).

- [ ] **Step 1: Lire le contexte exact de la fonction et du site d'appel**

Relire `indices_score.py:3789-3901` et `indices_score.py:4957-4974` dans le fichier réel avant de modifier quoi que ce soit — ce plan cite des numéros de ligne qui peuvent avoir légèrement bougé entre l'audit et l'implémentation.

- [ ] **Step 2: Écrire les tests qui échouent**

Ajouter dans `tests/test_indices_score.py` (créer la section si elle n'existe pas déjà, à côté des tests existants de `compute_company_alerts` — chercher `def test_compute_company_alerts` pour les localiser et respecter leurs conventions de nommage/fixtures) :

```python
def test_compute_company_alerts_no_entree_when_percentile_score_below_floor():
    """Audit 2026-10-02, critique n°1 : un profil recalibre (percentile)
    dont le score reste sous la mediane (0) ne doit jamais declencher
    "entree", meme si le score BRUT depasse le seuil d'hysteresis --
    c'etait le bug reel (GLE.PA, score affiche -75, alerte active)."""
    alerts = indices_score.compute_company_alerts(
        "GLE.PA", composite_raw=42.0, current_price=25.0, entry_price=25.5,
        previous_history=[], score_recalibrated=True, score=-75.0,
    )
    assert not any(a["kind"] == "entree" for a in alerts)


def test_compute_company_alerts_entree_when_percentile_score_at_or_above_floor():
    """Meme scenario brut, mais score percentile a la mediane ou au-dessus
    -- l'alerte doit toujours se declencher (non-regression)."""
    alerts = indices_score.compute_company_alerts(
        "OR.PA", composite_raw=42.0, current_price=25.0, entry_price=25.5,
        previous_history=[], score_recalibrated=True, score=0.0,
    )
    assert any(a["kind"] == "entree" for a in alerts)


def test_compute_company_alerts_entree_ignores_percentile_floor_when_score_raw_profile():
    """Un profil reste sur le score brut (score_recalibrated=False, ex.
    trust) n'a pas de score percentile comparable -- le plancher ne doit
    pas s'appliquer, meme si `score` n'est pas fourni (None)."""
    alerts = indices_score.compute_company_alerts(
        "TRUST.L", composite_raw=42.0, current_price=25.0, entry_price=25.5,
        previous_history=[], score_recalibrated=False, score=None,
    )
    assert any(a["kind"] == "entree" for a in alerts)


def test_compute_company_alerts_entree_default_score_none_preserves_old_behavior():
    """Un appelant qui ne fournit pas `score` (comportement d'avant ce
    correctif) ne doit jamais etre bloque par le nouveau plancher, meme
    si score_recalibrated=True -- retrocompatibilite stricte."""
    alerts = indices_score.compute_company_alerts(
        "XX.PA", composite_raw=42.0, current_price=25.0, entry_price=25.5,
        previous_history=[], score_recalibrated=True,
    )
    assert any(a["kind"] == "entree" for a in alerts)
```

- [ ] **Step 2b: Lancer les tests, vérifier qu'ils échouent**

Run: `python -m pytest tests/test_indices_score.py -k "percentile_floor or score_raw_profile or default_score_none" -v`
Expected: 4 échecs (`TypeError: compute_company_alerts() got an unexpected keyword argument 'score'`).

- [ ] **Step 3: Ajouter la constante**

Dans `indices_score.py`, juste après `HYSTERESIS_BAND = 5.0` (ligne ~3759) :

```python
# Plancher percentile sur l'alerte "entree" (audit 2026-10-02, critique
# n°1) : le seuil d'hysterese ci-dessus porte sur le score BRUT, qui
# filtre si peu (73-89% de chaque profil le depasse) que le score
# percentile (normalise entre profils, -100/+100, 0 = mediane) n'avait
# AUCUN role dans la decision d'ouvrir une position -- des alertes
# "entree" etaient actives sur des societes affichees "Tres fragile"
# (ex. GLE.PA, score -75). 0.0 = au moins la mediane de son profil, pas
# un seuil arbitraire plus strict -- coherent avec "entree" qui doit
# rester un signal frequent, pas reserve au dernier decile.
ENTRY_SCORE_PERCENTILE_FLOOR = 0.0
```

- [ ] **Step 4: Modifier la signature et le docstring de `compute_company_alerts`**

Dans `indices_score.py`, la signature de la fonction (ligne ~3789-3794) devient :

```python
def compute_company_alerts(
    ticker: str, composite_raw: float, current_price: float | None,
    entry_price: float | None, previous_history: list[dict],
    news_items: list[dict] | None = None, stage_label: str | None = None,
    score_recalibrated: bool = True, score: float | None = None,
) -> list[dict]:
```

Ajouter au docstring, après le paragraphe sur `score_recalibrated` (juste avant `Ne lève jamais d'exception` vers la ligne ~3817) :

```
    `score` (optionnel, audit 2026-10-02 critique n°1) : le score
    PERCENTILE recalibré de la société (company["score"], -100/+100, 0 =
    médiane du profil) -- quand `score_recalibrated` est True, l'alerte
    "entree" exige maintenant AUSSI `score >= ENTRY_SCORE_PERCENTILE_FLOOR`
    (0.0), en plus des conditions déjà existantes. `None` (défaut,
    rétrocompatible) désactive ce plancher, même comportement qu'avant ce
    correctif -- un appelant qui ne fournit pas `score` doit explicitement
    le faire pour bénéficier du garde-fou. Quand `score_recalibrated` est
    False (profil resté sur le score brut, ex. trust), ce plancher ne
    s'applique jamais : il n'y a pas de score percentile comparable.
```

- [ ] **Step 5: Modifier la condition `score_favorable`**

Dans `indices_score.py`, remplacer (vers la ligne ~3891-3895) :

```python
    score_favorable = (
        composite_raw > HYSTERESIS_BAND if abs(composite_raw) > HYSTERESIS_BAND
        else (last_regime is not None and last_regime > HYSTERESIS_BAND)
    )
    if score_favorable and near_entry and stage_label != "Déclin":
```

par :

```python
    score_favorable = (
        composite_raw > HYSTERESIS_BAND if abs(composite_raw) > HYSTERESIS_BAND
        else (last_regime is not None and last_regime > HYSTERESIS_BAND)
    )
    # Plancher percentile (audit 2026-10-02, critique n°1) : ne s'applique
    # que si le profil est recalibré ET qu'un score percentile a été
    # fourni -- voir le docstring du paramètre `score` ci-dessus pour le
    # choix de rétrocompatibilité.
    percentile_favorable = (
        not score_recalibrated or score is None or score >= ENTRY_SCORE_PERCENTILE_FLOOR
    )
    if score_favorable and percentile_favorable and near_entry and stage_label != "Déclin":
```

- [ ] **Step 6: Lancer les tests, vérifier qu'ils passent**

Run: `python -m pytest tests/test_indices_score.py -k "percentile_floor or score_raw_profile or default_score_none" -v`
Expected: 4 réussites.

- [ ] **Step 7: Mettre à jour le site d'appel réel**

Dans `indices_score.py`, l'appel dans `_attach_alerts_and_update_history` (vers la ligne ~4969-4974) devient :

```python
            company["alerts"] = compute_company_alerts(
                company["ticker"], company["score_raw"], company["current_price"],
                company["entry_price"], ticker_history,
                news_items=company.get("news", []), stage_label=company.get("stage_label"),
                score_recalibrated=company.get("score_recalibrated", True),
                score=company.get("score"),
            )
```

- [ ] **Step 8: Lancer toute la suite de tests du fichier**

Run: `python -m pytest tests/test_indices_score.py -q`
Expected: 0 échec (503 tests avant cette tâche, +4 nouveaux = 507 attendus, aucune régression).

- [ ] **Step 9: Vérification empirique sur les données réelles**

Avec `docs/indices.json` du dépôt (déjà présent, pas besoin de réseau), vérifier combien de sociétés avec une alerte "entree" actuelle auraient leur alerte supprimée par ce correctif :

```python
import json
with open("docs/indices.json", encoding="utf-8") as fh:
    data = json.load(fh)
entrees = [c for c in data["companies"] if any(a["kind"] == "entree" for a in c.get("alerts", []))]
sous_plancher = [c for c in entrees if c.get("score_recalibrated", True) and (c.get("score") or 0) < 0]
print(f"{len(sous_plancher)}/{len(entrees)} alertes entree actuelles seraient supprimees par le plancher")
for c in sous_plancher:
    print(f"  {c['ticker']} : score={c.get('score')}")
```

Documenter le résultat dans le message de commit (combien d'alertes changent, quels tickers) — ces données ne seront régénérées qu'au prochain run du workflow GitHub Actions, donc `docs/indices.json` ne reflétera le correctif qu'après ce run, pas immédiatement.

Vérifier aussi, dans la même passe, si ce correctif atténue le biais "profil financier" relevé par l'audit (Important, point I-A : médiane brute financier 41,0 contre standard 24,2, 7 alertes "entree" sur 11 concernent des financières alors qu'elles ne pèsent que 11% de l'univers) — ce plan ne lui consacre pas de tâche séparée car le plancher percentile de cette tâche neutralise une bonne partie de ce biais mécaniquement (le score percentile est déjà normalisé par profil, donc une financière "généreuse en brut mais médiocre dans son propre profil" peut désormais être filtrée). Ajouter au code ci-dessus :

```python
financieres_avant = sum(1 for c in entrees if c.get("is_financial"))
financieres_apres = sum(1 for c in entrees if c.get("is_financial")) - sum(
    1 for c in sous_plancher if c.get("is_financial"))
print(f"Alertes entree financieres : {financieres_avant}/{len(entrees)} avant -> "
      f"{financieres_apres}/{len(entrees) - len(sous_plancher)} apres")
```

Si le déséquilibre persiste fortement après ce correctif (ex. toujours >50% de financières alors qu'elles pèsent 11% de l'univers), le documenter dans le message de commit comme un point resté ouvert — ne pas construire de correctif supplémentaire dans cette tâche, se contenter de consigner le résultat pour une décision ultérieure.

- [ ] **Step 10: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "fix(indices_score): exige un score percentile >= mediane pour l'alerte entree (audit 2026-10-02, critique 1)"
```

---

### Task 2: Cache de repli pour le taux sans risque (panne FRED)

**Contexte (Critique n°2 de l'audit) :** `fetch_risk_free_rate` renvoie `None` sur tout échec réseau/API. En aval, `None` fait tomber `cost_of_capital`/`cost_of_equity` sur `COST_OF_CAPITAL_PROXY` (8.0%, une constante générique) au lieu du dernier taux réellement observé — une panne FRED transitoire d'une heure a généré de vrais nouveaux signaux d'achat le 2026-09-30 (DBK.DE, FME.DE, GLE.PA, SAB.MC sont apparues entre deux runs du même jour). Il faut persister le dernier taux connu PAR DEVISE et l'utiliser en repli avant de tomber sur le proxy générique.

**Files:**
- Modify: `indices_score.py:5226-5232` (début de `main()`)
- Create (via le code, pas littéralement un fichier à créer manuellement) : `docs/risk_free_rate_cache.json` — géré par les nouvelles fonctions ci-dessous, ne pas créer ce fichier à la main.
- Test: `tests/test_indices_score.py`

**Interfaces:**
- Produces: `load_risk_free_rate_cache(path=RISK_FREE_RATE_CACHE_PATH) -> dict` (devise → taux), `save_risk_free_rate_cache(rates: dict, path=RISK_FREE_RATE_CACHE_PATH) -> None`.
- Consumes: rien de nouveau — s'insère entre l'appel existant à `fetch_risk_free_rate` et son utilisation dans `main()`.

- [ ] **Step 1: Lire le contexte exact**

Relire `indices_score.py:4302-4360` (`fetch_risk_free_rate` et les constantes `RISK_FREE_SERIES_BY_CURRENCY`) et `indices_score.py:5226-5232` (début de `main()`) dans le fichier réel. Repérer aussi le pattern existant `load_dividend_history`/`update_dividend_history` (chercher `DIVIDEND_HISTORY_PATH` dans le fichier, vers la ligne ~3283) — les nouvelles fonctions de cache doivent suivre EXACTEMENT ce même style (lecture tolérante, écriture atomique simple, jamais d'exception qui remonte).

- [ ] **Step 2: Écrire les tests qui échouent**

Ajouter dans `tests/test_indices_score.py` :

```python
def test_load_risk_free_rate_cache_empty_when_file_absent(tmp_path):
    path = tmp_path / "risk_free_rate_cache.json"
    assert indices_score.load_risk_free_rate_cache(path=str(path)) == {}


def test_load_risk_free_rate_cache_empty_when_file_corrupt(tmp_path):
    path = tmp_path / "risk_free_rate_cache.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert indices_score.load_risk_free_rate_cache(path=str(path)) == {}


def test_save_then_load_risk_free_rate_cache_round_trips(tmp_path):
    path = tmp_path / "risk_free_rate_cache.json"
    indices_score.save_risk_free_rate_cache({"EUR": 4.0, "USD": 4.5}, path=str(path))
    assert indices_score.load_risk_free_rate_cache(path=str(path)) == {"EUR": 4.0, "USD": 4.5}


def test_save_risk_free_rate_cache_never_raises_on_bad_path(tmp_path):
    """Degrade silencieusement (meme contrat que update_dividend_history) :
    un chemin illisible ne doit jamais faire echouer main()."""
    bad_path = str(tmp_path / "no_such_dir" / "sub" / "cache.json")
    # Le dossier parent n'existe pas et n'est volontairement pas créable
    # (chemin sous un fichier, pas un dossier) pour forcer l'échec.
    (tmp_path / "no_such_dir").write_text("fichier, pas un dossier", encoding="utf-8")
    indices_score.save_risk_free_rate_cache({"EUR": 4.0}, path=bad_path)  # ne doit pas lever
```

- [ ] **Step 2b: Lancer les tests, vérifier qu'ils échouent**

Run: `python -m pytest tests/test_indices_score.py -k risk_free_rate_cache -v`
Expected: 4 échecs (`AttributeError: module 'indices_score' has no attribute 'load_risk_free_rate_cache'`).

- [ ] **Step 3: Implémenter les deux fonctions**

Dans `indices_score.py`, juste après le bloc `RISK_FREE_SERIES_BY_CURRENCY = {...}` (vers la ligne ~4328) :

```python
RISK_FREE_RATE_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "docs", "risk_free_rate_cache.json"
)


def load_risk_free_rate_cache(path: str = RISK_FREE_RATE_CACHE_PATH) -> dict:
    """{devise: taux} du dernier run où fetch_risk_free_rate a réussi pour
    cette devise. {} si le fichier est absent ou corrompu, jamais
    d'exception -- même contrat que load_dividend_history. Sert de repli
    quand une panne FRED transitoire renvoie None (audit 2026-10-02,
    critique n°2) : le dernier taux réellement observé est une bien
    meilleure estimation que COST_OF_CAPITAL_PROXY (8%, une constante
    générique sans rapport avec le marché réel ce jour-là)."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save_risk_free_rate_cache(rates: dict, path: str = RISK_FREE_RATE_CACHE_PATH) -> None:
    """Persiste `rates` (devise -> taux) tel quel -- c'est à l'appelant de
    ne passer que les devises réellement récupérées ce run (jamais un
    repli ne doit écraser un taux précédemment observé avec succès).
    Dégrade silencieusement sur erreur d'écriture, ne doit jamais faire
    échouer main() (même contrat que update_dividend_history)."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rates, fh, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except Exception as e:
        print(f"Erreur cache taux sans risque : {e}")
```

- [ ] **Step 4: Lancer les tests, vérifier qu'ils passent**

Run: `python -m pytest tests/test_indices_score.py -k risk_free_rate_cache -v`
Expected: 4 réussites.

- [ ] **Step 5: Écrire le test du branchement dans `main()`**

Ce test porte sur la LOGIQUE de repli, pas sur `main()` elle-même (qui fait de vrais appels réseau, non testable unitairement) — ajouter :

```python
def test_risk_free_rate_with_cache_fallback_uses_cache_on_fetch_failure(monkeypatch, tmp_path):
    """Reproduit exactement la boucle de main() (Step 6 ci-dessous) : une
    devise dont le fetch echoue (None) doit retomber sur le cache, pas
    rester None."""
    cache_path = tmp_path / "risk_free_rate_cache.json"
    indices_score.save_risk_free_rate_cache({"EUR": 4.0}, path=str(cache_path))

    def fake_fetch(series_id):
        return None  # simule la panne FRED

    monkeypatch.setattr(indices_score, "fetch_risk_free_rate", fake_fetch)

    cache = indices_score.load_risk_free_rate_cache(path=str(cache_path))
    rates = {}
    for currency, series_id in indices_score.RISK_FREE_SERIES_BY_CURRENCY.items():
        fetched = indices_score.fetch_risk_free_rate(series_id)
        rates[currency] = fetched if fetched is not None else cache.get(currency)

    assert rates["EUR"] == 4.0  # repli sur le cache, pas None
```

- [ ] **Step 5b: Lancer le test, vérifier qu'il échoue**

Run: `python -m pytest tests/test_indices_score.py -k cache_fallback_uses_cache -v`
Expected: Ce test teste une boucle inline, pas encore le code de `main()` — il doit déjà PASSER à ce stade puisqu'il n'appelle que les fonctions du Step 3/4 déjà implémentées. S'il échoue, corriger le Step 3 avant de continuer (ce test sert de garde-fou de régression pour le Step 6, pas de TDD classique sur du code encore inexistant).

- [ ] **Step 6: Brancher le repli dans `main()`**

Dans `indices_score.py`, remplacer (vers la ligne ~5226-5232) :

```python
def main():
    # Un taux sans risque par devise (voir RISK_FREE_SERIES_BY_CURRENCY) —
    # un seul appel FRED par devise pour tout le run, pas par entreprise.
    risk_free_rate_by_currency = {
        currency: fetch_risk_free_rate(series_id)
        for currency, series_id in RISK_FREE_SERIES_BY_CURRENCY.items()
    }
```

par :

```python
def main():
    # Un taux sans risque par devise (voir RISK_FREE_SERIES_BY_CURRENCY) —
    # un seul appel FRED par devise pour tout le run, pas par entreprise.
    # Repli sur le dernier taux connu en cas d'echec (audit 2026-10-02,
    # critique n°2) : une panne FRED transitoire ne doit plus faire
    # retomber TOUTES les societes d'une devise sur COST_OF_CAPITAL_PROXY
    # (8%, une valeur generique) au lieu du dernier taux reellement
    # observe -- voir load_risk_free_rate_cache pour le detail.
    _risk_free_rate_cache = load_risk_free_rate_cache()
    risk_free_rate_by_currency = {}
    for currency, series_id in RISK_FREE_SERIES_BY_CURRENCY.items():
        fetched = fetch_risk_free_rate(series_id)
        risk_free_rate_by_currency[currency] = (
            fetched if fetched is not None else _risk_free_rate_cache.get(currency)
        )
    save_risk_free_rate_cache({
        currency: rate for currency, rate in risk_free_rate_by_currency.items()
        if rate is not None
    })
```

- [ ] **Step 7: Lancer toute la suite de tests du fichier**

Run: `python -m pytest tests/test_indices_score.py -q`
Expected: 0 échec (507 après Task 1, +5 nouveaux = 512 attendus).

- [ ] **Step 8: Ajouter le nouveau fichier de cache au `.gitignore`**

Ce fichier est un état runtime régénéré à chaque run réussi (même famille que `docs/dividend_history.json`, qui lui EST commité — vérifier dans `.gitignore` s'il y a une raison de ne PAS le committer). Lire `.gitignore` et la ligne concernant `docs/dividend_history.json` si elle existe ; si `dividend_history.json` est committé (pas dans `.gitignore`), laisser `risk_free_rate_cache.json` être committé aussi, par cohérence (le run GitHub Actions a besoin du cache précédent pour fonctionner en cas de panne, donc il DOIT persister entre les runs — un fichier non commité serait perdu à chaque run sur un runner éphémère). Ne rien ajouter au `.gitignore` sauf si `dividend_history.json` y est déjà, auquel cas suivre le même choix.

- [ ] **Step 9: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "fix(indices_score): cache de repli pour le taux sans risque (audit 2026-10-02, critique 2)"
```

---

### Task 3: Ne jamais adopter une quantité IBKR supérieure à la quantité locale du bot

**Contexte (Important, audit) :** `portfolio.reconcile` adopte toujours la quantité IBKR (`active["quantite"] = quantite_ibkr`) dès qu'elle diffère de la quantité locale, même si elle est SUPÉRIEURE — ce qui peut arriver si le conid d'une position simulée en dry_run (ou clôturée puis rouverte manuellement par l'utilisateur) coïncide avec une action que l'utilisateur détient personnellement sur ce même compte. Si le bot adopte cette quantité gonflée puis déclenche une sortie, il vendrait la position personnelle de l'utilisateur en plus de la sienne.

**Files:**
- Modify: `ibkr_bot/portfolio.py:308-341` (fonction `reconcile`)
- Test: `tests/ibkr_bot/test_portfolio.py`

**Interfaces:**
- Produces: `reconcile(...)` renvoie toujours le même dict à 4 clés (`actives`, `cloturees_hors_bot`, `anomalies_quantite`, `ignorees`) — mais une anomalie où `quantite_ibkr > quantite_locale` porte désormais une clé supplémentaire `"quantite_ibkr_superieure": True`, et `active["quantite"]` n'est PAS mise à jour dans ce cas précis (reste la quantité locale).

- [ ] **Step 1: Lire le contexte exact**

Relire `ibkr_bot/portfolio.py:267-351` (fonction `reconcile` en entier) dans le fichier réel.

- [ ] **Step 2: Écrire le test qui échoue**

Ajouter dans `tests/ibkr_bot/test_portfolio.py`, juste après `test_reconcile_lets_the_ibkr_quantity_win_and_logs_the_anomaly` (chercher ce nom pour le localiser, vers la ligne ~819) :

```python
def test_reconcile_never_adopts_an_ibkr_quantity_larger_than_local():
    """Audit 2026-10-02 : une quantite IBKR SUPERIEURE a la quantite
    locale du bot peut signifier qu'un conid partage avec une position
    personnelle de l'utilisateur a ete adopte -- ne jamais gonfler la
    quantite geree par le bot au-dela de ce qu'il a lui-meme ouvert, pour
    ne jamais risquer de vendre plus que sa propre position."""
    locales = [_bot_position("A.PA", conid=4901, quantite=5)]
    chez_ibkr = [{"conid": 4901, "position": 12.0, "currency": "EUR"}]

    result = portfolio.reconcile(locales, chez_ibkr)

    assert result["actives"][0]["quantite"] == 5  # pas 12 : la quantite locale est gardee
    assert result["anomalies_quantite"] == [{
        "ticker": "A.PA", "conid": 4901, "quantite_locale": 5, "quantite_ibkr": 12,
        "quantite_ibkr_superieure": True,
    }]


def test_reconcile_still_adopts_a_smaller_ibkr_quantity():
    """Non-regression : la quantite IBKR plus PETITE que la locale (vente
    partielle hors bot, par ex.) continue d'etre adoptee comme avant --
    seule la direction "superieure" change de comportement."""
    locales = [_bot_position("A.PA", conid=4901, quantite=5)]
    chez_ibkr = [{"conid": 4901, "position": 3.0, "currency": "EUR"}]

    result = portfolio.reconcile(locales, chez_ibkr)

    assert result["actives"][0]["quantite"] == 3
    assert result["anomalies_quantite"] == [{
        "ticker": "A.PA", "conid": 4901, "quantite_locale": 5, "quantite_ibkr": 3,
    }]
    assert "quantite_ibkr_superieure" not in result["anomalies_quantite"][0]
```

- [ ] **Step 3: Lancer les tests, vérifier qu'ils échouent**

Run: `python -m pytest tests/ibkr_bot/test_portfolio.py -k "never_adopts_an_ibkr_quantity_larger or still_adopts_a_smaller" -v`
Expected: `test_reconcile_never_adopts_an_ibkr_quantity_larger_than_local` échoue (`assert 12 == 5`). `test_reconcile_still_adopts_a_smaller_ibkr_quantity` doit déjà passer (comportement inchangé) — si ce n'est pas le cas, s'arrêter et comprendre pourquoi avant de continuer.

- [ ] **Step 4: Modifier `reconcile`**

Dans `ibkr_bot/portfolio.py`, remplacer (vers la ligne ~332-340) :

```python
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
```

par :

```python
        active = dict(position)
        quantite_locale = position.get("quantite")
        if quantite_ibkr != quantite_locale:
            anomalie = {
                "ticker": position["ticker"],
                "conid": conid,
                "quantite_locale": quantite_locale,
                "quantite_ibkr": quantite_ibkr,
            }
            if quantite_ibkr > (quantite_locale or 0):
                # Audit 2026-10-02 : une quantite IBKR SUPERIEURE a la
                # quantite locale peut signifier un conid partage avec
                # une position personnelle de l'utilisateur sur ce meme
                # compte -- ne jamais l'adopter, pour ne jamais risquer
                # de vendre plus que ce que le bot a lui-meme ouvert. La
                # quantite locale est gardee telle quelle (active deja
                # une copie de `position`, donc `active["quantite"]` vaut
                # deja quantite_locale sans rien faire de plus).
                anomalie["quantite_ibkr_superieure"] = True
            else:
                active["quantite"] = quantite_ibkr
            anomalies.append(anomalie)
        actives.append(active)
```

- [ ] **Step 5: Lancer les tests, vérifier qu'ils passent**

Run: `python -m pytest tests/ibkr_bot/test_portfolio.py -k "never_adopts_an_ibkr_quantity_larger or still_adopts_a_smaller" -v`
Expected: 2 réussites.

- [ ] **Step 6: Lancer toute la suite de tests ibkr_bot**

Run: `python -m pytest tests/ibkr_bot/ -q`
Expected: 0 échec.

- [ ] **Step 7: Commit**

```bash
git add ibkr_bot/portfolio.py tests/ibkr_bot/test_portfolio.py
git commit -m "fix(ibkr_bot): ne jamais adopter une quantite IBKR superieure a la quantite locale (audit 2026-10-02)"
```

---

### Task 4: Refuser un ordre réel si le batch a été planifié en dry_run puis basculé en réel entre-temps

**Contexte (Important, audit) :** `_place_order` relit l'état depuis le disque avant CHAQUE ordre (déjà correct), mais ne gère qu'UNE SEULE direction de dérive : réel→dry_run mid-batch (déjà traité par `mode_attendu == "reel"` + état devenu dry_run → `annule_interruption`). La direction inverse n'est pas gardée : si `mode_attendu == "dry_run"` mais l'état sur disque est maintenant réel (l'opérateur a basculé le switch PENDANT le batch), le code actuel envoie un VRAI ordre pour une position qui n'a jamais existé que simulée — un risque réel de vente à découvert non couverte.

**Files:**
- Modify: `ibkr_bot/daily.py:251-304` (fonction `_place_order`)
- Test: `tests/ibkr_bot/test_daily.py`

**Interfaces:**
- Consumes: rien de nouveau.
- Produces: `_place_order` peut désormais renvoyer `{"statut": "annule_derive", ...}` en plus des statuts existants (`"simule"`, `"annule_interruption"`, `"erreur"`, succès normal).

- [ ] **Step 1: Lire le contexte exact**

Relire `ibkr_bot/daily.py:251-330` (`_place_order` en entier, y compris son docstring) dans le fichier réel. Chercher aussi `test_daily.py:1304` (le test cité par l'audit pour le cas symétrique déjà géré — `annule_interruption`) pour comprendre le style de test attendu ici.

- [ ] **Step 2: Écrire le test qui échoue**

Chercher dans `tests/ibkr_bot/test_daily.py` le test couvrant `annule_interruption` (probablement nommé `test_place_order_...interruption...` ou similaire, autour de la ligne 1304) pour copier son style de setup (mocks de `gw`, `state_path` avec `tmp_path`, etc.), puis ajouter un test symétrique :

```python
def test_place_order_refuses_a_real_send_when_mode_attendu_was_dry_run_but_state_is_now_live(tmp_path):
    """Audit 2026-10-02 : direction inverse du cas annule_interruption deja
    teste -- un ordre planifie en dry_run (mode_attendu="dry_run") dont
    l'etat disque est maintenant reel (bascule operateur mid-batch) ne
    doit JAMAIS partir reellement : la position pour laquelle cet ordre
    est envoye n'a jamais existe que simulee."""
    state_path = str(tmp_path / "state.json")
    state.save_state({"kill_switch": False, "dry_run": False}, state_path)
    gw = Mock()  # ne doit JAMAIS etre appele

    resultat = daily._place_order(
        gw, "https://127.0.0.1:4001", "U123", ticker="A.PA", conid=4901,
        side="SELL", quantity=5, prix_reference_cotation=100.0,
        state_path=state_path, mode_attendu="dry_run",
    )

    assert resultat["statut"] == "annule_derive"
    assert resultat["order_id"] is None
    gw.place_market_order.assert_not_called()
```

Adapter les imports/mocks exacts (`Mock`, `state`) au style déjà utilisé en haut de `tests/ibkr_bot/test_daily.py` — chercher comment le test de `annule_interruption` construit son `gw` factice et son `state_path`, et reproduire EXACTEMENT ce même style plutôt que d'improviser un style différent dans le même fichier.

- [ ] **Step 3: Lancer le test, vérifier qu'il échoue**

Run: `python -m pytest tests/ibkr_bot/test_daily.py -k mode_attendu_was_dry_run_but_state_is_now_live -v`
Expected: échec (`assert 'simule' == 'annule_derive'` ou équivalent — l'état lu est `dry_run=False`, donc le code actuel tombe dans la branche d'envoi réel, pas dans le garde existant).

- [ ] **Step 4: Ajouter le garde dans `_place_order`**

Dans `ibkr_bot/daily.py`, juste après `etat = state.load_state(state_path)` (ligne ~289) et AVANT le `if etat["kill_switch"] or etat["dry_run"]:` existant :

```python
    etat = state.load_state(state_path)
    # Audit 2026-10-02 : direction inverse du cas annule_interruption
    # ci-dessous. Un ordre planifie quand le batch croyait etre en
    # dry_run (mode_attendu="dry_run") mais dont l'etat disque est
    # maintenant reel (bascule operateur PENDANT le batch) ne doit
    # jamais partir reellement -- la position correspondante n'a jamais
    # existe que simulee (jamais ouverte chez IBKR), un ordre reel pour
    # elle (en particulier une vente) serait une position a decouvert
    # non couverte sur un compte sur marge.
    if mode_attendu == "dry_run" and not (etat["kill_switch"] or etat["dry_run"]):
        return {"statut": "annule_derive", "order_id": None,
                "prix_execution_cotation": None, "prix_execution_estime": False,
                "commission": None,
                "detail": (
                    "execution annulee : ce batch avait planifie cet ordre en "
                    "dry_run mais l'etat sur disque est passe en reel entre-temps "
                    "-- aucun ordre envoye, position laissee INTACTE dans "
                    "positions.json pour verification manuelle")}
    if etat["kill_switch"] or etat["dry_run"]:
```

(Le `if etat["kill_switch"] or etat["dry_run"]:` qui suit immédiatement est la ligne déjà existante — ne pas la dupliquer, juste insérer le nouveau bloc avant elle.)

- [ ] **Step 5: Lancer le test, vérifier qu'il passe**

Run: `python -m pytest tests/ibkr_bot/test_daily.py -k mode_attendu_was_dry_run_but_state_is_now_live -v`
Expected: 1 réussite.

- [ ] **Step 6: Lancer toute la suite de tests ibkr_bot**

Run: `python -m pytest tests/ibkr_bot/ -q`
Expected: 0 échec (vérifier en particulier que le test existant du cas `annule_interruption` passe toujours — les deux gardes ne doivent jamais se chevaucher : l'un porte sur `mode_attendu == "reel"`, l'autre sur `mode_attendu == "dry_run"`, mutuellement exclusifs).

- [ ] **Step 7: Commit**

```bash
git add ibkr_bot/daily.py tests/ibkr_bot/test_daily.py
git commit -m "fix(ibkr_bot): refuse un ordre reel planifie en dry_run si l'etat bascule en reel mid-batch (audit 2026-10-02)"
```

---

### Task 5: Garde-fou redondant sur la phase Weinstein "Déclin" côté bot

**Contexte (Important, audit) :** `signals.py::collect_new_signals` fait une confiance totale au filtrage déjà fait par `indices_score.compute_company_alerts` (qui bloque déjà "entree" en phase Déclin) — mais ne relit jamais `stage_label` lui-même. Si ce calcul amont échoue ou a un bug, rien côté bot ne rattrape l'erreur. `company.get("stage_label")` est déjà disponible dans `docs/indices.json` (lu via `companies_by_ticker`).

**Files:**
- Modify: `ibkr_bot/signals.py:65-109` (fonction `collect_new_signals`)
- Test: `tests/ibkr_bot/test_signals.py`

**Interfaces:**
- Produces: `collect_new_signals` peut désormais rejeter un signal avec `"raison": "phase_weinstein_declin"`.

- [ ] **Step 1: Lire le contexte exact**

Relire `ibkr_bot/signals.py:65-109` (`collect_new_signals` en entier) et le fichier de test associé `tests/ibkr_bot/test_signals.py` pour ses conventions de fixtures (chercher comment un `company`/`indices` factice est construit dans les tests existants).

- [ ] **Step 2: Écrire le test qui échoue**

Ajouter dans `tests/ibkr_bot/test_signals.py`, en suivant le style des fixtures déjà présentes dans ce fichier (adapter les noms exacts de helpers trouvés au Step 1) :

```python
def test_collect_new_signals_rejects_a_company_in_weinstein_decline_phase():
    """Audit 2026-10-02 : garde-fou redondant -- meme si indices_score
    n'avait pas deja bloque l'alerte "entree" en amont (bug hypothetique
    de ce cote-la), le bot ne doit jamais ouvrir une position sur une
    societe en phase Weinstein "Declin"."""
    indices = {
        "updated": "2026-10-02",
        "index_currency": {"CAC40": "EUR"},
        "companies": [{
            "ticker": "A.PA", "score": 10.0, "current_price": 100.0,
            "sector": "Industrie", "stage_label": "Déclin",
        }],
    }
    positions = [{
        "id": "A.PA-2026-10-02", "ticker": "A.PA", "status": "open",
        "entry_date": "2026-10-02", "index": "CAC40",
        "entry_price": 99.0, "target_exit_price": 130.0,
    }]

    signaux, rejets = signals.collect_new_signals(indices, positions, "2026-10-02")

    assert signaux == []
    assert rejets == [{"ticker": "A.PA", "raison": "phase_weinstein_declin"}]


def test_collect_new_signals_accepts_a_company_without_decline_phase():
    """Non-regression : une societe sans phase Declin (ou sans
    stage_label du tout) continue d'etre acceptee comme avant."""
    indices = {
        "updated": "2026-10-02",
        "index_currency": {"CAC40": "EUR"},
        "companies": [{
            "ticker": "A.PA", "score": 10.0, "current_price": 100.0,
            "sector": "Industrie", "stage_label": "Achat",
        }],
    }
    positions = [{
        "id": "A.PA-2026-10-02", "ticker": "A.PA", "status": "open",
        "entry_date": "2026-10-02", "index": "CAC40",
        "entry_price": 99.0, "target_exit_price": 130.0,
    }]

    signaux, rejets = signals.collect_new_signals(indices, positions, "2026-10-02")

    assert len(signaux) == 1
    assert signaux[0]["ticker"] == "A.PA"
    assert rejets == []
```

Si ces fixtures ne correspondent pas exactement au style déjà utilisé dans `tests/ibkr_bot/test_signals.py` (par ex. un helper `_company(...)`/`_position(...)` existe déjà), les réécrire avec ces helpers plutôt que de dupliquer un style différent dans le même fichier.

- [ ] **Step 3: Lancer les tests, vérifier le premier échoue**

Run: `python -m pytest tests/ibkr_bot/test_signals.py -k "decline_phase or without_decline_phase" -v`
Expected: `test_collect_new_signals_rejects_a_company_in_weinstein_decline_phase` échoue (le signal est actuellement accepté, `stage_label` n'est jamais lu). `test_collect_new_signals_accepts_a_company_without_decline_phase` doit déjà passer.

- [ ] **Step 4: Ajouter le garde dans `collect_new_signals`**

Dans `ibkr_bot/signals.py`, remplacer (vers la ligne ~87-90) :

```python
        company = companies_by_ticker.get(ticker)
        if company is None or _is_missing(company.get("score")):
            rejets.append({"ticker": ticker, "raison": "score_indisponible"})
            continue
```

par :

```python
        company = companies_by_ticker.get(ticker)
        if company is None or _is_missing(company.get("score")):
            rejets.append({"ticker": ticker, "raison": "score_indisponible"})
            continue
        # Garde-fou redondant (audit 2026-10-02) : indices_score bloque deja
        # l'alerte "entree" en phase Weinstein "Declin" en amont, mais
        # collect_new_signals ne relisait jamais stage_label lui-meme --
        # un seul point de defaillance en cas de bug/regression cote
        # scoring. Ne bloque QUE "Declin" explicite, jamais une phase
        # absente/None (qui reste acceptee, meme comportement qu'avant).
        if company.get("stage_label") == "Déclin":
            rejets.append({"ticker": ticker, "raison": "phase_weinstein_declin"})
            continue
```

- [ ] **Step 5: Lancer les tests, vérifier qu'ils passent**

Run: `python -m pytest tests/ibkr_bot/test_signals.py -k "decline_phase or without_decline_phase" -v`
Expected: 2 réussites.

- [ ] **Step 6: Lancer toute la suite de tests ibkr_bot**

Run: `python -m pytest tests/ibkr_bot/ -q`
Expected: 0 échec.

- [ ] **Step 7: Commit**

```bash
git add ibkr_bot/signals.py tests/ibkr_bot/test_signals.py
git commit -m "fix(ibkr_bot): garde-fou redondant sur la phase Weinstein Declin (audit 2026-10-02)"
```

---

### Task 6: Clôturer une position dont le ticker a disparu du roster d'indices

**Contexte (Important, audit) :** `portfolio.exit_reason` renvoie `None` (position intacte) dès que `company is None` — y compris quand la société a été RETIRÉE DÉFINITIVEMENT de l'indice (pas juste une donnée manquante ce run). Le paper-trading gère déjà ce cas (`ticker_retire_indice`, commit du 24/09) ; le bot n'a pas d'équivalent, une révision d'indice laisserait une vraie position bloquée sans stop-loss ni sortie possible.

**Files:**
- Modify: `ibkr_bot/portfolio.py:213-262` (`exit_reason` et `positions_to_close`)
- Modify: `ibkr_bot/daily.py` (site d'appel de `positions_to_close`, vers la ligne ~686-690)
- Test: `tests/ibkr_bot/test_portfolio.py`

**Interfaces:**
- Produces: `exit_reason(position, company, today, roster_tickers=None)` — nouveau paramètre optionnel, défaut `None` (comportement actuel inchangé si omis). `positions_to_close(open_positions, companies_by_ticker, today, roster_tickers=None)` — même nouveau paramètre, transmis tel quel à `exit_reason`.
- Consumes (dans `daily.py`) : `companies` (dict ticker→company déjà construit juste avant l'appel à `positions_to_close`, vers la ligne ~673-674) — `set(companies.keys())` EST le roster du jour.

- [ ] **Step 1: Lire le contexte exact**

Relire `ibkr_bot/portfolio.py:213-262` (`exit_reason` et `positions_to_close`) et `ibkr_bot/daily.py:669-695` (le site d'appel, section "6. Sorties") dans les fichiers réels.

- [ ] **Step 2: Écrire les tests qui échouent**

Ajouter dans `tests/ibkr_bot/test_portfolio.py`, juste après `test_exit_reason_none_when_the_ticker_disappeared_from_the_data` (vers la ligne ~602-604) :

```python
def test_exit_reason_ticker_retired_from_roster_when_removed_and_roster_given():
    """Audit 2026-10-02 : une societe retiree DEFINITIVEMENT de l'indice
    (absente des donnees du jour ET absente du roster complet fourni)
    doit se clore, pas rester bloquee indefiniment -- meme traitement
    que ticker_retire_indice cote paper-trading."""
    position = _bot_position("A.PA", prix_execution_reference=100.0)
    assert portfolio.exit_reason(
        position, None, "2026-09-14", roster_tickers={"B.PA", "C.PA"},
    ) == "ticker_retire_indice"


def test_exit_reason_none_when_company_missing_but_still_in_roster():
    """Donnee manquante CE RUN mais le ticker reste dans le roster complet
    (panne temporaire d'une source, pas une vraie radiation d'indice) --
    reste intacte, meme comportement qu'avant ce correctif."""
    position = _bot_position("A.PA", prix_execution_reference=100.0)
    assert portfolio.exit_reason(
        position, None, "2026-09-14", roster_tickers={"A.PA", "B.PA"},
    ) is None


def test_exit_reason_none_when_roster_not_provided_preserves_old_behavior():
    """Retrocompatibilite stricte : roster_tickers omis (defaut None) ->
    comportement identique a avant ce correctif, meme ticker disparu."""
    position = _bot_position("A.PA", prix_execution_reference=100.0)
    assert portfolio.exit_reason(position, None, "2026-09-14") is None
```

Et après `test_positions_to_close_leaves_untouched_what_has_no_data` (vers la ligne ~643-650) :

```python
def test_positions_to_close_closes_a_position_whose_ticker_left_the_roster():
    positions = [_bot_position("A.PA", prix_execution_reference=100.0)]
    companies = {}  # A.PA absent des donnees du jour

    result = portfolio.positions_to_close(
        positions, companies, "2026-09-14", roster_tickers={"B.PA"})

    assert len(result) == 1
    assert result[0]["close_reason"] == "ticker_retire_indice"
```

- [ ] **Step 3: Lancer les tests, vérifier qu'ils échouent**

Run: `python -m pytest tests/ibkr_bot/test_portfolio.py -k "retired_from_roster or missing_but_still_in_roster or roster_not_provided or left_the_roster" -v`
Expected: `test_exit_reason_ticker_retired_from_roster_when_removed_and_roster_given` et `test_positions_to_close_closes_a_position_whose_ticker_left_the_roster` échouent (`TypeError: exit_reason() got an unexpected keyword argument 'roster_tickers'`). Les deux autres (tests de rétrocompatibilité) doivent déjà passer une fois le `TypeError` résolu au step suivant — relancer après le Step 4 pour confirmer.

- [ ] **Step 4: Modifier `exit_reason` et `positions_to_close`**

Dans `ibkr_bot/portfolio.py`, remplacer la signature et le début de `exit_reason` (vers la ligne ~213-225) :

```python
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
```

par :

```python
def exit_reason(position: dict, company: dict | None, today: str,
                roster_tickers: set[str] | None = None) -> str | None:
    """Motif de cloture de `position` aujourd'hui, ou None si aucune
    condition n'est remplie.

    ORDRE DE PRIORITE STRICT, premiere condition remplie gagne (spec
    3.6) : ticker_retire_indice -> stop_loss -> objectif_atteint ->
    delai_max.

    Une position dont le ticker a disparu des donnees du jour, ou dont
    le prix courant manque, est laissee INTACTE et reevaluee demain :
    jamais de vente declenchee par une donnee absente -- SAUF si
    `roster_tickers` est fourni et que le ticker n'y figure plus : dans
    ce cas, la societe n'a pas juste une donnee manquante ce run, elle a
    ete RETIREE DE L'INDICE (audit 2026-10-02) -- meme traitement que
    ticker_retire_indice cote paper-trading (indices_score.py), pour ne
    jamais laisser une position bloquee indefiniment sans stop-loss ni
    sortie possible apres une revision d'indice. `roster_tickers=None`
    (defaut) desactive ce comportement, retrocompatible avec les
    appelants existants qui ne le fournissent pas.
    """
    if company is None or _is_missing(company.get("current_price")):
        if roster_tickers is not None and position["ticker"] not in roster_tickers:
            return "ticker_retire_indice"
        return None
```

Puis, dans la même fonction, le `if company is None or _is_missing(...)` ci-dessus gérait aussi le cas `company is None` seul (prix absent) — vérifier après cette modification que `_is_missing(company.get("current_price"))` quand `company is not None` mais que le prix manque continue de renvoyer `None` sans jamais passer par la nouvelle branche `ticker_retire_indice` (qui ne doit s'appliquer QUE quand `company is None`, jamais quand le prix est juste manquant pour une société toujours présente). Si le test `test_exit_reason_none_when_the_current_price_is_missing` (ligne ~607-612) échoue après cette modification, corriger la condition pour bien distinguer les deux cas (`company is None` vs `company is not None and prix manquant`) — la version ci-dessus le fait déjà correctement car la nouvelle branche est nichée DANS le bloc `if company is None or ...`, qui s'exécute aussi quand le prix manque ; il faut donc affiner :

```python
    if company is None:
        if roster_tickers is not None and position["ticker"] not in roster_tickers:
            return "ticker_retire_indice"
        return None
    if _is_missing(company.get("current_price")):
        return None
```

(Cette seconde version, plus explicite, est celle à implémenter — elle sépare clairement "société absente des données" de "prix manquant pour une société présente", pour que `roster_tickers` ne s'applique jamais au second cas.)

Ensuite, modifier `positions_to_close` (vers la ligne ~246-262) :

```python
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
```

par :

```python
def positions_to_close(open_positions: list[dict], companies_by_ticker: dict,
                       today: str, roster_tickers: set[str] | None = None) -> list[dict]:
    """Positions du bot dont une condition de sortie est remplie
    aujourd'hui, avec leur motif et le prix courant ayant declenche la
    decision. Ne mute rien. `roster_tickers` (optionnel, audit 2026-10-02)
    est transmis tel quel a exit_reason -- voir son docstring."""
    a_cloturer = []
    for position in open_positions:
        company = companies_by_ticker.get(position["ticker"])
        raison = exit_reason(position, company, today, roster_tickers=roster_tickers)
        if raison is None:
            continue
        a_cloturer.append({
            "position": position,
            "close_reason": raison,
            "current_price": company["current_price"] if company is not None else None,
        })
    return a_cloturer
```

(Le `company["current_price"] if company is not None else None` est nécessaire : avant ce correctif, `company` ne pouvait jamais être `None` à ce point du code puisque `raison` valait toujours `None` dans ce cas — maintenant `raison` peut valoir `"ticker_retire_indice"` avec `company is None`, donc `company["current_price"]` lèverait un `TypeError`.)

- [ ] **Step 5: Lancer les tests, vérifier qu'ils passent**

Run: `python -m pytest tests/ibkr_bot/test_portfolio.py -k "retired_from_roster or missing_but_still_in_roster or roster_not_provided or left_the_roster" -v`
Expected: 4 réussites.

- [ ] **Step 6: Lancer toute la suite de tests portfolio (non-régression)**

Run: `python -m pytest tests/ibkr_bot/test_portfolio.py -q`
Expected: 0 échec — en particulier `test_exit_reason_none_when_the_current_price_is_missing` et `test_exit_reason_none_when_the_ticker_disappeared_from_the_data` doivent toujours passer sans modification de leur code (ils n'appellent pas `roster_tickers`, donc le défaut `None` doit préserver leur résultat `None`).

- [ ] **Step 7: Brancher `roster_tickers` dans `daily.py`**

Dans `ibkr_bot/daily.py`, repérer le bloc `companies = {c["ticker"]: c for c in indices.get("companies", []) ...}` (vers la ligne ~673-674) — ce dict EST déjà le roster du jour. Remplacer l'appel (vers la ligne ~686-690) :

```python
    sorties_a_traiter = []
    for position in positions_ouvertes:
        try:
            sorties_a_traiter.extend(
                portfolio.positions_to_close([position], companies, today))
```

par :

```python
    roster_tickers = set(companies.keys())
    sorties_a_traiter = []
    for position in positions_ouvertes:
        try:
            sorties_a_traiter.extend(
                portfolio.positions_to_close(
                    [position], companies, today, roster_tickers=roster_tickers))
```

- [ ] **Step 8: Chercher et mettre à jour le test de parité existant mentionné par l'audit**

L'audit cite `test_portfolio.py:710` comme un test de parité qui "ne passe pas `roster_tickers`" — chercher `test_exit_reason_matches_the_paper_trading_logic` (vers la ligne ~688-716 dans ce fichier) et vérifier s'il doit être étendu. Ce test compare la décision du bot à celle du paper-trading sur des scénarios de PRIX (stop-loss/objectif/délai), pas sur la disparition d'un ticker — il n'a pas besoin de `roster_tickers` pour rester valide tel quel. Ne PAS le modifier sauf si son exécution (Step 9) révèle une régression.

- [ ] **Step 9: Lancer toute la suite de tests du projet**

Run: `python -m pytest tests/ -q`
Expected: 0 échec.

- [ ] **Step 10: Commit**

```bash
git add ibkr_bot/portfolio.py ibkr_bot/daily.py tests/ibkr_bot/test_portfolio.py
git commit -m "fix(ibkr_bot): cloture une position dont le ticker a quitte le roster d'indices (audit 2026-10-02)"
```

---

### Task 7: Plafond de diversification par zone géographique réelle

**Contexte (Important, audit) :** `MAX_POSITIONS_PER_INDEX` (4) plafonne un indice précis, pas une zone — NASDAQ et DOW sont tous deux américains (jusqu'à 8/10 positions US possibles), CAC40/DAX/IBEX35/FTSEMIB sont tous en zone euro (jusqu'à 10/10 possibles). Ce correctif AJOUTE un plafond par zone, sans remplacer le plafond par indice existant.

**Files:**
- Modify: `ibkr_bot/portfolio.py:18-184` (constantes + `select_entries`)
- Test: `tests/ibkr_bot/test_portfolio.py`

**Interfaces:**
- Produces: nouvelle constante `INDEX_ZONE: dict[str, str]`, nouvelle constante `MAX_POSITIONS_PER_ZONE = 5`. `select_entries` peut désormais rejeter avec `"raison": "plafond_zone_atteint"`.

- [ ] **Step 1: Lire le contexte exact**

Relire `ibkr_bot/portfolio.py:18-184` (constantes de plafond + `select_entries` en entier) dans le fichier réel.

- [ ] **Step 2: Écrire les tests qui échouent**

Ajouter dans `tests/ibkr_bot/test_portfolio.py`, juste après `test_select_entries_rejects_signal_when_index_cap_reached` (chercher ce nom, vers la ligne ~294-309) :

```python
def test_select_entries_rejects_signal_when_zone_cap_reached():
    """Audit 2026-10-02 : NASDAQ et DOW sont tous deux en zone
    amerique_nord -- le plafond par zone doit se declencher avant
    d'atteindre le plafond par indice (4) sur un seul des deux, en
    cumulant les deux indices de la meme zone."""
    positions_ouvertes = [
        _bot_position("N1", index="NASDAQ"), _bot_position("N2", index="NASDAQ"),
        _bot_position("D1", index="DOW"), _bot_position("D2", index="DOW"),
        _bot_position("D3", index="DOW"),
    ]  # 5 positions US (zone amerique_nord), plafond zone = 5
    signaux = [_signal("N3.US", 50.0, index="NASDAQ", sector="Techno")]
    plans = {"N3.US": _plan("N3.US")}
    contrats = _contrats(["N3.US"])

    retenus, rejets = portfolio.select_entries(
        signaux, positions_ouvertes, plans, contrats, _CASH_ILLIMITE)

    assert retenus == []
    assert rejets == [{"ticker": "N3.US", "rang": 1, "score": 50.0,
                        "raison": "plafond_zone_atteint"}]


def test_select_entries_zone_cap_counts_signals_retained_earlier_in_same_batch():
    """Meme logique que le plafond secteur/indice existant : compte aussi
    les signaux deja retenus plus haut dans le MEME classement, pas
    seulement les positions deja ouvertes."""
    signaux = [
        _signal("A.PA", 90.0, index="CAC40", sector="S1"),
        _signal("B.DE", 80.0, index="DAX", sector="S2"),
        _signal("C.MC", 70.0, index="IBEX35", sector="S3"),
        _signal("D.MI", 60.0, index="FTSEMIB", sector="S4"),
        _signal("E.PA", 50.0, index="CAC40", sector="S5"),
    ]  # 5 signaux zone_euro, plafond zone = 5 -> le 5e doit etre rejete
    plans = {t: _plan(t) for t in ("A.PA", "B.DE", "C.MC", "D.MI", "E.PA")}
    contrats = _contrats(["A.PA", "B.DE", "C.MC", "D.MI", "E.PA"])

    retenus, rejets = portfolio.select_entries(
        signaux, [], plans, contrats, _CASH_ILLIMITE)

    assert [r["signal"]["ticker"] for r in retenus] == ["A.PA", "B.DE", "C.MC", "D.MI"]
    assert [r["raison"] for r in rejets] == ["plafond_zone_atteint"]


def test_select_entries_zone_cap_does_not_block_other_zones():
    """Non-regression : un signal hors de la zone saturee n'est jamais
    bloque par le plafond de zone."""
    positions_ouvertes = [_bot_position(f"N{i}", index="NASDAQ") for i in range(5)]
    signaux = [_signal("X.L", 50.0, index="FTSE", sector="Techno")]
    plans = {"X.L": _plan("X.L")}
    contrats = _contrats(["X.L"])

    retenus, rejets = portfolio.select_entries(
        signaux, positions_ouvertes, plans, contrats, _CASH_ILLIMITE)

    assert [r["signal"]["ticker"] for r in retenus] == ["X.L"]
    assert rejets == []


def test_select_entries_zone_cap_rejection_does_not_consume_a_slot():
    """Meme philosophie que le plafond secteur/indice : un signal rejete
    par le plafond de zone ne consomme ni place ni budget -- la place
    reste disponible pour le signal suivant du classement."""
    positions_ouvertes = [_bot_position(f"N{i}", index="NASDAQ") for i in range(5)]
    signaux = [
        _signal("N6.US", 90.0, index="NASDAQ", sector="Techno"),  # bloque par la zone
        _signal("A.PA", 50.0, index="CAC40", sector="Industrie"),  # doit quand meme passer
    ]
    plans = {t: _plan(t) for t in ("N6.US", "A.PA")}
    contrats = _contrats(["N6.US", "A.PA"])

    retenus, rejets = portfolio.select_entries(
        signaux, positions_ouvertes, plans, contrats, _CASH_ILLIMITE)

    assert [r["signal"]["ticker"] for r in retenus] == ["A.PA"]
    assert [r["raison"] for r in rejets] == ["plafond_zone_atteint"]
```

- [ ] **Step 3: Lancer les tests, vérifier qu'ils échouent**

Run: `python -m pytest tests/ibkr_bot/test_portfolio.py -k "zone_cap" -v`
Expected: tous échouent (`AttributeError: module 'ibkr_bot.portfolio' has no attribute 'INDEX_ZONE'` ou équivalent — la zone n'est jamais appliquée, tous les signaux passent).

- [ ] **Step 4: Ajouter les constantes**

Dans `ibkr_bot/portfolio.py`, juste après `MAX_POSITIONS_PER_INDEX = 4` (ligne ~28) :

```python
# Zones geographiques reelles (audit 2026-10-02) : MAX_POSITIONS_PER_INDEX
# ne plafonne qu'un indice precis, pas une zone -- NASDAQ et DOW sont
# tous deux americains (jusqu'a 8/10 positions US possibles avant ce
# correctif), CAC40/DAX/IBEX35/FTSEMIB sont tous en zone euro (jusqu'a
# 10/10 possibles). Ce plafond s'AJOUTE au plafond par indice, ne le
# remplace pas -- memes 8 indices que signals.INDICES_IN_SCOPE.
INDEX_ZONE = {
    "CAC40": "zone_euro", "DAX": "zone_euro",
    "IBEX35": "zone_euro", "FTSEMIB": "zone_euro",
    "NASDAQ": "amerique_nord", "DOW": "amerique_nord",
    "FTSE": "royaume_uni",
    "SMI": "suisse",
}
MAX_POSITIONS_PER_ZONE = 5
```

- [ ] **Step 5: Ajouter le comptage et le filtre dans `select_entries`**

Dans `ibkr_bot/portfolio.py`, le compteur (vers la ligne ~130-131), remplacer :

```python
    sector_counts = Counter(p["sector"] for p in open_positions if p.get("sector"))
    index_counts = Counter(p["index"] for p in open_positions if p.get("index"))
```

par :

```python
    sector_counts = Counter(p["sector"] for p in open_positions if p.get("sector"))
    index_counts = Counter(p["index"] for p in open_positions if p.get("index"))
    zone_counts = Counter(
        INDEX_ZONE[p["index"]] for p in open_positions
        if p.get("index") in INDEX_ZONE
    )
```

Puis, juste après le filtre `plafond_indice_atteint` existant (vers la ligne ~166-169), ajouter le nouveau filtre AVANT le garde-fou de solde :

```python
        indice = signal.get("index") or ""
        if indice and index_counts[indice] >= MAX_POSITIONS_PER_INDEX:
            rejets.append({**base, "raison": "plafond_indice_atteint"})
            continue

        zone = INDEX_ZONE.get(indice, "")
        if zone and zone_counts[zone] >= MAX_POSITIONS_PER_ZONE:
            rejets.append({**base, "raison": "plafond_zone_atteint"})
            continue

        if base_cash - engage < BUDGET_EUR:
```

Et dans la boucle d'accumulation (vers la ligne ~178-181), remplacer :

```python
        if secteur:
            sector_counts[secteur] += 1
        if indice:
            index_counts[indice] += 1
```

par :

```python
        if secteur:
            sector_counts[secteur] += 1
        if indice:
            index_counts[indice] += 1
            if indice in INDEX_ZONE:
                zone_counts[INDEX_ZONE[indice]] += 1
```

- [ ] **Step 6: Lancer les tests, vérifier qu'ils passent**

Run: `python -m pytest tests/ibkr_bot/test_portfolio.py -k "zone_cap" -v`
Expected: 4 réussites.

- [ ] **Step 7: Mettre à jour le docstring de `select_entries`**

Dans `ibkr_bot/portfolio.py`, le docstring de `select_entries` liste l'ordre des filtres (vers la ligne ~92-94) : `deja detenu -> contrat non resolu -> plan absent -> quantite nulle -> plafond -> plafond secteur -> plafond indice -> solde`. Mettre à jour en `... -> plafond secteur -> plafond indice -> plafond zone -> solde`.

- [ ] **Step 8: Lancer toute la suite de tests portfolio (non-régression)**

Run: `python -m pytest tests/ibkr_bot/test_portfolio.py -q`
Expected: 0 échec.

- [ ] **Step 9: Commit**

```bash
git add ibkr_bot/portfolio.py tests/ibkr_bot/test_portfolio.py
git commit -m "feat(ibkr_bot): plafond de diversification par zone geographique reelle (audit 2026-10-02)"
```

---

### Task 8: Plafond quotidien de nouvelles entrées

**Contexte (Important, audit) :** le 2026-09-24, 45 positions papier ont été ouvertes en un seul jour (13 correctifs de méthodologie déployés d'un coup). Le bot n'a aucun plafond d'entrées par jour — un futur déploiement de méthodologie pourrait faire acheter plusieurs positions réelles en une seule journée, purement à cause du déploiement, pas d'un vrai mouvement de marché.

**Files:**
- Modify: `ibkr_bot/portfolio.py:18-184` (constante + `select_entries`)
- Test: `tests/ibkr_bot/test_portfolio.py`

**Interfaces:**
- Produces: nouvelle constante `MAX_NEW_ENTRIES_PER_DAY = 3`. `select_entries` peut désormais rejeter avec `"raison": "plafond_entrees_quotidien_atteint"`.

- [ ] **Step 1: Lire le contexte exact**

Relire `ibkr_bot/portfolio.py:86-184` (`select_entries` en entier, après les modifications de la Task 7) dans le fichier réel.

- [ ] **Step 2: Écrire les tests qui échouent**

Ajouter dans `tests/ibkr_bot/test_portfolio.py`, à la fin de la section "selection des entrees" (juste avant la section suivante, repérer son dernier test existant) :

```python
def test_select_entries_rejects_signals_beyond_the_daily_cap():
    """Audit 2026-10-02 : un jour de deploiement de methodologie (ex.
    2026-09-24, 45 positions papier en un seul jour) ne doit jamais faire
    acheter plus de MAX_NEW_ENTRIES_PER_DAY positions reelles d'un coup,
    quel que soit le nombre de places/budget disponibles par ailleurs."""
    signaux = [_signal(f"T{i}.PA", 90.0 - i, index="CAC40", sector=f"S{i}")
               for i in range(5)]  # 5 signaux, places et budget illimites
    plans = {s["ticker"]: _plan(s["ticker"]) for s in signaux}
    contrats = _contrats([s["ticker"] for s in signaux])

    retenus, rejets = portfolio.select_entries(
        signaux, [], plans, contrats, _CASH_ILLIMITE)

    assert len(retenus) == portfolio.MAX_NEW_ENTRIES_PER_DAY
    rejets_plafond = [r for r in rejets if r["raison"] == "plafond_entrees_quotidien_atteint"]
    assert len(rejets_plafond) == 5 - portfolio.MAX_NEW_ENTRIES_PER_DAY


def test_select_entries_daily_cap_rejection_does_not_block_other_filters_first():
    """Un signal qui echoue deja a un autre filtre (ex. deja detenu) ne
    doit jamais etre compte comme "retenu" avant le plafond quotidien --
    l'ordre des filtres existants reste prioritaire."""
    signaux = [_signal("A.PA", 90.0, index="CAC40", sector="S1")]
    plans = {"A.PA": _plan("A.PA")}
    contrats = _contrats(["A.PA"])
    deja_detenu = [_bot_position("A.PA")]

    retenus, rejets = portfolio.select_entries(
        signaux, deja_detenu, plans, contrats, _CASH_ILLIMITE)

    assert retenus == []
    assert rejets == [{"ticker": "A.PA", "rang": 1, "score": 90.0,
                        "raison": "deja_en_portefeuille"}]
```

- [ ] **Step 3: Lancer les tests, vérifier qu'ils échouent**

Run: `python -m pytest tests/ibkr_bot/test_portfolio.py -k "daily_cap" -v`
Expected: `test_select_entries_rejects_signals_beyond_the_daily_cap` échoue (`AttributeError: module 'ibkr_bot.portfolio' has no attribute 'MAX_NEW_ENTRIES_PER_DAY'`). `test_select_entries_daily_cap_rejection_does_not_block_other_filters_first` doit déjà passer (ne dépend pas de la nouvelle constante).

- [ ] **Step 4: Ajouter la constante**

Dans `ibkr_bot/portfolio.py`, juste après `MAX_POSITIONS_PER_ZONE = 5` (ajouté en Task 7) :

```python
# Plafond quotidien d'entrees (audit 2026-10-02) : un jour de deploiement
# de methodologie de scoring peut faire apparaitre beaucoup de nouveaux
# signaux d'un coup (45 positions papier le 2026-09-24, 13 correctifs
# deployes le meme jour) -- sans ce plafond, le bot les achetterait TOUS
# le meme jour, un achat pilote par un changement de methodologie plutot
# que par un vrai mouvement de marche. Les signaux au-dela de ce plafond
# ne sont pas perdus : ils restent visibles le jour suivant tant que la
# position papier correspondante reste ouverte (signals.collect_new_signals
# ne lit que les positions ouvertes AUJOURD'HUI -- un signal rejete ici
# ne reapparaitra PAS automatiquement les jours suivants, limite connue,
# voir le point I-C de l'audit, hors scope de ce plan).
MAX_NEW_ENTRIES_PER_DAY = 3
```

- [ ] **Step 5: Ajouter le filtre dans `select_entries`**

Dans `ibkr_bot/portfolio.py`, repérer le filtre `plafond_atteint` existant (vers la ligne ~157-159, APRÈS les modifications de la Task 7 ce plafond reste au même endroit relatif) :

```python
        if places < 1:
            rejets.append({**base, "raison": "signal_ignore_plafond_atteint"})
            continue
```

Remplacer par :

```python
        if places < 1:
            rejets.append({**base, "raison": "signal_ignore_plafond_atteint"})
            continue

        if len(retenus) >= MAX_NEW_ENTRIES_PER_DAY:
            rejets.append({**base, "raison": "plafond_entrees_quotidien_atteint"})
            continue
```

(Placé juste après le plafond de places disponibles, avant les plafonds secteur/indice/zone/solde — un signal qui dépasse le plafond quotidien ne doit pas non plus consommer de budget ni de place, même philosophie que les autres plafonds de ce fichier.)

- [ ] **Step 6: Lancer les tests, vérifier qu'ils passent**

Run: `python -m pytest tests/ibkr_bot/test_portfolio.py -k "daily_cap" -v`
Expected: 2 réussites.

- [ ] **Step 7: Mettre à jour le docstring de `select_entries`**

Dans `ibkr_bot/portfolio.py`, le docstring de `select_entries` (modifié en Task 7, étape 7) : `... -> plafond -> plafond entrees quotidien -> plafond secteur -> plafond indice -> plafond zone -> solde`.

- [ ] **Step 8: Lancer toute la suite de tests portfolio (non-régression)**

Run: `python -m pytest tests/ibkr_bot/test_portfolio.py -q`
Expected: 0 échec — vérifier en particulier que les tests existants qui retiennent plus de 3 signaux dans un même batch (ex. `test_select_entries_fills_free_slots_in_rank_order`, qui retient 3 signaux — tout juste à la limite) passent toujours. Si un test existant retient explicitement PLUS de `MAX_NEW_ENTRIES_PER_DAY` (3) signaux dans un scénario qui n'a rien à voir avec ce plafond (ex. un test de plafond de place à 10), l'ajuster pour rester sous ce nouveau plafond sans changer ce qu'il vérifie réellement — documenter ce choix dans le commit si ça arrive.

- [ ] **Step 9: Lancer toute la suite de tests du projet**

Run: `python -m pytest tests/ -q`
Expected: 0 échec.

- [ ] **Step 10: Commit**

```bash
git add ibkr_bot/portfolio.py tests/ibkr_bot/test_portfolio.py
git commit -m "feat(ibkr_bot): plafond quotidien de nouvelles entrees (audit 2026-10-02)"
```

---

## Fin de plan — revue finale

Une fois les 8 tâches complètes : lancer `pytest tests/ -q` une dernière fois (0 échec attendu), puis utiliser `superpowers:finishing-a-development-branch` pour fusionner `ibkr-audit-fixes` dans `main` et pousser — PAS de déploiement VPS automatique (ni pour `indices_score.py`, dont le déploiement se fait via le workflow GitHub Actions existant, ni pour `ibkr_bot/`, qui reste en `dry_run` tant que l'utilisateur n'a pas donné un feu vert explicite et séparé pour passer en réel — ce plan corrige des bugs, il n'autorise pas un passage en réel).
