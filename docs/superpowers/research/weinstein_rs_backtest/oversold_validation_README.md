# Validation du signal `oversold` — résultats

RECHERCHE / EXPLORATOIRE — PAS DU CODE DE PRODUCTION. Complète
`trader_scanner_factor_README.md` (volet b, résultat initial non validé)
et `walk_forward_robustness_README.md` (chantier parallèle sur le signal
de base).

## Contexte

`trader_scanner_standalone.py` (commit `a8577ad`) avait trouvé `oversold`
(RSI14 Wilder < 30, sortie à 20 jours de bourse ou stop -10%) comme seule
règle trader-scanner positive et cohérente : 61 737 signaux, rendement
moyen 1.92% vs buy&hold 1.45%, taux de réussite 56.5%. Trois limites
explicitement notées à l'époque : (1) RSI **approximé** par lissage
exponentiel (`ewm`), pas la récursion exacte de production ; (2) une
seule passe sur toute la donnée, pas un walk-forward strict ; (3) aucune
vérification du chevauchement avec le signal Weinstein+RS existant. Ce
chantier lève les trois.

## 1. RSI exact de production — pas une approximation

**Découverte méthodologique importante** : `day-trader-skill/trader-scanner/
scan.py::find_setups()` (via `tag_entry()` dans `trader_scanner_factor.py`)
n'applique PAS un Wilder RSI "long terme" convergé sur tout l'historique.
Il appelle `rsi()` sur une fenêtre **roulante de seulement 30 clôtures**
(`MIN_DAILY_BARS`), **reseedée chaque jour** : le seed (moyenne simple des
14 premiers deltas) repart à zéro à chaque fenêtre de 30 jours, au lieu
d'un seed unique au début de l'historique. C'est différent à la fois d'un
Wilder RSI classique long-terme ET de l'approximation `ewm(adjust=False)`
du script précédent (qui elle aussi ne seede qu'une fois, au tout premier
jour de l'historique). Les trois RSI ne sont pas mathématiquement
identiques.

Ce chantier implémente le RSI **exact de production** (fenêtre reseedée
de 30 jours), vectorisé via `numpy.lib.stride_tricks.sliding_window_view`
pour la vitesse, et **validé bit à bit contre la fonction de référence**
(`trader_scanner_factor.rsi`, elle-même une copie fidèle de
`scan.py:485-495`) sur 40 points pris au hasard sur 8 tickers : écart
maximal **0.00e+00** — exactitude confirmée avant tout calcul à l'échelle.

**Impact de la correction** (RSI exact vs approximation précédente) :

| | RSI approximé (précédent) | RSI exact (ce chantier) |
|---|---|---|
| Signaux | 61 737 | **79 293** (+28%) |
| Rendement moyen | 1.92% | 1.70% |
| Taux de réussite | 56.5% | 56.0% |

L'approximation n'était pas négligeable en **nombre** de signaux (+28%,
le RSI reseedé est plus volatil, donc le seuil <30 est franchi plus
souvent), mais la conclusion **qualitative** tient : rendement moyen et
taux de réussite restent proches et clairement positifs avec le RSI
exact. Tout ce qui suit utilise le RSI exact.

## 2. Walk-forward strict — 6 fenêtres indépendantes, pas une seule passe

| Fenêtre | n | Rendement moyen | Réussite |
|---|---|---|---|
| 2020-10 → 2021-10 | 7 039 | 2.89% | 63.7% |
| 2021-10 → 2022-10 | 18 743 | 0.93% | 53.3% |
| 2022-10 → 2023-10 | 13 584 | 1.62% | 55.9% |
| 2023-10 → 2024-10 | 11 008 | 2.34% | 58.4% |
| 2024-10 → 2025-10 | 14 103 | 2.16% | 56.4% |
| 2025-10 → 2026-10 | 14 770 | 1.25% | 53.9% |

**6/6 fenêtres positives**, jamais négatif — confirme, cette fois avec un
vrai découpage en fenêtres indépendantes (pas seulement l'agrégat publié
précédemment), que le résultat tient dans le temps. La fenêtre 2021-22
est nettement plus faible (0.93%, à peine au-dessus du coût de
transaction) — pas un signal uniformément fort, mais jamais négatif.

## 3. Sensibilité aux paramètres — pas de pic étroit

Seuil RSI (25/30/35) × horizon de sortie (15/20/25 jours), 9 combinaisons,
toutes sur le RSI exact :

| | horizon 15j | horizon 20j | horizon 25j |
|---|---|---|---|
| seuil < 25 | 1.68% / 56.1% | 1.94% / 56.1% | 2.30% / 55.8% |
| seuil < 30 (défaut) | 1.39% / 55.9% | 1.70% / 56.0% | 2.00% / 55.7% |
| seuil < 35 | 1.16% / 54.9% | 1.55% / 55.1% | 1.73% / 54.8% |

**Aucune combinaison n'est négative, aucun pic étroit** — le rendement
moyen et le taux de réussite varient de façon régulière et modeste sur
les 9 combinaisons. Le réglage par défaut (30/20j, tiré tel quel du code
source trader-scanner, jamais optimisé ici) n'est ni le meilleur ni le
pire de l'échantillon — signe rassurant contre le surajustement.

## 4. Chevauchement avec le signal Weinstein+RS — edge réellement distinct

Sur les 79 293 occurrences du signal `oversold`, phase Weinstein du
ticker (calculée semaine par semaine par `backtest_engine.compute_stage`)
au moment du signal :

| Phase Weinstein | Occurrences | % |
|---|---|---|
| Achat (seule phase où Weinstein+RS peut trader) | 5 509 | 6.9% |
| Déclin | 41 528 | 52.4% |
| Neutre | 32 248 | 40.7% |

**Seulement 6.9% des signaux `oversold` surviennent pendant une phase où
le signal Weinstein+RS pourrait même entrer en position** — ce n'est
donc PAS le même edge sous un autre nom. `oversold` capture
majoritairement des rebonds pendant des phases de déclin ou neutres,
un univers de trades presque entièrement différent.

**Mais la performance tient aussi dans ce sous-ensemble à risque** —
ventilation du rendement *du trade* par phase à l'entrée :

| Phase à l'entrée | n | Rendement moyen | Réussite |
|---|---|---|---|
| Achat | 5 509 | 3.21% | 62.5% |
| Neutre | 32 248 | 1.67% | 56.8% |
| Déclin | 41 528 | 1.51% | 54.5% |

Le rendement est positif dans les trois régimes, y compris en phase de
Déclin (le cas classique du "couteau qui tombe") — plus faible qu'en
phase Achat, mais pas négatif, probablement protégé par le stop -10% et
l'horizon court de 20 jours.

## Verdict

**L'edge `oversold` tient toujours après ce test plus rigoureux**, et
c'est un edge **réellement distinct** du signal Weinstein+RS existant
(6.9% de chevauchement de phase seulement) :
- RSI exact de production (pas une approximation), validé bit à bit.
- Positif dans les 6 fenêtres walk-forward indépendantes, jamais négatif.
- Robuste à des variations de seuil/horizon (pas de pic étroit).
- Positif dans les trois régimes Weinstein, y compris en phase de déclin.

**Limites qui restent entières** :
- Biais de survivance toujours présent et **pas re-testé ici** (univers
  = composition actuelle des indices, appliquée rétroactivement — comme
  pour le signal de base).
- C'est une stratégie à horizon ~1 mois sur univers brut (pas filtrée par
  le signal Weinstein+RS), donc un **second signal candidat distinct**,
  pas un complément du signal hebdomadaire actuel — intégration à
  ibkr_bot nécessiterait sa propre conception (fréquence d'exécution,
  sizing, cohabitation avec le signal existant), pas un simple ajout.
- Toujours un seul univers de test (pas de données hors-échantillon
  réellement nouvelles) — un vrai test prospectif (comme
  `project_ia_news_reasoning_test`) reste la preuve la plus fiable, mais
  n'existe pas encore pour ce signal.

**Recommandation** : ce signal mérite un chantier de conception séparé
s'il doit un jour rejoindre ibkr_bot — ce n'est pas un ajustement du
signal Weinstein+RS actuel, c'est une stratégie différente avec son propre
edge, sa propre fréquence, et son propre risque (phase de déclin).

## Fichiers

- `oversold_validation.py` — script complet (RSI exact + validation +
  walk-forward + sensibilité + chevauchement + ventilation par régime).
- `oversold_validation_results.json` — résultats détaillés.
- Réutilise `data/daily_closes.pkl` (cache gitignore, voir
  `trader_scanner_factor.py` pour la régénération).
