# Suivi des dividendes du portefeuille — design

## Statut

Validé par l'utilisateur le 2026-09-25, prêt pour le plan d'implémentation.

## 1. Contexte et objectif

Deuxième item du backlog de fonctionnalités établi le 2026-09-20 (mémoire
persistante `project_feature_backlog.md`), choisi comme priorité par
l'utilisateur après la courbe de performance (voir
`docs/superpowers/specs/2026-09-20-portfolio-performance-history-design.md`,
qui exclut explicitement le suivi des dividendes de son périmètre — §2 :
*"Le suivi des dividendes... items séparés du même backlog"*).

Objectif : donner, dans l'onglet Portefeuille, trois informations que
suivent déjà les trackers de référence (Snowball Analytics) — revenu
projeté (annuel/mensuel), cumul de dividendes réellement encaissés, et
rendement sur coût (dividend yield on cost) par ligne.

Utilité confirmée par l'utilisateur avant le design : le portefeuille
détient aujourd'hui plusieurs positions versant un dividende.

`indices_score.py` récupère déjà l'historique complet des dividendes de
chaque entreprise (`fetch_dividend_history`, ligne ~2713 — `yfinance`
`Ticker.dividends`), aujourd'hui utilisé uniquement pour calculer
`dividend_streak_years` (nombre d'années consécutives de versement, critère
Graham), puis jeté. Aucune nouvelle collecte de données n'est nécessaire.

## 2. Périmètre

### Dans le périmètre

- Un nouveau fichier public `docs/dividend_history.json`, alimenté
  quotidiennement par `indices_score.py`, réutilisant la Series déjà
  récupérée par `fetch_dividend_history` pour chaque entreprise suivie.
- Trois métriques calculées entièrement côté navigateur
  (`docs/portfolio.js`), à partir de `docs/dividend_history.json` + des
  positions (ouvertes et clôturées) déjà stockées en local :
  - Cumul de dividendes réellement encaissés (positions ouvertes et
    clôturées).
  - Revenu projeté (annuel + moyenne mensuelle), sur les positions
    ouvertes uniquement.
  - Rendement sur coût (yield on cost) par position ouverte qui verse
    effectivement un dividende.
- Une nouvelle sous-section "Dividendes" dans "Analyse" (Portefeuille →
  Actions), à côté de la concentration/santé/courbe de performance déjà
  là.

### Explicitement hors périmètre

- Toute conversion de change entre devises — même convention que
  `computePortfolioTotals`/`computePortfolioPerformanceCurves` : jamais de
  conversion, deux totaux séparés EUR/USD.
- Calendrier de versement (quel mois précis tombe quel paiement) — le
  "revenu mensuel projeté" est une moyenne (annuel / 12), pas une
  répartition calendaire réelle. Amélioration possible plus tard, pas
  demandée ici.
- Graphique (courbe/histogramme) des dividendes — les 3 métriques sont
  ponctuelles/tabulaires, pas une série temporelle à visualiser. Cohérent
  avec YAGNI : rien dans la demande n'appelle un graphique.
- Alertes ou notifications liées aux dividendes (ex-date à venir) —
  aucune demande en ce sens.
- Modification du modèle de position (`buy_date`/`sell_date`/`quantity`
  restent les seuls champs pertinents) — aucun nouveau champ requis sur
  une position.

## 3. Architecture

### 3.1 Vue d'ensemble

```
indices_score.py (job quotidien, deja existant)
  |
  |-- fetch_dividend_history(t) recupere deja t.dividends pour chaque
  |   entreprise (utilise pour dividend_streak_years, jusqu'ici jete
  |   apres usage)
  |
  |-- NOUVEAU : persiste cette Series (+ celle de chaque entreprise) dans
  |   docs/dividend_history.json, sans downsampling ni fenetre de
  |   retention (voir 3.3)
  v
docs/dividend_history.json (nouveau fichier public)
  |
  v
docs/portfolio.js (navigateur) — NOUVEAU : combine dividend_history.json
  avec les positions ouvertes + cloturees (stockage local, jamais
  transmises au serveur) pour calculer les 3 metriques
  |
  v
Section "Analyse" du Portefeuille — nouvelle sous-section "Dividendes"
```

