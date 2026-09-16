# Déploiement du Bot Actions IBKR sur le VPS

**⚠️ Ce Gateway (Client Portal Web API) est en cours de remplacement** par
IB Gateway / TWS API, suite à un bug d'authentification IBKR non résolu
sur ce Gateway (session jamais authentifiée malgré un login+2FA réussis).
Voir `deploy/README-ibkr-tws.md` pour le nouveau déploiement, installé EN
PARALLÈLE de celui-ci le temps de la validation — ce document reste exact
tant que la bascule n'est pas actée.

Le bot actions (`ibkr_bot/`) tourne sur **le même VPS Hetzner que le Bot
Or**, mais sous un **utilisateur système séparé** (`ibkrbot`) et avec son
propre clone du dépôt. Raison (spec 5.1) : un bug ou une compromission du
bot Or ne doit pas donner accès aux identifiants d'un compte-titres réel,
et réciproquement.

Pour le Bot Or, voir `deploy/README.md`. Les deux guides sont
indépendants ; seule l'étape 1 (sécurisation de base du VPS) est commune
et déjà faite.

## 1. Utilisateur dédié + dépôt

```bash
sudo adduser --disabled-password ibkrbot

# Sudoers restreint a exactement la commande dont deploy-ibkr.sh a besoin :
echo 'ibkrbot ALL=(root) NOPASSWD: /bin/systemctl restart ibkr-gateway' | sudo tee /etc/sudoers.d/ibkrbot-restart
sudo chmod 440 /etc/sudoers.d/ibkrbot-restart

sudo -u ibkrbot -i
git clone https://github.com/Alexandreauq/analyse-or.git
cd analyse-or
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-bot.txt
```

## 2. Installer le Gateway IBKR Client Portal

Le Gateway est un paquet **Java** fourni par IBKR (spec 4.4 / 9.3), pas
une dépendance Python.

```bash
sudo apt install -y openjdk-17-jre-headless unzip

sudo -u ibkrbot -i
mkdir -p ~/clientportal.gw && cd ~/clientportal.gw
# Télécharger l'archive officielle du Client Portal Gateway depuis le
# site IBKR (page "Client Portal Web API"), puis :
unzip clientportal.gw.zip
```

**Éditer `~/clientportal.gw/root/conf.yaml` pour n'écouter que sur la
boucle locale** : le Gateway ne doit **jamais** être joignable depuis
Internet, et **aucun port ne doit être ouvert dans ufw** (spec 4.4).
Vérifier après démarrage :

```bash
sudo ss -lntp | grep 5000    # doit montrer 127.0.0.1:5000, jamais 0.0.0.0:5000
```

## 3. Fichier `.env` (jamais commité)

Créer `/home/ibkrbot/analyse-or/.env` :

```
# Gateway local — jamais expose sur Internet
IBKR_GATEWAY_URL=https://127.0.0.1:5000
# Compte IBKR sur lequel trader (ex. U1234567)
IBKR_ACCOUNT_ID=...
SMTP_USER=...
SMTP_PASSWORD=...
# Optionnel, sinon = SMTP_USER
MAIL_TO=...
```

Permissions restreintes : `chmod 600 .env`.

**Le login et le mot de passe IBKR ne figurent PAS dans ce fichier, et ne
doivent jamais y figurer** (spec 5.1). Ils sont saisis à la main dans
l'interface d'authentification locale du Gateway, une fois par session.
C'est contraignant, mais c'est la meilleure propriété de sécurité du
dispositif : les identifiants maîtres du compte-titres ne sont stockés
sur le VPS sous aucune forme.

**Il n'y a pas non plus de jeton d'API** (`IBKR_BOT_API_TOKEN` n'existe
pas) : l'interrupteur d'urgence est un fichier modifié en SSH, pas une
route HTTPS (spec 5.2 / 9.4, décidé après l'incident `BOT_API_TOKEN` du
2026-09-14 sur le bot Or — un secret de moins à perdre).

## 4. Installer les services systemd

