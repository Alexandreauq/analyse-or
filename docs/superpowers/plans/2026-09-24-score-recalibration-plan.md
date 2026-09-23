# Recalibration du score composite (rang percentile) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transformer le score composite Indices (-100/+100) d'une somme pondérée brute (trop tassée vers le haut — 66% des sociétés notées "favorable") en un rang percentile calculé séparément par profil de société (standard/financier/trust), pour retrouver une échelle réellement discriminante.

**Architecture:** Une nouvelle passe (`recalibrate_scores_by_profile`) s'insère dans `main()` après que toutes les entrées société sont construites et avant le calcul des alertes. Elle mute `company["score"]`/`company["interpretation"]` en place, uniquement pour les profils dont le pool a au moins 20 sociétés (repli sur le score brut sinon). Aucune des 7 fonctions de scoring par facteur ni `compute_composite` ne change.

**Tech Stack:** Python 3.12, pytest — même stack que le reste d'`indices_score.py`.

**Spec:** `docs/superpowers/specs/2026-09-24-score-recalibration-design.md`

## Global Constraints

- `SCORE_RECALIBRATION_MIN_POOL_SIZE = 20` — sous ce seuil, un profil garde son score brut inchangé (pas de percentile).
- Le rang percentile utilise la méthode du rang moyen (gère les ex æquo) : `100 * (nb_strictement_inférieurs + 0.5 * nb_égaux) / n`.
- Remise à l'échelle : `round((percentile - 50) * 2, 1)`.
- Regroupement par profil : `is_trust` -> `"trust"`, sinon `is_financial` -> `"financial"`, sinon `"standard"`.
- Aucune modification des 7 fonctions de scoring par facteur (`score_rentabilite`, `score_structure_financiere`, etc.) ni de `compute_composite`.
- Aucune nouvelle dépendance (pas d'import `collections.defaultdict` — utiliser `dict.setdefault`).
- Le seuil d'alerte "entrée"/"watch" passe de `15` à `0` (deux sites d'appel dans `compute_company_alerts`).
- `interpret()` passe de 4 à 5 bandes, coupures à `60`/`20`/`-20`/`-60`.
- `RAPID_DROP_POINTS`/`RAPID_DROP_DAYS` restent inchangés dans ce plan.

---

### Task 1: Rang percentile et recalibration par profil (fonctions pures)

**Files:**
- Modify: `indices_score.py` (nouvelles fonctions, à insérer juste après la fonction `interpret` — cherche `def interpret(composite: float) -> str:` et sa ligne `return "Fragile"` qui la termine, insère juste après, avant `def get_row(df, *aliases):`)
- Test: `tests/test_indices_score.py` (nouveau bloc, à ajouter juste après `test_interpret_bands` — cherche `def test_interpret_bands():` et insère juste après son corps, avant le bloc `import pandas as pd` qui suit)

**Interfaces:**
- Produces: `compute_percentile_rank(value: float, pool: list[float]) -> float` — `pool` doit contenir `value` lui-même.
- Produces: `_score_profile_key(company: dict) -> str` — `"trust"`/`"financial"`/`"standard"` selon `company.get("is_trust")`/`company.get("is_financial")`.
- Produces: `recalibrate_scores_by_profile(companies: list[dict]) -> None` — mute `company["score"]`/`company["interpretation"]` en place pour les sociétés dont le profil a un pool `>= SCORE_RECALIBRATION_MIN_POOL_SIZE`. Consomme `interpret` (déjà existant) pour recalculer l'interprétation après remap.

- [ ] **Step 1: Écrire les tests qui échouent**

Ajoute ce bloc dans `tests/test_indices_score.py`, juste après le corps de `test_interpret_bands()` (avant le `import pandas as pd` qui suit) :

