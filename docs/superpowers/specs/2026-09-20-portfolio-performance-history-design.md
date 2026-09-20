# Historique de performance du portefeuille + comparaison à un indice — design

## Statut

Validé par l'utilisateur le 2026-09-20, prêt pour le plan d'implémentation.

## 1. Contexte et objectif

L'onglet Portefeuille → Actions → Analyse (`docs/portfolio.js`,
`portfolioAnalysisHtml`) affiche aujourd'hui uniquement des vues
**ponctuelles** : concentration, santé du modèle, contribution de
chaque ligne au P&L (`computePortfolioConcentration`/`Health`/
`Attribution`). Aucune de ces vues ne montre l'évolution dans le
temps, et `computePortfolioAttribution` documente explicitement cette
lacune dans son commentaire : *"pas de comparaison à l'indice sur la
période de détention (demanderait le cours de l'indice à la date
d'achat, non disponible pour des positions ajoutées manuellement à une
date arbitraire)"*.

Premier item du backlog de fonctionnalités établi le 2026-09-20
(mémoire persistante `project_feature_backlog.md`), choisi comme
priorité par l'utilisateur. Objectif : une courbe de performance du
portefeuille dans le temps, comparée à un indice de référence — ce que
font le mieux les trackers de portefeuille de référence (Sharesight,
Snowball Analytics).

## 2. Périmètre

### Dans le périmètre

- Un nouveau fichier public `docs/price_history.json`, alimenté
  quotidiennement par `indices_score.py`, couvrant l'historique de
  cours de **toutes les entreprises suivies** ET des **10 indices
  benchmark** (`INDEX_YFINANCE_TICKERS`).
- Une notion de **position clôturée** dans `docs/portfolio.js`
  (`sell_date`, `sell_price`), distincte de la suppression pure.
- Le calcul de la courbe de rendement (%) du portefeuille dans le
  temps, entièrement côté navigateur, à partir de
  `docs/price_history.json` + des positions (ouvertes et clôturées)
  stockées en local.
- L'affichage dans la section "Analyse" existante : un graphique par
  devise détenue (EUR/USD), avec sélecteur d'indice de référence
  (CAC40 par défaut).

### Explicitement hors périmètre

- Toute conversion de change entre devises (cohérent avec
  `computePortfolioTotals`, qui ne convertit jamais).
