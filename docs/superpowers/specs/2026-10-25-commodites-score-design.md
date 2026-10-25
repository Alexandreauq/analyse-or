# Commodités — généralisation du score macro-fondamental de l'or à 4 commodités

**Statut** : brainstormé et validé avec l'utilisateur le 2026-10-25, prêt pour `writing-plans`.

## Contexte et origine

L'onglet "Or" du site n'affichait qu'une seule valeur (le score macro-fondamental
de l'or, `gold_score.py`) — un onglet entier pour un seul actif, jugé trop
pauvre comparé à l'onglet Indices (710+ entreprises scorées). Décision prise
avec l'utilisateur : élargir l'onglet en un onglet **Commodités**, avec le
même type de scoring macro-fondamental (facteurs pondérés -10/+10, composite
-100/+100) appliqué à l'or, l'argent, le pétrole (WTI) et le gaz naturel,
chacun avec des facteurs adaptés à ce qui l'influence réellement — pas une
copie aveugle des facteurs de l'or.

**Important, tranché explicitement avec l'utilisateur** : `gold_bot/` (le bot
de trading live/scalping sur l'or, ses backtests, ses services VPS
`gold-bot-api`/`gold-bot-loop`) **reste intact, aucun fichier n'y est touché**.
Le bot a donné des résultats positifs sur un backtest de 6 ans — il est
seulement éteint côté VPS pour le moment (kill switch actif, service loop
désactivé le 2026-10-25), pas supprimé. Seule la partie **site** change :
l'onglet Scalping et l'onglet Suivi (qui affichaient le bot/le suivi des
signaux scalping or en paper-trading) disparaissent du site, parce qu'ils
n'apportent plus rien de visible tant que le bot est à l'arrêt — mais le code
Python du bot, lui, reste en place pour une réactivation future éventuelle.

**Attention à ne pas confondre** : il existe une AUTRE fonctionnalité de
suivi de performance de signaux, sur l'onglet Indices (paper-trading des
signaux "entrée" actions). Elle est indépendante de l'onglet Suivi de l'or et
n'est pas concernée par ce chantier.

## Découverte de terrain (CFTC) — pas dans le périmètre initial

En vérifiant en direct (pas supposé) les données CFTC nécessaires pour les
nouveaux facteurs de positionnement spéculatif, deux choses non anticipées :

1. **Le dataset "Legacy Futures Only" (Socrata `6dca-aqww`)**, utilisé tel
   quel par `gold_score.py` (`market_and_exchange_names = "GOLD - COMMODITY
   EXCHANGE INC."`), **ne contient plus aucune ligne pétrole ou gaz naturel
   depuis le 2022-02-01** — ces contrats physiques sont désormais rapportés
   uniquement dans le dataset **"Disaggregated Futures Only" (`72hh-3qpy`)**,
   qui a un schéma de champs différent (`m_money_positions_long_all` /
   `m_money_positions_short_all`, catégorie "Money Managers" — l'équivalent
   moderne du "non-commercial" du rapport Legacy) au lieu de
   `noncomm_positions_long_all` / `noncomm_positions_short_all`.
   L'argent (`SILVER - COMMODITY EXCHANGE INC.`), en revanche, reste bien
   dans le dataset Legacy, avec le même schéma que l'or — données vérifiées
   à jour au 2026-10-20.
2. **Les contrats eux-mêmes ont été renommés** vers 2022 : le pétrole WTI,
   qui s'appelait `CRUDE OIL, LIGHT SWEET - NEW YORK MERCANTILE EXCHANGE`,
   s'appelle maintenant **`WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE`**
   (vérifié : open interest ~1.8M contrats au 2026-10-20, de loin le plus
   gros contrat pétrole du dataset — c'est le bon). Le gaz naturel, qui
   s'appelait `NATURAL GAS - NEW YORK MERCANTILE EXCHANGE`, s'appelle
   maintenant **`NAT GAS NYME - NEW YORK MERCANTILE EXCHANGE`** (vérifié :
   open interest ~1.7M contrats, le contrat NYMEX principal — à ne pas
   confondre avec `NAT GAS ICE LD1`, plus gros en OI mais coté sur ICE, pas
   cohérent avec le NG=F de Yahoo Finance utilisé pour le prix).

Ces deux noms de marché + le dataset Disaggregated sont ceux à utiliser,
vérifiés par requête directe, pas une supposition issue de l'entraînement du
modèle (qui aurait probablement donné les anciens noms, périmés depuis 2022).

## Périmètre : facteurs par commodité

Même moteur que l'or (`FactorResult` : nom, score -10/+10, poids, valeur
brute d'audit), composite = somme pondérée × 10 (échelle -100/+100), mêmes
seuils d'interprétation (`> 50` très favorable, `> 15` favorable, `> -15`
neutre, sinon défavorable).

