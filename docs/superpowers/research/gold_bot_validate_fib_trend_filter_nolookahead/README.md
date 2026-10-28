# Fibonacci + filtre de tendance — correction du biais de lookahead dans le tag

RECHERCHE / EXPLORATOIRE — PAS DU CODE DE PRODUCTION.

## Le bug trouvé en revue finale avant déploiement

`../gold_bot_validate_fib_trend_filter/README.md` a validé le filtre de
force de tendance (seuil **5%**, 4/6 fenêtres, FTMO 25,4%/29j à
risk_pct=2%) comme le chiffre à annoncer pour le déploiement de
l'instance FTMO. Une revue de code de branche complète, juste avant ce
déploiement, a trouvé un biais de lookahead dans la façon dont cette
validation **tague** chaque trade :

- `diagnose_and_improve.daily_trend_strength()` calcule la force de
  tendance du jour *d* avec `by_day[d][-1]["close"]` — la bougie de
  **clôture** de ce jour (~22h).
- `diagnose_and_improve.tag_trend_strength()` tague chaque trade avec
  `strength_by_day.get(trade["entry_time"][:10], 0.0)` — la force du
  jour de **l'entrée du trade lui-même**.

Un trade ouvert à 14h un jour donné est donc tagué avec une donnée
(la clôture du soir de ce même jour) qui n'existe pas encore au moment
réel de la décision. En backtest pur ça ne casse rien (toute la bougie
du jour est déjà chargée) mais ça fuit de l'information du futur dans
le filtre évalué — exactement le type d'artefact que ce chantier a
déjà débusqué une fois (seuil 8% choisi par confirmation, voir le
README du fork précédent). Le correctif production (fait séparément,
hors scope ici) ne peut utiliser que la force du dernier jour
calendaire **complet** précédent. Ce fork revalide le seuil et le
chiffre FTMO avec cette même contrainte, honnêtement, avant d'annoncer
quoi que ce soit au déploiement.

## Méthode

Reprend `walk_forward_filter.py` à l'identique (6 fenêtres roulantes
annuelles 2021-2026, Fibonacci deux jambes **fixe**
window=40/fib_pct=0,618/target_r=1,5, grille de seuils
5%/6%/8%/10%/12%/15%, fitness train = R moyen par trade retenu,
généralisation vérifiée sur le test jamais vu). Réutilise sans
réimplémenter : `fibonacci_two_leg.generate_fibonacci_two_leg_trades`,
`diagnose_and_improve.daily_trend_strength` (le calcul par jour n'est
pas le bug, il reste inchangé), `diagnose_and_improve.tag_trend_strength`
(uniquement pour le run "AVANT" de sanity-check, volontairement
tainté), `ftmo_pass_rate.simulate_challenge_from_quantized`/
`_effective_distances`. La seule pièce de logique nouvelle est
`tag_trend_strength_nolookahead()` dans `walk_forward_filter_nolookahead.py`.

**Correction appliquée** : pour chaque trade, au lieu de
`strength_by_day.get(jour_entrée, 0.0)`, on prend la force du dernier
jour calendaire **strictement avant** le jour d'entrée présent dans
`strength_by_day`. Si ce jour précédent n'existe pas, ou s'il est
lui-même dans la zone "placeholder" de `daily_trend_strength`
(moins de `window_days`=20 jours calendaires complets d'historique
derrière lui — donc sa propre force n'est pas une vraie mesure), le
trade est **non mesurable** : il est exclu du jeu de trades taggés,
jamais defaulté à 0.0 (ce qui l'aurait fait passer n'importe quel
filtre à tort). Même logique que le correctif production : bloquer
plutôt que présumer. Sur les 3362 jambes générées, **18 sont exclues**
ainsi (0,5% — uniquement les toutes premières semaines du jeu de
données de 8,5 ans).

## Sanity-check (obligatoire avant de faire confiance au chiffre corrigé)

Le run "AVANT" rejoue le tag lookahead d'origine (`tag_trend_strength`,
inchangé, 0 exclusion) à travers exactement le même harnais que ce
script. Il reproduit bit pour bit le README précédent à partir des
mêmes données en cache (pas de nouvel appel API) :

| | Seuil le plus fréquent | Généralisation | risk_pct=1% | risk_pct=2% |
|---|---|---|---|---|
| Original (`walk_forward_filter.py`) | 5% (4/6) | 4/6 | 60,9% / 272j | **25,4% / 29j** |
| AVANT reproduit ici (sanity-check) | 5% (4/6) | 4/6 | 60,9% / 272,0j | **25,4% / 29,0j** |

Reproduction exacte → le harnais est correct, le chiffre corrigé
ci-dessous est digne de confiance.

## Résultat corrigé (sans lookahead)

### Stabilité du seuil