```python
def test_compute_percentile_rank_min_value_is_near_zero():
    pool = [10.0, 20.0, 30.0, 40.0, 50.0]
    result = indices_score.compute_percentile_rank(10.0, pool)
    assert result == 10.0  # 0 inférieurs, 1 égal (lui-même) -> 100*(0+0.5)/5


def test_compute_percentile_rank_max_value_is_near_hundred():
    pool = [10.0, 20.0, 30.0, 40.0, 50.0]
    result = indices_score.compute_percentile_rank(50.0, pool)
    assert result == 90.0  # 4 inférieurs, 1 égal -> 100*(4+0.5)/5


def test_compute_percentile_rank_median_value():
    pool = [10.0, 20.0, 30.0, 40.0, 50.0]
    result = indices_score.compute_percentile_rank(30.0, pool)
    assert result == 50.0  # 2 inférieurs, 1 égal -> 100*(2+0.5)/5


def test_compute_percentile_rank_handles_ties():
    pool = [10.0, 20.0, 20.0, 20.0, 50.0]
    result = indices_score.compute_percentile_rank(20.0, pool)
    # 1 strictement inférieur (10.0), 3 égaux (les trois 20.0) -> 100*(1+1.5)/5
    assert result == 50.0


def test_compute_percentile_rank_single_element_pool():
    result = indices_score.compute_percentile_rank(42.0, [42.0])
    assert result == 50.0  # seul élément du pool -> 100*(0+0.5)/1


def test_score_profile_key_trust():
    assert indices_score._score_profile_key({"is_trust": True, "is_financial": False}) == "trust"


def test_score_profile_key_financial():
    assert indices_score._score_profile_key({"is_trust": False, "is_financial": True}) == "financial"


def test_score_profile_key_standard():
    assert indices_score._score_profile_key({"is_trust": False, "is_financial": False}) == "standard"


def test_score_profile_key_defaults_to_standard_when_keys_absent():
    assert indices_score._score_profile_key({}) == "standard"


def _make_companies_with_scores(scores: list[float], is_financial=False, is_trust=False) -> list[dict]:
    return [
        {
            "ticker": f"T{i}", "score": s, "interpretation": "peu importe",
            "is_financial": is_financial, "is_trust": is_trust,
        }
        for i, s in enumerate(scores)
    ]


def test_recalibrate_scores_by_profile_remaps_large_pool():
    # 25 sociétés standard (>= seuil 20) avec des scores bruts variés.
    companies = _make_companies_with_scores([float(i) for i in range(25)])
    indices_score.recalibrate_scores_by_profile(companies)
    # La société avec le score brut le plus bas (0.0) doit désormais avoir
    # un score recalibré proche de -100 ; la plus haute (24.0), proche de +100.
    assert companies[0]["score"] < -80
    assert companies[-1]["score"] > 80
    # Le score n'est plus la valeur brute d'origine.
    assert companies[0]["score"] != 0.0


def test_recalibrate_scores_by_profile_updates_interpretation():
    companies = _make_companies_with_scores([float(i) for i in range(25)])
    indices_score.recalibrate_scores_by_profile(companies)
    for c in companies:
        assert c["interpretation"] == indices_score.interpret(c["score"])


def test_recalibrate_scores_by_profile_leaves_small_pool_untouched():
    # 5 sociétés trust (< seuil 20) : score et interpretation doivent rester
    # strictement identiques à ce qu'ils étaient avant l'appel.
    companies = _make_companies_with_scores([1.0, 2.0, 3.0, 4.0, 5.0], is_trust=True)
    for c in companies:
        c["interpretation"] = "Neutre"  # valeur arbitraire posée avant l'appel
    original = [dict(c) for c in companies]
    indices_score.recalibrate_scores_by_profile(companies)
    assert companies == original


def test_recalibrate_scores_by_profile_groups_by_profile_independently():
    # 25 standard (scores bruts 0..24) + 25 financier (scores bruts
    # 100..124, plage totalement disjointe) : si les pools étaient
    # incorrectement fusionnés en un seul de 50, TOUTES les sociétés
    # standard se retrouveraient tassées en bas du classement (dominées
    # par les scores financier, bien plus hauts) -- alors qu'avec un
    # regroupement correct, la meilleure société standard doit être proche
    # du sommet de SON PROPRE pool, peu importe les valeurs de l'autre
    # groupe.
    standard = _make_companies_with_scores([float(i) for i in range(25)], is_financial=False)
    financial = _make_companies_with_scores([float(i) + 100.0 for i in range(25)], is_financial=True)
    companies = standard + financial
    indices_score.recalibrate_scores_by_profile(companies)
    # Meilleure société standard (score brut 24.0, la plus haute de son
    # propre pool de 25) : doit être proche de +100, pas écrasée par les
    # scores financier.
    best_standard = companies[24]
    assert best_standard["score"] > 80
    # Pire société financier (score brut 100.0, la plus basse de SON
    # propre pool) : doit être proche de -100, pas portée en haut par sa
    # valeur brute élevée en absolu.
    worst_financial = companies[25]
    assert worst_financial["score"] < -80
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `python -m pytest tests/test_indices_score.py -k "compute_percentile_rank or score_profile_key or recalibrate_scores_by_profile" -v`
Expected: FAIL avec `AttributeError` (les fonctions n'existent pas encore)

- [ ] **Step 3: Implémenter**

Dans `indices_score.py`, juste après `def interpret(composite: float) -> str:` (après sa dernière ligne `return "Fragile"`, avant `def get_row(df, *aliases):`), ajoute :

```python
SCORE_RECALIBRATION_MIN_POOL_SIZE = 20  # voir docs/superpowers/specs/2026-09-24-score-recalibration-design.md