### Or — inchangé

`gold_score.py` n'est pas modifié. Il sert de référence/première commodité
de la future famille, et plusieurs de ses fonctions (pures, déjà
commodité-agnostiques : `factor_taux_reels`, `factor_dollar`,
`factor_inflation_anticipee`, `fetch_fred_series`, `score_from_momentum`,
`percentile_rank`, `FactorResult`, `compute_composite`) sont **réutilisées
par import direct**, jamais réimplémentées — même principe que
`analyste_backtest.py` avec `indices_score.py`.

### Argent (SI=F) — 100% automatisé, aucune saisie manuelle

| Facteur | Poids | Source |
|---|---|---|
| Taux réels US 10 ans | 30% | FRED `DFII10` (réutilise `factor_taux_reels`) |
| Dollar (DXY) | 20% | yfinance `DX-Y.NYB` (réutilise `factor_dollar`) |
| Positionnement CFTC | 15% | Legacy `6dca-aqww`, `"SILVER - COMMODITY EXCHANGE INC."` |
| Ratio or/argent | 25% | yfinance `GC=F`/`SI=F`, percentile 3 ans — ratio historiquement haut = argent sous-évalué vs or = favorable à l'argent (spécifique à l'argent, signal de retour à la moyenne) |
| Dynamique récente | 10% | momentum prix vs MM200, comme l'or |

### Pétrole WTI (CL=F)

| Facteur | Poids (v1, sans EIA) | Source |
|---|---|---|
| Dollar (DXY) | 20% | réutilise `factor_dollar` |
| Positionnement CFTC (Money Managers) | 20% | Disaggregated `72hh-3qpy`, `"WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE"` |
| Spread WTI-Brent | 15% | yfinance `CL=F` vs `BZ=F` — écartement du spread = défavorable au WTI (offre US relativement abondante), resserrement = favorable |
| Dynamique récente | 20% | momentum vs MM200 |
| Stocks hebdo EIA | 25% (poids nominal) | **placeholder neutre v1** — voir "Hors périmètre" |

### Gaz naturel (NG=F)

| Facteur | Poids (v1, sans EIA) | Source |
|---|---|---|
| Positionnement CFTC (Money Managers) | 20% | Disaggregated `72hh-3qpy`, `"NAT GAS NYME - NEW YORK MERCANTILE EXCHANGE"` |
| Saisonnalité vs moyenne 5 ans | 25% | FRED `DHHNGSP` (Henry Hub spot, vérifié : série active, données au 2026-10-20) — écart du prix du mois en cours vs sa moyenne historique sur les années précédentes pour ce même mois |
| Dynamique récente | 20% | momentum vs MM200 |
| Dollar (DXY) | 10% | réutilise `factor_dollar`, poids réduit (corrélation plus faible, marché majoritairement domestique US) |
| Stocks hebdo EIA | 25% (poids nominal) | **placeholder neutre v1** — voir "Hors périmètre" |

## Architecture technique

