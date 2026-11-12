# Support des ETF dans l'onglet Indices — Design

## Contexte

Le site suit aujourd'hui des actions individuelles, regroupées par indice
(CAC 40, S&P 500, etc.), chacune notée par un score composite -100/+100
construit à partir de 7 facteurs fondamentaux "Vernimmen-style" (rentabilité,
structure financière, croissance, génération de cash, valorisation,
dynamique récente, actualité récente — voir `indices_score.py`).

L'utilisateur veut pouvoir suivre une sélection d'ETF (Amundi, et quelques
émetteurs tiers suivant les mêmes indices MSCI/S&P) dans ce même onglet
Indices.

**Incompatibilité de fond, déjà vérifiée dans le code existant avant ce
design** : la méthodologie de score actuelle suppose un compte de résultat
et un bilan (EBITDA, EBIT, ROCE, dette nette/EBITDA...). Un ETF n'en a pas —
c'est un panier de titres, pas une entreprise. Même le cas le plus proche
déjà géré par le site (`TRUST_TICKERS`, les trusts d'investissement fermés
côtés à Londres) reste un panier d'actifs *avec* un bilan propre (capitaux
propres, NAV) ; un ETF classique n'a même pas ça. Aucun des chemins de
récupération/extraction de données existants (`fetch_company_financials`,
`extract_ratios`, `extract_ratios_financial`) ne peut donc être réutilisé
tel quel pour un ETF : il faut un chemin de données entièrement nouveau,
pas un 4ᵉ branchement sur les fonctions existantes.

## Décision validée avec l'utilisateur

**Piste A retenue** (sur 2 proposées) : pas de score composite inventé pour
les ETF. À la place, la fiche d'un ETF affiche les métriques qui comptent
réellement pour ce type d'instrument : frais de gestion (TER), méthode de
réplication, éligibilité PEA, politique de distribution, indice suivi, et
un écart de suivi calculé par le site lui-même.

Raison du choix (déjà actée en conversation, reprise ici pour mémoire) :
un score inventé pour les ETF (ex. pondération coût/liquidité/risque)
serait arbitraire, contrairement au score actions actuel qui s'appuie sur
une vraie théorie financière (Vernimmen) déjà documentée et déjà cohérente
dans le code. Mieux vaut rester honnête sur ce qui est mesurable plutôt que
fabriquer un second système de notation moins défendable.

## Périmètre

### Univers d'ETF (~20-25, liste candidate — tickers à vérifier avant codage)

Catégories visées (exposition, pas liste finale de tickers) :

- **Larges / géographiques** : MSCI World, S&P 500, Nasdaq-100, MSCI
  Emerging Markets, MSCI Europe, MSCI Japan
- **Zone euro / France** : CAC 40, Euro Stoxx 50, MSCI EMU
- **Matières premières** : Or physique
- **Quelques sectoriels** (santé, technologie)
- **Diversité d'émetteur** : au moins un ETF tiers (iShares ou équivalent)
  suivant un indice déjà présent dans la liste Amundi, pour varier au-delà
  d'un seul émetteur (demande explicite de l'utilisateur : "tous les etf
  amundi, msci etc")

**Contrainte dure pour l'implémentation** : chaque ticker Euronext exact,
chaque TER, chaque méthode de réplication, chaque éligibilité PEA et
chaque politique de distribution (capitalisant/distribuant) doit être
vérifié par recherche web au moment de construire la table statique — rien
de ces valeurs ne doit être codé en dur de mémoire. Ce sont des données
financières réelles affichées à l'utilisateur comme "vraies métriques,
pas un score inventé" (c'est la raison d'être de la piste A) : une valeur
fausse ici détruirait exactement l'argument qui a fait choisir cette piste.
La tâche de plan correspondante doit inclure explicitement cette étape de
vérification avant toute écriture de la table.

### Hors périmètre (explicitement exclu de ce chantier)