def compute_percentile_rank(value: float, pool: list[float]) -> float:
    """Rang percentile de `value` au sein de `pool` (méthode du rang moyen
    — gère les ex æquo sans biaiser vers le haut ou le bas). `pool` doit
    contenir `value` lui-même (le score de la société fait partie de son
    propre pool de comparaison). Renvoie une valeur entre 0 et 100."""
    n = len(pool)
    lower = sum(1 for v in pool if v < value)
    equal = sum(1 for v in pool if v == value)
    return 100 * (lower + 0.5 * equal) / n


def _score_profile_key(company: dict) -> str:
    if company.get("is_trust"):
        return "trust"
    if company.get("is_financial"):
        return "financial"
    return "standard"


def recalibrate_scores_by_profile(companies: list[dict]) -> None:
    """Mute company["score"] et company["interpretation"] en place pour
    chaque société dont le profil a un pool >= SCORE_RECALIBRATION_MIN_POOL_SIZE.
    Une société dans un profil au pool trop petit (ex: trust aujourd'hui,
    0 société) garde son score brut déjà calculé par build_company_entry —
    repli assumé, pas un oubli (voir spec)."""
    pools: dict[str, list[float]] = {}
    for c in companies:
        pools.setdefault(_score_profile_key(c), []).append(c["score"])

    for c in companies:
        key = _score_profile_key(c)
        pool = pools[key]
        if len(pool) < SCORE_RECALIBRATION_MIN_POOL_SIZE:
            continue  # repli : score brut déjà en place, on ne touche à rien
        percentile = compute_percentile_rank(c["score"], pool)
        new_score = round((percentile - 50) * 2, 1)
        c["score"] = new_score
        c["interpretation"] = interpret(new_score)
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `python -m pytest tests/test_indices_score.py -k "compute_percentile_rank or score_profile_key or recalibrate_scores_by_profile" -v`
Expected: 13 tests PASS

- [ ] **Step 5: Lancer toute la suite du fichier**