**Un seul nouveau module `commodity_score.py`** (pas un fichier par
commodité) : une config par commodité (poids, ticker yfinance, dataset/nom de
marché CFTC, unité d'affichage, facteurs spécifiques) + les fonctions
génériques déjà listées, importées depuis `gold_score.py`. Évite de dupliquer
~700 lignes 3 fois pour une logique partagée à 80% ; les parties réellement
spécifiques (ratio or/argent, spread WTI-Brent, saisonnalité gaz, CFTC
générique sur 2 schémas de dataset) sont des fonctions dédiées, pas une
généralisation forcée.

**Format de sortie JSON** : identique au contrat de `score.json` existant
(`date`, `composite_score`, `interpretation`, `cftc_percentile`, `factors[]`,
`technical{note, spot, ma200}`, `calendar`, `alerts`) — pour que le frontend
puisse réutiliser `render(data)` et `renderFactorRows(factors)` tels quels,
seule la source du fetch et le libellé d'unité changent. Fichiers, miroir
exact du pattern `score.json`/`docs/score.json`/`score_history.json` déjà en
place pour l'or :
- `score_argent.json`, `docs/score_argent.json`, `score_history_argent.json`
- `score_petrole.json`, `docs/score_petrole.json`, `score_history_petrole.json`
- `score_gaz.json`, `docs/score_gaz.json`, `score_history_gaz.json`

**Calendrier macro** : réutilise `build_macro_calendar` de `gold_score.py`
tel quel (FOMC + publications FRED, identique pour toutes les commodités —
ce sont les mêmes événements macro qui comptent).

**Workflow GitHub Actions** : extension de `.github/workflows/daily.yml`
(même cron horaire, même détection "premier run après 7h UTC = run
quotidien" déjà en place) — 3 appels supplémentaires à
`commodity_score.py <argent|petrole|gaz>` après l'appel existant à
`gold_score.py`, mêmes secrets (`FRED_API_KEY` déjà configuré, pas de
nouvelle clé nécessaire pour la v1).

## Frontend

- L'onglet "Or" (`id="or"`, deux emplacements de nav `data-route="or"`)
  devient l'onglet **"Commodités"** avec une sous-navigation Or / Argent /
  Pétrole / Gaz (même mise en page que l'actuelle vue "Long terme", pas de
  changement du calcul de rendu `render(data)`/`renderFactorRows` sauf
  paramétrage du libellé d'unité — `$ / oz` ne convient pas au pétrole
  (`$ / baril`) ni au gaz (`$ / MMBtu`)).
- Suppression de l'onglet Scalping (`#orTabScalping`, `#orScalpingView`,
  `docs/scalping.js`, `loadScalpingModule`/`loadScalpChart`/
  `startScalpingPolling`/`stopScalpingPolling`) et de l'onglet Suivi
  (`#orTabSuivi`, `#orSuiviView`, `#scalpTrackingPanel`,
  `loadScalpTracking`, le CSS `#or.suivi-active`).
- **Ne pas toucher** : `#portefeuilleTabOr` (sous-onglet "Or" du
  Portefeuille — fonctionnalité totalement différente, qui ne concerne que
  le suivi manuel des positions or de l'utilisateur), ni le suivi de
  performance des signaux "entrée" de l'onglet Indices.

## Hors périmètre / limitations connues (v1)

- **Stocks hebdomadaires EIA** (pétrole + gaz) : nécessitent une clé API EIA
  gratuite (inscription sur eia.gov) que l'utilisateur n'a pas encore. En
  v1, ce facteur reste un **placeholder neutre explicitement affiché**
  ("indisponible — nécessite une clé API EIA, pas encore configurée") plutôt
  que de bloquer le calcul ; les poids des facteurs automatisés sont
  **renormalisés pour sommer à 100%** tant que ce facteur est absent (ne
  fausse pas le composite avec un poids mort). Une clé `EIA_API_KEY` pourra
  être ajoutée plus tard pour l'activer, sans changer le format de sortie.
- **Pas d'email** pour les 3 nouvelles commodités en v1 (seul l'or continue
  d'envoyer un email quotidien) — évite le bruit, décision de portée pour
  cette v1, pas une limitation technique.
- Les constantes d'échelle des nouveaux facteurs (spread WTI-Brent, ratio
  or/argent, saisonnalité gaz) sont des estimations raisonnables, pas issues
  d'un backtest empirique — même situation que les constantes déjà en place
  dans `gold_score.py` (`scale=0.5`, `scale=2.0`...), pas une régression de
  rigueur.
