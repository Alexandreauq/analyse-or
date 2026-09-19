# API HTTPS en lecture seule pour le Bot Actions (ibkr_bot) — design

## Statut

Validé par l'utilisateur le 2026-09-20, prêt pour le plan d'implémentation.

## 1. Contexte et objectif

Le panneau "Bot Actions" (Portefeuille → Actions, `docs/index.html`)
affiche aujourd'hui un statut opérationnel public, publié par le bot
lui-même après chaque batch quotidien vers `docs/ibkr_bot_status.json`
(commité/poussé depuis le VPS, voir `ibkr_bot/status_publish.py`, livré
le 2026-09-19/20) : dernier statut, compteurs, et — uniquement en mode
`dry_run` — les positions ouvertes avec leurs tickers.

L'utilisateur veut plus, sur le modèle du panneau "Bot Or" déjà en
place : un solde de compte toujours visible, et un historique des
actions passées (achats/ventes), le tout derrière un mot de passe
plutôt que publié en clair dans le dépôt public — ce qui permet
d'afficher des informations plus complètes (solde, positions même en
mode réel) sans les exposer à n'importe quel visiteur du site.

**Ce document ne revient PAS sur la décision "pas d'API pour
ibkr_bot"** prise le 2026-09-14 (spec 5.2/9.4 de
`2026-09-14-ibkr-equities-automation-design.md`) à la légère : cette
décision faisait explicitement suite à un incident réel où le jeton
`BOT_API_TOKEN` du Bot Or avait fuité/été perdu ("un jeton de moins à
perdre"). Interrogé directement sur ce risque, l'utilisateur a confirmé
vouloir tout de même une API, avec le même modèle d'accès que le Bot
Or. Ce document applique donc les mêmes garde-fous de sécurité que
`gold_bot/api.py` (échec fermé, comparaison à temps constant, aucune
route de mutation) et n'élargit la surface d'action nulle part : le
coupe-circuit d'urgence reste exclusivement SSH (`ibkr_bot/state.json`),
cette partie-là de la décision de 2026-09-14 n'est pas remise en
question.

## 2. Périmètre

### Dans le périmètre

- Un endpoint HTTPS, `GET /dashboard`, protégé par un jeton dédié
  (`IBKR_BOT_API_TOKEN`), renvoyant :
  - le solde du compte (dernier instantané connu),
  - les positions actuellement ouvertes (sans restriction de mode,
    voir 4.3),
  - les 50 dernières actions (achats/ventes) exécutées par le bot.
- Le module `ibkr_bot/api.py` correspondant, déployé comme un nouveau
  service systemd sur le VPS.
- Les changements frontend du panneau "Bot Actions" pour consommer ce
  nouvel endpoint (prompt du jeton, affichage).

### Explicitement hors périmètre

- **Aucune route de mutation** (pas de `/kill`, `/resume`, ni
  équivalent) — le coupe-circuit reste exclusivement SSH-only
  (`ibkr_bot/state.json`), décision non remise en question ici.
- Aucune connexion live à IB Gateway déclenchée par une requête API —
  voir 4.2, les données servies sont celles du dernier batch quotidien,
  jamais un fetch à la demande.
- Le fichier `docs/ibkr_bot_status.json` (statut public, sans jeton)
  n'est ni modifié ni remplacé — les deux mécanismes coexistent, sans
  duplication de route (`/status` n'est pas recréé ici).

## 3. Rappel : pourquoi le Bot Actions n'a pas de boucle continue

Contrairement à `gold_bot/loop.py` (processus toujours actif, qui
rafraîchit son cache toutes les quelques minutes et alimente
`gold_bot/api.py`), le Bot Actions est un **batch quotidien unique**
(`ibkr_bot/daily.py`, déclenché par un timer systemd vers 14h45 UTC en
semaine). Il n'existe donc aucun processus qui pourrait tenir un cache
à jour en continu — c'est la raison d'être de la section 4.2.

## 4. Architecture

### 4.1 Vue d'ensemble

```
Batch quotidien (daily.py)
  │
  ├── écrit ibkr_bot/latest_account.json      (déjà existant, journal.py)
  ├── écrit ibkr_bot/positions.json           (déjà existant, portfolio.py)
  └── ajoute une ligne à ibkr_bot/real_trading_log.jsonl (déjà existant, journal.py)
                                                        │
                                                        ▼
                                    ibkr_bot/api.py (nouveau, lecture seule)
                                       GET /dashboard (jeton requis)
                                                        │
                                                        ▼
                                    docs/index.html — panneau Bot Actions
```

### 4.2 Fraîcheur des données — jamais de connexion live à la demande

`ibkr_bot/api.py` n'importe **jamais** `ibkr_bot/gateway.py`, exactement
comme `gold_bot/api.py` n'importe jamais son module de courtage. Toutes
les données servies proviennent de fichiers écrits par le dernier batch
réussi — au pire, elles datent de la veille (si le batch du jour a
échoué en `gateway_indisponible` ou `hors_jour_de_bourse` avant
d'atteindre l'étape qui rafraîchit ces fichiers). C'est un choix
délibéré, confirmé avec l'utilisateur (2026-09-20) : une connexion à la
demande échouerait fréquemment (sessions IBKR connues pour expirer
hors de la fenêtre du batch, 2FA manuel requis) et ouvrirait une
deuxième voie d'accès à IB Gateway en dehors du batch lui-même.

### 4.3 Sources de données (toutes déjà existantes)

| Donnée | Source | Écrite par |
|---|---|---|
| Solde (`base_cash`) | `ibkr_bot/journal.py::LATEST_ACCOUNT_PATH` (`latest_account.json`) | `daily.py` via `journal.save_account_snapshot`, déjà en place |
| Positions ouvertes | `ibkr_bot/portfolio.py::load_positions()` (`positions.json`) | `daily.py`, déjà en place |
| Actions récentes | `ibkr_bot/journal.py::REAL_TRADING_LOG_PATH` (`real_trading_log.jsonl`) | `daily.py` via `journal.append_run`, déjà en place |

Aucun de ces fichiers n'existe encore côté API — seule la lecture/mise
en forme pour `/dashboard` est nouvelle.

**Positions : pas de restriction dry_run/réel ici, contrairement à
`docs/ibkr_bot_status.json`.** La restriction sur le fichier de statut
public existe parce que ce fichier est commité dans un dépôt Git
public, lisible par n'importe qui sans aucune barrière. `/dashboard`
change ce modèle de menace : l'accès nécessite le jeton, donc la
distinction dry_run/réel n'a plus de raison d'être pour cette vue —
confirmé avec l'utilisateur pendant le brainstorming.

### 4.4 Nouvelle fonction : historique des actions

`real_trading_log.jsonl` a déjà un lecteur, `journal.read_runs(path,
day=None)`, mais il filtre sur **un seul jour UTC**. Une nouvelle
fonction est nécessaire pour parcourir plusieurs jours d'un coup :

```python
def read_recent_actions(path: str = REAL_TRADING_LOG_PATH, limit: int = 50) -> list[dict]:
    """Les `limit` dernières actions (achats/ventes) réellement exécutées
    ou simulées par le bot, tous jours confondus, triées du plus ancien
    au plus récent. Parcourt TOUT le fichier avant de tronquer (comme
    gold_bot.api._read_recent_decisions) — avec ~1 ligne de run par
    jour et 0-10 entrées/sorties par ligne, ce fichier grossit lentement
    (quelques Ko/jour), pas de souci de performance à moyen terme."""
```

Chaque entrée aplatie garde les champs déjà produits par
`journal.build_order_record` (ticker, sens, quantité, prix d'exécution,
statut, `close_reason` le cas échéant) — **pas le nom de l'entreprise
ni l'indice**, résolus côté frontend via `indicesData` (déjà chargé
pour toute la page Indices) plutôt que dupliqués côté API. Seules les
actions avec `statut` égal à `"execute"` ou `"simule"` comptent comme
"prises" (celles `"annule_interruption"` ou jamais tentées sont
exclues — elles ne représentent aucun mouvement réel ou simulé).

### 4.5 `ibkr_bot/api.py`

Structure calquée sur `gold_bot/api.py` :

```python
# ibkr_bot/api.py
import hmac
import math
import os

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import ibkr_bot.journal as journal
import ibkr_bot.portfolio as portfolio

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://alexandreauq.github.io"],
    allow_methods=["GET"],
    allow_headers=["X-Bot-Token"],
)


def _check_token(x_bot_token: str | None) -> None:
    """Identique à gold_bot.api._check_token : échec fermé si
    IBKR_BOT_API_TOKEN n'est pas configuré, comparaison à temps
    constant sur les octets UTF-8."""
    expected = os.environ.get("IBKR_BOT_API_TOKEN")
    if not expected or not hmac.compare_digest(
        (x_bot_token or "").encode("utf-8", "surrogateescape"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Jeton invalide ou absent")


def _sanitize_number(v):
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


@app.get("/dashboard")
def dashboard(x_bot_token: str | None = Header(default=None)):
    _check_token(x_bot_token)

    account = journal.load_account_snapshot()  # nouveau petit lecteur, symétrique de save_account_snapshot
    positions = portfolio.load_positions()
    actions = journal.read_recent_actions()

    return {
        "balance": _sanitize_number(account.get("base_cash")),
        "balance_fetched_at": account.get("fetched_at"),
        "positions": [
            {
                "ticker": p.get("ticker"), "name": p.get("name"), "index": p.get("index"),
                "quantite": p.get("quantite"),
                "prix_entree": _sanitize_number(p.get("prix_execution_reference")),
                "date_entree": p.get("date_entree"),
                "target_exit_price": _sanitize_number(p.get("target_exit_price")),
            }
            for p in positions
        ],
        "actions": actions,
    }
```

`journal.load_account_snapshot()` est un nouveau petit lecteur
symétrique de `save_account_snapshot` (même contrat que
`load_positions`/`load_signal_tracking` : fichier absent/corrompu → `{}`,
jamais d'exception).

## 5. Sécurité

- **Nouveau jeton dédié**, `IBKR_BOT_API_TOKEN`, généré séparément du
  `BOT_API_TOKEN` du Bot Or — pas de réutilisation entre les deux bots.
  Stocké dans `/home/ibkrbot/analyse-or/.env` (déjà là pour
  `GITHUB_PUSH_TOKEN`), jamais commité.
- Même mécanisme d'échec fermé et de comparaison à temps constant que
  `gold_bot/api.py::_check_token` (voir 4.5) — repris à l'identique,
  pas réinventé.
- CORS restreint à `https://alexandreauq.github.io`, méthode `GET`
  uniquement (pas de `POST`, puisqu'aucune route de mutation).
- `docs_url=None, redoc_url=None, openapi_url=None` : pas de
  documentation Swagger exposée publiquement, même choix que le Bot Or.
- Aucune route ne peut jamais déclencher un ordre ou modifier l'état du
  bot — ce module ne lit que des fichiers, jamais `gateway.py` ni
  `state.py` en écriture.

## 6. Déploiement

- Nouveau service systemd `ibkr-bot-api.service`, calqué sur
  `deploy/gold-bot-api.service` :

```ini
[Unit]
Description=Bot Actions IBKR - API tableau de bord
After=network.target

[Service]
Type=simple
User=ibkrbot
WorkingDirectory=/home/ibkrbot/analyse-or
EnvironmentFile=/home/ibkrbot/analyse-or/.env
ExecStart=/home/ibkrbot/analyse-or/venv/bin/uvicorn ibkr_bot.api:app --host 0.0.0.0 --port 8444 --ssl-keyfile /etc/letsencrypt/live/goldbot.fr/privkey.pem --ssl-certfile /etc/letsencrypt/live/goldbot.fr/fullchain.pem
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

- **Port 8444** (8443 déjà pris par `gold-bot-api.service`). Même
  certificat TLS que le Bot Or (`goldbot.fr`) — un certificat de domaine
  couvre n'importe quel port de ce même domaine, pas besoin d'un nouveau
  nom de domaine ni d'un nouveau certificat. Le frontend appellera donc
  `https://goldbot.fr:8444/dashboard`.
- `ufw allow 8444` à ajouter au pare-feu (`deploy/harden-vps-firewall.sh`
  ou une commande manuelle équivalente).
- L'utilisateur `ibkrbot` doit rejoindre le groupe `ssl-cert` pour lire
  le certificat existant (`usermod -aG ssl-cert ibkrbot`), comme
  `goldbot` l'a déjà fait (`deploy/setup-tls.sh`).
- Le hook de renouvellement Let's Encrypt existant
  (`/etc/letsencrypt/renewal-hooks/deploy/restart-gold-bot-api.sh`) doit
  être complété pour redémarrer aussi `ibkr-bot-api` (les deux services
  partagent le même certificat, donc le même renouvellement).

## 7. Frontend (`docs/index.html`)

Le panneau "Bot Actions" garde tel quel son bloc de statut public actuel
(déjà en place, alimenté par `docs/ibkr_bot_status.json`) et ajoute
en dessous, sur le modèle exact du Bot Or (`getBotToken()`,
`fetchBotDashboard()`) :

- `IBKR_BOT_API_BASE_URL = 'https://goldbot.fr:8444'` (nouvelle
  constante, à côté de `BOT_API_BASE_URL` existante).
- Un jeton distinct stocké sous une clé `localStorage` différente
  (`ibkrBotToken`, pas `goldBotToken`) — demandé une seule fois via
  `prompt()`, comme le Bot Or.
- `<h2>Solde</h2>` : le `balance` renvoyé, formaté en euros.
- `<h2>Positions ouvertes</h2>` : remplace/enrichit la section déjà
  construite pour le statut public dry_run-only — mêmes liens
  `#indices/TICKER`, désormais alimentée par `/dashboard` plutôt que
  par `docs/ibkr_bot_status.json` quand le jeton est disponible.
- `<h2>Actions récentes</h2>` : nouvelle section, une ligne par action
  (achat/vente, ticker, quantité, prix, date), lien vers
  `#indices/TICKER`, nom résolu via `companiesByTicker` (déjà construit
  ailleurs dans `renderPortfolioScreen`).
- Si le jeton est absent/invalide (401), retomber sur l'affichage
  public existant (`docs/ibkr_bot_status.json`) plutôt que de bloquer
  tout le panneau — le statut public reste utile même sans jeton.

## 8. Tests

- `tests/ibkr_bot/test_journal.py` : nouveaux tests pour
  `read_recent_actions` (trie/tronque correctement sur plusieurs jours,
  exclut `annule_interruption`, dégrade vers `[]` sur fichier
  absent/corrompu) et `load_account_snapshot` (symétrique de
  `save_account_snapshot`, dégrade vers `{}`).
- `tests/ibkr_bot/test_api.py` (nouveau fichier, calqué sur
  `tests/gold_bot/test_api.py`, qui existe déjà et fixe le style :
  `TestClient` de FastAPI, déjà une dépendance du projet) pour
  vérifier : 401 sans jeton, 401 avec mauvais jeton, 200 avec le bon
  jeton, contenu correctement assemblé à partir de fixtures pour les
  trois fichiers sources, dégradation propre si un fichier source est
  absent.
- `tests/test_deploy_files.py` (existant, étendu) : vérifie la présence
  et la cohérence de `deploy/ibkr-bot-api.service` (même contrôle que
  pour les autres fichiers de déploiement déjà couverts).

## 9. Points arbitrés

- **Pas de route `/status` publique dupliquée** : `docs/ibkr_bot_status.json`
  couvre déjà ce besoin (accès sans jeton, publié par le batch
  lui-même) — ajouter une route équivalente ici serait une duplication
  inutile de la même information par un canal différent.
- **Pas de restriction dry_run sur les positions servies par
  `/dashboard`** (contrairement au fichier public) — voir 4.3,
  confirmé avec l'utilisateur : le jeton change le modèle de menace.
- **Aucune route de mutation** — le coupe-circuit reste SSH-only, la
  décision de 2026-09-14 sur ce point précis n'est pas remise en
  question par ce document.