*(En tant qu'administrateur/root, pas dans le shell `ibkrbot`.)*

```bash
sudo cp deploy/ibkr-gateway.service /etc/systemd/system/
sudo cp deploy/ibkr-bot-daily.service /etc/systemd/system/
sudo cp deploy/ibkr-bot-daily.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ibkr-gateway
sudo systemctl enable --now ibkr-bot-daily.timer
```

Le **timer** est activé, pas le service : c'est lui qui déclenche le batch
à 14:45 UTC. Vérifier :

```bash
systemctl list-timers ibkr-bot-daily.timer --no-pager
```

## 5. Authentifier le Gateway (action manuelle récurrente)

**C'est la principale servitude opérationnelle de ce bot, et elle est
structurelle — pas un bug, pas un manque du code** (spec 4.4 / 8). La
session du Gateway expire (historiquement ~24 h) et sa réactivation peut
exiger une validation 2FA sur le téléphone. Il n'existe aucun moyen propre
de l'automatiser.

Depuis un poste de travail, ouvrir un tunnel SSH vers le Gateway (qui
n'écoute que sur la boucle locale du VPS) :

```bash
ssh -L 5000:127.0.0.1:5000 utilisateur@178.105.186.220
```

Puis, dans un navigateur local, ouvrir `https://127.0.0.1:5000` (accepter
le certificat auto-signé), saisir le login et le mot de passe IBKR, et
valider la 2FA. Vérifier :

```bash
curl -sk -X POST https://127.0.0.1:5000/v1/api/iserver/auth/status
# doit renvoyer "authenticated": true et "connected": true
```

**À faire avant 14:45 UTC chaque jour de bourse.** Si la session n'est pas
valide au moment du batch, le bot fait 3 tentatives espacées de 10
minutes, abandonne proprement (aucun ordre, ni entrée ni sortie) et envoie
**un email d'alerte immédiat** dont l'objet commence par « ALERTE ». Les
signaux d'entrée du jour sont perdus ; les sorties sont décalées au
lendemain (spec 5.5 / 9.8).

## 6. Vérifier

```bash
sudo systemctl status ibkr-gateway
systemctl list-timers ibkr-bot-daily.timer --no-pager

# Lancer un batch a la main, sans attendre le timer (reste en dry_run) :
sudo systemctl start ibkr-bot-daily
journalctl -u ibkr-bot-daily -n 100 --no-pager
```

Le bot tourne en **mode simulation** (`dry_run: true` par défaut, spec
5.3) : il évalue tout, journalise exactement ce qu'il aurait fait, et
**n'appelle jamais la route de passage d'ordre**. Observer
`ibkr_bot/real_trading_log.jsonl` et les résumés quotidiens par email.

## 7. Interrupteur d'urgence (`kill_switch`)

`ibkr_bot/state.json` est le **seul** fichier qui contrôle l'interrupteur
d'urgence et le mode simulation. Volontairement pas exposé par une API :
le seul chemin est une commande manuelle en SSH.

```bash
sudo -u ibkrbot -i
cd analyse-or
source venv/bin/activate
python3 -c "import ibkr_bot.state as state; s = state.load_state(state.STATE_PATH); s['kill_switch'] = True; state.save_state(s, state.STATE_PATH)"
```

Aucun redémarrage de service n'est nécessaire : le batch relit ce fichier
à chaque exécution, **et une seconde fois juste avant chaque envoi
d'ordre**. Pour lever l'interrupteur, remettre `False` avec la même
commande.

La cadence quotidienne laisse toujours plusieurs heures pour couper avant
le prochain batch — c'est exactement l'argument qui a fait écarter une API
HTTPS dédiée (spec 9.4).

**Couper l'interrupteur EN COURS de batch réel est un scénario prévu, pas
un cas limite** : si une position réelle doit être vendue et que
l'opérateur bascule `kill_switch` à `True` pendant que le batch tourne
encore, l'ordre n'est pas envoyé (la relecture juste avant l'envoi le
bloque) et la position correspondante est **laissée intacte dans
`positions.json`** — jamais retirée comme si elle avait été vendue. Elle
apparaît dans le résumé du jour avec le statut `annule_interruption`, à
traiter manuellement (vérifier l'état réel chez IBKR, puis relever le
kill switch pour que le batch du lendemain la reprenne).

## Jours fériés : hors périmètre, kill switch manuel requis

Le timer systemd (`deploy/ibkr-bot-daily.timer`) se déclenche **tous les
jours**, y compris les week-ends — le bot les détecte lui-même et abandonne
le batch entier (aucune entrée, aucune sortie, statut
`hors_jour_de_bourse`) avant même de contacter le Gateway.

**Les jours fériés, eux, ne sont PAS détectés.** `docs/indices.json` est
généré par un workflow GitHub Actions qui tourne lui aussi 7j/7 et tamponne
la date du jour sans savoir si les places boursières étaient ouvertes : le
garde de fraîcheur du bot ne voit donc rien d'anormal un jour férié. Et les
calendriers de jours fériés des différentes places couvertes par le bot
(Paris, Francfort, Londres, New York, Milan, Madrid, Zurich...) **ne
coïncident pas entre eux** (Independence Day n'est pas férié à Paris, le
Lundi de Pentecôte n'est pas férié à New York, etc.) : coder un vrai
calendrier par place est un chantier à part entière, volontairement laissé
hors du périmètre de ce correctif.

**En pratique, jusqu'à ce qu'un calendrier de jours fériés soit implémenté,
c'est à l'opérateur de couper le bot à la main** autour des jours fériés
connus (Noël, Jour de l'An, Vendredi saint, et tout autre jour férié propre
à une des places couvertes) — via l'interrupteur d'urgence décrit en
section 7 ci-dessus, remis à `False` une fois la place rouverte. Un batch
qui tourne un jour férié verrait le même risque que sur un week-end non
détecté : une règle de sortie à date fixe (délai de 6 mois, stop-loss
comparé à un cours de clôture périmé) peut légitimement se déclencher, et
la boucle de confirmation IBKR répondrait automatiquement `confirmed=True`
à un avertissement « marché fermé ».

## 8. Passage en mode réel (à ne faire qu'après validation de la simulation)

Avant tout passage en réel, deux prérequis manuels :

1. **Plusieurs semaines en `dry_run`** avec comparaison quotidienne des
   décisions simulées aux positions du paper-trading. Tout écart doit
   s'expliquer par une cause connue (plafond de 10, 0 action achetable,
   horaire d'exécution) — spec 7.
2. **Vérifier les coûts d'abonnement aux données de marché** sur le
   compte IBKR (ou auprès du support), place par place, ainsi que le seuil
   exact de dispense (spec 6 / 9.2). Ce point n'est délibérément chiffré
   nulle part dans la spec.

```bash
sudo -u ibkrbot -i
cd analyse-or
source venv/bin/activate
python3 -c "import ibkr_bot.state as state; s = state.load_state(state.STATE_PATH); s['dry_run'] = False; state.save_state(s, state.STATE_PATH)"
```

Pour revenir en simulation, remettre `dry_run` à `True` avec la même
commande.

## Mises à jour

```bash
sudo -u ibkrbot -i
cd analyse-or
./deploy/deploy-ibkr.sh
```

Le batch fait lui-même un `git pull` au début de chaque exécution (spec
4.5), donc les données `docs/indices.json` sont toujours fraîches ; mais
un changement de **code** ne prend effet qu'au batch suivant, ce qui est
voulu — un batch ne doit pas changer de version en plein vol.

## Fichiers générés (gitignorés)

| Fichier | Rôle |
|---|---|
| `ibkr_bot/state.json` | `kill_switch`, `dry_run` |
| `ibkr_bot/positions.json` | positions ouvertes **par le bot** |
| `ibkr_bot/real_trading_log.jsonl` | journal append-only, une ligne par batch |
| `ibkr_bot/conid_cache.json` | correspondances ticker → conid IBKR |
| `ibkr_bot/latest_account.json` | dernier instantané des soldes |