- Tout calcul de rendement pondéré dans le temps ("time-weighted
  return") au sens strict actuariel — la méthode retenue (§5) est une
  extension directe de `computePositionPnL`/`computePortfolioTotals`
  existants, pas une nouvelle méthodologie financière.
- Le suivi des dividendes, des métriques de risque (Sharpe, drawdown)
  et l'export fiscal — items séparés du même backlog, hors périmètre
  de cette spec.
- Toute modification du serveur/pipeline pour connaître les positions
  de l'utilisateur — elles restent exclusivement côté navigateur,
  comme aujourd'hui.

## 3. Architecture

### 3.1 Vue d'ensemble

```
indices_score.py (job quotidien, deja existant)
  |
  |-- fetch_company_financials() recupere deja history(period="6y")
  |   pour chaque entreprise (utilise pour ma200/current_price/ratios,
  |   jusqu'ici jete apres usage)
  |
  |-- NOUVEAU : persiste cet historique (+ celui des 10 indices
  |   benchmark) dans docs/price_history.json, avec retrimming
  |   hebdomadaire au-dela de 30 jours (voir 3.3)
  v
docs/price_history.json (nouveau fichier public)
  |
  v
docs/portfolio.js (navigateur) — NOUVEAU : combine price_history.json
  avec les positions ouvertes + cloturees (stockage local, jamais
  transmises au serveur) pour calculer la courbe de rendement
  |
  v
Section "Analyse" du Portefeuille — nouveau graphique
```

### 3.2 Pourquoi pas d'autre approche

- **Synchroniser les positions vers le serveur** pour qu'il calcule
  lui-même la courbe (comme le fait déjà `real_portfolio_mt5.json`
  pour l'onglet Or) : casserait la confidentialité actuelle de cet
  onglet (les positions "Actions" ne quittent aujourd'hui jamais le
  navigateur) et il n'existe aucun mécanisme d'écriture depuis ce site
  statique pour ça — rejeté.
- **Aller chercher les cours en direct depuis le navigateur** : pas de
  bibliothèque yfinance côté client, problèmes CORS/quota sur un site
  public — rejeté.

### 3.3 Taille du fichier et granularité

710 entreprises × 6 ans de cours quotidiens ≈ 25+ Mo — trop lourd pour
un PWA. Règle de downsampling, ré-appliquée à chaque exécution
(idempotente, ne dépend d'aucun état "a déjà été downsamplé") :

- Les entrées dont la date est dans les **30 derniers jours civils**
  restent **quotidiennes**, inchangées.
- Les entrées plus anciennes sont réduites à **une par semaine ISO**
  (la plus récente de la semaine est conservée, les autres jours de
  cette semaine sont supprimés).
- Rétention totale : 6 ans (2190 jours) par ticker, au-delà l'entrée
  la plus ancienne est supprimée.

Cette règle s'applique de façon identique aux entreprises et aux 10
indices benchmark, dans le même fichier.

## 4. Modèle de données

### 4.1 `docs/price_history.json`

Liste plate, même style que `docs/nikkei_hangseng_price_history.json` :

```json
[
  {"date": "2026-09-20", "ticker": "MC.PA", "price": 620.4},
  {"date": "2026-09-20", "ticker": "^FCHI", "price": 7850.2}
]
```

`ticker` est soit un ticker d'entreprise suivie (identique à
`docs/indices.json`), soit un ticker d'indice benchmark (les valeurs
de `INDEX_YFINANCE_TICKERS` : `^FCHI`, `^GDAXI`, `^NDX`, `^DJI`,
`^FTSE`, `^SSMI`, `^IBEX`, `FTSEMIB.MI`, `^N225`, `^HSI`). `price` est
toujours en devise de cotation native (pas de conversion), même
convention que `docs/indices.json::current_price`.

**Backfill initial** : la première fois qu'un ticker apparaît dans le
fichier, ses entrées sont dérivées de la série complète déjà en
mémoire (`history(period="6y")`), downsamplée selon la règle 3.3, pas
seulement du jour courant.

### 4.2 Positions clôturées (`docs/portfolio.js`)

Nouvelle clé de stockage local `PORTFOLIO_CLOSED_STORAGE_KEY =
'analyse-or-portfolio-closed'`, séparée de `PORTFOLIO_STORAGE_KEY`
existante (positions ouvertes). Une position clôturée reprend tous les
champs d'une position ouverte (`id`, `ticker`, `quantity`,
`buy_price`, `buy_date`) plus :

```json
{ "sell_date": "2026-09-15", "sell_price": 95.2 }
```