Run: `python -m pytest tests/test_indices_score.py -q`
Expected: tous PASS (aucune régression — ces fonctions sont nouvelles et ne sont pas encore appelées ailleurs)

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "Ajoute compute_percentile_rank et recalibrate_scores_by_profile (recalibration du score)"
```

---

### Task 2: Wiring dans `main()`

**Files:**
- Modify: `indices_score.py` (fonction `main` — cherche `def main():`)
- Test: `tests/test_indices_score.py` (nouveau test, à ajouter juste après `test_main_calls_update_signal_tracking` — cherche cette fonction et insère juste après)

**Interfaces:**
- Consumes: `recalibrate_scores_by_profile(companies: list[dict]) -> None` (Task 1).
- Produces: `main()` appelle désormais `recalibrate_scores_by_profile(companies)` avant `_attach_alerts_and_update_history(companies)` — les alertes et le suivi des signaux voient donc le score déjà recalibré.

- [ ] **Step 1: Écrire le test qui échoue**

Ajoute dans `tests/test_indices_score.py`, juste après `test_main_calls_update_signal_tracking` (reprends exactement le même style de mocking que ce test — cherche-le pour voir le pattern complet) :

```python
def test_main_recalibrates_scores_before_alerts_and_signal_tracking(monkeypatch, tmp_path):
    """Preuve que main() appelle recalibrate_scores_by_profile AVANT
    _attach_alerts_and_update_history/update_signal_tracking — si l'appel
    était supprimé ou mal placé, ce test doit échouer."""
    monkeypatch.setattr(indices_score, "fetch_risk_free_rate", lambda series_id: 3.68)
    monkeypatch.setattr(indices_score, "fetch_fx_rate_to_usd", lambda currency: 1.0)
    monkeypatch.setattr(indices_score, "load_previous_company_analyses", lambda: {})

    # Scores bruts variés (pas tous identiques) pour que le percentile ait
    # un sens à vérifier -- indexé par position d'appel, car
    # build_company_entry est appelé une fois par ticker de COMPANIES (plus
    # de 20, donc le pool "standard" dépasse largement le seuil de
    # recalibration).
    call_counter = {"n": 0}

    def _fake_build_company_entry(ticker, name, risk_free_rate, previous_analyses, index_key="CAC40", also_indices=None, fx_rate_to_usd=1.0):
        call_counter["n"] += 1
        return {
            "ticker": ticker, "name": name, "index": index_key,
            "score": float(call_counter["n"]), "interpretation": "peu importe",
            "current_price": 50.0, "entry_price": 50.0,
            "is_financial": False, "is_trust": False,
        }

    monkeypatch.setattr(indices_score, "build_company_entry", _fake_build_company_entry)
    monkeypatch.setattr(indices_score, "load_indices_history", lambda: [])
    monkeypatch.setattr(indices_score, "append_indices_history", lambda entries: entries)
    output_path = tmp_path / "indices.json"
    monkeypatch.setattr(indices_score, "OUTPUT_JSON_PATH", str(output_path))

    called_with = {}

    def _fake_update_signal_tracking(companies, newly_triggered_entree):
        called_with["companies"] = companies
        return []

    monkeypatch.setattr(indices_score, "update_signal_tracking", _fake_update_signal_tracking)
    monkeypatch.setattr(indices_score, "update_nikkei_hangseng_price_history", lambda companies: [])
    monkeypatch.setattr(indices_score, "fetch_index_prices", lambda: {"CAC40": None, "DAX": None, "NASDAQ": None, "DOW": None})
    monkeypatch.setattr(indices_score, "update_price_history", lambda entries, **kwargs: entries)
    monkeypatch.setattr(indices_score, "fetch_index_price_history", lambda: [])

    indices_score.main()

    companies = called_with["companies"]
    n = len(companies)
    assert n >= indices_score.SCORE_RECALIBRATION_MIN_POOL_SIZE
    # Les scores bruts posés par le mock étaient 1.0, 2.0, ..., n (jamais
    # négatifs) -- si recalibrate_scores_by_profile n'avait pas tourné
    # avant que update_signal_tracking les voie, AUCUN score ne serait
    # négatif. Après recalibration (rang percentile remis sur -100/+100),
    # les sociétés du bas du classement doivent avoir un score négatif.
    scores = [c["score"] for c in companies]
    assert min(scores) < 0
    assert max(scores) > 0
    # Le score n'est plus la valeur brute posée par le mock (1.0..n).
    assert scores != [float(i + 1) for i in range(n)]
```

- [ ] **Step 2: Vérifier que le test échoue**

Run: `python -m pytest tests/test_indices_score.py -k test_main_recalibrates_scores_before_alerts_and_signal_tracking -v`
Expected: FAIL (`min(scores) < 0` est faux — tous les scores bruts posés par le mock sont positifs, la recalibration n'a pas encore lieu)

- [ ] **Step 3: Implémenter**

Dans `indices_score.py`, fonction `main()`, cherche ce bloc existant :

```python
    price_history_entries = []
    for c in companies:
        price_history_entries.extend(c.pop("_price_history_daily", []))
    price_history_entries.extend(fetch_index_price_history())
    update_price_history(price_history_entries)

    newly_triggered_entree, newly_triggered_major_news = _attach_alerts_and_update_history(companies)
```

Remplace par (ajoute l'appel à `recalibrate_scores_by_profile` entre les deux) :

```python
    price_history_entries = []
    for c in companies:
        price_history_entries.extend(c.pop("_price_history_daily", []))
    price_history_entries.extend(fetch_index_price_history())
    update_price_history(price_history_entries)

    recalibrate_scores_by_profile(companies)

    newly_triggered_entree, newly_triggered_major_news = _attach_alerts_and_update_history(companies)
```

- [ ] **Step 4: Vérifier que le test passe**

Run: `python -m pytest tests/test_indices_score.py -k test_main_recalibrates_scores_before_alerts_and_signal_tracking -v`
Expected: PASS

- [ ] **Step 5: Lancer toute la suite du fichier**

Run: `python -m pytest tests/test_indices_score.py -q`
Expected: tous PASS (aucune régression -- les autres tests `test_main_*` posent des scores fixes identiques pour toutes les sociétés simulées ou n'en vérifient pas la valeur exacte, donc ne sont pas affectés par le remap ; vérifie-le si l'un d'eux échoue de façon inattendue plutôt que de le modifier à l'aveugle)

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "Appelle recalibrate_scores_by_profile dans main() avant les alertes"
```

---

### Task 3: Rééquilibrage des bandes d'interprétation (`interpret`)

