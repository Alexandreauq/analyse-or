# Portefeuille personnel — design

## Contexte et objectif

L'utilisateur veut pouvoir suivre les actions qu'il détient réellement
dans l'app, en profitant du score/des signaux déjà calculés par le
module Indices (CAC40/DAX/Nasdaq/Dow, `indices_score.py`), plutôt que de
jongler entre son courtier et le site pour savoir où en est chaque
ligne.

**Contrainte fondatrice :** ce site est 100% statique (Python → JSON →
GitHub Pages), sans backend qui tourne en continu. Une exécution
automatique d'ordres réels (l'objectif final évoqué par l'utilisateur —
recevoir un signal, valider d'un oui/non, que le site passe l'ordre)
nécessiterait un serveur, des identifiants de courtage à sécuriser, et
porte un vrai risque financier en cas de bug. C'est explicitement une
**phase 2 séparée**, à concevoir plus tard une fois cette phase 1
validée en usage réel. Ce document ne couvre que la phase 1 : suivi +
signaux, exécution manuelle par l'utilisateur chez son courtier.

## Portée

**Dans le périmètre (phase 1) :**
- Formulaire manuel : ajouter/modifier/supprimer une position (ticker
  choisi parmi les ~180 entreprises déjà suivies, quantité, prix
  d'achat, date d'achat).
- Une ligne = un lot acheté, pas un solde moyen par ticker — rien
  n'empêche deux lots distincts sur le même ticker (deux achats à des
  prix différents), chacun modifiable/supprimable indépendamment. Pas de
  calcul de prix de revient moyen en v1.
- Affichage par position : cours actuel, P&L latent (valeur et %), score
  composite et alertes actives de l'entreprise (réutilise l'existant :
  `alertColor`, badges d'indice, notice profil financier).
- Deux totaux de portefeuille séparés, EUR et USD (pas de conversion —
  voir décision utilisateur du 2026-09-10).
- Section "Opportunités" : entreprises suivies avec un signal `entree`
  actif que l'utilisateur ne détient pas encore.
- Stockage 100% côté client (`localStorage`) — aucun compte, aucun
  serveur, les données restent sur l'appareil.

**Hors périmètre (v1) :**
- Import CSV d'un relevé de courtier.
- Connexion à un broker, exécution automatique d'ordres (phase 2).
- Conversion de devises / total unique blendé.
- Tickers hors des ~180 entreprises déjà suivies (elles n'ont pas de
  score/signal calculé — les accepter alourdirait largement la phase 1).
- Synchronisation entre appareils (`localStorage` est local à un
  navigateur/appareil, assumé et documenté, pas un défaut à corriger).
