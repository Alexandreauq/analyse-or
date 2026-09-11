# Onglet "Bot" — tableau de bord du bot de trading or — design

## Contexte et objectif

Le bot de trading or (`gold_bot/`, voir
`docs/superpowers/specs/2026-09-10-bot-trading-or-design.md`) tourne
depuis le 11/09/2026 sur le VPS `goldbot.fr`, en mode simulation
(`dry_run`). L'onglet Portefeuille affiche déjà un bloc "Bot Or" minimal
(statut simulation/actif/coupé + bouton Arrêter/Relancer, protégé par
jeton). L'utilisateur veut un onglet dédié, plus complet : les positions
du bot, un graphique du cours avec les points d'entrée/sortie, et le
montant disponible du portefeuille.

Le site (`docs/index.html`) est **statique et public** (GitHub Pages,
sans authentification). Le montant réel et les positions du bot n'ont
jamais été exposés publiquement à ce jour — même la synchro MT5 en
lecture seule existante (`docs/real_portfolio_mt5.json`) masque
volontairement tous les chiffres (voir sa spec, section
Confidentialité). Ce nouvel onglet **change de posture** : il affiche
des montants réels, mais seulement pour l'utilisateur authentifié,
jamais publiquement — décision validée explicitement par l'utilisateur
avant ce document.

## Portée

**Dans le périmètre :**
- Nouvelle route `GET /dashboard` sur `gold_bot/api.py`, protégée par
  le même jeton `X-Bot-Token` que `/kill`/`/resume` — renvoie solde,
  positions ouvertes, et historique récent des décisions du bot.
- Le solde et les positions sont toujours interrogés en direct auprès
  de MetaApi (jamais mis en cache), le graphique de prix réutilise le
  dernier lot de bougies déjà récupéré par le cycle courant du bot
  (voir Mécanisme — aucun appel Twelve Data supplémentaire).
- Nouvel onglet "Bot" dans `docs/index.html`, au même niveau que
  Indices/Or/Portefeuille : montant disponible, liste des positions
  ouvertes (avec chiffres, contrairement à la synchro MT5 publique),
  graphique du cours XAUUSD avec marqueurs d'entrée/sortie (décisions
  réelles une fois en mode réel, simulées tant que `dry_run` est actif).
- Authentification réutilisant le mécanisme déjà en place
  (`getBotToken()`, jeton stocké dans `localStorage` côté navigateur) —
  aucun nouveau système d'auth.
- Dégradation propre : MetaApi indisponible (déjà observé : `504
  Gateway Timeout` intermittents), jeton absent/invalide, ou aucune
  donnée de bougies encore mise en cache (bot tout juste redémarré) —
  chaque cas affiche un état "indisponible" ciblé, jamais une page
  cassée.

**Hors périmètre :**
- Historique long terme au-delà de ce que journalise déjà
  `decisions_log.jsonl` — pas de base de données, pas d'agrégation
  côté serveur.
- Graphique interactif avancé (zoom, pan, bibliothèque de charting
  tierce) — un tracé SVG simple et statique, cohérent avec le reste du
  site qui n'utilise aucune dépendance de charting locale.
- Rafraîchissement temps réel (WebSocket, polling automatique) — la
  donnée se recharge à l'ouverture de l'onglet et sur clic d'un bouton
  "Actualiser", comme les autres onglets du site.
- Toute action d'écriture au-delà de celles qui existent déjà
  (Arrêter/Relancer) — `/dashboard` est strictement un point de
  lecture, aucune nouvelle route de mutation.
- Publication d'un fichier public équivalent — cette donnée ne
  transite jamais par un fichier commité dans le dépôt, uniquement par
  l'appel direct et protégé au VPS.

## Mécanisme

### Cache des bougies (`gold_bot/loop.py`)

Le bot appelle déjà `confluence.fetch_gold_candles(twelve_data_api_key)`
à chaque cycle (~1×/min). Pour que le graphique n'ajoute **aucune**
requête Twelve Data supplémentaire (la clé dédiée du bot tourne déjà
près de sa limite quotidienne, voir Contexte de la spec Plan B), le
loop écrit ce même lot de bougies dans un fichier de cache local après
chaque récupération réussie :

- Fichier : `gold_bot/latest_candles.json`, même dossier que
  `state.py`/`decisions_log.jsonl`.
