# Portefeuille — synchronisation MetaTrader 5 / Vantage (remplace la phase 2a IBKR) — design

## Contexte et objectif

La phase 2a du Portefeuille (`docs/superpowers/specs/2026-09-10-portefeuille-ibkr-sync-design.md`,
mergée dans `main` via PR #7) synchronisait les positions réelles depuis
Interactive Brokers via le Flex Web Service. En pratique, le Flex Web
Service est resté bloqué (`"Statement generation in progress"`) sur
plusieurs tentatives étalées sur plus d'une heure pour un token/compte
IBKR tout juste créés, sans qu'aucune documentation officielle ne
garantisse un délai d'activation précis.

L'utilisateur a un compte **MT5 déjà ouvert chez Vantage** et connaît
bien cette plateforme. Cette nouvelle phase **remplace complètement**
l'affichage des positions réelles IBKR par une synchronisation MT5/Vantage,
via le service tiers **MetaApi.cloud** (seule solution qui reste compatible
avec le modèle "site statique + cron GitHub Actions", MT5 n'ayant pas
d'équivalent au Flex Web Service d'IBKR — voir Mécanisme ci-dessous).

Le code IBKR (`portfolio_sync.py`, `.github/workflows/portfolio_sync.yml`,
`docs/real_portfolio.json`, sa spec et son plan) **reste dans le dépôt,
inutilisé** — son cron sera désactivé (voir Portée) plutôt que le code
supprimé, au cas où IBKR redevienne pertinent un jour, sans qu'une
réutilisation soit considérée probable à ce stade.

## Portée

**Dans le périmètre :**
- Nouveau script `portfolio_sync_mt5.py`, exécuté quotidiennement (cron
  GitHub Actions, même modèle que `portfolio_sync.py`/`indices_score.py`),
  qui interroge l'API REST MetaApi et écrit `docs/real_portfolio_mt5.json`.
- Nouveau fichier `.github/workflows/portfolio_sync_mt5.yml`, cron
  quotidien (voir Logique pour l'horaire).
- Le workflow `portfolio_sync.yml` (IBKR) est **désactivé** — son
  déclenchement `schedule:` est retiré, seul `workflow_dispatch:` reste
  (permet de le relancer manuellement plus tard si besoin, sans qu'il
  tourne tous les jours pour rien).
- Affichage sur le site : la section "Positions réelles (IBKR)"
  existante dans l'onglet Portefeuille est **remplacée** par "Positions
  réelles (MT5)", qui lit `docs/real_portfolio_mt5.json` au lieu de
  `docs/real_portfolio.json`. Séparée des positions manuelles de la
  phase 1 (aucune fusion), comme l'était la section IBKR.
- Dégradation propre si la synchronisation échoue : le site garde la
  dernière donnée connue et signale l'échec, plutôt que d'effacer les
  positions ou de planter — même principe qu'IBKR.
- **Aucun pourcentage de performance ni montant, absolu ou relatif,
  n'est affiché** (voir Confidentialité) — seulement le symbole, le
  sens (achat/vente), et un indicateur visuel gain/perte sans chiffre.

**Hors périmètre :**
- Toute exécution d'ordre, réelle ou simulée — le compte MetaApi est
  connecté avec le **mot de passe investisseur** du compte MT5, qui
  interdit structurellement le passage d'ordres au niveau du protocole
  MT5 lui-même (pas seulement une politique MetaApi qu'on choisit de ne
  pas utiliser).
- Rapprochement avec les entreprises suivies dans `indices.json` — les
  symboles tradés via MT5/Vantage (paires forex, métaux, indices CFD)
  ne correspondent pas au format des tickers actions suivis ; aucune
  tentative de rapprochement en v1 (voir Rapprochement).
- Suppression du code IBKR existant (voir Contexte) — seul son cron
  automatique est désactivé.
- Historique des transactions MT5 — uniquement les positions ouvertes
  actuelles, comme pour IBKR.
- Autres comptes MT5/courtiers que le compte Vantage déjà connecté à
  MetaApi.
- Rafraîchissement en temps réel — cadence périodique uniquement.

## Mécanisme : MetaApi.cloud (Client REST API)

MT5 est un logiciel de trading utilisé par de nombreux courtiers
différents — contrairement à IBKR, il n'existe pas d'API HTTPS
officielle et directe équivalente au Flex Web Service. **MetaApi.cloud**
est un service tiers qui maintient une connexion permanente vers le
serveur MT5 du courtier (ici Vantage) en coulisses, et expose cette
donnée via une API REST classique — ce qui permet à notre script,
lancé ponctuellement par le cron GitHub Actions, de faire un simple
appel HTTPS sans avoir à maintenir de connexion permanente de notre
côté.

**Garantie de lecture seule :** le compte MT5 est enregistré chez
MetaApi avec le **mot de passe investisseur** (`investor password`),
un type d'identifiant propre au protocole MT5 qui donne un accès en
lecture seule au terminal — distinct du mot de passe de trading. Un
compte connecté avec ce mot de passe est structurellement incapable de
passer un ordre, quel que soit le service qui l'interroge. Cette
garantie est **plus forte** que celle d'IBKR car elle vient du
protocole MT5 lui-même, pas d'une politique du service tiers.

**Coût :** contrairement à IBKR (gratuit), MetaApi facture à l'heure
tant que le compte reste connecté/déployé sur leur infrastructure —
environ **0,0126 $/heure** avec la configuration choisie (usage "Forex
API", plateforme MT5, région `london`, fiabilité "High"), soit environ
**9 $/mois**. Facturation continue indépendante de la fréquence
d'interrogation (une fois par jour ou mille fois par jour, même coût).
Accepté explicitement par l'utilisateur.

**Étapes de configuration côté utilisateur (déjà faites, hors de ce
dépôt, dans l'espace MetaApi.cloud) :**
1. Créer un compte sur metaapi.cloud.
2. Ajouter le compte MT5 Vantage via "MT Accounts" → "Add MetaTrader
   account" : use case "Forex API", plateforme MT5, identifiants du
   compte MT5 (numéro de compte, mot de passe **investisseur**, nom du
   serveur Vantage).
3. Générer un token d'API via "API Access".
4. Noter l'**Account ID** MetaApi de ce compte (visible sur sa page de
   détails dans "MT Accounts" — différent du numéro de compte MT5).
5. Ajouter au dépôt GitHub deux secrets Actions : `METAAPI_TOKEN` et
   `METAAPI_ACCOUNT_ID`.

**Appel API (une étape, JSON, contrairement au flux en deux étapes
d'IBKR) :**
```
GET https://mt-client-api-v1.{region}.agiliumtrade.ai/users/current/accounts/{accountId}/positions
Header: auth-token: {token}
```
où `{region}` est la région choisie à la configuration du compte
(`london` dans notre cas) — **l'URL exacte pour cette région doit être
confirmée depuis la page "API Access" du compte MetaApi avant
l'implémentation**, le format `mt-client-api-v1.<region>.agiliumtrade.ai`
étant documenté par MetaApi mais non garanti stable indéfiniment.
Renvoie un tableau JSON de positions (voir Modèle de données pour les
champs utilisés) ; `200 OK` avec `[]` si aucune position ouverte ;
`401` si le token est invalide.

Contrairement à IBKR, **aucune génération asynchrone à attendre** — la
réponse est immédiate, pas de logique de nouvelle tentative nécessaire
pour un "rapport en cours de génération".

## Modèle de données — `docs/real_portfolio_mt5.json`

```json
{
  "updated": "2026-09-10T18:00:00Z",
  "sync_status": "ok",
  "sync_error": null,
  "positions": [
    {
      "symbol": "XAUUSD",
      "type": "achat",
      "pnl_sign": "positif"
    }
  ]
}
```

- `sync_status` ∈ `"ok" | "error" | "not_configured"` — mêmes
  définitions que pour IBKR (`"not_configured"` : secrets absents,
  distinct de `"error"` pour ne pas afficher de bandeau rouge sur le
  site public tant que la configuration est simplement en attente).
  `sync_error` : message d'erreur lisible si `"error"`, sinon `null`.
- **Aucun montant, ni absolu ni en pourcentage, n'est jamais publié**
  (voir Confidentialité) : `type` traduit `POSITION_TYPE_BUY`/
  `POSITION_TYPE_SELL` en `"achat"`/`"vente"` ; `pnl_sign` traduit le
  signe du champ `profit` renvoyé par MetaApi (`"positif"` si
  `profit >= 0`, sinon `"négatif"`) — jamais la valeur `profit`
  elle-même.
- Pas de `matched_ticker` (voir Rapprochement — hors périmètre pour
  cette phase).
- En cas d'échec de synchronisation (`sync_status: "error"`),
  `positions` garde son contenu précédent, comme pour IBKR.

## Rapprochement avec les tickers suivis

**Aucun** en v1 (voir Portée — Hors périmètre). Les symboles MT5/Vantage
(`XAUUSD`, `EURUSD`, `US30`, `USTEC`, etc.) ne suivent pas le format
des tickers Yahoo Finance utilisés dans `indices.json`. Si l'utilisateur
trade un jour des CFD sur actions individuelles chez Vantage avec des
symboles reconnaissables, un rapprochement pourra être ajouté dans une
phase ultérieure — non traité ici (YAGNI).

## Logique du script `portfolio_sync_mt5.py`

Nouveau fichier, structure similaire à `portfolio_sync.py` (fonctions
pures testables + un `main()` fin) :

1. `fetch_positions(token, account_id, base_url) -> list[dict]` :
   appelle l'endpoint `GET .../positions` avec le header `auth-token`,
   renvoie le JSON parsé (liste de positions brutes MetaApi). Lève une
   exception (`requests.exceptions.RequestException`) sur toute erreur
   HTTP (401 token invalide, 404 compte introuvable, etc.) — même
   traitement que `requests.exceptions.RequestException` dans
   `portfolio_sync.py` (message assaini avant persistance, voir
   Confidentialité et le point déjà tranché pour IBKR : ne jamais
   stocker `str(exception)` brut, qui pourrait inclure le token en
   query param ou header selon la bibliothèque).
2. `to_public_positions(raw_positions) -> list[dict]` : transforme
   chaque position brute MetaApi en `{symbol, type, pnl_sign}` du
   modèle de données ci-dessus — ne construit **jamais** `profit`,
   `volume`, `openPrice`, `currentPrice`, ou tout autre champ
   numérique dans le dict renvoyé (même garantie "jamais matérialisé"
   que `parse_open_positions` pour IBKR).
3. `main()` : enchaîne 1 et 2. Secrets absents →
   `sync_status: "not_configured"`. Échec réseau/HTTP →
   `sync_status: "error"` avec message assaini, `positions` inchangé.
   Succès → `sync_status: "ok"`, `positions` mis à jour. Réutilise
   `_write_real_portfolio`-équivalent avec `allow_nan=False` (même
   protection qu'IBKR, bien que moins critique ici vu l'absence de
   champs numériques persistés).

**Cadence :** cron quotidien, décalé des deux autres crons existants
(`indices.yml` à 7h UTC, l'ancien `portfolio_sync.yml` IBKR
désactivé) — proposé à **8h UTC** (`0 8 * * *`).

**Dépendances :** aucune nouvelle — `requests` (déjà utilisé) suffit,
pas besoin de `xml.etree.ElementTree` (réponse JSON, pas XML).

## Cas limites

- **Aucune position ouverte chez Vantage** : `positions: []`,
  `sync_status: "ok"` — état valide, pas une erreur.
- **Token ou Account ID MetaApi invalide/absent** : `sync_status:
  "not_configured"` si les deux secrets sont absents, `"error"` si
  présents mais rejetés (401) par MetaApi.
- **Compte MetaApi pas encore complètement déployé côté leur
  infrastructure** (immédiatement après sa création) : traité comme une
  erreur HTTP classique (probablement 404 ou 400 selon leur API),
  `sync_status: "error"`, positions précédentes conservées — le prochain
  run cron réessaiera automatiquement le lendemain.
- **`docs/real_portfolio_mt5.json` absent au premier run** : le script
  part d'un état vide (`positions: []`), comme pour IBKR.

## Frontend

La section "Positions réelles (IBKR)" existante dans `docs/index.html`
(fonction `realPortfolioHtml`, variable `realPortfolioData`) est
**remplacée** par une section "Positions réelles (MT5)" :
- Fetch `real_portfolio_mt5.json` au lieu de `real_portfolio.json`
  (même pattern paresseux que le reste de l'écran Portefeuille).
- Si `sync_status === 'error'` : bandeau d'avertissement, comme IBKR.
- Si `sync_status === 'not_configured'` : message neutre, comme IBKR.
- Chaque position : symbole, sens (Achat/Vente), et un simple
  indicateur visuel coloré (vert si `pnl_sign === 'positif'`, rouge
  sinon) — **aucun chiffre affiché**, lecture seule (pas de bouton
  modifier/supprimer).
- Pas de rapprochement, donc pas de badges/score/lien vers une fiche
  entreprise pour cette section.

## Confidentialité

Même contrainte qu'IBKR (voir
`docs/superpowers/specs/2026-09-10-portefeuille-ibkr-sync-design.md` —
section Confidentialité) : `docs/real_portfolio_mt5.json` est publié
sur le même dépôt public, et le site GitHub Pages reste public quel
que soit le plan GitHub. Ici la contrainte va **plus loin** qu'IBKR :
non seulement aucun montant absolu, mais **aucun pourcentage non plus**
(voir Section 2 du brainstorm — MT5/CFD n'a pas de notion propre de
"prix de revient" permettant un calcul de performance fiable et non
trompeur compte tenu de l'effet de levier). Seuls le symbole tradé, le
sens de la position, et le signe du résultat sont publiés.

## Tests

`tests/test_portfolio_sync_mt5.py` (pytest, même densité que
`tests/test_portfolio_sync.py`) :
- `fetch_positions` : succès (renvoie la liste JSON parsée) ; échec
  HTTP (401) lève `requests.exceptions.HTTPError`.
- `to_public_positions` : transforme correctement `POSITION_TYPE_BUY`/
  `POSITION_TYPE_SELL` en `"achat"`/`"vente"` ; transforme le signe de
  `profit` (positif, négatif, et le cas limite `profit == 0`) en
  `pnl_sign` ; **ne construit jamais** `profit`/`volume`/`openPrice`/
  `currentPrice` dans le dict renvoyé (test de garantie de
  confidentialité, même esprit que
  `test_parse_open_positions_never_includes_absolute_amounts` pour
  IBKR).
- `main()` (avec mocks) : succès → fichier écrit avec `sync_status:
  "ok"` ; secrets absents → `"not_configured"` ; échec réseau →
  `"error"` avec message assaini (jamais le token brut), positions
  inchangées ; fichier absent au démarrage → part de `positions: []`.