- Historique des transactions clôturées (vente d'une position) — pas de
  concept de "position fermée" en v1, uniquement ce qui est détenu
  aujourd'hui.

## Modèle de données — `localStorage`

Clé `analyse-or-portfolio`, valeur JSON : tableau de positions.

```json
[
  {
    "id": "pos-2026-09-10-a1b2c3",
    "ticker": "MC.PA",
    "quantity": 10,
    "buy_price": 650.0,
    "buy_date": "2026-08-01"
  }
]
```

- `id` : généré côté client à la création (horodatage + suffixe
  aléatoire court), sert de clé stable pour modifier/supprimer une ligne
  précise même si deux lots partagent le même ticker.
- Volontairement minimal : pas de `name`/`index`/`currency` stockés —
  ces informations sont retrouvées à chaque rendu depuis
  `indicesData.companies` par `ticker`, pour ne jamais avoir une copie
  périmée (nom, indice d'appartenance) qui diverge de la donnée réelle.
- `quantity`/`buy_price` : nombres positifs, validés à la saisie (voir
  Cas limites).

## Logique côté client

Nouveau fichier `docs/portfolio.js` (JS pur, pas de framework — même
convention que `docs/scalping.js`), chargé à la demande au premier
passage sur l'onglet Portefeuille (même mécanisme que
`loadScalpingModule`).

Fonctions principales :
- `loadPortfolio()` / `savePortfolio(positions)` : lecture/écriture
  `localStorage`, dégradent vers `[]` si absent ou JSON invalide (voir
  Cas limites) plutôt que de lever une exception.
- `addPosition(ticker, quantity, buyPrice, buyDate)` : valide les
  entrées, génère un `id`, ajoute et sauvegarde.
- `updatePosition(id, fields)` / `removePosition(id)`.
- `computePositionPnL(position, currentPrice)` → `{value, pnlAbs,
  pnlPct}` — achat uniquement (pas de vente à découvert), donc
  `pnlAbs = (currentPrice - buy_price) * quantity`.
- `computePortfolioTotals(positions, companiesByTicker)` → deux totaux
  séparés `{EUR: {value, pnlAbs}, USD: {value, pnlAbs}}`, calculés à
  partir de `company.index` → devise via `INDEX_CURRENCY_BY_KEY`/
  `indicesData.index_currency` (déjà utilisé ailleurs dans le fichier).
- `findOpportunities(companies, heldTickers)` → entreprises avec une
  alerte `kind === 'entree'` active (voir `hasMajorNewsAlert` pour le
  même genre de filtre sur `company.alerts`) dont le ticker n'est pas
  dans `heldTickers`.
- Fonctions de rendu (`renderPortfolio`, `renderPositionForm`,
  `renderOpportunities`) suivant le même style que les fonctions
  `render*` déjà présentes dans `docs/index.html` (template literals,
  pas de framework).

## Cas limites

- `localStorage` absent, vide, ou JSON invalide au chargement → repart
  d'un portefeuille vide (`[]`), jamais d'exception qui casserait
  l'onglet.
- `localStorage` indisponible à l'écriture (navigation privée stricte,
  quota dépassé) → l'ajout/la modification échoue silencieusement côté
  stockage mais ne doit pas planter l'interface ; un message discret
  signale que la sauvegarde n'a pas pu être faite.
- Position dont le ticker n'est plus dans `indicesData.companies` (rare,
  sorti de l'indice suivi) : ligne affichée en dégradé (nom/score/cours
  indisponibles, "Cette entreprise n'est plus suivie") plutôt que
  masquée ou plantée — la position reste éditable/supprimable.
- Formulaire : quantité ou prix d'achat non numérique, négatif, ou nul →
  rejeté avant sauvegarde, message d'erreur inline, pas de position
  invalide enregistrée.
- `indices.json` pas encore chargé quand l'onglet Portefeuille s'ouvre
  en premier → état "Chargement…", le rendu réel attend les données
  (même pattern que le chargement initial des autres onglets).

## Frontend / UI

Nouvel onglet **"Portefeuille"**, même niveau que "Or"/"Indices" :
- `nav.top-bar` (desktop) et `nav.drawer` (mobile) : nouvelle entrée
  `data-route="portefeuille"`, même structure `<button class="nav-item">`
  que les entrées existantes.
- Carte de module sur l'écran d'accueil (`.home-modules`), à côté de
  "Or"/"Indices"/"Minières".
- Écran `#portefeuille` (nouveau), suit le gabarit `header` +
  `main`/skeleton déjà utilisé par `#or`/`#indices`.
- Contenu : formulaire d'ajout en tête, totaux EUR/USD, liste des
  positions (cliquable vers la fiche entreprise via
  `#indices/{ticker}`, réutilise le routage existant), section
  "Opportunités" en bas.

## Tests

`docs/portfolio.test.js` (Node, sans framework — même convention que
`docs/scalping.test.js`), en suivant la densité de tests déjà en place :
- CRUD : ajout, modification, suppression d'une position ; deux lots
  distincts sur le même ticker restent indépendants.
- Validation formulaire : quantité/prix négatifs, nuls, ou non
  numériques rejetés.
- P&L : calcul correct value/pnlAbs/pnlPct pour un gain et une perte.
- Totaux : séparation correcte EUR/USD, positions des deux devises
  mélangées dans le même portefeuille.
- Opportunités : détecte les entreprises avec alerte `entree` active non
  détenues, exclut celles déjà détenues même partiellement.
- Dégradation : `localStorage` absent/corrompu → portefeuille vide sans
  exception ; ticker disparu de `indicesData.companies` → ligne
  dégradée sans exception.