- Écriture atomique (fichier temporaire + `os.replace`), même pattern
  que `state.save_state` — jamais de fichier tronqué si le processus
  est interrompu en cours d'écriture.
- Contenu : `{"candles": [...], "fetched_at": "<ISO 8601 UTC>"}` — le
  format brut renvoyé par `fetch_gold_candles` (mêmes clés que celles
  déjà consommées par `confluence.compute_signal`), pas de
  transformation.
- Écrit uniquement en cas de succès de la récupération ; si le cycle
  échoue avant d'atteindre cette étape, le fichier précédent reste en
  place tel quel (donnée légèrement périodique plutôt qu'absente).

### Route protégée `GET /dashboard` (`gold_bot/api.py`)

```python
@app.get("/dashboard")
def dashboard(x_bot_token: str | None = Header(default=None)):
    _check_token(x_bot_token)
    ...
```

Réutilise `_check_token` tel quel (même comparaison à temps constant,
même échec fermé si `BOT_API_TOKEN` absent). Le docstring en tête de
fichier doit être ajusté : la garantie "aucune route ne peut déclencher
un ordre" reste intacte (`/dashboard` ne fait que lire), mais la phrase
"la SEULE action possible... est d'arrêter le bot" devient trompeuse
une fois `/dashboard` ajoutée — à reformuler en distinguant explicitement
les routes de lecture (`/status`, `/dashboard`) des routes de mutation
(`/kill`, `/resume`, seules routes qui changent un état, et seul
`kill_switch` peut être modifié — jamais `dry_run`).

Trois sources de données, chacune tolérante à l'échec indépendamment
des deux autres (une panne MetaApi ne doit pas empêcher d'afficher les
décisions déjà journalisées, et vice-versa) :

1. **Solde** — `broker.get_account_balance(token, account_id)`
   (`METAAPI_TRADE_TOKEN`/`METAAPI_TRADE_ACCOUNT_ID`, déjà dans
   `.env`). En cas d'échec (ex. `504` déjà observés en production),
   capturer l'exception et renvoyer `"balance": null, "balance_error":
   "<message assaini>"` plutôt que de faire échouer toute la réponse.
2. **Positions** — `broker.get_open_positions(token, account_id)`,
   transformées en dicts publics-mais-privés (le point d'accès est
   protégé, donc les chiffres bruts sont acceptables ici,
   contrairement à `portfolio_sync_mt5.to_public_positions` qui les
   masque pour un fichier public) : `{symbol, direction ("achat"/
   "vente", même traduction que to_public_positions), volume,
   open_price, current_price, profit, stop_loss, take_profit}`. Même
   traitement d'erreur indépendant que le solde.
3. **Bougies + décisions récentes** — lit `latest_candles.json` (voir
   ci-dessus ; absent ou périmé de plus de
   `SCALP_STALE_THRESHOLD_MS`-équivalent → `"candles": null`) et les
   `N=50` dernières lignes non triviales de `decisions_log.jsonl`
   (`action` dans `{"simulation_dry_run", "exécuté"}` — les
   `"aucune"`/`"erreur"`/`"ignore"` sont exclues, elles n'ont pas
   d'entrée/sortie à marquer). Pour chaque ligne retenue, seuls les
   `steps` de type `"ouverture_simulee"` sont utilisés pour les
   marqueurs (entrée/sortie) — `bot.decide_and_act` n'émet que
   `"ouverture_simulee"`/`"clôture_simulee"` comme types de step,
   **y compris une fois en mode réel** (nom hérité du Plan A, non
   renommé en Plan B ; ne pas s'y fier pour distinguer simulé/réel,
   c'est le champ `action` de la ligne — `"simulation_dry_run"` vs
   `"exécuté"` — qui porte cette information). Fichier absent ou
   illisible → liste vide, jamais d'exception (même convention que
   `notify.read_todays_decisions`).

Réponse `200 OK` (toujours 200 si le jeton est valide — les pannes
partielles sont dans le corps, pas dans le code HTTP, pour que le
frontend affiche ce qui est disponible plutôt que de tout perdre sur
une erreur MetaApi) :

```json
{
  "balance": 9140.10,
  "balance_error": null,
  "positions": [
    {"symbol": "XAUUSD", "direction": "vente", "volume": 2.0,
     "open_price": 4316.28, "current_price": 4316.49,
     "profit": -36.18, "stop_loss": null, "take_profit": null}
  ],
  "positions_error": null,
  "candles": [{"time": "2026-09-11 16:40:00", "open": 3651.2, "high": 3652.0,
               "low": 3650.8, "close": 3651.5}, ...],
  "candles_fetched_at": "2026-09-11T16:40:43Z",
  "recent_decisions": [
    {"timestamp": "2026-09-11T14:22:03Z", "type": "ouverture_simulee",
     "symbol": "XAUUSD", "direction": "achat", "entry": 3648.2,
     "stop_loss": 3644.0, "take_profit": 3656.0}
  ]
}
```

`401` si le jeton est absent/invalide (identique à `/kill`/`/resume`,
`_check_token` inchangée).

## Frontend (`docs/index.html`)

- Nouvel onglet "Bot" au même niveau que Indices/Or/Portefeuille
  (même mécanisme de routage par hash déjà utilisé, ex. `#bot`).
- Au chargement de l'onglet : `getBotToken()` (fonction existante,
  inchangée) — si aucun jeton en `localStorage`, prompt comme pour
  Arrêter/Relancer aujourd'hui. Si l'appel `/dashboard` renvoie `401`
  (jeton invalide/expiré), effacer le jeton stocké et re-proposer le
  prompt au prochain essai (aujourd'hui un jeton invalide resterait
  silencieusement stocké et jamais re-demandé).