**Files:**
- Modify: `indices_score.py` (fonction `interpret` — cherche `def interpret(composite: float) -> str:`)
- Test: `tests/test_indices_score.py` (modifie `test_interpret_bands` existant — cherche `def test_interpret_bands():`)

**Interfaces:**
- Produces: `interpret(composite: float) -> str` — 5 bandes désormais (`"Profil fondamental très solide"`, `"Solide"`, `"Neutre"`, `"Fragile"`, `"Très fragile"`), coupures à 60/20/-20/-60.

- [ ] **Step 1: Écrire les tests qui échouent**

Remplace `test_interpret_bands` (cherche `def test_interpret_bands():`) par :

```python
def test_interpret_bands():
    assert interpret(70.0) == "Profil fondamental très solide"
    assert interpret(30.0) == "Solide"
    assert interpret(0.0) == "Neutre"
    assert interpret(-30.0) == "Fragile"
    assert interpret(-70.0) == "Très fragile"


def test_interpret_boundary_values():
    # Valeurs exactement aux bornes -- doivent tomber dans la bande DU
    # DESSOUS (comparaison stricte ">", pas ">=").
    assert interpret(60.0) == "Solide"
    assert interpret(20.0) == "Neutre"
    assert interpret(-20.0) == "Fragile"
    assert interpret(-60.0) == "Très fragile"
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `python -m pytest tests/test_indices_score.py -k test_interpret -v`
Expected: FAIL sur les deux tests. `test_interpret_bands` échoue sur sa dernière ligne : `interpret(-70.0)` renvoie `"Fragile"` avec l'ancien code (qui n'a que 4 bandes), pas `"Très fragile"` (ses 4 premières assertions passent déjà par coïncidence, les seuils 70/30/0/-30 tombant du même côté avec l'ancien et le nouveau découpage). `test_interpret_boundary_values` échoue clairement dès sa première ligne : avec l'ancien code, `interpret(60.0)` renvoie `"Profil fondamental très solide"` (60 > 50), pas `"Solide"`.

- [ ] **Step 3: Implémenter**

Dans `indices_score.py`, remplace :

```python
def interpret(composite: float) -> str:
    if composite > 50:
        return "Profil fondamental très solide"
    if composite > 15:
        return "Solide"
    if composite > -15:
        return "Neutre"
    return "Fragile"
```

par :

```python
def interpret(composite: float) -> str:
    if composite > 60:
        return "Profil fondamental très solide"   # top ~20% (score recalibré)
    if composite > 20:
        return "Solide"                            # ~20%
    if composite > -20:
        return "Neutre"                             # ~20%, autour de la médiane
    if composite > -60:
        return "Fragile"                            # ~20%
    return "Très fragile"                            # ~20%
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `python -m pytest tests/test_indices_score.py -k test_interpret -v`
Expected: 2 tests PASS

- [ ] **Step 5: Lancer toute la suite du fichier**

Run: `python -m pytest tests/test_indices_score.py -q`
Expected: tous PASS. Si un autre test échoue parce qu'il appelait `interpret()` avec une valeur qui change de bande (ex: un test construit autour de l'ancien seuil 15 ou 50), corrige UNIQUEMENT la valeur attendue de ce test précis pour refléter les nouvelles bandes -- ne change jamais `interpret()` pour faire repasser un test, les nouvelles bandes sont la spec.

- [ ] **Step 6: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "Rééquilibre les bandes d'interpret() sur 5 niveaux (score recalibré)"
```

---

### Task 4: Seuils d'alerte "entrée"/"watch" (15 -> 0)

**Files:**
- Modify: `indices_score.py` (fonction `compute_company_alerts` — cherche `def compute_company_alerts(`)
- Test: `tests/test_indices_score.py` (modifie deux tests existants -- voir Step 1)

**Interfaces:**
- Produces: `compute_company_alerts(...)` déclenche désormais `"watch"` au franchissement de `0` (au lieu de `15`) et `"entree"` quand `composite > 0` (au lieu de `composite > 15`). Signature inchangée.

**Contexte important :** sur les ~10 tests existants de `compute_company_alerts`, seuls DEUX ont des valeurs qui changent de comportement avec le nouveau seuil. Ne touche à aucun autre test de ce groupe -- ils restent valides tels quels (leurs valeurs de `composite` ne traversent pas le nouveau seuil `0` d'une façon qui changerait leur résultat attendu). En particulier, `test_compute_company_alerts_risque_on_rapid_drop`/`test_compute_company_alerts_no_risque_when_drop_outside_window` (qui utilisent `composite=15.0`) et `test_compute_company_alerts_entree_when_score_favorable_and_price_near_entry`/`test_compute_company_alerts_no_entree_when_price_far_from_entry` (qui utilisent `composite=20.0`) restent inchangés : `15.0` et `20.0` sont tous les deux `> 0`, donc leur comportement ne bascule pas.

- [ ] **Step 1: Modifier les deux tests affectés**

Remplace `test_compute_company_alerts_watch_when_score_crosses_15_upward` (cherche cette fonction) par :

```python
def test_compute_company_alerts_watch_when_score_crosses_0_upward():
    previous_history = [{"date": "2026-09-05", "ticker": "BN.PA", "composite": -5.0}]
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=5.0, current_price=100.0, entry_price=50.0,
        previous_history=previous_history,
    )
    kinds = [a["kind"] for a in alerts]
    assert "watch" in kinds