- **Opportunités (signal d'entrée)** : `findOpportunities` repose sur les
  alertes `entree`/`actu_majeure` du score actions, qui n'existent pas pour
  un ETF. Les ETF ne sont pas candidats à ce scanner. Pas de changement à
  `findOpportunities` ni à son UI.
- **Score composite, facteurs, badge Graham** : aucun de ces concepts ne
  s'applique. Pas de tentative d'adaptation partielle (ex. "score sur 3
  facteurs au lieu de 7") — l'ensemble du concept de score est absent pour
  ce type d'instrument, pas réduit.
- **Nouveaux tests e2e navigateur** : comme pour `docs/index.html` dans
  tout le reste du site, pas de convention de test pour le rendu HTML/JS —
  seule la logique pure (si une fonction de calcul testable émerge, ex.
  l'écart de suivi) suit la convention `docs/portfolio.test.js`-style déjà
  en place si elle finit dans `docs/portfolio.js` ou un fichier JS
  équivalent purement calculatoire.

## Modèle de données

### Table statique (nouveau fichier Python, maintenue à la main)

Un nouveau module `etf_catalog.py` (ou une section dédiée dans
`indices_score.py`, à trancher au moment du plan selon la taille réelle une
fois la liste de tickers vérifiée) porte, par ETF :

```python
ETF_CATALOG = [
    {
        "ticker": "...",            # ticker Euronext exact, à vérifier
        "name": "...",
        "benchmark_name": "...",     # ex. "MSCI World"
        "benchmark_ticker": "...",   # ticker yfinance de l'indice suivi,
                                     # pour calculer l'écart de suivi --
                                     # réutilise si possible les tickers
                                     # déjà connus de INDEX_BENCHMARK_TICKERS
                                     # (docs/index.html) plutôt que d'en
                                     # inventer de nouveaux
        "ter_pct": 0.0,              # frais de gestion annuels, en %
        "replication": "physique" | "synthetique",
        "distribution": "capitalisant" | "distribuant",
        "pea_eligible": True | False,
        "isin": "...",               # utile pour lever toute ambiguité de
                                     # ticker au moment de la vérification
    },
    # ...
]
```

Chaque champ (sauf `ticker`/`name`/`benchmark_ticker`, mécaniques) est une
donnée publique figée (fiche produit de l'émetteur), pas une donnée de
marché qui bouge au quotidien — d'où le choix d'une table statique plutôt
que d'essayer de la faire ressortir de yfinance, qui ne l'expose pas de
façon fiable pour les ETF UCITS européens (vérifié avant ce design).

### Données récupérées dynamiquement (par ticker, à chaque run)

Seuls les champs qui bougent réellement sont allés chercher via yfinance,
dans une nouvelle fonction `fetch_etf_data(ticker)` (nouveau chemin,
**ne réutilise pas** `fetch_company_financials`/`extract_ratios*`, qui
supposent un compte de résultat inexistant pour un ETF) :

- Prix courant, historique de prix (même shape que pour une action, pour
  réutiliser le composant de graphique existant côté frontend)
- Rendement du dividende courant si disponible (`None` si l'ETF est
  capitalisant — pas de dividende à afficher, pas une donnée manquante à
  masquer artificiellement)

### Écart de suivi (tracking difference)

Calculé par le site, pas lu depuis une source externe : performance de
l'ETF sur une période donnée moins performance de son indice de référence
sur la même période, à partir des historiques de prix déjà présents côté
site (réutilise le même mécanisme que
`computeBenchmarkPerformanceCurve`/`INDEX_BENCHMARK_TICKERS` côté
portefeuille, qui sait déjà comparer une courbe de prix à un indice de
référence). Si l'historique de l'indice de référence n'est pas disponible
pour la période demandée, afficher "indisponible", jamais une valeur
approximée.

### Forme de l'entrée dans `docs/indices.json`

Un ETF obtient une entrée dans `COMPANIES` au même format que les actions
pour tout ce qui est mécanique (ticker, name, index, current_price, news),
plus un drapeau et un objet dédié :

```json
{
  "ticker": "...", "name": "...", "index": "ETF",
  "current_price": 0.0, "news": [...],
  "is_etf": true,
  "etf_info": {
    "benchmark_name": "...", "ter_pct": 0.0, "replication": "...",
    "distribution": "...", "pea_eligible": true,
    "tracking_difference_pct": 0.0
  }
}
```

Les champs `score`, `factors`, `sector`, `sector_profile`, `is_financial`,
`is_trust`, `alerts` sont **absents** (pas `null` ni `0` par convention
cachée — absents, pour qu'une vérification `"factors" in entry` distingue
sans ambiguïté un ETF d'une action, même convention de rigueur que les
gardes `data_available=False` déjà présents dans `indices_score.py` pour
ne jamais faire passer une donnée absente pour un vrai zéro).

`index: "ETF"` : nouvelle clé d'index au même sens que `"CAC40"` ou
`"SP500"` pour tout ce qui groupe/filtre par indice côté frontend
(`INDEX_DISPLAY_NAMES`, le sélecteur d'indice de l'onglet Indices), sans
induire que ce sont des "constituants" d'un indice ETF qui n'existe pas —
c'est une catégorie d'affichage, pas un vrai indice boursier.

## Pipeline backend (`indices_score.py` ou nouveau module)

1. `ETF_CATALOG` (table statique ci-dessus), vérifiée ticker par ticker
   avant d'être écrite.
2. `fetch_etf_data(ticker)` — nouvelle fonction, prix + historique + yield
   optionnel, aucune réutilisation des chemins orientés compte de résultat.
3. `build_etf_entry(catalog_entry)` — assemble l'entrée JSON (forme
   ci-dessus), calcule l'écart de suivi si l'historique de l'indice de
   référence est disponible.
4. `main()` : après la boucle existante sur `COMPANIES` (actions), une
   boucle équivalente sur `ETF_CATALOG` ajoute les entrées ETF au même
   fichier de sortie `docs/indices.json` — un seul fichier, pas un second
   fichier de données à charger côté frontend.

## Frontend (`docs/index.html`)

- **Sélecteur d'indice** (onglet Indices) : `"ETF"` devient une valeur
  possible au même titre que les indices boursiers existants, avec un nom
  d'affichage ("ETF") dans `INDEX_DISPLAY_NAMES`. Pas de pays associé
  (`INDEX_COUNTRY_BY_KEY` reste absent pour cette clé, le badge pays ne
  s'affiche déjà que si la clé y figure).
- **Liste/cartes** : la carte d'un ETF dans la liste n'affiche pas le badge
  de score/interprétation (absent par construction, voir plus haut) ; elle
  montre à la place nom, ticker, prix, variation du jour, et une mention
  courte type "TER X% · PEA" directement dans la carte si la place le
  permet (détail exact du gabarit laissé au plan d'implémentation).
- **Fiche détail** (`renderCompanyDetail`) : nouveau branchement
  `if (company.is_etf)` en tout début de fonction, qui rend un gabarit
  dédié plus court — graphique de prix (réutilise le composant existant),
  puis un panneau "Caractéristiques" (indice suivi, TER, réplication,
  distribution, éligibilité PEA, écart de suivi), puis les actualités
  (même composant que pour une action). Pas de jauge de score, pas de
  barres de facteurs, pas de bloc Graham — ces blocs ne sont simplement pas
  rendus pour ce type d'entrée, plutôt que rendus vides.
- **Portefeuille** : aucun changement de code nécessaire — un ETF présent
  dans `indicesData.companies` devient mécaniquement sélectionnable dans le
  combobox "Ajouter une position" (qui parcourt déjà toute la liste), et
  toutes les fonctions `computePortfolio*` qui lisent `company.current_price`/
  `company.index` continuent de fonctionner sans modification. Conséquence
  naturelle de l'ajout des données, pas un développement séparé.

## Risques / points de vigilance

- **Fiabilité yfinance pour les ETF UCITS** : même le prix/l'historique
  peuvent manquer pour certains tickers européens moins liquides —
  dégrader proprement (prix "indisponible", pas une exception qui casse le
  run) plutôt que de bloquer tout le pipeline sur un seul ETF en échec,
  même convention que le reste de `indices_score.py` pour les données
  actions manquantes.
- **Exactitude des données statiques** : seul vrai risque de ce chantier
  (voir contrainte dure plus haut) — traité comme tâche de vérification
  explicite dans le plan, pas comme un détail d'implémentation.
- **Double emploi visuel avec les indices actions** : vérifier au moment
  du plan que le sélecteur d'indice gère bien une entrée sans drapeau pays
  sans régression visuelle (l'essentiel du gabarit existant semble déjà
  conditionnel sur la présence de la clé, à confirmer en lisant le code
  réel au moment d'écrire les tâches, pas supposé ici).