| Seuil | AVANT (lookahead) | APRÈS (corrigé) |
|---|---|---|
| 5% | **4/6** | **4/6** |
| 10% | 2/6 | 0/6 |
| 15% | 0/6 | 2/6 |

**Le seuil 5% survit** : c'est toujours le plus fréquent, toujours
4/6, même en corrigeant le lookahead. Pas de deuxième correction de
seuil à faire cette fois.

### Généralisation hors-échantillon

| Année test | AVANT : améliore ? | APRÈS : améliore ? |
|---|---|---|
| 2021 | non | **OUI** |
| 2022 | non | non |
| 2023 | **OUI** | non |
| 2024 | **OUI** | non |
| 2025 | **OUI** | **OUI** |
| 2026 | **OUI** | **OUI** |
| **Total** | **4/6** | **3/6** |

La généralisation hors-échantillon **recule de 4/6 à 3/6** (50%, en
dessous de la majorité stricte des fenêtres). 2023 et 2024, qui
« marchaient » avec le tag taché de lookahead, choisissent maintenant
15% sur leur fenêtre d'entraînement — un seuil qui ne filtre quasiment
rien (184→184 et 214→214 trades) — preuve que l'amélioration apparente
sur ces deux années s'appuyait en partie sur la fuite d'information du
futur, pas sur un vrai signal exploitable en temps réel.

### Évaluation FTMO finale (seuil=5%, intégralité des 8,5 ans)

| risk_pct | AVANT (lookahead, 2044 jambes) | APRÈS (corrigé, 2014 jambes) | Écart |
|---|---|---|---|
| 1% | 60,9% / 272j | **70,2% / 328j** | +9,3 pts, +56j (plus lent) |
| **2%** | **25,4% / 29j** | **22,8% / 35j** | **-2,6 pts (-10% relatif), +6j (+21% relatif, plus lent)** |

## Verdict

**Le chiffre 25,4%/29j à risk_pct=2% — le chiffre annoncé pour
justifier le déploiement — ne survit pas intact.** Une fois le
lookahead corrigé, il tombe à **22,8%/35j**, soit environ **10% de
réussite relative en moins, pour une durée ~21% plus longue**. Ce
n'est pas une différence cosmétique : à ce niveau de risque, c'est une
baisse de catégorie comparable à l'écart qu'avait créé la précédente
correction de seuil (8%→5%), même si ici le seuil lui-même ne change
pas. La généralisation hors-échantillon recule aussi, de 4/6 à 3/6 —
en dessous de ce qui serait un vrai signe de robustesse (majorité
stricte des fenêtres), et ce chantier reste donc, comme les candidats
`vol_filter` et Ichimoku déjà discutés dans le README précédent, loin
de la preuve 6/6 du VWAP en production.

Côté positif honnête : à risk_pct=1% (le risque le plus pertinent pour
un passage FTMO réel, moins agressif), le chiffre corrigé est en fait
**meilleur** (70,2% contre 60,9%), même si plus lent (328j contre
272j). Le seuil **5%** lui-même reste validé (toujours 4/6, pas de
nouveau 8%→5%-style retournement de paramètre à faire).

**Ce que ça change pour le déploiement** : le filtre à seuil 5% reste
défendable, mais le chiffre à annoncer pour justifier le choix de
risk_pct=2% doit être **22,8%/35j**, pas 25,4%/29j — et la
généralisation doit être annoncée honnêtement comme **3/6**, pas 4/6.
Si l'intention était de piloter risk_pct=1% en pratique, le chiffre
corrigé (70,2%/328j) est au contraire une bonne nouvelle. Rien ici ne
dit qu'il faut annuler le déploiement du moteur Fibonacci corrigé
(lookahead déjà fixé séparément en production) — mais le chiffre
headline doit être corrigé avant d'être répété.

## Limites

- Comme le fork précédent : grille de seuils (5-15%) pas exhaustive,
  un optimum local plus fin entre deux valeurs testées reste possible.
- Les 18 trades exclus comme "non mesurables" sont tous dans les
  premières semaines du jeu de données (2017/2018) — négligeable en
  volume (0,5%), mais la bonne pratique à appliquer en production
  aussi : ne jamais défaulter une donnée de tendance manquante à 0,0.
- Comme pour tout le chantier : sortie à deux jambes, mode remplacement
  seul, conflit de position avec d'autres moteurs hors scope.

## Fichiers

- `walk_forward_filter_nolookahead.py` — script exécutable, rejoue le
  run "AVANT" (lookahead, sanity-check) et "APRÈS" (corrigé) à travers
  le même harnais walk-forward.
- `walk_forward_filter_nolookahead_results.json` — résultats bruts des
  deux runs (per-window, stabilité, évaluation FTMO).