Même schéma exactement que `docs/price_history.json` (stash sur l'entrée
company → pop dans `main()` → flush fichier), pas une nouvelle
architecture : `_dividend_history` est stashé sur `ratios` dans
`fetch_company_financials`, propagé par `build_company_entry`, popé et
écrit dans `main()` à côté du flush de `price_history_entries`.

### 3.2 Pourquoi pas d'autre approche

- **Bundler les paiements de dividendes dans `docs/indices.json`** (un
  champ par entreprise) : rejeté — grossirait le fichier le plus
  fréquemment téléchargé du site (`docs/indices.json`) pour une donnée
  utile seulement à l'onglet Portefeuille ; `docs/price_history.json` a
  déjà établi le pattern "fichier dédié, séparé, pour une donnée
  historique consommée uniquement par le Portefeuille".
- **Aller chercher les dividendes en direct depuis le navigateur** : même
  rejet que pour les prix (§3.2 de la spec performance-history) — pas de
  bibliothèque yfinance côté client, CORS/quota sur un site public.

### 3.3 Pas de downsampling ni de fenêtre de rétention

Contrairement à `docs/price_history.json` (cours quotidiens, des millions
de points bruts avant downsampling), les paiements de dividendes sont
trimestriels ou annuels : même une entreprise cotée depuis 20 ans avec un
historique de dividende ininterrompu totalise de l'ordre de 80 lignes.
Sur 710 entreprises, le fichier complet reste de l'ordre de quelques
dizaines de milliers de lignes — largement en dessous de toute contrainte
de taille pour un PWA.

Plus important : **tronquer l'historique sous-compterait le cumul
réellement encaissé**. Une position ouverte il y a plusieurs années doit
pouvoir sommer tous les dividendes reçus depuis son achat — une fenêtre de
rétention de type `PRICE_HISTORY_RETENTION_DAYS` (6 ans) ferait disparaître
des paiements réels pour une position plus ancienne, ce qui serait un bug
silencieux (le cumul affiché serait inférieur au cumul réel). Décision :
`docs/dividend_history.json` conserve l'historique complet depuis
toujours, sans troncature, pour chaque ticker.

## 4. Modèle de données

### 4.1 `docs/dividend_history.json`

Liste plate, même style que `docs/price_history.json` :

```json
[
  {"date": "2026-03-12", "ticker": "MC.PA", "amount": 3.55},
  {"date": "2025-12-10", "ticker": "MC.PA", "amount": 3.55}
]
```

`ticker` identique à `docs/indices.json`. `amount` = dividende versé par
action, en devise de cotation native (pas de conversion), même convention
que `docs/price_history.json::price`. `date` = date d'ex-dividende
(index de la Series `yfinance` `Ticker.dividends`).

### 4.2 Backend (`indices_score.py`)

```python
DIVIDEND_HISTORY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "docs", "dividend_history.json"
)


def load_dividend_history(path=DIVIDEND_HISTORY_PATH) -> list[dict]:
    """Meme contrat que load_price_history : [] si le fichier est absent
    ou corrompu, jamais d'exception."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError:
        return []


def update_dividend_history(new_entries: list[dict], path=DIVIDEND_HISTORY_PATH) -> list[dict]:
    """Ajoute `new_entries` ({date, ticker, amount}) a l'historique deja
    accumule, deduplique par (ticker, date) -- en cas de doublon, la
    derniere valeur de new_entries l'emporte (une re-recuperation yfinance
    plus recente est preferee a l'ancienne, meme motif que le reste du
    pipeline qui fait toujours confiance a la donnee la plus fraiche).
    Pas de downsampling ni de fenetre de retention (voir 3.3). Degrade
    toujours vers [] sur erreur, ne fait jamais echouer main()."""
    try:
        history = load_dividend_history(path)
        by_key = {(e["ticker"], e["date"]): e for e in history}
        for entry in new_entries:
            by_key[(entry["ticker"], entry["date"])] = entry
        merged = sorted(by_key.values(), key=lambda e: (e["ticker"], e["date"]))
        rounded = [{**entry, "amount": round(entry["amount"], 4)} for entry in merged]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rounded, fh, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        return rounded
    except Exception as e:
        print(f"Erreur historique de dividendes : {e}")
        return []
```

