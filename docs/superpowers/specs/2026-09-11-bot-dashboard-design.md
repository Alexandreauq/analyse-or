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
- Solde, positions et bougies sont tous les trois lus depuis un cache
  local écrit par `gold_bot/loop.py` à chaque cycle — `gold_bot/api.py`
  n'appelle **jamais** MetaApi ni Twelve Data lui-même (voir Mécanisme).
  Conséquence assumée : la donnée affichée a jusqu'à ~1 minute de
  retard (durée d'un cycle), jamais interrogée en direct au moment où
  l'utilisateur ouvre l'onglet.
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

### Cache local (`gold_bot/loop.py`)

Le bot appelle déjà, à chaque cycle (~1×/min), dans `run_cycle` :
`confluence.fetch_gold_candles(...)`, `broker.get_account_balance(...)`,
et `bot.reconcile_positions(...)` (→ `broker.get_open_positions`). Pour
que `gold_bot/api.py` n'ait **jamais** besoin d'appeler MetaApi ni
Twelve Data lui-même (voir Sécurité — `api.py` ne doit jamais importer
`gold_bot.broker`), le loop écrit chacun de ces trois résultats dans
son propre fichier de cache, **immédiatement après son propre appel
réussi** — pas après la fin du cycle entier, pour qu'un échec plus loin
dans le cycle (ex. les `504` déjà observés sur
`get_symbol_specification`) n'empêche pas de mettre à jour les caches
des appels qui, eux, ont réussi :

- `gold_bot/latest_candles.json` — `{"candles": [...], "fetched_at":
  "<ISO 8601 UTC>"}`, écrit juste après la ligne `candles =
  confluence.fetch_gold_candles(...)` dans `run_cycle`.
- `gold_bot/latest_balance.json` — `{"balance": <float>, "fetched_at":
  "<ISO 8601 UTC>"}`, écrit juste après la ligne `balance =
  broker.get_account_balance(...)`.
- `gold_bot/latest_positions.json` — `{"positions": [...],
  "fetched_at": "<ISO 8601 UTC>"}`, écrit juste après la ligne
  `open_positions = bot.reconcile_positions(...)` — `positions` est la
  liste brute renvoyée par MetaApi (mêmes clés que
  `portfolio_sync_mt5.to_public_positions` consomme), pas encore
  traduite achat/vente ; cette traduction se fait côté `api.py` à la
  lecture (voir plus bas), pas à l'écriture.

Les trois fichiers vivent dans le même dossier que `state.py`/
`decisions_log.jsonl`. Écriture atomique via `state.save_state(...)`
réutilisée telle quelle (fichier temporaire + `os.replace`, déjà
générique sur n'importe quel dict/chemin — aucun nouveau code
d'écriture atomique nécessaire). Chacun des trois fichiers n'est écrit
qu'en cas de succès de **son** appel ; si un cycle échoue avant
d'atteindre un appel donné, le fichier correspondant reste tel quel
(donnée légèrement périmée plutôt qu'absente, avec son propre
`fetched_at` pour que le frontend puisse dater ce qu'il affiche).

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
un ordre" reste intacte et se renforce même (`/dashboard` ne fait que
lire des fichiers locaux, `api.py` n'importe toujours pas
`gold_bot.broker` — `tests/gold_bot/test_api.py::
test_broker_module_is_never_referenced_in_api_source` doit rester vert
sans modification), mais la phrase "la SEULE action possible... est
d'arrêter le bot" devient trompeuse une fois `/dashboard` ajoutée — à
reformuler en distinguant explicitement les routes de lecture
(`/status`, `/dashboard`) des routes de mutation (`/kill`, `/resume`,
seules routes qui changent un état, et seul `kill_switch` peut être
modifié — jamais `dry_run`).

`/dashboard` lit les trois fichiers de cache ci-dessus (chacun
indépendamment absent/illisible → sa section vaut `null`/liste vide,
jamais d'exception — même convention que `notify.read_todays_decisions`)
et les `N=50` dernières lignes non triviales de `decisions_log.jsonl`
(`action` dans `{"simulation_dry_run", "exécuté"}` — les
`"aucune"`/`"erreur"`/`"ignore"` sont exclues, elles n'ont pas
d'entrée/sortie à marquer). Pour chaque ligne retenue, seuls les
`steps` de type `"ouverture_simulee"` sont utilisés pour les marqueurs
(entrée/sortie) — `bot.decide_and_act` n'émet que
`"ouverture_simulee"`/`"clôture_simulee"` comme types de step, **y
compris une fois en mode réel** (nom hérité du Plan A, non renommé en
Plan B ; ne pas s'y fier pour distinguer simulé/réel, c'est le champ
`action` de la ligne — `"simulation_dry_run"` vs `"exécuté"` — qui
porte cette information).

Le contenu de `latest_positions.json` est traduit à la lecture avec la
même correspondance que `portfolio_sync_mt5.to_public_positions`
(`POSITION_TYPE_BUY`/`POSITION_TYPE_SELL` → `"achat"`/`"vente"`), mais
**sans** masquer les chiffres cette fois (le point d'accès est
protégé, contrairement au fichier public que lit cette fonction) :
`{symbol, direction, volume, open_price, current_price, profit,
stop_loss, take_profit}`.

Réponse `200 OK` (toujours 200 si le jeton est valide — un cache
absent/périmé se traduit par `null`/liste vide dans le corps, jamais
par un code d'erreur, pour que le frontend affiche ce qui est
disponible plutôt que de tout perdre) :

```json
{
  "balance": 9140.10,
  "balance_fetched_at": "2026-09-11T16:40:05Z",
  "positions": [
    {"symbol": "XAUUSD", "direction": "vente", "volume": 2.0,
     "open_price": 4316.28, "current_price": 4316.49,
     "profit": -36.18, "stop_loss": null, "take_profit": null}
  ],
  "positions_fetched_at": "2026-09-11T16:40:06Z",
  "candles": [{"time": "2026-09-11 16:40:00", "open": 3651.2, "high": 3652.0,
               "low": 3650.8, "close": 3651.5}, ...],
  "candles_fetched_at": "2026-09-11T16:40:04Z",
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
  prompt au prochain essai — même comportement que `wireBotControls()`
  applique déjà à `/kill`/`/resume` (`localStorage.removeItem`
  sur `401`), à répliquer ici pour `/dashboard`.
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
- États vides indépendants par section (solde/positions/graphique) —
  un cache absent ou périmé sur l'une des trois n'empêche pas
  d'afficher les deux autres ni l'historique des décisions.

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
- `/dashboard` reste **strictement en lecture** — garantie renforcée
  par rapport au reste de l'API HTTPS (voir spec Plan B) : `api.py`
  n'importe toujours pas `gold_bot.broker` du tout (ni pour lire ni
  pour écrire), donc aucun chemin de code vers
  `broker.place_market_order`/`close_position` **ni même vers un appel
  MetaApi en lecture** — `/dashboard` ne lit que des fichiers locaux
  écrits par `loop.py`. Le test existant
  `test_broker_module_is_never_referenced_in_api_source` doit rester
  vert sans modification ; c'est un signal fort si l'implémentation
  dévie de ce mécanisme.

## Tests

- `gold_bot/loop.py` : chacun des trois caches
  (`latest_candles.json`/`latest_balance.json`/`latest_positions.json`)
  est écrit après le succès de son propre appel, indépendamment des
  deux autres — en particulier, un échec de `get_symbol_specification`
  (le point de défaillance déjà observé en production) laisse les
  trois caches déjà écrits ce cycle-là intacts, il ne les efface pas
  rétroactivement (mêmes conventions de test que l'existant :
  `tmp_path`, monkeypatch).
- `gold_bot/api.py` : `/dashboard` sans jeton → `401` ; avec jeton
  valide et les trois caches présents → `200` avec le corps complet ;
  avec un ou plusieurs caches absents/illisibles → `200` avec les
  champs correspondants à `null`/liste vide et les autres sections
  intactes (testé pour chaque cache indépendamment) ; le test
  `test_broker_module_is_never_referenced_in_api_source` existant doit
  rester vert sans modification.
- Frontend : pas de suite Node existante pour `docs/index.html` au-delà
  de `docs/*.test.js` (le fichier HTML lui-même n'a pas de tests
  automatisés aujourd'hui) — vérification manuelle dans un navigateur
  après implémentation, comme pour le reste du site.
