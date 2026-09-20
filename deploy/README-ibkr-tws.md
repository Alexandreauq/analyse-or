# Déploiement IB Gateway / TWS API (remplace le Client Portal Gateway)

Voir `docs/superpowers/specs/2026-09-16-ibkr-tws-gateway-migration-design.md`
pour le contexte complet : le Client Portal Gateway (`deploy/README-ibkr.md`)
ne parvenait jamais à établir de session authentifiée après un login+2FA
pourtant réussis côté navigateur — bug IBKR non résolu, documenté par la
communauté. Ce document décrit le remplacement par **IB Gateway** piloté
par **IBC**, empaqueté dans l'image Docker maintenue
[`gnzsnz/ib-gateway-docker`](https://github.com/gnzsnz/ib-gateway-docker).

**Déployé en parallèle** de `ibkr-gateway.service` (Client Portal Gateway) —
celui-ci n'est PAS touché tant que la nouvelle voie n'est pas validée en
`dry_run` sur plusieurs jours (spec §7 point 5-6).

## Composants

- **Docker** (installé le 2026-09-16 sur le VPS, absent avant).
- **`deploy/docker-compose-ibkr-tws.yml`** : service unique `ib-gateway`,
  image `ghcr.io/gnzsnz/ib-gateway:stable` (IB Gateway + IBC + Xvfb + VNC
  + socat, tout intégré).
- Ports exposés en local uniquement (`127.0.0.1:...`), jamais sur `0.0.0.0` :
  - `4001` → TWS API compte **réel** (mappé sur le port interne 4003 du
    conteneur, via `socat`).
  - `4002` → TWS API compte **paper** (port interne 4004).
  - `5900` → VNC, pour valider le login/2FA manuellement.

## Secrets (jamais commités)

Trois fichiers sur le VPS, propriété `ibkrbot`, à recréer si le conteneur
est redéployé depuis zéro :

- `/home/ibkrbot/secrets/tws_password.txt` — mot de passe IBKR.
- `/home/ibkrbot/secrets/vnc_password.txt` — mot de passe VNC (choisi
  librement, sert uniquement à protéger l'accès VNC en tunnel SSH).
- `/home/ibkrbot/analyse-or/deploy/.env` — `TWS_USERID=<identifiant IBKR>`
  et `TRADING_MODE=live` ou `paper`.

**Point important découvert au déploiement (2026-09-16) : les fichiers de
secrets Docker Compose (mode non-swarm) sont montés par bind-mount direct,
pas copiés dans un tmpfs.** Un fichier en `chmod 600` appartenant à
`ibkrbot` sur l'hôte est **illisible depuis l'intérieur du conteneur**
(qui tourne sous un UID interne différent) — provoque une boucle de
redémarrage avec `Permission denied` sur `/run/secrets/...`. Les fichiers
de secrets doivent être en `chmod 644` (lisibles par tous) ; la protection
réelle vient du dossier parent `/home/ibkrbot/secrets/` en `chmod 700`
(seul root/ibkrbot peut même y entrer), pas du mode du fichier individuel.

**Autre point découvert** : `TWS_USERID` n'a pas d'équivalent `_FILE`
(contrairement à `TWS_PASSWORD`) — il passe forcément par une variable
d'environnement en clair dans le `.env` local (non commité, `chmod 600`),
jamais dans `docker-compose-ibkr-tws.yml` lui-même (qui, lui, est commité).

## Login initial et validation hebdomadaire

1. Tunnel SSH vers le port VNC : `ssh -L 5900:127.0.0.1:5900 root@178.105.186.220`.
2. Client VNC (ex. TigerVNC Viewer) sur `127.0.0.1:5900`, mot de passe VNC.
3. IBC remplit automatiquement identifiant/mot de passe et clique sur
   "Log In" — reste à valider le 2FA (notification IBKR Mobile) si
   `TRADING_MODE=live` (le mode `paper` ne demande pas de 2FA, mais
   **nécessite un compte paper lié au compte réel dans IBKR Account
   Management** — sans ça, IB Gateway affiche "the specified user does not
   have a Paper Trading user associated with it" et reste bloqué sur
   l'écran de login. Ce compte (U28849893) n'a pas de paper lié : les
   tests de connectivité de ce déploiement ont donc été faits directement
   en `TRADING_MODE=live`, sans risque puisque `dry_run` reste la seule
   autorité côté bot pour l'envoi d'un ordre réel — voir `ibkr_bot/daily.py`).
4. Une fois connecté, IBC pré-coche automatiquement toutes les cases
   "Bypass ... for API Orders" (vérifié dans les logs du conteneur,
   `BYPASS_WARNING=yes`) — c'est ce qui garantit qu'aucune popup de
   confirmation ne bloque jamais un ordre réel envoyé par
   `gateway.py::place_market_order()` (voir plan de migration, Tâche 5).
5. **Revalidation manuelle uniquement le dimanche**, pas chaque jour.
   IBKR force un redémarrage de Gateway chaque nuit (~23h45 heure US),
   mais `TIME_ZONE`/`AUTO_RESTART_TIME`/`TWOFA_TIMEOUT_ACTION` dans
   `docker-compose-ibkr-tws.yml` laissent IBC reprendre la session
   automatiquement pour ces redémarrages — seul celui qui suit dimanche
   01h00 heure US exige de refaire les étapes 1-3 (login + 2FA). Un
   email de rappel part automatiquement chaque dimanche à l'heure du
   redémarrage (`ibkr_bot/gateway_reminder.py`, voir
   `deploy/ibkr-gateway-reminder.timer`). Avant ce réglage (découvert le
   2026-09-20), la revalidation était bien quotidienne, comme documenté
   dans `deploy/README-ibkr.md` pour l'ancien Gateway — ce n'est plus le
   cas ici.

## Vérification de connectivité (validé le 2026-09-16)

```bash
python3 -c "
from ib_async import IB
ib = IB()
ib.connect('127.0.0.1', 4001, clientId=99, timeout=15)  # 4002 en paper
print('connecte:', ib.isConnected())
print('comptes:', ib.managedAccounts())
ib.disconnect()
"
```
Attendu : `connecte: True`, `comptes: ['U28849893']` (ou le compte
concerné) non vide.

## Reste à faire avant bascule complète

1. Merger cette branche (`ibkr-tws-gateway-migration`) après la revue
   finale de branche.
2. Déployer le code mergé sur le VPS (`git pull` + réinstallation du venv
   avec `ib_async`, cf. `requirements.txt`).
3. Basculer `IBKR_GATEWAY_URL` dans le `.env` du bot
   (`/home/ibkrbot/analyse-or/.env`, différent du `.env` Docker ci-dessus)
   vers `127.0.0.1:4001` (ou `4002` pour des tests dry_run supplémentaires
   en paper, une fois un compte paper créé si souhaité).
4. Semaines de validation en `dry_run` (spec 5.3 du document Plan A/B
   original) avant tout passage en réel.
5. Une fois la stabilité confirmée : désinstaller le Client Portal Gateway
   (`ibkr-gateway.service`, `/home/ibkrbot/clientportal.gw/`).
6. Pendant les semaines de validation en `dry_run` (point 4) : journaliser
   la valeur brute de `primaryExchange` renvoyée par la TWS API pour un
   ticker par indice, et la confronter à la table `EXPECTED_VENUE` de
   `ibkr_bot/contracts.py` — cette table a été vérifiée contre les
   résultats de recherche du Client Portal Gateway, **pas** contre le
   champ `primaryExchange` de la TWS API. Un écart non détecté ferait
   échouer silencieusement `resolve_conid()` pour tous les tickers
   concernés (échec sûr, mais avec un motif de rejet confus dans le
   journal).
7. Avant de décommissionner réellement le Client Portal Gateway (point 5
   ci-dessus) :
   - mettre à jour les lignes `After=`/`Wants=` de
     `deploy/ibkr-bot-daily.service` pour qu'elles ne référencent plus
     `ibkr-gateway.service` (l'ancienne unité CPAPI) ;
   - retirer la ligne `sudo systemctl restart ibkr-gateway` de
     `deploy/deploy-ibkr.sh` — ce script tourne sous `set -e` et
     échouerait durement dès que cette ancienne unité n'existera plus.
   - noter aussi que `tests/ibkr_bot/test_deploy_files.py` devrait à
     terme recevoir des vérifications structurelles équivalentes pour
     `docker-compose-ibkr-tws.yml` (ex. ports liés à `127.0.0.1`, pas
     d'identifiants en dur) — pas urgent tant que les deux gateways
     tournent en parallèle, mais nécessaire avant la bascule.