Nouvelle fonction `closePosition(id, sellPrice, sellDate, storage)` :
retire la position de la liste ouverte (`loadPortfolio`), l'ajoute
(avec `sell_date`/`sell_price`) à la liste clôturée
(`loadClosedPortfolio`/`saveClosedPortfolio`, mêmes conventions de
repli que `loadPortfolio`/`savePortfolio` — jamais d'exception,
`[]`/`false` en cas d'échec). `removePosition` reste inchangée (une
vraie suppression, réservée aux erreurs de saisie, n'entre jamais dans
l'historique).

Dans "Tes positions" : un bouton "Vendre" à côté de
"Modifier"/"Supprimer" ouvre un petit formulaire (prix de vente, date
de vente) qui appelle `closePosition`.

## 5. Méthode de calcul

Extension directe de `computePositionPnL`/`computePortfolioTotals`
existants sur un axe temporel, pas une nouvelle méthodologie :

- Pour chaque date `D` de l'historique (une par entrée disponible dans
  `price_history.json`, agrégées sur l'union des tickers détenus) :
  - Une position (ouverte ou clôturée) est "active" à la date `D` si
    `buy_date <= D` et (`sell_date` absent OU `sell_date >= D`).
  - Pour chaque position active à `D`, prix utilisé = le cours connu
    dans `price_history.json` à la date `D` la plus proche
    disponible ≤ D (jamais d'extrapolation future) ; si aucune entrée
    ≤ D n'existe pour ce ticker, la position est ignorée pour cette
    date (comme `computePortfolioTotals` ignore déjà une position sans
    cours connu).
  - `cost = buy_price * quantity` (fixe), `value = prix(D) * quantity`.
  - Agrégées par devise (même règle que `computePortfolioTotals` :
    jamais mélangées) : `pnlPct(D) = somme(value - cost) / somme(cost)
    * 100` — rendement pondéré par le capital investi, pas une moyenne
    simple des % de chaque ligne (cohérent avec la façon dont
    `computePortfolioTotals` somme déjà `value`/`pnlAbs` plutôt que
    des pourcentages).
- La courbe benchmark réutilise **exactement la même liste de
  positions** (mêmes `buy_date`/`sell_date`/`cost` par position, même
  regroupement par devise) — seule la source de prix change : au lieu
  du cours de l'entreprise, chaque position "fantôme" utilise le cours
  de l'indice benchmark choisi à la même date. Concrètement, la
  fonction d'agrégation du §5 est réutilisée à l'identique avec un
  paramètre `priceForPosition(position, date)` différent (celui du
  portefeuille réel : `price_history[position.ticker][date]` ; celui
  du benchmark : `price_history[indexTicker][date]` pour toutes les
  positions). Ça garantit une comparaison réellement apples-to-apples
  (mêmes dates d'entrée/sortie, même pondération par capital investi)
  plutôt qu'un montant forfaitaire investi une seule fois.

## 6. UI

Dans la section "Analyse" du carrousel Portefeuille → Actions,
au-dessus du bloc concentration existant : un graphique en ligne par
devise détenue (EUR et/ou USD, selon ce que l'utilisateur possède —
aucun graphique si aucune position dans cette devise), tracé en
Canvas/SVG fait main (même style que le reste du site, pas de nouvelle
dépendance JS — TradingView, déjà utilisé pour Or/Scalping, ne convient
pas ici puisqu'il n'affiche pas de série personnalisée). Une liste
déroulante par graphique permet de changer l'indice de référence parmi
les 10 suivis (CAC40 par défaut).

## 7. Tests

- **Python** (`indices_score.py`) : nouvelle fonction de persistance
  (`update_price_history`), testée sur le modèle de
  `test_fetch_company_financials_uses_price_history_override...`/
  `update_nikkei_hangseng_price_history` déjà existants — backfill
  initial, downsampling après 30 jours, rétention 6 ans, dégradation
  vers `[]` sans exception.
- **JavaScript** (`docs/portfolio.js`) : nouvelles fonctions pures
  testables depuis Node (`docs/portfolio.test.js`, même modèle que les
  tests déjà là pour `computePortfolioConcentration`/`Health`) —
  `closePosition`, calcul de la courbe de rendement, agrégation par
  devise, position sans cours connu ignorée proprement.
- **Vérification visuelle** : Playwright réel (pattern déjà établi
  cette session pour le tableau de bord Bot Actions) pour le rendu du
  graphique et le sélecteur d'indice.

## 8. Points arbitrés

- **Rendement en % plutôt qu'en valeur absolue en €** — une valeur
  brute bouge aussi avec les achats/ventes (mouvement de capital, pas
  performance) ; cohérent avec `signal_tracking.json`, qui raisonne
  déjà en % pour le suivi des signaux.
- **Pas de conversion de change** — cohérent avec
  `computePortfolioTotals`, deux courbes séparées EUR/USD plutôt
  qu'une conversion approximative.
- **Downsampling hebdomadaire au-delà de 30 jours** — compromis taille
  de fichier/précision ; les achats récents (période la plus
  consultée) restent en quotidien.
- **`docs/price_history.json` couvre aussi les 10 indices benchmark**,
  pas seulement les entreprises — nécessaire pour la courbe de
  comparaison, absent de tout mécanisme existant
  (`fetch_index_prices()` ne capture qu'un instantané du jour).
