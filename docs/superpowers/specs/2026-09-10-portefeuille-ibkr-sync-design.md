# Portefeuille — synchronisation Interactive Brokers (phase 2a) — design

## Contexte et objectif

La phase 1 du Portefeuille (`docs/superpowers/specs/2026-09-10-portefeuille-design.md`,
PR #7) ne suit que des positions saisies manuellement. L'objectif final
évoqué par l'utilisateur — recevoir un signal, valider d'un oui/non, que
le site passe l'ordre chez un courtier — a été explicitement repoussé à
une phase 2 séparée, car elle nécessite un serveur et porte un vrai
risque financier.

Cette phase 2a est une étape intermédiaire, volontairement plus modeste :
**afficher les positions réellement détenues chez Interactive Brokers
(IBKR), automatiquement, sans exécuter le moindre ordre.** Le mécanisme
retenu (Flex Query, voir plus bas) est **structurellement incapable de
passer un ordre** — ce n'est pas une permission qu'on choisit de ne pas
utiliser, c'est un type de jeton IBKR qui ne donne accès qu'à des
rapports en lecture seule. Le risque financier direct (un bug qui
achète/vend à tort) n'existe donc pas dans cette phase.

## Portée

**Dans le périmètre :**
- Nouveau script `portfolio_sync.py`, exécuté périodiquement (cron
  GitHub Actions, même modèle que `indices_score.py`/`indices.yml`),
  qui interroge le Flex Web Service IBKR et écrit `docs/real_portfolio.json`.
- Affichage sur le site : nouvelle section "Positions réelles (IBKR)"
  dans l'onglet Portefeuille, **séparée** des positions manuelles de la
  phase 1 (aucune fusion, aucun risque de doublon/conflit).
- Toutes les positions du compte sont affichées, pas seulement celles
  qui correspondent aux ~180 entreprises déjà suivies — le but est de
  voir son vrai portefeuille, pas une vue filtrée. Les positions
  reconnues (ticker rapproché d'une entreprise suivie) profitent en
  plus du score/des alertes/des badges déjà calculés ; les autres
  s'affichent avec seulement ce qu'IBKR fournit.
- Dégradation propre si la synchronisation échoue (jeton expiré,
  IBKR indisponible, etc.) : le site garde la dernière donnée connue et
  signale que la synchronisation a échoué, plutôt que d'effacer les
  positions ou de planter.

**Hors périmètre (phase 2a) :**
- Toute exécution d'ordre, réelle ou simulée — pas de bouton "acheter",
  pas de connexion à une API de trading. Reporté à une phase
  ultérieure, explicitement.
- Fusion avec les positions manuelles de la phase 1.
- Historique des transactions IBKR (achats/ventes passés) — uniquement
  l'état actuel des positions ouvertes.
- Autres courtiers que IBKR.
- Rafraîchissement en temps réel ou à la demande — cadence périodique
  uniquement (voir Logique).

## Confidentialité

`docs/real_portfolio.json` est publié sur un dépôt **public**, et le site
GitHub Pages qui le sert reste public quel que soit le plan GitHub utilisé
sur un compte personnel (un repo privé + GitHub Pro cache le code source et
l'historique, mais pas le site publié lui-même — une confidentialité réelle
du site nécessiterait GitHub Enterprise Cloud, une offre organisationnelle
non pertinente ici). Décision explicite, prise avec l'utilisateur : ne
jamais publier de montant absolu (quantité détenue, valeur de position,
coût de revient, plus/moins-value en devise). Seul un pourcentage de
performance (`pnl_pct`) est publié, avec le symbole/nom/devise de la
position et le rapprochement éventuel avec une entreprise suivie. Ce choix
révèle toujours quelles valeurs sont détenues et leur performance relative,
mais jamais la taille réelle du portefeuille — un compromis assumé plutôt
qu'un défaut non examiné.

## Mécanisme IBKR : Flex Query / Flex Web Service

IBKR permet de configurer, dans son espace de gestion de compte
("Reports" → "Flex Queries"), un rapport ("Activity Flex Query")
définissant les champs souhaités — ici, la section "Open Positions"
suffit. Ce rapport a un identifiant (`Query ID`). Séparément, un
"Flex Web Service Token" se génère dans les paramètres du compte : il
donne accès en lecture seule aux rapports Flex configurés, avec une
date d'expiration que l'utilisateur choisit à la génération (ne peut
pas passer d'ordre, ne donne accès à rien d'autre que les rapports).

**Étapes de configuration côté utilisateur (à faire une fois, hors de ce
dépôt, dans l'espace de gestion de compte IBKR) :**
1. Créer une Flex Query "Open Positions" avec au minimum les champs :
   Symbol, Description, Currency, Position, MarkPrice, PositionValue,
   CostBasisPrice, CostBasisMoney, FifoPnlUnrealized.
2. Générer un Flex Web Service Token.
3. Ajouter au dépôt GitHub deux secrets Actions : `IBKR_FLEX_TOKEN` et
   `IBKR_FLEX_QUERY_ID` (même mécanisme que `TWELVE_DATA_API_KEY`/
   `FRED` déjà utilisés).

**Appel API (deux étapes, XML) :**
1. `GET https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest?t={token}&q={queryId}&v=3`
   → renvoie un XML avec soit `<ReferenceCode>` (succès), soit
   `<ErrorCode>`/`<ErrorMessage>` (échec — jeton expiré/révoqué, query
   invalide, etc.).
2. `GET https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement?t={token}&q={referenceCode}&v=3`
   → renvoie soit le rapport XML complet, soit un statut "en cours de
   génération" (texte contenant `"Statement generation in progress"`,
   pas un XML bien formé) qu'il faut retenter après une courte pause —
   IBKR peut prendre plusieurs secondes à générer le rapport.

## Modèle de données — `docs/real_portfolio.json`

```json
{
  "updated": "2026-09-10T18:00:00Z",
  "sync_status": "ok",
  "sync_error": null,
  "positions": [
    {
      "ibkr_symbol": "MC",
      "description": "LVMH MOET HENNESSY LOUIS VUI",
      "currency": "EUR",
      "pnl_pct": 8.72,
      "matched_ticker": "MC.PA"
    }
  ]
}
```

- `sync_status` ∈ `"ok" | "error" | "not_configured"`. `"not_configured"` :
  les secrets `IBKR_FLEX_TOKEN`/`IBKR_FLEX_QUERY_ID` ne sont pas encore
  définis (étape attendue avant que l'utilisateur ait fini de configurer
  son compte IBKR) — distinct de `"error"` pour ne pas afficher un
  bandeau d'erreur sur le site public tant que la configuration est
  simplement en attente. `sync_error` : message d'erreur lisible si
  `"error"`, sinon `null`.
- **Aucun montant absolu n'est jamais publié** (voir Confidentialité
  ci-dessus) : ni la quantité détenue, ni la valeur de position, ni le
  coût de revient, ni la plus/moins-value en devise. Seul `pnl_pct`
  (pourcentage de performance, calculé côté script à partir de
  `FifoPnlUnrealized`/`CostBasisMoney` fournis par IBKR) est conservé,
  `null` si le calcul n'est pas possible (coût de revient absent/nul).
- Une ligne IBKR en détail "lot" (`levelOfDetail="LOT"`, si la Flex Query
  est configurée en détail plutôt qu'en résumé) est ignorée — seules les
  lignes `"SUMMARY"` (ou sans l'attribut, comportement par défaut d'IBKR)
  sont retenues, pour ne pas compter une même position plusieurs fois.
- `matched_ticker` : `null` si aucune correspondance trouvée parmi les
  entreprises suivies (voir Rapprochement des tickers), sinon le
  ticker exact tel qu'utilisé dans `indices.json` (ex. `"MC.PA"`),
  permettant au frontend de retrouver score/alertes/badges via
  `indicesData.companies`.
- En cas d'échec de synchronisation (`sync_status: "error"`),
  `positions` garde son contenu précédent (le fichier n'est réécrit
  qu'en cas de succès, sauf pour `sync_status`/`sync_error`/`updated`
  qui reflètent toujours la dernière tentative — voir Logique).

## Rapprochement des tickers

IBKR ne rapporte pas ses positions avec le même format de ticker que ce
dépôt (`indices_score.py` utilise des tickers Yahoo Finance, ex.
`MC.PA`, `AAPL`). Le rapprochement est **best-effort**, sans garantie
de succès pour chaque position :

1. Construire une table `{symbole_nu: ticker_yahoo}` à partir de
   `indices.json` en retirant tout suffixe Yahoo connu (`.PA`, `.DE`)
   des tickers CAC40/DAX, et en gardant tel quel les tickers Nasdaq/Dow
   (déjà sans suffixe).
2. Pour chaque position IBKR, chercher `ibkr_symbol` (insensible à la
   casse) dans cette table. Une correspondance trouvée devient
   `matched_ticker`. Aucune correspondance → `matched_ticker: null`,
   sans erreur ni avertissement bloquant (attendu : IBKR peut contenir
   des ETF, obligations, ou actions hors des indices suivis).
3. Pas de désambiguïsation avancée (ex. un même symbole nu existant
   dans deux indices différents) en v1 — le premier trouvé dans l'ordre
   d'itération de `indices.json` gagne. Cas limite documenté, pas
   traité activement (comme le cas similaire déjà accepté dans le
   design de la phase 1 pour les tickers retirés d'un indice).

## Logique du script `portfolio_sync.py`

Nouveau fichier, structure similaire à `indices_score.py` (fonctions
pures testables + un `main()` fin) :

1. `fetch_flex_reference_code(token, query_id) -> str` : appelle
   `SendRequest`, parse le XML, renvoie le `ReferenceCode`. Lève une
   exception avec le message d'erreur IBKR si `<ErrorCode>` est présent.
2. `fetch_flex_statement(token, reference_code, max_attempts=5,
   retry_delay_s=3) -> str` : appelle `GetStatement` en boucle jusqu'à
   obtenir un XML bien formé (pas le texte "in progress"), ou lève une
   exception après `max_attempts` tentatives.
3. `parse_open_positions(xml_text) -> list[dict]` : extrait chaque
   `<OpenPosition>` en dict avec les clés du modèle de données
   ci-dessus (hors `matched_ticker`, ajouté à l'étape suivante).
4. `match_tickers(positions, companies) -> list[dict]` : applique le
   rapprochement décrit plus haut, ajoute `matched_ticker` à chaque
   position.
5. `main()` : enchaîne 1 à 4. Si une étape échoue (réseau, jeton
   expiré, XML invalide) : charge le `docs/real_portfolio.json`
   existant (ou un état vide s'il n'existe pas encore), le réécrit
   avec `sync_status: "error"`, `sync_error: <message>`,
   `updated: <maintenant>`, en gardant `positions` inchangé — jamais
   d'exception qui ferait échouer le run silencieusement sans laisser
   de trace. En cas de succès : `sync_status: "ok"`, `sync_error: null`,
   `positions` mis à jour.

**Cadence :** cron quotidien, même rythme que `indices.yml`
(`0 7 * * *`, décalé d'une heure pour ne pas cumuler avec le cron
Indices) — cohérent avec l'objectif "simple visualisation" de cette
phase, pas de besoin de fraîcheur à la minute.

**Dépendances :** aucune nouvelle — `xml.etree.ElementTree` (stdlib) et
`requests` (déjà utilisé dans `indices_score.py`) suffisent.

## Cas limites

- **Jeton Flex expiré ou révoqué** : `SendRequest` renvoie une erreur
  IBKR explicite → `sync_status: "error"` avec ce message, positions
  précédentes conservées. Le frontend affiche un bandeau "Dernière
  synchronisation IBKR échouée" au-dessus des positions (potentiellement
  périmées).
- **Rapport toujours "en cours de génération" après `max_attempts`** :
  traité comme un échec de cette tentative (le prochain run cron
  réessaiera depuis le début).
- **Aucune position ouverte chez IBKR** (compte vide) : `positions: []`,
  `sync_status: "ok"` — état valide, pas une erreur.
- **Position dont le symbole ne correspond à aucune entreprise suivie**
  (ETF, obligation, action hors périmètre) : affichée avec
  `matched_ticker: null`, sans score/alertes/badges — juste les
  données publiques IBKR (nom, devise, pourcentage de performance).
- **`docs/real_portfolio.json` absent au premier run** : le script part
  d'un état vide (`positions: []`) plutôt que de lever une exception en
  tentant de le lire.

## Frontend

Nouvelle section "Positions réelles (IBKR)" dans l'écran `#portefeuille`
existant (sous la section des positions manuelles de la phase 1, avant
"Opportunités") :
- Chargée en même temps que le reste de l'écran (`fetch('real_portfolio.json')`,
  même pattern paresseux que `indices.json`).
- Si `sync_status === 'error'` : bandeau d'avertissement avec
  `sync_error`, au-dessus des positions (affichées quand même si
  `positions` contient une donnée précédente valide).
- Chaque position : nom/symbole IBKR, pourcentage de performance
  (`pnl_pct`, coloré vert/rouge comme l'existant, "indisponible" si
  `null`) — **lecture seule, aucun bouton modifier/supprimer** (ce sont
  des données synchronisées, pas saisies). Si `matched_ticker` n'est pas
  `null` : badges d'indice + score + lien vers la fiche entreprise, en
  réutilisant `allIndicesBadgesHtml`/le routage `#indices/{ticker}`
  déjà en place.
- Pas de total agrégé par devise sur cette section (contrairement à la
  phase 1) : sans montant absolu publié, un total chiffré n'a pas de
  sens à afficher.

## Tests

`tests/test_portfolio_sync.py` (pytest, même densité que
`tests/test_indices_score.py`) :
- `fetch_flex_reference_code` : succès (extrait le `ReferenceCode`) ;
  échec (lève avec le message IBKR quand `<ErrorCode>` est présent).
- `fetch_flex_statement` : succès immédiat ; retente sur "in progress"
  puis réussit ; échoue après `max_attempts` tentatives.
- `parse_open_positions` : extrait correctement les champs d'un XML
  d'exemple avec plusieurs positions ; gère une section vide (`[]`).
- `match_tickers` : trouve une correspondance CAC40 (suffixe `.PA`
  retiré) et une correspondance Nasdaq (pas de suffixe) ; renvoie
  `matched_ticker: None` pour un symbole absent d'`indices.json`.
- `main()` (avec mocks) : succès → fichier écrit avec `sync_status: "ok"` ;
  échec réseau → fichier réécrit avec `sync_status: "error"` et
  `positions` inchangé par rapport à l'état précédent chargé ; fichier
  absent au démarrage → part de `positions: []` sans exception.
