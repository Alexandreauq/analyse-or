# Recalibration du score composite (rang percentile par profil) — Design

## Contexte et problème

Le score composite Indices (`indices_score.py`, échelle -100/+100, `compute_composite`)
est une somme pondérée de 7 facteurs, chacun noté de -10 à +10. Un audit complet de
la stratégie Actions/Indices (2026-09-21, validé par l'utilisateur) a trouvé que
cette échelle n'est pas bien calibrée : elle est trop généreuse, tassée vers le haut.

Vérifié sur les 703 sociétés en production (run du 2026-09-23) :

- 66.1% des sociétés ont un score ≥ 20 ("favorable" ou "très favorable")
- Moyenne 29.7, médiane 35.2 (sur une échelle censée être centrée sur 0)
- Seulement 10.7% ont un score < -20

Le score ne discrimine donc pas correctement les sociétés réellement exceptionnelles
des sociétés simplement correctes — la quasi-totalité du panel atterrit dans les
bandes "positives".

## Objectif

Recalibrer le score affiché sans toucher à la logique de notation par facteur
(ROCE, structure financière, croissance, etc. restent inchangés) : transformer le
score composite brut en **rang percentile au sein de son profil de société**
(standard / financier / trust), remis à l'échelle -100/+100. Par construction, un
rang percentile est uniformément réparti — le problème de tassement disparaît
mécaniquement, sans avoir à re-doser les formules des 7 facteurs individuels.

## Pourquoi par profil, pas globalement

Les profils standard, financier et trust utilisent des jeux de facteurs et de
méthodologies différents (`extract_ratios` vs `extract_ratios_financial`, DCF vs
valeur comptable, etc. — voir l'audit du 2026-09-21). Comparer leurs scores bruts
directement n'a pas de sens : un score de 40 chez une banque et un score de 40 chez
une industrielle ne sont pas mesurés de la même façon. Le rang percentile doit donc
être calculé séparément, au sein de chaque pool de profil.

Répartition réelle des profils (run du 2026-09-23, 703 sociétés) :

| Profil | Effectif |
|---|---|
| standard | 627 |
| financier | 76 |
| trust | 0 |

Le profil trust existe dans le code (`is_trust`, `score_*_trust`) mais n'a
actuellement aucun membre dans les 10 indices couverts — il pourrait en gagner un
jour (ex. ajout d'un indice avec des REIT).

## Repli pour les profils trop petits

Un rang percentile n'a pas de sens statistique sur un échantillon trop réduit (ex.
1-2 sociétés). Seuil : `SCORE_RECALIBRATION_MIN_POOL_SIZE = 20`. Un profil dont le
pool est sous ce seuil (trust aujourd'hui, avec 0 société) **garde son score brut**
(sortie directe de `compute_composite`, non recalibrée) et l'interprétation
calculée normalement. C'est un choix assumé et documenté dans le code, pas un
oubli : les scores des sociétés en repli ne sont pas directement comparables à ceux
des sociétés recalibrées. Standard (627) et financier (76) sont largement au-dessus
du seuil et seront systématiquement recalibrés.

## Architecture

Aucune modification des fonctions de scoring par facteur ni de `compute_composite`.
Une nouvelle passe s'insère dans `main()`, après que toutes les entrées société
sont construites et avant le calcul des alertes — car les alertes
(`compute_company_alerts`, appelée depuis `_attach_alerts_and_update_history`)
lisent `company["score"]` directement sur l'entrée déjà construite, donc doivent
voir le score déjà recalibré :

```
build_company_entry(...) pour chaque société   [inchangé — calcule le score brut]
    -> companies: list[dict], chacun avec company["score"] = score brut
recalibrate_scores_by_profile(companies)        [NOUVEAU — mute company["score"]/["interpretation"] en place]
    -> regroupe par profil (is_trust -> "trust", is_financial -> "financial", sinon "standard")
    -> pour chaque pool >= SCORE_RECALIBRATION_MIN_POOL_SIZE : remplace le score par son rang percentile remis à l'échelle
    -> pool < seuil : ne touche à rien (score déjà calculé par build_company_entry)
_attach_alerts_and_update_history(companies)    [inchangé, lit company["score"] -- voit désormais le score recalibré]
update_signal_tracking(companies, ...)          [inchangé, lit companies -- voit aussi le score recalibré]
```

Point d'insertion exact dans `main()` : juste après `update_price_history(price_history_entries)`
et juste avant `_attach_alerts_and_update_history(companies)`.

## Calcul du rang percentile

Nouvelle fonction pure, testable indépendamment :

```python
def compute_percentile_rank(value: float, pool: list[float]) -> float:
    """Rang percentile de `value` au sein de `pool` (méthode du rang moyen —
    gère les ex æquo sans biaiser vers le haut ou le bas). `pool` doit
    contenir `value` lui-même (le score de la société fait partie de son
    propre pool de comparaison). Renvoie une valeur entre 0 et 100."""
    n = len(pool)
    lower = sum(1 for v in pool if v < value)
    equal = sum(1 for v in pool if v == value)
    return 100 * (lower + 0.5 * equal) / n
```

Remise à l'échelle -100/+100, appliquée au score composite brut déjà calculé
(jamais aux facteurs individuels) :

```python
def recalibrate_scores_by_profile(companies: list[dict]) -> None:
    """Mute company["score"] et company["interpretation"] en place pour
    chaque société dont le profil a un pool >= SCORE_RECALIBRATION_MIN_POOL_SIZE.
    Ne lève jamais d'exception : une société sans profil reconnaissable
    (ne devrait pas arriver, mais défensif) est traitée comme "standard"."""
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


def _score_profile_key(company: dict) -> str:
    if company.get("is_trust"):
        return "trust"
    if company.get("is_financial"):
        return "financial"
    return "standard"
```

`SCORE_RECALIBRATION_MIN_POOL_SIZE = 20` (constante, même zone du fichier que les
autres seuils comme `RAPID_DROP_POINTS`).

## Seuils et libellés d'interprétation

Le seuil d'alerte "entrée"/"watch" actuel (`composite > 15` — franchissement) est
en réalité très permissif sur l'échelle actuelle : 70% des sociétés le franchissent
déjà (30ᵉ percentile). C'était un artefact de la sous-calibration, pas un choix
voulu. Sur la nouvelle échelle recalibrée, le seuil devient **`0`** (au-dessus de
la médiane du profil) — un signal "entrée" redevient réellement sélectif.

`interpret()` utilise aussi ces bornes pour ses libellés. Avec un score bien
réparti, garder les bornes actuelles (`15`/`-15`) ferait tomber ~42% des sociétés
dans "Fragile" (tout ce qui est sous le 42ᵉ percentile) — trop sévère pour des
sociétés simplement sous la médiane. Nouvelles bornes, visant une répartition par
quintile (points de coupure aux percentiles 80/60/40/20) :

```python
def interpret(composite: float) -> str:
    if composite > 60:
        return "Profil fondamental très solide"   # ~20% du pool
    if composite > 20:
        return "Solide"                            # ~20%
    if composite > -20:
        return "Neutre"                             # ~20%, autour de la médiane
    if composite > -60:
        return "Fragile"                            # ~20%
    return "Très fragile"                            # ~20%
```

Une 5ᵉ bande ("Très fragile") est ajoutée : l'ancien système produisait très peu de
scores très négatifs (2% sous -50 dans les données actuelles), donc n'avait pas
besoin de la distinguer de "Fragile". Avec une distribution bien étalée, ce cas
redevient un cas réel du quotidien et mérite son propre libellé plutôt que d'être
noyé dans "Fragile".

`RAPID_DROP_POINTS = 20` / `RAPID_DROP_DAYS = 5` (détection de chute rapide, alerte
"risque") restent inchangés dans cette itération — la nouvelle échelle pourrait
avoir une volatilité jour/jour différente (le rang percentile d'une société peut
bouger même si ses propres fondamentaux n'ont pas changé, si ses pairs bougent),
mais deviner un nouveau réglage sans données réelles serait arbitraire. À surveiller
après le déploiement ; ajustable dans une itération ultérieure si besoin.

## Ce qui ne change pas

- Les 7 fonctions de scoring par facteur (`score_rentabilite`, `score_structure_financiere`, etc.)
- `compute_composite` (le calcul de la somme pondérée -100/+100 brute)
- Le badge Graham (`compute_graham_defensive_badge`) — critères indépendants du score composite, aucun impact
- Le classement Weinstein (stage/stage_label) — indépendant du score composite
- `RAPID_DROP_POINTS`/`RAPID_DROP_DAYS` (voir ci-dessus)

## Impact attendu sur le suivi des signaux en cours

Le suivi de performance des signaux "entrée" (paper-trading, premier diagnostic
2026-09-18, prochain point prévu ~2026-12-18) est directement affecté : le seuil de
déclenchement change de sens (30ᵉ percentile permissif -> médiane, plus sélectif).
Décision explicite de l'utilisateur : recalibrer maintenant plutôt qu'attendre la
fin de la fenêtre de suivi. Le diagnostic du 18/09 reste une référence historique
("avant recalibration") mais n'est pas directement comparable aux signaux déclenchés
après ce changement — à noter clairement lors du prochain point de suivi.

## Tests

- `compute_percentile_rank` : valeur min du pool (percentile ≈0), valeur max
  (≈100), valeur médiane, ex æquo (plusieurs sociétés à la même valeur), pool
  à 1 élément.
- `recalibrate_scores_by_profile` : pool ≥ seuil recalibré correctement,
  regroupement standard/financier/trust correct, pool < seuil totalement
  inchangé (score et interpretation identiques à l'entrée), société sans
  clé de profil reconnaissable traitée comme standard.
- `interpret()` : les 5 nouvelles bandes avec des valeurs aux bornes exactes
  (60, 20, -20, -60) et de part et d'autre.
- Non-régression : suite complète `tests/test_indices_score.py`.

## Déploiement

Comme pour le badge Graham : merge, puis run manuel du workflow GitHub Actions
pour vérifier sur les vraies données (703 sociétés), avec un contrôle explicite de
la nouvelle distribution (devrait être nettement plus étalée que les 66%
favorable/très favorable actuels — viser quelque chose de proche de 20% par bande).