- Un bouton "Actualiser" recharge `/dashboard` à la demande (pas de
  polling automatique, cf. Hors périmètre).
- **Montant disponible** : affiché seulement après authentification
  réussie — jamais en dur, jamais mis en cache dans le HTML/JS servi
  publiquement.
- **Positions ouvertes** : liste/tableau, réutilise le style visuel
  déjà établi (`portfolio-position-row` etc.), mais **avec** les
  chiffres cette fois (volume, prix, profit) — contrairement au bloc
  "Positions réelles (MT5)" existant qui les masque.
- **Graphique** : tracé SVG simple construit à la main (polyligne des
  prix de clôture des bougies en cache, aucune bibliothèque externe) —
  axe temporel horizontal, prix vertical, avec un marqueur (point
  coloré) à chaque entrée/sortie de `recent_decisions` positionné à
  l'abscisse correspondant à son timestamp le plus proche dans la
  plage de bougies affichée. Palette identique au reste du site (or
  `--gold` pour achat/positif, rouille `--rust` pour vente/négatif).
  Si `candles` est `null` (cache pas encore écrit, ex. juste après un
  redémarrage du bot), afficher un état vide explicite plutôt qu'un
  graphique cassé.
- États d'erreur indépendants par section (solde/positions/graphique)
  — une panne MetaApi n'empêche pas d'afficher l'historique des
  décisions déjà connu, et inversement.

## Sécurité

- Le jeton `BOT_API_TOKEN` protège désormais un point d'accès qui
  expose un solde réel et des positions réelles (pas seulement un
  interrupteur) — sa sensibilité augmente en conséquence. Pas de
  nouveau mécanisme requis (le stockage `localStorage` + prompt
  existant reste adapté), mais cela vaut d'être mentionné explicitement
  dans le commentaire en tête de `gold_bot/api.py`.
- CORS : `allow_origins`/`allow_methods`/`allow_headers` déjà en place
  (`GET`, `POST`, `X-Bot-Token`) couvrent `/dashboard` sans
  modification.
- `/dashboard` reste **strictement en lecture** — même garantie
  structurelle que le reste de l'API HTTPS (voir spec Plan B) : aucun
  chemin de code vers `broker.place_market_order`/`close_position`.

## Tests

- `gold_bot/loop.py` : le cache `latest_candles.json` est écrit après
  un cycle réussi, laissé intact après un cycle en échec (mêmes
  conventions de test que l'existant : `tmp_path`, monkeypatch).
- `gold_bot/api.py` : `/dashboard` sans jeton → `401` ; avec jeton
  valide et les trois sources en succès → `200` avec le corps complet ;
  avec une ou plusieurs sources en échec → `200` avec les champs
  `*_error` renseignés et les autres sections intactes (pannes
  indépendantes, testées séparément) ; cache de bougies absent →
  `"candles": null` sans exception.
- Frontend : pas de suite Node existante pour `docs/index.html` au-delà
  de `docs/*.test.js` (le fichier HTML lui-même n'a pas de tests
  automatisés aujourd'hui) — vérification manuelle dans un navigateur
  après implémentation, comme pour le reste du site.