```

Remplace `test_compute_company_alerts_no_watch_when_already_above_15` (cherche cette fonction) par :

```python
def test_compute_company_alerts_no_watch_when_already_above_0():
    """Ne doit se déclencher qu'au franchissement, pas rester actif en continu."""
    previous_history = [{"date": "2026-09-05", "ticker": "BN.PA", "composite": 20.0}]
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=22.0, current_price=100.0, entry_price=50.0,
        previous_history=previous_history,
    )
    kinds = [a["kind"] for a in alerts]
    assert "watch" not in kinds
```

Remplace `test_compute_company_alerts_no_entree_when_score_not_favorable` (cherche cette fonction) par :

```python
def test_compute_company_alerts_no_entree_when_score_not_favorable():
    alerts = indices_score.compute_company_alerts(
        "BN.PA", composite=-5.0, current_price=101.0, entry_price=100.0,
        previous_history=[],
    )
    kinds = [a["kind"] for a in alerts]
    assert "entree" not in kinds
```

- [ ] **Step 2: Vérifier que les tests modifiés échouent**

Run: `python -m pytest tests/test_indices_score.py -k "test_compute_company_alerts_watch_when_score_crosses_0_upward or test_compute_company_alerts_no_watch_when_already_above_0 or test_compute_company_alerts_no_entree_when_score_not_favorable" -v`
Expected: seul `test_compute_company_alerts_watch_when_score_crosses_0_upward` échoue avec l'ancien code (seuil encore à 15 : `-5.0 -> 5.0` ne le franchit pas). Les deux autres passent déjà avant l'implémentation -- leurs valeurs (`20.0`/`22.0` et `-5.0`) tombent du même côté du seuil 15 que du seuil 0, donc ce n'est pas un cycle rouge/vert classique pour eux ; l'important est qu'ils passent toujours APRÈS le Step 3, pas qu'ils échouent avant.

- [ ] **Step 3: Implémenter**

Dans `indices_score.py`, fonction `compute_company_alerts`, remplace :

```python
    if prev_composite is not None and prev_composite <= 15 < composite:
```

par :

```python
    if prev_composite is not None and prev_composite <= 0 < composite:
```

Puis remplace :

```python
    if composite > 15 and near_entry:
```

par :

```python
    if composite > 0 and near_entry:
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `python -m pytest tests/test_indices_score.py -k "test_compute_company_alerts_watch_when_score_crosses_0_upward or test_compute_company_alerts_no_watch_when_already_above_0 or test_compute_company_alerts_no_entree_when_score_not_favorable" -v`
Expected: 3 tests PASS

- [ ] **Step 5: Lancer toute la suite du fichier**

Run: `python -m pytest tests/test_indices_score.py -q`
Expected: tous PASS. Si un test de `compute_company_alerts` autre que les trois modifiés échoue, relis-le attentivement avant de le changer -- son `composite` devrait rester du même côté du seuil `0` qu'il l'était du seuil `15` (voir le paragraphe "Contexte important" ci-dessus) ; un échec inattendu signale probablement une erreur d'implémentation plutôt qu'un test à corriger.

- [ ] **Step 6: Lancer toute la suite du dépôt**

Run: `python -m pytest -q` (depuis la racine du dépôt)
Expected: tous PASS (aucune régression ailleurs — gold_bot/ibkr_bot non concernés par ce plan)

- [ ] **Step 7: Commit**

```bash
git add indices_score.py tests/test_indices_score.py
git commit -m "Abaisse le seuil d'alerte entree/watch de 15 à 0 (score recalibré)"
```
