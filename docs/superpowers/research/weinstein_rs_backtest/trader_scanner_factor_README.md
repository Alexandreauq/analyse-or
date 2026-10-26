# Facteur trader-scanner (daily mode) — résultats

RECHERCHE / EXPLORATOIRE — PAS DU CODE DE PRODUCTION. Complète
`README.md` (moteur principal) et `walk_forward_robustness_README.md`
(renforcement du signal de base, chantier parallèle).

## Contexte

Tentative précédente (même session, plus tôt) : bloquée parce que
`docs/price_history.json` n'a de vraies clôtures **quotidiennes** que
depuis le 2026-09-28 (~1 mois), insuffisant pour calculer les règles
`breakout_20d`/RSI14/etc. sur l'historique 2020-2026 utilisé par le
backtest walk-forward (hebdomadaire).

## Déblocage : reconstruction de l'historique quotidien

**Obstacle technique rencontré et résolu** (spécifique à cette machine) :
yfinance échouait systématiquement avec `CertificateVerifyError:
unable to get local issuer certificate`. Cause identifiée : Norton
Antivirus intercepte le trafic HTTPS de `curl_cffi` (utilisé par
yfinance) avec son propre certificat (`C:\ProgramData\Norton\Antivirus\
wscert.pem`), que `curl_cffi` ne reconnaît pas par défaut — contrairement
à `requests`, qui passait par un autre chemin et fonctionnait déjà.
Résolu en combinant ce certificat avec le bundle `certifi` et en pointant
`CURL_CA_BUNDLE` vers le fichier combiné (fait automatiquement par
`build_ca_bundle()` dans `trader_scanner_factor.py` ; sans effet sur une
machine sans Norton, comme un VPS ou une CI).

**Test de faisabilité avant tout téléchargement massif** : 119/120
tickers d'un échantillon aléatoire récupérés en 26.5s (~0.22s/ticker),
**aucun rate-limiting** détecté sur ce volume. Extrapolé à l'univers
complet (1296 tickers après exclusion des indices), la reconstruction
complète 2020-10 → aujourd'hui a pris **279 secondes** et a réussi à
**100% (1296/1296 tickers)** — aucune réduction de périmètre n'a donc
été nécessaire, contrairement à ce que la tentative précédente
anticipait.

Les clôtures quotidiennes reconstruites sont cachées dans
`data/daily_closes.pkl` (gitignore, à régénérer avec
`python trader_scanner_factor.py --rebuild` si besoin — jamais écrit
dans `docs/price_history.json`, qui reste intouché).

## Volet (a) — règle trader-scanner comme FILTRE sur le signal Weinstein+RS