Câblage, en trois points exactement symétriques du câblage de
`_price_history_daily` :

1. Dans `fetch_company_financials`, juste après la ligne existante
   `ratios["_price_history_daily"] = [...]` :

   ```python
   ratios["_dividend_history"] = [
       {"date": idx.strftime("%Y-%m-%d"), "ticker": ticker, "amount": float(val)}
       for idx, val in dividends.items()
   ]
   ```

   (`dividends` est la Series déjà calculée ligne 2909 — `dividends =
   fetch_dividend_history(t)` — aucun nouvel appel réseau.)

2. Dans `build_company_entry`, juste après la ligne existante
   `entry["_price_history_daily"] = data["_price_history_daily"]` :

   ```python
   entry["_dividend_history"] = data["_dividend_history"]
   ```

3. Dans `main()`, juste après le flush existant de
   `price_history_entries` :

   ```python
   dividend_history_entries = []
   for c in companies:
       dividend_history_entries.extend(c.pop("_dividend_history", []))
   update_dividend_history(dividend_history_entries)
   ```

## 5. Méthode de calcul (`docs/portfolio.js`)

Toutes les fonctions sont pures, testables depuis Node (même contrat que
`computePerformanceCurve`/`computePortfolioTotals`), jamais de conversion
de devise, agrégation par devise via `currencyByIndex[company.index]`
(EUR/USD) — même convention que le reste du fichier.

```js
/**
 * Regroupe docs/dividend_history.json par ticker, meme forme que
 * groupPriceHistoryByTicker.
 */
function groupDividendHistoryByTicker(dividendHistory) { ... }

/**
 * Cumul de dividendes reellement encaisses, positions ouvertes ET
 * cloturees (un paiement recu pendant la detention reste un encaissement
 * reel meme si la position est cloturee depuis). Un paiement compte pour
 * une position si isPositionActiveOn(position, paiement.date). Agrege
 * par devise ({ EUR: montant, USD: montant }), jamais converti/melange.
 */
function computeDividendsReceived(positions, dividendHistoryByTicker, companiesByTicker, currencyByIndex) { ... }

/**
 * Revenu projete : pour chaque position OUVERTE, somme des paiements par
 * action des 365 derniers jours (TTM, trailing twelve months) x quantite
 * detenue. Agrege par devise. Retourne { EUR: { annual, monthly }, USD:
 * { annual, monthly } } -- monthly = annual / 12 (moyenne, pas un
 * calendrier reel -- voir §2 hors perimetre).
 */
function computeProjectedDividendIncome(openPositions, dividendHistoryByTicker, companiesByTicker, currencyByIndex, today) { ... }

/**
 * Rendement sur cout par position OUVERTE : dividende TTM par action /
 * buy_price * 100. Ne retourne que les positions dont le TTM > 0 (les
 * non-payeuses sont exclues, pas affichees a 0% -- eviter le bruit dans
 * la liste). Tableau [{ ticker, ttmPerShare, yieldOnCost, projectedAnnual }, ...].
 */
function computeYieldOnCost(openPositions, dividendHistoryByTicker, companiesByTicker, today) { ... }
```

Détails de calcul communs :

- **TTM (trailing twelve months)** = somme des `amount` d'un ticker dont
  `date` est dans `]today - 365 jours, today]`. Pas d'extrapolation si
  l'historique est plus court (ex. IPO récente) — le TTM reflète
  simplement ce qui a été réellement versé sur la fenêtre, potentiellement
  inférieur à un rythme "normalisé" en année pleine (cohérent avec
  `priceAtOrBefore`/`computePerformanceCurve` qui n'extrapolent jamais).
- Un ticker absent de `dividendHistoryByTicker` (jamais versé de
  dividende, ou retiré de l'indice suivi) contribue 0 à toutes les
  métriques, sans erreur — même philosophie que `computePortfolioTotals`
  ignorant une position sans cours connu.
- Une position dont le ticker n'est pas dans `companiesByTicker` (retiré
  de l'indice suivi) est ignorée, même contrat que
  `computePortfolioTotals`.

## 6. UI

Nouvelle sous-section "Dividendes" dans `portfolioAnalysisHtml`
(`docs/index.html`), après le bloc concentration/santé existant et la
courbe de performance :

- Trois chiffres clés par devise détenue (EUR et/ou USD, uniquement celles
  où le portefeuille a une position) : cumul reçu, revenu annuel projeté,
  revenu mensuel moyen projeté.
- Un petit tableau des positions ouvertes qui versent effectivement un
  dividende (TTM > 0) : ticker/nom, dividende TTM par action, rendement
  sur coût, revenu annuel projeté de la ligne. Triable comme les tableaux
  existants du Portefeuille (réutilise les classes CSS `list-sort`
  déjà en place, pas de nouveau composant visuel).
- Aucune position payant un dividende → la sous-section affiche un message
  informatif ("Aucune position ne verse actuellement de dividende") plutôt
  que de disparaître silencieusement — cohérent avec le principe déjà
  appliqué ailleurs dans le Portefeuille (une liste vide reste visible
  avec un message, jamais un bloc qui disparaît sans explication).

## 7. Tests

- **Python** (`indices_score.py`) : nouvelle fonction de persistance
  (`update_dividend_history`), testée sur le modèle des tests déjà
  existants pour `update_price_history` — accumulation, déduplication par
  `(ticker, date)` avec la valeur la plus récente qui l'emporte,
  dégradation vers `[]` sans exception (fichier corrompu, `NaN` détecté
  par `allow_nan=False`). Test de câblage : `fetch_company_financials`
  peuple `_dividend_history` à partir de la même Series `dividends` que
  celle utilisée pour `dividend_streak_years` (pas un second appel
  réseau).
- **JavaScript** (`docs/portfolio.js`) : nouvelles fonctions pures
  testables depuis Node (`docs/portfolio.test.js`, même modèle que les
  tests déjà là pour `computePortfolioTotals`/`computePerformanceCurve`) :
  - `computeDividendsReceived` : positions ouvertes et clôturées, paiement
    reçu avant l'achat exclu, paiement reçu après la clôture exclu,
    agrégation EUR/USD séparée.
  - `computeProjectedDividendIncome` : fenêtre TTM correcte (paiement de
    plus de 365 jours exclu), positions clôturées exclues, `monthly =
    annual / 12`.
  - `computeYieldOnCost` : calcul correct par position, non-payeuses
    (TTM = 0) absentes du résultat, ticker sans historique de dividende
    ignoré sans erreur.
- **Vérification visuelle** : même limite déjà signalée cette session pour
  la segmentation par périmètre du paper-trading — pas d'outillage
  navigateur/Node disponible dans cet environnement, donc relecture de
  code uniquement pour le rendu HTML/CSS, pas de test visuel réel. À
  confirmer visuellement par l'utilisateur une fois le site redéployé.

## 8. Points arbitrés

- **Pas de fenêtre de rétention sur `docs/dividend_history.json`**
  (contrairement à tous les autres fichiers d'historique du projet) —
  justifié en détail au §3.3 : la faible densité des données rend la
  troncature inutile, et elle introduirait un bug silencieux de
  sous-comptage pour les positions anciennes.
- **Cumul reçu inclut les positions clôturées** — un dividende encaissé
  pendant la détention reste un encaissement réel même après la vente de
  la position ; seuls le revenu projeté et le rendement sur coût sont
  restreints aux positions ouvertes (projeter un revenu futur sur une
  position qu'on ne détient plus n'a pas de sens).
- **Revenu mensuel = moyenne (annuel / 12), pas un calendrier réel** —
  cohérent avec le principe YAGNI : aucune demande pour un calendrier de
  versement, et le construire correctement demanderait de connaître le
  rythme réel de chaque entreprise (trimestriel, semestriel, annuel,
  irrégulier) pour repartir les paiements sur les bons mois.
- **Non-payeuses exclues du tableau par position plutôt qu'affichées à
  0%** — évite le bruit visuel dans un portefeuille où toutes les lignes
  ne versent pas de dividende.