Méthodologie : chacune des 838 entrées du signal de base (identique à
`walk_forward_eval.py`) est taguée avec les règles trader-scanner
actives au dernier jour de bourse **strictement avant** la semaine
d'entrée (aucune fuite), en utilisant la fonction `find_setups()` et le
RSI exact de `day-trader-skill/trader-scanner/scan.py` (reproduits à
l'identique, pas une approximation). Puis, pour chaque règle, on ne
garde que le sous-ensemble d'entrées où elle est active et on recalcule
les mêmes métriques walk-forward (6 fenêtres ~1 an) que le signal de
base.

| Règle | Entrées actives / 838 | Rendement moyen/fenêtre | vs baseline (25.2%) |
|---|---|---|---|
| **Baseline (sans filtre)** | 838 | **25.2%** | — |
| breakout_20d | 270 | 13.8% | **pire** |
| bull_flag | 111 | 16.1% | **pire** |
| reclaim_ma20 | 28 | ~0.0% | **nettement pire** |
| oversold | 8 | 48.6% | mieux, mais **n=8 : pas interprétable** |
| oversold_bounce | 1 | 4.4% | **n=1 : aucune valeur** |

**Verdict volet (a) : aucune des règles ne justifie d'être ajoutée comme
filtre au signal de production.** Les trois règles avec un échantillon
suffisant (breakout_20d, bull_flag, reclaim_ma20) font toutes **moins
bien** que ne pas filtrer. Les deux qui semblent meilleures
(oversold/oversold_bounce) le sont sur un échantillon ridicule (8 et 1
trades) — un seul trade à +170% dans une fenêtre explique l'essentiel du
résultat "oversold", ce n'est pas un signal, c'est du bruit.

## Volet (b) — règle trader-scanner comme signal AUTONOME

Méthodologie différente et plus légère (voir docstring de
`trader_scanner_standalone.py` pour le détail et ses limites) : chaque
règle est évaluée vectoriellement sur les ~1296 tickers × ~6 ans (RSI
vectorisé par lissage exponentiel, approximation déclarée du RSI exact
utilisé au volet (a) — l'écart est négligeable sur un historique de
plusieurs années). Entrée à la clôture du jour où la règle s'active,
sortie à horizon fixe 20 jours de bourse ou plus tôt si stop -10% touché,
coût 0.30% — comparé à un buy&hold de référence sur le même horizon,
tous tickers, tout l'historique (moyenne 1.45%, médiane 0.88%, n=380003).

| Règle | Signaux | Rendement moyen | Médiane | Taux de réussite | vs buy&hold (1.45%) |
|---|---|---|---|---|---|
| breakout_20d | 262 153 | 0.65% | 0.31% | 51.7% | pire |
| bull_flag | 71 657 | 0.66% | -0.04% | 49.8% | pire (médiane négative) |
| reclaim_ma20 | 113 206 | 0.69% | 0.23% | 51.1% | pire |
| **oversold** | **61 737** | **1.92%** | **1.42%** | **56.5%** | **meilleur** |
| oversold_bounce | 32 410 | 1.51% | 1.26% | 56.1% | ~équivalent |

**`oversold` (RSI14 < 30, sortie 20 jours/stop -10%) montre un edge réel
et robuste dans le temps** : rendement moyen positif et taux de réussite
> 50% dans **chacune des 6 fenêtres** testées séparément (2.98%/64.2% en
2020-21 jusqu'à 1.36%/53.6% en 2025-26, jamais négatif), sur un
échantillon large (4869 à 14693 signaux par fenêtre) — ce n'est pas un
résultat isolé comme au volet (a).

**Mais** : c'est un résultat **en échantillon unique, non encore testé
en walk-forward au sens strict** (la règle est tirée telle quelle du
code source trader-scanner, pas optimisée ici — donc moins de risque de
surajustement que si on avait tâtonné des seuils — mais ça reste une
seule passe sur toute la donnée, pas un split apprentissage/validation
formel). À ne pas intégrer en production sans un test de robustesse
dédié (ex. même méthodologie que `walk_forward_robustness.py` pour le
signal de base), et sans vérifier si ce edge mean-reversion n'est pas
déjà partiellement capturé différemment par le signal Weinstein+RS
existant.

## Conclusion globale

- Les règles de **momentum/breakout** (breakout_20d, bull_flag,
  reclaim_ma20) **n'ajoutent rien** — ni en filtre, ni en autonome,
  elles font moins bien que l'alternative sans elles.
- **`oversold` (mean-reversion RSI14) est le seul résultat positif et
  statistiquement consistant** de ce test, mais seulement en signal
  autonome — comme filtre sur le signal hebdomadaire existant, il est
  trop rare pour être utile (8 co-occurrences en 6 ans).
- **Recommandation** : ne pas intégrer trader-scanner au signal
  ibkr_bot actuel. Si un edge mean-reversion intraday/court-terme
  intéresse à l'avenir, `oversold` mérite un chantier dédié avec sa
  propre validation out-of-sample — mais ce serait une stratégie
  différente (horizon ~1 mois, pas le même univers d'entrée) plutôt
  qu'un ajout au signal Weinstein+RS existant.

## Fichiers

- `trader_scanner_factor.py` — volet (a), filtre sur le signal de base.
- `trader_scanner_standalone.py` — volet (b), signal autonome vectorisé.
- `trader_scanner_factor_results.json` / `trader_scanner_standalone_results.json` — résultats détaillés.
- `data/daily_closes.pkl` — cache local (gitignore), régénérable.
